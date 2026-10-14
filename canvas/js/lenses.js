"use strict";
/* workflow-board v3 lenses + teaching badges (module, B4). Loaded by panels.js (dynamic import). Reads window.wb only; no core hooks.
   Every mark is computed from typed fields (fields.spof/redundant_with/cost/entity/secrets/authority/owner/verified, ref.kind, boundary, edges) -
   a lens whose fields are absent says "no data on this board" instead of drawing nothing.
   Lens classes go on .card (and a data-lens-mark attribute on the Level-1 silhouette of the same card):
     spof -> .lens-spof     cost -> .lens-metered / .lens-zero / .lens-fixed     trust -> .lens-secret + edges .lens-trust
     entity -> .lens-entity (+ edges .lens-cross-entity)   external -> .lens-external   blast -> .lens-blast
   Public: window.wbLenses {lens, set(name|null), compute(), LENSES}. */
const $ = id => document.getElementById(id);
const E = (t, c, h) => { const e = document.createElement(t); if (c) e.className = c; if (h != null) e.textContent = h; return e };
const W = () => window.wb;
const LENSES = [["spof", "SPOF"], ["trust", "Trust"], ["cost", "Cost"], ["entity", "Entity"], ["external", "External"], ["blast", "Blast"]];
const PAL = ["#0072B2", "#CC79A7", "#009E73", "#E69F00", "#56B4E9", "#D55E00"]; /* Okabe-Ito; a text badge always accompanies colour */
const truthy = v => v === true || v === "true";
const store = { get(k) { try { return localStorage.getItem(k) } catch (_) { return null } }, set(k, v) { try { localStorage.setItem(k, v) } catch (_) { } } };
let active = null, qd = 0, selKey = "";

const CSS = `
#zm-lenses{position:absolute;right:8px;top:8px;z-index:5;display:flex;flex-direction:column;align-items:flex-end;gap:4px;max-width:calc(100% - 16px)}
#zm-lenses .bar{display:flex;flex-wrap:wrap;gap:3px;justify-content:flex-end;padding:3px;border:1px solid var(--line);border-radius:8px;background:var(--panel);box-shadow:0 1px 6px #0003}
#zm-lenses button{padding:2px 8px;font-size:12px}#zm-lenses button[aria-pressed=true]{background:var(--sel);color:#fff;border-color:var(--sel)}
#zm-lensnote{font-size:12px;padding:2px 8px;border-radius:6px;background:var(--panel);border:1px solid var(--line);color:var(--mut);display:none}#zm-lensnote.on{display:block}
.zm-badges{position:absolute;left:4px;top:-11px;z-index:3;display:flex;gap:3px;pointer-events:none;flex-wrap:nowrap}
.zm-badges span{font-size:10px;line-height:1;padding:2px 5px;border-radius:9px;border:1px solid var(--line);background:var(--panel);color:var(--ink);white-space:nowrap;font-weight:600}
.zm-badges .spof{border-color:#D55E00;color:#D55E00}.zm-badges .unv{border-style:dashed;border-color:#E69F00;color:#E69F00}.zm-badges .lens{background:var(--sel);color:#fff;border-color:var(--sel)}
.card[data-type=datastore]{border-radius:8px 8px 22px 22px}.card[data-type=external]{border-style:dashed}
.card.lens-dim{opacity:.35}
.card.lens-spof,[data-lens-mark=spof]{outline:3px solid #D55E00;outline-offset:2px}
.card.lens-metered,[data-lens-mark=metered]{outline:3px solid #E69F00;outline-offset:2px}.card.lens-zero,[data-lens-mark=zero]{outline:3px solid #009E73;outline-offset:2px}.card.lens-fixed,[data-lens-mark=fixed]{outline:3px solid #0072B2;outline-offset:2px}
.card.lens-secret,[data-lens-mark=secret]{outline:3px solid #E69F00;outline-offset:2px}
.card.lens-entity,[data-lens-mark=entity]{outline:3px solid var(--lc,#0072B2);outline-offset:2px}
.card.lens-external,[data-lens-mark=external]{outline:3px solid #56B4E9;outline-offset:2px}
.card.lens-blast,[data-lens-mark=blast]{outline:3px solid #D55E00;outline-offset:2px}
#edges path.e.lens-trust,#edges path.e.lens-cross-entity{stroke:#E69F00;stroke-width:3.5}#edges path.e.lens-cross-entity{stroke-dasharray:8 4}
`;

