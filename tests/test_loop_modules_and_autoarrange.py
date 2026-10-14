"""B2: (1) loop-hitl / loop-retry library modules arrive pre-arranged: inserted into an empty scratch board they give 0 overlaps,
0 crossings and >=1 loop. (2) A board saved through the API (PUT, an agent build) that fails the readability check is auto-arranged
by the server (layout-only PATCH, semantic hash unchanged); a save flagged X-WB-Source: canvas is only flagged, never rearranged.
Private engine copy on a free port; never 8765 and never the real boards."""
import copy
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import _real  # noqa: E402
import serve_v4_runs as R  # noqa: E402

PW = os.environ.get("PW", "")
pytestmark = pytest.mark.skipif(not shutil.which("node") or not os.path.isdir(PW), reason="node / playwright-core missing")


def call(base, method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(base + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture(scope="module")
def srv():
    tmp = tempfile.mkdtemp(prefix="wb-loopmod-")
    wb = os.path.join(tmp, "wb")
    shutil.copytree(ROOT, wb, ignore=shutil.ignore_patterns("__pycache__", "runs", "tickets", "proposals", "*.png", "node_modules"))
    _real.add_instances(os.path.join(wb, "boards"))
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = dict(os.environ, BOARD_PORT=str(port), WB_CHECK_ON_SAVE="1", WB_CHECK_DEBOUNCE_S="0.3", WB_TICKETS_DIR=os.path.join(tmp, "tickets"))
    p = subprocess.Popen([sys.executable, os.path.join(wb, "serve.py")], cwd=wb, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(75):
        try:
            urllib.request.urlopen(base + "/boards", timeout=1).read(); break
        except Exception:
            time.sleep(0.2)
    yield base, wb
    p.terminate()
    shutil.rmtree(tmp, ignore_errors=True)


def wait_done(base, name, want_auto=False, timeout=150):
    t = time.time()
    while time.time() - t < timeout:
        st, d = call(base, "GET", f"/board/{name}/readability")
        if st == 200 and d.get("state") in ("done", "error") and not d.get("stale") and (d.get("auto_arranged") or not want_auto):
            return d
        time.sleep(0.7)
    raise AssertionError("readability check did not finish")


def scratch_board(name, cards, edges):
    return {"schema": "workflow-board/v1", "board": name, "version": 1, "execution_mode": "observe", "saved_at": "2026-10-12T16:00:00-07:00",
            "cards": cards, "edges": edges, "integrity_hash": None}


def module_cards(mod):
    return [dict(copy.deepcopy(c), origin_module=mod["name"], module_version=mod["version"]) for c in mod["cards"]]


@pytest.mark.parametrize("mname", ["loop-hitl", "loop-retry"])
def test_loop_module_inserted_into_scratch_board_is_clean(srv, mname):
    base, wb = srv
    st, mod = call(base, "GET", f"/module/{mname}")
    assert st == 200
    name = "scratch-" + mname
    st, out = call(base, "PUT", f"/board/{name}", scratch_board(name, module_cards(mod), copy.deepcopy(mod["edges"])), {"X-WB-Source": "canvas"})
    assert st == 200, out
    d = wait_done(base, name)
    assert d["state"] == "done", d
    assert d["overlaps"] == [] and d["crossings"] == [] and d["pass"] is True, d
    assert d["loops"] >= 1, d
    assert not d.get("auto_arranged")  # canvas-flagged and pre-arranged: nothing had to move


def test_api_put_of_overlapping_board_is_auto_arranged_semantic_hash_unchanged(srv):
    base, wb = srv
    _, mod = call(base, "GET", "/module/loop-hitl")
    cards = module_cards(mod)
    for c in cards:  # deliberately stack every card on the same spot
        c["pos"] = {"x": 100, "y": 100}
    name = "scratch-api-overlap"
    board = scratch_board(name, cards, copy.deepcopy(mod["edges"]))
    want_hash = R.semantic_hash(board)
    st, out = call(base, "PUT", f"/board/{name}", board)  # no canvas header = an agent build
    assert st == 200 and out.get("readability") == "pending" and out.get("auto_arrange") == "on_failure", out
    d = wait_done(base, name, want_auto=True)
    assert d["state"] == "done" and d["pass"] is True and d["overlaps"] == [] and d["crossings"] == [], d
    assert d["auto_arranged"] is True and d["before"]["overlaps"] >= 1
    _, cur = call(base, "GET", f"/board/{name}")
    assert R.semantic_hash(cur) == want_hash
    assert len({(c["pos"]["x"], c["pos"]["y"]) for c in cur["cards"]}) == len(cur["cards"])


def test_canvas_put_of_overlapping_board_is_flagged_not_rearranged(srv):
    base, wb = srv
    _, mod = call(base, "GET", "/module/loop-hitl")
    cards = module_cards(mod)
    for c in cards:
        c["pos"] = {"x": 100, "y": 100}
    name = "scratch-canvas-overlap"
    st, out = call(base, "PUT", f"/board/{name}", scratch_board(name, cards, copy.deepcopy(mod["edges"])), {"X-WB-Source": "canvas"})
    assert st == 200 and "auto_arrange" not in out
    d = wait_done(base, name)
    assert d["pass"] is False and d["overlaps"] and not d.get("auto_arranged")
    _, cur = call(base, "GET", f"/board/{name}")
    assert all(c["pos"] == {"x": 100, "y": 100} for c in cur["cards"])
