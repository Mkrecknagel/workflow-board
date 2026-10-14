#!/usr/bin/env python3
"""Fallback `resource` card tests: schema/validator rules and library templates.
Run: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_resource_card.py"""
import copy
import json
import os
import sys
import tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)
import library as L  # noqa: E402
import validate as V  # noqa: E402


def board(*cards):
    return {"schema": "workflow-board/v1", "board": "rc", "version": 1, "execution_mode": "observe",
            "saved_at": "2026-10-14T00:00:00Z", "integrity_hash": None, "cards": list(cards), "edges": []}


RES = {"id": "rs-1", "type": "resource", "label": "a plugin", "immutable": False, "enabled": True, "execution_mode": "observe",
       "ref": {"kind": "plugin", "name": "a-plugin", "harness": "claude"}, "ports": {"in": ["in"], "out": ["out"]},
       "pos": {"x": 0, "y": 0}}


def invalid(doc):
    try:
        V.validate_doc(doc)
    except V.Invalid as e:
        return str(e)
    return None


def test_resource_card_validates():
    assert invalid(board(copy.deepcopy(RES))) is None


def test_resource_needs_kind_and_name():
    for k in ("kind", "name"):
        c = copy.deepcopy(RES)
        del c["ref"][k]
        assert f"missing '{k}'" in (invalid(board(c)) or "")


def test_resource_is_observe_only():
    c = copy.deepcopy(RES)
    c["execution_mode"] = "enforce"
    assert "enforce is legal only on" in (invalid(board(c)) or "")
    c = copy.deepcopy(RES)
    c["run_mode"] = "background"
    assert "run_mode is only allowed" in (invalid(board(c)) or "")


def test_unknown_type_is_still_rejected_by_the_validator():
    c = copy.deepcopy(RES)
    c["type"] = "future-kind"
    assert "not in" in (invalid(board(c)) or "")


def test_every_library_item_has_a_card_template():
    tmp = tempfile.mkdtemp(prefix="wb-rc-")
    claude = os.path.join(tmp, "claude")
    os.makedirs(os.path.join(claude, "plugins"))
    with open(os.path.join(claude, "plugins", "installed_plugins.json"), "w") as f:
        json.dump({"plugins": {"demo-plugin@market": {}}}, f)
    codex = os.path.join(tmp, "codex")
    os.makedirs(os.path.join(codex, "agents"))
    with open(os.path.join(codex, "hooks.json"), "w") as f:
        json.dump({"hooks": {"Stop": [{"hooks": []}]}}, f)
    with open(os.path.join(codex, "agents", "reviewer.toml"), "w") as f:
        f.write('description = "Reviews code"\n')
    items = L.scan({"claude": claude, "codex": codex, "kiro": None, "agents": None, "_mcp_json": None, "_tasks": False})
    plain = [i for i in items if i["kind"] != "module"]
    assert plain and all(i["card_template"] for i in plain)
    res = {i["name"]: i["card_template"] for i in plain if i["card_template"]["type"] == "resource"}
    assert {"demo-plugin@market", "Stop", "reviewer"} <= set(res)
    assert res["reviewer"]["ref"] == {"kind": "agent", "name": "reviewer", "harness": "codex", "path": res["reviewer"]["ref"]["path"]}
    for name, tpl in res.items():  # a card built from each template validates
        c = dict(tpl, id="rs-x", immutable=False, enabled=True, pos={"x": 0, "y": 0})
        assert invalid(board(c)) is None, (name, invalid(board(c)))


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
