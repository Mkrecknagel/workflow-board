# workflow-board

Board editor and store for agent workflows. Boards are JSON files that describe a workflow as cards (steps, skills, hooks, tools, agents, boundaries) joined by edges. The page edits them; the server validates every save; deploying a board to `active.json` is a human-approved step that this page never performs.

## What the board does

- **Library.** The left panel lists everything installed (skills, hooks, tools, MCP servers, scheduled tasks, plugin skills) from the Claude, Codex, Kiro and `~/.agents` install roots (`library.py`, `scanners.py`), plus reusable modules. Every scanner returns the same neutral item record (`schema/item.schema.json`, with a `harness` field), so adding a harness means adding a scanner. The Kiro scanner reads `skills/`, nested `steering/`, `settings/mcp.json` and each `agents/*.json`, including the hooks and MCP servers declared inside an agent and the `skill://` / `file://` resources it references. Scanners are read-only and record names only: MCP env/args and hook command lines are never copied. Drag an item onto the canvas to add a card, or click a palette type. Descriptions wrap in a wide panel; the panel and inspector widths are draggable and persisted.
- **Resource cards (fallback).** Every library item can go on the board. An item with no dedicated card type (a plugin, a Codex hook or agent, a scheduled task, a kind added later) becomes a `resource` card: `ref {kind, name}` plus optional `harness` and `path`. Resource cards are structural and observe-only: never enforced, never dispatched, never greyed for missing telemetry. The canvas also draws any card type it does not know with the resource look instead of failing; the validator still rejects unknown types on save.
- **Inspector and fields.** Click a card to edit its label, colour, ports and free-form `fields` (key/type/value; reserved names are rejected). Group (lane) colours are editable. Everything snaps to a grid.
- **Tickets.** Comment on any card from the page. Tickets are stored under `tickets/` (`tickets_store.py`) and worked from any agent with `python tickets.py list|show|claim|reply|status|approve|plan|release|sweep`. Ticket text is treated as untrusted. Governance-touching tickets need the owner's approval quote.
- **Modules.** `modules/*.module.json` are reusable sub-graphs (post-x, resize-caption, save-photo-caption, loop patterns). Insert one at the view; validate with `python validate.py <file> --module`.
- **Arrange.** Auto-arrange lanes and cards, copy/paste with snap, group drag.
- **Semantic zoom and lenses.** Zoomed out, lanes collapse to silhouettes; zoomed in, cards show full detail. Cards with a `drill` open their child board (breadcrumb, `#b=` hash). Lenses highlight single-point-of-failure, cost, trust, entity, external and blast-radius properties and say "no data on this board" when the fields are absent. See `docs/v3-semantic-zoom.md`.
- **Shared steps.** The same SOP step duplicated across lanes can become ONE standard step used in N places. `python dedup.py scan --board B` finds duplicate clusters, `python dedup.py tickets --board B` files one `[DEDUP-PROPOSAL]` task ticket per admitted cluster, the Duplicates lens highlights the members, and "Replace with standard step" links them. Editing a shared step shows an N-places modal (uncheck a lane to fork it) before anything changes; a link badge and drift badge (synced / drifted / proposed / missing) mark every instance; Unlink keeps a plain copy. Before any replace, edit or unlink the page saves unsaved layout first and aborts the operation (with the reason) if that save is rejected. SOP text changes are only written as proposals (`shared_steps.py`, `proposals/`), never applied to the SOP files.
- **Adapters.** `adapters/codex.py --board B` compiles a board to a Codex proposal (feasibility table, hooks fragment, AGENTS section) under `adapters/codex/out/`. It never writes to `~/.Codex`.
- **v4: containers and run history.** Connector containers (gmail, drive, calendar, discord) show their tools, hooks and skills from a read-only inventory; blocked (send-capable or unknown) tools are never wireable. Cards show recent runs, boards keep restorable history with a dirty badge against the active hash, lanes collapse, and a tidy button lays cards out by edge order. See `docs/v4.md`.

## Run the server

    python serve.py

The server listens on `http://127.0.0.1:8765` (loopback only). Every request must carry a loopback `Host` (and, for browser writes, a loopback `Origin`); anything else gets 403 (this stops DNS-rebinding and cross-site writes). To reach it through a reverse proxy or a tailnet name, list that host name in `WB_ALLOWED_HOSTS` (comma-separated, port ignored), for example `WB_ALLOWED_HOSTS=wb.example.ts.net`. `BOARD_PORT` overrides the port; `--roots claude=DIR,codex=DIR,kiro=DIR,agents=DIR` replaces the library install roots. `PUT /board/<name>` validates and writes atomically (invalid returns 422 and the file is untouched; immutable cards and their edges cannot be removed). `active.json` is never served or written here.

### Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `BOARD_PORT` | server port | `8765` |
| `WB_ALLOWED_HOSTS` | extra `Host` names the server accepts besides loopback (comma-separated, port ignored), for a reverse proxy or tailnet name, e.g. `wb.example.ts.net` | none |
| `WORKFLOW_BOARD_ROOT` | root folder scanned for instance boards (see below); `BOARD_REGISTRY_ROOT` is an alias used by tests | `registry-root/` next to `registry.py` |
| `PW` | path to a `playwright-core` install, used by the browser tests and readability scripts | `playwright-core` (resolved by node) |
| `WB_CLAUDE_DIR` | install folder scanned by the connector inventory | `~/.claude` |
| `WB_KIRO_DIR` | Kiro install root scanned by the library and used for ref validation | `~/.kiro` |
| `WB_AGENTS_DIR` | generic `.agents` root (`skills/*/SKILL.md`, `AGENTS.md`) | `~/.agents` |
| `WB_CODEX_DIR` | Codex install root | `~/.Codex` |
| `WB_IDENTITY_CONFIG` | optional JSON file with an `identity` block shown in the inventory | none |

A sample board lives at `boards/demo-board.json`.

## Import your workflows (`wb_init.py`)

Your agent files (SOPs, skills, hooks, agent configs) are the system of record. `wb_init.py` reads them and writes boards, which are a derived view:

    python wb_init.py --from <folder> --out <boards folder> [importer options]
    WB_BOARD_DIRS=<boards folder> python serve.py      # the picker now lists them

- **Importers** (`importers/`) turn one folder layout into boards. Today there is one, `tpm-portfolio` (`portfolio.json` + `projects/*/project.json`, optional `--sops DIR` of `*.sop.md`). It builds an overview, one board per project (pipeline, sources, children, the skills whose `SKILL.md` says `sop: SOP-NN`), process boards from SOP headings (nested, so zooming into a step opens its sub-steps), and a runtime board with the agent's hooks and MCP servers. The interface for adding importers is in `importers/__init__.py`.
- **Provenance.** Every generated card records `src_path`, `src_anchor` (the heading or key) and `src_sha` (a hash of the file when the board was made), so drift between a board and its files can be detected. Hover a card to see its source.
- **Station contracts.** `inputs` / `outputs` are filled only when the source declares them (an Inputs / Parameters / Outputs section, or `inputs:` / `outputs:` front matter). Otherwise the card shows **contract missing**. Nothing is guessed from headings.
- **No-clobber.** Re-running keeps your layout and sticky notes. A board you edited since it was generated, or a board of the same name that `wb_init` did not make, is left alone and listed in `<out>/.wb-init-conflicts.json` (exit code 3). `--force` overwrites after a backup in `<out>/.wb-init-backup/`.
- **Project board layout (same on every project):** a status banner, then Inputs (sources) | TPM agent workflow (open by default; the lane toggle collapses it) and Workflow skills | Outputs (knowledge base, reports, artifacts, skills the project built), then Children. An empty lane says "none yet". One card per pipeline station, typed by what runs it: a skill whose `SKILL.md` declares `sop: SOP-NN`, a hook named in the SOP, or a grey step. A step another step's SOP hands off to and calls becomes a gate at the end of that step's board. Stations flag **drift** (broken SOP pointer, or skill and SOP declare different contracts), **duplicate skills** and **split this** (more than 7 SOP steps). Skills used in a project's activity log but not registered show as dashed **suggested** cards.
- **Grouped tool cards.** Inputs and outputs are one card per tool (Slack, Outlook, SharePoint, Wiki, Taskei, Quip, Code, Local files, Web, Other; reports get one card too), each listing its entries as rows: the first 5, then "show all". The tool comes from the source category, or from the URL for artifacts. A row opens its own board when it has one (a local SOP or skill), else its link. Built skills, the knowledge base and children stay single cards.
- **Changing a role** (input source, output artifact, built skill, workflow skill): drag the card into another lane or pick a Role in the inspector. The page proposes the `project.json` change, shows the diff and writes only on approval (`projedit.py`, backup kept); with `WB_REFRESH_CMD` set the boards regenerate and reload.
- **Read-only views:** `/kb/<alias>/<page>` serves a project's KB page and `/report?p=<path>` a generated report, as plain text, confined to the registry root.
- The run overlay ("last run", grey cards) only appears when a run source exists; on a setup with no telemetry nothing is greyed.

