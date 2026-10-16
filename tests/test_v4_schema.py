#!/usr/bin/env python3
"""v4 N1 tests: schema/validator for note, container, approval gate, pin, on_fail, collapsed; serve_v4 route skeleton;
kinds.js render in a real browser (skipped if node/playwright/Chrome are missing). Server + browser run against a TEMP COPY
of workflow-board on a loopback port; real boards/ are only read. Run: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_v4_schema.py -q"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import pytest

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)
import _real  # noqa: E402
import validate as V  # noqa: E402

PORT = int(os.environ.get("BOARD_TEST_PORT_V4", "8797"))
BASE = f"http://127.0.0.1:{PORT}"
PW = os.environ.get("PW", "")
NODE = shutil.which("node")


def card(cid, typ, ref, ins=(), outs=(), **kw):
    c = {"id": cid, "type": typ, "label": cid, "immutable": False, "enabled": True, "execution_mode": "observe",
         "ref": ref, "ports": {"in": list(ins), "out": list(outs)}, "pos": {"x": 0, "y": 0}}
    c.update(kw)
    return c


def v4_board():
    cards = [
        card("trig", "trigger", {"event": "SessionStart"}, outs=["go"],
             pin={"port": "go", "sample": {"session": "synthetic-1"}, "synthetic": True}),
        card("appr", "gate", {"kind": "approval", "rule": "owner approves the draft", "verb": "deny",
                              "draft_path": "/tmp/example/draft.md", "approver": "owner"},
             ins=["in"], outs=["ok"], on_fail={"target": "note-1", "record": "telemetry"}),
        card("note-1", "note", {"kind": "note"}, fields={"text": "Reminder: approval is the owner's own message."},
             size={"w": 220, "h": 90}),
        card("gm", "container", {"kind": "connector", "connector": "gmail", "account": "Alice (primary mailbox)"},
             outs=["cap:create_draft"], fields={"inventory_key": "gmail"}),
        card("tgt", "step", {"kind": "task", "via_container": "gm"}, ins=["in"]),
    ]
    return {"schema": "workflow-board/v1", "board": "v4-demo", "version": 1, "execution_mode": "observe",
            "saved_at": "2026-10-05T00:00:00Z", "cards": cards, "integrity_hash": None,
            "edges": [{"from": "trig.go", "to": "appr.in"}, {"from": "gm.cap:create_draft", "to": "tgt.in"}]}


def bad(doc):
    with pytest.raises(V.Invalid) as e:
        V.validate_doc(doc)
    return str(e.value)


def test_v4_board_valid():
    V.validate_doc(v4_board())


@pytest.mark.parametrize("path", sorted(_real.real_paths().values()))
def test_existing_boards_unchanged(path):
    name = os.path.basename(path)[:-5]
    if name == "active":
        pytest.skip("active.json is the deploy pointer, not a board")
    with open(path, encoding="utf-8") as f, _real.real_registry():
        V.validate_doc(json.load(f), name=name)




def test_enforce_rejected_on_note_and_container():
    d = v4_board()
    next(c for c in d["cards"] if c["id"] == "note-1")["execution_mode"] = "enforce"
    assert "enforce" in bad(d)
    d = v4_board()
    next(c for c in d["cards"] if c["id"] == "gm")["execution_mode"] = "enforce"
    assert "enforce" in bad(d)


def test_pin_rules():
    d = v4_board()
    d["cards"][0]["pin"]["sample"] = {"blob": "x" * 2100}
    assert "2048" in bad(d)
    d = v4_board()
    d["cards"][0]["pin"]["synthetic"] = False
    bad(d)
    d = v4_board()
    del d["cards"][0]["pin"]["synthetic"]
    bad(d)
    d = v4_board()
    d["cards"][0]["pin"]["port"] = "nope"
    assert "not a port" in bad(d)
    d = v4_board()
    d["cards"][0]["pin"]["sample"] = {"a": "x" * 1900}
    V.validate_doc(d)  # under the limit is fine


def test_on_fail_rules():
    d = v4_board()
    d["cards"][1]["on_fail"]["target"] = "ghost"
    assert "not a card" in bad(d)
    d = v4_board()
    d["cards"][1]["on_fail"]["target"] = "trig"
    assert "must be a note" in bad(d)
    d = v4_board()
    d["cards"][1]["on_fail"]["record"] = "email"
    bad(d)


def test_approval_gate_rules():
    d = v4_board()
    d["cards"][1]["ref"]["verb"] = "inject"
    assert "deny" in bad(d)
    d = v4_board()
    d["cards"][1]["ref"]["verb"] = "send"  # rejected by the global verb list too
    bad(d)
    d = v4_board()
    d["cards"][1]["ref"]["approver"] = "agent"
    assert "owner" in bad(d)
    d = v4_board()
    del d["cards"][1]["ref"]["draft_path"]
    assert "draft_path" in bad(d)
    d = v4_board()
    d["cards"][1]["ref"]["kind"] = "budget"  # an ordinary gate is untouched by the approval rules
    del d["cards"][1]["ref"]["draft_path"]
    V.validate_doc(d)


def test_note_rules():
    d = v4_board()
    d["cards"][2]["fields"]["text"] = "x" * 1201
    assert "1200" in bad(d)
    d = v4_board()
    d["cards"][2]["ports"]["out"] = ["o"]
    assert "no ports" in bad(d)
    d = v4_board()
    d["cards"][2]["ref"]["kind"] = "sticky"
    bad(d)


def test_container_rules():
    d = v4_board()
    d["cards"][3]["ref"]["connector"] = "slack"
    assert "connector" in bad(d)
    for tok in ("ghp_" + "a" * 30, "token: abc", "A" * 40, "Bearer abcdefgh12345"):
        d = v4_board()
        d["cards"][3]["ref"]["account"] = tok
        assert "token" in bad(d), tok
    d = v4_board()
    d["cards"][3]["fields"]["inventory_key"] = ""
    bad(d)
    d = v4_board()
    d["cards"][4]["ref"]["via_container"] = "trig"
    assert "via_container" in bad(d)


def test_collapsed_group_style():
    d = v4_board()
    d["cards"][4]["group"] = "g"
    d["group_styles"] = {"g": {"collapsed": True}}
    V.validate_doc(d)
    d["group_styles"] = {"g": {"collapsed": "yes"}}
    bad(d)
    d["group_styles"] = {"g": {"folded": True}}
    bad(d)


def test_unknown_edge_port_still_rejected():
    d = v4_board()
    d["edges"][1]["from"] = "gm.cap:read_x"
    assert "no out port" in bad(d)


# ---------------- server skeleton + browser (temp copy, loopback) ----------------

def _free_port():
    import socket
    sk = socket.socket()
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
    sk.close()
    return port


def get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@pytest.fixture(scope="module")
def server():
    global PORT, BASE
    # A fixed port could already be answered by another (older) server whose boards differ: readiness then passed against
    # the wrong process and /board/v4-demo 404'd. Take a free port unless one is pinned, and wait for THIS server's own board.
    if "BOARD_TEST_PORT_V4" not in os.environ:
        PORT = _free_port()
        BASE = f"http://127.0.0.1:{PORT}"
    tmp = tempfile.mkdtemp(prefix="wb-v4n1-")
    wb = os.path.join(tmp, "wb")
    shutil.copytree(SRC, wb, ignore=shutil.ignore_patterns("__pycache__", "runs", "tickets", "proposals", "node_modules", "*.png", "executor", ".git"))
    _real.add_instances(os.path.join(wb, "boards"))
    bd = os.path.join(wb, "boards")
    with open(os.path.join(bd, "v4-demo.json"), "w", encoding="utf-8") as f:
        json.dump(v4_board(), f, indent=2)
    env = dict(os.environ, WB_BOARD_DIR=bd, WB_TICKETS_DIR=os.path.join(tmp, "tickets"), WB_DEDUP_DIR=os.path.join(tmp, "dedup"),
               WB_PROPOSALS_DIR=os.path.join(tmp, "proposals"), BOARD_PORT=str(PORT))
    proc = subprocess.Popen([sys.executable, os.path.join(wb, "serve.py")], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(100):
        if proc.poll() is not None:
            pytest.fail("server exited during start-up")
        try:
            urllib.request.urlopen(BASE + "/board/v4-demo", timeout=1).read()
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("server did not start")
    yield {"tmp": tmp, "wb": wb, "env": env}
    proc.kill()
    proc.wait(timeout=10)
    shutil.rmtree(tmp, ignore_errors=True)


def test_v4_routes_return_501_until_filled(server):
    wb = server["wb"]
    present = {m for m in ("serve_v4_inventory", "serve_v4_runs") if os.path.isfile(os.path.join(wb, m + ".py"))}
    stubs = {"/inventory": "serve_v4_inventory", "/inventory/gmail": "serve_v4_inventory", "/templates/touches": "serve_v4_inventory",
             "/runs/demo-board?card=x": "serve_v4_runs", "/history/demo-board": "serve_v4_runs", "/history/demo-board/abc": "serve_v4_runs",
             "/active": "serve_v4_runs"}
    for p, owner in stubs.items():
        code, body = get(p)
        if owner in present:
            assert code != 404, p  # a later node filled it
        else:
            assert code == 501 and json.loads(body)["error"] == "not implemented", (p, code)


def test_v4_modules_manifest_and_existing_routes(server):
    code, body = get("/v4/modules")
    assert code == 200 and json.loads(body)["modules"][0] == "kinds"
    assert get("/boards")[0] == 200
    assert get("/board/v4-demo")[0] == 200
    assert get("/canvas/js/kinds.js")[0] == 200
    assert get("/nope-v4")[0] == 404


DRIVER = r"""
const { chromium } = require(process.env.PW);
const out = {}, errs = [];
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 900 } })).newPage();
  page.on('pageerror', e => errs.push('pageerror: ' + e));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
  await page.goto(process.env.WB_BASE + '/?board=v4-demo'); await page.waitForSelector('.card');
  await page.waitForFunction(() => window.wbKinds && document.querySelector('.card[data-type=note] .v4-note'));
  out.noteText = await page.textContent('.card[data-type=note] .v4-note');
  out.noteCards = await page.locator('.card[data-type=note]').count();
  out.badges = await page.locator('.v4-badges span').allTextContents();
  out.cards = await page.locator('#cards > .card').count();
  out.containerRendered = await page.locator('.card[data-type=container]').count();
  out.paletteNote = await page.locator('.v4-palnote').count();
  await page.waitForTimeout(500);
  out.errs = errs;
  await browser.close();
  console.log(JSON.stringify(out));
})().catch(e => { console.log(JSON.stringify({ fatal: String(e), errs })); });
"""


def test_kinds_render_in_browser(server):
    if not NODE or not os.path.isdir(PW):
        pytest.skip("node/playwright-core missing")
    drv = os.path.join(server["tmp"], "v4drv.js")
    with open(drv, "w", encoding="utf-8") as f:
        f.write(DRIVER)
    r = subprocess.run([NODE, drv], env=dict(os.environ, PW=PW, WB_BASE=BASE), capture_output=True, text=True, timeout=120)
    last = [ln for ln in r.stdout.splitlines() if ln.strip().startswith("{")]
    assert last, (r.stdout[-300:], r.stderr[-300:])
    ui = json.loads(last[-1])
    if "fatal" in ui and "Chrome" in ui["fatal"]:
        pytest.skip("Chrome not available")
    assert "fatal" not in ui, ui
    assert ui["errs"] == [], ui
    assert "owner's own message" in ui["noteText"] and ui["noteCards"] == 1 and ui["cards"] == 5, ui
    assert any("approval" in b for b in ui["badges"]) and any("pinned" in b for b in ui["badges"]) and any("on fail" in b for b in ui["badges"]), ui
    assert ui["containerRendered"] == 1 and ui["paletteNote"] == 1, ui
