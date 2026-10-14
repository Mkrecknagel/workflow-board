#!/usr/bin/env python3
"""v4 runs / history / active routes (N3). Installed by serve_v4 (OPTIONAL_MODULES) via install(v4). GET only; read-only.

  /runs/{board}?card=<id>   last <=5 telemetry rows newest first (runs/*.jsonl; rows with card:null tolerated and never match a card).
                            Each row carries snapshot_id / board_hash_at_run / board_changed_since (open-run: the board version that
                            was live at the row's ts = earliest history snapshot written after it, else the current file).
  /runs/{board}?summary=1   {card: {ts, event, count}} last-seen per card (feeds the canvas overlay).
  /history/{board}          list of boards/.history snapshots, newest first.
  /history/{board}/{id}     the snapshot JSON; ?diff=current|<other id> returns an added/removed/changed card-id diff instead.
  /active                   boards/active.json + semantic hash of the deployed board file as it is on disk now.
RESTORE is not a new write route: the canvas fetches /history/{board}/{id} and PUTs it to the EXISTING /board/{name} (validated,
immutable-card rules, serve.backup_and_write backup). restore() below is the same operation as a function for tests/tools; it only
ever touches boards/{name}.json (+ the .history backup) and never boards/active.json.
semantic_text() re-implements executor/hooks/register.tsx semanticText (layout/style/pin/note cards excluded, keys sorted, compact JSON)."""
import glob
import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
import sys  # noqa: E402
sys.path.insert(0, HERE)
import registry as REG  # noqa: E402
LAYOUT_TOP = {"saved_at", "group_styles", "integrity_hash"}
LAYOUT_CARD = {"pos", "size", "style", "pin"}
NOTE_TYPE = "note"
MAX_ROWS = 5


def _canon(v):
    if isinstance(v, list):
        return [_canon(x) for x in v]
    if isinstance(v, dict):
        return {k: _canon(v[k]) for k in sorted(v)}
    if isinstance(v, float) and v == v and v not in (float("inf"), float("-inf")) and v == int(v):
        return int(v)  # JS prints 1.0 as 1
    return v


def _end_id(p):
    return str(p if p is not None else "").rsplit(".", 1)[0]


def semantic_text(doc):
    top = {k: v for k, v in (doc or {}).items() if k not in LAYOUT_TOP}
    if isinstance(top.get("cards"), list):
        note_ids = {str(c.get("id")) for c in top["cards"] if isinstance(c, dict) and c.get("type") == NOTE_TYPE}
        top["cards"] = [{k: v for k, v in c.items() if k not in LAYOUT_CARD} if isinstance(c, dict) else c
                        for c in top["cards"] if not (isinstance(c, dict) and c.get("type") == NOTE_TYPE)]
        if note_ids and isinstance(top.get("edges"), list):
            top["edges"] = [e for e in top["edges"]
                            if not (isinstance(e, dict) and (_end_id(e.get("from")) in note_ids or _end_id(e.get("to")) in note_ids))]
    return json.dumps(_canon(top), separators=(",", ":"), ensure_ascii=False)


def semantic_hash(doc):
    return hashlib.sha256(semantic_text(doc).encode("utf-8")).hexdigest()


def _sj(v):
    return json.dumps(_canon(v), sort_keys=True)


def diff_boards(a, b):
    """Card-level semantic diff a -> b. Ignores pos/size/saved_at/group_styles/integrity_hash."""
    a, b = a or {}, b or {}
    ca = {c["id"]: c for c in a.get("cards", []) if c.get("type") != NOTE_TYPE}
    cb = {c["id"]: c for c in b.get("cards", []) if c.get("type") != NOTE_TYPE}

    def sem(c):
        return _sj({k: v for k, v in c.items() if k not in LAYOUT_CARD})
    changed = sorted(i for i in ca if i in cb and sem(ca[i]) != sem(cb[i]))
    skip = LAYOUT_TOP | {"cards", "edges"}
    ta = {k: v for k, v in a.items() if k not in skip}
    tb = {k: v for k, v in b.items() if k not in skip}
    return {"added": sorted(set(cb) - set(ca)), "removed": sorted(set(ca) - set(cb)), "changed": changed,
            "board_fields_changed": sorted(k for k in set(ta) | set(tb) if _sj(ta.get(k)) != _sj(tb.get(k))),
            "edges_changed": _sj(a.get("edges")) != _sj(b.get("edges"))}


