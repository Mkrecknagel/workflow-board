#!/usr/bin/env python3
"""v4 N2 tests: connector inventory (allow-list, counts, no secret-like strings), /inventory + /templates/touches routes,
container card DOM (blocked rows have no executable affordance), capability wiring validates, gallery flags blocked-tool modules.
Server + browser run against a TEMP COPY of workflow-board on a loopback port; real boards/ are only read.
Run: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_inventory.py -q"""
import importlib.util
import json
import os
import re
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
import library as L  # noqa: E402

spec = importlib.util.spec_from_file_location("inv", os.path.join(SRC, "adapters", "connectors", "inventory.py"))
INV = importlib.util.module_from_spec(spec)
spec.loader.exec_module(INV)

PORT = int(os.environ.get("BOARD_TEST_PORT_N2", "8792"))
BASE = f"http://127.0.0.1:{PORT}"
PW = os.environ.get("PW", "")
NODE = shutil.which("node")
SECRET = [re.compile(p) for p in INV.SECRET_PATTERNS]
BLOCKED_MUST = {"send_message", "reply", "forward", "share_file"}


@pytest.fixture(scope="module")
def inv():
    return INV.build()


def test_five_connectors_counts_and_no_secrets(inv):
    assert sorted(inv["connectors"]) == ["calendar", "discord", "drive", "gmail"]
    for cid, c in inv["connectors"].items():
        for k in ("tools", "hooks", "skills"):
            assert c["counts"][k] == len(c[k]), (cid, k)
        assert c["account"] and c["sources"] and c["generated_at"]
    for s in re.findall(r'"((?:[^"\\]|\\.)*)"', json.dumps(inv)):
        for rx in SECRET:
            assert not rx.search(s), s
    assert INV.problems(inv) == []






def test_unknown_tools_default_blocked():
    for cid in INV.CONNECTORS:
        for n in ("frobnicate", "send_message", "mcp__x__do_everything", "delete_thing", "trash_message", "label_thread", ""):
            assert INV.classify(cid, n)[1] == "blocked", (cid, n)
    assert INV.classify("gmail", "reply")[1] == "blocked"
    assert INV.classify("gmail", "mcp__claude_ai_Gmail__send_message")[1] == "blocked"
    assert INV.classify("discord", "reply")[1].startswith("channel-reply")


def test_secret_like_account_is_refused_by_problems(inv):
    bad = json.loads(json.dumps(inv))
    bad["connectors"]["gmail"]["account"] = "Bearer abcdefgh12345678"
    assert INV.problems(bad)
    bad = json.loads(json.dumps(inv))
    bad["connectors"]["gmail"]["tools"].append({"name": "send_message", "class": "read", "status": "ok", "source": "x"})
    bad["connectors"]["gmail"]["counts"]["tools"] += 1
    assert INV.problems(bad)


def test_gallery_touches_flag_blocked_modules():
    mod = {"name": "x", "cards": [
        {"type": "tool", "ref": {"name": "mcp__claude_ai_Gmail__send_message"}},
        {"type": "tool", "ref": {"name": "mcp__claude_ai_Gmail__create_draft"}},
        {"type": "step", "ref": {"note": "uses mcp__claude_ai_Google_Drive__share_file"}},
        {"type": "tool", "ref": {"name": "mcp__unknown_srv__do_thing"}}]}
    t = L.module_touches(mod)
    assert "mcp__claude_ai_Gmail__send_message" in t["blocked"]
    assert "mcp__claude_ai_Google_Drive__share_file" in t["blocked"]
    assert "mcp__unknown_srv__do_thing" in t["blocked"]  # unknown -> blocked
    assert "mcp__claude_ai_Gmail__create_draft" not in t["blocked"]
    assert L.module_touches({"name": "y", "cards": [{"type": "step", "ref": {}}]})["blocked"] == []
    for it in L._scan_modules_list():  # real modules carry a touches summary
        assert "touches" in it


# ---------------- server + browser (temp copy, loopback) ----------------

def get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def card(cid, typ, ref, ins=(), outs=(), **kw):
    c = {"id": cid, "type": typ, "label": cid, "immutable": False, "enabled": True, "execution_mode": "observe",
         "ref": ref, "ports": {"in": list(ins), "out": list(outs)}, "pos": {"x": 0, "y": 0}}
    c.update(kw)
    return c


def make_board():
    return {"schema": "workflow-board/v1", "board": "cx-demo", "version": 1, "execution_mode": "observe", "saved_at": "2026-10-05T00:00:00Z",
            "integrity_hash": None, "edges": [],
            "cards": [card("gm", "container", {"kind": "connector", "connector": "gmail", "account": "Alice (primary mailbox)"},
                           fields={"inventory_key": "gmail"}),
                      card("tgt", "step", {"kind": "task"}, ins=["in"], outs=["out"]),
                      card("tgt2", "step", {"kind": "task"}, ins=["in"], outs=["out"])]}


BLOCKED_MODULE = {"schema": "workflow-board/module/v1", "name": "bad-send", "version": 1, "description": "fixture: references a blocked tool",
                  "created_at": "2026-10-05T00:00:00-07:00", "origin_board": "x", "exposed_ports": {"in": [], "out": []},
                  "cards": [card("t1", "tool", {"name": "mcp__claude_ai_Gmail__send_message"}, ins=["call"], outs=["result"])], "edges": []}


