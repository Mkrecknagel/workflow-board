#!/usr/bin/env python3
"""wb_init.py + importers/ tests on a synthetic portfolio, SOP folder and Kiro root (nothing real is read or written).
Run: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_wb_init.py"""
import json
import os
import subprocess
import sys
import tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)
import importers  # noqa: E402
import library as L  # noqa: E402
import validate as V  # noqa: E402
import wb_init  # noqa: E402

SOP = """# SOP-03: Compile

## Parameters

- **project** (required): project alias
- **tier** (optional): compile tier

## Outputs

- `kb/_index.md`: regenerated index

## Steps

### 1. Lock
Take the write lock.

### 2. Sweep
Read every registered source.

#### 2.1 Slack
Read Slack.

#### 2.2 Email
Read email.

### 3. Write
Write the pages.

## Troubleshooting
Nothing here is a step.
"""


def w(p, text):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text if isinstance(text, str) else json.dumps(text))
    return p


def fixture():
    t = tempfile.mkdtemp(prefix="wb-init-test-")
    pf = os.path.join(t, "portfolio")
    w(os.path.join(pf, "portfolio.json"), {"projects": [
        {"alias": "prog", "name": "Program", "state": "ACTIVE"},
        {"alias": "child", "name": "Child", "state": "ACTIVE", "parent": "prog"},
        {"alias": "old", "name": "Old", "state": "ARCHIVED"}]})
    doc = w(os.path.join(t, "docs", "runbook.md"), "# Runbook\n\n## Steps\n\n### Prepare\nGet ready.\n\n### Ship\nShip it.\n")
    w(os.path.join(pf, "projects", "prog", "project.json"), {"children": ["child"], "context": "ctx",
        "sources": {"sharepoint": [{"label": "Home", "url": "https://example.invalid/x"}],
                    "artifacts": [{"label": "Runbook", "url": "file://" + doc}]}})
    w(os.path.join(pf, "projects", "prog", "kb", "_index.md"), "---\nhealth: on_track\nnext_action: \"do it\"\n---\n# x\n")
    w(os.path.join(pf, "projects", "child", "project.json"), {"sources": {}})
    sops = os.path.join(t, "pkg", "agent-sops")
    sop = w(os.path.join(sops, "03-compile", "03-compile.sop.md"), SOP)
    w(os.path.join(sops, "07-extra", "07-extra.sop.md"), "# Extra\n\n## Steps\n\n### A\na\n\n### B\nb\n")
    skill = w(os.path.join(t, "pkg", "skills", "fx-compile", "SKILL.md"),
              "---\nname: fx-compile\ndescription: compiles\nsop: SOP-03\ninputs: project\noutputs: kb\n---\n# fx-compile\n")
    hook = w(os.path.join(t, "pkg", "hooks", "capture.sh"), "#!/bin/sh\n")
    kiro = os.path.join(t, "kiro")
    w(os.path.join(kiro, "agents", "fx-agent.json"), {"name": "fx-agent", "description": "fixture agent",
        "resources": ["skill://" + skill], "hooks": {"userPromptSubmit": [{"command": hook}]},
        "mcpServers": {"fx-mcp": {"command": "x"}}})
    items = L.scan({"claude": None, "codex": None, "kiro": kiro, "agents": None, "_mcp_json": None, "_tasks": False})
    return t, pf, sops, sop, kiro, items


def build():
    t, pf, sops, sop, kiro, items = fixture()
    return t, pf, sops, sop, kiro, importers.get("tpm-portfolio").build(pf, {"sops": [sops], "items": items})


def test_detect_and_board_set():
    t, pf, sops, sop, kiro, boards = build()
    assert importers.detect(pf).NAME == "tpm-portfolio" and importers.detect(t) is None
    assert {"portfolio", "p-prog", "p-child", "sop-compile", "sop-compile-2", "sop-07-extra", "runtime", "kb-prog"} <= set(boards)
    assert any(n.startswith("doc-prog-sources-artifacts") for n in boards)            # the local runbook artifact opens
    assert "p-old" not in boards                                                      # archived skipped by default


def test_provenance_on_every_generated_card():
    *_, boards = build()
    for name, b in boards.items():
        assert b["generated"]["importer"] == "tpm-portfolio" and b["generated"]["sources"], name
        for c in b["cards"]:
            f = c.get("fields", {})
            if c["id"] == "runtime":
                continue
            assert f.get("src_path") and f.get("src_anchor") and len(f.get("src_sha", "")) == 16, (name, c["id"], f)


