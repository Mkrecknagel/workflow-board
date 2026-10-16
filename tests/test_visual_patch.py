"""PATCH /board/<name>/visual: visual-only overlay endpoint. Temp board copy + free loopback port; never the real boards/ or 8765."""
import glob
import json
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import serve  # noqa: E402
import serve_v4_runs as R  # noqa: E402

sys.path.insert(0, HERE)
import _real  # noqa: E402

REAL = os.path.join(ROOT, "boards")


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    b = tmp_path / "boards"
    shutil.copytree(REAL, b)
    _real.add_instances(str(b))
    shutil.rmtree(b / ".history", ignore_errors=True)
    monkeypatch.setattr(serve, "BOARDS", str(b))
    real_before = {f: open(f, "rb").read() for f in _real.real_paths().values()}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve.H)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", b
    httpd.shutdown()
    assert {f: open(f, "rb").read() for f in real_before} == real_before  # real boards untouched


def call(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def load(b):
    return json.load(open(b / "demo-board.json", encoding="utf-8"))


def hist(b):
    d = b / ".history"
    return [f for f in os.listdir(d) if f.startswith("demo-board.")] if d.is_dir() else []


def note(i="note-1", text="hello"):
    return {"id": i, "type": "note", "label": "n", "enabled": False, "execution_mode": "observe", "immutable": False,
            "ref": {"kind": "note"}, "fields": {"text": text}, "ports": {"in": [], "out": []}, "pos": {"x": 10, "y": 10}}


def test_overlay_applies_pos_size_style_notes_group_styles(srv):
    base, b = srv
    d = load(b)
    h0 = R.semantic_hash(d)
    cid = d["cards"][0]["id"]
    st, r = call(base, "PATCH", "/board/demo-board/visual", {
        "cards": {cid: {"pos": {"x": 5, "y": 6}, "size": {"w": 300, "h": 120}, "style": {"color": "#aa0000"}}},
        "notes": [note()], "group_styles": {}})
    assert st == 200, r
    assert r["ok"] and r["hash"] == h0 and r["saved_at"]
    d2 = load(b)
    c = next(c for c in d2["cards"] if c["id"] == cid)
    assert c["pos"] == {"x": 5, "y": 6} and c["style"] == {"color": "#aa0000"}
    assert any(c["id"] == "note-1" for c in d2["cards"])
    assert R.semantic_hash(d2) == h0
    st, _ = call(base, "PATCH", "/board/demo-board/visual", {"notes": [note(text="v2")]})
    assert st == 200 and next(c for c in load(b)["cards"] if c["id"] == "note-1")["fields"]["text"] == "v2"
    st, _ = call(base, "PATCH", "/board/demo-board/visual", {"notes_removed": ["note-1"]})
    assert st == 200 and not any(c["id"] == "note-1" for c in load(b)["cards"])
    st, _ = call(base, "PATCH", "/board/demo-board/visual", {"cards": {cid: {"style": None}}})
    assert st == 200 and "style" not in next(c for c in load(b)["cards"] if c["id"] == cid)


def test_behaviour_keys_refused_and_nothing_written(srv):
    base, b = srv
    raw = open(b / "demo-board.json", "rb").read()
    n = len(hist(b))
    cid = load(b)["cards"][0]["id"]
    assert call(base, "PATCH", "/board/demo-board/visual", {"cards": {cid: {"enabled": False}}})[0] == 422
    assert call(base, "PATCH", "/board/demo-board/visual", {"notes": [note(cid)]})[0] == 422  # collides with a non-note card
    assert open(b / "demo-board.json", "rb").read() == raw and len(hist(b)) == n


def test_409_when_overlay_changes_semantics_writes_nothing(srv, monkeypatch):
    base, b = srv
    raw = open(b / "demo-board.json", "rb").read()
    n = len(hist(b))
    orig = R.semantic_hash
    calls = {"n": 0}

    def flaky(doc):  # first call hashes old, second hashes new: make new differ
        calls["n"] += 1
        return orig(doc) + ("x" if calls["n"] == 2 else "")
    monkeypatch.setattr(R, "semantic_hash", flaky)
    cid = load(b)["cards"][0]["id"]
    st, r = call(base, "PATCH", "/board/demo-board/visual", {"cards": {cid: {"pos": {"x": 1, "y": 1}}}})
    assert st == 409 and "semantics" in r["error"]
    assert open(b / "demo-board.json", "rb").read() == raw and len(hist(b)) == n


def test_422_unknown_id_non_note_removed_and_bad_values(srv):
    base, b = srv
    raw = open(b / "demo-board.json", "rb").read()
    cid = load(b)["cards"][0]["id"]
    assert call(base, "PATCH", "/board/demo-board/visual", {"cards": {"nope": {"pos": {"x": 1, "y": 1}}}})[0] == 422
    assert call(base, "PATCH", "/board/demo-board/visual", {"notes_removed": [cid]})[0] == 422
    assert call(base, "PATCH", "/board/demo-board/visual", {"cards": {cid: {"pos": "bad"}}})[0] == 422
    assert call(base, "PATCH", "/board/nope-board/visual", {})[0] == 404
    assert call(base, "PATCH", "/board/active/visual", {})[0] == 404
    assert open(b / "demo-board.json", "rb").read() == raw


def test_history_coalesced_over_50_patches(srv):
    base, b = srv
    cid = load(b)["cards"][0]["id"]
    for i in range(50):
        st, _ = call(base, "PATCH", "/board/demo-board/visual", {"cards": {cid: {"pos": {"x": i, "y": i}}}})
        assert st == 200
    assert len(hist(b)) <= 2
    assert load(b)["cards"][0]["pos"] == {"x": 49, "y": 49}


def test_put_still_snapshots_each_write(srv):
    base, b = srv
    d = load(b)
    for i in range(3):
        d["cards"][0]["pos"] = {"x": i, "y": 0}
        assert call(base, "PUT", "/board/demo-board", d)[0] == 200
        time.sleep(0.01)
    assert len(hist(b)) >= 3
