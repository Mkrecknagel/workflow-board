"""B5 end-to-end: browser -> server -> CLI -> mod -> browser, on a temp copy, temp WB_TICKETS_DIR, loopback temp port (NOT 8765).
Run: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python tests/e2e_tickets.py   (exit 0 = pass)
Board JSON sha256 is checked unchanged after every step."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _real  # noqa: E402
os.environ["BOARD_REGISTRY_ROOT"] = tempfile.mkdtemp(prefix="wb-emptyreg-")  # temp-copy servers see only their own boards
PORT = int(os.environ.get("BOARD_TEST_PORT_E2E", "8797"))
BASE = f"http://127.0.0.1:{PORT}"

HARNESS = """
import { register } from '%s/executor/hooks/register.tsx'
import fs from 'node:fs'
const handlers = {}
const on = (name, a, b) => { handlers[name] = b || a; if (b) handlers[name + ':' + a.command] = b }
register(on, { root: process.env.WB_ROOT })
const map = p => { const t = process.env.WB_ROOT + '/tickets/'; return p.startsWith(t) ? process.env.WB_TICKETS_DIR.split(String.fromCharCode(92)).join('/') + '/' + p.slice(t.length) : p }
const $ = { fs: { exists: async p => fs.existsSync(map(p)), read: async p => fs.readFileSync(map(p), 'utf8'), write: async () => { throw new Error('mod must not write') } },
  clock: { now: async () => Date.now() }, command: { register: async () => {} }, process: { run: async () => ({ code: 0, stdout: '', stderr: '' }) } }
const h = handlers['command.run:board'] || handlers['command.run']
console.log((await h($, { args: process.argv[2] })).text)
"""


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def cli(wb, env, *args):
    r = subprocess.run([sys.executable, os.path.join(wb, "tickets.py"), *args], env=env, capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout, r.stderr


def mod(wb, env, tmp, args):
    f = os.path.join(tmp, "harness.mjs")
    open(f, "w", encoding="utf-8").write(HARNESS % wb.replace("\\", "/"))
    r = subprocess.run(["bun", f, args], env=dict(env, WB_ROOT=wb.replace("\\", "/")), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


def main():
    from playwright.sync_api import sync_playwright
    tmp = tempfile.mkdtemp(prefix="wbe2e-")
    wb = os.path.join(tmp, "wb")
    shutil.copytree(SRC, wb, ignore=shutil.ignore_patterns("__pycache__", "runs", "tickets", "*.png", "node_modules"))
    _real.add_instances(os.path.join(wb, "boards"))
    tdir = os.path.join(tmp, "tickets")
    env = dict(os.environ, BOARD_PORT=str(PORT), WB_TICKETS_DIR=tdir)
    proc = subprocess.Popen([sys.executable, os.path.join(wb, "serve.py")], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(BASE + "/boards", timeout=1).read()
                break
            except Exception:
                time.sleep(0.2)
        boards = os.path.join(wb, "boards")
        base = {f: sha(os.path.join(boards, f)) for f in os.listdir(boards) if f.endswith(".json")}

        def unchanged(step):
            now = {f: sha(os.path.join(boards, f)) for f in base}
            assert now == base, f"board files changed at step: {step}"

        with sync_playwright() as pw:
            try:
                br = pw.chromium.launch(channel="chrome", headless=True)
            except Exception:
                br = pw.chromium.launch(headless=True)
            pg = br.new_page(viewport={"width": 1400, "height": 900})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto(BASE + "/")
            pg.wait_for_selector(".card")
            pg.wait_for_function("window.wbTickets && document.getElementById('tkPill')")
            board = pg.evaluate("document.getElementById('picker').value")
            cid = pg.evaluate("window.wb.board.cards[0].id")
            # 1. browser creates comment
            pg.click(f'.card[data-id="{cid}"] .hd')
            pg.wait_for_selector("#tkNew")
            pg.fill("#tkNew", "e2e: tighten this card's label")
            pg.click("#tkAdd")
            pg.wait_for_function(f"(document.querySelector('.card[data-id=\"{cid}\"] .tkb')||{{}}).textContent==='○1'")
            unchanged("create")
            # 2. Send
            pg.click("#tkList .tkSend")
            pg.wait_for_function("document.getElementById('tkPill').textContent==='tickets: 1 pending'")
            unchanged("send")
            # 3. tickets.py list shows it
            rc, out, err = cli(wb, env, "list", "--board", board)
            assert rc == 0 and "submitted" in out and "tighten this card" in out, (rc, out, err)
            tid = out.split("\t")[0]
            # 4. /board tickets (mod harness) shows it
            mt = mod(wb, env, tmp, "tickets")
            assert tid in mt and f"{board}/{cid}" in mt, mt
            assert "<untrusted_ticket_content" in mod(wb, env, tmp, f"ticket {tid}")
            # 5. claim, reply --link, status done
            link = "C:/example/commit-abc123"
            assert cli(wb, env, "claim", tid, "--session", "e2e-sess")[0] == 0
            assert cli(wb, env, "claim", tid, "--session", "other")[0] == 2  # EEXIST
            assert cli(wb, env, "reply", tid, "--text", "label tightened", "--link", link, "--session", "e2e-sess")[0] == 0
            rc, out, err = cli(wb, env, "status", tid, "done", "--session", "e2e-sess", "--link", link)
            assert rc == 0, (out, err)
            unchanged("agent flow")
            # 6. browser poll shows chip done + reply text
            pg.evaluate("window.dispatchEvent(new Event('focus'))")
            pg.wait_for_function("(document.querySelector('#tkList .tkchip')||{}).textContent==='✓ done'", timeout=15000)
            assert "agent: label tightened" in pg.inner_text("#tkList .tkr")
            pg.wait_for_function("document.getElementById('tkPill').textContent==='tickets: 0 pending'")
            # 7. Archive in browser -> fold
            pg.click("#tkList .tkArchive")
            pg.wait_for_function("document.getElementById('tkArchSum').textContent.startsWith('Archived (1)')")
            pg.click("#tkArchSum")
            pg.wait_for_selector("#tkArchBody .tk", state="visible")
            assert "label tightened" in pg.inner_text("#tkArchBody")
            unchanged("archive")
            assert not errs, errs
            br.close()
        # live file no longer holds it; archive does
        assert not [t for t in json.load(open(os.path.join(tdir, f"{board}.json")))["tickets"] if t["id"] == tid]
        assert tid in open(os.path.join(tdir, "archive", f"{board}.jsonl"), encoding="utf-8").read()
        unchanged("final")
        print("PASS")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except Exception:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