## Validate

    python validate.py boards/demo-board.json        # exit 0 ok, 2 invalid (reason on stderr)
    python validate.py modules/post-x.module.json --module

## Tests

One command runs everything (about 2 minutes; needs Chrome and the playwright-core install for the browser suites):

    python tests/run_all.py            # add --no-browser to skip Chrome suites

It runs every pytest file under `tests/` (with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`), `tests/test_server_v2.py`, `tests/e2e_tickets.py`, validation of all registered boards and modules, and every `tests/browser/*.js` against a temp copy of this folder on a free port, then checks every real registered board file is byte-identical. It never touches port 8765. `tests/browser/v21.js` is standalone: `node tests/browser/v21.js` builds its own server and fixtures. The executor mod has its own check: `claude plugin test <path to workflow-executor>`.

## Boards: engine boards vs instance boards

This folder is the engine. A board built for a specific workflow is an instance and lives in the project it serves, in that project's workspace under `workflows/` (for example `projects/my-project/workflows/my-board.json`). The server finds boards through `registry.py`:

- Engine boards: every `boards/*.json` here (except `active.json`).
- Instance boards: every `project.json` under `<root>/projects/` (depth up to 3) that lists the board in `sources.artifacts[]`:

      {"type": "workflow-board", "board": "<name>", "url": "<absolute path to the board json>"}

The same URL works for both kinds (`/board/<name>`, `?board=<name>`). GET and PUT read and write the board at its registered path. History snapshots (`boards/.history/<name>.*`), `boards/active.json` (the deployed-board pointer, may name an instance board) and `runs/` stay in the engine, keyed by board name.

To register a new instance board: put the file in the project's `workflows/` folder, add the artifact entry above to that project's `project.json`, and reload; it appears in `/boards`. A new board name that is not registered is created in `boards/` as before. `/boards` also returns an `errors` list (duplicate name, registered file missing, unreadable `project.json`) and never fails because of one.

The registry root is `$WORKFLOW_BOARD_ROOT` (or `$BOARD_REGISTRY_ROOT`), else `registry-root/` next to `registry.py`. Tests point it at an empty temp folder and copy instance boards into temp servers, so they never touch real files. `python registry.py list` and `python registry.py resolve <name>` show what the registry sees.

## Files

- `serve.py`, `serve_shared.py` - server and `/api/shared/*` routes. `validate.py`, `schema/` - validator and schemas.
- `library.py`, `modules/` - install scan and reusable sub-graphs. `canvas/` - the page (`js/`: core, library, inspector, panels, arrange, select, zoom, lenses, tickets, shared, plus the v4 kinds, containers, runs, history, dirty, collapse, tidy, toolbar).
- `tickets.py`, `tickets_store.py`, `tickets/` - ticket CLI and store. `dedup.py`, `shared_steps.py`, `dedup/`, `proposals/` - duplicate detection and shared steps.
- `serve_v4*.py`, `inventory/` - v4 routes and the connector inventory. `adapters/` - Codex adapter. `executor/` - runtime hooks source. `boards/` - the engine's boards (a sample `demo-board`), `active.json` and `.history/`. `registry.py` - finds every board by name (see Boards below). `contracts/` - agent briefing templates. `fixtures/` - invalid boards that must fail validation.

## Layout, line routing and the readability check (v6)
- **Auto layout** (`canvas/js/autolayout.js`, toolbar "Auto layout", Alt+G): ELK (vendored `canvas/vendor/elk.bundled.js`, EPL-2.0, unmodified, see `canvas/vendor/NOTICE.md`) places the cards and sizes boundary boxes; nothing else is written. Loop returns are found by a DFS from the entry cards (`router.js findLoops`).
- **Line router** (`canvas/js/router.js`, used by `core.js`): recomputed from positions on every render, never saved. Clean beziers stay; a DFS back-edge is drawn as a loop (right stub, down to a return lane below every card and lane it spans, left, up, in); any other line that would touch a card, lane header band or boundary label strip is routed orthogonally around them.
- **Readability check** (`canvas/js/readability.js`, one pure module): 0 box overlaps and 0 lines through boxes, judged on what the canvas draws. Live badge bottom-left; headless: `python scripts/check_layout.py --all` (screenshots in `runs/readability/<board>/`), `--fix` re-arranges through `PATCH /board/<name>/visual` (layout keys only, the semantic hash cannot move). The server re-checks a board 2 s after each save (`GET /board/<name>/readability`; a failing board is saved and flagged, never rejected). `tests/browser/layout.js` + `tests/test_readability_save.py` are in `tests/run_all.py`.