def _boards_dir(host=None):
    return getattr(host, "BOARDS", None) or os.path.join(HERE, "boards")


def _runs_dir(boards):
    return os.path.join(os.path.dirname(boards), "runs")


def _read_json(p):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def list_history(board, boards):
    """-> [{id, ts, sha8, bytes, file}] newest first. File: <board>.<ts>.<sha8>.json ; id = '<ts>.<sha8>'."""
    hd = os.path.join(boards, ".history")
    out = []
    if not os.path.isdir(hd):
        return out
    pre = board + "."
    for fn in os.listdir(hd):
        if not (fn.startswith(pre) and fn.endswith(".json")):
            continue
        parts = fn[len(pre):-5].split(".")
        if len(parts) != 2:
            continue
        out.append({"id": fn[len(pre):-5], "ts": parts[0], "sha8": parts[1], "bytes": os.path.getsize(os.path.join(hd, fn)), "file": fn})
    out.sort(key=lambda r: r["ts"], reverse=True)
    return out


def _snapshot_path(board, sid, boards):
    for r in list_history(board, boards):
        if r["id"] == sid:
            return os.path.join(boards, ".history", r["file"])
    return None


def _iso_from_hts(ts):  # 20261005T201155036783Z -> 2026-10-05T20:11:55.036Z
    try:
        return f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}T{ts[9:11]}:{ts[11:13]}:{ts[13:15]}.{ts[15:18]}Z"
    except Exception:  # noqa: BLE001
        return None


def read_rows(runs_dir):
    rows = []
    for f in sorted(glob.glob(os.path.join(runs_dir, "*.jsonl"))):
        try:
            with open(f, encoding="utf-8") as fh:
                for ln in fh:
                    try:
                        d = json.loads(ln)
                    except ValueError:
                        continue
                    if isinstance(d, dict) and d.get("ts"):
                        rows.append(d)
        except OSError:
            continue
    rows.sort(key=lambda d: str(d.get("ts")))
    return rows


def _board_rows(rows, board):
    """Rows for a board. Card rows carry no board field, so they belong to the board named by the nearest preceding row that has one
    (the executor runs one board at a time); rows before any marker are unattributed and skipped."""
    cur, out = None, []
    for d in rows:
        if d.get("board"):
            cur = d["board"]
        if cur == board:
            out.append(d)
    return out


def runs_for(board, card, boards, limit=MAX_ROWS):
    rows = _board_rows(read_rows(_runs_dir(boards)), board)
    if card is not None:
        rows = [d for d in rows if d.get("card") == card]  # card:null rows never match a card id
    rows = rows[::-1][:limit]
    hist = sorted(list_history(board, boards), key=lambda r: r["ts"])
    cur_path = REG.resolve(board, boards)
    cur_hash = semantic_hash(_read_json(cur_path)) if os.path.isfile(cur_path) else None
    cache = {}

    def hash_of(sid):
        if sid not in cache:
            p = _snapshot_path(board, sid, boards)
            try:
                cache[sid] = semantic_hash(_read_json(p)) if p else None
            except (OSError, ValueError):
                cache[sid] = None
        return cache[sid]
    out = []
    for d in rows:
        r = dict(d)
        sid, h = "current", cur_hash
        for s in hist:  # earliest snapshot written after the row = the version that was live when the row happened
            iso = _iso_from_hts(s["ts"])
            if iso and iso > str(d["ts"]):
                sid, h = s["id"], hash_of(s["id"])
                break
        r["snapshot_id"] = sid
        r["board_hash_at_run"] = h
        r["board_changed_since"] = bool(h is not None and cur_hash is not None and h != cur_hash)
        r["snapshot_note"] = "board changed since" if r["board_changed_since"] else "board unchanged since"
        out.append(r)
    return out


