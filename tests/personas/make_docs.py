"""Generates docs/personas/README.md and persona-1..3.md from personas.json (single source of truth for briefs and scripts)."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(os.path.dirname(HERE)), "docs", "personas")
SPEC = json.load(open(os.path.join(HERE, "personas.json"), encoding="utf-8"))

FLAVOUR = {
    "dana": "Dana clicks the wrong thing on purpose: she reads the first screen for plain-language help, hunts for what Deploy means, builds a board with her own words, and does select-all then Delete.",
    "erin": "Erin labels cards in her own words, adds notes for her team, moves things around on a phone, and sets cards to run in the background. She never opens a terminal and expects every visual change to save by itself.",
    "frank": "Frank toggles enforce and observe, requests a deploy, restores from history, zooms through nested boards, and deliberately sends unsafe requests (disable an immutable card, background an approval gate, edit during a zoom handoff).",
}


def main():
    os.makedirs(DOCS, exist_ok=True)
    rd = ["# Persona testing for the workflow board", "",
          "Three personas exercise every button on a temp copy of the board, on desktop (1440x900, mouse) and phone (390x844, touch). Briefs and ordered scripts live in `tests/personas/personas.json`; this folder is generated from it and is reusable (no personal data beyond the persona descriptions).", "",
          "## Run it", "",
          "    python tests/personas/run_personas.py --round 1      # all three personas, both viewports",
          "    python tests/personas/run_personas.py --only erin --steps f,g",
          "    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/personas/test_harness.py -q", "",
          "Each run copies the workspace to a temp directory, replaces `boards/` with three seeded starter boards (`starter-hub` is the deployed board; `starter-intake` and `starter-publish` are inner boards), starts the server on a free loopback port (never 8765), and hashes the real `boards/` before and after (self-test: untouched passes, tamper fails). A changed real board fails the run.", "",
          "Driver: `playwright-core` (the engine behind `playwright-cli`) headless from node, because a persona script is hundreds of stateful steps that need CDP touch gestures and offline control. The `playwright-cli` binary itself drives one smoke step on both viewports in `test_harness.py`.", "",
          "## Severity rubric", "",
          "- **Major**: crash, blank screen or uncaught exception; data loss (an edit or board vanishes, wrong-board write, overwritten board); stuck flow (no way forward or back without reload); unsafe action enabled (gate bypass, immutable card disabled, run_mode background on approval, enforcement hash broken by a visual edit, real boards modified).",
          "- **Minor**: wrong label or copy, awkward-but-recoverable flow, cosmetic glitch, phone ergonomics.",
          "- **Note**: suggestion. Only Major blocks done.", "",
          "## Output", "",
          "- `findings-<persona>.md`: counts, step results per viewport, findings with repro, evidence and suspected file.",
          "- `rollup.json`: per round counts by severity per persona, the Major list, guard result, `final.major_open`.",
          "- `<persona>/<viewport>-<step>.json` (step results and network facts) and `.png` (saved screenshots, evidence paths only).", "",
          "## Personas", ""]
    for n, p in enumerate(SPEC["personas"], 1):
        rd.append("%d. [%s](persona-%d.md)" % (n, p["name"], n))
    with open(os.path.join(DOCS, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(rd) + "\n")
    for n, p in enumerate(SPEC["personas"], 1):
        t = ["# Persona %d: %s" % (n, p["name"]), "", "## Brief", "", p["brief"], "", FLAVOUR[p["id"]], "",
             "Card vocabulary used when building: " + ", ".join(p["params"]["labelWords"]) + ".", "",
             "## Script (ordered, each step is recorded pass or fail with evidence)", "",
             "| id | action | expected | evidence path |", "|---|---|---|---|"]
        for s in SPEC["steps"]:
            t.append("| %s. | %s | %s | `%s` |" % (s["id"], s["action"], s["expected"], s["evidence"].replace("<persona>", p["id"])))
        t += ["", "Steps b to g also run on the phone viewport with touch gestures (step h); step i is the abuse pass."]
        with open(os.path.join(DOCS, "persona-%d.md" % n), "w", encoding="utf-8") as fh:
            fh.write("\n".join(t) + "\n")
    print("docs written")


if __name__ == "__main__":
    main()
