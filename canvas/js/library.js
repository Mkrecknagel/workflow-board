"use strict";
/* workflow-board canvas library panel (module). Self-initialising; uses window.wb (core.js) only.
   - GET /library on load, on every palette open, and on Refresh (server scan is stateless, no cache).
   - click/drag an item -> card from card_template via wb.insertSubgraph.
   - cards whose ref has no matching installed library item get an "unresolved" badge (MutationObserver on #cards). */
const $ = id => document.getElementById(id);
const E = (t, c, h) => { const e = document.createElement(t); if (c) e.className = c; if (h != null) e.textContent = h; return e; };
const say = (m, cls) => { const e = $("msg"); if (e) { e.textContent = m; e.className = cls || ""; e.title = m; } };
const BADGED = new Set(["skill", "mcp", "plugin", "command"]); /* card types whose ref.name is a library lookup */
let items = [], scannedAt = "", loaded = false, filter = "", addN = 0;
const els = {};

function css() {
  const s = E("style");
  s.textContent = ".lib-hd{display:flex;align-items:center;justify-content:space-between;margin-top:14px}.lib-hd button{padding:2px 8px;font-size:11px}" +
    "#libQ{width:100%;margin-bottom:6px}#libList{max-height:46vh;overflow:auto}.li{padding:5px 8px;margin-bottom:5px;border:1px solid var(--line);border-radius:6px;cursor:pointer;background:var(--card)}" +
    ".li:hover{border-color:var(--sel)}.li b{font-weight:600;word-break:break-all}.li small{color:var(--mut);display:block;font-size:10px}" +
    ".li .k{display:inline-block;margin-right:6px;padding:0 5px;border-radius:8px;background:var(--line);font-size:10px;color:var(--mut)}.li.off{opacity:.55}" +
    "#libMeta{color:var(--mut);font-size:10px;margin:2px 0 6px}.badge-blocked{margin-left:6px;padding:0 5px;border-radius:8px;background:#e5484d;color:#fff;font-size:10px;font-weight:600}.badge-blocked.ok{background:var(--line);color:var(--mut);font-weight:400}.badge-unres{margin-left:4px;padding:0 5px;border-radius:8px;background:#e5484d;color:#fff;font-size:10px;font-weight:600}";
  document.head.appendChild(s);
}

function build() {
  const pal = $("pal"); if (!pal) return;
  css();
  const hd = E("div", "lib-hd"); hd.appendChild(E("h4", null, "Library")); els.refresh = E("button", null, "Refresh"); els.refresh.id = "libRefresh"; hd.appendChild(els.refresh);
  els.q = E("input"); els.q.id = "libQ"; els.q.placeholder = "filter library"; els.q.autocomplete = "off";
  els.meta = E("div"); els.meta.id = "libMeta";
  els.list = E("div"); els.list.id = "libList";
  pal.append(hd, els.q, els.meta, els.list);
  els.refresh.onclick = () => refresh(true);
  els.q.oninput = () => { filter = els.q.value.trim().toLowerCase(); renderList(); };
  const bp = $("bPal"); if (bp) bp.addEventListener("click", () => refresh(false));
}

async function refresh(announce) {
  try {
    const r = await fetch("/library"); const j = await r.json();
    if (!r.ok) throw new Error((j && j.error) || r.status);
    items = j.items || []; scannedAt = j.scanned_at || ""; loaded = true;
    if (announce) say("library refreshed: " + items.length + " items", "ok");
  } catch (e) { say("library load failed: " + e, "err"); }
  renderList(); badge();
}

function match(it) {
  if (!filter) return true;
  return (it.name + " " + it.kind + " " + (it.description || "") + " " + (it.triggers || []).join(" ") + " " + it.source_agent).toLowerCase().includes(filter);
}

