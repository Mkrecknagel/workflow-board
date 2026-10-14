#!/usr/bin/env python3
"""Re-hash boards/active.json with the v5 semantic rule (style, pin and note cards excluded).

  python scripts/rehash_active.py <board> [--boards-dir DIR]

Backs up active.json to active.json.bak-<YYYY-MM-DD>-v5 (never overwrites an existing backup), then rewrites ONLY `hash` and
`hash_basis`; board, deployed_at and by are untouched, so enforcement stays on. Refuses if <board> is not the deployed board."""
import datetime
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import serve_v4_runs as R  # noqa: E402

BASIS = "semantic v5 (layout+style+pin+notes excluded)"


def rehash(board, boards_dir=None, today=None):
    d = boards_dir or os.path.join(ROOT, "boards")
    ap = os.path.join(d, "active.json")
    sys.path.insert(0, ROOT)
    import registry as REG
    bp = REG.resolve(board, d)
    if not os.path.isfile(ap):
        raise SystemExit("rehash: no active.json")
    if not os.path.isfile(bp):
        raise SystemExit(f"rehash: no board file {bp}")
    with open(ap, encoding="utf-8") as fh:
        active = json.load(fh)
    if active.get("board") != board:
        raise SystemExit(f"rehash: active board is '{active.get('board')}', not '{board}'")
    with open(bp, encoding="utf-8") as fh:
        doc = json.load(fh)
    bak = f"{ap}.bak-{today or datetime.date.today().isoformat()}-v5"
    if os.path.exists(bak):
        raise SystemExit(f"rehash: backup {bak} already exists; refusing to overwrite")
    shutil.copy2(ap, bak)
    old = active.get("hash")
    active["hash"] = R.semantic_hash(doc)
    active["hash_basis"] = BASIS
    tmp = ap + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(active, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, ap)
    return {"board": board, "old_hash": old, "new_hash": active["hash"], "backup": bak}


if __name__ == "__main__":
    a = sys.argv[1:]
    bd = None
    if "--boards-dir" in a:
        i = a.index("--boards-dir")
        bd = a[i + 1]
        del a[i:i + 2]
    if len(a) != 1:
        raise SystemExit(__doc__)
    r = rehash(a[0], bd)
    print(f"rehashed {r['board']}: {str(r['old_hash'])[:8]} -> {r['new_hash'][:8]} (backup {r['backup']})")
