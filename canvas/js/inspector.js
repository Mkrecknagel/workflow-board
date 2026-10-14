"use strict";
/* workflow-board inspector + split view (module, v2 item 5).
   Self-contained: talks to window.wb (board, sel, view, insertSubgraph) and GET /library, /inspect.
   Public: window.wbInspect(path) and the DOM event "wb:inspect" (detail = path or {path}).
   Shows frontmatter, heading outline, tools MENTIONED (string scan), raw body, and - only if a
   splits/<name>.proposal.json exists - the proposed chain, dashed and badged "not executable".
   No step is ever synthesized from prose. There is no Apply control: "insert as draft" only pastes
   observe-only step cards with ref.kind "proposed" onto the open board; the validator refuses enforce on them. */
const $ = id => document.getElementById(id);
const E = (t, c, h) => { const e = document.createElement(t); if (c) e.className = c; if (h != null) e.textContent = h; return e; };
const say = (m, cls) => { const e = $("msg"); if (e) { e.textContent = m; e.className = cls || ""; e.title = m; } };

const CSS = `
#wbi{display:none;position:fixed;inset:0;z-index:20;background:#0007;align-items:flex-start;justify-content:center;padding:12px}
#wbi.open{display:flex}
#wbi .box{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:10px;width:min(920px,100%);max-height:100%;overflow:auto;padding:12px 14px;box-shadow:0 8px 32px #0006}
#wbi h3{margin:0;font-size:15px}#wbi h4{margin:14px 0 6px;color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.06em}
#wbi .top{display:flex;gap:8px;align-items:center;flex-wrap:wrap}#wbi .top select{flex:1;min-width:160px;max-width:100%}
#wbi table{border-collapse:collapse;width:100%}#wbi td{border-top:1px solid var(--line);padding:3px 6px;vertical-align:top;word-break:break-word}#wbi td:first-child{color:var(--mut);white-space:nowrap;width:1%}
#wbi ol.outline{margin:0;padding:0;list-style:none;max-height:220px;overflow:auto;font-size:12px;white-space:pre}#wbi ol.outline li{padding:1px 0}#wbi ol.outline small{color:var(--mut);margin-left:6px}
#wbi .chips span{display:inline-block;margin:0 4px 4px 0;padding:1px 7px;border:1px solid var(--line);border-radius:10px;font-size:11px}
#wbi pre{margin:0;max-height:260px;overflow:auto;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:8px;font:11px/1.4 ui-monospace,Consolas,monospace;white-space:pre-wrap;word-break:break-word}
#wbi .badge{display:inline-block;padding:1px 8px;border-radius:10px;background:#d9822b;color:#fff;font-size:11px;font-weight:600}
#wbi .chain{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start}
#wbi .step{border:2px dashed var(--edge);border-radius:8px;padding:6px 8px;min-width:150px;max-width:260px;background:var(--card)}
#wbi .step b{display:block;font-size:12px}#wbi .step small{display:block;color:var(--mut);font-size:10px}
#wbi .edges{font-size:11px;color:var(--mut);margin-top:6px}
#wbi .empty{color:var(--mut)}
`;
let items = [], modal = null;

function ensureUI() {
  if (modal) return;
  const st = E("style"); st.textContent = CSS; document.head.appendChild(st);
  modal = E("div"); modal.id = "wbi";
  modal.addEventListener("keydown", ev => { ev.stopPropagation(); if (ev.key === "Escape") close(); });
  modal.addEventListener("pointerdown", ev => { if (ev.target === modal) close(); });
  document.body.appendChild(modal);
  const bar = $("bar");
  if (bar && !$("bInspectSkill")) {
    const b = E("button", null, "Inspect skill"); b.id = "bInspectSkill"; b.title = "Inspect an installed skill: frontmatter, outline, proposed split";
    b.onclick = () => open(); const m = $("msg"); bar.insertBefore(b, m || null);
  }
}
const close = () => modal && modal.classList.remove("open");

async function loadLibrary() {
  try { const r = await fetch("/library"); const j = await r.json(); items = (j.items || []).filter(i => i.path && i.kind === "skill"); }
  catch (e) { items = []; say("library scan failed: " + e, "err"); }
}

