"""Report writer for the persona harness (N5): docs/personas/findings-<persona>.md and docs/personas/rollup.json (round-aware)."""
import json
import os
import time

import classify

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(os.path.dirname(HERE)), "docs", "personas")
VIEWPORTS = ("desktop", "phone")


def step_table(results):
    """results: {viewport: result-json}. Returns {viewport: {step: [total, passed]}} and totals."""
    t = {}
    for vp, r in results.items():
        per = {}
        for s in r.get("steps", []):
            row = per.setdefault(s["step"], [0, 0])
            row[0] += 1
            row[1] += 1 if s["pass"] else 0
        t[vp] = per
    return t


def write_findings_md(persona, brief, results, findings, docs=DOCS, round_no=1):
    c = classify.counts(findings)
    st = step_table(results)
    lines = ["# Persona findings: %s (round %d)" % (persona["name"], round_no), "",
             "Persona: %s" % persona["brief"], "",
             "Generated %s. Driver: playwright-core (the playwright-cli engine) headless, desktop 1440x900 mouse and phone 390x844 touch, on a temp copy with seeded starter boards; real boards hash-guarded." % time.strftime("%Y-%m-%d %H:%M"),
             "", "Counts: major %d, minor %d, note %d." % (c["major"], c["minor"], c["note"]), "",
             "## Steps (passed / recorded)", "", "| step | " + " | ".join(VIEWPORTS) + " |", "|---|" + "---|" * len(VIEWPORTS)]
    steps = sorted({s for vp in st.values() for s in vp})
    for s in steps:
        cells = []
        for vp in VIEWPORTS:
            row = st.get(vp, {}).get(s)
            cells.append("%d/%d" % (row[1], row[0]) if row else "-")
        lines.append("| %s | %s |" % (s, " | ".join(cells)))
    lines += ["", "## Findings", ""]
    if not findings:
        lines.append("None.")
    for sev in classify.SEVERITIES:
        grp = [f for f in findings if f["severity"] == sev]
        if not grp:
            continue
        lines += ["### %s (%d)" % (sev.capitalize(), len(grp)), ""]
        for f in grp:
            lines += ["- **%s** step %s [%s] %s" % (f["id"], f["step"], "/".join(f["viewports"]) or "-", f["title"]),
                      "  - repro: %s" % f["repro"], "  - evidence: %s" % (f["evidence"] or "-").replace("\n", " "),
                      "  - suspected file: %s" % (f["file"] or "-")]
        lines.append("")
    p = os.path.join(docs, "findings-%s.md" % persona["id"])
    os.makedirs(docs, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return p


def build_round(round_no, per_persona, guard):
    """per_persona: {pid: {"findings": [...], "results": {...}}} -> one round entry for rollup.json."""
    r = {"round": round_no, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "personas": {}, "major": 0, "minor": 0, "note": 0, "majors": [],
         "guard": guard}
    for pid, d in per_persona.items():
        c = classify.counts(d["findings"])
        st = step_table(d["results"])
        ran = {vp: {"steps_recorded": sum(v[0] for v in st[vp].values()), "steps_passed": sum(v[1] for v in st[vp].values()),
                    "step_ids": sorted(st[vp])} for vp in st}
        r["personas"][pid] = dict(c, viewports=ran, findings=[f["id"] for f in d["findings"]])
        for k in c:
            r[k] += c[k]
        for f in d["findings"]:
            if f["severity"] == "major":
                r["majors"].append({"id": f["id"], "persona": pid, "step": f["step"], "title": f["title"], "repro": f["repro"],
                                    "evidence": f["evidence"], "evidence_path": "docs/personas/%s/" % pid, "file": f["file"],
                                    "viewports": f["viewports"]})
    return r


def write_rollup(round_entry, docs=DOCS):
    p = os.path.join(docs, "rollup.json")
    d = {"schema": "workflow-board/persona-rollup/v1", "rounds": []}
    if os.path.exists(p):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except ValueError:
            pass
    d["rounds"] = [r for r in d.get("rounds", []) if r.get("round") != round_entry["round"]] + [round_entry]
    d["rounds"].sort(key=lambda r: r["round"])
    last = d["rounds"][-1]
    d["final"] = {"round": last["round"], "major_open": last["major"], "minor_open": last["minor"], "note_open": last["note"]}
    os.makedirs(docs, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=2)
    return p