@pytest.fixture(scope="module")
def server():
    tmp = tempfile.mkdtemp(prefix="wb-v4n2-")
    wb = os.path.join(tmp, "wb")
    shutil.copytree(SRC, wb, ignore=shutil.ignore_patterns("__pycache__", "runs", "tickets", "proposals", "node_modules", "*.png", "executor", ".git"))
    _real.add_instances(os.path.join(wb, "boards"))
    bd = os.path.join(wb, "boards")
    with open(os.path.join(bd, "cx-demo.json"), "w", encoding="utf-8") as f:
        json.dump(make_board(), f, indent=2)
    with open(os.path.join(wb, "modules", "bad-send.module.json"), "w", encoding="utf-8") as f:
        json.dump(BLOCKED_MODULE, f, indent=2)
    env = dict(os.environ, WB_BOARD_DIR=bd, WB_TICKETS_DIR=os.path.join(tmp, "tickets"), WB_DEDUP_DIR=os.path.join(tmp, "dedup"),
               WB_PROPOSALS_DIR=os.path.join(tmp, "proposals"), BOARD_PORT=str(PORT))
    proc = subprocess.Popen([sys.executable, os.path.join(wb, "serve.py")], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(60):
        try:
            urllib.request.urlopen(BASE + "/boards", timeout=1).read()
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("server did not start")
    yield {"tmp": tmp, "wb": wb}
    proc.kill()
    proc.wait(timeout=10)
    shutil.rmtree(tmp, ignore_errors=True)


def test_inventory_routes(server):
    code, body = get("/inventory")
    assert code == 200 and sorted(json.loads(body)["connectors"]) == ["calendar", "discord", "drive", "gmail"]
    code, body = get("/inventory/gmail")
    assert code == 200 and json.loads(body)["connector"] == "gmail"
    assert get("/inventory/nope")[0] == 404
    code, body = get("/templates/touches")
    j = json.loads(body)
    assert code == 200 and "bad-send" in j["blocked_modules"]
    code, body = get("/library")
    mods = {i["name"]: i for i in json.loads(body)["items"] if i["kind"] == "module"}
    assert mods["bad-send"]["touches"]["blocked"] and not mods["post-x"]["touches"]["blocked"]
    assert get("/v4/modules")[0] == 200 and "containers" in json.loads(get("/v4/modules")[1])["modules"]


DRIVER = r"""
const { chromium } = require(process.env.PW);
const out = {}, errs = [];
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 900 } })).newPage();
  page.on('pageerror', e => errs.push('pageerror: ' + e));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
  await page.goto(process.env.WB_BASE + '/?board=cx-demo'); await page.waitForSelector('.card');
  await page.waitForSelector('.card[data-type=container] .cx-row', { timeout: 15000 });
  out.acct = await page.textContent('.card[data-type=container] .cx-acct');
  out.counts = await page.locator('.cx-counts span').allTextContents();
  out.sections = await page.locator('.card[data-type=container] .cx-sec > summary').allTextContents();
  out.rows = await page.$$eval('.cx-tools .cx-row', rs => rs.map(r => ({ tool: r.dataset.tool, status: r.dataset.status, blocked: r.classList.contains('blocked'),
    buttons: r.querySelectorAll('button,a,input,[draggable],[data-cap],[onclick]').length, draggable: r.draggable === true })));
  out.hooksListed = await page.locator('.cx-hooks .cx-li').count();
  out.skillsListed = await page.locator('.cx-skills .cx-li').count();
  out.wireButtons = await page.locator('.cx-wire').count();
  out.wireBlocked = await page.evaluate(() => window.wbContainers.wire('gm', 'send_message', 'tgt'));
  out.wireUnknown = await page.evaluate(() => window.wbContainers.wire('gm', 'zzz_unknown', 'tgt'));
  out.wireOk = await page.evaluate(() => window.wbContainers.wire('gm', 'create_draft', 'tgt'));
  out.board = await page.evaluate(() => JSON.parse(JSON.stringify(window.wb.board)));
  await page.waitForTimeout(400);
  out.edgesDrawn = await page.locator('#edges path, #edges line, svg path').count();
  out.errs = errs;
  await browser.close();
  console.log(JSON.stringify(out));
})().catch(e => { console.log(JSON.stringify({ fatal: String(e), errs })); });
"""


def test_container_dom_and_wiring(server):
    if not NODE or not os.path.isdir(PW):
        pytest.skip("node/playwright-core missing")
    drv = os.path.join(server["tmp"], "n2drv.js")
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
    assert "Alice" in ui["acct"] and len(ui["counts"]) == 3 and len(ui["sections"]) == 3
    gm = {t["name"]: t for t in json.load(open(os.path.join(SRC, "inventory", "connectors.json")))["connectors"]["gmail"]["tools"]}
    assert len(ui["rows"]) == len(gm) and ui["hooksListed"] >= 0
    blocked = [r for r in ui["rows"] if r["blocked"]]
    assert {"send_message", "reply", "forward"} <= {r["tool"] for r in blocked}
    for r in blocked:  # no executable affordance anywhere in a blocked row
        assert r["buttons"] == 0 and r["draggable"] is False and r["status"] == "blocked", r
    for r in ui["rows"]:
        assert r["blocked"] == (gm[r["tool"]]["status"] == "blocked")
    assert ui["wireButtons"] == len([r for r in ui["rows"] if not r["blocked"]]) > 0
    assert ui["wireBlocked"]["ok"] is False and ui["wireUnknown"]["ok"] is False
    assert ui["wireOk"]["ok"] is True
    b = ui["board"]
    tgt = next(c for c in b["cards"] if c["id"] == "tgt")
    assert tgt["ref"]["via_container"] == "gm"
    assert {"from": "gm.cap:create_draft", "to": "tgt.in"} in b["edges"]
    V.validate_doc(b)  # edge + via_container validate under the v4 schema