function renderList() {
  if (!els.list) return;
  els.list.innerHTML = "";
  const shown = items.filter(match);
  els.meta.textContent = loaded ? shown.length + " / " + items.length + " items" + (scannedAt ? " · scanned " + scannedAt.slice(11, 19) : "") : "loading...";
  shown.slice(0, 200).forEach(it => {
    const d = E("div", "li" + (it.installed === false ? " off" : "")); d.dataset.libId = it.id; d.draggable = true;
    const b = E("div"); b.appendChild(E("span", "k", it.kind)); b.appendChild(E("b", null, it.name)); d.appendChild(b);
    { const tc = it.touches; /* v4 F-B12: gallery flags modules that reference a blocked connector tool */
      if (tc && tc.tools && tc.tools.length) {
        const bl = tc.blocked || [];
        const bg = E("span", "badge-blocked" + (bl.length ? "" : " ok"), bl.length ? "\u{1F512} blocked tool" : "✓ tools ok");
        bg.title = bl.length ? "references blocked tool(s): " + bl.join(", ") : "touches: " + tc.tools.map(t => t.tool).join(", ");
        if (bl.length) d.classList.add("has-blocked");
        b.appendChild(bg); } }
    if (it.description) d.appendChild(E("small", null, it.description.slice(0, 220)));
    d.title = (it.source_agent || "") + " · " + (it.path || "") + (it.installed === false ? " (not installed)" : "");
    { const tf = it.card_template && it.card_template.fields && it.card_template.fields.teach; if (tf) d.title += " - " + tf; } /* v3: teaching text on hover */
    d.addEventListener("click", () => addItem(it));
    if ((it.kind === "skill" || it.kind === "hook") && it.path) { const ib = E("button", "li-inspect", "Inspect"); ib.title = "Open in the inspector (read-only)"; ib.style.cssText = "float:right;padding:0 6px;font-size:10px"; ib.addEventListener("click", ev => { ev.stopPropagation(); if (window.wbInspect) window.wbInspect(it.path); else say("inspector not loaded", "err"); }); b.appendChild(ib); }
    d.addEventListener("dragstart", ev => { ev.dataTransfer.setData("text/plain", it.id); });
    els.list.appendChild(d);
  });
}

function addItem(it) {
  const wb = window.wb;
  if (!wb || !wb.board) { say("open a board first", "err"); return; }
  if (it.kind === "module") { document.dispatchEvent(new CustomEvent("wb:library-module", { detail: it })); say("module '" + it.name + "' - insert handled by modules panel", ""); return; }
  const t = it.card_template; if (!t) { say("no card template for " + it.name, "err"); return; }
  const vp = $("vp").getBoundingClientRect(), v = wb.view, off = (addN++ % 6) * 24;
  const card = JSON.parse(JSON.stringify(t));
  card.id = card.type + "-lib"; card.immutable = false; card.enabled = true; card.execution_mode = card.execution_mode || "observe";
  card.label = card.label || it.name; card.ref = card.ref || {}; card.ports = card.ports || { in: ["in"], out: ["out"] };
  /* insertSubgraph offsets +30/+30, so pre-subtract to land at viewport centre */
  card.pos = { x: wb.snap((vp.width / 2 - v.x) / v.k - 95 + off - 30), y: wb.snap((vp.height / 2 - v.y) / v.k - 40 + off - 30) };
  const prevOpen = $("app").classList.contains("palopen");
  wb.insertSubgraph([card], []);
  if (prevOpen) $("app").classList.remove("palopen");
  say("added " + card.type + " '" + card.label + "' from library - Save to persist", "ok");
}

function resolves(c) {
  const n = c.ref && c.ref.name; if (!n) return true;
  if (c.ref.kind === "proposed") return true;
  return items.some(i => i.name === n && i.installed !== false && (i.kind === c.type || c.type === "skill" && i.kind === "skill"));
}

function badge() {
  const wb = window.wb; if (!wb || !wb.board || !loaded) return;
  const byId = {}; wb.board.cards.forEach(c => { byId[c.id] = c; });
  document.querySelectorAll("#cards .card").forEach(el => {
    const c = byId[el.dataset.id]; if (!c) return;
    const tt = c.fields && c.fields.teach; if (tt) el.title = tt; else if (el.title && el.dataset.teach) el.removeAttribute("title"); if (tt) el.dataset.teach = "1"; /* v3: teach text on hover */
    const old = el.querySelector(".badge-unres");
    const bad = BADGED.has(c.type) && !resolves(c);
    if (bad && !old) { const b = E("small", "badge-unres", "unresolved"); b.title = "ref '" + (c.ref && c.ref.name) + "' not found in the installed library"; const hd = el.querySelector(".hd"); if (hd) hd.appendChild(b); }
    else if (!bad && old) old.remove();
  });
}

function init() {
  build();
  const cards = $("cards");
  if (cards) new MutationObserver(() => badge()).observe(cards, { childList: true });
  refresh(false);
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
window.wbLibrary = { refresh: () => refresh(true), get items() { return items; }, badge };
import("/canvas/js/modules.js").catch(e => say("modules.js failed to load: " + e, "err")); /* drag-drop, touch place, Save as module */