def test_contracts_parsed_never_invented_and_drift_flagged():
    t, pf, sops, sop, kiro, boards = build()
    st = {c["id"]: c for c in boards["p-prog"]["cards"]}
    comp = st["st-compile"]["fields"]
    assert comp["inputs"] == "project" and comp["outputs"] == "kb"                  # the skill's contract wins...
    assert "different inputs/outputs" in comp["drift"]                              # ...and differing from its SOP is drift
    assert st["st-register"]["fields"]["contract"] == "missing"                       # no SOP and no skill for this step
    steps = [c for c in boards["sop-compile"]["cards"]]
    assert [c["label"] for c in steps] == ["1. Lock", "2. Sweep", "3. Write"]          # Troubleshooting is not a step
    assert all(c["fields"]["contract"] == "missing" for c in steps)                   # headings never become contracts
    assert steps[1]["drill"] == "sop-compile-2"


def test_one_card_per_station_typed_by_what_runs_it():
    *_, boards = build()
    cards = boards["p-prog"]["cards"]
    tpm = [c for c in cards if c["group"] == "TPM agent workflow"]
    assert [c["id"] for c in tpm] == ["agent", "st-register", "st-log", "st-compile", "st-validate", "st-report"]
    comp = next(c for c in tpm if c["id"] == "st-compile")
    assert comp["type"] == "skill" and comp["ref"]["name"] == "fx-compile" and comp["drill"] == "sop-compile"
    assert next(c for c in tpm if c["id"] == "st-register")["type"] == "step"           # nothing runs it: grey
    assert not [c for c in cards if c["type"] == "skill" and c["group"] != "TPM agent workflow" and c["ref"].get("name") == "fx-compile"]
    rt = {c["type"] for c in boards["runtime"]["cards"]}
    assert {"agent", "hook", "tool"} <= rt


def run_cli(pf, sops, kiro, out, *extra):
    env = dict(os.environ, WB_KIRO_DIR=kiro, WB_BOARD_DIRS="")
    return subprocess.run([sys.executable, os.path.join(SRC, "wb_init.py"), "--from", pf, "--sops", sops, "--out", out, *extra],
                          env=env, capture_output=True, text=True)


def test_cli_writes_valid_boards_and_is_idempotent():
    t, pf, sops, sop, kiro, _ = fixture()
    out = os.path.join(t, "out")
    r = run_cli(pf, sops, kiro, out)
    assert r.returncode == 0, r.stderr
    assert "new" in r.stdout and os.path.isfile(os.path.join(out, "portfolio.json"))
    r2 = run_cli(pf, sops, kiro, out)
    assert r2.returncode == 0 and " 0 new, 0 updated" in r2.stdout, r2.stdout


def test_no_clobber_keeps_edits_layout_and_notes():
    t, pf, sops, sop, kiro, _ = fixture()
    out = os.path.join(t, "out")
    assert run_cli(pf, sops, kiro, out).returncode == 0
    p_prog, p_child = os.path.join(out, "p-prog.json"), os.path.join(out, "p-child.json")
    a = json.load(open(p_prog))
    moved = next(c for c in a["cards"] if c["id"] == "st-compile")
    moved["pos"] = {"x": 777, "y": 777}
    a["cards"].append({"id": "my-note", "type": "note", "label": "n", "immutable": False, "enabled": True, "execution_mode": "observe",
                       "ref": {"kind": "note"}, "ports": {"in": [], "out": []}, "pos": {"x": 0, "y": 0}, "fields": {"text": "mine"}})
    json.dump(a, open(p_prog, "w"))
    b = json.load(open(p_child))
    edited = next(c for c in b["cards"] if c["type"] != "note")
    edited["label"] = "EDITED BY USER"
    json.dump(b, open(p_child, "w"))
    w(sop, SOP.replace("### 3. Write", "### 3. Write all pages"))                 # source changed -> boards must update
    r = run_cli(pf, sops, kiro, out)
    assert r.returncode == 3, (r.stdout, r.stderr)
    assert any(c["label"] == "EDITED BY USER" for c in json.load(open(p_child))["cards"])  # edited board kept
    conf = json.load(open(os.path.join(out, ".wb-init-conflicts.json")))["conflicts"]
    assert [c["board"] for c in conf] == ["p-child"]
    a2 = json.load(open(p_prog))
    assert next(c for c in a2["cards"] if c["id"] == "st-compile")["pos"] == {"x": 777, "y": 777}  # layout carried over
    assert any(c["id"] == "my-note" for c in a2["cards"])                          # sticky note carried over
    assert "3. Write all pages" in [c["label"] for c in json.load(open(os.path.join(out, "sop-compile.json")))["cards"]]
    r = run_cli(pf, sops, kiro, out, "--force")
    assert r.returncode == 0 and not any(c["label"] == "EDITED BY USER" for c in json.load(open(p_child))["cards"])
    assert os.listdir(os.path.join(out, ".wb-init-backup"))                       # old version backed up
    assert not os.path.exists(os.path.join(out, ".wb-init-conflicts.json"))