/* ---------- model ---------- */
function model() {
  const w = W(), b = w && w.board; if (!b) return null;
  const by = {}; b.cards.forEach(c => by[c.id] = c);
  const f = c => c.fields || {};
  const chain = c => { const o = []; let x = c, g = 0; while (x && x.boundary && by[x.boundary] && g++ < 8) { x = by[x.boundary]; o.push(x) } return o };
  const kindOf = c => c.ref && typeof c.ref === "object" ? c.ref.kind : null;
  const epCard = s => s.slice(0, s.lastIndexOf("."));
  return { b, by, f, chain, kindOf, epCard };
}
/* AMEND-front-door rule 3: spof when fields.spof === "true", OR a boundary holding >=3 component/datastore children none of which has fields.redundant_with */
function computeSpof(m) {
  const s = new Set();
  m.b.cards.forEach(c => {
    if (truthy(m.f(c).spof)) s.add(c.id);
    if (c.type === "boundary") {
      const kids = m.b.cards.filter(d => d.boundary === c.id && (d.type === "component" || d.type === "datastore"));
      if (kids.length >= 3 && !kids.some(d => m.f(d).redundant_with)) s.add(c.id);
    }
  });
  return s;
}
function zoneOf(m, c) { /* nearest enclosing trust-bearing boundary (machine / vm / network / trust / cloud) */
  const own = c.type === "boundary" ? [c, ...m.chain(c)] : m.chain(c);
  const z = own.find(x => ["machine", "vm", "network", "trust", "cloud"].includes(m.kindOf(x)) || m.f(x).trust);
  return z ? z.id : null;
}
function entityOf(m, c) {
  if (m.f(c).entity) return String(m.f(c).entity);
  const e = m.chain(c).find(x => m.kindOf(x) === "entity"); return e ? e.id : null;
}
function machineOf(m, c) { const x = [...(c.type === "boundary" ? [c] : []), ...m.chain(c)].find(y => m.kindOf(y) === "machine"); return x ? x.id : null }
function blastSet(m, id) {
  const src = m.by[id]; if (!src) return new Set(); const mach = machineOf(m, src), seen = new Set([id]), q = [id];
  while (q.length) { const x = q.shift(); m.b.edges.forEach(e => { if (m.epCard(e.from) === x) { const t = m.epCard(e.to), tc = m.by[t]; if (tc && !seen.has(t) && machineOf(m, tc) === mach) { seen.add(t); q.push(t) } } }) }
  seen.delete(id); return seen;
}
/* compute(): pure function of the board, returns the marks for the active lens (also used by tests) */
function compute(lens) {
  const m = model(); if (!m) return null;
  const r = { lens, cards: {}, edges: {}, dim: [], note: "", nodata: false, spof: [...computeSpof(m)] };
  const mark = (id, cls, extra) => { r.cards[id] = Object.assign({ cls }, extra || {}) };
  if (lens === "spof") {
    const hasData = m.b.cards.some(c => c.type === "boundary" || m.f(c).spof != null);
    if (!hasData) r.nodata = true; else { r.spof.forEach(id => mark(id, "spof", { text: "SPOF" })); r.note = "SPOF: " + r.spof.length + " flagged" }
  } else if (lens === "cost") {
    const withCost = m.b.cards.filter(c => m.f(c).cost);
    if (!withCost.length) r.nodata = true; else {
      withCost.forEach(c => { const v = String(m.f(c).cost); if (["metered", "zero", "fixed"].includes(v)) mark(c.id, v, { text: v === "zero" ? "$0" : v === "metered" ? "$ metered" : "$ fixed" }) });
      const n = v => withCost.filter(c => String(m.f(c).cost) === v).length; r.note = "Cost: " + n("metered") + " metered, " + n("fixed") + " fixed, " + n("zero") + " zero";
    }
  } else if (lens === "trust") {
    const bnd = m.b.cards.filter(c => c.type === "boundary"); if (!bnd.length) r.nodata = true; else {
      m.b.cards.forEach(c => { if (m.f(c).secrets) mark(c.id, "secret", { text: "key" }) });
      m.b.edges.forEach((e, i) => { const a = m.by[m.epCard(e.from)], z = m.by[m.epCard(e.to)]; if (!a || !z) return; const za = zoneOf(m, a), zz = zoneOf(m, z); if (za && zz && za !== zz) r.edges[i] = "lens-trust" });
      r.note = "Trust: " + Object.keys(r.edges).length + " edges cross a trust boundary, " + Object.keys(r.cards).length + " cards hold secrets";
    }
  } else if (lens === "entity") {
    const ents = {}; m.b.cards.forEach(c => { const e = entityOf(m, c); if (e) { ents[e] = ents[e] || Object.keys(ents).length; mark(c.id, "entity", { text: e, color: PAL[ents[e] % PAL.length] }) } });
    m.b.edges.forEach((e, i) => { const a = m.by[m.epCard(e.from)], z = m.by[m.epCard(e.to)]; if (!a || !z) return; const ea = entityOf(m, a), ez = entityOf(m, z); if (ea && ez && ea !== ez) r.edges[i] = "lens-cross-entity" });
    if (!Object.keys(ents).length) { if (m.b.cards.some(c => m.kindOf(c) === "entity")) r.note = "Entity: boundaries exist but no card is assigned to one"; else r.nodata = true }
    else r.note = "Entity: " + Object.keys(ents).length + " entities, " + Object.keys(r.edges).length + " cross-entity edges";
  } else if (lens === "external") {
    const ext = m.b.cards.filter(c => c.type === "external" || truthy(m.f(c).external)); if (!ext.length) r.nodata = true; else {
      ext.forEach(c => mark(c.id, "external", { text: "ext" })); m.b.cards.forEach(c => { if (!r.cards[c.id]) r.dim.push(c.id) }); r.note = "External: " + ext.length + " outside your control";
    }
  } else if (lens === "blast") {
    const sel = W().sel; if (!sel || sel.size !== 1) r.note = "Blast: select one card"; else {
      const id = [...sel][0], s = blastSet(m, id); s.forEach(x => mark(x, "blast", { text: "blast" })); r.note = "Blast radius: " + s.size + " downstream on the same machine"; r.blast = [...s];
    }
  }
  if (r.nodata) r.note = "no data on this board";
  return r;
}

