#!/usr/bin/env python3
"""v5 N4 tests: run_mode schema + validator rules, pure runmode.js behaviour (node), hash sensitivity, and a browser check of the
indicator at k 0.1 / 0.5 / 1.5 (temp copy, loopback port; real boards untouched).
Run: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_runmode.py -q"""
import copy
import json
import os
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

PORT = int(os.environ.get("BOARD_TEST_PORT_RUNMODE", "8793"))
BASE = f"http://127.0.0.1:{PORT}"
PW = os.environ.get("PW", "")
NODE = shutil.which("node")
RM_JS = os.path.join(SRC, "canvas", "js", "runmode.js")


def card(cid, typ, ref, ins=("in",), outs=("out",), **kw):
    c = {"id": cid, "type": typ, "label": cid, "immutable": False, "enabled": True, "execution_mode": "observe",
         "ref": ref, "ports": {"in": list(ins), "out": list(outs)}, "pos": {"x": 0, "y": 0}}
    c.update(kw)
    return c


def board():
    cards = [
        card("kbc", "skill", {"name": "demo-compile"}, pos={"x": 40, "y": 40}),
        card("dash", "skill", {"name": "demo-dashboard"}, pos={"x": 360, "y": 40}),
        card("stp", "step", {"kind": "task"}, pos={"x": 40, "y": 240}),
        card("ag", "agent", {"seat": "fable", "route": "nontrivial_code", "verb": "inject"}, pos={"x": 360, "y": 240}),
        card("gt", "gate", {"kind": "approval", "rule": "owner approves", "verb": "deny", "draft_path": "C:/x/d.md", "approver": "owner"},
             pos={"x": 40, "y": 440}),
    ]
    return {"schema": "workflow-board/v1", "board": "rm-demo", "version": 1, "execution_mode": "observe",
            "saved_at": "2026-10-05T00:00:00Z", "cards": cards, "integrity_hash": None, "edges": []}


def bad(doc):
    with pytest.raises(V.Invalid) as e:
        V.validate_doc(doc)
    return str(e.value)


def with_rm(doc, cid, val):
    d = copy.deepcopy(doc)
    next(c for c in d["cards"] if c["id"] == cid)["run_mode"] = val
    return d


def test_baseline_and_schema_enum():
    V.validate_doc(board())
    s = V.load_schema()
    assert s["properties"]["cards"]["items"]["properties"]["run_mode"]["enum"] == ["inline", "background", "wait"]


@pytest.mark.parametrize("cid", ["dash", "stp", "ag", "kbc"])
@pytest.mark.parametrize("mode", ["inline", "background", "wait"])
def test_accepted_on_dispatchable(cid, mode):
    V.validate_doc(with_rm(board(), cid, mode))  # the kbc blocker is a UI/data concern, not a schema one


@pytest.mark.parametrize("val", ["fast", "", "BACKGROUND", None, 1, True])
def test_invalid_values_rejected(val):
    bad(with_rm(board(), "dash", val))


def test_rejected_on_gate_immutable_and_non_dispatchable():
    assert "gate" in bad(with_rm(board(), "gt", "background"))
    d = with_rm(board(), "dash", "wait")
    d["cards"][1]["immutable"] = True
    assert "immutable" in bad(d)
    for typ, ref, extra in [
        ("hook", {"event": "Stop", "file": "hooks/x.sh"}, {}),
        ("container", {"kind": "connector", "connector": "gmail"}, {}),
        ("note", {"kind": "note"}, {"fields": {"text": "hi"}}),
        ("trigger", {"event": "SessionStart"}, {}),
        ("output", {"schema": "x"}, {}),
        ("boundary", {"kind": "machine"}, {}),
        ("step", {"kind": "choice"}, {}),
        ("step", {"kind": "proposed"}, {}),
    ]:
        d = board()
        ports = {"ins": (), "outs": ()} if typ == "note" else {}
        c = card("zz", typ, ref, **ports, **extra)
        c["run_mode"] = "background"
        d["cards"].append(c)
        assert "run_mode" in bad(d), typ


