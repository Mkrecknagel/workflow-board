# Node briefing header (shared)

NODE {node_id} - {title}
MODEL/TIER: {model} - route {route} per council-routing.yaml
INPUTS:     {inputs}
TASK:       {task}
DO NOT:     write to logs/*; send, post or publish; write governance files; spawn agents.
MAY DELEGATE: false
DURABLE OUTPUT: {output_path}
RETURN PACKET: <=400 words, VERDICT/CRITERIA/VERIFY/ARTIFACTS/IMPACT/LEDGER_LINE/REFUTED/UNVERIFIED/OPEN
ACCEPTANCE CRITERIA: {criteria}
BUDGET:     token_ceiling {token_ceiling} - max {max_tool_calls} tool calls - 0 image reads - timeout {timeout_s}s
SCAN:       IMPACT: required (NONE acceptable).
