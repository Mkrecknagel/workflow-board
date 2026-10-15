#!/usr/bin/env python3
"""deploy.py: turn a board into agent files, proposal first (stdlib only).

The board is a control panel; the files it produces are what the agent runs. Deploy never writes in place:

  python deploy.py propose <board> --target DIR [--name SKILL]   compile the board into a staged proposal, print the diff
  python deploy.py apply   <proposal id>  --approve                write the staged files into --target (atomic, new files only)
  python deploy.py list                                           list staged proposals and their state

What a line compiles to (the portable carrier every harness discovers):
  <target>/<skill>/SKILL.md     front matter (name, description, inputs, outputs, generated_from) and an ordered
                                "## Steps" list: one section per station, in edge order from the first station to
                                the last, each with its inputs, outputs and what it runs (a skill, tool, agent or the
                                step's own instruction). The agent follows it top to bottom in any harness.
  <target>/<skill>/line.json    machine-readable manifest of the same stations (ids, refs, contracts, provenance)

This version only creates new files: if any target path already exists the proposal is marked conflicting and apply
refuses. Editing existing skills, reusing modules and rollback are separate (see docs/deploy.md).

Proposals live in $WB_DEPLOY_DIR (default ~/.workflow-board/deploy/<id>/): files/, plan.json, diff.txt.
"""
import argparse
import hashlib
import json
import os
import re
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


def compile_line(doc, skill_name, description=None):
    """-> (files {relative path: text}, manifest dict)."""
    stations = order_stations(doc)
    if not stations:
        raise ValueError("the board has no stations to deploy (steps, skills, tools, agents, gates)")
    first_in = _list((stations[0].get("fields") or {}).get("inputs"))
    last_out = _list((stations[-1].get("fields") or {}).get("outputs"))
    desc = description or f"Runs the '{doc['board']}' line from {stations[0]['label']} to {stations[-1]['label']}."
    man = {"schema": "workflow-board/line/v1", "name": skill_name, "board": doc["board"], "generated_at": _now(),
           "board_sha": _sha(json.dumps(doc, sort_keys=True)), "stations": []}
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
                                "inputs": ins, "outputs": outs,
                                "source": {k: f[k] for k in ("src_path", "src_anchor", "src_sha") if f.get(k)}})
    lines += ["## Done", "", f"The line is done when station {len(stations)} ({stations[-1]['label']}) has produced its outputs."
              + (f" Final outputs: {', '.join(last_out)}." if last_out else ""), ""]
    man["contract_missing"] = missing
    return {f"{skill_name}/SKILL.md": "\n".join(lines), f"{skill_name}/line.json": json.dumps(man, indent=2) + "\n"}, man


def _atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".wb-", dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def propose(board, target, skill_name=None, description=None):
    doc, bpath = load_board(board)
    skill_name = skill_name or re.sub(r"[^a-z0-9-]+", "-", board.lower()).strip("-")
    if not NAME_RE.match(skill_name):
        raise SystemExit(f"skill name {skill_name!r} must be lowercase letters, digits and hyphens")
    target = os.path.abspath(os.path.expanduser(target))
    files, man = compile_line(doc, skill_name, description)
    pid = f"{_now().replace(':', '').replace('-', '')}-{skill_name}"
    pdir = os.path.join(deploy_dir(), pid)
    plan = {"id": pid, "board": board, "board_path": bpath, "board_file_sha": _sha(open(bpath, "rb").read()),
            "target": target, "created_at": _now(), "state": "proposed", "files": [], "contract_missing": man["contract_missing"]}
    diff = [f"Proposal {pid}: board '{board}' -> {target}", ""]
    for rel, text in sorted(files.items()):
        dest = os.path.join(target, rel)
        exists = os.path.exists(dest)
        _atomic_write(os.path.join(pdir, "files", rel), text)
        plan["files"].append({"path": rel, "action": "conflict" if exists else "create", "sha": _sha(text)})
        diff.append(("! EXISTS (will not overwrite)  " if exists else "+ create  ") + dest)
        if not exists:
            diff += ["    | " + ln for ln in text.splitlines()[:400]]
        diff.append("")
    if any(f["action"] == "conflict" for f in plan["files"]):
        plan["state"] = "conflict"
        diff.append("Some files already exist. This version only creates new files; choose another --name or --target.")
    if man["contract_missing"]:
        diff.append(f"Note: {len(man['contract_missing'])} station(s) declare no inputs or outputs: "
                    + ", ".join(man["contract_missing"]) + ". The skill tells the agent to state them as it runs.")
    _atomic_write(os.path.join(pdir, "plan.json"), json.dumps(plan, indent=2) + "\n")
    _atomic_write(os.path.join(pdir, "diff.txt"), "\n".join(diff) + "\n")
    return plan, "\n".join(diff)