def test_absent_run_mode_unchanged_and_hash_sensitive():
    import serve_v4_runs as R
    a = board()
    h0 = R.semantic_hash(a)
    b = with_rm(a, "dash", "background")
    assert R.semantic_hash(b) != h0  # behaviour: hashed
    c = with_rm(b, "dash", "wait")
    assert R.semantic_hash(c) != R.semantic_hash(b)
    d = copy.deepcopy(b)
    del d["cards"][1]["run_mode"]
    assert R.semantic_hash(d) == h0  # removing the field restores the old hash


NODE_TEST = r"""
const m = await import(process.env.RM_URL);
const out = {};
const sk = { id: "a", type: "skill", ref: { name: "demo-dashboard" } };
out.cycle = [m.cycle(sk).mode, m.cycle(sk).mode, m.cycle(sk).mode];
out.afterFull = Object.prototype.hasOwnProperty.call(sk, "run_mode");
m.cycle(sk);
out.onlyWhenNonInline = sk.run_mode;
const kb = { id: "k", type: "skill", ref: { name: "demo-compile" } };
const r = m.cycle(kb);
out.blocked = { ok: r.ok, text: r.blocker, field: Object.prototype.hasOwnProperty.call(kb, "run_mode") };
out.free = m.blockerFor(sk);
out.gate = m.isEligible({ type: "gate", ref: { kind: "approval" } });
out.imm = m.isEligible({ type: "skill", immutable: true, ref: { name: "x" } });
out.note = m.isEligible({ type: "note", ref: {} });
out.structStep = m.isEligible({ type: "step", ref: { kind: "choice" } });
out.counts = m.counts({ cards: [{ type: "skill", ref: { name: "a" }, run_mode: "background" }, { type: "step", ref: { kind: "task" }, run_mode: "wait" }, { type: "skill", ref: { name: "b" } }, { type: "gate", ref: {}, run_mode: "wait" }] });
out.badMode = m.modeOf({ run_mode: "bogus" });
console.log(JSON.stringify(out));
"""


