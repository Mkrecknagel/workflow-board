#!/usr/bin/env python3
"""Ticket store for workflow-board (stdlib only). The ONLY writer of tickets/.

Layout (under tickets/, override with env WB_TICKETS_DIR):
  {board}.json            live tickets  {"board":..., "tickets":[...]}
  archive/{board}.jsonl   archived tickets, one per line, append-only
  queue/{id}.json         pending pointer; queue/_index.json = list of pending ids
  locks/{id}.lock         claim lock; locks/_{board}.lock = read-modify-write lock
  plans/{id}.diff         bulk dry-runs
  config.json             {"owner_login": "..."}
"""
import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
TEXT_CAP = 8 * 1024
CLAIM_TTL = 30 * 60
KINDS = ("note", "governance", "bulk", "task")
OPEN = ("draft", "submitted", "in_progress", "blocked")
GOV_RE = re.compile(
    r"CLAUDE\.md|AGENTS\.md|agents/|skills/[^\s]*SKILL\.md|modules/[^\s]*\.yaml|hooks/|settings(\.local)?\.json|council-routing",
    re.I)


class TicketError(Exception):
    """Refused operation. .code: 'refused'|'notfound'|'conflict'|'invalid'."""

    def __init__(self, msg, code="refused", holder=None):
        super().__init__(msg)
        self.code = code
        self.holder = holder


def root():
    return os.environ.get("WB_TICKETS_DIR") or os.path.join(HERE, "tickets")


def _p(*parts):
    return os.path.join(root(), *parts)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure():
    for d in ("", "archive", "queue", "locks", "plans"):
        os.makedirs(_p(d) if d else root(), exist_ok=True)


def _atomic_write(path, text):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


class _BoardLock:
    """O_EXCL lock with 2 s retry; stale (>10 s) locks are broken."""

    def __init__(self, board):
        self.path = _p("locks", f"_{board}.lock")

    def __enter__(self):
        _ensure()
        deadline = time.time() + 2.0
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > 10:
                        os.unlink(self.path)
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise TicketError("board busy (lock timeout)", "conflict")
                time.sleep(0.02)

    def __exit__(self, *a):
        try:
            os.unlink(self.path)
        except OSError:
            pass


# ---------- raw board file helpers ----------
def _load(board):
    d = _read_json(_p(f"{board}.json"), None)
    if d is None:
        d = {"board": board, "tickets": []}
    return d


def _save(board, d):
    _atomic_write(_p(f"{board}.json"), json.dumps(d, indent=2) + "\n")


def _safe(name, what):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", name) or name.startswith("."):
        raise TicketError(f"invalid {what}", "invalid")
    return name


def boards():
    _ensure()
    out = []
    for fn in os.listdir(root()):
        if fn.endswith(".json") and fn != "config.json":
            out.append(fn[:-5])
    return sorted(out)


def _archive_path(board):
    return _p("archive", f"{board}.jsonl")


