"""Python helpers for the persona harness: temp-copy sandbox with seeded starter boards, free-port server (never 8765),
guard enforcement, and a smoke step driven through the playwright-cli binary (headless, desktop + phone viewports)."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

import guard

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(os.path.dirname(HERE))
NODE = shutil.which("node")
CLI = shutil.which("playwright-cli")
SKIP = ("__pycache__", "runs", "tickets", "proposals", "node_modules")


class GuardError(RuntimeError):
    pass


def free_port():
    while True:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        if p != 8765:
            return p


def enforce_guard(before, after):
    """Refuse (raise) when the real boards/ hash differs after a run."""
    ok, changed = guard.compare(before, after)
    if not ok:
        raise GuardError("real boards/ changed during the run: %s" % ", ".join(changed[:5]))
    return True


def make_sandbox():
    """Copy the workspace to a temp dir, replace boards/ with the seeded starter library, write a matching active.json."""
    tmp = tempfile.mkdtemp(prefix="wb-persona-py-")
    wb = os.path.join(tmp, "wb")
    shutil.copytree(SRC, wb, ignore=shutil.ignore_patterns(*SKIP, "*.png", "personas"))
    bd = os.path.join(wb, "boards")
    shutil.rmtree(bd)
    os.makedirs(bd)
    subprocess.run([NODE, os.path.join(HERE, "seed.js"), bd], check=True, capture_output=True)
    code = ("import json,sys;sys.path.insert(0,'.');import serve_v4_runs as R;d=json.load(open('boards/starter-hub.json'));"
            "json.dump({'board':'starter-hub','hash':R.semantic_hash(d),'deployed_at':'2026-10-05T00:00:00.000Z','by':'owner'},open('boards/active.json','w'),indent=2)")
    subprocess.run([sys.executable, "-c", code], cwd=wb, check=True, capture_output=True)
    return {"tmp": tmp, "wb": wb, "boards": bd}


def start_server(sb):
    port = free_port()
    assert port != 8765
    env = dict(os.environ, BOARD_PORT=str(port), WB_TICKETS_DIR=os.path.join(sb["tmp"], "tickets"))
    proc = subprocess.Popen([sys.executable, os.path.join(sb["wb"], "serve.py")], cwd=sb["wb"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = "http://127.0.0.1:%d" % port
    for _ in range(75):
        try:
            urllib.request.urlopen(base + "/boards", timeout=1).read()
            return proc, port, base
        except Exception:
            time.sleep(0.2)
    proc.kill()
    raise RuntimeError("sandbox server did not start")


def cli(session, *args, timeout=60):
    r = subprocess.run([CLI, "-s=" + session] + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def smoke_step(base, viewport, session=None):
    """Drive one scripted step (open the starter board, read card count and the Active label) through playwright-cli, headless."""
    w, h = {"desktop": (1440, 900), "phone": (390, 844)}[viewport]
    session = session or "wbp-%s-%d" % (viewport, os.getpid())
    out = {"viewport": viewport, "driver": "playwright-cli"}
    try:
        rc, o = cli(session, "open", base + "/?board=starter-hub", "--browser", "chrome", timeout=120)
        out["open_rc"] = rc
        cli(session, "resize", str(w), str(h))
        time.sleep(2.5)
        rc, o = cli(session, "eval", "() => ({cards: document.querySelectorAll('.card').length, label: (document.getElementById('nest-label')||{dataset:{}}).dataset.board, w: innerWidth, h: innerHeight})")
        out["eval_rc"] = rc
        out["raw"] = o.strip()[:300]
        out["ok"] = rc == 0 and ('"cards": 11' in o or "cards: 11" in o or "'cards': 11" in o) and ("starter-hub" in o)
        out["width_ok"] = (str(w) in o)
    finally:
        cli(session, "close")
    return out
