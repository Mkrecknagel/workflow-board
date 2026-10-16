#!/usr/bin/env python3
"""deploy.py: turn a board into agent files, proposal first (stdlib only).

The board is a control panel; the files it produces are what the agent runs. Deploy never writes in place:

  python deploy.py propose <board> --target DIR [--name SKILL]   compile the board into a staged proposal, print the diff
  python deploy.py apply   <proposal id>  --approve --token T              write the staged files into --target (atomic, new files only)
  python deploy.py list                                           list staged proposals and their state

What a line compiles to (the portable carrier every harness discovers):
  <target>/<skill>/SKILL.md     front matter (name, description, inputs, outputs, generated_from) and an ordered
                                "## Steps" list: one section per station, in edge order from the first station to
                                the last, each with its inputs, outputs and what it runs (a skill, tool, agent or the
                                step's own instruction). The agent follows it top to bottom in any harness.
  <target>/<skill>/line.json    machine-readable manifest of the same stations (ids, refs, contracts, provenance)

Updating a line that was deployed before (PR 4):
  - A target file that Deploy wrote earlier (its line.json says generated_from this board) is UPDATED: the proposal
    shows a unified diff, apply snapshots the old files first, and `deploy.py rollback <id>` restores them.
  - A target file Deploy did not write, or one edited by hand since Deploy wrote it, is a CONFLICT and is never
    overwritten. `--adopt` on propose takes over a hand-edited generated file (the old copy is still snapshotted).
  - Before applying, every station's source file is re-hashed. If an SOP or skill the board was imported from has
    changed since the import (src_sha differs), apply refuses: re-run wb_init.py and propose again.
  - After applying, the written files are re-read and checked against the plan (`drift` must be empty).
Module reuse: cards inserted from a module (origin_module) collapse into ONE station that calls the installed skill
of the same name when one exists in the target or the library, instead of re-inlining the module's steps.

Proposals live in $WB_DEPLOY_DIR (default ~/.workflow-board/deploy/<id>/): files/, plan.json, diff.txt.
"""
import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import registry as REG  # noqa: E402
import validate as V  # noqa: E402

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
STATION_TYPES = ("step", "skill", "agent", "tool", "gate", "hook", "input", "output", "trigger")


def deploy_dir():
    return os.environ.get("WB_DEPLOY_DIR") or os.path.join(os.path.expanduser("~"), ".workflow-board", "deploy")


def _read(path, mode="r"):
    if "b" in mode:
        with open(path, "rb") as f:
            return f.read()
    with open(path, encoding="utf-8", errors="replace" if mode == "r?" else "strict") as f:
        return f.read()


