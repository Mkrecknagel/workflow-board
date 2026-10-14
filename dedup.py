#!/usr/bin/env python3
"""Shared-step duplicate detector for workflow-board (stdlib only, offline, deterministic).

Stage 1  scan     SOP sections + fragments under each board step card -> dedup/candidates.json
Stage 2  judge    pluggable: heuristic fallback (offline) or --judge-cmd; writes dedup/verdicts.json
Stage 3  tickets  one [DEDUP-PROPOSAL] kind:task ticket per admitted cluster (tickets_store.create)
         suppress closed/rejected proposal tickets -> dedup/suppressions.json (scan also syncs this)

Never writes boards, the shared library, or SOP files. SOP files are read-only inputs.
Paths: --board-dir/--sop-dir/--out-dir or env WB_BOARD_DIR / WB_SOP_DIR / WB_DEDUP_DIR.
"""
import argparse
import difflib
import glob
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tickets_store as TS  # noqa: E402
import registry as REG  # noqa: E402

THRESH = {"section_jaccard": 0.40, "section_cosine": 0.55, "fragment_jaccard": 0.45, "exact": 0.85}
ADMIT = 0.85
WHOLE_COVER = 0.80
MIN_FRAGMENT_TOKENS = 6
STOP_PHRASES = ("you must", "the owner", "constraints")
TAG = "[DEDUP-PROPOSAL]"
HEAD_RE = re.compile(r"^###\s+(?:Step\s+)?(\d+[a-z]?)[.:]?\s+(.*?)\s*$")
END_RE = re.compile(r"^#{1,3}\s")


def board_dir(a=None):
    return a or os.environ.get("WB_BOARD_DIR") or os.path.join(HERE, "boards")


def sop_dir(a=None):
    return a or os.environ.get("WB_SOP_DIR") or os.path.expanduser("~/.claude/sops")


def out_dir(a=None):
    return a or os.environ.get("WB_DEDUP_DIR") or os.path.join(HERE, "dedup")


# ---------------------------------------------------------------- text
def strip_fences(t):
    return re.sub(r"```.*?```", " ", t, flags=re.S)


