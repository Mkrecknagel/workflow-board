"""B1 tests: ticket store + CLI. Uses a temp WB_TICKETS_DIR; never touches real tickets/ or boards/."""
import os
import subprocess
import sys
import tempfile

import pytest

WB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WB)
import tickets_store as S  # noqa: E402


@pytest.fixture(autouse=True)
def tdir(monkeypatch):
    d = tempfile.mkdtemp(prefix="wbtix-")
    monkeypatch.setenv("WB_TICKETS_DIR", d)
    return d


def cli(*args, env=None):
    e = dict(os.environ)
    p = subprocess.run([sys.executable, os.path.join(WB, "tickets.py"), *args], capture_output=True, text=True, env=e, cwd=WB)
    return p.returncode, p.stdout, p.stderr


def submitted(text="hello", author="owner", kind=None):
    t = S.create("b1", "cardA", text, author, kind)
    if author == "owner":
        S.send(t["id"])
    return t["id"]


def test_create_send_claim_flow(tdir):
    t = S.create("b1", "cardA", "please tweak label")
    assert t["status"] == "draft" and t["author"] == "owner"
    S.send(t["id"])
    assert S.pending()["count"] == 1
    c = S.claim(t["id"], "sessA")
    assert c["status"] == "in_progress" and c["claim"]["session"] == "sessA"
    assert S.pending()["count"] == 0
    assert [h["to"] for h in c["history"]] == ["draft", "submitted", "in_progress"]


def test_second_claim_other_process_eexist(tdir):
    tid = submitted()
    rc1, out1, _ = cli("claim", tid, "--session", "p1")
    rc2, _, err2 = cli("claim", tid, "--session", "p2")
    assert rc1 == 0, out1
    assert rc2 == 2 and "already claimed" in err2 and "p1" in err2


def test_reply_and_done(tdir):
    tid = submitted()
    S.claim(tid, "s")
    assert cli("status", tid, "done", "--session", "s")[0] == 1  # no reply yet
    assert cli("reply", tid, "--text", "fixed", "--link", "abc123", "--session", "s")[0] == 0
    rc, out, err = cli("status", tid, "done", "--session", "s")
    assert rc == 0, err
    _, t, _ = S.find(tid)
    assert t["status"] == "done" and t["links"] == ["abc123"] and t["claim"] is None
    assert not os.path.exists(os.path.join(tdir, "locks", f"{tid}.lock"))


def test_done_needs_links_or_no_change(tdir):
    tid = submitted()
    S.claim(tid, "s")
    S.reply(tid, "looked, nothing to change", "agent", "s")
    assert cli("status", tid, "done", "--session", "s")[0] == 1
    assert cli("status", tid, "done", "--session", "s", "--no-change")[0] == 0


def test_archive_moves_to_jsonl(tdir):
    tid = submitted()
    S.claim(tid, "s")
    S.reply(tid, "ok", "agent", "s", ["x"])
    S.set_status(tid, "done", "agent", "s")
    S.archive(tid)
    live = open(os.path.join(tdir, "b1.json"), encoding="utf-8").read()
    assert tid not in live
    arch = open(os.path.join(tdir, "archive", "b1.jsonl"), encoding="utf-8").read()
    assert tid in arch and arch.count("\n") == 1
    assert [t["id"] for t in S.list_archive("b1")] == [tid]
    S.unarchive(tid)
    assert tid in open(os.path.join(tdir, "b1.json"), encoding="utf-8").read()


def test_archive_requires_done_and_owner(tdir):
    tid = submitted()
    with pytest.raises(S.TicketError):
        S.archive(tid)
    with pytest.raises(S.TicketError):
        S.archive(tid, actor="agent")


def test_governance_done_without_approve_fails(tdir):
    tid = submitted("please edit ~/.claude/CLAUDE.md agents_per_run")
    _, t, _ = S.find(tid)
    assert t["kind"] == "governance"
    S.claim(tid, "s")
    S.reply(tid, "PROPOSAL: change X", "agent", "s", ["l"])
    rc, _, err = cli("status", tid, "done", "--session", "s")
    assert rc != 0 and "approve" in err
    assert cli("status", tid, "blocked", "--session", "s")[0] == 0
    assert cli("approve", tid, "--quote", "approve it")[0] == 0
    # blocked -> done is not an agent transition; owner re-queues, agent re-claims
    S.reply(tid, "go", "owner")
    S.claim(tid, "s2")
    S.reply(tid, "applied", "agent", "s2", ["l2"])
    assert cli("status", tid, "done", "--session", "s2")[0] == 0


def test_guest_send_refused(tdir):
    t = S.create("b1", "cardA", "hi from stranger", author="guest")
    with pytest.raises(S.TicketError) as e:
        S.send(t["id"])
    assert e.value.code == "refused"
    assert S.pending()["count"] == 0


def test_agent_cannot_edit_or_archive_or_touch_draft(tdir):
    t = S.create("b1", "cardA", "draft one")
    with pytest.raises(S.TicketError):
        S.claim(t["id"], "s")  # draft is not claimable
    with pytest.raises(S.TicketError):
        S.set_status(t["id"], "done", "agent", "s")


def test_blocked_requires_reply_and_owner_reply_requeues(tdir):
    tid = submitted()
    S.claim(tid, "s")
    with pytest.raises(S.TicketError):
        S.set_status(tid, "blocked", "agent", "s")
    S.reply(tid, "need info", "agent", "s")
    S.set_status(tid, "blocked", "agent", "s")
    S.reply(tid, "here you go", "owner")
    _, t, _ = S.find(tid)
    assert t["status"] == "submitted" and S.pending()["count"] == 1


def test_sweep_ttl(tdir):
    import time
    tid = submitted()
    S.claim(tid, "s")
    assert S.sweep() == []
    assert S.sweep(now_ts=time.time() + 31 * 60) == [tid]
    _, t, _ = S.find(tid)
    assert t["status"] == "submitted" and t["claim"] is None and S.pending()["count"] == 1


def test_compute_author(tdir):
    import json
    with open(os.path.join(tdir, "config.json"), "w") as f:
        json.dump({"owner_login": "me@x"}, f)
    assert S.compute_author("127.0.0.1", {}) == "owner"
    assert S.compute_author("127.0.0.1", {"Tailscale-User-Login": "me@x"}) == "owner"
    assert S.compute_author("127.0.0.1", {"Tailscale-User-Login": "stranger@x"}) == "guest"
    assert S.compute_author("10.0.0.5", {}) == "guest"


def test_text_cap_and_classify(tdir):
    with pytest.raises(S.TicketError):
        S.create("b1", "c", "x" * 9000)
    assert S.classify("edit modules/foo.yaml") == "governance"
    assert S.classify("change label color") == "note"
