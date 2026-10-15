# Deploy: board to agent files

The board is the control panel. Your agent files are the system of record. Deploy compiles a board into files the
agent runs, and never writes until you approve the diff.

## What a line compiles to

    <skills folder>/<name>/SKILL.md    the entry point every harness discovers (Claude Code, Codex, Kiro, ~/.agents)
    <skills folder>/<name>/line.json   manifest: stations in run order with refs, contracts and source provenance

`SKILL.md` front matter carries `name`, `description`, the line's first `inputs` and last `outputs`, and
`generated_from`. The body lists one `### N. <station>` per station in edge order (a topological walk; ties broken
left to right). Each station says what to run (a skill, a tool, an agent, an approval gate, or the step's own
instruction) and its declared inputs and outputs. Stations with no declared contract are flagged in the proposal,
and the skill tells the agent to state what it used and produced. Hooks are optional add-ons: a hook card becomes
a station that tells the agent to check the rule itself when its harness has no hooks. A loop in the line is refused.

## Use it

From the page: **Deploy**, choose the skills folder and name, **Propose**, read the diff, **Approve and create files**.

From a terminal:

    python deploy.py propose <board> --target ~/.agents/skills [--name my-line]
    python deploy.py apply <proposal id> --approve
    python deploy.py list

Proposals are staged in `$WB_DEPLOY_DIR` (default `~/.workflow-board/deploy/<id>/`: `files/`, `plan.json`, `diff.txt`).
`WB_DEPLOY_TARGET` sets the page's default skills folder.

## Updating a deployed line

Run Deploy again after you change the board.

- A file Deploy wrote earlier for this board (its `line.json` records the board and each file's hash) is **updated**.
  The proposal shows a unified diff. Apply snapshots the old file first; `python deploy.py rollback <id>` restores it,
  or removes the files a first deploy created. Rollback refuses if the files were edited after the deploy.
- A file Deploy did not write, or one edited by hand since, is a **conflict** and is never overwritten.
  `propose --adopt` takes it over; the old copy is still snapshotted, so the adoption can be rolled back.
- **Stale sources.** Each station remembers the hash of the SOP or skill it was imported from (`src_sha`). If that file
  changed since the import, apply refuses: re-run `wb_init.py`, check the board, propose again.
- **Drift check.** After apply, every written file is re-read and compared with the plan; `drift` must be empty.
- **Module reuse.** Cards inserted from a module (`origin_module`) collapse into one station that calls the installed
  skill of the same name, when one exists in the target folder or the scanned library. Otherwise the steps are inlined.

## Checking a run in any harness

    python deploy.py check <skills folder>/<name> --workdir <where the agent ran>

passes when every station's declared output files exist. This is how runs are compared across harnesses: by declared
artifacts and finishing the line, not by identical text.

## Four-harness acceptance

    python tests/harness/four_harness.py --static                       # in tests/run_all.py
    python tests/harness/four_harness.py --live kiro,claude,codex,agents  # opt-in, runs the real CLIs in temp folders

Live result on 2026-10-15 (kiro-cli 2.31.1, Claude Code, Codex CLI): all four ran the three-station fixture line and
produced all three declared outputs. How each found the skill differed:

| Harness | Skill placed in | How the agent reached it |
|---|---|---|
| Kiro | `.kiro/skills/` | read `SKILL.md` directly (native discovery) |
| Claude Code | `.claude/skills/` | no search seen in its output (native discovery assumed, not proven by the log) |
| Codex | `.codex/skills/` | searched the project folder (`rg --files`), then read `SKILL.md` |
| plain `.agents` (run with Kiro) | `.agents/skills/` | searched the filesystem, then read `SKILL.md` |

So the line itself is portable, but for Codex and a plain `.agents` setup the skill was found by searching, not by
native discovery. Deploy into each harness's native skills folder (for Codex, its user skills folder) or name the
skill path in the task until those harnesses discover project skills natively.

## Safety rules

- Apply needs `--approve` (the page sends it only from the Approve button on a shown diff).
- New files only: if any target path exists, the proposal is a conflict and apply refuses. Nothing is overwritten.
- Apply refuses when the board changed after the proposal, a staged file was edited, or a target path appeared
  meanwhile. Each new skill folder is moved in with one rename, so a refused or failed apply leaves nothing behind.
- The `/deploy/*` routes are owner-only, and every request to the server must name this loopback listener in `Host`
  and, when a browser sends `Origin`, come from this same listener. That blocks other websites and DNS rebinding from
  driving the local server.
