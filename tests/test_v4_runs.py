"""N3 tests: /runs, /history, /active, restore, dirty hash parity. Never mutates the real boards/ (all writes go to a temp copy)."""
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import serve_v4_runs as R  # noqa: E402

sys.path.insert(0, HERE)
import _real  # noqa: E402

REAL = os.path.join(ROOT, "boards")
BOARD_FILES = sorted(_real.real_paths().values())


class FakeH:
    def __init__(self):
        self.code, self.body = None, None

    def _send(self, code, body, ctype=None):
        self.code, self.body = code, body if isinstance(body, bytes) else body.encode()

    def _err(self, code, msg):
        self._send(code, json.dumps({"error": msg}))

    def json(self):
        return json.loads(self.body)


class FakeHost:
    def __init__(self, boards):
        self.BOARDS = boards


@pytest.fixture()
def tmpws(tmp_path):
    """Temp workspace: boards/ copied from the real one (+.history), empty runs/."""
    b = tmp_path / "boards"
    shutil.copytree(REAL, b)
    _real.add_instances(str(b))
    (tmp_path / "runs").mkdir()
    return tmp_path


def write_rows(ws, rows):
    with open(ws / "runs" / "2026-10-05.jsonl", "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def test_runs_max5_newest_first_null_card_tolerated(tmpws):
    rows = [{"card": None, "event": "turn.start", "ts": "2026-10-05T10:00:00.000Z", "board": "demo-board"}]
    for i in range(8):
        rows.append({"card": "c1", "event": "prompt.submit", "ts": f"2026-10-05T10:0{i + 1}:00.000Z"})
        rows.append({"card": None, "event": "agent.spawn.idle", "ts": f"2026-10-05T10:0{i + 1}:30.000Z", "subagentType": "x"})
    write_rows(tmpws, rows)
    got = R.runs_for("demo-board", "c1", str(tmpws / "boards"))
    assert len(got) == 5
    assert [r["ts"] for r in got] == sorted((r["ts"] for r in got), reverse=True)
    assert all(r["card"] == "c1" for r in got)
    assert len(R.runs_for("demo-board", None, str(tmpws / "boards"))) == 5  # card=None rows are returned, not a crash
    assert R.runs_for("demo-board", "nope", str(tmpws / "boards")) == []
    h = FakeH()
    R.handle_runs(h, FakeHost(str(tmpws / "boards")), {"board": "demo-board"}, {"card": ["c1"]})
    assert h.code == 200 and len(h.json()["rows"]) == 5
    assert R.summary_for("demo-board", str(tmpws / "boards"))["c1"]["count"] == 8


def test_runs_real_rows_do_not_crash():
    got = R.runs_for("demo-board", None, REAL)
    assert len(got) <= 5 and all("snapshot_id" in r for r in got)


def test_diff_ignores_layout_and_reports_ids():
    a = json.load(open(BOARD_FILES[0], encoding="utf-8"))
    b = json.loads(json.dumps(a))
    b["saved_at"] = "2099-01-01T00:00:00Z"
    b["group_styles"] = {"zz": {"color": "#fff", "collapsed": True}}
    for c in b["cards"]:
        c["pos"] = {"x": 99999, "y": 99999}
        c["size"] = {"w": 1, "h": 1}
    d = R.diff_boards(a, b)
    assert d["added"] == d["removed"] == d["changed"] == [] and not d["edges_changed"] and d["board_fields_changed"] == []
    first = b["cards"][0]["id"]
    b["cards"][0]["label"] = "changed label"
    b["cards"].append(dict(b["cards"][1], id="brand-new-card"))
    removed = b["cards"].pop(2)["id"]
    d = R.diff_boards(a, b)
    assert d["added"] == ["brand-new-card"] and d["removed"] == [removed] and first in d["changed"]










FIXTURE = os.path.join(HERE, "fixtures", "hash-parity.json")


def test_fixture_hash_parity_python():
    fx = json.load(open(FIXTURE, encoding="utf-8"))
    assert R.semantic_hash(fx["doc"]) == fx["expected_hash"]
    assert R.semantic_text(fx["doc"]) == fx["expected_text"]
    assert '"n1"' not in fx["expected_text"] and '"pin"' not in fx["expected_text"] and '"style"' not in fx["expected_text"]


def test_fixture_hash_parity_bun_register_and_dirty_js(tmp_path):
    bun = shutil.which("bun")
    if not bun:
        pytest.skip("bun not installed")
    fx = json.load(open(FIXTURE, encoding="utf-8"))
    reg = (ROOT + "/executor/hooks/register.tsx").replace("\\", "/")
    dj = (ROOT + "/canvas/js/dirty.js").replace("\\", "/")
    script = tmp_path / "f.mjs"
    script.write_text(
        "import {semanticText as ts} from 'file:///" + reg + "';\n"
        "import {semanticText as js, sha256} from 'file:///" + dj + "';\n"
        "import fs from 'node:fs';\n"
        "const d=JSON.parse(fs.readFileSync(process.argv[2],'utf8')).doc;\n"
        "console.log(JSON.stringify([await sha256(ts(d)), await sha256(js(d)), ts(d)]));\n", encoding="utf-8")
    r = subprocess.run([bun, str(script), FIXTURE], capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr[-300:]
    h_ts, h_js, txt = json.loads(r.stdout.decode("utf-8"))
    assert h_ts == h_js == fx["expected_hash"]
    assert txt == fx["expected_text"]


def test_style_pin_notes_do_not_change_hash_but_run_mode_does():
    fx = json.load(open(FIXTURE, encoding="utf-8"))
    d = fx["doc"]
    h0 = R.semantic_hash(d)
    d["cards"][1]["style"] = {"color": "#00ff00"}
    d["cards"][1]["pin"] = {"port": "in", "sample": "q"}
    d["cards"].append({"id": "n2", "type": "note", "ref": {"kind": "note"}, "pos": {"x": 0, "y": 0}})
    d["edges"].append({"from": "n2.out", "to": "a.in"})
    assert R.semantic_hash(d) == h0
    d["cards"][0]["run_mode"] = "wait"
    assert R.semantic_hash(d) != h0