def test_pure_logic_in_node(tmp_path):
    if not NODE:
        pytest.skip("node missing")
    f = tmp_path / "t.mjs"
    f.write_text(NODE_TEST, encoding="utf-8")
    url = "file:///" + RM_JS.replace("\\", "/")
    r = subprocess.run([NODE, str(f)], env=dict(os.environ, RM_URL=url), capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr[-400:]
    o = json.loads(r.stdout.strip().splitlines()[-1])
    assert o["cycle"] == ["background", "wait", "inline"] and o["afterFull"] is False
    assert o["onlyWhenNonInline"] == "background"
    assert o["blocked"]["ok"] is False and o["blocked"]["field"] is False
    assert "KB-writer agent" in o["blocked"]["text"] and ".compile-active" in o["blocked"]["text"]
    assert o["free"] is None and o["gate"] is False and o["imm"] is False and o["note"] is False and o["structStep"] is False
    assert o["counts"] == {"inline": 1, "background": 1, "wait": 1, "nonInline": 2} and o["badMode"] == "inline"


def test_runmode_js_is_data_only_no_network_or_storage():
    s = open(RM_JS, encoding="utf-8").read()
    for bad_tok in ("fetch(", "XMLHttpRequest", "localStorage", "sessionStorage", "indexedDB", "innerHTML"):
        assert bad_tok not in s, bad_tok


def test_module_listed_by_loader():
    import serve_v4
    assert "runmode" in serve_v4.canvas_modules()


# ---------------- browser (temp copy, loopback) ----------------

@pytest.fixture(scope="module")
def server():
    tmp = tempfile.mkdtemp(prefix="wb-v5n4-")
    wb = os.path.join(tmp, "wb")
    shutil.copytree(SRC, wb, ignore=shutil.ignore_patterns("__pycache__", "runs", "tickets", "proposals", "node_modules", "*.png", "executor", ".git"))
    _real.add_instances(os.path.join(wb, "boards"))
    bd = os.path.join(wb, "boards")
    with open(os.path.join(bd, "rm-demo.json"), "w", encoding="utf-8") as f:
        json.dump(board(), f, indent=2)
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
    yield {"tmp": tmp, "wb": wb, "bd": bd}
    proc.kill()
    proc.wait(timeout=10)
    shutil.rmtree(tmp, ignore_errors=True)


DRIVER = r"""
const { chromium } = require(process.env.PW);
const out = {}, errs = [];
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 900 } })).newPage();
  page.on('pageerror', e => errs.push('pageerror: ' + e));
  await page.goto(process.env.WB_BASE + '/?board=rm-demo'); await page.waitForSelector('.card');
  await page.waitForFunction(() => window.wbRunMode && document.querySelector('.card[data-id=dash] .rm button'));
  const btn = id => page.locator('.card[data-id=' + id + '] .rm button');
  out.initial = await btn('dash').textContent();
  out.hasGate = await page.locator('.card[data-id=gt] .rm').count();
  out.blockedText = await page.locator('.card[data-id=kbc] .rm .rm-need').textContent();
  out.blockedBtn = await btn('kbc').textContent();
  await btn('dash').click(); out.c1 = await btn('dash').textContent();
  out.field1 = await page.evaluate(() => window.wb.board.cards.find(c => c.id === 'dash').run_mode);
  await btn('dash').click(); out.c2 = await btn('dash').textContent();
  await btn('stp').click();
  await btn('kbc').click(); out.kbcField = await page.evaluate(() => window.wb.board.cards.find(c => c.id === 'kbc').run_mode || null);
  out.counts = await page.evaluate(() => window.wbRunMode.counts());
  async function at(k) {
    await page.evaluate(k => window.wb.setView({ k, x: 60, y: 60 }), k);
    await page.waitForTimeout(1500);
    return await page.evaluate(() => {
      const vp = document.getElementById('vp'), bd = document.getElementById('rm-badge');
      const vis = e => { if (!e) return false; const r = e.getBoundingClientRect(), s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' };
      const chip = document.querySelector('.card[data-id=dash] .rm button');
      return { lod: vp.dataset.lod, badgeVisible: vis(bd), badgeText: bd ? bd.textContent : '', chipVisible: vis(chip) };
    });
  }
  out.k15 = await at(1.5); out.k05 = await at(0.5); out.k01 = await at(0.1);
  out.errs = errs;
  await browser.close();
  console.log(JSON.stringify(out));
})().catch(e => { console.log(JSON.stringify({ fatal: String(e), errs })); });
"""


def test_indicator_and_toggle_in_browser(server):
    if not NODE or not os.path.isdir(PW):
        pytest.skip("node/playwright-core missing")
    drv = os.path.join(server["tmp"], "rmdrv.js")
    with open(drv, "w", encoding="utf-8") as f:
        f.write(DRIVER)
    r = subprocess.run([NODE, drv], env=dict(os.environ, PW=PW, WB_BASE=BASE), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    last = [ln for ln in r.stdout.splitlines() if ln.strip().startswith("{")]
    assert last, (r.stdout[-300:], r.stderr[-300:])
    ui = json.loads(last[-1])
    if "fatal" in ui and "Chrome" in ui["fatal"]:
        pytest.skip("Chrome not available")
    assert "fatal" not in ui, ui
    assert ui["errs"] == [], ui
    assert "inline" in ui["initial"] and ui["hasGate"] == 0
    assert "needs change" in ui["blockedBtn"] and "KB-writer agent" in ui["blockedText"] and ui["kbcField"] is None
    assert "background" in ui["c1"] and ui["field1"] == "background" and "wait" in ui["c2"]
    assert ui["counts"]["nonInline"] == 2  # dash=wait, stp=background; blocked kbc stays inline
    # LOD 1 silhouette (k 0.1): count badge with text; k 0.5 / 1.5: the card chip itself with glyph + text
    assert ui["k01"]["lod"] == "1" and ui["k01"]["badgeVisible"] and "2 non-inline" in ui["k01"]["badgeText"], ui["k01"]
    assert ui["k05"]["lod"] == "2" and ui["k05"]["chipVisible"], ui["k05"]
    assert ui["k15"]["lod"] == "2" and ui["k15"]["chipVisible"], ui["k15"]
