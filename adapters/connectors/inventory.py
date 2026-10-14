#!/usr/bin/env python3
"""Connector inventory generator (v4 F-A). Read-only on ~/.claude and an optional identity config (WB_IDENTITY_CONFIG); writes ONLY inventory/connectors.json
inside the workflow-board workspace. Never reads token/credential values: only tool names, file names, identity display strings.

  python adapters/connectors/inventory.py            # regenerate inventory/connectors.json
  python adapters/connectors/inventory.py --check    # regenerate in memory + assert invariants (exit 0/1), also checks the file on disk

Per connector: {connector, server, account, tools[{name,class,status,source}], hooks[{name,source}], skills[{name,source}],
counts{tools,hooks,skills}, generated_at, sources[]}.
Tool classification is an ALLOW-LIST (C1-design.md section 3). Anything not on it, and every unknown tool, is `blocked`:
  read   ^(get|list|search|read|fetch|download|suggest|query)_      -> status ok
  draft  create_draft / update_draft / delete_draft                  -> status ok
  Calendar create_event / update_event                               -> allowed-carveout (CLAUDE.md 2026-08-03)
  Discord reply / react / edit_message                               -> channel-reply (main session only)
  everything else (send_message, reply, forward, share_file, delete_*, respond_to_event, trash_*, unknown) -> blocked
"""
import glob
import json
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
WB = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(WB, "inventory", "connectors.json")
HOME = os.path.expanduser("~")
CLAUDE = os.environ.get("WB_CLAUDE_DIR") or os.path.join(HOME, ".claude")
IDENTITY_CONFIG = os.environ.get("WB_IDENTITY_CONFIG") or ""

CONNECTORS = {
    "gmail": {"prefix": "mcp__claude_ai_Gmail__", "tokens": r"gmail", "map_heading": "Gmail"},
    "drive": {"prefix": "mcp__claude_ai_Google_Drive__", "tokens": r"google[ _-]?drive|gdrive|\bdrive\b", "map_heading": "Google Drive"},
    "calendar": {"prefix": "mcp__claude_ai_Google_Calendar__", "tokens": r"calendar", "map_heading": "Google Calendar"},
    "discord": {"prefix": "mcp__plugin_discord_discord__", "tokens": r"discord", "map_heading": None},
}
# Tools known to exist on these connectors but absent from adapter-map.md; listed so the UI shows them (blocked). Never trusted.
KNOWN_UNVERIFIED = {
    "gmail": ["send_message", "reply", "forward", "delete_draft", "trash_message", "trash_thread", "mark_message_spam", "mark_thread_spam"],
    "drive": ["share_file", "trash_file", "update_file"],
    "calendar": ["delete_event", "respond_to_event"],
    "discord": [],
}
READ_RE = re.compile(r"^(get|list|search|read|fetch|download|suggest|query)_")
DRAFT = {"create_draft", "update_draft", "delete_draft"}
CARVEOUT = {"calendar": {"create_event", "update_event"}}
CHANNEL_REPLY = {"discord": {"reply", "react", "edit_message"}}
SECRET_PATTERNS = [r"[A-Za-z0-9_\-+/=]{32,}", r"(?i)\b(sk|pk|ghp|gho|xox[abp]|ya29|AKIA|Bearer)[-_ ]?[A-Za-z0-9]{8,}",
                   r"(?i)(token|secret|password|passwd|api[_-]?key|authorization)\s*[:=]",
                   r"[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6,7}\.[A-Za-z0-9_-]{27,}"]


def base_name(tool):
    """Strip any mcp__<server>__ prefix -> bare tool name."""
    m = re.match(r"^mcp__.+?__(.+)$", tool)
    return m.group(1) if m else tool


def classify(connector, tool):
    """-> (class, status). Allow-list; default blocked. `tool` may be bare or prefixed."""
    b = base_name(tool)
    if b in DRAFT:
        return "draft", "ok"
    if b in CARVEOUT.get(connector, ()):
        return "write", "allowed-carveout"
    if b in CHANNEL_REPLY.get(connector, ()):
        return "write", "channel-reply (main session only)"
    if READ_RE.match(b):
        return "read", "ok"
    return "blocked", "blocked"


