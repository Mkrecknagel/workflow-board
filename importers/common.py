"""Shared helpers for board importers (stdlib only).

Every card an importer generates carries provenance in its `fields`:
  src_path    absolute path of the file the card was read from
  src_anchor  where in that file (a markdown heading, or a JSON key path)
  src_sha     first 16 hex chars of the sha256 of that file's bytes when the board was generated
so a later step can tell whether the source changed since (deploy.py refuses stale boards).

Station contracts are optional and parsed only from explicit sources: SKILL.md / SOP front matter keys
`inputs:` / `outputs:`, or a markdown section named Inputs / Parameters (inputs) or Outputs / Returns (outputs).
Nothing is inferred from headings. A station with no parsed contract gets fields.contract = "missing".
"""
import hashlib
import json
import os
import re

COL, ROW = 280, 190
LAYOUT_VERSION = 5  # bump when generated positions change; wb_init then stops carrying old positions over


def slug(s):
    return re.sub(r"[^a-z0-9-]+", "-", str(s).lower()).strip("-")[:60] or "x"


def jload(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


_SHA = {}


def file_sha(path):
    """sha256 of a file's bytes, first 16 hex chars. Cached per process. '' if unreadable."""
    if not path:
        return ""
    p = os.path.abspath(path)
    if p not in _SHA:
        try:
            with open(p, "rb") as f:
                _SHA[p] = hashlib.sha256(f.read()).hexdigest()[:16]
        except OSError:
            _SHA[p] = ""
    return _SHA[p]


def prov(path, anchor):
    """Provenance fields for a card read from `path` at `anchor`."""
    return {"src_path": os.path.abspath(path), "src_anchor": str(anchor)[:200], "src_sha": file_sha(path)}


def card(cid, label, kind, name, x, y, group, fields=None, drill=None, ports=None, ctype="resource", ref=None):
    c = {"id": cid, "type": ctype, "label": str(label)[:80], "immutable": False, "enabled": True, "execution_mode": "observe",
         "ref": ref if ref is not None else ({"kind": kind, "name": str(name)} if ctype == "resource" else {"kind": "task"}),
         "ports": ports or {"in": ["in"], "out": ["out"]}, "pos": {"x": x, "y": y}, "group": group}
    f = {k: str(v)[:400] for k, v in (fields or {}).items() if v not in (None, "")}
    if f:
        c["fields"] = f
    if drill:
        c["drill"] = drill
    return c


def board(name, cards, edges, importer=None, sources=None):
    doc = {"schema": "workflow-board/v1", "board": name, "version": 1, "execution_mode": "observe",
           "saved_at": "2026-01-01T00:00:00Z", "integrity_hash": None, "cards": cards, "edges": edges}
    if importer:
        doc["generated"] = {"importer": importer, "sources": sorted(set(sources or [])), "layout": LAYOUT_VERSION}
    return doc


# ---- markdown ----

def frontmatter(text):
    if not text.startswith("---"):
        return {}
    m = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.S)
    out = {}
    for line in (m.group(1).splitlines() if m else []):
        km = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if km:
            out[km.group(1)] = km.group(2).strip().strip("\"'")
    return out


def md_tree(text):
    """Markdown headings -> tree of {title, level, body, children}. Fenced code is ignored."""
    root = {"title": "", "level": 0, "body": [], "children": []}
    stack, fence = [root], False
    for ln in text.splitlines():
        if ln.lstrip().startswith("```"):
            fence = not fence
        m = None if fence else re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", ln)
        if m:
            node = {"title": m.group(2), "level": len(m.group(1)), "body": [], "children": []}
            while stack[-1]["level"] >= node["level"]:
                stack.pop()
            stack[-1]["children"].append(node)
            stack.append(node)
        else:
            stack[-1]["body"].append(ln)
    return root


def summary(node):
    for ln in node["body"]:
        t = ln.strip()
        if t and not t.startswith(("|", "```", ">")) and not re.fullmatch(r"\*\*[^*]+:\*\*", t):
            return re.sub(r"^[-*] +|^\d+\. +|\*\*|`", "", t)[:300]
    return ""


IN_SECTIONS = re.compile(r"^(inputs?|parameters|params|arguments)\b", re.I)
OUT_SECTIONS = re.compile(r"^(outputs?|returns|deliverables?|produces)\b", re.I)


def _bullet_names(node):
    """Names from a section's list items: '- **project** (required): ...' / '- `out`: ...' / '- name — ...'."""
    out = []
    for ln in node["body"]:
        m = (re.match(r"^[-*]\s+(?:\*\*|`)([A-Za-z0-9_.{}/-]+)(?:\*\*|`)", ln)        # - **name** / - `name`
             or re.match(r"^[-*]\s+([a-z0-9_.{}/-]+)\s*(?:\(|:|\u2014| - )", ln))       # - name: / - name (
        if m:
            out.append(m.group(1))
        m = re.match(r"^\s*\|\s*`?([A-Za-z0-9_.{}-]+)`?\s*\|", ln)  # table rows: | name | ...
        if m and m.group(1).lower() not in ("parameter", "name", "input", "output", "field"):
            out.append(m.group(1))
    return out