def normalize(t):
    t = strip_fences(t or "").lower()
    t = re.sub(r"\{[^}]*\}", " ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = " " + re.sub(r"\s+", " ", t).strip() + " "
    for p in STOP_PHRASES:
        t = t.replace(" " + p + " ", " ")
    return re.sub(r"\s+", " ", t).strip()


def shingles(toks, n=3):
    if not toks:
        return set()
    if len(toks) < n:
        return {tuple(toks)}
    return {tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def heading_key(title):
    """lowercase + collapsed whitespace; the form stored in cluster member keys and sop_anchor.heading."""
    return re.sub(r"\s+", " ", (title or "").strip().lower())


def fuzzy_title(t):
    t = re.sub(r"\([^)]*\)", " ", (t or "").lower())
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", t)).strip()


def fragments(raw_body):
    """Bullets and sentences of >= MIN_FRAGMENT_TOKENS normalized tokens (code fences dropped)."""
    body = strip_fences(raw_body)
    items, cur = [], None
    for line in body.splitlines():
        s = line.strip()
        if not s:
            cur = None
            continue
        if re.match(r"^([-*]|\d+[.)])\s+", s):
            cur = [re.sub(r"^([-*]|\d+[.)])\s+", "", s)]
            items.append(cur)
        elif cur is not None and line[:1] in (" ", "\t"):
            cur.append(s)
        else:
            cur = None
            for sent in re.split(r"(?<=[.!?])\s+", s):
                if sent:
                    items.append([sent])
    out = []
    for it in items:
        raw = " ".join(it).strip()
        n = normalize(raw)
        if len(n.split()) >= MIN_FRAGMENT_TOKENS:
            out.append({"raw": raw, "norm": n})
    return out


def lead_sentence(raw_body):
    for line in strip_fences(raw_body).splitlines():
        s = line.strip()
        if s and not s.startswith("**Constraints"):
            return re.split(r"(?<=[.!?])\s+", s)[0]
    return ""


# ---------------------------------------------------------------- SOP parsing
def load_anchors(path=None):
    p = path or os.path.join(HERE, "dedup", "anchors.json")
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    return [dict(a, rx=re.compile(a["pattern"])) for a in d["anchors"]]


def parse_sops(sdir):
    """{ 'SOP-01': [ {num,title,key,fuzzy,raw} ... ] } from <sdir>/**/NN-*.sop.md (read-only)."""
    out = {}
    files = sorted(glob.glob(os.path.join(sdir, "**", "[0-9][0-9]-*.sop.md"), recursive=True))
    for fp in files:
        nn = os.path.basename(fp)[:2]
        with open(fp, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
        secs, cur = [], None
        for ln in lines:
            m = HEAD_RE.match(ln)
            if m:
                cur = {"num": m.group(1), "title": m.group(2), "key": heading_key(m.group(2)),
                       "fuzzy": fuzzy_title(m.group(2)), "body": []}
                secs.append(cur)
            elif END_RE.match(ln):
                cur = None
            elif cur is not None:
                cur["body"].append(ln)
        for s in secs:
            s["raw"] = "\n".join(s.pop("body")).strip("\n")
        out["SOP-" + nn] = secs
    return out


def sop_files(sdir):
    """{'SOP-01': absolute path} for the NN-*.sop.md files under sdir."""
    out = {}
    for fp in sorted(glob.glob(os.path.join(sdir, "**", "[0-9][0-9]-*.sop.md"), recursive=True)):
        out["SOP-" + os.path.basename(fp)[:2]] = fp
    return out


def find_section(sops, sop, label=None, heading=None):
    secs = sops.get(sop) or []
    num, title = None, heading
    if label:
        m = re.match(r"^(\d+[a-z]?)\s+(.*)$", label.strip())
        num, title = (m.group(1), m.group(2)) if m else (None, label)
    ft = fuzzy_title(title)
    for s in secs:
        if ft and s["fuzzy"] == ft:
            return s
    if num:
        c = [s for s in secs if s["num"] == num]
        if len(c) == 1:
            a, b = set(ft.split()), set(c[0]["fuzzy"].split())
            if a and b and len(a & b) / len(a | b) >= 0.5:
                return c[0]
    return None


# ---------------------------------------------------------------- units & scoring
def build_unit(uid, title, raw, anchors):
    frs = fragments(raw)
    sec_norm = normalize(title + " " + raw)
    toks = sec_norm.split()
    for fr in frs:
        fr["sh"] = shingles(fr["norm"].split())
        fr["anch"] = {a["name"] for a in anchors if a["rx"].search(fr["norm"])}
    lead = lead_sentence(raw)
    lead_anch = {a["name"] for a in anchors if a["rx"].search(normalize(lead))}
    anch = set(lead_anch)
    for f in frs:
        anch |= f["anch"]
    return {"id": uid, "title": title, "raw": raw, "norm": sec_norm, "toks": toks,
            "sh": shingles(toks), "frags": frs, "lead_anch": lead_anch, "anch": anch}


class Tfidf:
    def __init__(self, norms):
        self.n = len(norms)
        df = Counter()
        for t in norms:
            df.update(set(t.split()))
        self.idf = {w: math.log((1 + self.n) / (1 + c)) + 1.0 for w, c in df.items()}

    def vec(self, norm):
        tf = Counter(norm.split())
        v = {w: c * self.idf.get(w, 1.0) for w, c in tf.items()}
        nrm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {w: x / nrm for w, x in v.items()}

    @staticmethod
    def cos(a, b):
        if len(a) > len(b):
            a, b = b, a
        return sum(x * b.get(w, 0.0) for w, x in a.items())


def pair_eval(a, b, cos):
    """Scores + trigger flags + matched fragment indexes for one unit pair."""
    sj = jaccard(a["sh"], b["sh"])
    best, bi, bj = 0.0, None, None
    for i, fa in enumerate(a["frags"]):
        for j, fb in enumerate(b["frags"]):
            s = jaccard(fa["sh"], fb["sh"])
            if s > best:
                best, bi, bj = s, i, j
    shared_anchor = sorted(a["anch"] & b["anch"])
    sec_trig = sj >= THRESH["section_jaccard"] or cos >= THRESH["section_cosine"]
    frag_trig = best >= THRESH["fragment_jaccard"]
    anch_trig = bool(shared_anchor)
    return {"sj": sj, "cos": cos, "frag": best, "bi": bi, "bj": bj, "anchor": shared_anchor,
            "sec": sec_trig, "fragt": frag_trig, "anch": anch_trig,
            "candidate": sec_trig or frag_trig or anch_trig,
            "score": round(max(sj, cos if sec_trig else 0.0, best), 4)}


def scope_and_text(u, pe, side):
    """('whole'|'fragment', matched raw text) for unit u in pair evaluation pe."""
    idx = pe["bi"] if side == "a" else pe["bj"]
    if pe["sec"]:
        return "whole", u["raw"]
    matched = []
    if pe["anch"]:
        names = set(pe["anchor"])
        matched = [f for f in u["frags"] if f["anch"] & names]
        if u["lead_anch"] & names:
            return "whole", u["raw"]
    if not matched and pe["fragt"] and idx is not None:
        matched = [u["frags"][idx]]
    if not matched:
        return "whole", u["raw"]
    cov = sum(len(f["norm"].split()) for f in matched) / max(1, len(u["toks"]))
    if cov >= WHOLE_COVER:
        return "whole", u["raw"]
    return "fragment", "\n".join(f["raw"] for f in matched)


# ---------------------------------------------------------------- scan
def load_board(board, bdir):
    with open(REG.resolve(board, bdir), encoding="utf-8") as f:
        return json.load(f)


class UF:
    def __init__(self):
        self.p = {}

    def f(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def u(self, a, b):
        ra, rb = self.f(a), self.f(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def member_key(m):
    return f"{m['sop']}:{m['heading']}:{m['scope']}"


def cluster_id_of(members):
    return sha(json.dumps(sorted(member_key(m) for m in members)))


def scan_units(board_doc, sops, anchors):
    units = []
    for c in sorted((c for c in board_doc["cards"] if c.get("type") == "step"), key=lambda c: c["id"]):
        if c.get("shared"):
            continue  # already resolved to a library entry
        sec = find_section(sops, c.get("sop"), label=c.get("label"))
        if not sec:
            continue
        u = build_unit(c["id"], sec["title"], sec["raw"], anchors)
        u.update(sop=c.get("sop"), heading=sec["key"])
        units.append(u)
    return units


def scan_clusters(units):
    tf = Tfidf([u["norm"] for u in units])
    vec = {u["id"]: tf.vec(u["norm"]) for u in units}
    uf, pairs = UF(), {}
    for i in range(len(units)):
        for j in range(i + 1, len(units)):
            a, b = units[i], units[j]
            pe = pair_eval(a, b, Tfidf.cos(vec[a["id"]], vec[b["id"]]))
            if pe["candidate"]:
                pairs[(a["id"], b["id"])] = pe
                uf.u(a["id"], b["id"])
    by = {u["id"]: u for u in units}
    groups = {}
    for (x, y) in pairs:
        groups.setdefault(uf.f(x), set()).update((x, y))
    clusters = []
    for ids in groups.values():
        ids = sorted(ids)
        mem = {}
        for (x, y), pe in sorted(pairs.items()):
            if x in ids and y in ids:
                for side, cid in (("a", x), ("b", y)):
                    sc, txt = scope_and_text(by[cid], pe, side)
                    cur = mem.get(cid)
                    # keep the widest scope seen across this member's pairs, best score
                    if cur is None or (sc == "whole" and cur["scope"] != "whole"):
                        mem[cid] = {"scope": sc, "text": txt, "score": pe["score"] if cur is None else max(cur["score"], pe["score"])}
                    else:
                        cur["score"] = max(cur["score"], pe["score"])
        anchors = sorted({a for (x, y), pe in pairs.items() if x in ids and y in ids for a in pe["anchor"]})
        members = []
        for cid in ids:
            m, u = mem[cid], by[cid]
            members.append({"card_id": cid, "sop": u["sop"], "heading": u["heading"], "scope": m["scope"],
                            "text_hash": sha(normalize(m["text"])), "score": round(m["score"], 4),
                            "excerpt": m["text"][:600]})
        scores = [pe["score"] for (x, y), pe in pairs.items() if x in ids and y in ids]
        clusters.append({"cluster_id": cluster_id_of(members), "anchor": anchors[0] if anchors else None,
                         "members": members, "bypass_stage2": bool(scores) and min(scores) >= THRESH["exact"]})
    clusters.sort(key=lambda c: c["cluster_id"])
    return clusters


def dumps(o):
    return json.dumps(o, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(text.encode("utf-8"))


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def input_stamp(paths):
    from datetime import datetime, timezone
    ts = 0
    for p in paths:
        try:
            ts = max(ts, int(os.path.getmtime(p)))
        except OSError:
            pass
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def apply_suppressions(clusters, supp):
    rows = {r["cluster_id"]: set(r.get("member_text_hashes", [])) for r in supp.get("suppressed", [])}
    keep = []
    for c in clusters:
        if c["cluster_id"] in rows and rows[c["cluster_id"]] == {m["text_hash"] for m in c["members"]}:
            continue
        keep.append(c)
    return keep


def scan(board, bdir=None, sdir=None, odir=None, anchors_path=None):
    bdir, sdir, odir = board_dir(bdir), sop_dir(sdir), out_dir(odir)
    doc = load_board(board, bdir)
    sync_suppressions(board, bdir, odir)
    sops = parse_sops(sdir)
    anchors = load_anchors(anchors_path)
    units = scan_units(doc, sops, anchors)
    clusters = apply_suppressions(scan_clusters(units), read_json(os.path.join(odir, "suppressions.json"), {}))
    srcs = [REG.resolve(board, bdir)] + sorted(glob.glob(os.path.join(sdir, "**", "*.sop.md"), recursive=True))
    out = {"generated_at": input_stamp(srcs), "board": board, "thresholds": dict(THRESH),
           "units": len(units), "clusters": clusters}
    write_text(os.path.join(odir, "candidates.json"), dumps(out))
    return out


# ---------------------------------------------------------------- stage 2 (pluggable)
def heuristic_judge(cluster, anchors_meta):
    """Offline fallback, NOT semantic: anchor-consistent clusters get the 0.85 admit floor, others their lexical score."""
    best = max(m["score"] for m in cluster["members"])
    eq = ADMIT if cluster.get("anchor") else best
    if cluster.get("bypass_stage2"):
        eq = max(eq, best)
    pick = sorted(cluster["members"], key=lambda m: (m["scope"] != "whole", len(m["excerpt"]), m["card_id"]))[0]
    toks = [set(normalize(m["excerpt"]).split()) for m in cluster["members"]]
    common = set.intersection(*toks) if toks else set()
    div = [f"{m['sop']} {m['heading']} ({m['scope']}) adds: " + ", ".join(sorted(t - common)[:8]) for m, t in zip(cluster["members"], toks)]
    title = (anchors_meta.get(cluster.get("anchor")) or {}).get("title") or pick["heading"]
    return {"cluster_id": cluster["cluster_id"], "equivalence": round(eq, 4), "divergences": div,
            "canonical_text": pick["excerpt"], "canonical_title": title, "judge": "heuristic"}


def judge(odir=None, judge_cmd=None, anchors_path=None):
    odir = out_dir(odir)
    cand = read_json(os.path.join(odir, "candidates.json"), {"clusters": []})
    am = {a["name"]: a for a in load_anchors(anchors_path)}
    todo = [c for c in cand["clusters"] if not c.get("bypass_stage2")]
    verdicts = {c["cluster_id"]: heuristic_judge(c, am) for c in cand["clusters"]}
    if judge_cmd:  # external judge reads batches of 5 clusters on stdin, prints {"clusters":[verdict...]}
        for i in range(0, len(todo), 5):
            batch = {"clusters": todo[i:i + 5]}
            r = subprocess.run(judge_cmd, shell=True, input=json.dumps(batch), capture_output=True, text=True, timeout=600)
            if r.returncode == 0:
                for v in json.loads(r.stdout).get("clusters", []):
                    if v.get("cluster_id") in verdicts:
                        verdicts[v["cluster_id"]] = dict(verdicts[v["cluster_id"]], **v, judge="external")
    out = {"clusters": [verdicts[c["cluster_id"]] for c in cand["clusters"]]}
    write_text(os.path.join(odir, "verdicts.json"), dumps(out))
    return out


# ---------------------------------------------------------------- stage 3 tickets / suppressions
def build_ticket_text(cl, verdict, board_name):
    canon = verdict.get("canonical_text", "")
    title = verdict.get("canonical_title") or cl["members"][0]["heading"]
    lanes = ", ".join(sorted({m["sop"] for m in cl["members"]}))
    head = [f"{TAG} Standardize: {title}", f"cluster: {cl['cluster_id']}", f"board: {board_name}",
            f"blast radius: {len(cl['members'])} places in lanes {lanes}", "members:"]
    for m in cl["members"]:
        head.append(f"- {m['card_id']} | {m['sop']} | {m['heading']} | {m['scope']} | {m['text_hash']}")
    head.append(f"equivalence: {verdict.get('equivalence')} ({verdict.get('judge', 'verdict')})")
    head.append("canonical_text: " + canon.replace("\n", " ")[:500])
    base = "\n".join(head)
    diffs = []
    for m in cl["members"]:
        d = "\n".join(difflib.unified_diff(canon.splitlines(), m["excerpt"].splitlines(),
                                           "canonical", f"{m['sop']}:{m['heading']}", lineterm="", n=1))
        diffs.append(f"--- diff {m['card_id']} ---\n{d}")
    text = base + "\n" + "\n".join(diffs)
    if TS.classify(text) == "governance":  # keep kind=task: drop SOP excerpts that mention governance paths
        text = base + "\n(diffs omitted: see dedup/candidates.json)"
    cap = TS.TEXT_CAP - 64
    while len(text.encode("utf-8")) > cap and diffs:
        diffs = [d[: len(d) // 2] for d in diffs]
        text = base + "\n" + "\n".join(diffs) + "\n[diffs truncated]"
        if all(len(d) < 8 for d in diffs):
            text = base + "\n[diffs truncated]"
            break
    return text


def tickets(board, bdir=None, odir=None, verdicts_path=None):
    bdir, odir = board_dir(bdir), out_dir(odir)
    sync_suppressions(board, bdir, odir)
    cand = read_json(os.path.join(odir, "candidates.json"), None)
    if cand is None:
        raise SystemExit("no candidates.json: run `dedup.py scan` first")
    vp = verdicts_path or os.path.join(odir, "verdicts.json")
    vd = {v["cluster_id"]: v for v in read_json(vp, {"clusters": []})["clusters"]} if os.path.isfile(vp) else None
    am = {a["name"]: a for a in load_anchors()}
    supp = read_json(os.path.join(odir, "suppressions.json"), {})
    existing = " ".join(t["text"] for t in TS.list_tickets(board, include_archived=True) if t["text"].startswith(TAG))
    made = []
    for cl in apply_suppressions(cand["clusters"], supp):
        v = vd.get(cl["cluster_id"]) if vd is not None else heuristic_judge(cl, am)
        if v is None and cl.get("bypass_stage2"):
            v = heuristic_judge(cl, am)
        if v is None or not (cl.get("bypass_stage2") or v.get("equivalence", 0) >= ADMIT):
            continue
        if "cluster: " + cl["cluster_id"] in existing:
            continue
        t = TS.create(board, cl["members"][0]["card_id"], build_ticket_text(cl, v, board), author="agent", kind="task")
        made.append(t["id"])
    return made


def parse_ticket(t):
    m = re.search(r"^cluster: ([0-9a-f]{64})$", t["text"], re.M)
    if not (t["text"].startswith(TAG) and m):
        return None
    mem = []
    for ln in re.findall(r"^- (\S+) \| ([^|]+) \| ([^|]+) \| (\w+) \| ([0-9a-f]{64})$", t["text"], re.M):
        mem.append({"card_id": ln[0], "text_hash": ln[4]})
    return m.group(1), mem


def sync_suppressions(board, bdir=None, odir=None):
    """Closed (done) or archived [DEDUP-PROPOSAL] tickets whose cluster was not replaced -> suppressions.json."""
    bdir, odir = board_dir(bdir), out_dir(odir)
    path = os.path.join(odir, "suppressions.json")
    cur = read_json(path, {"suppressed": []})
    have = {r["cluster_id"] for r in cur["suppressed"]}
    try:
        doc = load_board(board, bdir)
    except OSError:
        return cur
    shared_ids = {c["id"] for c in doc["cards"] if c.get("shared")}
    changed = False
    for t in TS.list_tickets(board, include_archived=True):
        pt = parse_ticket(t)
        if not pt or not (t["status"] == "done" or t.get("archived")):
            continue
        cid, mem = pt
        if any(m["card_id"] in shared_ids for m in mem) or cid in have:
            continue
        cur["suppressed"].append({"cluster_id": cid, "member_text_hashes": sorted(m["text_hash"] for m in mem),
                                  "ticket_id": t["id"], "at": t.get("updated_at") or t.get("created_at")})
        have.add(cid)
        changed = True
    if changed:
        cur["suppressed"].sort(key=lambda r: r["cluster_id"])
        write_text(path, dumps(cur))
    return cur


# ---------------------------------------------------------------- fixture evaluation
def classify_pair(a_text, b_text, corpus_texts, anchors):
    units = [build_unit("a", "", a_text, anchors), build_unit("b", "", b_text, anchors)]
    tf = Tfidf([normalize(t) for t in corpus_texts])
    return pair_eval(units[0], units[1], Tfidf.cos(tf.vec(units[0]["norm"]), tf.vec(units[1]["norm"])))["candidate"]


def evaluate_fixture(path, anchors=None):
    anchors = anchors if anchors is not None else load_anchors()
    pairs = read_json(path, {"pairs": []})["pairs"]
    corpus = [t for p in pairs for t in (p["a"], p["b"])]
    tp = fp = fn = tn = 0
    for p in pairs:
        pred = classify_pair(p["a"], p["b"], corpus, anchors)
        if pred and p["duplicate"]:
            tp += 1
        elif pred:
            fp += 1
        elif p["duplicate"]:
            fn += 1
        else:
            tn += 1
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": tp / (tp + fp) if tp + fp else 1.0, "recall": tp / (tp + fn) if tp + fn else 1.0}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dedup.py")
    ap.add_argument("cmd", choices=["scan", "judge", "tickets", "suppress", "fixture"])
    ap.add_argument("--board", default="demo-board")
    ap.add_argument("--board-dir")
    ap.add_argument("--sop-dir")
    ap.add_argument("--out-dir")
    ap.add_argument("--verdicts")
    ap.add_argument("--judge-cmd")
    ap.add_argument("--fixture", default=os.path.join(HERE, "fixtures", "dedup-pairs.json"))
    a = ap.parse_args(argv)
    if a.cmd == "scan":
        r = scan(a.board, a.board_dir, a.sop_dir, a.out_dir)
        print(f"scan: {r['units']} units, {len(r['clusters'])} clusters -> {os.path.join(out_dir(a.out_dir), 'candidates.json')}")
    elif a.cmd == "judge":
        r = judge(a.out_dir, a.judge_cmd)
        print(f"judge: {len(r['clusters'])} verdicts")
    elif a.cmd == "tickets":
        ids = tickets(a.board, a.board_dir, a.out_dir, a.verdicts)
        print("tickets: " + (", ".join(ids) if ids else "none"))
    elif a.cmd == "suppress":
        r = sync_suppressions(a.board, a.board_dir, a.out_dir)
        print(f"suppressed: {len(r['suppressed'])}")
    else:
        print(json.dumps(evaluate_fixture(a.fixture)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