def _read(p, limit=400_000):
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def _json(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _adapter_map_tools(heading):
    """Backticked base names in the table rows of the `## {heading}` section of adapter-map.md (rows that are not 'NONE')."""
    text = _read(os.path.join(CLAUDE, "sops", "adapter-map.md"))
    m = re.search(r"^##\s+" + re.escape(heading) + r"[^\n]*\n(.*?)(?=^##\s|\Z)", text, re.S | re.M)
    names = []
    for line in (m.group(1) if m else "").splitlines():
        if not line.startswith("|") or "NONE" in line:
            continue
        for t in re.findall(r"`([a-z][a-z0-9_]+)`", line):
            if t not in names:
                names.append(t)
    return names


def _settings_tools(prefix):
    names = set()
    for p in glob.glob(os.path.join(CLAUDE, "settings*.json")):
        if ".bak" in os.path.basename(p):
            continue
        for t in re.findall(re.escape(prefix) + r"([A-Za-z0-9_]+)", _read(p)):
            names.add(t)
    return names


def _plugin_discord_tools():
    names = set()
    for p in glob.glob(os.path.join(CLAUDE, "plugins", "cache", "*", "discord", "*", "server.ts")):
        txt = _read(p)
        for t in ("reply", "react", "edit_message", "fetch_messages", "download_attachment"):
            if re.search(r"name:\s*['\"]" + t + r"['\"]", txt):
                names.add(t)
    return names


def _denylist_names():
    txt = _read(os.path.join(CLAUDE, "hooks", "safety-gate.sh"))
    m = re.search(r"case \"\$BARE\" in\s*\n(.*?)\)\s*\n\s*echo \"BLOCKED", txt, re.S)
    return set(re.findall(r"[a-z][a-z0-9_]+", m.group(1))) if m else set()


def _tools(cid, sources):
    cfg = CONNECTORS[cid]
    out = {}  # name -> source
    if cfg["map_heading"]:
        for t in _adapter_map_tools(cfg["map_heading"]):
            out[t] = "adapter-map.md"
        sources.append("sops/adapter-map.md")
    for t in sorted(_settings_tools(cfg["prefix"])):
        out.setdefault(t, "settings.json")
    if cid == "discord":
        for t in sorted(_plugin_discord_tools()):
            out.setdefault(t, "plugin:discord server.ts")
        sources.append("plugins/cache/*/discord/*/server.ts")
    deny = _denylist_names()
    for t in KNOWN_UNVERIFIED[cid]:
        out.setdefault(t, "safety-gate denylist" if t in deny else "unverified")
    tools = []
    for name in sorted(out):
        cls, st = classify(cid, name)
        tools.append({"name": name, "class": cls, "status": st, "source": out[name]})
    return tools


def _hooks(cid):
    rx = re.compile(CONNECTORS[cid]["tokens"], re.I)
    hits = {}
    for p in sorted(glob.glob(os.path.join(CLAUDE, "hooks", "*.sh"))):
        n = os.path.basename(p)
        if ".bak" in n or n.endswith(".log"):
            continue
        if rx.search(_read(p, 200_000)):
            hits[n] = "hooks/*.sh token grep"
    st = _json(os.path.join(CLAUDE, "settings.json")) or {}
    for ev, groups in (st.get("hooks") or {}).items():
        for g in groups if isinstance(groups, list) else []:
            if not isinstance(g, dict) or not rx.search(str(g.get("matcher", ""))):
                continue
            for h in g.get("hooks", []):
                m = re.search(r"hooks/([\w.-]+\.(?:sh|py|ps1))", str(h.get("command", "")).replace("\\", "/")) if isinstance(h, dict) else None
                if m and ".bak" not in m.group(1):
                    hits[m.group(1)] = f"settings.json {ev} matcher"
    return [{"name": n, "source": s} for n, s in sorted(hits.items())]


def _skills(cid):
    rx = re.compile(CONNECTORS[cid]["tokens"], re.I)
    out = []
    for p in sorted(glob.glob(os.path.join(CLAUDE, "skills", "*", "SKILL.md"))):
        if rx.search(_read(p, 200_000)):
            out.append({"name": os.path.basename(os.path.dirname(p)), "source": "skills/*/SKILL.md token grep"})
    return out


def _account(cid):
    cfg = _json(IDENTITY_CONFIG) or {}
    ident = cfg.get("identity") or {}
    who = ident.get("name") or "User"
    if cid in ("gmail", "calendar", "drive"):
        mail = ident.get("email")
        return f"{who} ({mail})" if mail else who
    if cid == "discord":
        acc = _json(os.path.join(CLAUDE, "channels", "discord", "access.json")) or {}
        n, g = len(acc.get("allowFrom") or []), len(acc.get("groups") or {})
        return f"{who} DM (allowFrom {n}) + {g} group channel(s)"
    return f"{who} (identity unverified)"


def build():
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    conns = {}
    for cid, cfg in CONNECTORS.items():
        sources = ["identity config", "hooks/*.sh", "settings.json hooks", "skills/*/SKILL.md"]
        tools = _tools(cid, sources)
        if cid == "discord":
            sources.append("channels/discord/access.json (counts only)")
        hooks, skills = _hooks(cid), _skills(cid)
        conns[cid] = {"connector": cid, "server": cfg["prefix"][5:-2], "account": _account(cid), "tools": tools, "hooks": hooks, "skills": skills,
                      "counts": {"tools": len(tools), "hooks": len(hooks), "skills": len(skills)},
                      "generated_at": now, "sources": sorted(set(sources))}
    return {"schema": "workflow-board/inventory/v1", "generated_at": now, "connectors": conns}


def problems(inv):
    """Invariant check shared by --check and tests. Returns a list of strings (empty = ok)."""
    bad = []
    conns = inv.get("connectors") or {}
    if sorted(conns) != sorted(CONNECTORS):
        bad.append(f"connectors {sorted(conns)} != {sorted(CONNECTORS)}")
    rxs = [re.compile(p) for p in SECRET_PATTERNS]
    for cid, c in conns.items():
        for k in ("tools", "hooks", "skills"):
            if c["counts"].get(k) != len(c[k]):
                bad.append(f"{cid}: counts.{k} != len")
        for t in c["tools"]:
            cls, st = classify(cid, t["name"])
            if (t["class"], t["status"]) != (cls, st):
                bad.append(f"{cid}.{t['name']}: stored class/status disagree with allow-list")
            if t["status"] == "ok" and not (READ_RE.match(t["name"]) or t["name"] in DRAFT):
                bad.append(f"{cid}.{t['name']}: ok outside read/draft allow-list")
            if t["name"] in ("send_message", "forward", "share_file") and t["status"] != "blocked":
                bad.append(f"{cid}.{t['name']} not blocked")
            if t["name"] == "reply" and cid != "discord" and t["status"] != "blocked":
                bad.append(f"{cid}.reply not blocked")
    for s in re.findall(r'"((?:[^"\\]|\\.)*)"', json.dumps(inv)):
        if any(rx.search(s) for rx in rxs):
            bad.append(f"secret-like string: {s[:40]!r}")
    return bad


def main(argv):
    inv = build()
    if "--check" in argv:
        bad = problems(inv)
        disk = _json(OUT)
        if disk is None:
            bad.append("inventory/connectors.json missing or unparseable (run without --check)")
        else:
            bad += ["disk: " + b for b in problems(disk)]
        for b in bad:
            print("FAIL", b)
        print("inventory check:", "FAIL" if bad else "ok", {k: v["counts"]["tools"] for k, v in inv["connectors"].items()})
        return 1 if bad else 0
    bad = problems(inv)
    if bad:
        print("refusing to write; problems:", *bad, sep="\n  ")
        return 1
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(inv, f, indent=2)
        f.write("\n")
    print("wrote", OUT, {k: v["counts"] for k, v in inv["connectors"].items()})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
