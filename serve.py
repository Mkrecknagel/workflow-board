#!/usr/bin/env python3
"""workflow-board local server. Loopback only. GET/PUT /board/<name>, GET / -> canvas/index.html."""
import json
import os
import re
import sys
import tempfile
import time
import hashlib
import mimetypes
import threading
import datetime
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import validate as V  # noqa: E402
import library as L  # noqa: E402
import registry as REG  # noqa: E402
import tickets_store as TS  # noqa: E402

HERE = V.HERE
BOARDS = os.path.join(HERE, "boards")
MODULES = os.path.join(HERE, "modules")
SPLITS = os.path.join(HERE, "splits")
CANVAS = os.path.join(HERE, "canvas")
NAME = re.compile(r"^/board/([a-z0-9][a-z0-9-]*)$")
VNAME = re.compile(r"^/board/([a-z0-9][a-z0-9-]*)/visual$")
VISUAL_KEYS = ("pos", "size", "style", "pin")
HIST_COALESCE_S = 300
_VLOCK = threading.Lock()
RNAME = re.compile(r"^/board/([a-z0-9][a-z0-9-]*)/readability$")
MNAME = re.compile(r"^/module/([a-z0-9][a-z0-9-]*)$")
MAX = 1_000_000
TNAME = re.compile(r"^/tickets/([A-Za-z0-9][A-Za-z0-9._-]*)(?:/(archive)|/([A-Za-z0-9._-]+)/(send|reply|status|archive|unarchive|recall))?$")
TCODE = {"refused": 403, "notfound": 404, "conflict": 409, "invalid": 422}
KEEP = 20
ROOTS = L.default_roots()
MIME = {".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".html": "text/html; charset=utf-8", ".json": "application/json", ".png": "image/png", ".svg": "image/svg+xml", ".md": "text/plain; charset=utf-8"}


def parse_roots(spec):
    """--roots 'DIR' or 'claude=DIR,codex=DIR,kiro=DIR,agents=DIR' (os.pathsep or comma separated). Replaces the default install roots."""
    r = {"claude": None, "codex": None, "kiro": None, "agents": None, "_mcp_json": None, "_tasks": False}
    for part in re.split(r"[,;]" if os.pathsep == ";" else r"[,:]", spec):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(claude|codex|kiro|agents)=(.+)$", part)
        agent, d = (m.group(1), m.group(2)) if m else ("claude", part)
        r[agent] = d
    if r["claude"]:
        r["_mcp_json"] = os.path.join(r["claude"], ".claude.json")
    return r


def _recent_snapshot(histdir, base, within_s):
    """True if a history snapshot of `base` was written less than within_s seconds ago."""
    if not os.path.isdir(histdir):
        return False
    now = time.time()
    for fn in os.listdir(histdir):
        if fn.startswith(base + ".") and fn.endswith(".json"):
            try:
                if now - os.path.getmtime(os.path.join(histdir, fn)) < within_s:
                    return True
            except OSError:
                pass
    return False


def backup_and_write(path, raw, histdir, base, coalesce_s=0):
    """Copy current file to <histdir>/<base>.<ts>.<sha8>.json (prune to KEEP), then atomic write of raw.
    coalesce_s > 0: skip the snapshot when one was written within that many seconds (visual autosave)."""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    if os.path.isfile(path) and not (coalesce_s and _recent_snapshot(histdir, base, coalesce_s)):
        os.makedirs(histdir, exist_ok=True)
        with open(path, "rb") as fh:
            cur = fh.read()
        ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + f"{int((time.time() % 1) * 1e6):06d}Z"
        shutil_name = f"{base}.{ts}.{hashlib.sha256(cur).hexdigest()[:8]}.json"
        with open(os.path.join(histdir, shutil_name), "wb") as fh:
            fh.write(cur)
        olds = sorted(fn for fn in os.listdir(histdir) if fn.startswith(base + ".") and fn.endswith(".json"))
        for fn in olds[:-KEEP]:
            try:
                os.remove(os.path.join(histdir, fn))
            except OSError:
                pass
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def telemetry():
    """card id -> latest ISO ts seen in runs/*.jsonl (read-only; executor is the sole writer)."""
    out = {}
    d = os.path.join(HERE, "runs")
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".jsonl"):
            continue
        try:
            with open(os.path.join(d, fn), encoding="utf-8") as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    cid = r.get("card") or r.get("card_id") or r.get("id")
                    ts = r.get("ts") or r.get("time")
                    if isinstance(cid, str) and isinstance(ts, str) and ts > out.get(cid, ""):
                        out[cid] = ts
        except OSError:
            pass
    return out


