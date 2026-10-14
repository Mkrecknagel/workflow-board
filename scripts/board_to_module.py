#!/usr/bin/env python3
"""Generate modules/<name>.module.json from child boards (the child board file is the source of truth).

  python scripts/board_to_module.py --manifest <manifest.json>            regenerate the module files
  python scripts/board_to_module.py --manifest <manifest.json> --check    exit 2 if a committed module differs from a regeneration,
                                                                          or a child input/output card id is not a parent port name

Manifest: {"modules_dir": "<abs dir, default engine modules/>",
           "modules": [{"module": name, "board": "<abs child board json>", "version": 1, "description": "...",
                        "parents": [{"board": "<abs parent board json>", "card": "<card id>"}], "exact": true}]}
Port rule: child `input` card ids are the module's exposed `in` ports, child `output` card ids its exposed `out` ports. For every parent
card listed: its ports.in must equal (exact=true) or be a subset of (exact=false) the child input ids, same for out/output ids.
Positions are normalised to start at 0,0; `drill` links are dropped; created_at is preserved from an existing file. Stdlib only.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import validate as V  # noqa: E402


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def build(entry, modules_dir):
    b = load(entry["board"])
    cards = json.loads(json.dumps(b["cards"]))
    ins = [c["id"] for c in cards if c["type"] == "input"]
    outs = [c["id"] for c in cards if c["type"] == "output"]
    mx = min(c["pos"]["x"] for c in cards)
    my = min(c["pos"]["y"] for c in cards)
    for c in cards:
        c["pos"] = {"x": c["pos"]["x"] - mx, "y": c["pos"]["y"] - my}
        c.pop("drill", None)
    path = os.path.join(modules_dir, entry["module"] + ".module.json")
    created = None
    if os.path.isfile(path):
        created = load(path).get("created_at")
    mod = {"schema": "workflow-board/module/v1", "name": entry["module"], "version": entry["version"],
           "description": entry["description"], "created_at": created or entry.get("created_at") or "2026-10-12T15:00:00-07:00",
           "origin_board": b["board"], "exposed_ports": {"in": ins, "out": outs}, "cards": cards, "edges": b["edges"]}
    return mod, path


def port_errors(entry, mod):
    errs = []
    ins, outs = set(mod["exposed_ports"]["in"]), set(mod["exposed_ports"]["out"])
    for p in entry.get("parents", []):
        pb = load(p["board"])
        pc = next((c for c in pb["cards"] if c["id"] == p["card"]), None)
        if pc is None:
            errs.append(f"{entry['module']}: parent card {p['card']} not in {pb['board']}")
            continue
        pi, po = set(pc["ports"]["in"]), set(pc["ports"]["out"])
        if entry.get("exact", True):
            if pi != ins or po != outs:
                errs.append(f"{entry['module']}: parent {p['card']} ports in={sorted(pi)} out={sorted(po)} != child in={sorted(ins)} out={sorted(outs)}")
        elif not (pi <= ins and po <= outs):
            errs.append(f"{entry['module']}: parent {p['card']} ports in={sorted(pi)} out={sorted(po)} not within child in={sorted(ins)} out={sorted(outs)}")
    return errs


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    man = load(a.manifest)
    mdir = man.get("modules_dir") or os.path.join(HERE, "modules")
    errs = []
    for entry in man["modules"]:
        mod, path = build(entry, mdir)
        errs += port_errors(entry, mod)
        try:
            V.validate_module(mod, entry["module"])
        except V.Invalid as e:
            errs.append(f"{entry['module']}: invalid module: {e}")
        if a.check:
            if not os.path.isfile(path):
                errs.append(f"{entry['module']}: module file missing ({path})")
            elif load(path) != mod:
                errs.append(f"{entry['module']}: DRIFT - committed module differs from regeneration of {entry['board']}")
        elif not errs:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(mod, f, indent=2, ensure_ascii=False)
                f.write("\n")
            print("wrote", path)
    for e in errs:
        print("ERROR:", e, file=sys.stderr)
    if a.check and not errs:
        print("OK: no drift,", len(man["modules"]), "modules")
    return 2 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