/* ---------- decoration ---------- */
function badgesFor(m, c, spof) {
  const o = [], f = m.f(c), add = (cls, t, title) => { const s = E("span", cls, t); if (title) s.title = title; o.push(s) };
  if (spof.has(c.id)) add("spof", "SPOF", "single point of failure");
  if (f.verified === false || f.verified === "false") add("unv", "unverified", "this fact is not confirmed yet");
  if (c.type === "datastore" && f.authority) { const a = String(f.authority); add("auth", a === "derived" ? "cache" : a, a === "SoT" ? "source of truth" : a === "snapshot" ? "point-in-time copy" : "derived copy") }
  if (f.secrets) add("key", "key", "holds secrets: " + f.secrets);
  if (c.type === "external" || truthy(f.external)) add("ext", "ext", "outside your control");
  if (f.cost === "metered" || f.cost === "fixed") add("cost", "$", "cost: " + f.cost);
  if (f.owner) add("owner", String(f.owner).length > 1 ? String(f.owner) : String(f.owner).toUpperCase(), "owner");
  return o;
}
function apply() {
  const m = model(); if (!m) return; const spof = computeSpof(m), r = active ? compute(active) : null;
  m.b.cards.forEach(c => {
    const el = document.querySelector('.card[data-id="' + c.id + '"]'); if (!el) return;
    el.dataset.type = el.dataset.type || c.type;
    [...el.classList].filter(x => x.startsWith("lens-")).forEach(x => el.classList.remove(x)); el.style.removeProperty("--lc");
    el.querySelectorAll(":scope>.zm-badges").forEach(x => x.remove());
    const bs = badgesFor(m, c, spof), mk = r && r.cards[c.id];
    if (mk) { el.classList.add("lens-" + mk.cls); if (mk.color) el.style.setProperty("--lc", mk.color); const t = E("span", "lens", mk.text || mk.cls); bs.push(t) }
    else if (r && r.dim.includes(c.id)) el.classList.add("lens-dim");
    if (bs.length) { const box = E("div", "zm-badges"); bs.forEach(x => box.appendChild(x)); el.appendChild(box) }
  });
  document.querySelectorAll("#zm-lod [data-sil]").forEach(s => {
    const id = s.dataset.sil.slice(2), mk = r && r.cards[id]; if (mk) { s.dataset.lensMark = mk.cls; if (mk.color) s.style.setProperty("--lc", mk.color) } else { delete s.dataset.lensMark; s.style.removeProperty("--lc") }
  });
  document.querySelectorAll("#edges path.e").forEach((p, i) => { p.classList.remove("lens-trust", "lens-cross-entity"); if (r && r.edges[i]) p.classList.add(r.edges[i]) });
  const note = $("zm-lensnote"); if (note) { note.textContent = r ? r.note : ""; note.classList.toggle("on", !!(r && r.note)) }
  document.querySelectorAll("#zm-lenses button[data-lens]").forEach(b => b.setAttribute("aria-pressed", b.dataset.lens === active ? "true" : "false"));
}
function queue() { if (qd) return; qd = requestAnimationFrame(() => { qd = 0; apply() }) }
function set(name) { active = name && LENSES.some(l => l[0] === name) ? name : null; store.set("wb.lens", active || ""); queue(); document.dispatchEvent(new CustomEvent("wb:lens", { detail: { lens: active } })) }

