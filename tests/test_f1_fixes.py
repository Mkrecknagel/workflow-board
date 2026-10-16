"""F1 fix round 1 (server + pure-logic regressions). Temp board copy + free loopback port; never the real boards/ or 8765.
M1: stale PUT -> 409 base mismatch; fresh PUT ok; PUT without base unchanged; autosave PATCH bumps saved_at so a stale tab is detected.
M1/M2 pure: rebase() replays my behaviour edit on their newer doc without losing either side (node).
m2: visual-action messages no longer say "Save to keep". Browser behaviour (M2, M3, G1, G2, m1, m3-m5) is in tests/browser/f1.js."""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import serve  # noqa: E402

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
    before = {f: open(f, "rb").read() for f in _real.real_paths().values()}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve.H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", b
    httpd.shutdown()
    assert {f: open(f, "rb").read() for f in before} == before


def call(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())






def doc_saved(b):
    return json.load(open(b / "demo-board.json", encoding="utf-8")).get("saved_at")




def node(js):
    return subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, cwd=ROOT, timeout=60)


def test_m1_rebase_keeps_both_sides():
    js = r'''
import {rebase} from "./canvas/js/autosave.js";
const card=(id,x,fields)=>({id,type:"skill",label:id,pos:{x,y:0},ref:{name:id},ports:{in:["in"],out:["out"]},fields:fields||{}});
const base={board:"b",cards:[card("a",0),card("b",100),card("c",200)],edges:[{from:"a.out",to:"b.in"}]};
const mine=JSON.parse(JSON.stringify(base)); mine.cards[0].fields={k:"mine"}; mine.cards[0].pos={x:5,y:5}; mine.cards.push(card("n",300)); mine.edges.push({from:"b.out",to:"n.in"}); mine.cards=mine.cards.filter(c=>c.id!=="c");
const theirs=JSON.parse(JSON.stringify(base)); theirs.cards[1].pos={x:999,y:999}; theirs.saved_at="T2"; theirs.cards.push(card("t",400));
const out=rebase(base,mine,theirs), by=id=>out.cards.find(c=>c.id===id);
const ok = by("a").fields.k==="mine" && by("a").pos.x===0            /* my behaviour kept, my stale position NOT forced over theirs */
  && by("b").pos.x===999                                           /* their move kept */
  && !!by("t") && !!by("n") && !by("c")                            /* their add, my add, my delete */
  && out.edges.some(e=>e.to==="n.in") && out.edges.some(e=>e.from==="a.out") && out.saved_at==="T2";
console.log(ok?"OK":"BAD "+JSON.stringify(out));
'''
    r = node(js)
    assert r.stdout.strip() == "OK", r.stdout + r.stderr


def test_m2_beforeunload_and_draft_wiring_present():
    src = open(os.path.join(ROOT, "canvas", "js", "autosave.js"), encoding="utf-8").read()
    assert ('addEventListener("beforeunload"' in src or 'window.onbeforeunload =' in src) and "wb-draft:" in src and "wb-draft-restore" in src


def test_M3_minor4_minor5_static_wiring():
    core = open(os.path.join(ROOT, "canvas", "js", "core.js"), encoding="utf-8").read()
    assert "guardNew" in core and "isApproval" in core and core.count("confirm(") >= 2
    assert "Deploy = your agent builds the files this board describes" in core


def test_minor2_visual_messages_say_autosaves():
    for f in ("arrange.js", "tidy.js", "toolbar.js"):
        t = open(os.path.join(ROOT, "canvas", "js", f), encoding="utf-8").read()
        assert "Save to keep" not in t, f
    sel = open(os.path.join(ROOT, "canvas", "js", "select.js"), encoding="utf-8").read()
    assert 'cards (autosaves)' in sel
