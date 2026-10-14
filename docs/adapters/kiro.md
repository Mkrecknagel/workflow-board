# Kiro adapter spec (spec only, no code)

No Kiro install exists on this host; `.kiro/` is not observable. Every Kiro-side claim
below is UNVERIFIED until an install exists. `validate.py --target kiro` refuses any
enabled enforce card.

Mapping of the 9 board card types to Kiro concepts:

| Card type | Kiro mapping | Status |
|---|---|---|
| trigger | hook event (`.kiro/hooks/*.json` trigger) | UNVERIFIED: event names and file schema |
| input | steering-file context / spec input | UNVERIFIED |
| skill | steering file (`.kiro/steering/*.md`) | UNVERIFIED: no skill-dir equivalent confirmed |
| hook | `.kiro/hooks/*.json` entry | UNVERIFIED: tool-call interception availability |
| gate | none; cannot be enforced | UNVERIFIED, treated as unsupported |
| agent | none confirmed for seat/route injection | UNVERIFIED, unsupported |
| output | spec/task output artifact | UNVERIFIED |
| tool | MCP server reference in Kiro MCP config | UNVERIFIED |
| step | spec task list item | UNVERIFIED |

All cards would be observe-only (documentation). Writing to `.kiro/` is out of scope until
a Kiro install exists and a owner deploy step is approved.