def test_hand_made_board_with_same_name_is_never_overwritten():
    t, pf, sops, sop, kiro, _ = fixture()
    out = os.path.join(t, "out")
    os.makedirs(out)
    mine = {"schema": "workflow-board/v1", "board": "portfolio", "version": 1, "execution_mode": "observe",
            "saved_at": "x", "integrity_hash": None, "cards": [], "edges": []}
    w(os.path.join(out, "portfolio.json"), mine)
    r = run_cli(pf, sops, kiro, out)
    assert r.returncode == 3 and json.load(open(os.path.join(out, "portfolio.json"))) == mine


def test_registry_finds_boards_in_wb_board_dirs():
    import registry
    t = tempfile.mkdtemp(prefix="wb-dirs-")
    w(os.path.join(t, "gen-x.json"), {"board": "gen-x"})
    old = os.environ.get("WB_BOARD_DIRS")
    os.environ["WB_BOARD_DIRS"] = t
    try:
        paths, _ = registry.scan(os.path.join(SRC, "boards"), root=tempfile.mkdtemp())
        assert paths.get("gen-x") == os.path.join(t, "gen-x.json")
        assert registry.resolve("gen-x", os.path.join(SRC, "boards"), root=tempfile.mkdtemp()) == os.path.join(t, "gen-x.json")
    finally:
        if old is None:
            os.environ.pop("WB_BOARD_DIRS", None)
        else:
            os.environ["WB_BOARD_DIRS"] = old


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))


def test_importer_finds_nested_and_configured_project_folders_and_slugs_alias():
    t = tempfile.mkdtemp(prefix="wb-init-layout-")
    root = os.path.join(t, "vault")
    w(os.path.join(root, "config.json"), {"paths": {"projects": "4-projects"}})
    w(os.path.join(root, "portfolio.json"), {"projects": [
        {"alias": "top", "name": "Top", "state": "ACTIVE"},
        {"alias": "kid", "name": "Kid", "state": "ACTIVE", "parent": "top"},
        {"alias": "../../escape", "name": "Bad", "state": "ACTIVE"}]})
    w(os.path.join(root, "4-projects", "top", "project.json"), {"context": "top ctx", "children": ["kid"]})
    w(os.path.join(root, "4-projects", "top", "0-kb", "_index.md"), "---\nhealth: on_track\nnext_action: \"go\"\n---\n")
    w(os.path.join(root, "4-projects", "top", "kid", "project.json"), {"context": "kid ctx"})
    w(os.path.join(t, "escape", "project.json"), {"context": "OUTSIDE"})                 # must never be read
    imp = importers.get("tpm-portfolio")
    assert imp.detect(root)
    boards = imp.build(root, {"items": []})
    about = [c for c in boards["p-top"]["cards"] if c["id"] == "status"][0]
    assert about["fields"]["context"] == "top ctx" and about["fields"]["health"] == "on_track"   # configured folder + 0-kb
    kid = [c for c in boards["p-kid"]["cards"] if c["id"] == "status"][0]
    assert kid["fields"]["context"] == "kid ctx"                                                   # nested child found by walking
    bad = [c for c in boards[[n for n in boards if "escape" in n][0]]["cards"] if c["id"] == "status"][0]
    assert bad["fields"].get("context") is None and "OUTSIDE" not in json.dumps(boards)                                                        # alias slugged, no traversal


def test_atomic_write_leaves_no_temp_file_on_failure():
    d = tempfile.mkdtemp(prefix="wb-atomic-")
    try:
        wb_init._atomic_write(os.path.join(d, "x.json"), {"bad": object()})
    except TypeError:
        pass
    assert os.listdir(d) == []
    wb_init._atomic_write(os.path.join(d, "x.json"), {"ok": 1})
    assert os.listdir(d) == ["x.json"]


def test_registered_board_wins_over_wb_board_dirs_board_of_same_name():
    import registry as REG
    t = tempfile.mkdtemp(prefix="wb-prec-")
    board = {"schema": "workflow-board/v1", "board": "same", "cards": [], "edges": []}
    reg_board = w(os.path.join(t, "ws", "same.json"), board)
    gen_dir = os.path.join(t, "gen")
    w(os.path.join(gen_dir, "same.json"), board)
    root = os.path.join(t, "root")
    w(os.path.join(root, "projects", "a", "project.json"),
      {"sources": {"artifacts": [{"type": "workflow-board", "board": "same", "url": reg_board}]}})
    old = os.environ.get("WB_BOARD_DIRS")
    os.environ["WB_BOARD_DIRS"] = gen_dir
    try:
        paths, errs = REG.scan(os.path.join(t, "engine"), root)
        assert os.path.samefile(paths["same"], reg_board)
        assert any("duplicate board name 'same'" in e for e in errs)
        assert os.path.samefile(REG.resolve("same", os.path.join(t, "engine"), root), reg_board)
    finally:
        if old is None:
            os.environ.pop("WB_BOARD_DIRS", None)
        else:
            os.environ["WB_BOARD_DIRS"] = old
