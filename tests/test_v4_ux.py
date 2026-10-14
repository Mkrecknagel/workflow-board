"""N4 tests: tidy (idempotent + undo), collapse (group_styles only, exposed ports), toolbar (390px, touch), shortcuts.
Runs against a TEMP COPY of the workbench on a loopback port; never touches real boards."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import pytest

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)
import _real  # noqa: E402
import validate as V  # noqa: E402

PORT = int(os.environ.get("BOARD_TEST_PORT_V4UX", "8794"))
BASE = f"http://127.0.0.1:{PORT}"
PW = os.environ.get("PW", "")
NODE = shutil.which("node")
JS = ["collapse.js", "tidy.js", "toolbar.js"]


def card(cid, group, x, y):
    return {"id": cid, "type": "step", "label": cid, "immutable": False, "enabled": True, "execution_mode": "observe",
            "ref": {"kind": "task"}, "ports": {"in": ["i"], "out": ["o"]}, "pos": {"x": x, "y": y}, **({"group": group} if group else {})}


def ux_board():
    cards = [card("a3", "A", 40, 520), card("a1", "A", 600, 40), card("a2", "A", 120, 300),
             card("b2", "B", 900, 80), card("b1", "B", 520, 600), card("x1", None, 1000, 700)]
    edges = [{"from": "x1.o", "to": "a1.i"}, {"from": "a1.o", "to": "a2.i"}, {"from": "a2.o", "to": "a3.i"},
             {"from": "a3.o", "to": "b1.i"}, {"from": "b1.o", "to": "b2.i"}]
    return {"schema": "workflow-board/v1", "board": "v4-ux", "version": 1, "execution_mode": "observe",
            "saved_at": "2026-10-05T00:00:00Z", "cards": cards, "edges": edges, "integrity_hash": None}


def test_fixture_board_valid():
    V.validate_doc(ux_board())


@pytest.mark.parametrize("f", JS)
def test_node_check(f):
    r = subprocess.run([NODE, "--check", os.path.join(SRC, "canvas", "js", f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def read(f):
    with open(os.path.join(SRC, "canvas", "js", f), encoding="utf-8") as fh:
        return fh.read()


def test_no_select_js_key_shadowing():
    # select.js owns Ctrl/Cmd + c x v a; N4 keys are Alt-only and ignore Ctrl/Meta.
    src = read("toolbar.js")
    assert "ev.altKey" in src and "ev.ctrlKey || ev.metaKey" in src
    for f in JS:
        assert "ev.key===\"c\"" not in read(f).replace(" ", "")
    keys = set(re.findall(r'k === "(\w)"', src))
    assert keys.isdisjoint({"c", "x", "v", "a"}), keys


def test_no_storage_outside_group_styles():
    for f in JS:
        s = read(f)
        assert "localStorage" not in s and "sessionStorage" not in s and "indexedDB" not in s, f
        assert "fetch(" not in s and "XMLHttpRequest" not in s, f  # no network writes


@pytest.fixture(scope="module")
def driver_out():
    if not NODE:
        pytest.skip("node missing")
    tmp = tempfile.mkdtemp(prefix="wb-v4ux-")
    wb = os.path.join(tmp, "wb")
    shutil.copytree(SRC, wb, ignore=shutil.ignore_patterns("__pycache__", "runs", "tickets", "proposals", "node_modules", "*.png", "executor", ".git"))
    _real.add_instances(os.path.join(wb, "boards"))
    bd = os.path.join(wb, "boards")
    with open(os.path.join(bd, "v4-ux.json"), "w", encoding="utf-8") as f:
        json.dump(ux_board(), f, indent=2)
    env = dict(os.environ, WB_BOARD_DIR=bd, WB_TICKETS_DIR=os.path.join(tmp, "tickets"), WB_DEDUP_DIR=os.path.join(tmp, "dedup"),
               WB_PROPOSALS_DIR=os.path.join(tmp, "proposals"), BOARD_PORT=str(PORT))
    proc = subprocess.Popen([sys.executable, os.path.join(wb, "serve.py")], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(BASE + "/boards", timeout=1).read()
                break
            except Exception:
                time.sleep(0.2)
        else:
            pytest.fail("server did not start")
        mods = json.loads(urllib.request.urlopen(BASE + "/v4/modules", timeout=5).read())["modules"]
        assert {"collapse", "tidy", "toolbar"} <= set(mods), mods
        drv = os.path.join(SRC, "tests", "browser", "v4_ux.js")
        r = subprocess.run([NODE, drv], env=dict(os.environ, PW=PW, WB_BASE=BASE, WB_BOARD="v4-ux"), capture_output=True, text=True, timeout=180)
        lines = [ln for ln in r.stdout.splitlines() if ln.startswith("{")]
        assert lines, (r.stdout[-400:], r.stderr[-400:])
        o = json.loads(lines[-1])
        assert "fatal" not in o, o.get("fatal")
        yield o
    finally:
        proc.kill()
        proc.wait(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)












