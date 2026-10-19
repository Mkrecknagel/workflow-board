#!/usr/bin/env python3
"""PR 5: project board layout, role moves in project.json (projedit.py), and the read-only KB/report routes.
Temp portfolio only. Run: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_project_layout.py"""
import json
import os
import sys
import urllib.error
import urllib.request

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)
sys.path.insert(0, os.path.join(SRC, "tests"))
import importers  # noqa: E402
import projedit as PE  # noqa: E402
from test_deploy import _post, _server  # noqa: E402
from test_wb_init import fixture, w  # noqa: E402


def build(**opts):
    t, pf, sops, sop, kiro, items = fixture()
    return t, pf, items, importers.get("tpm-portfolio").build(pf, dict({"sops": [sops], "items": items}, **opts))


def lanes(b):
    out = {}
    for c in b["cards"]:
        out.setdefault(c["group"], []).append(c)
    return out


def test_every_project_board_has_the_same_lanes():
    *_, boards = build()
    want = {"Status", "Inputs", "TPM agent workflow", "Workflow skills", "Outputs", "Children"}
    for n in ("p-prog", "p-child"):
        assert set(lanes(boards[n])) == want, n                                     # empty lanes still shown
    child = lanes(boards["p-child"])
    assert child["Inputs"][0]["fields"]["role"] == "empty"
    banner = boards["p-prog"]["cards"][0]
    assert banner["type"] == "note" and banner["fields"]["banner"] == "true" and "on track" in banner["fields"]["text"]


def test_tpm_workflow_starts_open_everywhere():
    *_, boards = build(agent_project="prog")
    assert all(boards[n]["group_styles"]["TPM agent workflow"]["collapsed"] is False for n in ("p-prog", "p-child"))


def test_inputs_and_outputs_grouped_one_card_per_tool_with_rows():
    t, pf, sops, sop, kiro, items = fixture()
    pj = os.path.join(pf, "projects", "prog", "project.json")
    d = json.load(open(pj))
    d["sources"]["slack"] = [{"name": "#a"}, {"name": "#b"}]
    d["sources"]["sharepoint"].append({"label": "Plan", "url": "https://x.sharepoint.com/p"})
    d["sources"]["artifacts"] += [{"label": "Deck", "url": "https://x.sharepoint.com/d"}, {"label": "Repo", "url": "https://code.amazon.com/packages/X"}]
    w(pj, d)
    b = importers.get("tpm-portfolio").build(pf, {"sops": [sops], "items": items})["p-prog"]
    L = lanes(b)
    ins = {c["fields"]["tool"]: [r["label"] for r in c["rows"]] for c in L["Inputs"]}
    assert ins == {"Slack": ["#a", "#b"], "SharePoint": ["Home", "Plan"]}
    outs = {c["fields"]["tool"]: [r["pj_key"] for r in c["rows"]] for c in L["Outputs"] if c.get("rows") and c["fields"].get("tool") != "Reports"}
    assert outs == {"SharePoint": ["sources.artifacts[1]"], "Code": ["sources.artifacts[2]"], "Local files": ["sources.artifacts[0]"]}
    runbook = next(c for c in L["Outputs"] if c["fields"].get("tool") == "Local files")["rows"][0]
    assert runbook["drill"].startswith("doc-prog-")                                # a row that opens its own board
    assert all(e["to"] == "st-compile.in" for e in b["edges"] if e["from"].startswith("in-"))


def test_validate_handed_off_and_called_becomes_a_gate():
    t, pf, sops, sop, kiro, items = fixture()
    # SOP-03 hands off to SOP-04 and the compile skill names the validate skill -> Validate closes Compile
    w(sop, open(sop).read().replace("## Steps", "- **Hands off to SOP-04:** every compile ends by validating.\n\n## Steps"))
    w(os.path.join(sops, "04-validate", "04-validate.sop.md"), "# Validate\n\n## Steps\n\n### A\na\n\n### B\nb\n")
    sk = os.path.join(os.path.dirname(sops), "skills")
    w(os.path.join(sk, "fx-validate", "SKILL.md"), "---\nname: fx-validate\ndescription: v\nsop: SOP-04\n---\n# v\n")
    p = os.path.join(sk, "fx-compile", "SKILL.md")
    w(p, open(p).read() + "\nEnds by running fx-validate.\n")
    agent = os.path.join(kiro, "agents", "fx-agent.json")
    a = json.load(open(agent))
    a["resources"].append("skill://" + os.path.join(sk, "fx-validate", "SKILL.md"))
    w(agent, a)
    import library as L
    items = L.scan({"claude": None, "codex": None, "kiro": kiro, "agents": None, "_mcp_json": None, "_tasks": False})
    boards = importers.get("tpm-portfolio").build(pf, {"sops": [sops], "items": items})
    tpm = [c["id"] for c in lanes(boards["p-prog"])["TPM agent workflow"]]
    assert "st-validate" not in tpm
    gate = boards["sop-compile"]["cards"][-1]
    assert gate["type"] == "gate" and gate["fields"]["skill"] == "fx-validate" and gate["drill"]


