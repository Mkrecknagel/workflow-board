#!/usr/bin/env python3
"""Static part of the four-harness acceptance gate (tests/harness/four_harness.py --static): a deployed line lands in
each harness's project skills folder, passes the Agent Skills format checks, is found by the scanner, and re-reads with
no drift. The live part (--live kiro,claude,codex,agents) runs the real CLIs and is opt-in.
Run: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_four_harness.py"""
import os
import sys

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SRC, "tests", "harness"))
import four_harness as F  # noqa: E402


def test_static_all_four_harnesses():
    rows = F.static()
    assert [r[0] for r in rows] == ["claude", "codex", "kiro", "agents"]
    bad = [r for r in rows if not r[2]]
    assert not bad, bad


def test_check_run_judges_artifacts(tmp_path):
    import deploy as D
    skills = tmp_path / "skills"
    F.deploy_into(str(skills))
    work = tmp_path / "work"
    work.mkdir()
    (work / "notes.txt").write_text("a\nb\nc\n")
    r = D.check_run(str(skills / F.LINE), str(work))
    assert not r["ok"] and [o["status"] for s in r["stations"] for o in s["outputs"]] == ["present", "MISSING", "MISSING"]
    (work / "count.txt").write_text("3")
    (work / "report.md").write_text("# r")
    assert D.check_run(str(skills / F.LINE), str(work))["ok"]