function selectedSkillName() {
  const wb = window.wb; if (!wb || !wb.board || !wb.sel || wb.sel.size !== 1) return null;
  const c = wb.board.cards.find(x => x.id === [...wb.sel][0]);
  return c && c.type === "skill" && c.ref ? c.ref.name : null;
}

/* v5: run_mode control for the single selected dispatchable card (logic lives in runmode.js; this only hosts the button) */
function runModeControl(top) {
  const R = window.wbRunMode, wb = window.wb;
  if (!R || !wb || !wb.board || !wb.sel || wb.sel.size !== 1) return;
  const c = wb.board.cards.find(x => x.id === [...wb.sel][0]);
  if (!c || !R.isEligible(c)) return;
  const b = E("button", null); b.id = "wbiRunMode";
  const paint = () => { b.textContent = "Run mode: " + R.modeOf(c) + (R.blockerFor(c) ? " (needs change)" : ""); b.title = R.blockerFor(c) || "Cycle inline / background / wait (guidance only; behaviour change, needs Save + deploy)"; };
  b.onclick = () => { R.cycle(c.id); paint(); };
  paint(); top.appendChild(b);
}

async function open(pathOrNull) {
  ensureUI(); modal.innerHTML = ""; modal.classList.add("open");
  const box = E("div", "box"); modal.appendChild(box);
  const top = E("div", "top"); box.appendChild(top);
  top.appendChild(E("h3", null, "Inspect skill"));
  const sel = E("select"); top.appendChild(sel);
  runModeControl(top);
  const x = E("button", null, "Close"); x.onclick = close; top.appendChild(x);
  const body = E("div"); body.id = "wbiBody"; box.appendChild(body);
  body.appendChild(E("div", "empty", "Scanning library..."));
  await loadLibrary();
  sel.innerHTML = "";
  const ph = E("option", null, items.length ? "choose a skill (" + items.length + ")" : "no skills found"); ph.value = ""; sel.appendChild(ph);
  items.slice().sort((a, b) => (b.has_proposal ? 1 : 0) - (a.has_proposal ? 1 : 0) || a.name.localeCompare(b.name)).forEach(i => {
    const o = E("option", null, i.name + (i.has_proposal ? "  [proposal]" : "") + "  (" + i.source_agent + ")"); o.value = i.path; sel.appendChild(o);
  });
  sel.onchange = () => sel.value && show(sel.value, body);
  let want = typeof pathOrNull === "string" ? pathOrNull : null;
  if (!want) { const n = selectedSkillName(); const m = n && items.find(i => i.name === n); if (m) want = m.path; }
  if (want) {
    if (![...sel.options].some(o => o.value === want)) { const o = E("option", null, want); o.value = want; sel.appendChild(o); }
    sel.value = want; show(want, body);
  } else {
    body.innerHTML = "";
    body.appendChild(E("div", "empty", "Pick a skill above. Nothing is inferred from prose; a step chain appears only if a splits/*.proposal.json exists."));
  }
}

async function show(path, body) {
  body.innerHTML = ""; body.appendChild(E("div", "empty", "Loading..."));
  let d;
  try {
    const r = await fetch("/inspect?path=" + encodeURIComponent(path));
    if (!r.ok) { body.textContent = "inspect refused (" + r.status + ")"; return; }
    d = await r.json();
  } catch (e) { body.textContent = "inspect failed: " + e; return; }
  body.innerHTML = "";
  body.appendChild(E("h4", null, "Frontmatter"));
  const keys = Object.keys(d.frontmatter || {});
  if (!keys.length) body.appendChild(E("div", "empty", "none"));
  else {
    const t = E("table");
    keys.forEach(k => { const tr = E("tr"); tr.appendChild(E("td", null, k)); const v = d.frontmatter[k]; tr.appendChild(E("td", null, typeof v === "string" ? v : JSON.stringify(v))); t.appendChild(tr); });
    body.appendChild(t);
  }
  body.appendChild(E("h4", null, "Outline (" + (d.outline || []).length + " headings)"));
  const ol = E("ol", "outline");
  (d.outline || []).forEach(h => { const li = E("li", null, " ".repeat((h.level - 1) * 3) + h.title); li.appendChild(E("small", null, "L" + h.line)); ol.appendChild(li); });
  body.appendChild(ol);
  body.appendChild(E("h4", null, "Tools mentioned (string scan, not verified use)"));
  const ch = E("div", "chips");
  (d.tools_mentioned || []).forEach(t => ch.appendChild(E("span", null, t)));
  if (!(d.tools_mentioned || []).length) ch.appendChild(E("span", "empty", "none"));
  body.appendChild(ch);
  body.appendChild(E("h4", null, "Proposed split"));
  if (d.proposal) body.appendChild(proposalView(d.proposal)); else body.appendChild(E("div", "empty", "No proposal exists for this skill."));
  body.appendChild(E("h4", null, "Raw markdown"));
  const det = E("details"); det.appendChild(E("summary", null, "show raw (" + (d.raw || "").length + " chars)"));
  const pre = E("pre"); pre.textContent = d.raw || ""; det.appendChild(pre); body.appendChild(det);
}

