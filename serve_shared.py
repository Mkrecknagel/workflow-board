#!/usr/bin/env python3
"""/api/shared/* routes for workflow-board. Installed by ONE `import serve_shared` line in serve.py, which
wraps the request handler class H (do_GET / do_POST / do_PUT) instead of editing its bodies.

GET  /api/shared/library?board=B        -> {shared_library: [...]}
GET  /api/shared/drift?board=B          -> drift report (also written to dedup/drift-report.json)
POST /api/shared/replace      {board, members:[id|{card_id,scope,match_text}], canonical?, label?, body?, fields?}
POST /api/shared/edit-preview {board, step_id, changes:{label?,body?,fields?}}
POST /api/shared/edit         {board, step_id, changes, confirm:true, expected_version?, fork_cards?}
POST /api/shared/unlink       {board, card_id}
POST /api/shared/delete       {board, step_id}        (409 while refcount > 0)
POST /api/shared/propose      {board, step_id?, date?}  (writes proposals/<date>/<sop>.diff + proposal.json; SOPs untouched)

Mutating routes are owner-only (author computed server-side, as for tickets). Every PUT /board/<name> also mirrors
shared library payloads into instance cards and regenerates the drift report.
"""
import io
import json
import os
import re
import sys
import threading
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shared_steps as SS  # noqa: E402
import dedup as D  # noqa: E402
import tickets_store as TS  # noqa: E402
import registry as REG  # noqa: E402

CODE = {"invalid": 422, "notfound": 404, "conflict": 409, "refused": 403}
BNAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
LOCK = threading.Lock()
MAX = 1_000_000


def _host():
    for n in ("__main__", "serve"):
        m = sys.modules.get(n)
        if m is not None and hasattr(m, "H") and hasattr(m, "BOARDS"):
            return m
    raise RuntimeError("serve module not found")


def _handle(h, method, u, host):
    op = u.path[len("/api/shared/"):].strip("/")
    q = parse_qs(u.query)
    body = {}
    if method == "POST":
        n = int(h.headers.get("Content-Length") or 0)
        if n < 0 or n > MAX:
            return h._err(413, "bad body size")
        if n:
            try:
                body = json.loads(h.rfile.read(n).decode("utf-8"))
            except ValueError:
                return h._err(422, "unparseable JSON")
            if not isinstance(body, dict):
                return h._err(422, "body must be an object")
    board = body.get("board") or (q.get("board") or [""])[0]
    if not BNAME.match(board or "") or board == "active":
        return h._err(422, "board name required")
    reads = {"library", "drift"}
    if (method == "GET") != (op in reads) or op not in reads | {"replace", "edit-preview", "edit", "unlink", "delete", "propose"}:
        return h._err(404, "not found")
    if op not in reads and op != "edit-preview" and TS.compute_author(h.client_address[0], dict(h.headers.items())) != "owner":
        return h._err(403, "owner only")
    bdir = host.BOARDS
    try:
        with LOCK:
            doc = SS.load(board, bdir)
            if op == "library":
                return h._send(200, json.dumps({"shared_library": doc.get("shared_library", [])}))
            if op == "drift":
                return h._send(200, json.dumps(SS.drift(doc, board)))
            if op == "edit-preview":
                return h._send(200, json.dumps(SS.preview(doc, body.get("step_id"), body.get("changes"))))
            if op == "propose":
                return h._send(200, json.dumps(SS.propose(doc, board, step_id=body.get("step_id"), date=body.get("date"))))
            if op == "replace":
                res = SS.replace(doc, body.get("members"), label=body.get("label"), body=body.get("body"),
                                 fields=body.get("fields"), canonical=body.get("canonical"), sop_dir=D.sop_dir())
            elif op == "edit":
                res = SS.edit(doc, body.get("step_id"), body.get("changes"), confirm=body.get("confirm"),
                              expected_version=body.get("expected_version"), fork_cards=body.get("fork_cards"))
            elif op == "unlink":
                res = SS.unlink(doc, body.get("card_id"))
            else:
                res = SS.delete_entry(doc, body.get("step_id"))
            SS.save(doc, board, bdir)
            _refresh_drift(doc, board)
            return h._send(200, json.dumps(res))
    except SS.SharedError as e:
        return h._err(CODE.get(e.code, 400), str(e))


def _refresh_drift(doc, board):
    if doc.get("shared_library"):
        try:
            SS.drift(doc, board)
        except Exception:  # noqa: BLE001  best-effort: a missing SOP dir must never fail a board save
            pass


def install(host):
    H = host.H
    if getattr(H, "_shared_installed", False):
        return
    orig_get, orig_post, orig_put = H.do_GET, H.do_POST, H.do_PUT

    def do_GET(self):
        u = urlparse(self.path)
        if u.path.startswith("/api/shared/"):
            return _handle(self, "GET", u, host)
        return orig_get(self)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path.startswith("/api/shared/"):
            return _handle(self, "POST", u, host)
        return orig_post(self)

    def do_PUT(self):
        m = host.NAME.match(self.path.split("?")[0])
        if not m or m.group(1) == "active":
            return orig_put(self)
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX:
            return orig_put(self)
        raw = self.rfile.read(n)
        try:  # mirror library payloads into instance cards before the normal validate+write path
            doc = json.loads(raw.decode("utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("cards"), list) and (
                    doc.get("shared_library") or any(isinstance(c, dict) and c.get("shared") for c in doc["cards"])):
                SS.recount(doc)
                SS.mirror(doc)
                raw = json.dumps(doc, indent=2, ensure_ascii=False).encode("utf-8")
        except (ValueError, KeyError, TypeError):
            pass
        self.rfile = io.BytesIO(raw)
        del self.headers["Content-Length"]
        self.headers["Content-Length"] = str(len(raw))
        orig_put(self)
        try:
            with open(REG.resolve(m.group(1), host.BOARDS), encoding="utf-8") as f:
                saved = json.load(f)
            _refresh_drift(saved, m.group(1))
        except (OSError, ValueError):
            pass

    H.do_GET, H.do_POST, H.do_PUT = do_GET, do_POST, do_PUT
    H._shared_installed = True


install(_host())