def contract_of(node, fm=None):
    """(inputs, outputs, anchor) parsed from front matter keys or Inputs/Outputs-style child sections. Empty when absent."""
    fm = fm or {}
    ins = [x.strip() for x in fm.get("inputs", "").strip("[]").split(",") if x.strip()]
    outs = [x.strip() for x in fm.get("outputs", "").strip("[]").split(",") if x.strip()]
    anchor = "front matter" if (ins or outs) else ""
    scope = list(node.get("children", []))
    if len(scope) == 1 and node.get("level", 0) == 0:  # a whole document: look under its title heading
        scope = scope + scope[0]["children"]
    for k in scope:
        t = re.sub(r"^[\d.]+\s*", "", k["title"])
        if not ins and IN_SECTIONS.match(t):
            ins, anchor = _bullet_names(k), k["title"]
        elif not outs and OUT_SECTIONS.match(t):
            outs, anchor = _bullet_names(k), (anchor + " + " if anchor else "") + k["title"]
    return ins, outs, anchor


def contract_fields(ins, outs, anchor):
    if not ins and not outs:
        return {"contract": "missing"}
    f = {"inputs": ", ".join(ins), "outputs": ", ".join(outs), "contract_src": anchor}
    if not ins or not outs:
        f["contract"] = "partial: no " + ("inputs" if not ins else "outputs") + " declared"
    return f


# ---- process boards from a markdown document ----

PROCESS_SECTIONS = re.compile(r"^(steps|procedure|workflow|process|phases|stages)\b", re.I)
NON_STEPS = re.compile(r"^(overview|contents|table of contents|in pm terms|scope|parameters|examples?|troubleshooting|"
                       r"dependencies|references?|see also|faq|notes|changelog|purpose|background)\b", re.I)
MAX_DEPTH = 3


def process_root(tree):
    def find(n):
        for c in n["children"]:
            if PROCESS_SECTIONS.match(c["title"]) and len(c["children"]) >= 2:
                return c
            hit = find(c)
            if hit:
                return hit
        return None
    hit = find(tree)
    if hit:
        return hit
    if len(tree["children"]) == 1 and tree["children"][0]["children"]:
        return tree["children"][0]
    return tree


def process_boards(prefix, path, out, importer, depth=0, node=None):
    """One board per process node with >= 2 sub-steps. Each step card drills into its own board when it has sub-steps.
    Cards carry provenance (path, heading, sha) and a parsed-or-missing contract. Returns the top board name, or None."""
    if node is None:
        try:
            node = process_root(md_tree(open(path, encoding="utf-8", errors="replace").read(400_000)))
        except OSError:
            return None
    kids = node["children"]
    if not PROCESS_SECTIONS.match(node["title"] or ""):
        kids = [k for k in kids if not NON_STEPS.match(re.sub(r"^[\d.]+\s*", "", k["title"]))] or kids
    if len(kids) < 2:
        return None
    name = prefix[:70].rstrip("-")
    cards, edges = [], []
    for i, k in enumerate(kids):
        cid = f"s{i + 1}-{slug(k['title'])[:30]}".rstrip("-")
        child = process_boards(f"{name}-{i + 1}", path, out, importer, depth + 1, k) if depth + 1 < MAX_DEPTH else None
        f = {"summary": summary(k), "substeps": len(k["children"]) or ""}
        f.update(prov(path, k["title"]))
        f.update(contract_fields(*contract_of(k)))
        c = card(cid, k["title"], "task", k["title"], 40 + (i % 4) * COL, 40 + (i // 4) * (ROW + 40),
                 node["title"] or os.path.basename(path), f, drill=child, ctype="step",
                 ports={"in": [] if i == 0 else ["in"], "out": [] if i == len(kids) - 1 else ["next"]})
        c["sop"] = os.path.basename(path)
        cards.append(c)
        if i:
            edges.append({"from": f"{cards[i - 1]['id']}.next", "to": f"{cid}.in"})
    out[name] = board(name, cards, edges, importer, [path])
    return name


def local_doc(ref):
    """A local markdown process: a .md file, or a folder holding SKILL.md or *.sop.md. None for anything else."""
    if not isinstance(ref, str):
        return None
    p = os.path.expanduser(re.sub(r"^file://", "", ref))
    if not os.path.isabs(p):
        return None
    if os.path.isfile(p) and p.endswith(".md"):
        return p
    if os.path.isdir(p):
        for cand in ["SKILL.md"] + sorted(f for f in os.listdir(p) if f.endswith(".sop.md")):
            if os.path.isfile(os.path.join(p, cand)):
                return os.path.join(p, cand)
    return None
