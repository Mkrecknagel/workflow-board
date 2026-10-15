#!/usr/bin/env python3
"""Board registry: board name -> file path. Stdlib only.

Boards come from two places:
  (a) the engine's own boards dir (engine design boards, e.g. demo-board), and
  (b) INSTANCE boards registered in a project's project.json as a source:
        sources.artifacts[] = {"type": "workflow-board", "board": "<name>", "url": "<absolute path to the board json>"}
      scanned from <root>/<projects>/**/project.json (depth <= 3).
  (c) every *.json in the folders listed in env WB_BOARD_DIRS (os.pathsep separated), for generated boards that
      must live outside the engine (wb_init.py writes there by default).
Root = env WORKFLOW_BOARD_ROOT (alias BOARD_REGISTRY_ROOT, used by tests), else a `registry-root` folder next to this file.
The registry only READS project.json files; it never writes them. Problems (duplicate name, missing registered
file, unreadable project.json) are returned as error strings, never raised.
"""
import json
import os

DEFAULT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "registry-root")
MAX_DEPTH = 3


def board_dirs():
    """Extra board folders from WB_BOARD_DIRS, in order. Missing folders are skipped."""
    return [d for d in (os.environ.get("WB_BOARD_DIRS") or "").split(os.pathsep) if d and os.path.isdir(d)]


def _dir_boards(d):
    out = {}
    for fn in sorted(os.listdir(d)):
        p = os.path.join(d, fn)
        if fn.endswith(".json") and fn != "active.json" and not fn.startswith(".") and os.path.isfile(p):
            out[fn[:-5]] = p
    return out


def portfolio_root():
    return os.environ.get("BOARD_REGISTRY_ROOT") or os.environ.get("WORKFLOW_BOARD_ROOT") or DEFAULT_ROOT


def _projects_dir(root):
    sub = "projects"
    try:
        with open(os.path.join(root, "config.json"), encoding="utf-8") as f:
            sub = (json.load(f).get("paths") or {}).get("projects") or sub
    except (OSError, ValueError, AttributeError):
        pass
    return os.path.join(root, sub)


def _project_files(pdir):
    out = []
    if not os.path.isdir(pdir):
        return out
    for dp, dns, fns in os.walk(pdir):
        depth = os.path.relpath(dp, pdir).count(os.sep) + 1 if dp != pdir else 0
        if depth >= MAX_DEPTH:
            dns[:] = []
        dns[:] = [d for d in dns if not d.startswith(".") and d not in ("kb", "node_modules")]
        if "project.json" in fns:
            out.append(os.path.join(dp, "project.json"))
    return sorted(out)


def registered(root=None):
    """-> list of {"board","path","project"} read from project.json files, plus errors list."""
    root = root or portfolio_root()
    found, errors = [], []
    for pj in _project_files(_projects_dir(root)):
        try:
            with open(pj, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError) as e:
            errors.append(f"unreadable project.json {pj}: {str(e)[:80]}")
            continue
        arts = ((doc.get("sources") or {}).get("artifacts") or []) if isinstance(doc, dict) else []
        for a in arts:
            if isinstance(a, dict) and a.get("type") == "workflow-board":
                name, url = a.get("board"), a.get("url")
                if not isinstance(name, str) or not isinstance(url, str) or not name or not url:
                    errors.append(f"malformed workflow-board entry in {pj}: needs string 'board' and 'url'")
                    continue
                found.append({"board": name, "path": url, "project": pj})
    return found, errors


def scan(boards_dir, root=None):
    """-> (paths {name: path}, errors [str]). Engine boards first; a duplicate name keeps the first and reports the rest."""
    paths, errors = {}, []
    if boards_dir and os.path.isdir(boards_dir):
        for fn in sorted(os.listdir(boards_dir)):
            p = os.path.join(boards_dir, fn)
            if fn.endswith(".json") and fn != "active.json" and os.path.isfile(p):
                paths[fn[:-5]] = p
    for d in board_dirs():
        for n, p in _dir_boards(d).items():
            if n in paths:
                errors.append(f"duplicate board name '{n}': {p} (WB_BOARD_DIRS) conflicts with {paths[n]}; keeping the first")
                continue
            paths[n] = p
    reg, errors2 = registered(root)
    errors += errors2
    for r in reg:
        n, p = r["board"], r["path"]
        if n in paths:
            errors.append(f"duplicate board name '{n}': {p} (registered in {r['project']}) conflicts with {paths[n]}; keeping the first")
            continue
        if not os.path.isfile(p):
            errors.append(f"registered board '{n}' is missing: {p} (registered in {r['project']})")
            continue
        paths[n] = p
    return paths, errors


def list_boards(boards_dir, root=None):
    paths, errors = scan(boards_dir, root)
    return sorted(paths), errors


def resolve(name, boards_dir, root=None):
    """Path for a board name. Unregistered names fall back to <boards_dir>/<name>.json (new boards are created in the engine).
    A registered-but-missing file still resolves to its registered path (a save recreates it where it is registered)."""
    reg, _ = registered(root)
    hits = [r["path"] for r in reg if r["board"] == name]
    eng = os.path.join(boards_dir, name + ".json") if boards_dir else None
    if eng and os.path.isfile(eng):
        return eng
    for d in board_dirs():
        p = os.path.join(d, name + ".json")
        if os.path.isfile(p):
            return p
    if hits:
        return hits[0]
    return eng


if __name__ == "__main__":
    import sys
    a = sys.argv[1:]
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "boards")
    if len(a) == 2 and a[0] == "resolve":
        p = resolve(a[1], here)
        print(p or "")
        sys.exit(0 if p and os.path.isfile(p) else 1)
    if a == ["list"]:
        names, errs = list_boards(here)
        print(json.dumps({"boards": names, "errors": errs}, indent=2))
        sys.exit(0)
    sys.exit("usage: registry.py resolve <name> | list")
