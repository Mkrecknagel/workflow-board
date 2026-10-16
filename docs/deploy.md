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

## Safety rules

- Apply needs `--approve` (the page sends it only from the Approve button on a shown diff).
- New files only: if any target path exists, the proposal is a conflict and apply refuses. Nothing is overwritten.
- Apply refuses when the board changed after the proposal, a staged file was edited, or a target path appeared
  meanwhile. Each new skill folder is moved in with one rename, so a refused or failed apply leaves nothing behind.
- The `/deploy/*` routes are owner-only, and every request to the server must name this loopback listener in `Host`
  and, when a browser sends `Origin`, come from this same listener. That blocks other websites and DNS rebinding from
  driving the local server.