def apply_visual(f, name, body):
    """Apply a visual-only overlay to board file f. Returns (200, {ok,board,hash,saved_at}) or (409, msg); raises V.Invalid (-> 422).
    Writes nothing unless the semantic hash is unchanged and validate_doc passes."""
    import serve_v4_runs as R
    with open(f, encoding="utf-8") as fh:
        old = json.load(fh)
    new = json.loads(json.dumps(old))
    cards = new.get("cards", [])
    byid = {c.get("id"): c for c in cards if isinstance(c, dict)}
    ov = body.get("cards", {})
    if not isinstance(ov, dict):
        raise V.Invalid("cards must be an object of id -> overlay")
    for cid, o in ov.items():
        if cid not in byid:
            raise V.Invalid(f"unknown card id '{cid}'")
        if not isinstance(o, dict) or set(o) - set(VISUAL_KEYS):
            raise V.Invalid(f"card '{cid}': overlay may only carry {', '.join(VISUAL_KEYS)}")
        for k, v in o.items():
            if v is None:
                byid[cid].pop(k, None)
            else:
                byid[cid][k] = v
    removed = body.get("notes_removed", [])
    notes = body.get("notes", [])
    if not isinstance(removed, list) or not isinstance(notes, list):
        raise V.Invalid("notes and notes_removed must be arrays")
    for rid in removed:
        c = byid.get(rid)
        if c is not None and c.get("type") != "note":
            raise V.Invalid(f"notes_removed: '{rid}' is not a note card")
    gone = {rid for rid in removed if rid in byid}
    if gone:
        cards[:] = [c for c in cards if c.get("id") not in gone]
        new["edges"] = [e for e in new.get("edges", []) if not (isinstance(e, dict) and (e["from"].rsplit(".", 1)[0] in gone or e["to"].rsplit(".", 1)[0] in gone))]
    for nc in notes:
        if not isinstance(nc, dict) or nc.get("type") != "note" or not isinstance(nc.get("id"), str):
            raise V.Invalid("notes entries must be note cards with a string id")
        ex = next((c for c in cards if c.get("id") == nc["id"]), None)
        if ex is None:
            cards.append(nc)
        elif ex.get("type") != "note":
            raise V.Invalid(f"notes: '{nc['id']}' collides with a non-note card")
        else:
            cards[cards.index(ex)] = nc
    if "group_styles" in body:
        gs = body["group_styles"]
        if gs is None:
            new.pop("group_styles", None)
        elif isinstance(gs, dict):
            new["group_styles"] = gs
        else:
            raise V.Invalid("group_styles must be an object")
    if R.semantic_hash(new) != R.semantic_hash(old):
        return 409, "overlay would change board semantics; use Save (PUT) for behaviour edits"
    V.validate_doc(new, name)
    saved_at = datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")
    new["saved_at"] = saved_at
    raw = json.dumps(new, indent=2, ensure_ascii=False).encode("utf-8")
    backup_and_write(f, raw, os.path.join(BOARDS, ".history"), name, coalesce_s=HIST_COALESCE_S)
    return 200, {"ok": True, "board": name, "hash": R.semantic_hash(new), "saved_at": saved_at, "prev_saved_at": old.get("saved_at")}