def apply(pid, approve=False):
    pdir = os.path.join(deploy_dir(), pid)
    try:
        plan = json.load(open(os.path.join(pdir, "plan.json"), encoding="utf-8"))
    except (OSError, ValueError):
        raise SystemExit(f"proposal {pid} not found in {deploy_dir()}")
    if plan["state"] == "applied":
        raise SystemExit(f"proposal {pid} was already applied")
    if not approve:
        raise SystemExit("refusing to write without --approve. Review diff.txt first:\n  " + os.path.join(pdir, "diff.txt"))
    if plan["state"] == "conflict":
        raise SystemExit("proposal has files that already exist; this version only creates new files")
    if _sha(open(plan["board_path"], "rb").read()) != plan["board_file_sha"]:
        raise SystemExit("the board changed after this proposal was made; run propose again")
    for f in plan["files"]:
        if os.path.exists(os.path.join(plan["target"], f["path"])):
            raise SystemExit(f"{f['path']} appeared in the target after the proposal was made; nothing was written")
        if _sha(open(os.path.join(pdir, "files", f["path"]), encoding="utf-8").read()) != f["sha"]:
            raise SystemExit(f"staged file {f['path']} was modified after the proposal; run propose again")
    # stage the whole skill folder next to the target, then move each new top-level folder in with one rename
    os.makedirs(plan["target"], exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".wb-deploy-", dir=plan["target"])
    try:
        for f in plan["files"]:
            dst = os.path.join(tmp, f["path"])
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(pdir, "files", f["path"]), dst)
        tops = sorted({f["path"].split("/", 1)[0] for f in plan["files"]})
        for t in tops:
            if os.path.exists(os.path.join(plan["target"], t)):
                raise SystemExit(f"{t} appeared in the target during apply; nothing was written")
        for t in tops:
            os.rename(os.path.join(tmp, t), os.path.join(plan["target"], t))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    plan["state"], plan["applied_at"] = "applied", _now()
    _atomic_write(os.path.join(pdir, "plan.json"), json.dumps(plan, indent=2) + "\n")
    return plan


def list_proposals():
    d = deploy_dir()
    out = []
    for pid in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        try:
            p = json.load(open(os.path.join(d, pid, "plan.json"), encoding="utf-8"))
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
    a2 = sub.add_parser("apply")
    a2.add_argument("id")
    a2.add_argument("--approve", action="store_true")
    sub.add_parser("list")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "propose":
            plan, diff = propose(a.board, a.target, a.name, a.description)
            print(diff)
            print(f"\nstate: {plan['state']}. To write these files: python deploy.py apply {plan['id']} --approve")
            return 0 if plan["state"] == "proposed" else 3
        if a.cmd == "apply":
            plan = apply(a.id, a.approve)
            print(f"applied {plan['id']}: {len(plan['files'])} file(s) created in {plan['target']}")
            return 0
        print(json.dumps(list_proposals(), indent=2))
        return 0
    except (ValueError, V.Invalid) as e:
        print(f"deploy: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
