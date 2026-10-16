# Workflow board adapters

Core (vendor-neutral): board/module/proposal/item JSON, `validate.py`, `serve.py`, `library.py`, `scanners.py`, the canvas.

Discovery and execution are separate. Every harness gets a read-only scanner that turns its install into neutral item
records (`schema/item.schema.json`); an item a harness cannot execute still appears on the board.

| Agent | Adapter | Status |
|---|---|---|
| Claude | existing dev-mod executor (unchanged) | live |
| Codex | `adapters/codex.py --board <name>` | compile-to-proposal only |
| Kiro | `scanners.py scan_kiro`, `docs/adapters/kiro.md` | discovery live; execution observe-only |
| any `.agents` harness | `scanners.py scan_generic` (`~/.agents/skills`, `AGENTS.md`) | discovery live |

## Codex

`python adapters/codex.py --board demo-board` prints a per-card feasibility table
(`native` = already wired in Codex, `static` = expressible as a proposed hooks.json
entry or AGENTS.md prose, `unsupported` = Codex cannot honor it) and writes proposals
ONLY under `adapters/codex/out/`. It reads `~/.Codex/hooks.json` read-only and never
writes under `~/.Codex`. Realization is a manual owner step.

Invariant: `validate.py --target codex` exits 2 on enabled enforce `inject` cards;
`--target kiro` exits 2 on any enabled enforce card.