function init() {
  const st = E("style"); st.textContent = CSS; document.head.appendChild(st);
  const box = E("div"); box.id = "zm-lenses"; const bar = E("div", "bar"); bar.setAttribute("role", "toolbar"); bar.setAttribute("aria-label", "Lenses");
  LENSES.forEach(([k, t]) => { const b = E("button", null, t); b.dataset.lens = k; b.title = "Lens: " + t; b.setAttribute("aria-pressed", "false"); b.onclick = () => set(active === k ? null : k); b.addEventListener("pointerdown", e => e.stopPropagation()); bar.appendChild(b) });
  const note = E("div"); note.id = "zm-lensnote"; box.appendChild(bar); box.appendChild(note); $("vp").appendChild(box);
  ["cards", "lod"].forEach(id => { const e = $(id); if (e) new MutationObserver(queue).observe(e, { childList: true }) });
  new MutationObserver(queue).observe($("edges"), { childList: true });
  const lodWatch = new MutationObserver(() => { const l = $("zm-lod"); if (l) { new MutationObserver(queue).observe(l, { childList: true }); lodWatch.disconnect() } }); lodWatch.observe($("world"), { childList: true });
  setInterval(() => { const w = W(); const k = w && w.sel ? [...w.sel].join(",") : ""; if (k !== selKey) { selKey = k; if (active === "blast") queue() } }, 150);
  queue();
}
window.wbLenses = { get lens() { return active }, set, compute, apply, LENSES };
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
