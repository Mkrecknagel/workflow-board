"""Board registry tests: temp portfolio root with fake project.json files; never reads or writes the real registry."""
import json
import os
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import registry as REG  # noqa: E402
import serve  # noqa: E402

BOARD = {"schema": "workflow-board/v1", "board": "x", "cards": [], "edges": []}


def project(root, rel, arts):
    d = root.joinpath("projects", *rel.split("/"))
    d.mkdir(parents=True, exist_ok=True)
    (d / "project.json").write_text(json.dumps({"alias": rel, "sources": {"artifacts": arts}}), encoding="utf-8")


def board_file(p, name):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(dict(BOARD, board=name)), encoding="utf-8")
    return str(p)


def test_engine_and_registered_boards_resolve(tmp_path):
    eng = tmp_path / "engine"
    board_file(eng / "eng-one.json", "eng-one")
    (eng / "active.json").write_text("{}")
    root = tmp_path / "root"
    a = board_file(tmp_path / "ws" / "a" / "workflows" / "one.json", "one")
    b = board_file(tmp_path / "ws" / "b" / "workflows" / "two.json", "two")
    project(root, "prog/a", [{"type": "workflow-board", "board": "one", "url": a}, {"type": "doc", "url": "x"}])  # child, depth 2
    project(root, "b", [{"type": "workflow-board", "board": "two", "url": b}])
    names, errs = REG.list_boards(str(eng), str(root))
    assert names == ["eng-one", "one", "two"] and errs == []
    assert REG.resolve("one", str(eng), str(root)) == a
    assert REG.resolve("eng-one", str(eng), str(root)) == str(eng / "eng-one.json")
    assert REG.resolve("brand-new", str(eng), str(root)) == os.path.join(str(eng), "brand-new.json")  # unregistered -> engine


def test_duplicates_and_missing_are_errors_not_crashes(tmp_path):
    eng = tmp_path / "engine"
    board_file(eng / "dup.json", "dup")
    root = tmp_path / "root"
    a = board_file(tmp_path / "ws" / "dup.json", "dup")
    project(root, "a", [{"type": "workflow-board", "board": "dup", "url": a},
                        {"type": "workflow-board", "board": "gone", "url": str(tmp_path / "nope.json")},
                        {"type": "workflow-board", "board": "bad"}])
    names, errs = REG.list_boards(str(eng), str(root))
    assert names == ["dup"]
    text = " | ".join(errs)
    assert "duplicate board name 'dup'" in text and "'gone' is missing" in text and "malformed" in text
    (root / "projects" / "z").mkdir()
    (root / "projects" / "z" / "project.json").write_text("{not json")
    assert any("unreadable" in e for e in REG.list_boards(str(eng), str(root))[1])


def test_missing_root_is_empty(tmp_path):
    assert REG.registered(str(tmp_path / "nowhere")) == ([], [])


def test_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("BOARD_REGISTRY_ROOT", str(tmp_path))
    assert REG.portfolio_root() == str(tmp_path)


@pytest.fixture()
def server(tmp_path, monkeypatch):
    eng = tmp_path / "engine"
    board_file(eng / "eng-one.json", "eng-one")
    root = tmp_path / "root"
    inst = board_file(tmp_path / "ws" / "inst.json", "inst")
    project(root, "p", [{"type": "workflow-board", "board": "inst", "url": inst},
                        {"type": "workflow-board", "board": "lost", "url": str(tmp_path / "ws" / "lost.json")}])
    monkeypatch.setenv("BOARD_REGISTRY_ROOT", str(root))
    monkeypatch.setattr(serve, "BOARDS", str(eng))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve.H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", inst, eng
    httpd.shutdown()