def test_outputs_kb_reports_and_built_skill():
    t, pf, sops, sop, kiro, items = fixture()
    w(os.path.join(pf, "reports", "2026", "W1-prog-status.md"), "# r\n")
    w(os.path.join(pf, "reports", "2026", "other.md"), "---\nproject: prog\n---\n# r2\n")
    w(os.path.join(pf, "projects", "prog", "kb", "who.md"), "---\nlast_compiled: \"2026-10-01\"\n---\n# Who\n\nMatt owns it.\n")
    boards = importers.get("tpm-portfolio").build(pf, {"sops": [sops], "items": items})
    out = lanes(boards["p-prog"])["Outputs"]
    kb = next(c for c in out if c["id"] == "out-kb")
    assert kb["type"] == "datastore" and kb["drill"] == "kb-prog"
    page = next(c for c in boards["kb-prog"]["cards"] if c["label"] == "who")
    assert page["fields"]["open"] == "/kb/prog/who" and page["fields"]["summary"] == "Matt owns it."
    reps = {r["label"]: r["matched_by"] for c in out if c["fields"].get("tool") == "Reports" for r in c["rows"]}
    assert reps == {"W1-prog-status": "file name", "other": "front matter"}


def test_suggested_skill_from_activity_log():
    t, pf, sops, sop, kiro, items = fixture()
    extra = w(os.path.join(t, "agents-root", "skills", "helper", "SKILL.md"), "---\nname: helper\ndescription: h\n---\n# h\n")
    import library as L
    items = L.scan({"claude": None, "codex": None, "kiro": kiro, "agents": os.path.join(t, "agents-root"), "_mcp_json": None, "_tasks": False})
    w(os.path.join(pf, "activity", "2026-10-01.md"),
      '---\nschema: 1\nalias: "prog"\nskills_used: ["helper", "source-read"]\n---\nDid things.\n')
    boards = importers.get("tpm-portfolio").build(pf, {"sops": [sops], "items": items})
    sug = [c for c in lanes(boards["p-prog"])["Workflow skills"] if c["fields"].get("role") == "suggested"]
    assert [c["ref"]["name"] for c in sug] == ["helper"] and sug[0]["fields"]["skill_path"] == extra
    assert not [c for c in lanes(boards["p-child"])["Workflow skills"] if c["fields"].get("role") == "suggested"]


# ---- projedit ----

_SAVED = {}


def pj_fixture():
    t, pf, sops, sop, kiro, items = fixture()
    for k in ("BOARD_REGISTRY_ROOT", "WB_BOARD_DIRS"):
        _SAVED.setdefault(k, os.environ.get(k))
    os.environ["WORKFLOW_BOARD_ROOT"] = pf
    os.environ.pop("BOARD_REGISTRY_ROOT", None)
    os.environ["WB_DEPLOY_DIR"] = os.path.join(t, "deploy")
    return t, pf, os.path.join(pf, "projects", "prog", "project.json")


def teardown_function(_):
    os.environ.pop("WORKFLOW_BOARD_ROOT", None)
    for k, v in _SAVED.items():  # conftest's empty registry root must hold for every later test
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_move_artifact_to_input_and_back_with_backup():
    t, pf, pj = pj_fixture()
    before = open(pj).read()
    plan, diff = PE.propose(pj, "sources.artifacts[0]", "input:sharepoint")
    assert '"moved_from": "artifacts"' in diff and open(pj).read() == before          # proposal writes nothing
    PE.apply(plan["id"], approve=True)
    d = json.load(open(pj))
    assert d["sources"]["artifacts"] == [] and d["sources"]["sharepoint"][-1]["label"] == "Runbook"
    assert d["sources"]["sharepoint"][-1]["classification"] == "source"
    assert open(os.path.join(os.environ["WB_DEPLOY_DIR"], "projedit", plan["id"], "project.json.bak")).read() == before
    plan, _ = PE.propose(pj, "sources.sharepoint[1]", "output")
    PE.apply(plan["id"], approve=True)
    assert json.load(open(pj))["sources"]["artifacts"][-1]["classification"] == "artifact"


