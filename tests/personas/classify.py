"""Findings schema + severity classifier for the persona harness (N5).

Finding = {id, persona, severity, step, evidence, repro, title, category, flags[], viewports[], file}.
The rubric (C1 design "Persona test plan"):
  MAJOR  crash / blank screen / uncaught exception; data loss (an edit or board vanishes, wrong-board write, overwritten board);
         stuck flow (no way forward or back without reload); unsafe action enabled (gate bypass, immutable card disabled,
         run_mode background on approval, enforcement hash broken by a visual edit, real boards modified).
  MINOR  wrong label/copy, awkward-but-recoverable flow, cosmetic glitch, phone ergonomics.
  NOTE   suggestion.
Only Major blocks "done".
"""
import re

SEVERITIES = ("major", "minor", "note")
FIELDS = ("id", "persona", "severity", "step", "evidence", "repro", "title", "category", "flags", "viewports", "file")

# flags that always make a finding Major, whatever its category
MAJOR_FLAGS = {"crash", "data_loss", "stuck", "unsafe", "real_boards_modified", "wrong_board_write", "hash_broken_by_visual"}
# categories that are Major by themselves
MAJOR_CATEGORIES = {"uncaught_exception", "blank_screen", "data_loss", "unsafe_enabled", "real_boards_modified"}
# categories that are Minor unless a Major flag is present
MINOR_CATEGORIES = {"copy", "cosmetic", "awkward", "phone_ergonomics", "stuck_flow", "console_error"}
NOTE_CATEGORIES = {"note", "harness"}


def classify(finding):
    """Return 'major' | 'minor' | 'note' for one raw finding dict."""
    flags = set(finding.get("flags") or [])
    cat = finding.get("category") or "note"
    if flags & MAJOR_FLAGS:
        return "major"
    if cat in MAJOR_CATEGORIES:
        return "major"
    if cat == "stuck_flow":  # a flow that is merely awkward stays Minor; 'stuck' flag (above) makes it Major
        return "minor"
    if cat in MINOR_CATEGORIES or flags & {"awkward", "cosmetic", "jargon", "touch_target"}:
        return "minor"
    return "note"


def normalize_title(t):
    """Title key used to merge the same issue seen on desktop and phone (viewport numbers removed)."""
    t = re.sub(r"\d+x\d+", "", t or "")
    t = re.sub(r"\b(desktop|phone)\b", "", t, flags=re.I)
    t = re.sub(r"\(\s*\d+(?:/\d+)?\s*(?:edges|px)?[^)]*\)", "", t)
    t = re.sub(r"\d+", "#", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def dedupe(raw):
    """Merge raw findings of one persona (several viewports) into unique findings with a viewports list. Order is stable."""
    out, seen = [], {}
    for f in raw:
        key = (f.get("persona"), f.get("step"), f.get("category"), normalize_title(f.get("title")))
        if key in seen:
            u = seen[key]
            if f.get("viewport") and f["viewport"] not in u["viewports"]:
                u["viewports"].append(f["viewport"])
            continue
        u = {"persona": f.get("persona"), "step": f.get("step"), "title": f.get("title"), "category": f.get("category"),
             "flags": list(f.get("flags") or []), "repro": f.get("repro", ""), "evidence": f.get("evidence", ""),
             "file": f.get("file", ""), "viewports": [f["viewport"]] if f.get("viewport") else []}
        u["severity"] = classify(u)
        seen[key] = u
        out.append(u)
    order = {s: i for i, s in enumerate(SEVERITIES)}
    out.sort(key=lambda u: (order[u["severity"]], u["step"] or "", u["title"] or ""))
    for n, u in enumerate(out, 1):
        u["id"] = "%s-%03d" % ((u["persona"] or "x")[:4], n)
    return out


def counts(findings):
    c = {s: 0 for s in SEVERITIES}
    for f in findings:
        c[f["severity"]] += 1
    return c


def validate_finding(f):
    """Schema check: returns a list of problems (empty = valid)."""
    bad = [k for k in FIELDS if k not in f]
    if f.get("severity") not in SEVERITIES:
        bad.append("severity")
    return bad
