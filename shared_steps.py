#!/usr/bin/env python3
"""Shared step cards for workflow-board (stdlib only). Links, not copies; modules stay copies.

Board model (additive; validate.py ignores nothing it checks explicitly, see check_shared):
  doc["shared_library"] = [ {id, version, label, body, fields, history[], refcount, created_at, updated_at} ]
  lane card (type stays "step") gains  shared = {step_id, version, overrides, sop_anchor, sop_sync}
  On every save label / ref.detail / fields of each instance mirror the library entry (denormalized cache).

SOP files are READ-ONLY inputs here: `propose` writes unified diffs + proposal.json under proposals/<date>/,
never the SOP. Applying a proposal is a separate owner-run step outside this module.
CLI:  shared_steps.py drift|propose --board NAME [--board-dir D] [--sop-dir D] [--step-id ID] [--date YYYY-MM-DD]
"""
import argparse
import copy
import difflib
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dedup as D  # noqa: E402
import validate as V  # noqa: E402
import registry as REG  # noqa: E402

OVERRIDE_KEYS = ("skip", "enabled", "execution_mode", "local_note")
KEEP = 20


class SharedError(Exception):
    """.code: 'invalid' (422) | 'notfound' (404) | 'conflict' (409) | 'refused' (403)"""

    def __init__(self, msg, code="invalid"):
        super().__init__(msg)
        self.code = code


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def bdir_of(a=None):
    return D.board_dir(a)


def proposals_dir(a=None):
    return a or os.environ.get("WB_PROPOSALS_DIR") or os.path.join(HERE, "proposals")


# ------------------------------------------------------------------ persistence
def load(board, bdir=None):
    p = REG.resolve(board, bdir_of(bdir))
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise SharedError(f"no board '{board}'", "notfound")


