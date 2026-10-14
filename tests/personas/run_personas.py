#!/usr/bin/env python3
"""Persona runner (N5/N6). Usage: python tests/personas/run_personas.py [--round N] [--only dana,erin,frank] [--jobs 3] [--steps a,b]
Runs every persona on desktop 1440x900 (mouse) and phone 390x844 (touch), each in its own temp sandbox + free-port server, then writes
docs/personas/findings-<persona>.md and docs/personas/rollup.json (round N). Refuses (non-zero exit) when the real boards/ hash changed.
It tests and reports; it never edits product code."""
import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import classify
import guard
import harness
import report

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = json.load(open(os.path.join(HERE, "personas.json"), encoding="utf-8"))


def run_one(pid, vp, out_dir, steps=None):
    env = dict(os.environ)
    if steps:
        env["STEPS"] = steps
    os.makedirs(out_dir, exist_ok=True)
    t = time.time()
    r = subprocess.run([harness.NODE, os.path.join(HERE, "run_persona.js"), pid, vp, out_dir], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=1500)
    res_path = os.path.join(out_dir, vp + "-result.json")
    res = json.load(open(res_path, encoding="utf-8")) if os.path.exists(res_path) else {"steps": [], "findings": [], "errors": [], "guard": {"ok": False}}
    res["_rc"] = r.returncode
    res["_secs"] = round(time.time() - t)
    return pid, vp, res


def extra_findings(pid, vp, res):
    """Console errors and failed GETs are Minor findings (pageerror is already a Major finding from the runner)."""
    out = []
    seen = set()
    for e in res.get("errors", []):
        if e["t"] == "pageerror":
            continue
        if e["t"] == "http":
            code, method = e["m"].split(" ")[0], e["m"].split(" ")[1]
            if method != "GET" and code.startswith("4"):
                continue  # intentional refusals (abuse tests, unsaved invalid saves) are asserted as step results
        key = (e["t"], e["m"][:80])
        if key in seen:
            continue
        seen.add(key)
        out.append({"persona": pid, "viewport": vp, "step": e.get("step", "?"), "category": "console_error", "flags": ["console"],
                    "title": "Unexpected %s during the run: %s" % (e["t"], e["m"][:100]), "repro": "see step %s evidence" % e.get("step"),
                    "evidence": e["m"], "file": ""})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--only", default="")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--steps", default="")
    ap.add_argument("--docs", default=report.DOCS)
    a = ap.parse_args()
    pids = [p["id"] for p in SPEC["personas"] if not a.only or p["id"] in a.only.split(",")]
    before = guard.snapshot()
    selftest = guard.self_test()
    if not selftest["ok"]:
        print("GUARD SELF-TEST FAILED", selftest)
        return 3
    jobs = [(pid, vp) for pid in pids for vp in SPEC["viewports"]]
    results = {}
    with ThreadPoolExecutor(max_workers=a.jobs) as ex:
        futs = [ex.submit(run_one, pid, vp, os.path.join(a.docs, pid), a.steps or None) for pid, vp in jobs]
        for f in futs:
            pid, vp, res = f.result()
            results.setdefault(pid, {})[vp] = res
            print("done %s/%s steps=%d failed=%d findings=%d rc=%s %ss" % (pid, vp, len(res["steps"]), sum(1 for s in res["steps"] if not s["pass"]),
                                                                         len(res["findings"]), res["_rc"], res["_secs"]), flush=True)
    after = guard.snapshot()
    gok, changed = guard.compare(before, after)
    guard_info = {"self_test": selftest, "real_boards_unchanged": gok, "files_hashed": len(before), "changed": changed}
    per = {}
    for pid in pids:
        raw = []
        for vp, res in results[pid].items():
            raw += res["findings"] + extra_findings(pid, vp, res)
        if not gok:
            raw.append({"persona": pid, "viewport": "desktop", "step": "guard", "category": "real_boards_modified", "flags": ["real_boards_modified", "unsafe"],
                        "title": "Real boards/ changed during the persona run", "repro": "run_personas.py", "evidence": ",".join(changed)[:200], "file": ""})
        fs = classify.dedupe(raw)
        per[pid] = {"findings": fs, "results": results[pid]}
        persona = next(p for p in SPEC["personas"] if p["id"] == pid)
        report.write_findings_md(persona, persona["brief"], results[pid], fs, a.docs, a.round)
    entry = report.build_round(a.round, per, guard_info)
    entry["driver"] = "playwright-core (playwright-cli engine) headless; playwright-cli binary used for the smoke step"
    entry["viewports"] = {k: "%dx%d%s" % (v["width"], v["height"], " touch" if v["touch"] else "") for k, v in SPEC["viewports"].items()}
    entry["script_errors"] = sum(1 for pid in pids for vp, r in results[pid].items() for s in r["steps"] if s["sub"] == "script" and not s["pass"])
    rp = report.write_rollup(entry, a.docs)
    print(json.dumps({"round": a.round, "major": entry["major"], "minor": entry["minor"], "note": entry["note"], "guard_ok": gok,
                      "script_errors": entry["script_errors"], "rollup": rp}))
    harness.enforce_guard(before, after)
    return 0


if __name__ == "__main__":
    sys.exit(main())
