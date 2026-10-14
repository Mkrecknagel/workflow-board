"""B3 browser test: tickets UI. Temp copy of workflow-board, temp WB_TICKETS_DIR, loopback temp port (NOT 8765).
Run: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/browser/test_tickets.py -q"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import pytest

SRC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# F4: a free port per run (the fixed 8796 collided with a server left over from the previous suite run). Env override kept.
PORT = int(os.environ.get("BOARD_TEST_PORT_UI") or _free_port())
BASE = f"http://127.0.0.1:{PORT}"


def _launch(pw):
    try:
        return pw.chromium.launch(channel="chrome", headless=True)
    except Exception:
        return pw.chromium.launch(headless=True)


def test_tickets_ui():
    sync = pytest.importorskip("playwright.sync_api")
    tmp = tempfile.mkdtemp(prefix="wbui-")
    wb = os.path.join(tmp, "wb")
    shutil.copytree(SRC, wb, ignore=shutil.ignore_patterns("__pycache__", "runs", "tickets", "*.png", "node_modules"))
    tdir = os.path.join(tmp, "tickets")
    os.environ["WB_TICKETS_DIR"] = tdir
    sys.path.insert(0, wb)
    import importlib
    import tickets_store as TS
    TS = importlib.reload(TS)
    env = dict(os.environ, BOARD_PORT=str(PORT), WB_TICKETS_DIR=tdir)
    proc = subprocess.Popen([sys.executable, os.path.join(wb, "serve.py")], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(BASE + "/boards", timeout=1).read()
                break
            except Exception:
                time.sleep(0.2)
        with sync.sync_playwright() as pw:
            br = _launch(pw)
            pg = br.new_page(viewport={"width": 1400, "height": 900})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto(BASE + "/")
            pg.wait_for_selector(".card")
            pg.wait_for_function("window.wbTickets && document.getElementById('tkPill')")
            board = pg.evaluate("document.getElementById('picker').value")
            cid = pg.evaluate("window.wb.board.cards[0].id")
            # header pill starts at 0
            pg.wait_for_function("document.getElementById('tkPill').textContent==='tickets: 0 pending'")
            # select card, add a comment -> draft badge ○1
            pg.click(f'.card[data-id="{cid}"] .hd')
            pg.wait_for_selector("#tkNew")
            pg.fill("#tkNew", "why is this card here? <b>x</b>")
            pg.click("#tkAdd")
            pg.wait_for_function(f"(document.querySelector('.card[data-id=\"{cid}\"] .tkb')||{{}}).textContent==='○1'")
            # text rendered as text, not HTML
            assert pg.evaluate("document.querySelector('#tkList .tkt').textContent") == "why is this card here? <b>x</b>"
            assert pg.evaluate("document.querySelectorAll('#tkList .tkt b').length") == 0
            # Send -> ◔, pill 1 pending
            pg.click("#tkList .tkSend")
            pg.wait_for_function(f"document.querySelector('.card[data-id=\"{cid}\"] .tkb').textContent==='◔1'")
            pg.wait_for_function("document.getElementById('tkPill').textContent==='tickets: 1 pending'")
            # agent side (fixture): claim, reply, done -> inspector chip "done"
            tid = TS.list_tickets(board)[0]["id"]
            TS.claim(tid, "sess-test")
            TS.reply(tid, "looked, no change needed", actor="agent", session="sess-test")
            TS.set_status(tid, "done", actor="agent", session="sess-test", no_change=True)
            pg.evaluate("window.dispatchEvent(new Event('focus'))")
            pg.wait_for_function("(document.querySelector('#tkList .tkchip')||{}).textContent==='✓ done'")
            assert "done" in pg.inner_text("#tkList .tkchip")
            pg.wait_for_function("document.getElementById('tkPill').textContent==='tickets: 0 pending'")
            assert "agent: looked, no change needed" in pg.inner_text("#tkList .tkr")
            # Archive -> leaves list, summary Archived (1), expanding shows it
            pg.click("#tkList .tkArchive")
            pg.wait_for_function("document.querySelectorAll('#tkList .tk').length===0")
            pg.wait_for_function("document.getElementById('tkArchSum').textContent.startsWith('Archived (1)')")
            assert "Archived (1)" in pg.inner_text("#tkArchSum")
            pg.click("#tkArchSum")
            pg.wait_for_selector("#tkArchBody .tk", state="visible")
            assert pg.inner_text("#tkArchBody .tkt").startswith("why is this card here?")
            assert pg.evaluate("document.getElementById('tkPill').textContent") == "tickets: 0 pending"
            assert not errs, errs
            br.close()
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except Exception:
            proc.kill()
        if wb in sys.path:
            sys.path.remove(wb)
        shutil.rmtree(tmp, ignore_errors=True)
