#!/usr/bin/env python3
"""projedit.py: change a project's input/output roles in project.json, proposal first (stdlib only).

The board shows each registered entry in a lane by its role. Moving a card to another lane (or picking a Role in the
inspector) does not edit the board; it proposes the matching project.json change, which is applied only on approval:

  role                 where the entry goes in project.json
  input:<category>     sources.<category>[]   (slack, sharepoint, wiki, taskei, harmony)  - read every compile
  output               sources.artifacts[]    (classification: artifact)                  - a delivered output
  built                sources.artifacts[]    with "type": "skill"                        - a skill this project built
  uses                 workflow_skills[]      {name, path}                                - a skill its workflow runs

Moves keep the entry's own fields (sid, label, url) and record moved_from / moved_at, like SOP-03 Step 3.5.
A suggested skill (from the activity log) is registered with key "new".

  python projedit.py propose <project.json> <key> <role> [--name N --path P]
  python projedit.py apply <id> --approve
Proposals and the backup of the previous project.json live in $WB_DEPLOY_DIR/projedit/<id>/.
Only a project.json under $WORKFLOW_BOARD_ROOT/projects (the registry root) can be edited.
"""
import argparse
import difflib
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

INPUT_CATS = ("slack", "sharepoint", "wiki", "taskei", "harmony")
KEY_RE = re.compile(r"^(?:sources\.([a-z_]+)(?:\[(\d+)\])?|workflow_skills\[(\d+)\]|new)$")


class Refused(Exception):
    pass


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(b):
    return hashlib.sha256(b if isinstance(b, bytes) else b.encode("utf-8")).hexdigest()


def _dir():
    return os.path.join(os.environ.get("WB_DEPLOY_DIR") or os.path.join(os.path.expanduser("~"), ".workflow-board", "deploy"), "projedit")


def check_path(pj_path):
    """The file must be a project.json inside the registry's projects folder (no symlink tricks)."""
    real = os.path.realpath(pj_path)
    pdir = os.path.realpath(REG._projects_dir(REG.portfolio_root()))
    if os.path.basename(real) != "project.json" or not real.startswith(pdir + os.sep) or not os.path.isfile(real):
        raise Refused(f"{pj_path} is not a project.json under {pdir}")
    return real


def _role_of(key, entry):
    m = KEY_RE.match(key)
    if key == "new":
        return "suggested"
    if m.group(3) is not None:
        return "uses"
    if m.group(1) == "artifacts":
        return "built" if isinstance(entry, dict) and entry.get("type") == "skill" else "output"
    return "input:" + m.group(1)


def _take(doc, key):
    """Remove and return the entry at key."""
    m = KEY_RE.match(key or "")
    if not m or key == "new":
        raise Refused(f"bad key {key!r}")
    if m.group(3) is not None:
        lst, i = doc.setdefault("workflow_skills", []), int(m.group(3))
    else:
        cat, i = m.group(1), m.group(2)
        src = doc.setdefault("sources", {})
        if i is None:  # a scalar or single-object category
            if not src.get(cat):
                raise Refused(f"{key} is empty")
            e, src[cat] = src[cat], None
            return e, cat
        lst, i = src.get(cat), int(i)
        if not isinstance(lst, list):
            raise Refused(f"{key} is not a list")
    if not 0 <= i < len(lst):
        raise Refused(f"{key} does not exist (the project changed since the board was generated; refresh the boards)")
    return lst.pop(i), (m.group(1) or "workflow_skills")


def _skill_name(path, fallback):
    try:
        text = open(os.path.expanduser(path), encoding="utf-8").read(4000)
        m = re.search(r"^name:\s*(\S+)", text, re.M)
        if m:
            return m.group(1).strip("\"'")
    except OSError:
        pass
    return os.path.basename(os.path.dirname(os.path.expanduser(path))) if path.endswith("SKILL.md") else fallback


def _put(doc, role, entry, moved_from):
    now = _now()
    if isinstance(entry, dict):
        entry = dict(entry)
        if moved_from:
            entry["moved_from"], entry["moved_at"] = moved_from, now
    if role.startswith("input:"):
        cat = role.split(":", 1)[1]
        if cat not in INPUT_CATS:
            raise Refused(f"input category must be one of {', '.join(INPUT_CATS)}")
        cur = doc.setdefault("sources", {}).get(cat)
        if cur and not isinstance(cur, list):
            raise Refused(f"sources.{cat} holds a single entry, not a list; edit it in project.json")
        if isinstance(entry, dict):
            entry.update({"classification": "source", "classified_at": now, "classification_reason": "moved on the workflow board"})
            if entry.get("type") == "skill":
                del entry["type"]
        doc["sources"][cat] = (cur or []) + [entry]
    elif role in ("output", "built"):
        if isinstance(entry, dict):
            entry.update({"classification": "artifact", "classified_at": now, "classification_reason": "moved on the workflow board"})
            if role == "built":
                entry["type"] = "skill"
            elif entry.get("type") == "skill":
                del entry["type"]
        doc.setdefault("sources", {}).setdefault("artifacts", []).append(entry)
    elif role == "uses":
        e = entry if isinstance(entry, dict) else {"url": entry}
        path = re.sub(r"^file://", "", str(e.get("path") or e.get("url") or ""))
        ws = {"name": e.get("name") or _skill_name(path, e.get("label") or "skill"), "path": path}
        for k in ("sid", "label", "moved_from", "moved_at"):
            if e.get(k):
                ws[k] = e[k]
        doc.setdefault("workflow_skills", []).append(ws)
    else:
        raise Refused(f"unknown role {role!r}")