const epCard = s => s.slice(0, s.lastIndexOf("."));

/* column = longest path from a root; row = order within the column */
function layout(p) {
  const depth = {}; p.steps.forEach(s => { depth[s.id] = 0; });
  for (let i = 0; i < p.steps.length; i++) p.edges.forEach(e => { const f = epCard(e.from), t = epCard(e.to); if (depth[t] < depth[f] + 1) depth[t] = depth[f] + 1; });
  const rows = {}, pos = {};
  p.steps.forEach(s => { const d = depth[s.id]; rows[d] = rows[d] || 0; pos[s.id] = { x: d * 250, y: rows[d] * 130 }; rows[d]++; });
  return pos;
}

export function proposalToCards(p) {
  const pos = layout(p);
  return p.steps.map(s => ({
    id: s.id, type: "step", label: s.label, immutable: false, enabled: true, execution_mode: "observe",
    ref: { kind: "proposed" }, ports: { in: s.ports.in.slice(), out: s.ports.out.slice() }, pos: pos[s.id],
    proposal: p.target_skill + "#" + s.id + " (" + p.status + ", inferred)"
  }));
}

function proposalView(p) {
  const w = E("div");
  const head = E("div"); head.appendChild(E("span", "badge", "proposed · inferred · not executable"));
  head.appendChild(E("span", null, "  status: " + p.status + " (owner-owned), by " + p.proposed_by)); w.appendChild(head);
  w.appendChild(E("p", null, p.rationale));
  const chain = E("div", "chain");
  p.steps.forEach(s => {
    const c = E("div", "step"); c.appendChild(E("b", null, s.label));
    c.appendChild(E("small", null, s.kind + " · in: " + s.ports.in.join(",") + " · out: " + s.ports.out.join(",")));
    c.appendChild(E("small", null, s.scope)); chain.appendChild(c);
  });
  w.appendChild(chain);
  w.appendChild(E("div", "edges", p.edges.map(e => e.from + " → " + e.to).join("   ")));
  w.appendChild(E("div", "edges", "Realization: " + p.realization_note));
  const b = E("button", "pri", "insert as draft"); b.id = "wbiInsert"; b.style.marginTop = "8px";
  b.title = "Adds observe-only step cards (ref.kind proposed) to the open board. Nothing is applied to any skill.";
  b.onclick = () => insertDraft(p); w.appendChild(b);
  return w;
}

function insertDraft(p) {
  const wb = window.wb;
  if (!wb || !wb.board) { say("open a board first", "err"); return; }
  const cards = proposalToCards(p), v = wb.view;
  const ox = Math.round((-v.x) / v.k / 8) * 8 + 40, oy = Math.round((-v.y) / v.k / 8) * 8 + 40;
  cards.forEach(c => { c.pos = { x: c.pos.x + ox, y: c.pos.y + oy }; });
  wb.insertSubgraph(cards, p.edges);
  close(); say("inserted " + cards.length + " proposed step cards (draft, observe-only). Save to persist.", "ok");
}

window.wbInspect = open;
window.addEventListener("wb:inspect", ev => { const d = ev.detail; open(typeof d === "string" ? d : d && d.path); });
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ensureUI); else ensureUI();
