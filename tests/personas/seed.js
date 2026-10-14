/* Persona harness seed: the "starter library" boards written into a TEMP copy only. Usage: node seed.js <boards-dir>
   Boards: starter-hub (the deployed board: approval gate, container, immutable hook, notes, drill cards, one empty card),
   starter-intake and starter-publish (inner boards). All card ids/labels are generic business words, no personal data. */
const fs = require('fs'), path = require('path');
const base = (id, type, label, ref, ports, x, y, extra) => Object.assign({ id, type, label, immutable: false, enabled: true, execution_mode: 'observe', ref, ports, pos: { x, y } }, extra || {});
const comp = (id, label, x, y, extra) => base(id, 'component', label, { kind: 'service' }, { in: ['uses'], out: ['provides'] }, x, y, Object.assign({ fields: { verified: false } }, extra || {}));
const mk = (name, cards, edges) => ({ schema: 'workflow-board/v1', board: name, version: 1, execution_mode: 'observe', saved_at: '2026-10-05T00:00:00.000Z', cards, edges: edges || [], integrity_hash: null });
function boards() {
  const hub = mk('starter-hub', [
    base('hub-trigger', 'trigger', 'New customer request', { event: 'prompt.submit', matcher: '^request:' }, { in: [], out: ['task'] }, 0, 0, { group: 'Intake' }),
    base('hub-draft', 'skill', 'Draft the reply', { name: 'graph-loop', path: '~/.claude/skills/graph-loop/SKILL.md' }, { in: ['task'], out: ['draft'] }, 280, 0, { group: 'Intake' }),
    base('hub-compile', 'skill', 'Update the knowledge base', { name: 'demo-compile', path: '~/.claude/skills/demo-compile/SKILL.md' }, { in: ['draft'], out: ['done'] }, 560, 0, { group: 'Publish' }),
    base('hub-agent', 'agent', 'Research helper', { route: 'research', seat: 'claude', verb: 'inject', model: 'sonnet', subagent_type: 'general-purpose' }, { in: ['in'], out: ['out'] }, 280, 200, { group: 'Publish' }),
    base('hub-approval', 'gate', 'Owner approves before sending', { kind: 'approval', rule: 'owner signs off', approver: 'owner', verb: 'deny', draft_path: 'drafts/reply.md' }, { in: ['draft'], out: ['approved'] }, 560, 200, { group: 'Publish' }),
    base('hub-guard', 'hook', 'Safety check (reference)', { event: 'PreToolUse', matcher: 'Write', file: 'hooks/safety-gate.sh' }, { in: ['draft'], out: ['draft'] }, 840, 0, { immutable: true, group: 'Publish' }),
    base('hub-gmail', 'container', 'Email account', { kind: 'connector', connector: 'gmail', account: 'Business inbox' }, { in: [], out: [] }, 0, 200, { fields: { inventory_key: 'gmail' } }),
    base('hub-note', 'note', 'Note', { kind: 'note' }, { in: [], out: [] }, 0, 400, { fields: { text: 'Remember: owner signs off on every customer email.' } }),
    comp('hub-host', 'Intake process', 280, 400, { drill: 'starter-intake', group: 'Intake' }),
    comp('hub-host2', 'Publishing process', 560, 400, { drill: 'starter-publish', group: 'Publish' }),
    comp('hub-empty', 'Month-end close (not mapped yet)', 840, 400),
  ], [{ from: 'hub-trigger.task', to: 'hub-draft.task' }, { from: 'hub-draft.draft', to: 'hub-compile.draft' }, { from: 'hub-draft.draft', to: 'hub-approval.draft' }]);
  const intake = mk('starter-intake', [comp('in-a', 'Receive request', 0, 0), comp('in-b', 'Check the details', 280, 0), comp('in-c', 'Assign an owner', 560, 0)], [{ from: 'in-a.provides', to: 'in-b.uses' }, { from: 'in-b.provides', to: 'in-c.uses' }]);
  const pub = mk('starter-publish', [comp('pb-a', 'Write the post', 0, 0), comp('pb-b', 'Owner review', 280, 0), comp('pb-c', 'Publish', 560, 0)], [{ from: 'pb-a.provides', to: 'pb-b.uses' }, { from: 'pb-b.provides', to: 'pb-c.uses' }]);
  return { 'starter-hub': hub, 'starter-intake': intake, 'starter-publish': pub };
}
function write(dir) { const b = boards(); for (const n of Object.keys(b)) fs.writeFileSync(path.join(dir, n + '.json'), JSON.stringify(b[n], null, 2)); return Object.keys(b); }
module.exports = { boards, write };
if (require.main === module) console.log(write(process.argv[2] || '.').join(','));
