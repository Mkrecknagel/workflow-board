"""Point the board registry at an EMPTY root for every test (and every subprocess they spawn), so temp-copy servers see only the
boards copied into them and never the real registrations. tests/_real.py reads the real registry explicitly."""
import os
import tempfile

os.environ["BOARD_REGISTRY_ROOT"] = tempfile.mkdtemp(prefix="wb-emptyreg-")
# The headless readability check-on-save (serve.py) is off for every test and subprocess unless a test turns it on explicitly (tests/test_readability_save.py).
os.environ.setdefault("WB_CHECK_ON_SAVE", "0")

# Validation resolves skill refs against an install root. Use a synthetic one so tests do not depend on the machine's real install.
_claude = tempfile.mkdtemp(prefix="wb-claude-")
for _s in ("demo-compile", "demo-dashboard", "graph-loop"):
    os.makedirs(os.path.join(_claude, "skills", _s), exist_ok=True)
    with open(os.path.join(_claude, "skills", _s, "SKILL.md"), "w", encoding="utf-8") as _f:
        _f.write("---\nname: %s\ndescription: demo skill\n---\n# %s\n" % (_s, _s))
os.environ["WB_CLAUDE_DIR"] = _claude
os.makedirs(os.path.join(_claude, "hooks"), exist_ok=True)
with open(os.path.join(_claude, "hooks", "safety-gate.sh"), "w", encoding="utf-8") as _f:
    _f.write("#!/bin/sh\nexit 0\n")
