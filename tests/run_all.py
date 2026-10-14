#!/usr/bin/env python3
"""One command for the whole workflow-board regression:  python tests/run_all.py   (add --no-browser to skip Chrome suites).

Runs, in order: every pytest file under tests/ (PYTEST_DISABLE_PLUGIN_AUTOLOAD=1), tests/e2e_tickets.py,
validation of every registered board (engine boards/*.json + instance boards registered in project.json files; active.json excluded) and modules/*.module.json, and every tests/browser/*.js against a TEMP
COPY of this folder on a free loopback port (never 8765). v21.js builds its own server and fixtures. Prints one row per suite and
exits non-zero if any suite failed. Real boards are hashed before/after; a change is a failure."""
import glob
import hashlib
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
NODE = shutil.which("node")
PW = os.environ.get("PW", "")
sys.path.insert(0, os.path.join(SRC, "tests"))
import _real  # noqa: E402

# Temp-copy servers/subprocesses see an EMPTY board registry (only the boards copied into them); _real reads the real one explicitly.
os.environ["BOARD_REGISTRY_ROOT"] = tempfile.mkdtemp(prefix="wb-emptyreg-")
os.environ.setdefault("WB_CHECK_ON_SAVE", "0")  # check-on-save spawns Chromium; only tests/test_readability_save.py turns it on
ENV = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
rows = []


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def run(cmd, env=None, cwd=SRC, timeout=900):
    t = time.time()
    r = subprocess.run(cmd, env=env or ENV, cwd=cwd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    return r, time.time() - t


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def row(name, ok, detail):
    rows.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name.ljust(34) + detail, flush=True)


def pytest_counts(out):
    m = re.search(r"(\d+) passed", out)
    f = re.search(r"(\d+) failed", out)
    sk = re.search(r"(\d+) skipped", out)
    return (int(m.group(1)) if m else 0), (int(f.group(1)) if f else 0), (int(sk.group(1)) if sk else 0)


def main():
    no_browser = "--no-browser" in sys.argv
    boards = sorted(_real.real_paths().values())
    before = {b: sha(b) for b in boards}
    for f in sorted(glob.glob(os.path.join(SRC, "tests", "test_*.py")) + glob.glob(os.path.join(SRC, "tests", "browser", "test_*.py"))):
        if os.path.basename(f) == "test_server_v2.py":
            continue  # script-style suite, run below
        if no_browser and os.sep + "browser" + os.sep in f:
            continue
        r, dt = run([PY, "-m", "pytest", f, "-q", "-p", "no:cacheprovider"])
        p, fl, sk = pytest_counts(r.stdout)
        row(os.path.relpath(f, SRC).replace("\\", "/"), r.returncode == 0, f"{p} passed, {fl} failed, {sk} skipped ({dt:.0f}s)")
        if r.returncode != 0:  # F4: show why (a bare "1 failed" hid a start-up flake)
            for ln in (r.stdout + r.stderr).strip().splitlines()[-14:]:
                print("    | " + ln[:200], flush=True)
    for f in ("tests/e2e_tickets.py",):
        r, dt = run([PY, f])
        tail = (r.stdout.strip().splitlines() or [""])[-1][:80]
        row(f, r.returncode == 0, f"exit {r.returncode} ({dt:.0f}s) {tail}")
    bad = []
    for b in boards:
        r, _ = run([PY, "validate.py", b], env=_real.real_env())
        if r.returncode:
            bad.append(os.path.basename(b))
    mods = sorted(glob.glob(os.path.join(SRC, "modules", "*.module.json")))
    for m in mods:
        r, _ = run([PY, "validate.py", m, "--module"])
        if r.returncode:
            bad.append(os.path.basename(m))
    row("validate boards + modules", not bad, f"{len(boards)} boards, {len(mods)} modules" + (f"; INVALID: {bad}" if bad else ""))
    if not no_browser and NODE and os.path.isdir(PW):
        tmp = tempfile.mkdtemp(prefix="wb-all-")
        wb = os.path.join(tmp, "wb")
        shutil.copytree(SRC, wb, ignore=shutil.ignore_patterns("__pycache__", "runs", "tickets", "proposals", "*.png", "node_modules"))
        _real.add_instances(os.path.join(wb, "boards"))
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        srv = subprocess.Popen([PY, os.path.join(wb, "serve.py")], cwd=wb, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               env=dict(os.environ, BOARD_PORT=str(port), WB_TICKETS_DIR=os.path.join(tmp, "tickets")))
        try:
            for _ in range(75):
                try:
                    urllib.request.urlopen(base + "/boards", timeout=1).read()
                    break
                except Exception:
                    time.sleep(0.2)
            for js in sorted(glob.glob(os.path.join(SRC, "tests", "browser", "*.js"))):
                name = os.path.basename(js)
                if name.startswith("_"):
                    continue  # helper module, not a suite
                if name == "shared.js":
                    row("browser/shared.js", True, "driven by tests/test_shared_e2e.py (counted there)")
                    continue
                if name == "layout.js":  # earlier suites drag cards and autosave into this temp copy; the layout suite judges the STORED layouts, so start from the real files
                    for bf in glob.glob(os.path.join(SRC, "boards", "*.json")):
                        shutil.copy(bf, os.path.join(wb, "boards"))
                    _real.add_instances(os.path.join(wb, "boards"))
                args = [NODE, js] + (["shot.png"] if name == "edges.js" else [])
                env = dict(os.environ, WB_BASE=base, PW=PW)
                if name == "v21.js":
                    env.pop("WB_BASE")  # standalone: builds its own server + zz-skill fixture
                r, dt = run(args, env=env, cwd=tmp, timeout=600)
                lines = [ln for ln in r.stdout.splitlines() if ln.startswith(("PASS", "FAIL"))]
                p = sum(ln.startswith("PASS") for ln in lines)
                row("browser/" + name, r.returncode == 0 and p == len(lines) and "SCRIPT ERROR" not in r.stdout, f"{p}/{len(lines)} checks ({dt:.0f}s)")
        finally:
            srv.terminate()
            shutil.rmtree(tmp, ignore_errors=True)
    elif not no_browser:
        row("browser/*.js", False, "node or playwright-core missing (env)")
    row("real boards unchanged", {b: sha(b) for b in boards} == before, f"{len(boards)} files sha256-compared")
    failed = [n for n, ok, _ in rows if not ok]
    print(f"\n{len(rows) - len(failed)}/{len(rows)} suites passed" + (f"; FAILED: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
