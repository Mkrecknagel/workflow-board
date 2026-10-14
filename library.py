#!/usr/bin/env python3
"""workflow-board library scanner (stdlib only). Stateless: every call rescans the disk.

scan(roots) -> list of library items (schema: synthesis section 4).
roots: {"claude": dir|None, "codex": dir|None, "kiro": dir|None}; default_roots() gives the real installs.
Never writes anything.
"""
import csv
import glob
import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scanners as S  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
TASK_FILTER = re.compile(r"workflow|nightly|inbox|report|council", re.I)
BUILTIN_TOOLS = ["Agent", "Bash", "Read", "Write", "Edit", "MultiEdit", "Glob", "Grep", "PowerShell",
                 "WebFetch", "WebSearch", "NotebookEdit", "Skill", "ToolSearch", "TaskStop", "Monitor"]


def _inventory_mod():
    """adapters/connectors/inventory.py loaded by path (adapters/ is not a package). None if unavailable."""
    try:
        import importlib.util
        sp = os.path.join(HERE, "adapters", "connectors", "inventory.py")
        spec = importlib.util.spec_from_file_location("wb_connector_inventory", sp)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    except Exception:
        return None


MCP_REF = re.compile(r"mcp__[A-Za-z0-9_-]+?__[A-Za-z0-9_]+")


def module_touches(mod):
    """v4 F-B12: which connector tools a module JSON references -> {"tools":[{tool,connector,status}], "blocked":[...], "connectors":[...]}.
    Sources: any `mcp__server__tool` string anywhere in the module, plus bare names on `tool` cards. Anything that is not on the
    read/draft/carve-out allow-list, including every unknown or unrecognised connector tool, is blocked. Read-only."""
    inv = _inventory_mod()
    if not inv or not isinstance(mod, dict):
        return {"tools": [], "blocked": [], "connectors": []}
    by_prefix = {c["prefix"]: cid for cid, c in inv.CONNECTORS.items()}
    found = {}
    def note(raw):
        cid = None
        for pre, c in by_prefix.items():
            if raw.startswith(pre):
                cid = c
        if cid is None and raw.startswith("mcp__"):
            found[raw] = (None, "blocked")  # unknown connector: unknown tools default to blocked
            return
        if cid is None:
            return
        found[raw] = (cid, inv.classify(cid, raw)[1])
    for m in MCP_REF.findall(json.dumps(mod)):
        note(m)
    for c in mod.get("cards", []) if isinstance(mod.get("cards"), list) else []:
        if isinstance(c, dict) and c.get("type") == "tool":
            n = (c.get("ref") or {}).get("name", "")
            if isinstance(n, str) and n and not n.startswith("mcp__") and n not in BUILTIN_TOOLS:
                cid = next((k for k in inv.CONNECTORS if n in inv.KNOWN_UNVERIFIED.get(k, [])), None)
                found[n] = (cid, inv.classify(cid or "", n)[1])
    tools = [{"tool": t, "connector": c, "status": st} for t, (c, st) in sorted(found.items())]
    return {"tools": tools, "blocked": [t["tool"] for t in tools if t["status"] == "blocked"],
            "connectors": sorted({t["connector"] for t in tools if t["connector"]})}


def default_roots():
    return {"claude": os.path.join(HOME, ".claude"), "codex": os.environ.get("WB_CODEX_DIR") or os.path.join(HOME, ".Codex"),
            "kiro": os.environ.get("WB_KIRO_DIR") or os.path.join(HOME, ".kiro"),
            "agents": os.environ.get("WB_AGENTS_DIR") or os.path.join(HOME, ".agents"), "_mcp_json": os.path.join(HOME, ".claude.json"), "_tasks": True}


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _norm(p):
    return os.path.normpath(p).replace("\\", "/")


def _read(p, limit=400_000):
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def frontmatter(text):
    """Flat YAML-ish frontmatter -> (dict, body). Handles `key: value` and folded `>` / `|` blocks."""
    if not text.startswith("---"):
        return {}, text
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?", text, re.S)
    if not m:
        return {}, text
    fm, key = {}, None
    for line in m.group(1).splitlines():
        km = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if km:
            key = km.group(1)
            v = km.group(2).strip()
            fm[key] = "" if v in (">", "|", ">-", "|-") else v.strip("\"'")
        elif key and line.startswith((" ", "\t")):
            fm[key] = (fm[key] + " " + line.strip()).strip()
    return fm, text[m.end():]


def _triggers(desc):
    return re.findall(r"[\"“]([^\"”]{2,60})[\"”]", desc or "")[:8]


def _item(agent, kind, name, desc, path, installed=True, template=None, extra=None):
    it = {"id": f"lib__{agent}__{kind}__{re.sub(r'[^A-Za-z0-9_.-]+', '-', name)}", "kind": kind, "source_agent": agent,
          "name": name, "description": (desc or "")[:300], "triggers": _triggers(desc), "path": _norm(path) if path else None,
          "installed": installed, "scanned_at": None, "has_proposal": False, "card_template": template}
    if extra:
        it.update(extra)
    return it


