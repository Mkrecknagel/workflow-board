#!/usr/bin/env python3
"""deploy.py (new-files-only Deploy) and the serve.py same-machine guard.
Every test uses temp boards, a temp deploy dir and a temp target. Run: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_deploy.py"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)


def card(cid, typ, label, x, ins=None, outs=None, ref=None, ports=None):
    f = {}
    if ins:
        f["inputs"] = ins
    if outs:
        f["outputs"] = outs
    c = {"id": cid, "type": typ, "label": label, "immutable": False, "enabled": True, "execution_mode": "observe",
         "ref": ref or {"kind": "task"}, "ports": ports or {"in": ["in"], "out": ["next"]}, "pos": {"x": x, "y": 0}}
    if f:
        c["fields"] = f
    return c


def line_board(name="line-a"):
    # deliberately listed out of order: the edges, not the card order, decide the run order
    cards = [card("c", "step", "Write report", 600, ins="notes", outs="report.md"),
             card("a", "step", "Gather", 0, outs="raw"),
             card("b", "step", "Summarize", 300, ins="raw", outs="notes"),
             card("n", "note", "a note", 900, ref={"kind": "note"}, ports={"in": [], "out": []})]
    return {"schema": "workflow-board/v1", "board": name, "version": 1, "execution_mode": "observe", "saved_at": "x",
            "integrity_hash": None, "cards": cards, "edges": [{"from": "a.next", "to": "b.in"}, {"from": "b.next", "to": "c.in"}]}


def env():
    t = tempfile.mkdtemp(prefix="wb-deploy-test-")
    bd = os.path.join(t, "boards")
    os.makedirs(bd)
    os.environ["WB_BOARD_DIRS"] = bd
    os.environ["WB_DEPLOY_DIR"] = os.path.join(t, "deploy")
    return t, bd, os.path.join(t, "skills")


def save(bd, doc):
    with open(os.path.join(bd, doc["board"] + ".json"), "w") as f:
        json.dump(doc, f)


def test_compile_orders_by_edges_and_carries_contracts():
    import deploy as D
    files, man = D.compile_line(line_board(), "line-a")
    assert [s["label"] for s in man["stations"]] == ["Gather", "Summarize", "Write report"]
    sk = files["line-a/SKILL.md"]
    assert sk.startswith("---\nname: line-a\n") and "outputs: [report.md]" in sk
    assert sk.index("### 1. Gather") < sk.index("### 2. Summarize") < sk.index("### 3. Write report")
    assert "- **Inputs:** raw" in sk and "a note" not in sk                        # notes are not stations
    assert man["contract_missing"] == []


def test_loop_is_refused():
    import deploy as D
    b = line_board()
    b["edges"].append({"from": "c.next", "to": "a.in"})
    try:
        D.order_stations(b)
    except ValueError as e:
        assert "loop" in str(e)
    else:
        raise AssertionError("cycle accepted")


def test_propose_writes_nothing_in_target_and_apply_needs_approval():
    import deploy as D
    t, bd, target = env()
    save(bd, line_board())
    plan, diff = D.propose("line-a", target)
    assert plan["state"] == "proposed" and not os.path.exists(target)               # staged only
    assert "+ create" in diff and "### 1. Gather" in diff
    try:
        D.apply(plan["id"])
    except SystemExit as e:
        assert "--approve" in str(e)
    assert not os.path.exists(target)
    D.apply(plan["id"], approve=True, token=plan["token"])
    assert os.path.isfile(os.path.join(target, "line-a", "SKILL.md"))
    assert json.load(open(os.path.join(target, "line-a", "line.json")))["board"] == "line-a"
    try:
        D.apply(plan["id"], approve=True, token=plan["token"])
    except SystemExit as e:
        assert "already applied" in str(e)


def test_existing_files_are_never_overwritten():
    import deploy as D
    t, bd, target = env()
    save(bd, line_board())
    os.makedirs(os.path.join(target, "line-a"))
    with open(os.path.join(target, "line-a", "SKILL.md"), "w") as f:
        f.write("mine")
    plan, diff = D.propose("line-a", target)
    assert plan["state"] == "conflict" and "CONFLICT" in diff
    try:
        D.apply(plan["id"], approve=True, token=plan["token"])
    except SystemExit:
        pass
    assert open(os.path.join(target, "line-a", "SKILL.md")).read() == "mine"


def test_stale_board_or_racing_file_refuses_apply():
    import deploy as D
    t, bd, target = env()
    save(bd, line_board())
    plan, _ = D.propose("line-a", target)
    b = line_board()
    b["cards"][0]["label"] = "Changed"
    save(bd, b)
    try:
        D.apply(plan["id"], approve=True, token=plan["token"])
    except SystemExit as e:
        assert "board changed" in str(e)
    assert not os.path.exists(os.path.join(target, "line-a"))
    save(bd, line_board())
    plan, _ = D.propose("line-a", target)
    os.makedirs(os.path.join(target, "line-a"))                                     # someone created it meanwhile
    try:
        D.apply(plan["id"], approve=True, token=plan["token"])
    except SystemExit as e:
        assert "appeared" in str(e)
    assert os.listdir(os.path.join(target, "line-a")) == []


def test_generated_skill_is_found_by_the_neutral_scanner():
    import deploy as D
    import scanners as S
    t, bd, target = env()
    save(bd, line_board())
    plan, _ = D.propose("line-a", target)
    D.apply(plan["id"], approve=True, token=plan["token"])
    col = S.Collector()
    S.scan_skill_dir("agents", target, col)
    assert [i["name"] for i in col.items] == ["line-a"]


# ---- serve.py: deploy routes and the same-machine guard ----

def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _server(t, bd):
    port = _port()
    e = dict(os.environ, BOARD_PORT=str(port), WB_BOARD_DIRS=bd, WB_DEPLOY_DIR=os.path.join(t, "deploy"),
             WB_TICKETS_DIR=os.path.join(t, "tickets"), WB_CHECK_ON_SAVE="0")
    p = subprocess.Popen([sys.executable, os.path.join(SRC, "serve.py")], env=e, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/boards", timeout=1).read()
            return p, port
        except Exception:
            time.sleep(0.1)
    p.kill()
    raise RuntimeError("server did not start")


def _post(port, path, body, headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(), method="POST",
                                 headers=dict({"Content-Type": "application/json"}, **(headers or {})))
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_http_propose_apply_and_cross_origin_refused():
    t, bd, target = env()
    save(bd, line_board())
    p, port = _server(t, bd)
    try:
        code, j = _post(port, "/deploy/propose", {"board": "line-a", "target": target}, {"Origin": "https://evil.example"})
        assert code == 403 and "cross-origin" in j["error"]
        code, j = _post(port, "/deploy/propose", {"board": "line-a", "target": target}, {"Host": "evil.example:" + str(port)})
        assert code == 403                                                          # DNS-rebinding host
        code, j = _post(port, "/deploy/propose", {"board": "line-a", "target": target}, {"Origin": f"http://127.0.0.1:{port}"})
        assert code == 200 and j["plan"]["state"] == "proposed" and not os.path.exists(target)
        code, j2 = _post(port, "/deploy/apply", {"id": j["plan"]["id"]})
        assert code == 409 and not os.path.exists(target)                           # approve flag required
        code, j2 = _post(port, "/deploy/apply", {"id": j["plan"]["id"], "approve": True, "token": j["plan"]["token"]})
        assert code == 200 and os.path.isfile(os.path.join(target, "line-a", "SKILL.md"))
        req = urllib.request.Request(f"http://127.0.0.1:{port}/board/line-a", data=b"{}", method="PUT",
                                     headers={"Origin": "https://evil.example", "Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("cross-origin PUT accepted")
        except urllib.error.HTTPError as e:
            assert e.code == 403                                                    # existing write routes are guarded too
    finally:
        p.terminate()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))


def test_request_is_local_allowed_hosts_env():
    import serve
    old = os.environ.pop("WB_ALLOWED_HOSTS", None)
    try:
        h = {"Host": "wb.example.ts.net"}
        assert serve.request_is_local(h, 8765)[0] is False                         # default: loopback only
        os.environ["WB_ALLOWED_HOSTS"] = "wb.example.ts.net, other.example.ts.net:443"
        assert serve.request_is_local(h, 8765)[0] is True
        assert serve.request_is_local({"Host": "wb.example.ts.net:9999", "Origin": "https://wb.example.ts.net"}, 8765)[0] is True
        assert serve.request_is_local({"Host": "other.example.ts.net"}, 8765)[0] is True
        assert serve.request_is_local({"Host": "evil.example"}, 8765)[0] is False
        assert serve.request_is_local({"Host": "wb.example.ts.net", "Origin": "https://evil.example"}, 8765)[0] is False
        assert serve.request_is_local({"Host": "127.0.0.1:8765"}, 8765)[0] is True
    finally:
        os.environ.pop("WB_ALLOWED_HOSTS", None)
        if old is not None:
            os.environ["WB_ALLOWED_HOSTS"] = old


def _refused(fn, *a, **k):
    try:
        fn(*a, **k)
    except SystemExit as e:
        return str(e)
    raise AssertionError("not refused")


def test_proposal_id_is_validated_and_contained():
    import deploy as D
    t, bd, target = env()
    save(bd, line_board())
    plan, _ = D.propose("line-a", target)
    outside = os.path.join(t, "outside")
    os.makedirs(outside)
    with open(os.path.join(outside, "plan.json"), "w") as f:
        json.dump({"state": "proposed", "target": target, "files": []}, f)
    for bad in ("../outside", "..\\outside",outside, "/etc/x", "", "A-b", "20260101T000000Z-../x", None, 7):
        msg = _refused(D.apply, bad, approve=True, token="x")
        assert "valid proposal id" in msg or "outside" in msg, (bad, msg)
    assert not os.path.exists(target)


def test_apply_needs_the_proposal_token():
    import deploy as D
    t, bd, target = env()
    save(bd, line_board())
    plan, _ = D.propose("line-a", target)
    assert "token_sha" in plan and plan["token"] and plan["token"] not in json.dumps(
        json.load(open(os.path.join(os.environ["WB_DEPLOY_DIR"], plan["id"], "plan.json"))))   # only the hash is stored
    for tok in (None, "", "wrong"):
        assert "token" in _refused(D.apply, plan["id"], approve=True, token=tok)
    assert not os.path.exists(target)
    D.apply(plan["id"], approve=True, token=plan["token"])
    assert os.path.isfile(os.path.join(target, "line-a", "SKILL.md"))


def test_diff_shows_every_line_of_a_long_file():
    import deploy as D
    t, bd, target = env()
    n = 160                                                                           # a chain long enough that SKILL.md passes 400 lines
    cards = [card(f"s{i}", "step", f"Station {i}", i * 10, ins=f"in{i}", outs=f"out{i}") for i in range(n)]
    b = dict(line_board(), cards=cards, edges=[{"from": f"s{i}.next", "to": f"s{i+1}.in"} for i in range(n - 1)])
    save(bd, b)
    plan, diff = D.propose("line-a", target)
    shown = D._read(os.path.join(os.environ["WB_DEPLOY_DIR"], plan["id"], "files", "line-a", "SKILL.md")).splitlines()
    assert len(shown) > 400
    assert "    | " + shown[-1] in diff and "Station 159" in diff       # nothing past line 400 is hidden


def test_failed_rename_leaves_target_as_found():
    import deploy as D
    t, bd, target = env()
    b = line_board()
    save(bd, b)
    plan, _ = D.propose("line-a", target)
    real = os.rename
    calls = []

    def flaky(a, c):
        calls.append(c)
        if len(calls) == 2:
            raise OSError("boom")
        return real(a, c)
    # force two new top-level folders so the second rename can fail after the first succeeded
    pdir = os.path.join(os.environ["WB_DEPLOY_DIR"], plan["id"])
    plan_json = os.path.join(pdir, "plan.json")
    doc = json.load(open(plan_json))
    extra = {"path": "zz-extra/SKILL.md", "action": "create", "sha": D._sha("x\n")}
    os.makedirs(os.path.join(pdir, "files", "zz-extra"))
    with open(os.path.join(pdir, "files", "zz-extra", "SKILL.md"), "w", encoding="utf-8") as f:
        f.write("x\n")
    doc["files"].append(extra)
    with open(plan_json, "w") as f:
        json.dump(doc, f)
    os.rename = flaky
    try:
        try:
            D.apply(plan["id"], approve=True, token=plan["token"])
        except OSError:
            pass
        else:
            raise AssertionError("rename failure swallowed")
    finally:
        os.rename = real
    left = [n for n in (os.listdir(target) if os.path.isdir(target) else []) if not n.startswith(".")]
    assert left == []                                                                 # the first folder was taken back out