def _jread(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def load_board(name):
    p = REG.resolve(name, os.path.join(HERE, "boards"))
    if not p or not os.path.isfile(p):
        raise SystemExit(f"board '{name}' not found")
    with open(p, encoding="utf-8") as f:
        doc = json.load(f)
    V.validate_doc(doc, name)
    return doc, p


def _card_of(endpoint):
    return str(endpoint).rsplit(".", 1)[0]


def order_stations(doc):
    """Stations (action cards) in run order: a topological walk of the edges, ties broken by position (left to right,
    top to bottom). Structural cards (notes, lanes, boundaries, resources) are not stations. Raises on a cycle."""
    cards = {c["id"]: c for c in doc["cards"] if c["type"] in STATION_TYPES and c.get("enabled", True)}
    succ = {cid: [] for cid in cards}
    indeg = {cid: 0 for cid in cards}
    for e in doc.get("edges", []):
        a, b = _card_of(e.get("from")), _card_of(e.get("to"))
        if a in cards and b in cards and a != b and b not in succ[a]:
            succ[a].append(b)
            indeg[b] += 1
    key = lambda cid: (cards[cid]["pos"]["x"], cards[cid]["pos"]["y"], cid)  # noqa: E731
    ready = sorted([c for c, d in indeg.items() if d == 0], key=key)
    out = []
    while ready:
        cid = ready.pop(0)
        out.append(cards[cid])
        for n in sorted(succ[cid], key=key):
            indeg[n] -= 1
            if indeg[n] == 0:
                ready.append(n)
        ready.sort(key=key)
    if len(out) != len(cards):
        stuck = sorted(set(cards) - {c["id"] for c in out})
        raise ValueError(f"the line has a loop through {', '.join(stuck)}; a deployable line must run first to last")
    return out


def _list(v):
    return [x.strip() for x in str(v or "").split(",") if x.strip()]


def _runs(c):
    """One line saying what the agent does at this station, from the card's own ref."""
    r, t = c.get("ref") or {}, c["type"]
    if t == "skill":
        return f"Use the skill `{r.get('name')}`" + (f" ({r['path']})" if r.get("path") else "") + "."
    if t == "tool":
        return f"Call the tool `{r.get('name')}`."
    if t == "agent":
        return f"Hand this station to the agent `{r.get('route')}`."
    if t == "gate":
        return f"Stop and get approval before continuing ({r.get('rule') or 'owner approval'}). Do not continue without it."
    if t == "hook":
        return f"This station is enforced by the `{r.get('event')}` hook `{os.path.basename(str(r.get('file') or ''))}` where the harness supports hooks; otherwise check it yourself here."
    if t in ("input", "trigger"):
        return f"Start here: {r.get('schema') or r.get('event') or 'the request'}."
    if t == "output":
        return f"Produce the final output: {r.get('schema') or 'the result'}."
    f = c.get("fields") or {}
    return f.get("instruction") or f.get("summary") or f"Do: {c['label']}."


def collapse_modules(stations, installed):
    """Consecutive stations from the same module (origin_module) become one station that calls the installed skill of
    that name, when one is installed. Inputs come from the first station, outputs from the last. -> (stations, reused)."""
    out, reused, i = [], [], 0
    while i < len(stations):
        m = stations[i].get("origin_module")
        if m and m in installed:
            j = i
            while j + 1 < len(stations) and stations[j + 1].get("origin_module") == m:
                j += 1
            first, last = stations[i].get("fields") or {}, stations[j].get("fields") or {}
            out.append({"id": f"module-{m}", "type": "skill", "label": m, "pos": stations[i]["pos"],
                        "ref": {"name": m, "path": installed[m]},
                        "fields": {k: v for k, v in (("inputs", first.get("inputs")), ("outputs", last.get("outputs"))) if v},
                        "module_cards": [c["id"] for c in stations[i:j + 1]]})
            reused.append(m)
            i = j + 1
        else:
            out.append(stations[i])
            i += 1
    return out, reused


def compile_line(doc, skill_name, description=None, installed=None):
    """-> (files {relative path: text}, manifest dict). `installed` {skill name: path} enables module reuse."""
    stations, reused = collapse_modules(order_stations(doc), installed or {})
    if not stations:
        raise ValueError("the board has no stations to deploy (steps, skills, tools, agents, gates)")
    first_in = _list((stations[0].get("fields") or {}).get("inputs"))
    last_out = _list((stations[-1].get("fields") or {}).get("outputs"))
    desc = description or f"Runs the '{doc['board']}' line from {stations[0]['label']} to {stations[-1]['label']}."
    man = {"schema": "workflow-board/line/v1", "name": skill_name, "board": doc["board"],
           "board_sha": _sha(json.dumps({k: v for k, v in doc.items() if k != "saved_at"}, sort_keys=True)), "stations": []}
    lines = ["---", f"name: {skill_name}", f"description: {desc}"]
    if first_in:
        lines.append(f"inputs: [{', '.join(first_in)}]")
    if last_out:
        lines.append(f"outputs: [{', '.join(last_out)}]")
    lines += [f"generated_from: workflow-board {doc['board']}", "---", "", f"# {skill_name}", "",
              desc, "",
              "Run every station below in order, from the first to the last. Finish a station, check its outputs exist, "
              "then go to the next. If a station's outputs are missing, stop and report which station failed and why. "
              "Do not skip, merge or reorder stations.", "", "## Steps", ""]
    missing = []
    for i, c in enumerate(stations, 1):
        f = c.get("fields") or {}
        ins, outs = _list(f.get("inputs")), _list(f.get("outputs"))
        if not ins and not outs:
            missing.append(c["label"])
        lines += [f"### {i}. {c['label']}", "", _runs(c)]
        if ins:
            lines.append(f"- **Inputs:** {', '.join(ins)}")
        if outs:
            lines.append(f"- **Outputs:** {', '.join(outs)}")
        if not ins and not outs:
            lines.append("- **Contract:** not declared. State what you used and what you produced before moving on.")
        if f.get("src_path"):
            lines.append(f"- **Source:** {f['src_path']}" + (f" ({f['src_anchor']})" if f.get("src_anchor") else ""))
        lines.append("")
        man["stations"].append({"n": i, "id": c["id"], "type": c["type"], "label": c["label"], "ref": c.get("ref") or {},
                                **({"module_cards": c["module_cards"]} if c.get("module_cards") else {}),
                                "inputs": ins, "outputs": outs,
                                "source": {k: f[k] for k in ("src_path", "src_anchor", "src_sha") if f.get(k)}})
    lines += ["## Done", "", f"The line is done when station {len(stations)} ({stations[-1]['label']}) has produced its outputs."
              + (f" Final outputs: {', '.join(last_out)}." if last_out else ""), ""]
    man["contract_missing"] = missing
    man["reused"] = reused
    return {f"{skill_name}/SKILL.md": "\n".join(lines), f"{skill_name}/line.json": json.dumps(man, indent=2) + "\n"}, man


def _atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".wb-", dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _installed_skills(target):
    """{name: path} of skills already present in the target folder and in the scanned library."""
    out = {}
    try:
        import library
        for it in library.scan():
            if it.get("kind") == "skill" and str(it.get("path", "")).endswith("SKILL.md"):
                out.setdefault(it["name"], it["path"])
    except Exception:  # noqa: BLE001  reuse is best-effort; a scan failure means "inline the module"
        pass
    if os.path.isdir(target):
        for d in sorted(os.listdir(target)):
            p = os.path.join(target, d, "SKILL.md")
            if os.path.isfile(p):
                out[d] = p
    return out


def _owned(target, rel, board):
    """(owned, expected_sha): Deploy wrote this file for this board and it is unchanged since (per the line.json written then)."""
    man_path = os.path.join(target, rel.split("/", 1)[0], "line.json")
    try:
        man = _jread(man_path)
    except (OSError, ValueError):
        return False, None
    if man.get("board") != board:
        return False, None
    exp = (man.get("files") or {}).get(rel)
    if rel.endswith("line.json"):
        return True, exp  # this manifest says it was written for this board, so it is ours to regenerate
    if not exp:
        return False, None
    cur = _sha(_read(os.path.join(target, rel)))
    return cur == exp, exp


def _unified(old, new, rel):
    import difflib
    return list(difflib.unified_diff(old.splitlines(), new.splitlines(), f"a/{rel}", f"b/{rel}", lineterm="", n=2))


def propose(board, target, skill_name=None, description=None, adopt=False):
    doc, bpath = load_board(board)
    skill_name = skill_name or re.sub(r"[^a-z0-9-]+", "-", board.lower()).strip("-")
    if not NAME_RE.match(skill_name):
        raise SystemExit(f"skill name {skill_name!r} must be lowercase letters, digits and hyphens")
    target = os.path.abspath(os.path.expanduser(target))
    files, man = compile_line(doc, skill_name, description, installed=_installed_skills(target))
    sk_rel = f"{skill_name}/SKILL.md"
    man["files"] = {sk_rel: _sha(files[sk_rel])}
    files[f"{skill_name}/line.json"] = json.dumps(man, indent=2) + "\n"
    pid = f"{_now().replace(':', '').replace('-', '')}-{skill_name}"
    pdir = _plan_dir(pid)
    token = secrets.token_urlsafe(18)  # handed to whoever called propose (the page); apply needs it back
    plan = {"id": pid, "board": board, "board_path": bpath, "board_file_sha": _sha(_read(bpath, "rb")),
            "target": target, "created_at": _now(), "state": "proposed", "files": [], "token_sha": _sha(token),
            "contract_missing": man["contract_missing"], "reused": man.get("reused", []),
            "sources": sorted({(st["source"]["src_path"], st["source"].get("src_sha", "")) for st in man["stations"]
                               if st["source"].get("src_path")})}
    diff = [f"Proposal {pid}: board '{board}' -> {target}", ""]
    for rel, text in sorted(files.items()):
        dest = os.path.join(target, rel)
        _atomic_write(os.path.join(pdir, "files", rel), text)
        if not os.path.exists(dest):
            plan["files"].append({"path": rel, "action": "create", "sha": _sha(text)})
            diff += [f"+ create  {dest}"] + ["    | " + ln for ln in text.splitlines()] + [""]
            continue
        old = _read(dest, "r?")
        if old == text:
            plan["files"].append({"path": rel, "action": "unchanged", "sha": _sha(text)})
            diff += [f"= unchanged  {dest}", ""]
            continue
        owned, _ = _owned(target, rel, board)
        if owned or adopt:
            plan["files"].append({"path": rel, "action": "update", "sha": _sha(text), "old_sha": _sha(old),
                                  "adopted": bool(adopt and not owned)})
            diff += [f"~ update  {dest}" + ("  (adopting a file edited by hand)" if adopt and not owned else "")]
            diff += ["    " + ln for ln in _unified(old, text, rel)] + [""]
        else:
            plan["files"].append({"path": rel, "action": "conflict", "sha": _sha(text), "old_sha": _sha(old)})
            diff += [f"! CONFLICT  {dest}: exists and was not written by Deploy for this board, or was edited by hand since. "
                     "It will not be overwritten (use --adopt to take it over; the old copy is kept for rollback).", ""]
    if any(f["action"] == "conflict" for f in plan["files"]):
        plan["state"] = "conflict"
    if man.get("reused"):
        diff.append("Reused installed skills instead of inlining modules: " + ", ".join(man["reused"]) + ".")
    if man["contract_missing"]:
        diff.append(f"Note: {len(man['contract_missing'])} station(s) declare no inputs or outputs: "
                    + ", ".join(man["contract_missing"]) + ". The skill tells the agent to state them as it runs.")
    _atomic_write(os.path.join(pdir, "plan.json"), json.dumps(plan, indent=2) + "\n")
    _atomic_write(os.path.join(pdir, "diff.txt"), "\n".join(diff) + "\n")
    return dict(plan, token=token), "\n".join(diff)  # the token is returned, never stored: plan.json keeps only its hash


PID_RE = re.compile(r"^[0-9TZ]+-[a-z0-9][a-z0-9-]*$")


def _plan_dir(pid):
    """<deploy_dir>/<pid> for a well-formed proposal id that stays inside deploy_dir(); anything else is refused."""
    if not isinstance(pid, str) or not PID_RE.match(pid):
        raise SystemExit(f"proposal id {str(pid)[:60]!r} is not a valid proposal id")
    root = os.path.realpath(deploy_dir())
    pdir = os.path.realpath(os.path.join(root, pid))
    if os.path.commonpath([pdir, root]) != root or pdir == root:
        raise SystemExit(f"proposal id {pid!r} resolves outside the proposal folder")
    return pdir


def _load_plan(pid):
    pdir = _plan_dir(pid)
    try:
        return pdir, _jread(os.path.join(pdir, "plan.json"))
    except (OSError, ValueError):
        raise SystemExit(f"proposal {pid} not found in {deploy_dir()}")


def stale_sources(plan):
    """Source files (SOPs, skills) whose current hash differs from the one recorded when the board was imported."""
    out = []
    for path, sha in plan.get("sources") or []:
        if not sha:
            continue
        try:
            cur = hashlib.sha256(_read(path, "rb")).hexdigest()[:16]
        except OSError:
            cur = "missing"
        if cur != sha:
            out.append({"path": path, "imported_sha": sha, "now": cur})
    return out


def drift(plan):
    """Files whose content on disk differs from what the plan says was written. Empty means the deploy landed exactly."""
    out = []
    for f in plan["files"]:
        if f["action"] == "conflict":
            continue
        p = os.path.join(plan["target"], f["path"])
        cur = _sha(_read(p)) if os.path.isfile(p) else "missing"
        if cur != f["sha"]:
            out.append({"path": f["path"], "expected": f["sha"], "found": cur})
    return out


def apply(pid, approve=False, token=None):
    pdir, plan = _load_plan(pid)
    if plan["state"] in ("applied", "rolled_back"):
        raise SystemExit(f"proposal {pid} was already {plan['state'].replace('_', ' ')}")
    if not approve:
        raise SystemExit("refusing to write without --approve. Review diff.txt first:\n  " + os.path.join(pdir, "diff.txt"))
    if not isinstance(token, str) or not token or not hmac.compare_digest(_sha(token), str(plan.get("token_sha") or "")):
        raise SystemExit("missing or wrong proposal token: apply needs the token that propose returned for this proposal")
    if plan["state"] == "conflict":
        raise SystemExit("proposal has conflicting files that will not be overwritten; see diff.txt")
    if _sha(_read(plan["board_path"], "rb")) != plan["board_file_sha"]:
        raise SystemExit("the board changed after this proposal was made; run propose again")
    st = stale_sources(plan)
    if st:
        raise SystemExit("source files changed since the board was imported (" + ", ".join(s["path"] for s in st)
                         + "); re-run wb_init.py, review the board, and propose again")
    for f in plan["files"]:
        dest = os.path.join(plan["target"], f["path"])
        if f["action"] == "create" and os.path.exists(dest):
            raise SystemExit(f"{f['path']} appeared in the target after the proposal was made; nothing was written")
        if f["action"] == "update":
            cur = _sha(_read(dest)) if os.path.isfile(dest) else "missing"
            if cur != f["old_sha"]:
                raise SystemExit(f"{f['path']} changed after the proposal was made; nothing was written")
        if _sha(_read(os.path.join(pdir, "files", f["path"]))) != f["sha"]:
            raise SystemExit(f"staged file {f['path']} was modified after the proposal; run propose again")
    # rollback snapshot of every file this apply will replace
    snap = os.path.join(pdir, "rollback")
    for f in plan["files"]:
        if f["action"] == "update":
            dst = os.path.join(snap, f["path"])
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(plan["target"], f["path"]), dst)
    os.makedirs(plan["target"], exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".wb-deploy-", dir=plan["target"])
    try:
        creates = [f for f in plan["files"] if f["action"] == "create"]
        new_tops = sorted({f["path"].split("/", 1)[0] for f in creates} - {f["path"].split("/", 1)[0] for f in plan["files"]
                                                                          if f["action"] in ("update", "unchanged")})
        for f in creates:  # files under a brand-new skill folder go in with one rename per folder
            if f["path"].split("/", 1)[0] in new_tops:
                dst = os.path.join(tmp, f["path"])
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(os.path.join(pdir, "files", f["path"]), dst)
        for t in new_tops:
            if os.path.exists(os.path.join(plan["target"], t)):
                raise SystemExit(f"{t} appeared in the target during apply; nothing was written")
        renamed = []
        try:
            for t in new_tops:
                os.rename(os.path.join(tmp, t), os.path.join(plan["target"], t))
                renamed.append(t)
        except OSError:
            for t in reversed(renamed):  # a later rename failed: take back the folders already moved in, leave the target as found
                shutil.rmtree(os.path.join(plan["target"], t), ignore_errors=True)
            raise
        for f in plan["files"]:  # new files inside an existing folder, and updates: one atomic replace each
            if f["action"] == "update" or (f["action"] == "create" and f["path"].split("/", 1)[0] not in new_tops):
                _atomic_write(os.path.join(plan["target"], f["path"]), _read(os.path.join(pdir, "files", f["path"])))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    plan["state"], plan["applied_at"] = "applied", _now()
    plan["drift"] = drift(plan)
    _atomic_write(os.path.join(pdir, "plan.json"), json.dumps(plan, indent=2) + "\n")
    return plan


