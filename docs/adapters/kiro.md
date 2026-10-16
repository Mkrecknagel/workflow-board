# Kiro adapter

## Discovery (implemented, `scanners.py scan_kiro`)

Verified against a Kiro CLI install (2026-10-14). The scanner reads, read-only:

| Kiro location | Board item |
|---|---|
| `skills/*/SKILL.md` | skill (`subkind: skill`) |
| `steering/**/*.md` (recursive) | skill (`subkind: steering`) |
| `settings/mcp.json` `mcpServers` | mcp (names only) |
| `agents/*.json` | agent |
| `agents/*.json` `hooks.<event>[]` (agentSpawn, userPromptSubmit, preToolUse, postToolUse, stop) | hook, `ref.file` = the script the command runs, else the agent file |
| `agents/*.json` `mcpServers` | mcp, with `used_by` listing the agents |
| `agents/*.json` `resources` (`skill://` and absolute `file://`, globs capped) | skill / steering, with `used_by` |
| `hooks/*.json` (IDE hook files) | hook |

Relative `file://` resources resolve against the workspace an agent runs in, which a global scan cannot know, so they are skipped.

## Execution (not implemented)

Mapping of board card types to Kiro concepts. Execution stays observe-only: `validate.py --target kiro` still refuses any enabled enforce card until a conformance test shows Kiro hook semantics (payload, exit-code denial) match the board's gate and inject verbs.

| Card type | Kiro mapping | Status |
|---|---|---|
| trigger | agent hook event | discovered, not executed |
| input | steering / resource context | discovered |
| skill | skill or steering file | discovered |
| hook | agent `hooks` entry | discovered, not executed |
| gate | `preToolUse` hook | UNVERIFIED, treated as unsupported |
| agent | `agents/*.json` | discovered; seat/route injection unsupported |
| output | task output artifact | UNVERIFIED |
| tool | MCP server in agent or `settings/mcp.json` | discovered |
| step | spec task list item | UNVERIFIED |
