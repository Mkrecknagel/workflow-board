"""Importer for the TPM Portfolio layout (one adapter of many; see importers/__init__.py for the interface).

Reads, never writes:
  <root>/portfolio.json                      {"projects": [{alias, name, state, parent?, role?}...]}
  <root>/projects/<alias>/project.json       sources by category (inputs), sources.artifacts (outputs), children[],
                                             workflow_skills[] (skills the project's workflow uses)
  <root>/projects/<alias>/kb/*.md            the knowledge base (an output of Compile)
  <root>/reports/**/*.md                     generated reports (front matter `project:` or the alias in the file name)
  <root>/activity/*.md                       skills_used per project, for SUGGESTED workflow skills (never auto-registered)
  --sops DIR (optional)                      *.sop.md; NN-*.sop.md with NN 01-05 is pipeline step NN

Every project board has the same lanes, so pages are predictable:
  Status (banner) / Inputs | TPM agent workflow + Workflow skills | Outputs / Children
Stations: one card per pipeline step, typed by what runs it (explicit links only):
  skill  a SKILL.md whose front matter says `sop: SOP-NN`
  hook   a hook of the pipeline agent whose script file is named in the SOP text
  step   nothing runs it yet (grey)
A step whose SOP says "Hands off to SOP-B" and whose skill names B's skill becomes a gate at the end of that step's
board (TPM: Validate closes Compile). Flags: duplicate skills for one SOP, drift (broken SOP pointer in the skill, or
skill and SOP declare different contracts), and a split hint for SOPs with more than SPLIT_HINT steps.
"""
import glob
import os
import re

from .common import (COL, ROW, board, card, contract_fields, contract_of, frontmatter, jload, local_doc, md_tree,
                     process_boards, prov, slug)

NAME = "tpm-portfolio"
STEPS = ["Register", "Log", "Compile", "Validate", "Report"]
INPUT_CATS = ["slack", "sharepoint", "wiki", "taskei", "harmony", "outlook_folder"]
KB_PAGES = ["_index", "who", "what", "why", "when", "where", "how", "impact"]
SPLIT_HINT = 7
LANES = {"status": "Status", "in": "Inputs", "tpm": "TPM agent workflow", "uses": "Workflow skills",
         "out": "Outputs", "kids": "Children"}


def _projects_dir(root):
    """The projects folder: <root>/config.json paths.projects when present, else <root>/projects."""
    sub = "projects"
    cfg = jload(os.path.join(root, "config.json"))
    if isinstance(cfg, dict) and isinstance(cfg.get("paths"), dict):
        v = cfg["paths"].get("projects")
        if isinstance(v, str) and v and not os.path.isabs(v) and ".." not in v.replace("\\", "/").split("/"):
            sub = v
    return os.path.join(root, sub)


def _safe_alias(alias):
    """An alias reduced to one safe path segment (no separators, no '..'), case kept."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(alias)).strip(".-") or "x"


def project_dir(root, alias):
    """<projects>/<alias>, or the nearest folder named <alias> that holds a project.json (max depth 3); the default path if none."""
    pdir, seg = _projects_dir(root), _safe_alias(alias)
    direct = os.path.join(pdir, seg)
    if os.path.isfile(os.path.join(direct, "project.json")):
        return direct
    for dp, dns, fns in os.walk(pdir):
        depth = os.path.relpath(dp, pdir).count(os.sep) + 1 if dp != pdir else 0
        if depth >= 3:
            dns[:] = []
        dns[:] = [x for x in dns if not x.startswith(".") and x not in ("kb", "0-kb", "node_modules")]
        if os.path.basename(dp) == seg and "project.json" in fns:
            return dp
    return direct


def kb_dir(d):
    for kb in ("kb", "0-kb"):
        if os.path.isdir(os.path.join(d, kb)):
            return os.path.join(d, kb)
    return os.path.join(d, "kb")


def detect(root):
    return os.path.isfile(os.path.join(root, "portfolio.json")) and os.path.isdir(_projects_dir(root))


def _read(p, n=400_000):
    try:
        return open(p, encoding="utf-8", errors="replace").read(n)
    except OSError:
        return ""


def _index_meta(path):
    fm = frontmatter(_read(path, 8000))
    return {k: fm.get(k) for k in ("health", "next_action", "last_compiled") if fm.get(k)}


def _src_label(cat, s):
    if isinstance(s, str):
        return s
    return s.get("label") or s.get("name") or s.get("channel") or s.get("url") or s.get("server_relative_url") or cat


def _entries(v):
    """(index or None, entry) for a list- or scalar-valued source category."""
    if isinstance(v, list):
        return list(enumerate(v))
    return [(None, v)] if v else []


def _key(cat, i):
    return f"sources.{cat}" + (f"[{i}]" if i is not None else "")


def _sop_number(path):
    m = re.match(r"^(\d+)", os.path.basename(path))
    return int(m.group(1)) if m else None


def _typed(it, cid, x, y, group, extra=None, label=None):
    """A card from a library item's own template (skill, hook, agent, tool), with provenance."""
    t = dict(it["card_template"])
    f = dict(t.pop("fields", {}) or {})
    if it.get("path"):
        f.update(prov(it["path"], it.get("event") or it["name"]))
    f.update(extra or {})
    c = card(cid, label or t.get("label") or it["name"], None, None, x, y, group, f, ctype=t["type"], ref=dict(t["ref"]),
             ports=t.get("ports"))
    if t.get("type") == "hook":
        c["ports"] = {"in": ["event"], "out": ["verdict"]}
    return c