def rollback(pid):
    """Undo an applied proposal: restore every updated file from the snapshot and remove every file it created.
    Refuses if a file was changed after the apply (it would lose that change)."""
    pdir, plan = _load_plan(pid)
    if plan["state"] != "applied":
        raise SystemExit(f"proposal {pid} is {plan['state']}, not applied")
    d = drift(plan)
    if d:
        raise SystemExit("files changed after this deploy (" + ", ".join(x["path"] for x in d) + "); not rolling back over them")
    for f in plan["files"]:
        dest = os.path.join(plan["target"], f["path"])
        if f["action"] == "update":
            _atomic_write(dest, _read(os.path.join(pdir, "rollback", f["path"])))
        elif f["action"] == "create":
            os.remove(dest)
    for top in sorted({f["path"].split("/", 1)[0] for f in plan["files"] if f["action"] == "create"}):
        p = os.path.join(plan["target"], top)
        if os.path.isdir(p) and not os.listdir(p):
            os.rmdir(p)
    plan["state"], plan["rolled_back_at"] = "rolled_back", _now()
    _atomic_write(os.path.join(pdir, "plan.json"), json.dumps(plan, indent=2) + "\n")
    return plan


def check_run(skill_dir, workdir):
    """Harness-neutral run check: did every station's declared outputs appear in `workdir`? Judged on artifacts, not text.
    Outputs that look like file names (contain a '.' or '/') must exist as files; others are reported as unchecked.
    -> {"ok": bool, "stations": [{n, label, outputs: [{name, status}]}]}"""
    man = _jread(os.path.join(skill_dir, "line.json"))
    res, ok = [], True
    for st in man["stations"]:
        outs = []
        for o in st.get("outputs") or []:
            if "." in o or "/" in o:
                present = os.path.exists(os.path.join(workdir, o))
                ok = ok and present
                outs.append({"name": o, "status": "present" if present else "MISSING"})
            else:
                outs.append({"name": o, "status": "unchecked (not a file name)"})
        res.append({"n": st["n"], "label": st["label"], "outputs": outs})
    return {"ok": ok, "line": man["name"], "stations": res}


