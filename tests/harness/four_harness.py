#!/usr/bin/env python3
"""Four-harness fixture: one deployed line, judged the same way in Claude Code, Codex, Kiro and a plain .agents setup.

The fixture line has three stations that each write one declared output file:
  1. Collect  -> notes.txt     2. Count -> count.txt     3. Report -> report.md
Acceptance is artifact-based (deploy.check_run): every declared output exists and the line finished. Identical text
across harnesses is NOT required.

  python tests/harness/four_harness.py --static            # always runs: layout + discovery + spec checks for all four
  python tests/harness/four_harness.py --live kiro,claude,codex,agents
                                                           # opt-in: runs the real CLIs in temp folders, then checks files

Live mode runs each harness non-interactively in a fresh temp folder with only file tools, using the harness's own
skill discovery (the deployed skill is placed in that folder's skills dir). It never touches your real installs.
"agents" has no CLI of its own: it is run with Kiro pointed at a project-local .agents/skills folder.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

SRC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, SRC)
import deploy as D  # noqa: E402
import scanners as S  # noqa: E402

LINE = "wb-fixture-line"
TASK = ("Use the skill %s. Run every station in order in the current folder and write each station's output file "
        "exactly as named. Do not ask questions." % LINE)

# where each harness discovers project-level skills, relative to the project folder
LAYOUT = {"claude": ".claude/skills", "codex": ".codex/skills", "kiro": ".kiro/skills", "agents": ".agents/skills"}


def fixture_board():
    def st(cid, label, x, outs, instr, ins=None):
        f = {"outputs": outs, "instruction": instr}
        if ins:
            f["inputs"] = ins
        return {"id": cid, "type": "step", "label": label, "immutable": False, "enabled": True, "execution_mode": "observe",
                "ref": {"kind": "task"}, "ports": {"in": ["in"], "out": ["next"]}, "pos": {"x": x, "y": 0}, "fields": f}
    cards = [st("collect", "Collect", 0, "notes.txt", "Write three short lines about why repeatable workflows matter to notes.txt."),
             st("count", "Count", 300, "count.txt", "Count the lines in notes.txt and write only the number to count.txt.", "notes.txt"),
             st("report", "Report", 600, "report.md", "Write report.md with a title, the three notes as bullets, and the count.",
                "notes.txt, count.txt")]
    return {"schema": "workflow-board/v1", "board": LINE, "version": 1, "execution_mode": "observe", "saved_at": "x",
            "integrity_hash": None, "cards": cards,
            "edges": [{"from": "collect.next", "to": "count.in"}, {"from": "count.next", "to": "report.in"}]}


def deploy_into(skills_dir):
    t = tempfile.mkdtemp(prefix="wb-4h-")
    bd = os.path.join(t, "boards")
    os.makedirs(bd)
    with open(os.path.join(bd, LINE + ".json"), "w") as f:
        json.dump(fixture_board(), f)
    os.environ["WB_BOARD_DIRS"], os.environ["WB_DEPLOY_DIR"] = bd, os.path.join(t, "deploy")
    plan, _ = D.propose(LINE, skills_dir)
    return D.apply(plan["id"], approve=True, token=plan["token"])


def spec_check(skill_md):
    """Agent Skills format checks every harness relies on: front matter name == folder, name pattern, description."""
    text = open(skill_md, encoding="utf-8").read()
    fm = S.frontmatter(text)
    folder = os.path.basename(os.path.dirname(skill_md))
    errs = []
    if fm.get("name") != folder:
        errs.append(f"name {fm.get('name')!r} != folder {folder!r}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", fm.get("name") or ""):
        errs.append("name must be lowercase letters, digits, hyphens")
    if not (0 < len(fm.get("description", "")) <= 1024):
        errs.append("description missing or over 1024 chars")
    if "## Steps" not in text:
        errs.append("no ## Steps section")
    return errs


def static():
    rows = []
    for h, rel in LAYOUT.items():
        proj = tempfile.mkdtemp(prefix=f"wb-4h-{h}-")
        plan = deploy_into(os.path.join(proj, rel))
        skill = os.path.join(proj, rel, LINE, "SKILL.md")
        col = S.Collector()
        S.scan_skill_dir(h, os.path.join(proj, rel), col)
        found = [i["name"] for i in col.items]
        errs = spec_check(skill) + ([] if found == [LINE] else [f"scanner found {found}"]) + [f"drift {d}" for d in plan["drift"]]
        rows.append((h, rel, not errs, "; ".join(errs) or "discoverable, spec ok, no drift"))
        shutil.rmtree(proj, ignore_errors=True)
    return rows


def _cmd(h, proj):
    if h == "claude":
        return ["claude", "-p", TASK, "--allowedTools", "Read,Write,Edit,Skill", "--permission-mode", "acceptEdits"]
    if h == "codex":
        return ["codex", "exec", "--skip-git-repo-check", "--sandbox", "workspace-write", TASK]
    return ["kiro-cli", "chat", "--no-interactive", "--trust-tools=fs_read,fs_write", TASK]


def live(harnesses, timeout):
    rows = []
    for h in harnesses:
        proj = tempfile.mkdtemp(prefix=f"wb-4h-live-{h}-")
        deploy_into(os.path.join(proj, LAYOUT[h]))
        exe = _cmd(h, proj)[0]
        if not shutil.which(exe):
            rows.append((h, "SKIP", f"{exe} not installed"))
            continue
        try:
            r = subprocess.run(_cmd(h, proj), cwd=proj, capture_output=True, text=True, timeout=timeout)
            with open(os.path.join(proj, f".{h}-run.log"), "w") as f:
                f.write(r.stdout + "\n--- stderr ---\n" + r.stderr)
            tail = (r.stdout + r.stderr).strip().splitlines()[-1:] or [""]
        except subprocess.TimeoutExpired:
            r, tail = None, ["timed out"]
        res = D.check_run(os.path.join(proj, LAYOUT[h], LINE), proj)
        got = ", ".join(f"{o['name']}:{o['status']}" for st in res["stations"] for o in st["outputs"])
        rows.append((h, "PASS" if res["ok"] else "FAIL", f"exit {r.returncode if r else '-'}; {got}; last line: {tail[0][:120]}"))
        if res["ok"] and not os.environ.get("WB_KEEP_RUNS"):
            shutil.rmtree(proj, ignore_errors=True)
        else:
            rows[-1] = rows[-1][:2] + (rows[-1][2] + f"; kept {proj}",)
    return rows


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--static", action="store_true")
    ap.add_argument("--live", default="")
    ap.add_argument("--timeout", type=int, default=300)
    a = ap.parse_args(argv)
    ok = True
    if a.static or not a.live:
        for h, rel, good, info in static():
            ok = ok and good
            print(f"{'PASS' if good else 'FAIL'} static {h:7s} {rel:16s} {info}")
    if a.live:
        for h, st, info in live([x for x in a.live.split(",") if x], a.timeout):
            ok = ok and st != "FAIL"
            print(f"{st} live   {h:7s} {info}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
