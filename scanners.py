#!/usr/bin/env python3
"""Harness-neutral, read-only install scanners (stdlib only).

Every scanner turns what one harness has installed into the same neutral item record
(schema/item.schema.json). The board only ever sees these records, so adding a harness means
adding a scanner, not touching the canvas or the validator.

Scanners here:
  scan_skill_dir(harness, dir)   any folder of <name>/SKILL.md (the open Agent Skills layout)
  scan_agents_md(harness, path)  an AGENTS.md instructions file
  scan_mcp_config(harness, path) an MCP config with an "mcpServers" object (server NAMES only)
  scan_kiro(root)                a Kiro install: skills/, steering/** (recursive), hooks/*.json,
                                 settings/mcp.json and agents/*.json, including the hooks and MCP
                                 servers declared inside each agent and the file:// and skill://
                                 resources each agent references (so package-managed skills that
                                 live outside the root are found too)
  scan_generic(root)             a ~/.agents style root: skills/*/SKILL.md and AGENTS.md

Rules every scanner follows:
  - Read-only. Nothing is written, executed or imported from the scanned folders.
  - Names and descriptions only. MCP env/args/headers and hook command lines are never copied
    into a record (they can hold tokens); a hook record names only its script file.
  - Bounded. Each referenced glob is capped (MAX_GLOB) and files are read with a size limit.
"""
import glob
import hashlib
import json
import os
import re

MAX_GLOB = 200
MAX_READ = 64_000


def _norm(p):
    return os.path.normpath(p).replace("\\", "/")


def _read(p, limit=MAX_READ):
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


def frontmatter(text):
    """Flat YAML-ish frontmatter -> dict. Handles `key: value` and folded `>` / `|` blocks."""
    if not text.startswith("---"):
        return {}
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?", text, re.S)
    if not m:
        return {}
    fm, key = {}, None
    for line in m.group(1).splitlines():
        km = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if km:
            key = km.group(1)
            v = km.group(2).strip()
            fm[key] = "" if v in (">", "|", ">-", "|-") else v.strip("\"'")
        elif key and line.startswith((" ", "\t")):
            fm[key] = (fm[key] + " " + line.strip()).strip()
    return fm


def _first_heading(text):
    m = re.search(r"^#\s+(.+?)\s*$", text, re.M)
    return m.group(1) if m else ""


def _slug(s):
    s = re.sub(r"[^a-z0-9-]+", "-", str(s).lower()).strip("-")
    return s or "item"


def _triggers(desc):
    return re.findall(r"[\"“]([^\"”]{2,60})[\"”]", desc or "")[:8]


def item(harness, kind, name, desc, path, template=None, subkind=None, extra=None):
    """One neutral item record. `source_agent` is kept as an alias of `harness` for older canvas code."""
    it = {"id": f"lib__{harness}__{kind}__{re.sub(r'[^A-Za-z0-9_.-]+', '-', name)}", "kind": kind,
          "harness": harness, "source_agent": harness, "name": name, "description": (desc or "")[:300],
          "triggers": _triggers(desc), "path": _norm(path) if path else None, "installed": True,
          "scanned_at": None, "has_proposal": False, "card_template": template}
    if subkind:
        it["subkind"] = subkind
    if extra:
        it.update(extra)
    return it


def skill_template(name, path, harness):
    return {"type": "skill", "label": name, "execution_mode": "observe", "ref": {"name": name, "path": _norm(path)},
            "ports": {"in": ["task"], "out": ["result"]}, "fields": {"harness": harness}}


def _md_item(harness, path, subkind, name=None):
    """A markdown file (SKILL.md, steering file, AGENTS.md) as a skill-kind item."""
    text = _read(path)
    fm = frontmatter(text)
    if name is None:
        base = os.path.basename(path)
        name = os.path.basename(os.path.dirname(path)) if base == "SKILL.md" else os.path.splitext(base)[0]
        name = fm.get("name") or name
    name = _slug(name)
    desc = fm.get("description") or _first_heading(text) or f"{harness} {subkind}"
    return item(harness, "skill", name, desc, path, skill_template(name, path, harness), subkind)


class Collector:
    """Accumulates items, de-duplicating by (kind, path) and recording which agents use each item."""

    def __init__(self):
        self.items, self._by_key = [], {}

    def add(self, it, used_by=None):
        if it["kind"] == "mcp":  # one record per server name; the agents that declare it go into used_by
            key = (it["kind"], it["name"])
        elif it["kind"] == "hook":
            key = (it["kind"], it.get("path"), it["name"])
        else:
            key = (it["kind"], it.get("path") or it["name"])
        cur = self._by_key.get(key)
        if cur is None:
            self._by_key[key] = cur = it
            self.items.append(it)
        if used_by:
            ub = cur.setdefault("used_by", [])
            if used_by not in ub:
                ub.append(used_by)
        return cur


# ---- generic, format-level scanners ----

def scan_skill_dir(harness, d, col, subkind="skill"):
    if not d or not os.path.isdir(d):
        return
    for p in sorted(glob.glob(os.path.join(d, "*", "SKILL.md")))[:MAX_GLOB * 5]:
        col.add(_md_item(harness, p, subkind))


