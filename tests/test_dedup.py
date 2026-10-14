#!/usr/bin/env python3
"""dedup.py tests (stdlib + pytest-compatible). Everything runs on TEMP COPIES: the real boards/, tickets/,
dedup/ and ~/.claude/sops are never written. Run: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_dedup.py"""
import glob
import json
import os
import shutil
import sys
import tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)
sys.path.insert(0, os.path.join(SRC, "tests"))
import _real  # noqa: E402
import dedup  # noqa: E402
import tickets_store as TS  # noqa: E402

REAL_SOPS = os.path.expanduser("~/.claude/sops")


def env():
    tmp = tempfile.mkdtemp(prefix="wb-dedup-")
    bd = os.path.join(tmp, "boards")
    os.makedirs(bd)
    shutil.copy(_real.real_path("demo-board"), bd)
    sd = os.path.join(tmp, "sops")
    os.makedirs(sd)
    for f in glob.glob(os.path.join(REAL_SOPS, "*", "[0-9][0-9]-*.sop.md")):
        shutil.copy(f, sd)
    os.environ["WB_TICKETS_DIR"] = os.path.join(tmp, "tickets")
    return tmp, bd, sd, os.path.join(tmp, "out")


def read(p):
    with open(p, "rb") as f:
        return f.read()














if __name__ == "__main__":
    fails = 0
    for n, fn in sorted(globals().items()):
        if n.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", n)
            except Exception as e:  # noqa: BLE001
                fails += 1
                print("FAIL", n, repr(e)[:200])
    sys.exit(1 if fails else 0)
