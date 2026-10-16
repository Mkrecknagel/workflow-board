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


def test_ticket_endpoints():
    tmp = tempfile.mkdtemp(prefix="wbsrv-")
    wb = os.path.join(tmp, "wb")
    shutil.copytree(SRC, wb, ignore=shutil.ignore_patterns("__pycache__", "runs", "tickets", "*.png"))
    _real.add_instances(os.path.join(wb, "boards"))
    tdir = os.path.join(tmp, "tickets")
    env = dict(os.environ, BOARD_PORT=str(PORT), WB_TICKETS_DIR=tdir)
    proc = subprocess.Popen([sys.executable, os.path.join(wb, "serve.py")], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(50):
            try:
                req("GET", "/boards"); break
            except Exception:
                time.sleep(0.1)
        boards = os.path.join(wb, "boards")
        before = {f: sha(os.path.join(boards, f)) for f in os.listdir(boards) if f.endswith(".json")}
        st, t = req("POST", "/tickets/demo-board", {"card": "st-intake", "text": "tweak label", "author": "agent"})
        assert st == 200 and t["status"] == "draft" and t["author"] == "owner"  # client author ignored
        st, g = req("GET", "/tickets/demo-board")
        assert st == 200 and [x["status"] for x in g["tickets"]] == ["draft"]
        st, _ = req("POST", f"/tickets/demo-board/{t['id']}/send", {}, {"Tailscale-User-Login": "stranger@x"})
        assert st == 403
        assert req("GET", "/tickets/pending")[1]["count"] == 0
        st, s = req("POST", f"/tickets/demo-board/{t['id']}/send", {})
        assert st == 200 and s["status"] == "submitted"
        idx = json.load(open(os.path.join(tdir, "queue", "_index.json")))
        assert t["id"] in idx
        assert req("GET", "/tickets/pending")[1]["count"] == 1
        # guest ticket via header cannot be sent
        st, gt = req("POST", "/tickets/demo-board", {"card": "st-intake", "text": "hi"}, {"Tailscale-User-Login": "stranger@x"})
        assert st == 200 and gt["author"] == "guest"
        assert req("POST", f"/tickets/demo-board/{gt['id']}/send", {})[0] == 403
        # an action addressed to the wrong board is a 404 and changes nothing
        st, wt = req("POST", "/tickets/demo-board", {"card": "st-intake", "text": "wrong-board probe"})
        assert st == 200
        for op in ("send", "archive", "recall", "unarchive", "reply", "status"):
            assert req("POST", f"/tickets/other-board/{wt['id']}/{op}", {"text": "x", "to": "done"})[0] == 404, op
        assert [x["status"] for x in req("GET", "/tickets/demo-board")[1]["tickets"] if x["id"] == wt["id"]] == ["draft"]
        assert req("GET", "/tickets/other-board")[1]["tickets"] == []
        # board PUT leaves other boards byte-identical; the PUT'd board itself is validated and stored as sent
        body = open(os.path.join(boards, "demo-board.json"), "rb").read()
        r = urllib.request.Request(BASE + "/board/demo-board", data=body, method="PUT")
        assert urllib.request.urlopen(r).status == 200
        after = {f: sha(os.path.join(boards, f)) for f in before}
        assert after == before
        assert req("GET", "/tickets/nonexistent!")[0] in (404, 400)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_ticket_endpoints()
    print("PASS")