# ---- readability check on save (v6) --------------------------------------------------------------------------------
# After a board save (PUT or PATCH /visual) a check job is debounced (2 s, so autosave bursts collapse), run single-flight in a
# child `node scripts/check_board.js` (headless Chromium against THIS server), skipped when the layout fingerprint is unchanged.
# Result: runs/readability/<board>.latest.json, served by GET /board/<name>/readability. A failing board is flagged, never rejected
# (a half-dragged autosave must still save). Off with WB_CHECK_ON_SAVE=0, or when node / playwright-core are missing.
READ_DIR = os.path.join(HERE, "runs", "readability")
CHECK_SCRIPT = os.path.join(HERE, "scripts", "check_board.js")
PW_DIR = os.environ.get("PW") or "playwright-core"
CHECK_DEBOUNCE_S = float(os.environ.get("WB_CHECK_DEBOUNCE_S", "2"))
_CHECK_LOCK = threading.Lock()
_CHECK_TIMERS = {}
_CHECK_STATE = {}
PORT = [8765]


def check_enabled():
    import shutil
    return os.environ.get("WB_CHECK_ON_SAVE", "1") != "0" and bool(shutil.which("node")) and os.path.isdir(PW_DIR) and os.path.isfile(CHECK_SCRIPT)


def layout_fingerprint(f):
    try:
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    cs = [[c.get("id"), c.get("pos"), c.get("size"), c.get("group"), c.get("boundary")] for c in d.get("cards", []) if isinstance(c, dict)]
    return hashlib.sha256(json.dumps([cs, d.get("edges"), d.get("group_styles")], sort_keys=True).encode("utf-8")).hexdigest()


def _latest_path(name):
    return os.path.join(READ_DIR, name + ".latest.json")


