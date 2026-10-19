#!/usr/bin/env python3
"""workflow-board validator (stdlib only). Exit 0 ok, 2 invalid (one-line reason on stderr)."""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import registry as REG  # noqa: E402
CARD_W, CARD_H = 192, 90
CLAUDE = os.environ.get("WB_CLAUDE_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


class Invalid(Exception):
    pass


def load_schema():
    with open(os.path.join(HERE, "schema", "board.schema.json"), encoding="utf-8") as f:
        return json.load(f)


_T = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is(v, t):
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, _T[t])


def check(v, s, path):
    if "const" in s and v != s["const"]:
        raise Invalid(f"{path}: must be {s['const']!r}")
    if "enum" in s and v not in s["enum"]:
        raise Invalid(f"{path}: {v!r} not in {s['enum']}")
    if "type" in s:
        ts = s["type"] if isinstance(s["type"], list) else [s["type"]]
        if not any(_is(v, t) for t in ts):
            raise Invalid(f"{path}: expected {'/'.join(ts)}")
    if isinstance(v, str) and "pattern" in s and not re.search(s["pattern"], v):
        raise Invalid(f"{path}: {v!r} does not match {s['pattern']}")
    if "minimum" in s and isinstance(v, (int, float)) and v < s["minimum"]:
        raise Invalid(f"{path}: below minimum {s['minimum']}")
    if isinstance(v, dict):
        for k in s.get("required", []):
            if k not in v:
                raise Invalid(f"{path}: missing required '{k}'")
        for k, sub in s.get("properties", {}).items():
            if k in v:
                check(v[k], sub, f"{path}.{k}")
    if isinstance(v, list) and "items" in s:
        for i, it in enumerate(v):
            check(it, s["items"], f"{path}[{i}]")


