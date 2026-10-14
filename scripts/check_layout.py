#!/usr/bin/env python3
"""Readability check CLI for workflow boards.

  python scripts/check_layout.py --all                     check every registered board (engine boards/ + registry-registered instance boards)
  python scripts/check_layout.py --board NAME [--board X]  check named boards
  python scripts/check_layout.py --all --fix               auto-arrange (ELK) + save through PATCH /visual, then re-check from a fresh page load
  options: --base URL (use a running server; default: a private read-only server on a free port from THIS checkout)
           --out DIR (screenshots, default runs/readability)   --json (machine output)

Pass = 0 box overlaps and 0 lines through boxes (card boxes, lane frames, lane header bands, boundary label strips), judged on what the real canvas
draws, after a fresh page load (so it holds after a reload). Exit 0 = every board passes, 1 = at least one fails, 2 = tooling error.
Screenshots are written per board and are never read by an agent: attach them for a human, trust the geometry."""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start_server():
    port = free_port()
    env = dict(os.environ, BOARD_PORT=str(port), WB_CHECK_ON_SAVE="0")
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "serve.py")], cwd=HERE, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(75):
        try:
            urllib.request.urlopen(base + "/boards", timeout=1).read()
            return proc, base
        except Exception:
            time.sleep(0.2)
    proc.terminate()
    raise SystemExit("check_layout: private server did not start")


def node(script, args):
    return subprocess.run([NODE, os.path.join(HERE, "scripts", script)] + args, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=HERE, timeout=900)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", action="append", default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--base")
    ap.add_argument("--out", default=os.path.join(HERE, "runs", "readability"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if not NODE:
        print("check_layout: node not found", file=sys.stderr)
        return 2
    if not (a.all or a.board):
        ap.error("give --all or --board NAME")
    proc = None
    base = a.base
    if not base:
        proc, base = start_server()
    sel = ["--all"] if a.all else sum([["--board", b] for b in a.board], [])
    try:
        if a.fix:
            if not a.base:
                print("check_layout: --fix writes board files; pass --base of the server that owns them (e.g. http://127.0.0.1:8765)", file=sys.stderr)
                return 2
            r = node("relayout.js", ["--base", base, "--save"] + sel)
            sys.stderr.write(r.stdout[-1500:] if r.returncode else "")
        r = node("check_board.js", ["--base", base, "--out", a.out, "--no-fail"] + sel)
        try:
            doc = json.loads(r.stdout)
        except ValueError:
            print("check_layout: checker failed\n" + (r.stdout + r.stderr)[-800:], file=sys.stderr)
            return 2
    finally:
        if proc:
            proc.terminate()
    if a.json:
        print(json.dumps(doc, indent=1))
    else:
        print(f"{'board':30} {'cards':>5} {'overlap':>7} {'line-thru':>9} {'loops':>5}  routes")
        for b in doc["boards"]:
            print(f"{b['board']:30} {b.get('cards', '-'):>5} {len(b['overlaps']):>7} {len(b['crossings']):>9} {b.get('loops', '-'):>5}  {b.get('routes', b.get('error', ''))}")
            for o in b["overlaps"][:3]:
                print("    overlap:", o)
            for c in b["crossings"][:3]:
                print("    through:", c)
        print("PASS" if doc["pass"] else "FAIL")
    return 0 if doc["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