def summary_for(board, boards):
    s = {}
    for d in _board_rows(read_rows(_runs_dir(boards)), board):
        c = d.get("card")
        if not c:
            continue
        e = s.setdefault(c, {"ts": d["ts"], "event": d.get("event"), "count": 0})
        e["count"] += 1
        e["ts"], e["event"] = d["ts"], d.get("event")
    return s


def diff_history(board, sid, other, boards):
    p = _snapshot_path(board, sid, boards)
    if not p:
        return None
    a = _read_json(p)
    if other in (None, "", "current"):
        b = _read_json(REG.resolve(board, boards))
        other = "current"
    else:
        op = _snapshot_path(board, other, boards)
        if not op:
            return None
        b = _read_json(op)
    d = diff_boards(a, b)  # snapshot -> other (what changed since this snapshot)
    d.update({"from": sid, "to": other, "same_semantics": semantic_hash(a) == semantic_hash(b)})
    return d


def restore(board, sid, boards, backup_and_write=None):
    """Write snapshot `sid` over boards/{board}.json via the existing backup path. Never touches active.json. Returns new semantic hash."""
    if backup_and_write is None:
        import serve  # noqa: PLC0415
        backup_and_write = serve.backup_and_write
    p = _snapshot_path(board, sid, boards)
    if not p:
        raise FileNotFoundError(f"no snapshot {sid} for {board}")
    with open(p, "rb") as fh:
        raw = fh.read()
    doc = json.loads(raw.decode("utf-8"))
    import validate as V  # noqa: PLC0415
    V.validate_doc(doc, board)
    backup_and_write(REG.resolve(board, boards), raw, os.path.join(boards, ".history"), board)
    return semantic_hash(doc)


def active_info(boards):
    p = os.path.join(boards, "active.json")
    if not os.path.isfile(p):
        return {"active": None}
    a = _read_json(p)
    info = {"active": a, "board": a.get("board")}
    bp = REG.resolve(a.get("board"), boards)
    if a.get("board") and os.path.isfile(bp):
        h = semantic_hash(_read_json(bp))
        info.update({"file_hash": h, "file_matches_deploy": h == a.get("hash")})
    else:
        info.update({"file_hash": None, "file_matches_deploy": False})
    return info


# ---- handlers: fn(h, host, params, query) ----
def _q(query, k):
    v = query.get(k)
    return v[0] if v else None


def handle_runs(h, host, params, query):
    boards = _boards_dir(host)
    if _q(query, "summary"):
        return h._send(200, json.dumps({"board": params["board"], "last": summary_for(params["board"], boards)}))
    card = _q(query, "card")
    return h._send(200, json.dumps({"board": params["board"], "card": card, "rows": runs_for(params["board"], card, boards)}))


def handle_history(h, host, params, query):
    rows = list_history(params["board"], _boards_dir(host))
    for r in rows:
        r["iso"] = _iso_from_hts(r["ts"])
    return h._send(200, json.dumps({"board": params["board"], "snapshots": rows}))


def handle_history_item(h, host, params, query):
    boards = _boards_dir(host)
    b, sid = params["board"], params["id"]
    if _q(query, "diff") is not None:
        d = diff_history(b, sid, _q(query, "diff"), boards)
        return h._send(200, json.dumps(d)) if d else h._err(410, "snapshot not found")
    p = _snapshot_path(b, sid, boards)
    if not p:
        return h._err(410, "snapshot not found (pruned or never existed)")  # 410 not 404: snapshots are pruned at KEEP
    with open(p, "rb") as fh:
        return h._send(200, fh.read())


def handle_active(h, host, params, query):
    return h._send(200, json.dumps(active_info(_boards_dir(host))))


def install(v4):
    v4.register("runs", handle_runs)
    v4.register("history", handle_history)
    v4.register("history_item", handle_history_item)
    v4.register("active", handle_active)
