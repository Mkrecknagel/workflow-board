"""B2 tests: ticket endpoints on a temp copy of workflow-board, loopback temp port, temp WB_TICKETS_DIR."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _real  # noqa: E402

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = int(os.environ.get("BOARD_TEST_PORT", "8794"))
BASE = f"http://127.0.0.1:{PORT}"


def req(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()




if __name__ == "__main__":
    test_ticket_endpoints()
    print("PASS")
