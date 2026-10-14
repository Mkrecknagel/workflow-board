#!/usr/bin/env python3
"""Harness-neutral scanner tests (scanners.py, library.py, validate.py ref resolution).
Every test builds its own synthetic install under a temp dir; no real ~/.kiro, ~/.agents or ~/.claude is read.
Run: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_scanners.py"""
import json
import os
import subprocess
import sys
import tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)
import library as L  # noqa: E402
import scanners as S  # noqa: E402
import validate as V  # noqa: E402

SECRET = "sk-THIS-IS-A-FAKE-SECRET-0123456789abcdef"


def w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def skill(d, name, desc="does a thing"):
    return w(os.path.join(d, name, "SKILL.md"), f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\n")


def kiro_root():
    """A Kiro install shaped like a real one: skills/, nested steering, agent JSON with embedded hooks, MCP servers and
    skill:// / file:// resources pointing OUTSIDE the root (as package-managed agents do)."""
    tmp = tempfile.mkdtemp(prefix="wb-kiro-test-")
    root, pkg = os.path.join(tmp, "kiro"), os.path.join(tmp, "pkg")
    skill(os.path.join(root, "skills"), "local-skill", "Local Kiro skill")
    w(os.path.join(root, "steering", "team", "style.md"), "# House style\n")
    w(os.path.join(root, "settings", "mcp.json"), json.dumps({"mcpServers": {"global-mcp": {"command": "x", "env": {"TOKEN": SECRET}}}}))
    pkg_skill = skill(os.path.join(pkg, "skills"), "pkg-skill", "Packaged skill")
    w(os.path.join(pkg, "context", "a-ctx.md"), "# Context A\n")
    w(os.path.join(pkg, "context", "b-ctx.md"), "# Context B\n")
    hook = w(os.path.join(pkg, "hooks", "gate.sh"), "#!/bin/sh\nexit 0\n")
    agent = {"name": "helper", "description": "Helper agent",
             "hooks": {"agentSpawn": [{"command": f"{hook} --token {SECRET}"}],
                       "stop": [{"command": f"echo done --key {SECRET}"}]},
             "mcpServers": {"global-mcp": {"command": "x", "args": [SECRET]}, "agent-mcp": {"command": "y"}},
             "resources": [f"skill://{pkg_skill}", f"file://{pkg}/context/*-ctx.md", "file://README.md", "https://example.com/x"]}
    w(os.path.join(root, "agents", "helper.json"), json.dumps(agent))
    w(os.path.join(root, "agents", "broken.json"), "{not json")
    return tmp, root, hook


def kiro_items(root):
    col = S.Collector()
    S.scan_kiro(root, col)
    return col.items


def by(items, kind, name):
    return [i for i in items if i["kind"] == kind and i["name"] == name]


def test_kiro_finds_skills_steering_agents_hooks_mcp():
    _, root, hook = kiro_root()
    it = kiro_items(root)
    assert by(it, "skill", "local-skill") and by(it, "skill", "local-skill")[0]["subkind"] == "skill"
    assert by(it, "skill", "style")[0]["subkind"] == "steering"            # nested steering is found
    assert by(it, "skill", "pkg-skill")[0]["used_by"] == ["helper"]        # skill:// resource outside the root
    assert by(it, "skill", "a-ctx") and by(it, "skill", "b-ctx")           # file:// glob resource
    assert by(it, "agent", "helper")[0]["card_template"]["ref"]["seat"] == "kiro"
    hooks = [i for i in it if i["kind"] == "hook"]
    assert {h["event"] for h in hooks} == {"agentSpawn", "stop"}
    spawn = [h for h in hooks if h["event"] == "agentSpawn"][0]
    assert spawn["path"] == S._norm(hook)                                  # the script, not the command line
    stop = [h for h in hooks if h["event"] == "stop"][0]
    assert stop["path"].endswith("agents/helper.json")                    # non-script command -> the agent file
    mcp = by(it, "mcp", "global-mcp")
    assert len(mcp) == 1 and mcp[0]["used_by"] == ["helper"]               # one record per server name
    assert by(it, "mcp", "agent-mcp")
    assert not by(it, "agent", "broken")                                   # unreadable agent file is skipped


def test_no_secrets_or_command_lines_leak():
    _, root, _ = kiro_root()
    blob = json.dumps(kiro_items(root))
    assert SECRET not in blob
    assert "--token" not in blob and "echo done" not in blob


def test_relative_and_non_file_resources_are_skipped():
    _, root, _ = kiro_root()
    names = {i["name"] for i in kiro_items(root)}
    assert "readme" not in names and "x" not in names


def test_generic_agents_root():
    tmp = tempfile.mkdtemp(prefix="wb-agents-test-")
    skill(os.path.join(tmp, "skills"), "portable-skill")
    w(os.path.join(tmp, "AGENTS.md"), "# Project rules\n")
    col = S.Collector()
    S.scan_generic(tmp, col)
    kinds = {(i["name"], i.get("subkind")) for i in col.items}
    assert ("portable-skill", "skill") in kinds
    assert any(sk == "instructions" for _, sk in kinds)
    assert all(i["harness"] == "agents" for i in col.items)


def test_mcp_config_names_only():
    tmp = tempfile.mkdtemp(prefix="wb-mcp-test-")
    p = w(os.path.join(tmp, "mcp.json"), json.dumps({"mcpServers": {"a": {"env": {"K": SECRET}}, "b": {}}}))
    col = S.Collector()
    S.scan_mcp_config("any", p, col)
    assert [i["name"] for i in col.items] == ["a", "b"] and SECRET not in json.dumps(col.items)
    S.scan_mcp_config("any", os.path.join(tmp, "missing.json"), col)      # missing file is not an error


def test_missing_roots_are_empty():
    col = S.Collector()
    S.scan_kiro("/nonexistent/wb", col)
    S.scan_generic(None, col)
    assert col.items == []


def test_library_records_match_item_schema_and_ids_unique():
    tmp, root, _ = kiro_root()
    ag = os.path.join(tmp, "agents-root")
    skill(os.path.join(ag, "skills"), "local-skill")                     # same name as the Kiro skill, different harness
    items = L.scan({"claude": None, "codex": None, "kiro": root, "agents": ag, "_mcp_json": None, "_tasks": False})
    schema = json.load(open(os.path.join(SRC, "schema", "item.schema.json"), encoding="utf-8"))
    for i in items:
        V.check(i, schema, i["id"])
    assert len({i["id"] for i in items}) == len(items)
    assert {i["harness"] for i in items} >= {"kiro", "agents", "workflow-board"}


def _board(cards):
    return {"schema": "workflow-board/v1", "board": "scan-test", "version": 1, "execution_mode": "observe",
            "saved_at": "2026-10-14T00:00:00Z", "integrity_hash": None, "cards": cards, "edges": []}


def _card(cid, tpl):
    c = dict(tpl)
    c.update({"id": cid, "immutable": False, "enabled": True, "pos": {"x": 0, "y": 0}})
    c.pop("fields", None)
    return c


def test_cards_from_kiro_items_validate_and_unknowns_still_fail():
    tmp, root, _ = kiro_root()
    env = dict(os.environ, WB_KIRO_DIR=root, WB_AGENTS_DIR=os.path.join(tmp, "none"), WB_CODEX_DIR=os.path.join(tmp, "none"))
    items = kiro_items(root)
    cards = [_card("sk", by(items, "skill", "pkg-skill")[0]["card_template"]),
             _card("hk", [i for i in items if i["kind"] == "hook" and i["event"] == "agentSpawn"][0]["card_template"]),
             _card("mc", by(items, "mcp", "agent-mcp")[0]["card_template"])]
    good = w(os.path.join(tmp, "good.json"), json.dumps(_board(cards)))
    r = subprocess.run([sys.executable, os.path.join(SRC, "validate.py"), good], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for bad_ref in ({"type": "skill", "ref": {"name": "no-such-skill"}},
                    {"type": "hook", "ref": {"event": "stop", "file": os.path.join(root, "..", "pkg", "hooks", "other.sh")}},
                    {"type": "tool", "ref": {"name": "@no-such-mcp"}}):
        tpl = dict(cards[0], **bad_ref)
        if bad_ref["type"] == "hook":
            tpl["ports"] = {"in": ["event"], "out": ["verdict"]}
        bad = w(os.path.join(tmp, "bad.json"), json.dumps(_board([_card("x", tpl)])))
        r = subprocess.run([sys.executable, os.path.join(SRC, "validate.py"), bad], env=env, capture_output=True, text=True)
        assert r.returncode == 2, (bad_ref, r.stdout, r.stderr)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