def propose(pj_path, key, role, name=None, path=None):
    real = check_path(pj_path)
    raw = open(real, "rb").read()
    doc = json.loads(raw)
    if key == "new":
        if role not in ("uses", "built"):
            raise Refused("a suggested skill can be registered as 'uses' or 'built'")
        if not path or not os.path.isfile(os.path.expanduser(path)):
            raise Refused("a suggested skill needs the path of its SKILL.md")
        entry, frm = ({"name": name, "path": path} if role == "uses"
                      else {"label": name or _skill_name(path, "skill"), "url": "file://" + path, "type": "skill", "added": _now()[:10]}), None
        if role == "uses":
            doc.setdefault("workflow_skills", []).append({"name": name or _skill_name(path, "skill"), "path": path})
        else:
            _put(doc, "built", entry, None)
    else:
        m = KEY_RE.match(key or "")
        if not m:
            raise Refused(f"bad key {key!r}")
        entry, frm = _take(doc, key)
        if _role_of(key, entry) == role:
            raise Refused(f"{key} already has role {role}")
        _put(doc, role, entry, frm)
    new = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    old = raw.decode("utf-8")
    pid = _now().replace(":", "").replace("-", "") + "-" + _sha(real + key + role)[:6]
    pdir = os.path.join(_dir(), pid)
    os.makedirs(pdir, exist_ok=True)
    with open(os.path.join(pdir, "project.json.new"), "w", encoding="utf-8") as f:
        f.write(new)
    diff = list(difflib.unified_diff(json.dumps(json.loads(old), indent=2, ensure_ascii=False).splitlines(), new.splitlines(),
                                     "a/project.json", "b/project.json", lineterm="", n=2))
    plan = {"id": pid, "path": real, "key": key, "role": role, "old_sha": _sha(raw), "new_sha": _sha(new), "state": "proposed",
            "created_at": _now()}
    with open(os.path.join(pdir, "plan.json"), "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)
    text = f"Proposal {pid}: {real}\n  {key} -> {role}\n\n" + "\n".join(diff[:300])
    return plan, text


def apply(pid, approve=False):
    pdir = os.path.join(_dir(), os.path.basename(pid))
    try:
        plan = json.load(open(os.path.join(pdir, "plan.json"), encoding="utf-8"))
    except (OSError, ValueError):
        raise Refused(f"proposal {pid} not found")
    if plan["state"] != "proposed":
        raise Refused(f"proposal {pid} is {plan['state']}")
    if not approve:
        raise Refused("refusing to write project.json without approve")
    real = check_path(plan["path"])
    if _sha(open(real, "rb").read()) != plan["old_sha"]:
        raise Refused("project.json changed after this proposal; propose again")
    new = open(os.path.join(pdir, "project.json.new"), encoding="utf-8").read()
    if _sha(new) != plan["new_sha"]:
        raise Refused("the staged change was modified; propose again")
    shutil.copy2(real, os.path.join(pdir, "project.json.bak"))
    fd, tmp = tempfile.mkstemp(prefix=".wb-", dir=os.path.dirname(real))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(new)
    os.replace(tmp, real)
    plan["state"], plan["applied_at"], plan["backup"] = "applied", _now(), os.path.join(pdir, "project.json.bak")
    with open(os.path.join(pdir, "plan.json"), "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)
    return plan


def main(argv):
    ap = argparse.ArgumentParser(description="Change input/output roles in project.json, proposal first.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("propose")
    p.add_argument("project_json")
    p.add_argument("key")
    p.add_argument("role")
    p.add_argument("--name")
    p.add_argument("--path")
    a2 = sub.add_parser("apply")
    a2.add_argument("id")
    a2.add_argument("--approve", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "propose":
            plan, text = propose(a.project_json, a.key, a.role, a.name, a.path)
            print(text + f"\n\nTo apply: python projedit.py apply {plan['id']} --approve")
        else:
            plan = apply(a.id, a.approve)
            print(f"applied {plan['id']}; backup at {plan['backup']}")
        return 0
    except Refused as e:
        print(f"projedit: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