# ---- stations ----

SOP_PTR = re.compile(r"(?<![\w.-])((?:\.{1,2}/)*(?:[\w.-]+/)*?agent-sops/[A-Za-z0-9_./-]+\.md)")


def _skill_drift(skill_path):
    """Broken agent-sops/... pointers cited in a skill (resolved like the TPM release gate)."""
    pkg = os.path.dirname(os.path.dirname(os.path.dirname(skill_path)))
    if not os.path.isdir(os.path.join(pkg, "agent-sops")):
        return []
    bad = []
    for ref in sorted(set(SOP_PTR.findall(_read(skill_path)))):
        tgt = os.path.join(os.path.dirname(skill_path), ref) if ref.startswith(("./", "../")) else os.path.join(pkg, ref)
        if not os.path.exists(tgt):
            bad.append(ref)
    return bad


def resolve_stations(items, sop_files, boards):
    """-> (stations [dict], gates {host step: dict}, agent name or None). Builds the SOP process boards as a side effect."""
    by_sop = {}
    for it in items:
        if it.get("kind") == "skill" and str(it.get("path", "")).endswith("SKILL.md"):
            m = re.match(r"^SOP-(\d+)$", frontmatter(_read(it["path"], 4000)).get("sop", "").strip(), re.I)
            if m:
                by_sop.setdefault(int(m.group(1)), []).append(it)
    agents = sorted({a for v in by_sop.values() for it in v for a in (it.get("used_by") or [])})
    agent = agents[0] if agents else None
    agent_hooks = [it for it in items if it["kind"] == "hook" and agent in (it.get("used_by") or []) and it.get("card_template")
                   and not str(it.get("path", "")).endswith(".json")]
    st = []
    for i, step in enumerate(STEPS):
        sop = sop_files.get(step)
        sop_text = _read(sop) if sop else ""
        skills = sorted(by_sop.get(i + 1, []), key=lambda it: it["name"])
        s = {"step": step, "n": i + 1, "sop": sop, "sop_text": sop_text, "skills": skills, "kind": "step", "item": None,
             "flags": {}, "board": process_boards(f"sop-{slug(step)}", sop, boards, NAME) if sop else None}
        if skills:
            s["kind"], s["item"] = "skill", skills[0]
        else:
            hooks = [h for h in agent_hooks if os.path.basename(h["path"]) in sop_text]
            if hooks:
                s["kind"], s["item"] = "hook", hooks[0]
        if len(skills) > 1:
            s["flags"]["duplicate_skills"] = ", ".join(it["name"] for it in skills) + " all declare SOP-%02d; keep one" % (i + 1)
        sop_c = contract_of(md_tree(sop_text), frontmatter(sop_text[:4000])) if sop else ([], [], "")
        sk_c = ([], [], "")
        if s["kind"] == "skill":
            t = _read(s["item"]["path"])
            sk_c = contract_of(md_tree(t), frontmatter(t[:4000]))
            drift = _skill_drift(s["item"]["path"])
            if drift:
                s["flags"]["drift"] = "skill cites missing SOP path(s): " + ", ".join(drift)
            if (sk_c[0] or sk_c[1]) and (sop_c[0] or sop_c[1]) and (set(sk_c[0]) != set(sop_c[0]) or set(sk_c[1]) != set(sop_c[1])):
                s["flags"]["drift"] = (s["flags"].get("drift", "") + "; " if s["flags"].get("drift") else "") + \
                    "skill and SOP declare different inputs/outputs"
        s["contract"] = sk_c if (sk_c[0] or sk_c[1]) else sop_c
        if s["board"] and len(boards[s["board"]]["cards"]) > SPLIT_HINT:
            s["flags"]["hint"] = f"{len(boards[s['board']]['cards'])} steps in one SOP: consider splitting into smaller skills and hooks"
        st.append(s)
    # a step handed off to and called by another step's skill becomes a gate that closes that step
    gates = {}
    for s in st:
        for m in re.finditer(r"Hands off to SOP-(\d+)", s["sop_text"]):
            tgt = next((x for x in st if x["n"] == int(m.group(1))), None)
            if tgt and s["kind"] == "skill" and tgt["kind"] == "skill" and tgt["item"]["name"] in _read(s["item"]["path"]):
                gates[s["step"]] = tgt
    gated = {g["step"] for g in gates.values()}
    for host, g in gates.items():
        hb = next((x["board"] for x in st if x["step"] == host), None)
        if hb:
            b = boards[hb]
            last = b["cards"][-1]
            last["ports"]["out"] = ["next"]
            f = {"implements": g["step"], "skill": g["item"]["name"], "summary": f"Runs {g['item']['name']} (SOP-{g['n']:02d}) "
                 f"before {host} counts as done."}
            f.update(prov(g["item"]["path"], g["item"]["name"]))
            gc = card(f"gate-{slug(g['step'])}", f"{g['step']} gate ({g['item']['name']})", None, None,
                      last["pos"]["x"] + COL, last["pos"]["y"], last.get("group") or "", f, drill=g["board"], ctype="gate",
                      ref={"kind": "rule", "rule": f"{g['step']} (SOP-{g['n']:02d}) passes before {host} is done", "verb": "deny"},
                      ports={"in": ["in"], "out": []})
            b["cards"].append(gc)
            b["edges"].append({"from": f"{last['id']}.next", "to": f"{gc['id']}.in"})
    return [s for s in st if s["step"] not in gated], gates, agent


