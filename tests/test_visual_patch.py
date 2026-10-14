"""PATCH /board/<name>/visual: visual-only overlay endpoint. Temp board copy + free loopback port; never the real boards/ or 8765."""
import glob
import json
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import serve  # noqa: E402
import serve_v4_runs as R  # noqa: E402

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
    real_before = {f: open(f, "rb").read() for f in _real.real_paths().values()}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve.H)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", b
    httpd.shutdown()
    assert {f: open(f, "rb").read() for f in real_before} == real_before  # real boards untouched


def call(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def load(b):
    return json.load(open(b / "demo-board.json", encoding="utf-8"))


def hist(b):
    d = b / ".history"
    return [f for f in os.listdir(d) if f.startswith("demo-board.")] if d.is_dir() else []


def note(i="note-1", text="hello"):
    return {"id": i, "type": "note", "label": "n", "enabled": False, "execution_mode": "observe", "immutable": False,
            "ref": {"kind": "note"}, "fields": {"text": text}, "ports": {"in": [], "out": []}, "pos": {"x": 10, "y": 10}}