def find_verbs(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == "verb":
                yield path, v
            yield from find_verbs(v, f"{path}.{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from find_verbs(v, f"{path}[{i}]")


_SCANNED = None


def scanned_refs():
    """Refs of everything the harness-neutral scanners find (Kiro, ~/.agents, Codex), so a card made from any library item
    validates, not only ~/.claude ones. Roots come from WB_KIRO_DIR / WB_AGENTS_DIR / WB_CODEX_DIR (defaults ~/.kiro,
    ~/.agents, ~/.Codex). Cached per process; read-only."""
    global _SCANNED
    if _SCANNED is None:
        import library as L
        home = os.path.expanduser("~")
        roots = {"claude": None, "_mcp_json": None, "_tasks": False,
                 "codex": os.environ.get("WB_CODEX_DIR") or os.path.join(home, ".Codex"),
                 "kiro": os.environ.get("WB_KIRO_DIR") or os.path.join(home, ".kiro"),
                 "agents": os.environ.get("WB_AGENTS_DIR") or os.path.join(home, ".agents")}
        out = {"skill": set(), "hook": set(), "tool": set()}
        for it in L.scan(roots):
            t = it.get("card_template") or {}
            r = t.get("ref") or {}
            if t.get("type") == "skill" and r.get("name"):
                out["skill"].add(r["name"])
            elif t.get("type") == "hook" and r.get("file"):
                out["hook"].add(os.path.normpath(r["file"]))
            elif t.get("type") == "tool" and r.get("name"):
                out["tool"].add(r["name"])
        _SCANNED = out
    return _SCANNED


def tool_installed(name, schema):
    if name in schema["x-builtin-tools"]:
        return True
    if any(name.startswith(p) and len(name) > len(p) for p in schema["x-connected-mcp-prefixes"]):
        return True
    if name in scanned_refs()["tool"]:
        return True
    try:
        with open(os.path.join(CLAUDE, "settings.json"), encoding="utf-8") as f:
            return name in f.read()
    except OSError:
        return False


def resolve_refs(doc, schema):
    for c in doc["cards"]:
        t, r, cid = c["type"], c["ref"], c["id"]
        for k in schema["x-ref-required"].get(t, []):
            if k not in r:
                raise Invalid(f"card {cid}: {t} ref missing '{k}'")
        if t == "skill":
            n = r["name"]
            rp = str(r.get("path") or "")
            ok = re.fullmatch(r"[a-z0-9-]+", str(n)) and (os.path.isfile(os.path.join(CLAUDE, "skills", n, "SKILL.md"))
                                                          or n in scanned_refs()["skill"]
                                                          or (rp.endswith("SKILL.md") and os.path.isfile(os.path.expanduser(rp))))
            if not ok:
                raise Invalid(f"card {cid}: skill '{n}' not installed (no ~/.claude/skills/{n}/SKILL.md)")
        elif t == "hook":
            f = str(r["file"])
            p = os.path.normpath(os.path.join(CLAUDE, f))
            claude_ok = ".." not in f.replace("\\", "/").split("/") and p.startswith(os.path.normpath(CLAUDE)) and os.path.isfile(p)
            if not claude_ok and os.path.normpath(f) not in scanned_refs()["hook"]:
                raise Invalid(f"card {cid}: hook file '{f}' not installed under ~/.claude")
        elif t == "step":
            # SOP step card: documentation only, no ref resolution. v1 steps (no ref.kind) need sop + order;
            # v2 steps carry ref.kind (task|choice|...|proposed) and are exempt.
            k = r.get("kind")
            if k is None:
                if not isinstance(c.get("sop"), str) or not isinstance(c.get("order"), int):
                    raise Invalid(f"card {cid}: step needs 'sop' (string) and 'order' (integer)")
            elif k not in schema["x-step-ref-kinds"]:
                raise Invalid(f"card {cid}: step ref.kind {k!r} not in {schema['x-step-ref-kinds']}")
        elif t == "tool":
            if not tool_installed(str(r["name"]), schema):
                raise Invalid(f"card {cid}: tool '{r['name']}' not installed")


def card_verbs(c):
    return {v for _, v in find_verbs(c)}


def check_cards_v2(cards, schema, target):
    for c in cards:
        cid = c["id"]
        if c["execution_mode"] == "enforce":
            if c["type"] not in schema["x-enforce-types"]:
                raise Invalid(f"card {cid}: execution_mode enforce is legal only on {'/'.join(schema['x-enforce-types'])} cards, not '{c['type']}'")
            if c["type"] == "step" or c["ref"].get("kind") == "proposed":
                raise Invalid(f"card {cid}: proposed steps are observe-only")
        if c["ref"].get("kind") == "proposed" and c["type"] == "step" and c["execution_mode"] != "observe":
            raise Invalid(f"card {cid}: proposed steps are observe-only")
        if target in ("codex", "kiro") and c["enabled"] and c["execution_mode"] == "enforce":
            if target == "kiro":
                raise Invalid(f"target kiro cannot honor enforce card '{cid}' (spec-only adapter)")
            if "inject" in card_verbs(c):
                raise Invalid(f"target codex cannot honor enforce inject card '{cid}'")


def check_style_fields(cards, schema):
    """v2.1: optional card style{color}, fields{} (scalar values, no reserved keys)."""
    reserved = schema["x-reserved-field-keys"]
    for c in cards:
        cid = c.get("id")
        st = c.get("style")
        if st is not None:
            if not isinstance(st, dict):
                raise Invalid(f"card {cid}: style must be an object")
            for k in st:
                if k != "color":
                    raise Invalid(f"card {cid}: style key {k!r} not allowed (only color)")
        f = c.get("fields")
        if f is None:
            continue
        if not isinstance(f, dict):
            raise Invalid(f"card {cid}: fields must be an object")
        for k, val in f.items():
            if k in reserved:
                raise Invalid(f"card {cid}: field name {k!r} shadows a reserved card key ({', '.join(reserved)})")
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", k):
                raise Invalid(f"card {cid}: field name {k!r} must be 1-40 chars of A-Z a-z 0-9 _ . -")
            if not (isinstance(val, (str, int, float, bool)) and val == val and val not in (float("inf"), float("-inf"))):
                raise Invalid(f"card {cid}: field {k!r} must be a string, number or boolean")
            if isinstance(val, str) and len(val) > 4000:
                raise Invalid(f"card {cid}: field {k!r} longer than 4000 chars")


def check_group_styles(doc):
    gs = doc.get("group_styles")
    if gs is None:
        return
    if not isinstance(gs, dict):
        raise Invalid("board.group_styles: expected object")
    names = {c.get("group") for c in doc["cards"] if c.get("group")}
    for g, st in gs.items():
        if not isinstance(st, dict) or set(st) - {"color", "size", "collapsed"}:
            raise Invalid(f"board.group_styles.{g}: must be an object with only 'color', 'size' and 'collapsed'")
        if "collapsed" in st and not isinstance(st["collapsed"], bool):
            raise Invalid(f"board.group_styles.{g}.collapsed: must be boolean")
        if "size" in st:
            sz = st["size"]
            if not isinstance(sz, dict) or not sz or set(sz) - {"w", "h"}:
                raise Invalid(f"board.group_styles.{g}.size: must be an object with 'w' and/or 'h'")
            for k, v in sz.items():
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not (0 < v <= 20000):
                    raise Invalid(f"board.group_styles.{g}.size.{k}: must be a number in (0, 20000]")
        if "color" in st and not (isinstance(st["color"], str) and re.fullmatch(r"#[0-9a-fA-F]{6}", st["color"])):
            raise Invalid(f"board.group_styles.{g}.color: must be #rrggbb")
        if g not in names:
            raise Invalid(f"board.group_styles: group {g!r} has no cards")


def check_graph(doc):
    ids = [c["id"] for c in doc["cards"]]
    if len(set(ids)) != len(ids):
        raise Invalid("duplicate card id")
    ports = {c["id"]: c["ports"] for c in doc["cards"]}
    for e in doc["edges"]:
        for side, key in (("from", "out"), ("to", "in")):
            cid, port = e[side].rsplit(".", 1)
            if cid not in ports:
                raise Invalid(f"edge {e['from']}->{e['to']}: unknown card '{cid}'")
            if port not in ports[cid][key]:
                raise Invalid(f"edge {e['from']}->{e['to']}: card '{cid}' has no {key} port '{port}'")


def check_v3(doc, schema):
    """v3: ref.kind enums, datastore authority, boundary membership, drill links. Returns warnings."""
    warns = []
    cards = {c["id"]: c for c in doc["cards"]}
    for c in doc["cards"]:
        cid, t = c["id"], c["type"]
        kinds = schema["x-ref-kinds"].get(t)
        if kinds and c["ref"].get("kind") not in kinds:
            raise Invalid(f"card {cid}: {t} ref.kind {c['ref'].get('kind')!r} not in {kinds}")
        if t == "datastore":
            a = (c.get("fields") or {}).get("authority")
            if a not in schema["x-datastore-authority"]:
                raise Invalid(f"card {cid}: datastore fields.authority required, one of {schema['x-datastore-authority']}")
        if t == "boundary" and "size" not in c:
            raise Invalid(f"card {cid}: boundary needs size {{w,h}}")
        b = c.get("boundary")
        if b is not None:
            if b not in cards or cards[b]["type"] != "boundary":
                raise Invalid(f"card {cid}: boundary {b!r} is not a boundary card on this board")
            if b == cid:
                raise Invalid(f"card {cid}: boundary contains itself")
    for cid in cards:  # containment chain must not loop
        seen, cur = set(), cid
        while cards[cur].get("boundary"):
            if cur in seen:
                raise Invalid(f"card {cid}: boundary containment cycle")
            seen.add(cur)
            cur = cards[cur]["boundary"]
    def chain(cid):
        out, cur = set(), cards[cid].get("boundary")
        while cur and cur not in out:
            out.add(cur)
            cur = cards[cur].get("boundary")
        return out
    boxes = [c for c in doc["cards"] if c["type"] == "boundary" and "size" in c]
    for c in doc["cards"]:
        w, h = (c["size"]["w"], c["size"]["h"]) if "size" in c else (CARD_W, CARD_H)
        cx, cy = c["pos"]["x"] + w / 2, c["pos"]["y"] + h / 2
        for bx in boxes:
            if bx["id"] == c["id"] or bx["id"] in chain(c["id"]) or c["id"] in chain(bx["id"]):
                continue
            if bx["pos"]["x"] <= cx <= bx["pos"]["x"] + bx["size"]["w"] and bx["pos"]["y"] <= cy <= bx["pos"]["y"] + bx["size"]["h"]:
                warns.append(f"card {c['id']} sits inside boundary {bx['id']} but has no boundary field")
    return warns


def check_v4(doc, schema):
    """v4 (all optional, schema stays v1): note + container cards, approval gate, pin, on_fail, ref.via_container.
    Raises Invalid with a one-line reason. enforce on note/container is already refused by check_cards_v2 (x-enforce-types)."""
    v4 = schema["x-v4"]
    tok = [re.compile(p) for p in v4["token-like-account-patterns"]]
    cards = {c["id"]: c for c in doc["cards"]}

    def no_token(cid, label, val):
        if any(p.search(val) for p in tok):
            raise Invalid(f"card {cid}: {label} looks like a token or secret; store a display identity only")

    for c in doc["cards"]:
        cid, t, r = c["id"], c["type"], c["ref"]
        f = c.get("fields") or {}
        if t == "note":
            if c["ports"]["in"] or c["ports"]["out"]:
                raise Invalid(f"card {cid}: note cards have no ports")
            if "text" in f and (not isinstance(f["text"], str) or len(f["text"]) > v4["note-text-max"]):
                raise Invalid(f"card {cid}: note fields.text must be a string of at most {v4['note-text-max']} chars")
        elif t == "container":
            if r.get("connector") not in v4["container-connectors"]:
                raise Invalid(f"card {cid}: container ref.connector {r.get('connector')!r} not in {v4['container-connectors']}")
            acct = r.get("account")
            if acct is not None:
                if not isinstance(acct, str) or len(acct) > 120:
                    raise Invalid(f"card {cid}: container ref.account must be a display string of at most 120 chars")
                no_token(cid, "ref.account", acct)
            for k, val in f.items():
                if isinstance(val, str):
                    no_token(cid, f"fields.{k}", val)
            if "inventory_key" in f and not (isinstance(f["inventory_key"], str) and f["inventory_key"]):
                raise Invalid(f"card {cid}: container fields.inventory_key must be a non-empty string")
        elif t == "gate" and r.get("kind") == v4["gate-approval-kind"]:
            if r.get("verb") != v4["gate-approval-verb"]:
                raise Invalid(f"card {cid}: approval gate verb must be '{v4['gate-approval-verb']}', not {r.get('verb')!r}")
            if r.get("approver") != v4["gate-approval-approver"]:
                raise Invalid(f"card {cid}: approval gate ref.approver must be '{v4['gate-approval-approver']}' (approval is the owner's own message, never a node)")
            if not (isinstance(r.get("draft_path"), str) and r["draft_path"].strip()):
                raise Invalid(f"card {cid}: approval gate needs ref.draft_path (non-empty string)")
        if "run_mode" in c:
            rm = schema["x-run-mode"]
            if c["run_mode"] not in ("inline", "background", "wait"):
                raise Invalid(f"card {cid}: run_mode {c['run_mode']!r} must be inline, background or wait")
            if t == "gate":
                raise Invalid(f"card {cid}: run_mode is not allowed on a gate/approval card (gates are decided in the main session)")
            if c.get("immutable") is True:
                raise Invalid(f"card {cid}: run_mode is not allowed on an immutable card")
            if t not in rm["card-types"] or (t == "step" and r.get("kind") not in rm["step-ref-kinds"]):
                raise Invalid(f"card {cid}: run_mode is only allowed on dispatchable action cards (skill, tool, agent, step task), not '{t}'")
        pin = c.get("pin")
        if pin is not None:
            if pin.get("synthetic") is not True:
                raise Invalid(f"card {cid}: pin.synthetic must be true (pinned payloads are synthetic samples, never real data)")
            if pin["port"] not in c["ports"]["in"] and pin["port"] not in c["ports"]["out"]:
                raise Invalid(f"card {cid}: pin.port {pin['port']!r} is not a port of this card")
            n = len(json.dumps(pin["sample"], separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
            if n > v4["pin-sample-max-bytes"]:
                raise Invalid(f"card {cid}: pin.sample is {n} bytes, over the {v4['pin-sample-max-bytes']} byte limit")
        of = c.get("on_fail")
        if of is not None:
            tg = cards.get(of["target"])
            if tg is None:
                raise Invalid(f"card {cid}: on_fail.target {of['target']!r} is not a card on this board")
            if tg["type"] != "note":
                raise Invalid(f"card {cid}: on_fail.target {of['target']!r} must be a note card, not '{tg['type']}'")
        vc = r.get("via_container")
        if vc is not None:
            if not isinstance(vc, str) or vc not in cards or cards[vc]["type"] != "container":
                raise Invalid(f"card {cid}: ref.via_container {vc!r} is not a container card on this board")


def board_drills(doc):
    return {c["id"]: c["drill"] for c in doc["cards"] if c.get("drill")}


def load_board_file(name):
    p = REG.resolve(name, os.path.join(HERE, "boards"))
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def check_drills(doc):
    """Drill graph over boards/: dangling target and cycle are errors; port/trigger mismatch is a warning."""
    warns = []
    cache = {doc["board"]: doc}

    def get(n):
        if n not in cache:
            cache[n] = load_board_file(n)
        return cache[n]

    for c in doc["cards"]:
        t = c.get("drill")
        if not t:
            continue
        child = get(t)
        if child is None:
            raise Invalid(f"card {c['id']}: drill target not found: board '{t}'")
        kinds = {x["type"] for x in child.get("cards", [])}
        if not (kinds & {"trigger", "input", "output"}):
            continue  # architecture child board: no workflow entry/exit cards to compare
        if c["ports"]["in"] and "trigger" not in kinds and "input" not in kinds:
            warns.append(f"card {c['id']}: declares in ports but board '{t}' has no trigger/input cards")
        if c["ports"]["out"] and "output" not in kinds:
            warns.append(f"card {c['id']}: declares out ports but board '{t}' has no output cards")
    state = {}

    def dfs(n, path):
        if state.get(n) == 2:
            return
        if state.get(n) == 1:
            raise Invalid("drill cycle: " + " -> ".join(path[path.index(n):] + [n]))
        state[n] = 1
        d = get(n)
        for tgt in (board_drills(d).values() if d else []):
            if get(tgt) is None:
                raise Invalid(f"drill target not found: board '{tgt}' (from '{n}')")
            dfs(tgt, path + [n])
        state[n] = 2

    dfs(doc["board"], [])
    return warns


def load_named(fn):
    with open(os.path.join(HERE, "schema", fn), encoding="utf-8") as f:
        return json.load(f)


def validate_module(doc, name=None):
    board = load_schema()
    mod = load_named("module.schema.json")
    mod["properties"]["cards"]["items"] = board["properties"]["cards"]["items"]
    check(doc, mod, "module")
    if name is not None and doc["name"] != name:
        raise Invalid(f"module name '{doc['name']}' does not match '{name}'")
    check_graph(doc)
    check_cards_v2(doc["cards"], board, None)
    check_style_fields(doc["cards"], board)
    for p, v in find_verbs(doc):
        if v not in board["x-verbs"]:
            raise Invalid(f"verb {v!r} at {p} not allowed (only inject, deny)")
    resolve_refs(doc, board)


def validate_proposal(doc):
    check(doc, load_named("proposal.schema.json"), "proposal")
    ids = [s["id"] for s in doc["steps"]]
    if len(set(ids)) != len(ids):
        raise Invalid("duplicate step id")
    ports = {s["id"]: s["ports"] for s in doc["steps"]}
    for e in doc["edges"]:
        for side, key in (("from", "out"), ("to", "in")):
            cid, port = e[side].rsplit(".", 1)
            if cid not in ports or port not in ports[cid][key]:
                raise Invalid(f"edge {e['from']}->{e['to']}: unknown {key} port '{e[side]}'")


def validate_doc(doc, name=None, target=None):
    """Raise Invalid with a one-line reason; return None when valid."""
    schema = load_schema()
    check(doc, schema, "board")
    if name is not None and doc["board"] != name:
        raise Invalid(f"board name '{doc['board']}' does not match '{name}'")
    ids = [c["id"] for c in doc["cards"]]
    if len(set(ids)) != len(ids):
        raise Invalid("duplicate card id")
    check_cards_v2(doc["cards"], schema, target)
    check_style_fields(doc["cards"], schema)
    check_group_styles(doc)
    check_v4(doc, schema)
    for need in schema["x-immutable-required"].get(doc["board"], []):
        c = next((c for c in doc["cards"] if c["id"] == need), None)
        if c is None:
            raise Invalid(f"immutable card '{need}' is missing")
        if c["immutable"] is not True:
            raise Invalid(f"card '{need}' must have immutable=true")
    for p, v in find_verbs(doc):
        if v not in schema["x-verbs"]:
            raise Invalid(f"verb {v!r} at {p} not allowed (only inject, deny)")
    ports = {c["id"]: c["ports"] for c in doc["cards"]}
    for e in doc["edges"]:
        for side, key in (("from", "out"), ("to", "in")):
            cid, port = e[side].rsplit(".", 1)
            if cid not in ports:
                raise Invalid(f"edge {e['from']}->{e['to']}: unknown card '{cid}'")
            if port not in ports[cid][key]:
                raise Invalid(f"edge {e['from']}->{e['to']}: card '{cid}' has no {key} port '{port}'")
    resolve_refs(doc, schema)
    warns = check_v3(doc, schema) + check_drills(doc) + check_shared(doc)
    for w in warns:
        print(f"WARN: {w}", file=sys.stderr)



SHARED_OVERRIDES = ("skip", "enabled", "execution_mode", "local_note")
SHARED_SYNC = ("synced", "drifted", "proposed", "missing")


def check_shared(doc):
    """Shared step cards: library entries, instances, flat topology, override whitelist. Returns warnings."""
    warns = []
    libs = doc.get("shared_library")
    if libs is None:
        libs = []
    if not isinstance(libs, list):
        raise Invalid("shared_library: expected array")
    entries, seen = {}, set()
    for i, e in enumerate(libs):
        p = f"shared_library[{i}]"
        if not isinstance(e, dict):
            raise Invalid(f"{p}: expected object")
        if "shared" in e:
            raise Invalid(f"{p}: library entry may not carry 'shared' (nested shared steps are not allowed)")
        sid = e.get("id")
        if not isinstance(sid, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", sid):
            raise Invalid(f"{p}: id must match [a-z0-9][a-z0-9-]*")
        if sid in seen:
            raise Invalid(f"{p}: duplicate shared step id '{sid}'")
        seen.add(sid)
        if not (isinstance(e.get("version"), int) and not isinstance(e.get("version"), bool) and e["version"] >= 1):
            raise Invalid(f"{p}: version must be an integer >= 1")
        for k in ("label", "body"):
            if not isinstance(e.get(k), str) or not e[k].strip():
                raise Invalid(f"{p}: {k} must be a non-empty string")
        if "history" in e and not isinstance(e["history"], list):
            raise Invalid(f"{p}: history must be an array")
        check_style_fields([{"id": sid, "fields": e.get("fields")}], load_schema())
        entries[sid] = e
    counts = {sid: 0 for sid in entries}
    for c in doc["cards"]:
        sh = c.get("shared")
        if sh is None:
            continue
        cid = c["id"]
        if not isinstance(sh, dict):
            raise Invalid(f"card {cid}: shared must be an object")
        if c["type"] != "step":
            raise Invalid(f"card {cid}: only step cards may carry 'shared'")
        sid = sh.get("step_id")
        if sid not in entries:
            raise Invalid(f"card {cid}: shared.step_id {sid!r} is not in shared_library (dangling reference)")
        counts[sid] += 1
        v = sh.get("version")
        if not (isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= entries[sid]["version"]):
            raise Invalid(f"card {cid}: shared.version must be an integer between 1 and {entries[sid]['version']}")
        ov = sh.get("overrides", {})
        if not isinstance(ov, dict):
            raise Invalid(f"card {cid}: shared.overrides must be an object")
        for k, val in ov.items():
            if k not in SHARED_OVERRIDES:
                raise Invalid(f"card {cid}: override {k!r} not allowed (only {', '.join(SHARED_OVERRIDES)}); unlink to change label or body")
            if k in ("skip", "enabled") and not isinstance(val, bool):
                raise Invalid(f"card {cid}: override {k} must be boolean")
            if k == "execution_mode" and val not in ("observe", "enforce"):
                raise Invalid(f"card {cid}: override execution_mode must be observe or enforce")
            if k == "local_note" and (not isinstance(val, str) or len(val) > 280):
                raise Invalid(f"card {cid}: override local_note must be a string of at most 280 chars")
        if sh.get("sop_sync", "drifted") not in SHARED_SYNC:
            raise Invalid(f"card {cid}: shared.sop_sync must be one of {', '.join(SHARED_SYNC)}")
        a = sh.get("sop_anchor")
        if a is not None and (not isinstance(a, dict) or a.get("scope", "whole") not in ("whole", "fragment")):
            raise Invalid(f"card {cid}: shared.sop_anchor must be an object with scope whole or fragment")
        if c["label"] != entries[sid]["label"] or c["ref"].get("detail") != entries[sid]["body"]:
            warns.append(f"card {cid}: label/ref.detail differ from shared step {sid} (re-save to mirror)")
    for sid, e in entries.items():
        if e.get("refcount") != counts[sid]:
            raise Invalid(f"shared step '{sid}': refcount {e.get('refcount')!r} != {counts[sid]} instances")
        if counts[sid] == 0:
            warns.append(f"orphan shared step '{sid}' (refcount 0)")
    return warns

def main(argv):
    args = argv[1:]
    mode, target, path = "board", None, None
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--module", "--proposal"):
            mode = a[2:]
        elif a == "--target" and i + 1 < len(args):
            i += 1
            target = args[i]
        elif a.startswith("--target="):
            target = a.split("=", 1)[1]
        elif path is None and not a.startswith("--"):
            path = a
        else:
            path = None
            break
        i += 1
    if path is None or target not in (None, "claude", "codex", "kiro"):
        print("usage: validate.py <file.json> [--module|--proposal] [--target claude|codex|kiro]", file=sys.stderr)
        return 2
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        if mode == "module":
            validate_module(doc)
        elif mode == "proposal":
            validate_proposal(doc)
        else:
            validate_doc(doc, target=target)
    except Invalid as e:
        print(f"INVALID: {e}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as e:
        print(f"INVALID: cannot read/parse: {str(e)[:120]}", file=sys.stderr)
        return 2
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