def station_card(s, x, y, group, first, last):
    f = {"step": s["n"]}
    if s["sop"]:
        f.update(prov(s["sop"], "(whole SOP)"))
        f["sop"] = os.path.basename(s["sop"])
    else:
        f.update(prov(__file__, "STEPS"))
    f.update(contract_fields(*s["contract"]))
    f.update(s["flags"])
    ports = {"in": ["in"], "out": ["out"] if last else ["next"]}
    cid = f"st-{slug(s['step'])}"
    if s["kind"] in ("skill", "hook"):
        c = _typed(s["item"], cid, x, y, group, f, label=f"{s['step']} \u00b7 {s['item']['name'] if s['kind'] == 'skill' else os.path.basename(s['item']['path'])}")
        c["fields"].update(f)  # station fields win over the item's own provenance for src_* (the SOP is the station's source)
        if s["kind"] == "skill":
            c["fields"]["skill_path"] = s["item"]["path"]
        c["ports"] = ports if s["kind"] == "skill" else {"in": ["event", "in"], "out": ports["out"]}
    else:
        c = card(cid, s["step"], "step", s["step"], x, y, group, f, ctype="step", ports=ports)
    if s["board"]:
        c["drill"] = s["board"]
    return c


# ---- per-project parts ----

def kb_board(root, alias):
    d = kb_dir(project_dir(root, alias))
    cards = []
    for i, page in enumerate(KB_PAGES):
        p = os.path.join(d, page + ".md")
        if not os.path.isfile(p):
            continue
        text = _read(p, 40_000)
        fm = frontmatter(text)
        body = re.sub(r"^---.*?---\s*", "", text, flags=re.S)
        first = next((ln.strip("# ").strip() for ln in body.splitlines() if ln.strip() and not ln.startswith(("#", "|", "---"))), "")
        f = {"summary": first[:300], "last_compiled": fm.get("last_compiled"), "open": f"/kb/{alias}/{page}", "authority": "derived"}
        f.update(prov(p, page))
        cards.append(card(f"kb-{slug(page)}", page.lstrip("_") or "index", None, None, 40 + (i % 4) * COL, 40 + (i // 4) * ROW,
                          "Knowledge base pages", f, ctype="datastore", ref={"kind": "file", "path": p}, ports={"in": ["write"], "out": ["read"]}))
    return board(f"kb-{slug(alias)}", cards, [], NAME, [c["fields"]["src_path"] for c in cards]) if cards else None


def reports_by_project(root, aliases):
    """{alias: [(path, how)]}: front matter `project:` wins; else the longest alias found as a token in the file name."""
    out = {}
    longest = sorted(aliases, key=len, reverse=True)
    for p in sorted(glob.glob(os.path.join(root, "reports", "**", "*.md"), recursive=True)):
        fm = frontmatter(_read(p, 3000))
        a, how = fm.get("project"), "front matter"
        if a not in aliases:
            base = os.path.basename(p)[:-3].lower()
            a = next((x for x in longest if re.search(rf"(^|[-_]){re.escape(x)}([-_.]|$)", base)), None)
            how = "file name"
        if a:
            out.setdefault(a, []).append((p, how))
    return out


def activity_skills(root):
    """{alias: {skill: count}} from skills_used in activity entries."""
    out = {}
    for p in sorted(glob.glob(os.path.join(root, "activity", "*.md"))):
        text = _read(p, 2_000_000)
        for m in re.finditer(r'^alias:\s*"?([a-z0-9-]+)"?\s*$(.*?)^skills_used:\s*\[(.*?)\]', text, re.M | re.S):
            if "\n---" in m.group(2):
                continue  # the two keys belong to different entries
            for s in re.findall(r'"([^"]+)"', m.group(3)):
                out.setdefault(m.group(1), {})
                out[m.group(1)][s] = out[m.group(1)].get(s, 0) + 1
    return out


def _skill_path(ref):
    p = local_doc(ref) if isinstance(ref, str) else None
    return p if p and p.endswith("SKILL.md") else None


def project_board(root, alias, entry, known, boards, stations, agent_item, expanded, reports, act, items, pipeline_skill_paths):
    d = project_dir(root, alias)
    kbd = kb_dir(d)
    pj_path = os.path.join(d, "project.json")
    proj = jload(pj_path) or {}
    meta = _index_meta(os.path.join(kbd, "_index.md"))
    cards, edges, sources = [], [], [pj_path]
    XI, XW = 40, 40 + COL + 80
    XO = XW + (len(stations) + 1) * COL + 80
    Y0, DY = 230, 150
    compile_port = next((("next" if s is not stations[-1] else "out") for s in stations if s["step"] == "Compile"), None)

    def pjf(key, role, **kw):
        f = {"role": role, "pj_path": pj_path, "pj_key": key}
        f.update(prov(pj_path, key))
        f.update({k: v for k, v in kw.items() if v})
        return f

    # status banner (a note: never a station, never part of the behaviour hash)
    health = {"on_track": "\u2705 on track", "at_risk": "\u26a0\ufe0f at risk", "blocked": "\U0001f6a8 blocked"}.get(meta.get("health", ""), "\u26aa no data")
    banner = " \u00b7 ".join(x for x in [f"{entry.get('name', alias)}", f"Health: {health}", f"State: {entry.get('state', '?')}",
                                       f"Next: {meta['next_action']}" if meta.get("next_action") else "",
                                       f"Last compiled: {meta['last_compiled'][:10]}" if meta.get("last_compiled") else ""] if x)
    cards.append({"id": "status", "type": "note", "label": "Status", "immutable": False, "enabled": True, "execution_mode": "observe",
                  "ref": {"kind": "note"}, "ports": {"in": [], "out": []}, "pos": {"x": XI, "y": 20}, "group": LANES["status"],
                  "size": {"w": XO + COL - XI, "h": 70}, "fields": {"text": banner, "banner": "true",
                             **{k: v for k, v in (("health", meta.get("health")), ("next_action", meta.get("next_action")),
                                                  ("last_compiled", meta.get("last_compiled")), ("state", entry.get("state")),
                                                  ("context", proj.get("context"))) if v},
                             **(prov(os.path.join(kbd, "_index.md"), "front matter")
                                                if os.path.isfile(os.path.join(kbd, "_index.md"))
                                                else prov(os.path.join(root, "portfolio.json"), f"projects[{alias}]"))}})

    # inputs
    srcs = proj.get("sources") or {}
    n = 0
    for cat in INPUT_CATS:
        for i, s in _entries(srcs.get(cat)):
            url = isinstance(s, dict) and (s.get("url") or s.get("path") or s.get("site_url")) or (s if isinstance(s, str) else "")
            doc = local_doc(url)
            drill = process_boards(f"doc-{slug(alias)}-in{n}", doc, boards, NAME) if doc else None
            cards.append(card(f"in-{n}", f"{cat}: {_src_label(cat, s)}", "source", cat, XI, Y0 + n * DY, LANES["in"],
                              pjf(_key(cat, i), "input", category=cat, url=url), drill=drill, ports={"in": [], "out": ["feeds"]}))
            if compile_port:
                edges.append({"from": f"in-{n}.feeds", "to": "st-compile.in"})
            n += 1
    if not n:
        cards.append(card("in-none", "No inputs registered", "none", "none", XI, Y0, LANES["in"], {"role": "empty", **prov(pj_path, "(none registered)")}))

    # TPM agent workflow: agent + one card per station
    if agent_item:
        cards.append(_typed(agent_item, "agent", XW, Y0, LANES["tpm"], {"runs": "the TPM pipeline for this project"}, label=f"Agent \u00b7 {agent_item['name']}"))
        cards[-1]["drill"] = "runtime" if "runtime" in boards else None
        if not cards[-1]["drill"]:
            del cards[-1]["drill"]
    for j, s in enumerate(stations):
        cards.append(station_card(s, XW + (j + 1) * COL, Y0, LANES["tpm"], j == 0, j == len(stations) - 1))
        if j:
            edges.append({"from": f"st-{slug(stations[j - 1]['step'])}.next", "to": f"st-{slug(s['step'])}.in"})
    if agent_item and stations:
        edges.append({"from": "agent.result", "to": f"st-{slug(stations[0]['step'])}.in"})

    # workflow skills: registered (workflow_skills[]) and suggested (activity logs; dashed, never auto-registered)
    yw, k = Y0 + ROW + 70, 0
    registered = set()
    for i, ws in enumerate(proj.get("workflow_skills") or []):
        p = _skill_path(ws.get("path") or ws.get("url")) if isinstance(ws, dict) else None
        name = (ws.get("name") if isinstance(ws, dict) else str(ws)) or (os.path.basename(os.path.dirname(p)) if p else "skill")
        registered.add(name)
        f = pjf(f"workflow_skills[{i}]", "uses", skill_path=p)
        if p:
            t = _read(p)
            f.update(contract_fields(*contract_of(md_tree(t), frontmatter(t[:4000]))))
        c = card(f"uses-{k}", name, None, None, XW + k * COL, yw, LANES["uses"], f, ctype="skill",
                 ref={"name": name, **({"path": p} if p else {})}, ports={"in": ["task"], "out": ["result"]},
                 drill=process_boards(f"doc-{slug(alias)}-uses{k}", p, boards, NAME) if p else None)
        cards.append(c)
        k += 1
    art_paths = {(_skill_path(a.get("url") or a.get("path")) if isinstance(a, dict) else None) for _, a in _entries(srcs.get("artifacts"))}
    lib = {it["name"]: it for it in items if it.get("kind") == "skill" and str(it.get("path", "")).endswith("SKILL.md")}
    for name, cnt in sorted((act.get(alias) or {}).items(), key=lambda kv: -kv[1]):
        it = lib.get(name)
        if (not it or name in registered or it["path"] in pipeline_skill_paths or it["path"] in art_paths
                or (agent_item and agent_item["name"] in (it.get("used_by") or []))):  # the agent's own toolkit is not this project's
            continue
        if any(s.get("item") and s["item"].get("path") == it["path"] for s in stations):
            continue
        f = {"role": "suggested", "suggested": f"used {cnt}x in this project's activity log; not registered",
             "pj_path": pj_path, "pj_key": "new", "skill_name": name, "skill_path": it["path"]}
        f.update(prov(it["path"], name))
        cards.append(card(f"sugg-{slug(name)}", name, None, None, XW + k * COL, yw, LANES["uses"], f, ctype="skill",
                          ref={"name": name, "path": it["path"]}, ports={"in": ["task"], "out": ["result"]}))
        k += 1
    if not k:
        cards.append(card("uses-none", "No workflow skills yet", "none", "none", XW, yw, LANES["uses"], {"role": "empty", **prov(pj_path, "(none registered)")}))

    # outputs: knowledge base, reports, artifacts (a skill folder is a BUILT skill)
    yo, m = Y0, 0
    kb = kb_board(root, alias)  # the KB card is taller (datastore), so the outputs after it start one ROW down
    if kb:
        boards[kb["board"]] = kb
        f = {"health": meta.get("health"), "last_compiled": meta.get("last_compiled"), "pages": len(kb["cards"]), "authority": "derived"}
        f.update(prov(os.path.join(kbd, "_index.md"), "kb/"))
        cards.append(card("out-kb", "Knowledge base", None, None, XO, yo, LANES["out"], f, drill=kb["board"], ctype="datastore",
                          ref={"kind": "file", "path": kbd}, ports={"in": ["write"], "out": ["read"]}))
        if compile_port:
            edges.append({"from": f"st-compile.{compile_port}", "to": "out-kb.write"})
        m += 1
    for p, how in reports.get(alias, []):
        rel = os.path.relpath(p, os.path.join(root, "reports"))
        f = {"role": "report", "open": f"/report?p={rel}", "matched_by": how}
        f.update(prov(p, "(report)"))
        cards.append(card(f"out-rep-{m}", os.path.basename(p)[:-3], "report", rel, XO, yo + m * DY + (ROW - DY if kb and m else 0), LANES["out"], f,
                          ports={"in": ["in"], "out": []}))
        m += 1
    for i, a in _entries(srcs.get("artifacts")):
        if isinstance(a, dict) and a.get("type") == "workflow-board":
            continue
        url = isinstance(a, dict) and (a.get("url") or a.get("path")) or (a if isinstance(a, str) else "")
        sp = _skill_path(url)
        built = bool(sp)  # a built skill needs its SKILL.md on disk to be shown as a skill; else it is a plain artifact
        f = pjf(_key("artifacts", i), "built" if built else "output", url=url, skill_path=sp)
        if built:
            c = card(f"out-{m}", f"built: {_src_label('artifacts', a)}", None, None, XO, yo + m * DY + (ROW - DY if kb and m else 0), LANES["out"], f, ctype="skill",
                     ref={"name": os.path.basename(os.path.dirname(sp)) if sp else slug(_src_label("artifacts", a)), **({"path": sp} if sp else {})},
                     ports={"in": ["task"], "out": ["result"]},
                     drill=process_boards(f"doc-{slug(alias)}-out{m}", sp, boards, NAME) if sp else None)
        else:
            doc = local_doc(url)
            c = card(f"out-{m}", _src_label("artifacts", a), "artifact", "artifact", XO, yo + m * DY + (ROW - DY if kb and m else 0), LANES["out"], f,
                     drill=process_boards(f"doc-{slug(alias)}-out{m}", doc, boards, NAME) if doc else None, ports={"in": ["in"], "out": []})
        cards.append(c)
        m += 1
    if not m:
        cards.append(card("out-none", "No outputs yet", "none", "none", XO, yo, LANES["out"], {"role": "empty", **prov(pj_path, "(none registered)")}))

    # children
    yc = max(Y0 + max(n, 1) * DY, yw + ROW, yo + max(m, 1) * DY + (ROW - DY if kb else 0)) + 60
    for j, ch in enumerate(proj.get("children") or []):
        cards.append(card(f"ch-{slug(ch)}", known.get(ch, ch), "project", ch, XI + (j % 6) * COL, yc + (j // 6) * ROW, LANES["kids"],
                          prov(pj_path, "children"), drill=f"p-{slug(ch)}" if ch in known else None))
    if not proj.get("children"):
        cards.append(card("ch-none", "No child projects", "none", "none", XI, yc, LANES["kids"], {"role": "empty", **prov(pj_path, "(none registered)")}))

    for c in cards:  # drop empty drill keys left by optional sub-boards
        if "drill" in c and not c["drill"]:
            del c["drill"]
    b = board(f"p-{slug(alias)}", cards, edges, NAME, sources + [s["sop"] for s in stations if s["sop"]])
    b["group_styles"] = {LANES["tpm"]: {"collapsed": not expanded}}
    return b


def build(root, opts):
    """-> {board name: board doc}. opts: sops [dirs], include_archived, items (default library.scan()), agent_project alias."""
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

    boards = {}
    stations, gates, agent = resolve_stations(items, sop_files, boards)
    agent_item = next((it for it in items if it["kind"] == "agent" and it["name"] == agent and it.get("card_template")), None)
    pipeline_paths = {s["item"]["path"] for s in stations if s.get("item")} | {g["item"]["path"] for g in gates.values()}
    if agent:
        _runtime(boards, items, agent)
    reports = reports_by_project(root, set(known))
    act = activity_skills(root)
    for p in ps:
        boards[f"p-{slug(p['alias'])}"] = project_board(root, p["alias"], p, known, boards, stations, agent_item,
                                                       p["alias"] == opts.get("agent_project"), reports, act, items, pipeline_paths)

    lanes = {}
    for p in ps:
        top = p
        while top.get("parent") in known:
            top = next(q for q in ps if q["alias"] == top["parent"])
        lanes.setdefault(top.get("name", top["alias"]), []).append(p)
    cards, y = [], 40
    for lane, members in sorted(lanes.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        for i, p in enumerate(sorted(members, key=lambda q: (q.get("parent") is not None, q["alias"]))):
            meta = _index_meta(os.path.join(kb_dir(project_dir(root, p["alias"])), "_index.md"))
            f = {"state": p.get("state"), "health": meta.get("health"), "next_action": meta.get("next_action"), "parent": p.get("parent")}
            f.update(prov(pf_path, f"projects[{p['alias']}]"))
            cards.append(card(f"pj-{slug(p['alias'])}", p.get("name", p["alias"]), "project", p["alias"],
                              40 + (i % 6) * COL, y + (i // 6) * ROW, lane, f, drill=f"p-{slug(p['alias'])}"))
        y += ((len(members) + 5) // 6) * ROW + 60
    overview = board("portfolio", cards, [], NAME, [pf_path])
    boards["portfolio"] = overview
    y0 = max([c["pos"]["y"] for c in cards] or [0]) + ROW + 60
    for i, path in enumerate(sops_lane):
        sb = process_boards(f"sop-{slug(os.path.basename(path)[:-7])}", path, boards, NAME)
        cards.append(card(f"sop-{i}", os.path.basename(path)[:-7], "sop", os.path.basename(path), 40 + (i % 6) * COL,
                          y0 + (i // 6) * ROW, "Supplementary SOPs", prov(path, "(whole SOP)"), drill=sb))
        overview["generated"]["sources"].append(path)
    if "runtime" in boards:
        cards.append(card("runtime", "Agent runtime (hooks, MCP)", "runtime", "runtime", 40, y0 + ((len(sops_lane) + 5) // 6) * ROW + 60,
                          "Agent runtime", {"agent": agent}, drill="runtime"))
    for b in boards.values():
        for c in b["cards"]:
            if "drill" in c and not c["drill"]:
                del c["drill"]
    return boards


def _runtime(boards, items, a):
    ag = next((it for it in items if it["kind"] == "agent" and it["name"] == a and it.get("card_template")), None)
    if not ag:
        return
    rc = [_typed(ag, f"ag-{slug(a)}", 40, 40, f"Agent {a}")]
    hooks = [it for it in items if it["kind"] == "hook" and a in (it.get("used_by") or []) and it.get("card_template")
             and not str(it.get("path", "")).endswith(".json")]
    for j, h in enumerate(hooks):
        rc.append(_typed(h, f"hk-{slug(a)}-{j}", 40 + (j + 1) * COL, 40, f"Agent {a}"))
    mcps = [it for it in items if it["kind"] == "mcp" and a in (it.get("used_by") or []) and it.get("card_template")]
    for j, m in enumerate(mcps):
        rc.append(_typed(m, f"mc-{slug(a)}-{j}", 40 + (j % 6) * COL, 40 + ROW + (j // 6) * ROW, f"Agent {a}"))
    boards["runtime"] = board("runtime", rc, [], NAME, sorted({c["fields"]["src_path"] for c in rc if c.get("fields", {}).get("src_path")}))