def _skill_tpl(name, path):
    return {"type": "skill", "label": name, "execution_mode": "observe", "ref": {"name": name, "path": path},
            "ports": {"in": ["task"], "out": ["result"]}}


def _json(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _scan_claude(root, mcp_json, items):
    if not root or not os.path.isdir(root):
        return
    def stpl(n, p):
        return _skill_tpl(n, "~/.claude/skills/" + n + "/SKILL.md") if p.startswith(_norm(os.path.join(root, "skills"))) else _skill_tpl(n, p)
    for p in sorted(glob.glob(os.path.join(root, "skills", "*", "SKILL.md"))):
        fm, _ = frontmatter(_read(p, 8000))
        n = os.path.basename(os.path.dirname(p))
        items.append(_item("claude", "skill", n, fm.get("description", ""), p, True, _skill_tpl(n, f"~/.claude/skills/{n}/SKILL.md")))
    for p in sorted(glob.glob(os.path.join(root, "plugins", "cache", "**", "SKILL.md"), recursive=True)):
        fm, _ = frontmatter(_read(p, 8000))
        n = fm.get("name") or os.path.basename(os.path.dirname(p))
        items.append(_item("claude", "skill", n, fm.get("description", ""), p, True, _skill_tpl(n, _norm(p)), {"plugin_cache": True}))
    for p in sorted(glob.glob(os.path.join(root, "agents", "*.md"))):
        fm, _ = frontmatter(_read(p, 8000))
        n = fm.get("name") or os.path.splitext(os.path.basename(p))[0]
        items.append(_item("claude", "agent", n, fm.get("description", ""), p, True,
                           {"type": "agent", "label": n, "execution_mode": "observe", "ref": {"route": n, "seat": "claude", "verb": "inject"},
                            "ports": {"in": ["task"], "out": ["result"]}}))
    for p in sorted(glob.glob(os.path.join(root, "commands", "*.md"))):
        fm, _ = frontmatter(_read(p, 8000))
        n = os.path.splitext(os.path.basename(p))[0]
        items.append(_item("claude", "command", n, fm.get("description", ""), p, True,
                           {"type": "step", "label": "/" + n, "execution_mode": "observe", "ref": {"kind": "task"}, "ports": {"in": ["in"], "out": ["out"]}}))
    st = _json(os.path.join(root, "settings.json")) or {}
    seen = set()
    for ev, groups in (st.get("hooks") or {}).items():
        for g in groups if isinstance(groups, list) else []:
            for h in g.get("hooks", []) if isinstance(g, dict) else []:
                cmd = h.get("command", "") if isinstance(h, dict) else ""
                m = re.search(r"([\w./~-]*hooks/[\w.-]+\.(?:sh|py|ps1|js|ts))", cmd.replace("\\", "/"))
                if not m:
                    continue
                f = m.group(1)
                f = f[f.index("hooks/"):]
                key = (ev, f)
                if key in seen:
                    continue
                seen.add(key)
                n = f"{ev}:{os.path.basename(f)}"
                items.append(_item("claude", "hook", n, f"{ev} hook {f} (matcher {g.get('matcher', '*')})", os.path.join(root, f),
                                   os.path.isfile(os.path.join(root, f)),
                                   {"type": "hook", "label": os.path.basename(f), "execution_mode": "observe", "ref": {"event": ev, "file": f},
                                    "ports": {"in": ["event"], "out": ["verdict"]}}))
    ip = _json(os.path.join(root, "plugins", "installed_plugins.json")) or {}
    for pid in sorted((ip.get("plugins") or ip).keys() if isinstance(ip.get("plugins") or ip, dict) else []):
        if pid in ("version",):
            continue
        items.append(_item("claude", "plugin", pid, "installed plugin", os.path.join(root, "plugins", "installed_plugins.json"), True, None))
    mj = _json(mcp_json) if mcp_json else None
    names = set()
    if isinstance(mj, dict):
        names.update((mj.get("mcpServers") or {}).keys())
        for pr in (mj.get("projects") or {}).values():
            if isinstance(pr, dict):
                names.update((pr.get("mcpServers") or {}).keys())
    for n in sorted(names):  # names only, never config values
        items.append(_item("claude", "mcp", n, "MCP server (name only)", mcp_json, True,
                           {"type": "tool", "label": f"mcp__{n}__*", "execution_mode": "observe", "ref": {"name": f"mcp__{n}__"},
                            "ports": {"in": ["call"], "out": ["result"]}}))
    for n in BUILTIN_TOOLS:
        items.append(_item("claude", "mcp", n, "built-in tool", None, True,
                           {"type": "tool", "label": n, "execution_mode": "observe", "ref": {"name": n}, "ports": {"in": ["call"], "out": ["result"]}},
                           {"builtin": True}))


def _scan_codex(root, items):
    if not root or not os.path.isdir(root):
        return
    for p in sorted(glob.glob(os.path.join(root, "skills", "*", "SKILL.md"))):
        fm, _ = frontmatter(_read(p, 8000))
        n = os.path.basename(os.path.dirname(p))
        items.append(_item("codex", "skill", n, fm.get("description", ""), p, True, _skill_tpl(n, f"~/.Codex/skills/{n}/SKILL.md")))
    hj = _json(os.path.join(root, "hooks.json")) or {}
    for ev, groups in (hj.get("hooks") or hj).items():
        if not isinstance(groups, list):
            continue
        items.append(_item("codex", "hook", ev, f"Codex hook event {ev} ({len(groups)} group(s))", os.path.join(root, "hooks.json"), True, None))
    cfg = _read(os.path.join(root, "config.toml"))
    for m in re.finditer(r"^\[(mcp_servers|plugins)\.\"?([^\]\"]+)\"?\]", cfg, re.M):
        kind = "mcp" if m.group(1) == "mcp_servers" else "plugin"
        items.append(_item("codex", kind, m.group(2), f"Codex {kind} (name only)", os.path.join(root, "config.toml"), True, None))
    for p in sorted(glob.glob(os.path.join(root, "agents", "*.toml"))):
        n = os.path.splitext(os.path.basename(p))[0]
        m = re.search(r'^description\s*=\s*"([^"]*)"', _read(p, 4000), re.M)
        items.append(_item("codex", "agent", n, m.group(1) if m else "", p, True, None))


def _scan_kiro(root, items):
    """Kiro install, via the harness-neutral scanners (skills, recursive steering, agent JSON with embedded hooks,
    MCP servers and referenced resources). See scanners.py."""
    col = S.Collector()
    S.scan_kiro(root, col)
    items.extend(col.items)


def _scan_generic(root, items):
    """A plain ~/.agents style root (skills/*/SKILL.md + AGENTS.md), usable by any harness that follows the open layout."""
    col = S.Collector()
    S.scan_generic(root, col)
    items.extend(col.items)


def _scan_modules(items):
    d = os.path.join(HERE, "modules")
    for p in sorted(glob.glob(os.path.join(d, "*.module.json"))):
        m = _json(p)
        if not isinstance(m, dict):
            continue
        n = m.get("name") or os.path.basename(p).split(".")[0]
        items.append(_item("workflow-board", "module", n, m.get("description", ""), p, True,
                           {"type": "module", "label": n, "module": n, "module_version": m.get("version", 1)},
                           {"version": m.get("version", 1), "card_count": len(m.get("cards", [])), "touches": module_touches(m)}))


def _scan_tasks(items):
    try:
        r = subprocess.run(["schtasks", "/query", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=6)
    except (OSError, subprocess.SubprocessError):
        return
    seen = set()
    for row in csv.reader(io.StringIO(r.stdout or "")):
        if not row:
            continue
        n = row[0].lstrip("\\")
        base = n.split("\\")[-1]
        if base in seen or not TASK_FILTER.search(base):
            continue
        seen.add(base)
        items.append(_item("windows", "task", base, f"Scheduled task (read-only); next run {row[1] if len(row) > 1 else '?'}", None, True, None))


def _scan_modules_list():
    items = []
    _scan_modules(items)
    return items


def scan(roots=None):
    roots = roots or default_roots()
    items = []
    _scan_claude(roots.get("claude"), roots.get("_mcp_json"), items)
    _scan_codex(roots.get("codex"), items)
    _scan_kiro(roots.get("kiro"), items)
    _scan_generic(roots.get("agents"), items)
    _scan_modules(items)
    if roots.get("_tasks"):
        _scan_tasks(items)
    ts = _now()
    S.unique_ids(items)
    for it in items:
        it.setdefault("harness", it.get("source_agent"))
        it["scanned_at"] = ts
        if it["kind"] == "skill":
            it["has_proposal"] = os.path.isfile(os.path.join(HERE, "splits", it["name"] + ".proposal.json"))
    return items


def inspect(path):
    """Parse one scanned file. Caller guarantees `path` came from scan()."""
    text = _read(path)
    out = {"path": _norm(path), "frontmatter": {}, "outline": [], "tools_mentioned": [], "raw": text[:200_000], "proposal": None}
    if path.endswith(".json"):
        return out
    fm, body = frontmatter(text)
    out["frontmatter"] = fm
    in_code = False
    for i, line in enumerate(text.splitlines(), 1):
        if line.startswith("```"):
            in_code = not in_code
        m = None if in_code else re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if m:
            out["outline"].append({"level": len(m.group(1)), "title": m.group(2), "line": i})
    tools = set(re.findall(r"\bmcp__[A-Za-z0-9_.-]+", text))
    tools.update(t for t in BUILTIN_TOOLS if re.search(r"\b" + t + r"\b", body))
    out["tools_mentioned"] = sorted(tools)
    name = fm.get("name") or os.path.basename(os.path.dirname(path))
    pp = os.path.join(HERE, "splits", f"{name}.proposal.json")
    if os.path.isfile(pp):
        out["proposal"] = _json(pp)
    return out


if __name__ == "__main__":
    its = scan()
    print(len(its), "items", file=sys.stderr)
