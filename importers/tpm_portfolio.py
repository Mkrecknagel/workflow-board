"""Importer for the TPM Portfolio layout (one adapter of many; see importers/__init__.py for the interface).

Reads, never writes:
  <root>/portfolio.json                      {"projects": [{alias, name, state, parent?, role?}...]}
  <root>/projects/<alias>/project.json       sources by category, children[]
  <root>/projects/<alias>/kb/_index.md       optional front matter: health, next_action, last_compiled
  --sops DIR (optional)                      *.sop.md files. NN-*.sop.md with NN 01-05 maps to the pipeline step of the
                                             same position; other SOPs go on the overview's Supplementary lane.

Boards it produces:
  portfolio            one lane per top-level program; each project card drills into its project board
  p-<alias>            pipeline (Register > Log > Compile > Validate > Report), sources, children, about card,
                       and the skills that declare `sop: SOP-NN` for a pipeline step (typed skill cards)
  sop-<step|name>...   process boards from SOP headings, nested up to 3 levels
  doc-<alias>-<n>...   process boards from a project's local markdown sources (a .md file or a skill folder)
  runtime              the agent that uses those skills, with its hooks and MCP servers as typed cards
"""
import glob
import os
import re

from .common import (COL, ROW, board, card, contract_fields, contract_of, file_sha, frontmatter, jload, local_doc,
                     md_tree, process_boards, prov, slug)

NAME = "tpm-portfolio"
STEPS = ["Register", "Log", "Compile", "Validate", "Report"]
SRC_CATS = ["slack", "sharepoint", "wiki", "taskei", "outlook_folder", "artifacts"]


def detect(root):
    return os.path.isfile(os.path.join(root, "portfolio.json")) and os.path.isdir(os.path.join(root, "projects"))


def _index_meta(path):
    meta = {}
    try:
        text = open(path, encoding="utf-8").read(8000)
    except OSError:
        return meta
    m = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.S)
    for line in (m.group(1).splitlines() if m else []):
        km = re.match(r"^(health|next_action|last_compiled):\s*(.*)$", line)
        if km:
            meta[km.group(1)] = km.group(2).strip().strip("\"'")
    return meta


def _src_label(cat, s):
    if isinstance(s, str):
        return s
    return s.get("label") or s.get("name") or s.get("channel") or s.get("url") or s.get("server_relative_url") or cat


def _sop_number(path):
    m = re.match(r"^(\d+)", os.path.basename(path))
    return int(m.group(1)) if m else None


def _skills_by_sop(items):
    """{sop number: [library skill item]} for skills whose SKILL.md front matter says `sop: SOP-NN` (explicit, not guessed)."""
    out = {}
    for it in items:
        if it.get("kind") != "skill" or not (it.get("path") or "").endswith("SKILL.md"):
            continue
        try:
            fm = frontmatter(open(it["path"], encoding="utf-8", errors="replace").read(4000))
        except OSError:
            continue
        m = re.match(r"^SOP-(\d+)$", fm.get("sop", "").strip(), re.I)
        if m:
            out.setdefault(int(m.group(1)), []).append(it)
    return out


def _typed(it, cid, x, y, group, extra=None):
    """A card built from a library item's own template (skill, hook, agent, tool), with provenance."""
    t = dict(it["card_template"])
    f = dict(t.pop("fields", {}) or {})
    f.update(prov(it["path"], it.get("event") or it["name"]) if it.get("path") else {})
    f.update(extra or {})
    if t.get("type") == "skill" and str(it.get("path", "")).endswith(".md"):
        try:
            text = open(it["path"], encoding="utf-8", errors="replace").read(400_000)
            f.update(contract_fields(*contract_of(md_tree(text), frontmatter(text))))
        except OSError:
            f["contract"] = "missing"
    c = card(cid, t.get("label") or it["name"], None, None, x, y, group, f, ctype=t["type"], ref=dict(t["ref"]),
             ports=t.get("ports"))
    if t.get("type") == "hook":
        c["ports"] = {"in": ["event"], "out": ["verdict"]}
    return c