def save(doc, board, bdir=None):
    """mirror -> validate -> history copy -> atomic write. Raises SharedError(invalid) on a bad board."""
    d = bdir_of(bdir)
    recount(doc)
    mirror(doc)
    try:
        V.validate_doc(doc, board)
    except V.Invalid as e:
        raise SharedError(str(e), "invalid")
    path = REG.resolve(board, d)
    raw = (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    hist = os.path.join(d, ".history")
    if os.path.isfile(path):
        os.makedirs(hist, exist_ok=True)
        with open(path, "rb") as fh:
            cur = fh.read()
        ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + f"{int((time.time() % 1) * 1e6):06d}Z"
        with open(os.path.join(hist, f"{board}.{ts}.{hashlib.sha256(cur).hexdigest()[:8]}.json"), "wb") as fh:
            fh.write(cur)
        olds = sorted(fn for fn in os.listdir(hist) if fn.startswith(board + ".") and fn.endswith(".json"))
        for fn in olds[:-KEEP]:
            try:
                os.remove(os.path.join(hist, fn))
            except OSError:
                pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ------------------------------------------------------------------ model helpers
def lib(doc):
    return doc.setdefault("shared_library", [])


def entry(doc, sid):
    for e in doc.get("shared_library") or []:
        if e.get("id") == sid:
            return e
    raise SharedError(f"no shared step '{sid}'", "notfound")


def instances(doc, sid):
    return [c for c in doc["cards"] if isinstance(c.get("shared"), dict) and c["shared"].get("step_id") == sid]


def recount(doc):
    for e in doc.get("shared_library") or []:
        e["refcount"] = len(instances(doc, e["id"]))


def mirror(doc):
    """Denormalized cache: label, ref.detail and fields of every instance follow its library entry."""
    byid = {e["id"]: e for e in doc.get("shared_library") or []}
    for c in doc["cards"]:
        sh = c.get("shared")
        e = byid.get(sh.get("step_id")) if isinstance(sh, dict) else None
        if not e:
            continue
        c["label"] = e["label"]
        c.setdefault("ref", {})["detail"] = e["body"]
        if e.get("fields"):
            c["fields"] = dict(c.get("fields") or {}, **e["fields"])


def _next_id(doc):
    n = 0
    for e in doc.get("shared_library") or []:
        m = re.fullmatch(r"shared-(\d+)", e["id"])
        if m:
            n = max(n, int(m.group(1)))
    return f"shared-{n + 1:04d}"


def _card(doc, cid):
    for c in doc["cards"]:
        if c["id"] == cid:
            return c
    raise SharedError(f"no card '{cid}'", "notfound")


def _title_of(label):
    return re.sub(r"^\d+[a-z]?\s+", "", (label or "").strip())


def _check_fields(fields):
    if fields is None:
        return {}
    if not isinstance(fields, dict):
        raise SharedError("fields must be an object")
    try:
        V.check_style_fields([{"id": "shared-entry", "fields": fields}], V.load_schema())
    except V.Invalid as e:
        raise SharedError(str(e))
    return dict(fields)


def _check_text(label, body):
    if not isinstance(label, str) or not label.strip():
        raise SharedError("label must be a non-empty string")
    if not isinstance(body, str) or not body.strip():
        raise SharedError("body must be a non-empty string")


# ------------------------------------------------------------------ replace
def replace(doc, members, label=None, body=None, fields=None, canonical=None, sop_dir=None, actor="owner"):
    """Turn >= 2 step cards into references to ONE new library entry. Whole members become references in
    place; fragment members keep their card and get a new reference card inserted right after it."""
    if not isinstance(members, list) or len(members) < 2:
        raise SharedError("replace needs at least 2 member cards")
    mem = []
    for m in members:
        m = {"card_id": m} if isinstance(m, str) else dict(m)
        if m.get("scope", "whole") not in ("whole", "fragment"):
            raise SharedError("scope must be whole or fragment")
        m.setdefault("scope", "whole")
        mem.append(m)
    ids = [m["card_id"] for m in mem]
    if len(set(ids)) != len(ids):
        raise SharedError("duplicate member card")
    cards = {}
    for cid in ids:
        c = _card(doc, cid)
        if c["type"] != "step":
            raise SharedError(f"card '{cid}' is not a step card")
        if c.get("shared"):
            raise SharedError(f"card '{cid}' is already a shared step instance")
        if c.get("immutable"):
            raise SharedError(f"card '{cid}' is immutable")
        cards[cid] = c
    canon_id = canonical if isinstance(canonical, str) else None
    if canon_id and canon_id not in cards:
        raise SharedError("canonical must be one of the member cards")
    sops = D.parse_sops(D.sop_dir(sop_dir)) if (body is None or sop_dir) else {}
    head = cards[canon_id or ids[0]]
    if label is None:
        label = _title_of(head["label"])
    if body is None:
        sec = D.find_section(sops, head.get("sop"), label=head["label"])
        body = (sec["raw"].strip() if sec else None) or head.get("ref", {}).get("detail") or label
    _check_text(label, body)
    fields = _check_fields(fields)
    sid = _next_id(doc)
    ts = now()
    anchors = {}
    for m in mem:
        c = cards[m["card_id"]]
        sec = D.find_section(sops, c.get("sop"), label=c["label"]) if sops else None
        anchor = {"sop": c.get("sop", ""), "heading": sec["key"] if sec else D.heading_key(_title_of(c["label"])),
                  "scope": m["scope"]}
        if m["scope"] == "fragment" and m.get("match_text"):
            anchor["match_text"] = m["match_text"]
        anchors[m["card_id"]] = anchor
    inserted = []
    for m in mem:
        c = cards[m["card_id"]]
        sh = {"step_id": sid, "version": 1,
              "overrides": {"skip": False, "enabled": bool(c.get("enabled", True)),
                            "execution_mode": c.get("execution_mode", "observe"), "local_note": ""},
              "sop_anchor": anchors[m["card_id"]], "sop_sync": "drifted"}
        if m["scope"] == "whole":
            c["shared"] = sh
            continue
        nid, k = c["id"] + "-ref", 1
        taken = {x["id"] for x in doc["cards"]}
        while nid in taken:
            k += 1
            nid = f"{c['id']}-ref{k}"
        for o in doc["cards"]:  # make room: renumber later cards of the same lane
            if o.get("group") == c.get("group") and o.get("sop") == c.get("sop") and isinstance(o.get("order"), int) \
                    and isinstance(c.get("order"), int) and o["order"] > c["order"]:
                o["order"] += 1
        nc = {"id": nid, "type": "step", "label": label, "immutable": False, "enabled": True,
              "execution_mode": "observe", "ref": {"detail": body}, "ports": {"in": ["in"], "out": ["out"]},
              "pos": {"x": c["pos"]["x"] + 24, "y": c["pos"]["y"] + 100}, "sop": c.get("sop", ""),
              "order": (c.get("order") or 0) + 1, "shared": sh}
        if c.get("group"):
            nc["group"] = c["group"]
        doc["cards"].append(nc)
        inserted.append(nid)
    lib(doc).append({"id": sid, "version": 1, "label": label, "body": body, "fields": fields, "refcount": len(mem),
                     "history": [{"version": 1, "at": ts, "actor": actor, "summary": f"created from {len(mem)} cards"}],
                     "created_at": ts, "updated_at": ts})
    recount(doc)
    mirror(doc)
    return {"step_id": sid, "refcount": len(instances(doc, sid)), "cards": ids, "inserted": inserted}


# ------------------------------------------------------------------ edit preview / edit
def _render(label, body, fields):
    out = [f"label: {label}", "body:"] + body.splitlines()
    for k in sorted(fields or {}):
        out.append(f"fields.{k}: {fields[k]}")
    return out


def _apply_changes(e, changes):
    if not isinstance(changes, dict) or not set(changes) <= {"label", "body", "fields"} or not changes:
        raise SharedError("changes must be an object with label, body and/or fields")
    label = changes.get("label", e["label"])
    body = changes.get("body", e["body"])
    _check_text(label, body)
    fields = _check_fields(changes["fields"]) if "fields" in changes else dict(e.get("fields") or {})
    return label, body, fields


def preview(doc, sid, changes):
    e = entry(doc, sid)
    label, body, fields = _apply_changes(e, changes)
    inst = instances(doc, sid)
    diff = "\n".join(difflib.unified_diff(_render(e["label"], e["body"], e.get("fields")), _render(label, body, fields),
                                          f"{sid}@v{e['version']}", f"{sid}@v{e['version'] + 1}", lineterm="", n=3))
    lanes = [{"card_id": c["id"], "group": c.get("group", ""), "sop": c.get("sop", ""), "label": c["label"]} for c in inst]
    return {"step_id": sid, "version": e["version"], "new_version": e["version"] + 1, "n_places": len(inst),
            "n_sops": len({c.get("sop", "") for c in inst}), "lanes": lanes, "diff": diff,
            "before": {"label": e["label"], "body": e["body"], "fields": e.get("fields") or {}},
            "after": {"label": label, "body": body, "fields": fields}}


def unlink(doc, card_id):
    c = _card(doc, card_id)
    sh = c.get("shared")
    if not isinstance(sh, dict):
        raise SharedError(f"card '{card_id}' is not a shared step instance")
    e = entry(doc, sh["step_id"])
    c["label"] = e["label"]  # clone the current library payload into the card
    c.setdefault("ref", {})["detail"] = e["body"]
    if e.get("fields"):
        c["fields"] = dict(c.get("fields") or {}, **e["fields"])
    del c["shared"]
    recount(doc)
    return {"card_id": card_id, "step_id": e["id"], "refcount": e["refcount"]}


def edit(doc, sid, changes, confirm=False, expected_version=None, fork_cards=None, actor="owner"):
    """Land a change to the library entry (N places). Nothing lands without confirm=True."""
    e = entry(doc, sid)
    if confirm is not True:
        raise SharedError("edit changes N places: confirm must be true (use edit-preview first)", "refused")
    if expected_version is not None and expected_version != e["version"]:
        raise SharedError(f"shared step is at version {e['version']}, not {expected_version}", "conflict")
    label, body, fields = _apply_changes(e, changes)
    if (label, body, fields) == (e["label"], e["body"], dict(e.get("fields") or {})):
        raise SharedError("no change")
    forks = list(fork_cards or [])
    for cid in forks:
        c = _card(doc, cid)
        if not (isinstance(c.get("shared"), dict) and c["shared"].get("step_id") == sid):
            raise SharedError(f"card '{cid}' is not an instance of {sid}")
    for cid in forks:  # unchecked lanes fork with the OLD payload before the change lands
        unlink(doc, cid)
    changed = [k for k, a, b in (("label", e["label"], label), ("body", e["body"], body),
                                 ("fields", e.get("fields") or {}, fields)) if a != b]
    e["label"], e["body"], e["fields"] = label, body, fields
    e["version"] += 1
    e["updated_at"] = now()
    e["history"].append({"version": e["version"], "at": e["updated_at"], "actor": actor,
                         "summary": f"edited {', '.join(changed)}; {len(instances(doc, sid))} places, {len(forks)} forked"})
    for c in instances(doc, sid):
        c["shared"]["version"] = e["version"]
        c["shared"]["sop_sync"] = "drifted"
    recount(doc)
    mirror(doc)
    return {"step_id": sid, "version": e["version"], "n_places": e["refcount"], "forked": forks}


def delete_entry(doc, sid):
    e = entry(doc, sid)
    n = len(instances(doc, sid))
    if n > 0:
        raise SharedError(f"shared step '{sid}' is used in {n} places; unlink them first", "conflict")
    doc["shared_library"] = [x for x in doc["shared_library"] if x["id"] != sid]
    return {"deleted": sid}


# ------------------------------------------------------------------ drift
def _proposals(pdir):
    out = []
    if os.path.isdir(pdir):
        for root, _, files in os.walk(pdir):
            if "proposal.json" in files:
                try:
                    with open(os.path.join(root, "proposal.json"), encoding="utf-8") as f:
                        out.append(json.load(f))
                except (OSError, ValueError):
                    pass
    return out


def drift(doc, board, sdir=None, pdir=None, write_to=None):
    sops = D.parse_sops(D.sop_dir(sdir))
    props = _proposals(proposals_dir(pdir))
    inst = []
    for c in doc["cards"]:
        sh = c.get("shared")
        if not isinstance(sh, dict):
            continue
        e = next((x for x in doc.get("shared_library") or [] if x["id"] == sh.get("step_id")), None)
        a = sh.get("sop_anchor") or {}
        sec = D.find_section(sops, a.get("sop"), heading=a.get("heading"))
        row = {"card_id": c["id"], "step_id": sh.get("step_id"), "sop": a.get("sop"), "heading": a.get("heading")}
        if not e or not sec:
            row.update(state="missing", board_hash=None, sop_hash=None)
        else:
            nb = D.normalize(e["body"])
            sop_text = a.get("match_text") if a.get("scope") == "fragment" and a.get("match_text") else sec["raw"]
            nsec = D.normalize(sec["raw"])
            if f"shared-step {e['id']}" in sec["raw"] or nb == D.normalize(sop_text) or nb in nsec:
                st = "synced"
            elif any(p.get("status") == "proposed" and not p.get("applied") and e["id"] in
                     ([p.get("step_id")] + [s.get("step_id") for s in p.get("steps", [])]) and p.get("version", 0) >= e["version"]
                     for p in props):
                st = "proposed"
            else:
                st = "drifted"
            row.update(state=st, board_hash=D.sha(nb), sop_hash=D.sha(D.normalize(sop_text)))
        inst.append(row)
    rep = {"generated_at": now(), "board": board, "instances": sorted(inst, key=lambda r: r["card_id"])}
    if write_to is not False:
        D.write_text(os.path.join(D.out_dir(write_to), "drift-report.json"), D.dumps(rep))
    return rep


# ------------------------------------------------------------------ propose
def _block_ranges(lines, s, e):
    """[(start, end_exclusive)] of bullet blocks / paragraph lines in lines[s:e]."""
    out, i = [], s
    while i < e:
        if re.match(r"^\s*([-*]|\d+[.)])\s+", lines[i]):
            j = i + 1
            while j < e and lines[j].strip() and not re.match(r"^\s*([-*]|\d+[.)])\s+", lines[j]) and lines[j][:1] in (" ", "\t"):
                j += 1
            out.append((i, j))
            i = j
        else:
            if lines[i].strip():
                out.append((i, i + 1))
            i += 1
    return out


def _section_span(lines, heading_key):
    start = None
    for i, ln in enumerate(lines):
        m = D.HEAD_RE.match(ln)
        if m and (D.heading_key(m.group(2)) == heading_key or D.fuzzy_title(m.group(2)) == D.fuzzy_title(heading_key)):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if D.END_RE.match(lines[j]):
            end = j
            break
    return start + 1, end


def propose(doc, board, sdir=None, pdir=None, step_id=None, date=None):
    sdir = D.sop_dir(sdir)
    files = D.sop_files(sdir)
    texts, orig = {}, {}
    for k, fp in files.items():
        with open(fp, encoding="utf-8", newline="") as fh:
            raw = fh.read()
        orig[k] = raw.split("\n")
        texts[k] = list(orig[k])
    ents = [entry(doc, step_id)] if step_id else list(doc.get("shared_library") or [])
    skipped, used = [], []
    for e in ents:
        ref = f"See standard step: {e['label']} (shared-step {e['id']})"
        body_lines = e["body"].splitlines()
        for c in instances(doc, e["id"]):
            a = (c["shared"].get("sop_anchor") or {})
            sop, lines = a.get("sop"), texts.get(a.get("sop"))
            if lines is None:
                skipped.append({"card_id": c["id"], "reason": "sop file not found"})
                continue
            if any(f"shared-step {e['id']}" in ln for ln in lines):
                continue  # already references the standard step
            span = _section_span(lines, a.get("heading", ""))
            if not span:
                skipped.append({"card_id": c["id"], "reason": "heading not found"})
                continue
            s, en = span
            new = ["", ref, ""] + body_lines + [""]
            if a.get("scope") == "fragment":
                frs = {D.normalize(x) for x in (a.get("match_text") or "").split("\n") if x.strip()}
                blocks = [(i, j) for i, j in _block_ranges(lines, s, en)
                          if D.normalize(re.sub(r"^\s*([-*]|\d+[.)])\s+", "", " ".join(x.strip() for x in lines[i:j]))) in frs]
                if not blocks:
                    skipped.append({"card_id": c["id"], "reason": "fragment text not found in section"})
                    continue
                first = blocks[0][0]
                bul = "- " if re.match(r"^\s*([-*]|\d+[.)])\s+", lines[first]) else ""
                for i, j in reversed(blocks):
                    del lines[i:j]
                lines[first:first] = [bul + ref] + body_lines
            else:
                lines[s:en] = new
            if sop not in used:
                used.append(sop)
    day = date or datetime.now().strftime("%Y-%m-%d")
    out = os.path.join(proposals_dir(pdir), day)
    sops_meta = []
    for k in sorted(used):
        rel = os.path.relpath(files[k], sdir).replace("\\", "/")
        diff = "\n".join(difflib.unified_diff(orig[k], texts[k], f"a/{rel}", f"b/{rel}", lineterm="", n=2)) + "\n"
        D.write_text(os.path.join(out, k + ".diff"), diff)
        sops_meta.append({"sop": k, "file": rel, "diff": f"proposals/{day}/{k}.diff", "hunks": 0})
    if not sops_meta:
        raise SharedError("nothing to propose (already synced, or no instances)", "conflict")
    for m in sops_meta:  # exact hunk count
        with open(os.path.join(out, m["sop"] + ".diff"), encoding="utf-8") as fh:
            m["hunks"] = sum(1 for ln in fh if ln.startswith("@@ "))
    prop = {"id": f"prop-{day}-0001", "step_id": step_id or (ents[0]["id"] if len(ents) == 1 else "multiple"),
            "version": max(e["version"] for e in ents), "steps": [{"step_id": e["id"], "version": e["version"]} for e in ents],
            "sops": sops_meta, "skipped": skipped, "status": "proposed", "applied": False}
    D.write_text(os.path.join(out, "proposal.json"), D.dumps(prop))
    return prop


# ------------------------------------------------------------------ CLI
def main(argv=None):
    ap = argparse.ArgumentParser(prog="shared_steps.py")
    ap.add_argument("cmd", choices=["drift", "propose"])
    ap.add_argument("--board", default="demo-board")
    ap.add_argument("--board-dir")
    ap.add_argument("--sop-dir")
    ap.add_argument("--out-dir")
    ap.add_argument("--proposals-dir")
    ap.add_argument("--step-id")
    ap.add_argument("--date")
    a = ap.parse_args(argv)
    doc = load(a.board, a.board_dir)
    try:
        if a.cmd == "drift":
            r = drift(doc, a.board, a.sop_dir, a.proposals_dir, write_to=a.out_dir)
            print(f"drift: {len(r['instances'])} instances -> {os.path.join(D.out_dir(a.out_dir), 'drift-report.json')}")
        else:
            r = propose(doc, a.board, a.sop_dir, a.proposals_dir, a.step_id, a.date)
            print(f"propose: {r['id']} {len(r['sops'])} sop diffs")
    except SharedError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
