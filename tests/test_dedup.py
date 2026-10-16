#!/usr/bin/env python3
"""dedup.py tests (stdlib + pytest-compatible). Everything runs on TEMP COPIES: the real boards/, tickets/,
dedup/ and ~/.claude/sops are never written. Run: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_dedup.py"""
import glob
import json
import os
import shutil
import sys
import tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)
sys.path.insert(0, os.path.join(SRC, "tests"))
import _real  # noqa: E402
import dedup  # noqa: E402
import tickets_store as TS  # noqa: E402

FIX = os.path.join(SRC, "tests", "fixtures", "dedup-demo")
BOARD = "dedup-demo"


def env():
    """Temp boards dir (one fixture board), temp SOP dir (two fixture SOPs), temp out dir; tickets go to a temp store."""
    tmp = tempfile.mkdtemp(prefix="wb-dedup-")
    bd = os.path.join(tmp, "boards")
    os.makedirs(bd)
    shutil.copy(os.path.join(FIX, BOARD + ".json"), bd)
    sd = os.path.join(tmp, "sops")
    os.makedirs(sd)
    for f in sorted(os.listdir(os.path.join(FIX, "sops"))):
        shutil.copy(os.path.join(FIX, "sops", f), sd)
    os.environ["WB_TICKETS_DIR"] = os.path.join(tmp, "tickets")
    return tmp, bd, sd, os.path.join(tmp, "out")


def scan(bd, sd, od):
    return dedup.scan(BOARD, bd, sd, od)  # anchors come from the repo default, dedup/anchors.json


def read(p):
    with open(p, "rb") as f:
        return f.read()


def test_scan_finds_activity_log_cluster():
    tmp, bd, sd, od = env()
    r = scan(bd, sd, od)
    cl = [c for c in r["clusters"] if {"step-01-8", "step-03-8"} <= {m["card_id"] for m in c["members"]}]
    assert len(cl) == 1
    sc = {m["card_id"]: m["scope"] for m in cl[0]["members"]}
    assert sc["step-01-8"] == "whole" and sc["step-03-8"] == "fragment"
    assert cl[0]["anchor"] == "activity-log"
    assert os.path.isfile(os.path.join(od, "candidates.json"))


def test_scan_is_deterministic():
    tmp, bd, sd, od = env()
    scan(bd, sd, od)
    a = read(os.path.join(od, "candidates.json"))
    scan(bd, sd, od)
    assert a == read(os.path.join(od, "candidates.json"))


def test_fixture_precision():
    r = dedup.evaluate_fixture(os.path.join(SRC, "fixtures", "dedup-pairs.json"))
    n = len(json.load(open(os.path.join(SRC, "fixtures", "dedup-pairs.json"), encoding="utf-8"))["pairs"])
    assert n == 10 and r["precision"] >= 0.8, r


def test_tickets_one_per_admitted_cluster_and_suppression():
    tmp, bd, sd, od = env()
    cand = scan(bd, sd, od)
    v = dedup.judge(od)
    admitted = [x["cluster_id"] for x in v["clusters"] if x["equivalence"] >= dedup.ADMIT]
    assert len(admitted) >= 1
    made = dedup.tickets(BOARD, bd, od)
    assert len(made) == len(admitted)
    ts = TS.list_tickets(BOARD)
    assert len(ts) == len(made) and all(t["kind"] == "task" and t["text"].startswith(dedup.TAG) for t in ts)
    assert all(len(t["text"].encode()) <= TS.TEXT_CAP for t in ts)
    assert dedup.tickets(BOARD, bd, od) == []  # idempotent: no duplicate tickets
    t0 = next(t for t in ts if "step-01-8" in t["text"])
    TS.set_status(t0["id"], "done", actor="owner")  # owner closes the proposal without replacing
    r = scan(bd, sd, od)
    supp = json.load(open(os.path.join(od, "suppressions.json"), encoding="utf-8"))
    assert len(supp["suppressed"]) == 1 and supp["suppressed"][0]["ticket_id"] == t0["id"]
    assert not any("step-01-8" in {m["card_id"] for m in c["members"]} and c["anchor"] == "activity-log" for c in r["clusters"])
    scan(bd, sd, od)  # re-run does not add a second row
    assert len(json.load(open(os.path.join(od, "suppressions.json"), encoding="utf-8"))["suppressed"]) == 1


def test_changed_member_text_unsuppresses():
    tmp, bd, sd, od = env()
    scan(bd, sd, od)
    dedup.judge(od)
    dedup.tickets(BOARD, bd, od)
    t0 = next(t for t in TS.list_tickets(BOARD) if "step-01-8" in t["text"])
    TS.set_status(t0["id"], "done", actor="owner")
    f = glob.glob(os.path.join(sd, "01-*.sop.md"))[0]
    s = open(f, encoding="utf-8").read().replace("Record the registration in the activity log.", "Record the registration in the activity log now.")
    open(f, "w", encoding="utf-8", newline="").write(s)
    r = scan(bd, sd, od)
    assert any("step-01-8" in {m["card_id"] for m in c["members"]} and c["anchor"] == "activity-log" for c in r["clusters"])


def test_external_judge_is_pluggable_offline():
    tmp, bd, sd, od = env()
    scan(bd, sd, od)
    cmd = f'"{sys.executable}" -c "import sys,json; d=json.load(sys.stdin); print(json.dumps({{\'clusters\':[{{\'cluster_id\':c[\'cluster_id\'],\'equivalence\':0.1}} for c in d[\'clusters\']]}}))"'
    v = dedup.judge(od, judge_cmd=cmd)
    assert v["clusters"] and all(x["equivalence"] == 0.1 and x["judge"] == "external" for x in v["clusters"] if not x.get("bypass"))
    assert dedup.tickets(BOARD, bd, od) == []


if __name__ == "__main__":
    fails = 0
    for n, fn in sorted(globals().items()):
        if n.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", n)
            except Exception as e:  # noqa: BLE001
                fails += 1
                print("FAIL", n, repr(e)[:200])
    sys.exit(1 if fails else 0)