def _project_board(root, alias, entry, known, extra, skills_for_step, sop_files):
    d = os.path.join(root, "projects", alias)
    pj_path = os.path.join(d, "project.json")
    proj = jload(pj_path) or {}
    meta = _index_meta(os.path.join(d, "kb", "_index.md"))
    cards, edges, sources = [], [], [pj_path]
    for i, st in enumerate(STEPS):
        f = {"step": i + 1}
        sop = sop_files.get(st)
        if sop:
            ins, outs, anchor = contract_of(md_tree(open(sop, encoding="utf-8", errors="replace").read(400_000)),
                                            frontmatter(open(sop, encoding="utf-8", errors="replace").read(4000)))
            f.update(prov(sop, "(whole SOP)"))
            f.update(contract_fields(ins, outs, anchor))
            f["sop"] = os.path.basename(sop)
            sources.append(sop)
        else:  # the five-step pipeline is this importer's convention, so the importer itself is the source
            f.update(prov(__file__, "STEPS"))
            f["contract"] = "missing"
        cards.append(card(f"st-{slug(st)}", st, "step", st, 40 + i * COL, 40, "Workflow", f, ctype="step",
                          ports={"in": [] if i == 0 else ["in"], "out": [] if i == len(STEPS) - 1 else ["next"]}))
        if i:
            edges.append({"from": f"st-{slug(STEPS[i - 1])}.next", "to": f"st-{slug(st)}.in"})
        for j, it in enumerate(skills_for_step.get(st, [])):
            cards.append(_typed(it, f"sk-{slug(st)}-{j}", 40 + i * COL, 40 + ROW + j * 120, "Skills that implement each step",
                                {"implements": st}))
    y = 40 + 2 * ROW + 40 + 120 * max([len(v) for v in skills_for_step.values()] or [0])
    n = 0
    srcs = proj.get("sources") or {}
    for cat in SRC_CATS:
        v = srcs.get(cat)
        for s in (v if isinstance(v, list) else [v] if v else []):
            cid = f"src-{n}"
            url = isinstance(s, dict) and (s.get("url") or s.get("path") or s.get("site_url")) or (s if isinstance(s, str) else "")
            doc = local_doc(url)
            drill = process_boards(f"doc-{slug(alias)}-{n}", doc, extra, NAME) if doc else None
            f = {"category": cat, "url": url}
            f.update(prov(pj_path, f"sources.{cat}[{n}]"))
            cards.append(card(cid, f"{cat}: {_src_label(cat, s)}", "source", cat, 40 + (n % 5) * COL, y + (n // 5) * ROW,
                              "Sources", f, drill=drill, ports={"in": [], "out": ["feeds"]}))
            if cat != "artifacts":
                edges.append({"from": f"{cid}.feeds", "to": "st-compile.in"})
            n += 1
    y += ((n + 4) // 5 or 1) * ROW + 60
    for j, ch in enumerate(proj.get("children") or []):
        f = prov(pj_path, "children")
        cards.append(card(f"ch-{slug(ch)}", known.get(ch, ch), "project", ch, 40 + (j % 5) * COL, y + (j // 5) * ROW, "Children",
                          f, drill=f"p-{slug(ch)}" if ch in known else None))
    f = {"state": entry.get("state"), "role": entry.get("role"), "health": meta.get("health"),
         "next_action": meta.get("next_action"), "last_compiled": meta.get("last_compiled"), "context": proj.get("context")}
    f.update(prov(os.path.join(root, "portfolio.json"), f"projects[{alias}]"))
    cards.append(card("about", entry.get("name", alias), "project", alias, 40 + 5 * COL, 40, "About", f))
    return board(f"p-{slug(alias)}", cards, edges, NAME, sources)


def build(root, opts):
    """-> {board name: board doc}. opts: sops [dirs], include_archived bool, items [library items] (default: library.scan())."""
    items = opts.get("items")
    if items is None:
        import library
        items = library.scan()
    pf_path = os.path.join(root, "portfolio.json")
    pf = jload(pf_path) or {}
    ps = pf.get("projects", pf)
    ps = ps if isinstance(ps, list) else list(ps.values())
    ps = [p for p in ps if isinstance(p, dict) and p.get("alias") and (opts.get("include_archived") or p.get("state") != "ARCHIVED")]
    known = {p["alias"]: p.get("name", p["alias"]) for p in ps}

    sop_files, sops_lane = {}, []
    for d in opts.get("sops") or []:
        for path in sorted(glob.glob(os.path.join(os.path.expanduser(d), "**", "*.sop.md"), recursive=True)):
            nn = _sop_number(path)
            if nn and 1 <= nn <= len(STEPS):
                sop_files.setdefault(STEPS[nn - 1], path)
            else:
                sops_lane.append(path)
    by_sop = _skills_by_sop(items)
    skills_for_step = {st: by_sop.get(i + 1, []) for i, st in enumerate(STEPS)}

    boards = {}
    for p in ps:
        boards[f"p-{slug(p['alias'])}"] = _project_board(root, p["alias"], p, known, boards, skills_for_step, sop_files)

    lanes = {}
    for p in ps:
        top = p
        while top.get("parent") in known:
            top = next(q for q in ps if q["alias"] == top["parent"])
        lanes.setdefault(top.get("name", top["alias"]), []).append(p)
    cards, y = [], 40
    for lane, members in sorted(lanes.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        for i, p in enumerate(sorted(members, key=lambda q: (q.get("parent") is not None, q["alias"]))):
            meta = _index_meta(os.path.join(root, "projects", p["alias"], "kb", "_index.md"))
            f = {"state": p.get("state"), "health": meta.get("health"), "next_action": meta.get("next_action"), "parent": p.get("parent")}
            f.update(prov(pf_path, f"projects[{p['alias']}]"))
            cards.append(card(f"pj-{slug(p['alias'])}", p.get("name", p["alias"]), "project", p["alias"],
                              40 + (i % 6) * COL, y + (i // 6) * ROW, lane, f, drill=f"p-{slug(p['alias'])}"))
        y += ((len(members) + 5) // 6) * ROW + 60
    overview = board("portfolio", cards, [], NAME, [pf_path])
    boards["portfolio"] = overview

    for step, path in sop_files.items():
        sb = process_boards(f"sop-{slug(step)}", path, boards, NAME)
        for name, doc in list(boards.items()):
            if name.startswith("p-") and sb:
                for c in doc["cards"]:
                    if c["id"] == f"st-{slug(step)}":
                        c["drill"] = sb
    oc = overview["cards"]
    y0 = max([c["pos"]["y"] for c in oc] or [0]) + ROW + 60
    for i, path in enumerate(sops_lane):
        sb = process_boards(f"sop-{slug(os.path.basename(path)[:-7])}", path, boards, NAME)
        oc.append(card(f"sop-{i}", os.path.basename(path)[:-7], "sop", os.path.basename(path), 40 + (i % 6) * COL,
                       y0 + (i // 6) * ROW, "Supplementary SOPs", prov(path, "(whole SOP)"), drill=sb))
        overview["generated"]["sources"].append(path)

    # runtime: the agent(s) that use the step skills, with their hooks and MCP servers, all typed from the library scan
    step_skill_paths = {it["path"] for v in skills_for_step.values() for it in v}
    agents = sorted({a for it in items if it.get("path") in step_skill_paths for a in (it.get("used_by") or [])})
    if agents:
        rc, ry = [], 40
        for a in agents:
            ag = next((it for it in items if it["kind"] == "agent" and it["name"] == a and it.get("card_template")), None)
            if not ag:
                continue
            rc.append(_typed(ag, f"ag-{slug(a)}", 40, ry, f"Agent {a}"))
            hooks = [it for it in items if it["kind"] == "hook" and a in (it.get("used_by") or []) and it.get("card_template")
                     and not it["path"].endswith(".json")]
            for j, h in enumerate(hooks):
                rc.append(_typed(h, f"hk-{slug(a)}-{j}", 40 + (j + 1) * COL, ry, f"Agent {a}"))
            mcps = [it for it in items if it["kind"] == "mcp" and a in (it.get("used_by") or []) and it.get("card_template")]
            for j, m in enumerate(mcps):
                rc.append(_typed(m, f"mc-{slug(a)}-{j}", 40 + (j % 6) * COL, ry + ROW + (j // 6) * ROW, f"Agent {a}"))
            ry += ROW * (2 + (len(mcps) + 5) // 6) + 60
        if rc:
            boards["runtime"] = board("runtime", rc, [], NAME, [it["path"] for it in items if it.get("path")
                                                               and any(c.get("fields", {}).get("src_path") == os.path.abspath(it["path"]) for c in rc)])
            oc.append(card("runtime", "Agent runtime (hooks, MCP)", "runtime", "runtime", 40, y0 + ((len(sops_lane) + 5) // 6) * ROW + 60,
                           "Agent runtime", {"agents": ", ".join(agents)}, drill="runtime"))
    return boards