def list_proposals():
    d = deploy_dir()
    out = []
    for pid in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        try:
            p = _jread(os.path.join(d, pid, "plan.json"))
            out.append({k: p.get(k) for k in ("id", "board", "target", "state", "created_at")})
        except (OSError, ValueError):
            continue
    return out


def main(argv):
    ap = argparse.ArgumentParser(description="Turn a board into agent files, proposal first.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("propose")
    p.add_argument("board")
    p.add_argument("--target", required=True, help="skills folder to deploy into, e.g. ~/.agents/skills or ~/.kiro/skills")
    p.add_argument("--name", help="skill folder name (default: the board name)")
    p.add_argument("--description")
    p.add_argument("--adopt", action="store_true", help="take over existing files edited by hand (old copies kept for rollback)")
    a2 = sub.add_parser("apply")
    a2.add_argument("id")
    a2.add_argument("--approve", action="store_true")
    a2.add_argument("--token", help="the proposal token printed by propose")
    rb = sub.add_parser("rollback")
    rb.add_argument("id")
    ck = sub.add_parser("check", help="after a run: check every station's declared output files exist")
    ck.add_argument("skill_dir")
    ck.add_argument("--workdir", default=".")
    sub.add_parser("list")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "propose":
            plan, diff = propose(a.board, a.target, a.name, a.description, a.adopt)
            print(diff)
            print(f"\nstate: {plan['state']}. To write these files: python deploy.py apply {plan['id']} --approve --token {plan['token']}")
            return 0 if plan["state"] == "proposed" else 3
        if a.cmd == "apply":
            plan = apply(a.id, a.approve, a.token)
            n = {k: sum(f["action"] == k for f in plan["files"]) for k in ("create", "update", "unchanged")}
            print(f"applied {plan['id']} in {plan['target']}: {n['create']} created, {n['update']} updated, {n['unchanged']} unchanged"
                  + (f"; DRIFT {plan['drift']}" if plan["drift"] else "; re-read matches the plan"))
            print(f"undo with: python deploy.py rollback {plan['id']}")
            return 0 if not plan["drift"] else 4
        if a.cmd == "rollback":
            plan = rollback(a.id)
            print(f"rolled back {plan['id']}")
            return 0
        if a.cmd == "check":
            r = check_run(a.skill_dir, a.workdir)
            print(json.dumps(r, indent=2))
            return 0 if r["ok"] else 1
        print(json.dumps(list_proposals(), indent=2))
        return 0
    except (ValueError, V.Invalid) as e:
        print(f"deploy: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