def _archived(board):
    out = []
    try:
        with open(_archive_path(board), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    except FileNotFoundError:
        pass
    return out


def _all_ids():
    ids = set()
    for b in boards():
        ids.update(t["id"] for t in _load(b)["tickets"])
    arch = _p("archive")
    if os.path.isdir(arch):
        for fn in os.listdir(arch):
            if fn.endswith(".jsonl"):
                ids.update(t["id"] for t in _archived(fn[:-6]))
    return ids


def _new_id(board_hint=None):
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"t-{day}-"
    n = max([int(i[len(prefix):]) for i in _all_ids() if i.startswith(prefix) and i[len(prefix):].isdigit()] or [0])
    return f"{prefix}{n + 1:03d}"


def find(tid, include_archive=True):
    """Return (board, ticket, archived_flag) or raise notfound."""
    _ensure()
    for b in boards():
        for t in _load(b)["tickets"]:
            if t["id"] == tid:
                return b, t, False
    if include_archive:
        arch = _p("archive")
        for fn in sorted(os.listdir(arch)):
            if fn.endswith(".jsonl"):
                for t in _archived(fn[:-6]):
                    if t["id"] == tid:
                        return fn[:-6], t, True
    raise TicketError(f"ticket not found: {tid}", "notfound")


# ---------- queue ----------
def _index():
    return _read_json(_p("queue", "_index.json"), [])


def _queue_add(t):
    ev = {"id": t["id"], "board": t["board"], "card": t["card"], "kind": t["kind"],
          "queued_at": now(), "preview": t["text"][:80]}
    _atomic_write(_p("queue", f"{t['id']}.json"), json.dumps(ev, indent=2) + "\n")
    idx = _index()
    if t["id"] not in idx:
        idx.append(t["id"])
    _atomic_write(_p("queue", "_index.json"), json.dumps(idx) + "\n")


def _queue_remove(tid):
    try:
        os.unlink(_p("queue", f"{tid}.json"))
    except OSError:
        pass
    idx = [i for i in _index() if i != tid]
    _atomic_write(_p("queue", "_index.json"), json.dumps(idx) + "\n")


def pending():
    """-> {count, items} from queue pointers (one index read + pointer reads)."""
    _ensure()
    items = []
    for i in _index():
        ev = _read_json(_p("queue", f"{i}.json"), None)
        if ev:
            items.append(ev)
    return {"count": len(items), "items": items}


# ---------- transition matrix ----------
OWNER_T = {
    ("draft", "submitted"), ("submitted", "draft"), ("blocked", "submitted"),
    ("draft", "blocked"), ("submitted", "blocked"), ("in_progress", "blocked"),
    ("draft", "done"), ("submitted", "done"), ("in_progress", "done"), ("blocked", "done"),
}
AGENT_T = {("submitted", "in_progress"), ("in_progress", "blocked"), ("in_progress", "done")}


def _hist(t, actor, frm, to, session=None):
    h = {"at": now(), "actor": actor, "from": frm, "to": to}
    if session:
        h["session"] = session
    t["history"].append(h)
    t["status"] = to
    t["updated_at"] = h["at"]


def _check_actor(actor):
    if actor not in ("owner", "agent", "guest"):
        raise TicketError("invalid actor", "invalid")


def classify(text):
    return "governance" if GOV_RE.search(text or "") else "note"


# ---------- public ops ----------
def create(board, card, text, author="owner", kind=None, session=None):
    _check_actor(author)
    _safe(board, "board")
    _safe(card, "card")
    if not isinstance(text, str) or not text.strip():
        raise TicketError("empty text", "invalid")
    if len(text.encode("utf-8")) > TEXT_CAP:
        raise TicketError("text exceeds 8 KB", "invalid")
    _ensure()
    k = kind or classify(text)
    if k not in KINDS:
        raise TicketError("invalid kind", "invalid")
    if classify(text) == "governance":
        k = "governance"
    with _BoardLock(board):
        d = _load(board)
        t = {
            "id": _new_id(), "board": board, "card": card, "author": author, "kind": k,
            "text": text, "status": "draft", "archived": False,
            "created_at": now(), "updated_at": now(), "claim": None,
            "approved_in_chat": None, "no_change": False, "links": [],
            "replies": [], "history": [],
        }
        t["history"].append({"at": t["created_at"], "actor": author, "from": None, "to": "draft"})
        d["tickets"].append(t)
        _save(board, d)
    return t


def list_tickets(board=None, status=None, include_archived=False):
    _ensure()
    out = []
    for b in ([board] if board else boards()):
        for t in _load(b)["tickets"]:
            if status and t["status"] != status:
                continue
            out.append(t)
        if include_archived:
            out.extend(_archived(b))
    return out


def list_archive(board):
    _ensure()
    return _archived(_safe(board, "board"))


def _mutate(tid, fn):
    """Locate ticket, lock its board, apply fn(ticket, doc) and save."""
    board, _, arch = find(tid)
    if arch:
        raise TicketError("ticket is archived; unarchive first", "refused")
    with _BoardLock(board):
        d = _load(board)
        for t in d["tickets"]:
            if t["id"] == tid:
                res = fn(t, d)
                _save(board, d)
                return res if res is not None else t
    raise TicketError(f"ticket not found: {tid}", "notfound")


def send(tid, actor="owner"):
    _check_actor(actor)

    def f(t, d):
        if actor != "owner" or t["author"] != "owner":
            raise TicketError("only owner tickets may be sent by the owner (guest tickets are notes)", "refused")
        if t["status"] != "draft":
            raise TicketError(f"cannot send from {t['status']}", "refused")
        _hist(t, actor, "draft", "submitted")
        _queue_add(t)
    return _mutate(tid, f)


def recall(tid, actor="owner"):
    def f(t, d):
        if actor != "owner":
            raise TicketError("only owner may recall", "refused")
        if t["status"] != "submitted" or t.get("claim"):
            raise TicketError("can only recall an unclaimed submitted ticket", "refused")
        _hist(t, actor, "submitted", "draft")
        _queue_remove(tid)
    return _mutate(tid, f)


def _claim_lock(tid):
    return _p("locks", f"{tid}.lock")


def claim(tid, session, pid=None):
    if not session:
        raise TicketError("session required", "invalid")
    _ensure()
    _safe(tid, "id")
    find(tid, include_archive=False)
    lp = _claim_lock(tid)
    try:
        fd = os.open(lp, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        holder = _read_json(lp, None)
        raise TicketError(f"already claimed by {holder}", "conflict", holder=holder)
    info = {"session": session, "pid": pid or os.getpid(), "claimed_at": now()}
    try:
        os.write(fd, json.dumps(info).encode())
        os.close(fd)

        def f(t, d):
            if t["status"] != "submitted":
                raise TicketError(f"cannot claim from {t['status']}", "refused")
            t["claim"] = info
            _hist(t, "agent", "submitted", "in_progress", session)
            _queue_remove(tid)
        return _mutate(tid, f)
    except BaseException:
        try:
            os.unlink(lp)
        except OSError:
            pass
        raise


def release(tid, session=None):
    """Return a claimed ticket to submitted (agent gives it back)."""
    def f(t, d):
        if t["status"] != "in_progress":
            raise TicketError("not in_progress", "refused")
        t["claim"] = None
        _hist(t, "agent", "in_progress", "submitted", session)
        _queue_add(t)
    r = _mutate(tid, f)
    try:
        os.unlink(_claim_lock(tid))
    except OSError:
        pass
    return r


def reply(tid, text, actor="agent", session=None, links=None):
    _check_actor(actor)
    if actor == "guest":
        raise TicketError("guests cannot reply", "refused")
    if not isinstance(text, str) or not text.strip():
        raise TicketError("empty reply", "invalid")
    if len(text.encode("utf-8")) > TEXT_CAP:
        raise TicketError("reply exceeds 8 KB", "invalid")

    def f(t, d):
        if actor == "agent" and t["status"] != "in_progress":
            raise TicketError("agent may reply only on an in_progress ticket", "refused")
        r = {"id": f"r-{len(t['replies']) + 1:03d}", "author": actor, "at": now(), "text": text,
             "links": list(links or [])}
        if session:
            r["session"] = session
        t["replies"].append(r)
        t["updated_at"] = r["at"]
        if actor == "owner" and t["status"] == "blocked":
            _hist(t, "owner", "blocked", "submitted")
            t["claim"] = None
            _queue_add(t)
    return _mutate(tid, f)


def approve(tid, quote, actor="agent"):
    """Record the owner's words (from live chat) approving a governance/bulk ticket."""
    if not isinstance(quote, str) or not quote.strip():
        raise TicketError("--quote required", "invalid")

    def f(t, d):
        if t["kind"] not in ("governance", "bulk"):
            raise TicketError("approve applies to governance/bulk tickets", "refused")
        if t["status"] != "blocked":
            raise TicketError("approve requires a blocked ticket", "refused")
        t["approved_in_chat"] = {"quote": quote.strip(), "at": now(), "recorded_by": actor}
        t["updated_at"] = t["approved_in_chat"]["at"]
    return _mutate(tid, f)


def set_status(tid, to, actor="agent", session=None, no_change=False, links=None):
    _check_actor(actor)
    if to not in ("draft", "submitted", "in_progress", "blocked", "done"):
        raise TicketError("invalid status", "invalid")

    def f(t, d):
        frm = t["status"]
        if actor == "guest":
            raise TicketError("guests cannot change status", "refused")
        if actor == "owner":
            if (frm, to) not in OWNER_T:
                raise TicketError(f"owner transition {frm}->{to} not allowed", "refused")
            if frm == "submitted" and to == "draft" and t.get("claim"):
                raise TicketError("claimed; cannot recall", "refused")
        else:
            if (frm, to) not in AGENT_T:
                raise TicketError(f"agent transition {frm}->{to} not allowed", "refused")
            if t["author"] != "owner":
                raise TicketError("guest tickets are notes; agent cannot work them", "refused")
            if to == "blocked":
                if not _agent_reply_since_claim(t):
                    raise TicketError("blocked requires an attached reply", "refused")
            if to == "done":
                if t["kind"] in ("governance", "bulk") and not t.get("approved_in_chat"):
                    raise TicketError(f"{t['kind']} ticket requires `approve --quote` before done", "refused")
                if not _agent_reply_since_claim(t):
                    raise TicketError("done requires an attached reply", "refused")
                if links:
                    t["links"] = list(t["links"]) + [x for x in links if x not in t["links"]]
                last_links = t["replies"][-1].get("links") if t["replies"] else None
                if not (t["links"] or last_links or no_change):
                    raise TicketError("done requires links or --no-change", "refused")
                t["no_change"] = bool(no_change)
                if last_links:
                    t["links"] = list(t["links"]) + [x for x in last_links if x not in t["links"]]
        _hist(t, actor, frm, to, session)
        if frm == "submitted":
            _queue_remove(tid)
        if to == "submitted":
            _queue_add(t)
        if to == "submitted" and frm == "draft":
            pass
        if frm == "in_progress" and to != "in_progress":
            t["claim"] = None
            try:
                os.unlink(_claim_lock(tid))
            except OSError:
                pass
    return _mutate(tid, f)


def _agent_reply_since_claim(t):
    since = (t.get("claim") or {}).get("claimed_at") or ""
    return any(r["author"] == "agent" and r["at"] >= since for r in t["replies"])


def archive(tid, actor="owner"):
    if actor != "owner":
        raise TicketError("only owner may archive", "refused")
    board, t, arch = find(tid)
    if arch:
        raise TicketError("already archived", "refused")
    with _BoardLock(board):
        d = _load(board)
        for i, x in enumerate(d["tickets"]):
            if x["id"] == tid:
                if x["status"] != "done":
                    raise TicketError("only done tickets can be archived", "refused")
                x["archived"] = True
                _hist_archive(x, "archived")
                with open(_archive_path(board), "a", encoding="utf-8", newline="\n") as f:
                    f.write(json.dumps(x) + "\n")
                del d["tickets"][i]
                _save(board, d)
                return x
    raise TicketError("not found", "notfound")


def _hist_archive(t, what):
    t["history"].append({"at": now(), "actor": "owner", "from": "done", "to": what})
    t["updated_at"] = t["history"][-1]["at"]


def unarchive(tid, actor="owner"):
    if actor != "owner":
        raise TicketError("only owner may unarchive", "refused")
    board, t, arch = find(tid)
    if not arch:
        raise TicketError("not archived", "refused")
    with _BoardLock(board):
        rows = _archived(board)
        keep = [r for r in rows if r["id"] != tid]
        row = next(r for r in rows if r["id"] == tid)
        row["archived"] = False
        row["history"].append({"at": now(), "actor": "owner", "from": "archived", "to": "done"})
        row["updated_at"] = row["history"][-1]["at"]
        _atomic_write(_archive_path(board), "".join(json.dumps(r) + "\n" for r in keep))
        d = _load(board)
        d["tickets"].append(row)
        _save(board, d)
        return row


def plan(tid, diff_text=None):
    """Bulk dry-run: writes plans/{id}.diff (placeholder header if no diff given); returns path."""
    board, t, arch = find(tid)
    if t["kind"] != "bulk":
        raise TicketError("plan applies to kind=bulk tickets", "refused")
    path = _p("plans", f"{tid}.diff")
    body = diff_text if diff_text is not None else (
        f"# DRY-RUN PLAN for {tid} ({board}/{t['card']})\n# backup manifest: .bak-{now()[:10]}-{tid}\n"
        f"# request: {t['text'][:200]}\n# (agent: replace this stub with the real diff before moving to blocked)\n")
    _atomic_write(path, body)
    return path


def sweep(ttl=CLAIM_TTL, now_ts=None):
    """Remove claim locks older than ttl; return stale in_progress tickets to submitted. -> list of ids."""
    _ensure()
    t_now = now_ts if now_ts is not None else time.time()
    released = []
    for fn in os.listdir(_p("locks")):
        if fn.startswith("_") or not fn.endswith(".lock"):
            continue
        lp = _p("locks", fn)
        try:
            age = t_now - os.path.getmtime(lp)
        except OSError:
            continue
        if age <= ttl:
            continue
        tid = fn[:-5]
        try:
            os.unlink(lp)
        except OSError:
            continue
        try:
            def f(t, d):
                if t["status"] == "in_progress":
                    t["claim"] = None
                    _hist(t, "agent", "in_progress", "submitted", "sweep")
                    _queue_add(t)
                    return t
            _mutate(tid, f)
        except TicketError:
            pass
        released.append(tid)
    return released


# ---------- authorship (server-side) ----------
def owner_login():
    return (_read_json(_p("config.json"), {}) or {}).get("owner_login")


def compute_author(peer_ip, headers):
    """owner iff loopback peer with no Tailscale-User-Login header, or header == config.owner_login."""
    login = None
    for k, v in (headers or {}).items():
        if k.lower() == "tailscale-user-login":
            login = v
    if login is None:
        return "owner" if peer_ip in ("127.0.0.1", "::1") else "guest"
    ol = owner_login()
    return "owner" if ol and login == ol else "guest"
