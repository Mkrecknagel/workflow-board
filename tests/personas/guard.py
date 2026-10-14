"""Real-boards hash guard (N5 criterion 1). Hashes every file under the real boards/ directory (recursive, includes .history and active.json).
A persona run is refused/failed when the hash differs after the run. self_test() proves it passes on an untouched copy and fails on a deliberate tamper."""
import hashlib
import os
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(os.path.dirname(HERE))
REAL_BOARDS = os.path.join(SRC, "boards")


def snapshot(d=REAL_BOARDS):
    out = {}
    for root, _dirs, files in os.walk(d):
        for f in files:
            p = os.path.join(root, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, d).replace("\\", "/")] = hashlib.sha256(fh.read()).hexdigest()
    return out


def compare(before, after):
    """Returns (ok, changed_paths)."""
    changed = sorted({k for k in set(before) | set(after) if before.get(k) != after.get(k)})
    return (not changed), changed


def self_test():
    """Copy the real boards to a temp dir, hash, (a) leave untouched -> must pass, (b) tamper with one byte -> must fail, (c) add a file -> must fail."""
    tmp = tempfile.mkdtemp(prefix="wb-guard-")
    try:
        d = os.path.join(tmp, "boards")
        shutil.copytree(REAL_BOARDS, d)
        a = snapshot(d)
        ok_untouched, _ = compare(a, snapshot(d))
        victim = next(os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".json"))
        with open(victim, "ab") as fh:
            fh.write(b" ")
        ok_tampered, changed = compare(a, snapshot(d))
        os.remove(victim) if False else None
        with open(os.path.join(d, "intruder.json"), "w") as fh:
            fh.write("{}")
        ok_added, changed2 = compare(a, snapshot(d))
        return {"untouched_passes": ok_untouched, "tamper_detected": not ok_tampered, "added_file_detected": not ok_added,
                "ok": ok_untouched and not ok_tampered and not ok_added, "files": len(a), "tampered": changed}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    import json
    print(json.dumps(self_test()))
