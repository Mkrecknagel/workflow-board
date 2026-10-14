import hashlib, json, subprocess, sys, tempfile, unittest
from pathlib import Path

WB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WB / "tests"))
import _real  # noqa: E402
HOOKS = Path.home() / ".Codex" / "hooks.json"


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def run(*a):
    return subprocess.run([sys.executable, str(WB / "adapters" / "codex.py"), *a], capture_output=True, text=True, env=_real.real_env())


class T(unittest.TestCase):

    def test_refuses_codex_home_out(self):
        r = run("--board", "demo-board", "--out", str(Path.home() / ".Codex" / "x"))
        self.assertEqual(r.returncode, 2)


    def test_kiro_doc_nine_types(self):
        t = (WB / "docs" / "adapters" / "kiro.md").read_text()
        for ty in "trigger input skill hook gate agent output tool step".split():
            self.assertIn("| %s |" % ty, t)
        self.assertIn("UNVERIFIED", t)


if __name__ == "__main__":
    unittest.main()
