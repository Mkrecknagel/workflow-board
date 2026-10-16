#!/usr/bin/env python3
"""deploy.py updates (PR 4): re-deploying a line, hand-edit conflicts, --adopt, stale-source refusal, rollback,
drift check, and module reuse. Temp boards, deploy dir and targets only.
Run: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_deploy_update.py"""
import json
import os
import sys
import tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)
sys.path.insert(0, os.path.join(SRC, "tests"))
import deploy as D  # noqa: E402
from test_deploy import card, env, line_board, save  # noqa: E402


def deployed(target, name="line-a"):
    return open(os.path.join(target, name, "SKILL.md")).read()


def first_deploy():
    t, bd, target = env()
    save(bd, line_board())
    plan, _ = D.propose("line-a", target)
    D.apply(plan["id"], approve=True, token=plan["token"])
    return t, bd, target


def test_redeploy_unchanged_board_is_a_no_op():
    t, bd, target = first_deploy()
    plan, diff = D.propose("line-a", target)
    assert plan["state"] == "proposed" and {f["action"] for f in plan["files"]} == {"unchanged"}, diff


def test_changed_board_updates_generated_files_with_a_diff_and_rolls_back():
    t, bd, target = first_deploy()
    before = deployed(target)
    b = line_board()
    b["cards"][1]["label"] = "Gather everything"
    save(bd, b)
    plan, diff = D.propose("line-a", target)
    assert plan["state"] == "proposed"
    assert [f["action"] for f in plan["files"] if f["path"].endswith("SKILL.md")] == ["update"]
    assert "-### 1. Gather" in diff and "+### 1. Gather everything" in diff        # unified diff of the change
    plan = D.apply(plan["id"], approve=True, token=plan["token"])
    assert plan["drift"] == [] and "### 1. Gather everything" in deployed(target)
    D.rollback(plan["id"])
    assert deployed(target) == before                                             # restored from the snapshot


def test_rollback_of_a_first_deploy_removes_what_it_created():
    t, bd, target = env()
    save(bd, line_board())
    plan, _ = D.propose("line-a", target)
    plan = D.apply(plan["id"], approve=True, token=plan["token"])
    D.rollback(plan["id"])
    assert not os.path.exists(os.path.join(target, "line-a"))


def test_hand_edit_is_a_conflict_unless_adopted():
    t, bd, target = first_deploy()
    p = os.path.join(target, "line-a", "SKILL.md")
    with open(p, "a") as f:
        f.write("\nmy own note\n")
    b = line_board()
    b["cards"][1]["label"] = "Gather v2"
    save(bd, b)
    plan, diff = D.propose("line-a", target)
    assert plan["state"] == "conflict" and "edited by hand" in diff
    try:
        D.apply(plan["id"], approve=True, token=plan["token"])
    except SystemExit:
        pass
    assert "my own note" in open(p).read()                                        # never overwritten
    plan, diff = D.propose("line-a", target, adopt=True)
    assert plan["state"] == "proposed" and "adopting" in diff
    plan = D.apply(plan["id"], approve=True, token=plan["token"])
    assert "my own note" not in open(p).read()
    D.rollback(plan["id"])
    assert "my own note" in open(p).read()                                        # adoption is reversible


def test_skill_not_written_by_deploy_is_never_touched():
    t, bd, target = env()
    save(bd, line_board())
    os.makedirs(os.path.join(target, "line-a"))
    with open(os.path.join(target, "line-a", "SKILL.md"), "w") as f:
        f.write("---\nname: line-a\n---\nsomeone else's skill\n")
    plan, diff = D.propose("line-a", target)
    assert plan["state"] == "conflict"


def test_changed_source_sop_refuses_apply():
    t, bd, target = env()
    sop = os.path.join(t, "compile.sop.md")
    with open(sop, "w") as f:
        f.write("# Compile\n")
    import hashlib
    sha = hashlib.sha256(open(sop, "rb").read()).hexdigest()[:16]
    b = line_board()
    b["cards"][1]["fields"]["src_path"], b["cards"][1]["fields"]["src_sha"] = sop, sha
    save(bd, b)
    plan, _ = D.propose("line-a", target)
    with open(sop, "a") as f:
        f.write("changed after import\n")
    try:
        D.apply(plan["id"], approve=True, token=plan["token"])
        raise AssertionError("stale source accepted")
    except SystemExit as e:
        assert "source files changed" in str(e)
    assert not os.path.exists(os.path.join(target, "line-a"))


def test_rollback_refuses_after_later_edits():
    t, bd, target = first_deploy()
    plan = json.load(open(os.path.join(os.environ["WB_DEPLOY_DIR"], sorted(os.listdir(os.environ["WB_DEPLOY_DIR"]))[-1], "plan.json")))
    with open(os.path.join(target, "line-a", "SKILL.md"), "a") as f:
        f.write("later edit\n")
    try:
        D.rollback(plan["id"])
        raise AssertionError("rollback over a later edit")
    except SystemExit as e:
        assert "changed after this deploy" in str(e)
    assert "later edit" in deployed(target)


def test_module_cards_reuse_an_installed_skill():
    t, bd, target = env()
    os.makedirs(os.path.join(target, "review-loop"))
    with open(os.path.join(target, "review-loop", "SKILL.md"), "w") as f:
        f.write("---\nname: review-loop\ndescription: draft and approve\n---\n# review-loop\n")
    b = line_board("line-m")
    m1 = card("m1", "step", "draft", 320, ins="raw")
    m2 = card("m2", "gate", "approve", 460, outs="notes", ref={"kind": "rule", "rule": "owner approves", "verb": "deny"})
    for c in (m1, m2):
        c["origin_module"], c["module_version"] = "review-loop", 1
    b["cards"] = [c for c in b["cards"] if c["id"] != "b"] + [m1, m2]
    b["edges"] = [{"from": "a.next", "to": "m1.in"}, {"from": "m1.next", "to": "m2.in"}, {"from": "m2.next", "to": "c.in"}]
    save(bd, b)
    plan, diff = D.propose("line-m", target)
    sk = open(os.path.join(os.environ["WB_DEPLOY_DIR"], plan["id"], "files", "line-m", "SKILL.md")).read()
    assert "Use the skill `review-loop`" in sk and "### 2. review-loop" in sk and "### 3. Write report" in sk
    assert "approve" not in [ln[6:] for ln in sk.splitlines() if ln.startswith("### ")]  # module steps were not inlined
    assert plan["reused"] == ["review-loop"] and "Reused installed skills" in diff
    man = json.loads(open(os.path.join(os.environ["WB_DEPLOY_DIR"], plan["id"], "files", "line-m", "line.json")).read())
    assert man["stations"][1]["module_cards"] == ["m1", "m2"]


def test_module_without_installed_skill_is_inlined():
    files, man = D.compile_line(dict(line_board(), cards=[dict(c, origin_module="nowhere") for c in line_board()["cards"]]), "x", installed={})
    assert man["reused"] == [] and len(man["stations"]) == 3


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