def _run_check(name):
    import subprocess
    f = REG.resolve(name, BOARDS)
    with _CHECK_LOCK:
        fp = layout_fingerprint(f)
        try:
            with open(_latest_path(name), encoding="utf-8") as fh:
                last = json.load(fh)
            if last.get("layout_hash") == fp and last.get("state") == "done":
                _CHECK_STATE[name] = "done"
                return last
        except (OSError, ValueError):
            pass
        _CHECK_STATE[name] = "running"
        out = {"state": "error", "board": name, "layout_hash": fp, "checked_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
        try:
            r = subprocess.run(["node", CHECK_SCRIPT, "--base", f"http://127.0.0.1:{PORT[0]}", "--board", name, "--out", READ_DIR, "--no-fail"],
                               capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace", env=dict(os.environ, PW=PW_DIR))
            res = json.loads(r.stdout)["boards"][0]
            out.update(state="done", pass_=bool(res.get("pass")), overlaps=res.get("overlaps", []), crossings=res.get("crossings", []), loops=res.get("loops"), routes=res.get("routes"), shot=res.get("shot"))
            out["pass"] = out.pop("pass_")
            if res.get("error"):
                out.update(state="error", error=res["error"])
        except Exception as e:  # noqa: BLE001 - a failed check must never take the server down
            out["error"] = str(e)[:200]
        os.makedirs(READ_DIR, exist_ok=True)
        with open(_latest_path(name), "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1)
        _CHECK_STATE[name] = out["state"]
        return out


RELAYOUT_SCRIPT = os.path.join(HERE, "scripts", "relayout.js")


def _write_latest(name, out):
    os.makedirs(READ_DIR, exist_ok=True)
    with open(_latest_path(name), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)


def _run_check_auto(name):
    """API saves (agent builds): when the check fails, run the existing auto-layout (scripts/relayout.js, which stores ONLY layout keys through
    the layout-only PATCH, so the semantic hash cannot change), then re-check. The verdict records what happened and, if still failing, why."""
    import subprocess
    first = _run_check(name)
    if not first or first.get("state") != "done" or first.get("pass"):
        return
    _CHECK_STATE[name] = "running"
    note = {"auto_arranged": True, "before": {"overlaps": len(first.get("overlaps", [])), "crossings": len(first.get("crossings", []))}}
    try:
        r = subprocess.run(["node", RELAYOUT_SCRIPT, "--base", f"http://127.0.0.1:{PORT[0]}", "--board", name, "--save"],
                           capture_output=True, text=True, timeout=240, encoding="utf-8", errors="replace", env=dict(os.environ, PW=PW_DIR))
        note["relayout_exit"] = r.returncode
        if r.returncode:
            note["reason"] = ("auto-layout did not reach a clean layout: " + (r.stdout or r.stderr or "")[-200:]).strip()
    except Exception as e:  # noqa: BLE001
        note["reason"] = "auto-layout failed to run: " + str(e)[:160]
    final = _run_check(name)
    if final is not None:
        final.update(note)
        if not final.get("pass") and "reason" not in final:
            final["reason"] = "still failing after auto-layout (%d overlaps, %d lines through boxes)" % (len(final.get("overlaps", [])), len(final.get("crossings", [])))
        with _CHECK_LOCK:
            _write_latest(name, final)
            _CHECK_STATE[name] = final["state"]


def schedule_check(name, auto=False):
    """Debounced, single-flight. Returns True when a job is (re)scheduled. auto=True (API PUT) also rearranges a failing board."""
    if not check_enabled():
        return False
    t = _CHECK_TIMERS.pop(name, None)
    if t:
        t.cancel()
    t = threading.Timer(CHECK_DEBOUNCE_S, _run_check_auto if auto else _run_check, args=(name,))
    t.daemon = True
    _CHECK_TIMERS[name] = t
    _CHECK_STATE[name] = "pending"
    t.start()
    return True


def readability_of(name):
    try:
        with open(_latest_path(name), encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        d = {"state": "none", "board": name}
    if _CHECK_STATE.get(name) in ("pending", "running"):
        d = dict(d, state=_CHECK_STATE[name], stale=True)
    return d


LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def _allowed_hosts():
    """Extra Host names accepted besides loopback: WB_ALLOWED_HOSTS, comma-separated (e.g. a reverse proxy's name such as
    wb.example.ts.net). Matched on the host name only, so any port is accepted; read per request so it can be changed live."""
    out = set()
    for h in (os.environ.get("WB_ALLOWED_HOSTS") or "").split(","):
        h = h.strip().lower()
        if h:
            out.add(h.rpartition(":")[0] if h.count(":") == 1 and not h.endswith("]") else h)
    return out


def request_is_local(headers, port):
    """Same-machine guard for every request. The server binds 127.0.0.1, but a page on another site can still send
    requests to it from your browser, and DNS rebinding can make such a page same-origin. So:
      - Host must name this loopback listener (127.0.0.1 / localhost / [::1] on this port): defeats DNS rebinding.
      - For writes, Origin, when the browser sends it, must be that same listener: defeats cross-site form/fetch writes.
    Non-browser clients (curl, the CLI tools, tests) send no Origin and a loopback Host, so they pass."""
    host = (headers.get("Host") or "").strip().lower()
    name, _, hp = host.rpartition(":") if not host.endswith("]") else (host, "", "")
    if not name:
        name, hp = host, ""
    extra = _allowed_hosts()
    if name in extra:
        pass  # an operator-listed proxy name: any port (a proxy fronts this listener on its own port)
    elif name not in LOCAL_HOSTS or (hp and hp != str(port)):
        return False, f"Host {host!r} is not this local server"
    origin = (headers.get("Origin") or "").strip().lower()
    if origin and origin != "null":
        from urllib.parse import urlsplit
        u = urlsplit(origin)
        if u.hostname in extra:
            pass
        elif (u.hostname not in ("127.0.0.1", "localhost", "::1")) or (u.port or 80) != port:
            return False, f"cross-origin request from {origin!r} refused"
    elif origin == "null":
        return False, "opaque-origin request refused"
    return True, ""


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def _err(self, code, msg):
        self._send(code, json.dumps({"error": msg}))

    def _path(self):
        m = NAME.match(self.path.split("?")[0])
        if not m or m.group(1) == "active":
            return None, None
        return m.group(1), REG.resolve(m.group(1), BOARDS)

    def _static(self, p):
        rel = p[len("/canvas/"):]
        f = os.path.normpath(os.path.join(CANVAS, rel))
        if not f.startswith(os.path.normpath(CANVAS) + os.sep) or not os.path.isfile(f):
            return self._err(404, "not found")
        ext = os.path.splitext(f)[1].lower()
        with open(f, "rb") as fh:
            return self._send(200, fh.read(), MIME.get(ext) or mimetypes.guess_type(f)[0] or "application/octet-stream")

    def do_GET(self):
        ok, why = request_is_local(self.headers, self.server.server_address[1])
        if not ok:
            return self._err(403, why)
        u = urlparse(self.path)
        p = u.path
        km = re.match(r"^/kb/([a-z0-9][a-z0-9-]*)/(_index|who|what|why|when|where|how|impact)$", p)
        if km:  # a project's KB page: read-only plain text, inside the registry's projects folder only
            from importers import tpm_portfolio as TP
            return self._text_file(os.path.join(TP.kb_dir(TP.project_dir(REG.portfolio_root(), km.group(1))), km.group(2) + ".md"))
        if p == "/report":  # a generated report: read-only plain text, inside <root>/reports only
            from urllib.parse import parse_qs
            rel = (parse_qs(u.query).get("p") or [""])[0]
            base = os.path.realpath(os.path.join(REG.portfolio_root(), "reports"))
            real = os.path.realpath(os.path.join(base, rel))
            if not rel.endswith(".md") or not real.startswith(base + os.sep):
                return self._err(404, "not found")
            return self._text_file(real)
        if p.startswith("/canvas/"):
            return self._static(p)
        rm = RNAME.match(p)
        if rm:
            if not os.path.isfile(REG.resolve(rm.group(1), BOARDS)):
                return self._err(404, f"no board '{rm.group(1)}'")
            return self._send(200, json.dumps(readability_of(rm.group(1))))
        if p == "/library":
            return self._send(200, json.dumps({"items": L.scan(ROOTS), "scanned_at": L._now()}))
        if p == "/inspect":
            q = parse_qs(u.query).get("path", [""])[0]
            allowed = {L._norm(i["path"]): i["path"] for i in L.scan(ROOTS) if i.get("path")}
            key = L._norm(q) if q else ""
            if key not in allowed or not os.path.isfile(allowed[key]):
                return self._err(403, "path not in library scan")
            return self._send(200, json.dumps(L.inspect(allowed[key])))
        if p == "/splits":
            names = sorted(fn[: -len(".proposal.json")] for fn in os.listdir(SPLITS) if fn.endswith(".proposal.json")) if os.path.isdir(SPLITS) else []
            return self._send(200, json.dumps({"splits": names}))
        m = MNAME.match(p)
        if m:
            f = os.path.join(MODULES, m.group(1) + ".module.json")
            if not os.path.isfile(f):
                return self._err(404, f"no module '{m.group(1)}'")
            with open(f, "rb") as fh:
                return self._send(200, fh.read())
        if p in ("/", "/index.html"):
            f = os.path.join(HERE, "canvas", "index.html")
            if not os.path.isfile(f):
                return self._err(404, "canvas/index.html not built")
            with open(f, "rb") as fh:
                return self._send(200, fh.read(), "text/html; charset=utf-8")
        if p == "/boards":
            names, errs = REG.list_boards(BOARDS)
            out = {"boards": names}
            if errs:
                out["errors"] = errs
            return self._send(200, json.dumps(out))
        if p == "/telemetry":
            return self._send(200, json.dumps({"last_seen": telemetry()}))
        if p == "/tickets/pending":
            return self._tickets(lambda: TS.pending())
        tm = TNAME.match(p)
        if tm and tm.group(1) != "pending" and not tm.group(3):
            if tm.group(2):
                return self._tickets(lambda: {"board": tm.group(1), "tickets": TS.list_archive(tm.group(1))})
            return self._tickets(lambda: {"board": tm.group(1), "tickets": TS.list_tickets(tm.group(1))})
        name, f = self._path()
        if not name:
            return self._err(404, "not found")
        if not os.path.isfile(f):
            return self._err(404, f"no board '{name}'")
        with open(f, "rb") as fh:
            self._send(200, fh.read())

    def _actor(self):
        return TS.compute_author(self.client_address[0], dict(self.headers.items()))

    def _tickets(self, fn):
        try:
            return self._send(200, json.dumps(fn()))
        except TS.TicketError as e:
            return self._err(TCODE.get(e.code, 400), str(e))

    def _projedit(self, up):
        """POST /project/propose {board, card, role} -> diff of the project.json change for that card's entry.
        POST /project/apply {id, approve: true} -> writes it (backup kept), then runs WB_REFRESH_CMD if set so the boards
        show the change. The card's project.json path and key are read from the board on disk, never from the request."""
        if self._actor() != "owner":
            return self._err(403, "owner only")
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX:
            return self._err(400, "bad body size")
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            return self._err(422, "unparseable JSON")
        import projedit as PE
        try:
            if up == "/project/propose":
                bp = REG.resolve(str(body.get("board") or ""), BOARDS)
                doc = json.load(open(bp, encoding="utf-8")) if bp and os.path.isfile(bp) else None
                c = next((x for x in (doc or {}).get("cards", []) if x.get("id") == body.get("card")), None)
                f = (c or {}).get("fields") or {}
                if not f.get("pj_path") or not f.get("pj_key"):
                    return self._err(422, "this card is not a project.json entry")
                plan, diff = PE.propose(f["pj_path"], f["pj_key"], str(body.get("role") or ""), f.get("skill_name"), f.get("skill_path"))
                return self._send(200, json.dumps({"plan": plan, "diff": diff}))
            if up == "/project/apply":
                plan = PE.apply(str(body.get("id") or ""), approve=body.get("approve") is True)
                out = {"plan": plan, "refreshed": False}
                cmd = os.environ.get("WB_REFRESH_CMD")
                if cmd:
                    import shlex
                    import subprocess
                    try:
                        r = subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=180)
                        out["refreshed"], out["refresh_output"] = r.returncode in (0, 3), (r.stdout + r.stderr)[-600:]
                    except (OSError, subprocess.SubprocessError) as e:
                        out["refresh_output"] = f"refresh failed: {e}"
                return self._send(200, json.dumps(out))
        except PE.Refused as e:
            return self._err(409, str(e))
        except (OSError, ValueError) as e:
            return self._err(422, str(e))
        return self._err(404, "not found")

    def _text_file(self, path):
        try:
            data = open(path, "rb").read(2_000_000)
        except OSError:
            return self._err(404, "not found")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _deploy(self, up):
        """POST /deploy/propose {board, target, name?} -> plan + diff (stages files, writes nothing in target).
        POST /deploy/apply {id, approve: true, token} -> creates the staged NEW files in target. Owner only; same-machine only
        (request_is_local). The page never applies without the owner clicking Approve on the shown diff."""
        if self._actor() != "owner":
            return self._err(403, "owner only")
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX:
            return self._err(400, "bad body size")
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            return self._err(422, "unparseable JSON")
        import deploy as D
        try:
            if up == "/deploy/propose":
                tgt = body.get("target") or os.environ.get("WB_DEPLOY_TARGET") or ""
                if not tgt:
                    return self._err(422, "no target folder: pass target or set WB_DEPLOY_TARGET (for example ~/.agents/skills)")
                plan, diff = D.propose(str(body.get("board") or ""), tgt, body.get("name") or None)
                return self._send(200, json.dumps({"plan": plan, "diff": diff}))
            if up == "/deploy/apply":
                plan = D.apply(str(body.get("id") or ""), approve=body.get("approve") is True, token=body.get("token") if isinstance(body.get("token"), str) else None)
                return self._send(200, json.dumps({"plan": plan}))
        except SystemExit as e:  # deploy.py reports refusals as SystemExit(message)
            return self._err(409, str(e))
        except (ValueError, V.Invalid) as e:
            return self._err(422, str(e))
        return self._err(404, "not found")

    def do_POST(self):
        ok, why = request_is_local(self.headers, self.server.server_address[1])
        if not ok:
            return self._err(403, why)
        up = self.path.split("?")[0]
        if up.startswith("/deploy/"):
            return self._deploy(up)
        if up.startswith("/project/"):
            return self._projedit(up)
        tm = TNAME.match(up)
        if not tm or tm.group(2) or tm.group(1) == "pending":
            return self._err(404, "not found")
        n = int(self.headers.get("Content-Length") or 0)
        if n < 0 or n > MAX:
            return self._err(413, "bad body size")
        body = {}
        if n:
            try:
                body = json.loads(self.rfile.read(n).decode("utf-8"))
            except ValueError:
                return self._err(422, "unparseable JSON")
            if not isinstance(body, dict):
                return self._err(422, "body must be an object")
        actor = self._actor()  # server-computed; any client-supplied author is ignored
        board, tid, op = tm.group(1), tm.group(3), tm.group(4)
        if not tid:
            return self._tickets(lambda: TS.create(board, body.get("card"), body.get("text"), author=actor, kind=body.get("kind")))
        if actor != "owner":
            return self._err(403, "owner only")
        try:  # the ticket must belong to the board named in the URL
            owner_board = TS.find(tid)[0]
        except TS.TicketError as e:
            return self._err(TCODE.get(e.code, 400), str(e))
        if owner_board != board:
            return self._err(404, f"ticket {tid} is not on board '{board}'")
        ops = {
            "send": lambda: TS.send(tid, actor="owner"),
            "recall": lambda: TS.recall(tid, actor="owner"),
            "archive": lambda: TS.archive(tid, actor="owner"),
            "unarchive": lambda: TS.unarchive(tid, actor="owner"),
            "reply": lambda: TS.reply(tid, body.get("text"), actor="owner", links=body.get("links")),
            "status": lambda: TS.set_status(tid, body.get("to"), actor="owner", no_change=bool(body.get("no_change")), links=body.get("links")),
        }
        return self._tickets(ops[op])

    def do_PUT(self):
        ok, why = request_is_local(self.headers, self.server.server_address[1])
        if not ok:
            return self._err(403, why)
        up = self.path.split("?")[0]
        if up.startswith("/splits"):
            return self._err(403, "splits are written by a human or agent turn, never by the server")
        mm = MNAME.match(up)
        if mm:
            return self._put_module(mm.group(1))
        name, f = self._path()
        if not name:
            return self._err(404, "not found")
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX:
            return self._err(413 if n > MAX else 400, "bad body size")
        raw = self.rfile.read(n)
        try:
            doc = json.loads(raw.decode("utf-8"))
            V.validate_doc(doc, name)
            if os.path.isfile(f):  # immutable cards of the stored board may not be dropped or relaxed
                with open(f, encoding="utf-8") as fh:
                    old = json.load(fh)
                new = {c["id"]: c for c in doc["cards"]}
                for c in old.get("cards", []):
                    if c.get("immutable"):
                        nc = new.get(c["id"])
                        if nc is None or nc.get("immutable") is not True or not nc.get("enabled", False):
                            raise V.Invalid(f"immutable card '{c['id']}' cannot be removed, disabled or relaxed")
                        for e in old.get("edges", []):
                            ends = (e["from"].rsplit(".", 1)[0], e["to"].rsplit(".", 1)[0])
                            if c["id"] in ends and e not in doc["edges"]:
                                raise V.Invalid(f"edge {e['from']}->{e['to']} of immutable card '{c['id']}' cannot be disconnected")
        except V.Invalid as e:
            return self._err(422, str(e))
        except ValueError as e:
            return self._err(422, f"unparseable JSON: {str(e)[:100]}")
        base = (parse_qs(urlparse(self.path).query).get("base") or [None])[0]
        if base and os.path.isfile(f):  # F1/M1: optimistic concurrency; a tab whose base is older than the stored saved_at must not overwrite
            try:
                with open(f, encoding="utf-8") as fh:
                    cur = json.load(fh).get("saved_at")
            except (ValueError, OSError):
                cur = None
            if cur and cur != base:
                return self._send(409, json.dumps({"error": "base mismatch: this board was saved by another tab or session after you opened it", "code": "base_mismatch", "saved_at": cur, "base": base}))
        backup_and_write(f, raw, os.path.join(BOARDS, ".history"), name)
        out = {"ok": True, "board": name, "saved_at": doc.get("saved_at")}
        canvas = (self.headers.get("X-WB-Source") or "").lower() == "canvas"  # a human dragging in the canvas is only flagged, never rearranged
        if schedule_check(name, auto=not canvas):
            out["readability"] = "pending"
            if not canvas:
                out["auto_arrange"] = "on_failure"
        self._send(200, json.dumps(out))

    def do_PATCH(self):
        ok, why = request_is_local(self.headers, self.server.server_address[1])
        if not ok:
            return self._err(403, why)
        m = VNAME.match(self.path.split("?")[0])
        if not m or m.group(1) == "active":
            return self._err(404, "not found")
        name = m.group(1)
        f = REG.resolve(name, BOARDS)
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX:
            return self._err(413 if n > MAX else 400, "bad body size")
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            return self._err(422, "unparseable JSON")
        if not isinstance(body, dict):
            return self._err(422, "body must be an object")
        if not os.path.isfile(f):
            return self._err(404, f"no board '{name}'")
        with _VLOCK:
            try:
                code, out = apply_visual(f, name, body)
            except V.Invalid as e:
                return self._err(422, str(e))
        if code != 200:
            return self._err(code, out)
        if schedule_check(name):
            out["readability"] = "pending"
        self._send(200, json.dumps(out))

    def _put_module(self, name):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX:
            return self._err(413 if n > MAX else 400, "bad body size")
        raw = self.rfile.read(n)
        try:
            V.validate_module(json.loads(raw.decode("utf-8")), name)
        except V.Invalid as e:
            return self._err(422, str(e))
        except ValueError as e:
            return self._err(422, f"unparseable JSON: {str(e)[:100]}")
        backup_and_write(os.path.join(MODULES, name + ".module.json"), raw, os.path.join(MODULES, ".history"), name)
        self._send(200, json.dumps({"ok": True, "module": name}))

    def log_message(self, *a):
        pass


import serve_shared  # noqa: E402,F401  (installs /api/shared/* routes on H)
import serve_v4  # noqa: E402,F401  (v4 read-only routes; see serve_v4.py)


def main():
    global ROOTS
    argv = sys.argv[1:]
    if "--roots" in argv:
        ROOTS = parse_roots(argv[argv.index("--roots") + 1])
    port = int(os.environ.get("BOARD_PORT", "8765"))
    PORT[0] = port
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"workflow-board on http://127.0.0.1:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