def scan_agents_md(harness, path, col):
    if path and os.path.isfile(path):
        col.add(_md_item(harness, path, "instructions", name=f"agents-md-{os.path.basename(os.path.dirname(os.path.abspath(path)))}"))


def mcp_item(harness, name, path):
    n = str(name)
    return item(harness, "mcp", n, "MCP server (name only)", path,
                {"type": "tool", "label": f"@{n}", "execution_mode": "observe", "ref": {"name": f"@{n}"},
                 "ports": {"in": ["call"], "out": ["result"]}, "fields": {"harness": harness}})


def scan_mcp_config(harness, path, col, used_by=None):
    d = _json(path) if path else None
    servers = d.get("mcpServers") if isinstance(d, dict) else None
    for n in sorted(servers or {}):  # names only: env, args and headers are never read into a record
        col.add(mcp_item(harness, n, path), used_by)


def scan_generic(root, col, harness="agents"):
    """A plain ~/.agents style root: skills/*/SKILL.md plus an AGENTS.md at the root."""
    if not root or not os.path.isdir(root):
        return
    scan_skill_dir(harness, os.path.join(root, "skills"), col)
    scan_agents_md(harness, os.path.join(root, "AGENTS.md"), col)


# ---- Kiro ----

_SCRIPT = re.compile(r"((?:~|/)[^\s'\"|;&]+|[\w.-]+\.(?:sh|py|js|ts|ps1))")


def _hook_file(command, agent_path):
    """The script a hook command runs, or the agent file when the command is not a script path. Never the full command."""
    for tok in _SCRIPT.findall(command or ""):
        p = os.path.expanduser(tok)
        if os.path.isfile(p):
            return _norm(p)
    return _norm(agent_path)


def _resource_paths(ref):
    """file:// and skill:// resource URIs -> absolute paths. Relative file:// refs resolve against the
    workspace the agent runs in, which a global scan cannot know, so they are skipped."""
    if not isinstance(ref, str):
        return []
    m = re.match(r"^(file|skill)://(.+)$", ref)
    if not m:
        return []
    p = os.path.expanduser(m.group(2))
    if not os.path.isabs(p):
        return []
    if any(ch in p for ch in "*?["):
        return sorted(glob.glob(p, recursive=True))[:MAX_GLOB]
    return [p] if os.path.isfile(p) else []


def scan_kiro(root, col):
    if not root or not os.path.isdir(root):
        return
    h = "kiro"
    scan_skill_dir(h, os.path.join(root, "skills"), col)
    for p in sorted(glob.glob(os.path.join(root, "steering", "**", "*.md"), recursive=True))[:MAX_GLOB]:
        col.add(_md_item(h, p, "steering"))
    for p in sorted(glob.glob(os.path.join(root, "hooks", "*.json"))):  # IDE-style hook files
        n = os.path.splitext(os.path.basename(p))[0]
        col.add(item(h, "hook", n, "Kiro hook file", p,
                     {"type": "hook", "label": n, "execution_mode": "observe", "ref": {"event": "file", "file": _norm(p)},
                      "ports": {"in": ["event"], "out": ["verdict"]}, "fields": {"harness": h}}))
    scan_mcp_config(h, os.path.join(root, "settings", "mcp.json"), col)
    for ap in sorted(glob.glob(os.path.join(root, "agents", "*.json"))):
        a = _json(ap)
        if not isinstance(a, dict):
            continue
        an = str(a.get("name") or os.path.splitext(os.path.basename(ap))[0])
        col.add(item(h, "agent", an, a.get("description", ""), ap,
                     {"type": "agent", "label": an, "execution_mode": "observe",
                      "ref": {"route": an, "seat": h, "verb": "inject"}, "ports": {"in": ["task"], "out": ["result"]},
                      "fields": {"harness": h}}))
        for ev, hooks in sorted((a.get("hooks") or {}).items()):
            for hk in hooks if isinstance(hooks, list) else []:
                cmd = hk.get("command", "") if isinstance(hk, dict) else ""
                f = _hook_file(cmd, ap)
                n = f"{an}:{ev}:{os.path.basename(f)}"
                col.add(item(h, "hook", n, f"{ev} hook of agent {an}", f,
                             {"type": "hook", "label": f"{ev}: {os.path.basename(f)}", "execution_mode": "observe",
                              "ref": {"event": ev, "file": f, "agent": an}, "ports": {"in": ["event"], "out": ["verdict"]},
                              "fields": {"harness": h}}, extra={"event": ev}), an)
        for n in sorted(a.get("mcpServers") or {}):
            col.add(mcp_item(h, n, ap), an)
        for ref in a.get("resources") or []:
            for p in _resource_paths(ref):
                if p.endswith("SKILL.md"):
                    col.add(_md_item(h, p, "skill"), an)
                elif p.endswith(".md"):
                    col.add(_md_item(h, p, "steering"), an)


def unique_ids(items):
    """Make ids unique: a second item with the same id gets a short path hash suffix."""
    seen = set()
    for it in items:
        if it["id"] in seen:
            it["id"] += "~" + hashlib.sha1((it.get("path") or it["name"]).encode()).hexdigest()[:6]
        seen.add(it["id"])
    return items