def test_uses_and_built_and_suggested():
    t, pf, pj = pj_fixture()
    sk = w(os.path.join(t, "s", "my-skill", "SKILL.md"), "---\nname: my-skill\ndescription: x\n---\n")
    plan, _ = PE.propose(pj, "new", "uses", "my-skill", sk)
    PE.apply(plan["id"], approve=True)
    assert json.load(open(pj))["workflow_skills"] == [{"name": "my-skill", "path": sk}]
    plan, _ = PE.propose(pj, "workflow_skills[0]", "built")
    PE.apply(plan["id"], approve=True)
    d = json.load(open(pj))
    assert d["workflow_skills"] == [] and d["sources"]["artifacts"][-1]["type"] == "skill"


def test_refusals():
    t, pf, pj = pj_fixture()
    for args, msg in [((pj, "sources.artifacts[9]", "output"), "does not exist"),
                      ((pj, "sources.artifacts[0]", "output"), "already has role"),
                      ((pj, "sources.artifacts[0]", "input:nowhere"), "input category"),
                      ((os.path.join(t, "elsewhere", "project.json"), "sources.artifacts[0]", "output"), "not a project.json under")]:
        try:
            PE.propose(*args)
            raise AssertionError(args)
        except PE.Refused as e:
            assert msg in str(e), (args, str(e))
    plan, _ = PE.propose(pj, "sources.artifacts[0]", "input:wiki")
    try:
        PE.apply(plan["id"])
        raise AssertionError("applied without approve")
    except PE.Refused:
        pass
    with open(pj, "a") as f:
        f.write(" ")
    try:
        PE.apply(plan["id"], approve=True)
        raise AssertionError("stale project.json accepted")
    except PE.Refused as e:
        assert "changed after" in str(e)


def test_http_routes_kb_report_and_role_move():
    t, pf, pj = pj_fixture()
    w(os.path.join(pf, "projects", "prog", "kb", "who.md"), "# Who\n")
    w(os.path.join(pf, "reports", "2026", "W1-prog-status.md"), "# r\n")
    out = os.path.join(t, "boards")
    os.makedirs(out)
    b = importers.get("tpm-portfolio").build(pf, {"items": []})["p-prog"]
    json.dump(b, open(os.path.join(out, "p-prog.json"), "w"))
    os.environ["WB_BOARD_DIRS"] = out
    p, port = _server(t, out)
    try:
        base = f"http://127.0.0.1:{port}"
        assert urllib.request.urlopen(base + "/kb/prog/who").read() == b"# Who\n"
        assert urllib.request.urlopen(base + "/report?p=2026/W1-prog-status.md").read() == b"# r\n"
        for bad in ("/kb/prog/secrets", "/report?p=../portfolio.json", "/report?p=../projects/prog/project.json"):
            try:
                urllib.request.urlopen(base + bad)
                raise AssertionError(bad)
            except urllib.error.HTTPError as e:
                assert e.code == 404, bad
        card = next(c for c in b["cards"] if any(r.get("pj_key") == "sources.artifacts[0]" for r in c.get("rows") or []))
        body = {"board": "p-prog", "card": card["id"], "row": "sources.artifacts[0]", "role": "input:wiki"}
        code, j = _post(port, "/project/propose", body, {"Origin": "https://evil.example"})
        assert code == 403
        code, j = _post(port, "/project/propose", dict(body, row="sources.nope[9]"))
        assert code == 422                                                         # unknown row
        code, j = _post(port, "/project/propose", body)
        assert code == 200 and "moved_from" in j["diff"]
        code, j = _post(port, "/project/apply", {"id": j["plan"]["id"], "approve": True})
        assert code == 200 and json.load(open(pj))["sources"]["wiki"][-1]["label"] == "Runbook"
    finally:
        p.terminate()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
