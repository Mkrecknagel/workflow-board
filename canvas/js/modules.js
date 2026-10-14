"use strict";
/* workflow-board canvas modules.js (module). Self-initialising; loaded by a one-line dynamic import in library.js.
   Uses only window.wb (board, sel, view, insertSubgraph, render) and window.wbLibrary (items, refresh).
   - "Save as module": selected cards + internal edges -> PUT /module/<name> (server validates; nothing else is written).
   - Library module item click / drop -> GET /module/<name> -> insertSubgraph with origin_module/module_version/pasted_at.
   - Drag and drop: HTML5 drop of ANY library item onto #vp creates the card at the drop point (desktop).
   - Touch path: long-press a library item (~450ms), then tap the canvas to place it there. Esc cancels.
   Nothing here posts, sends or executes anything; cards are observe-only templates. */
const $ = id => document.getElementById(id);
const E = (t, c, h) => { const e = document.createElement(t); if (c) e.className = c; if (h != null) e.textContent = h; return e; };
const say = (m, cls) => { const e = $("msg"); if (e) { e.textContent = m; e.className = cls || ""; e.title = m; } };
const wb = () => window.wb;
const epCard = s => s.slice(0, s.lastIndexOf("."));
const epPort = s => s.slice(s.lastIndexOf(".") + 1);
const NAME = /^[a-z0-9][a-z0-9-]*$/;
let armed = null, ready = false, tapXY = null, suppress = false, holdT = null, holdXY = null;

function toWorld(ev) { const r = $("vp").getBoundingClientRect(), v = wb().view; return { x: (ev.clientX - r.left - v.x) / v.k, y: (ev.clientY - r.top - v.y) / v.k }; }
function centre() { const r = $("vp").getBoundingClientRect(), v = wb().view; return { x: (r.width / 2 - v.x) / v.k - 95, y: (r.height / 2 - v.y) / v.k - 40 }; }
const snap = n => wb().snap(n);

/* ---------- save as module ---------- */
function buildPanel() {
  const pal = $("pal"); if (!pal) return;
  const s = E("style");
  s.textContent = ".mod-box{margin-top:14px}.mod-box input{width:100%;margin-bottom:6px}.mod-box button{width:100%}.li.armed{outline:2px solid var(--sel);outline-offset:1px}.li{-webkit-touch-callout:none;-webkit-user-select:none;user-select:none}";
  document.head.appendChild(s);
  const box = E("div", "mod-box"); box.appendChild(E("h4", null, "Modules"));
  const n = E("input"); n.id = "modName"; n.placeholder = "module name (a-z, 0-9, -)"; n.autocomplete = "off";
  const b = E("button", null, "Save as module"); b.id = "modSave"; b.title = "Saves the selected cards (and edges between them) as a reusable module. Writes modules/<name>.module.json only.";
  box.append(n, b); pal.appendChild(box);
  b.onclick = saveModule;
}

async function saveModule() {
  const w = wb(); if (!w || !w.board) { say("open a board first", "err"); return; }
  const name = $("modName").value.trim();
  if (!NAME.test(name)) { say("module name must match a-z 0-9 - (start with letter/digit)", "err"); return; }
  const ids = new Set(w.sel), cards = w.board.cards.filter(c => ids.has(c.id));
  if (!cards.length) { say("select one or more cards first (shift-click or marquee)", "err"); return; }
  const lock = cards.find(c => c.immutable);
  if (lock) { say("refused: '" + lock.id + "' is immutable and cannot be put in a module", "err"); return; }
  const inner = w.board.edges.filter(e => ids.has(epCard(e.from)) && ids.has(epCard(e.to)));
  const x0 = Math.min(...cards.map(c => c.pos.x)), y0 = Math.min(...cards.map(c => c.pos.y));
  const mc = cards.map(c => { const k = JSON.parse(JSON.stringify(c)); k.pos = { x: c.pos.x - x0, y: c.pos.y - y0 }; k.immutable = false; delete k.group; if (k.boundary && !ids.has(k.boundary)) delete k.boundary; /* v3: boundary + drill + size survive the round-trip; a membership pointing outside the selection would dangle */ delete k.origin_module; delete k.module_version; delete k.pasted_at; return k; });
  const hasIn = new Set(inner.map(e => e.to)), hasOut = new Set(inner.map(e => e.from));
  const ein = [], eout = [];
  mc.forEach(c => { c.ports.in.forEach(p => { if (!hasIn.has(c.id + "." + p) && !ein.includes(p)) ein.push(p); }); c.ports.out.forEach(p => { if (!hasOut.has(c.id + "." + p) && !eout.includes(p)) eout.push(p); }); });
  let version = 1;
  try { /* ask the (fresh) library, not GET /module/<new>, so a brand-new name never logs a 404 */
    const lr = await fetch("/library"); const lj = await lr.json(); const ex = (lj.items || []).find(i => i.kind === "module" && i.name === name);
    if (ex) version = (ex.version || 0) + 1;
  } catch (_) { /* new module */ }
  const mod = { schema: "workflow-board/module/v1", name: name, version: version, description: "Saved from board " + (w.board.board || "?") + " (" + mc.length + " cards)", created_at: new Date().toISOString(), origin_board: w.board.board, exposed_ports: { in: ein, out: eout }, cards: mc, edges: inner.map(e => ({ from: e.from, to: e.to })) };
  const r = await fetch("/module/" + name, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(mod, null, 2) });
  let j = null; try { j = await r.json(); } catch (_) { /* none */ }
  if (!r.ok) { say("module not saved (" + r.status + "): " + ((j && j.error) || "error"), "err"); return; }
  say("saved module '" + name + "' v" + version + " (" + mc.length + " cards)", "ok");
  if (window.wbLibrary) await window.wbLibrary.refresh();
  say("saved module '" + name + "' v" + version + " (" + mc.length + " cards)", "ok");
}

/* ---------- insert ---------- */
async function insertModule(it, at) {
  const w = wb(); if (!w || !w.board) { say("open a board first", "err"); return; }
  const name = it.name || (it.card_template && it.card_template.module);
  let m; try { const r = await fetch("/module/" + name); if (!r.ok) throw new Error(r.status); m = await r.json(); } catch (e) { say("module load failed: " + e, "err"); return; }
  const p = at || centre(), ox = snap(p.x) - 30, oy = snap(p.y) - 30, stamp = new Date().toISOString();
  const cards = m.cards.map(c => { const k = JSON.parse(JSON.stringify(c)); k.pos = { x: c.pos.x + ox, y: c.pos.y + oy }; k.origin_module = m.name; k.module_version = m.version; k.pasted_at = stamp; return k; });
  w.insertSubgraph(cards, m.edges);
  $("app").classList.remove("palopen");
  say("inserted module '" + m.name + "' v" + m.version + " (" + cards.length + " cards) - Save to persist", "ok");
}

function placeItem(it, at) {
  if (it.kind === "module") return insertModule(it, at);
  const w = wb(); if (!w || !w.board) { say("open a board first", "err"); return; }
  const t = it.card_template; if (!t) { say("no card template for " + it.name, "err"); return; }
  const card = JSON.parse(JSON.stringify(t));
  card.id = card.type + "-lib"; card.immutable = false; card.enabled = true; card.execution_mode = card.execution_mode || "observe";
  card.label = card.label || it.name; card.ref = card.ref || {}; card.ports = card.ports || { in: ["in"], out: ["out"] };
  card.pos = { x: snap(at.x) - 30, y: snap(at.y) - 30 }; /* insertSubgraph adds +30/+30, so the card lands on the snapped point */
  w.insertSubgraph([card], []);
  $("app").classList.remove("palopen");
  say("placed " + card.type + " '" + card.label + "' from library - Save to persist", "ok");
}

const findItem = id => ((window.wbLibrary && window.wbLibrary.items) || []).find(i => i.id === id);

/* ---------- desktop drop ---------- */
function wireDrop() {
  const vp = $("vp"); if (!vp) return;
  vp.addEventListener("dragover", ev => { if (ev.dataTransfer && [...ev.dataTransfer.types].includes("text/plain")) { ev.preventDefault(); ev.dataTransfer.dropEffect = "copy"; } });
  vp.addEventListener("drop", ev => {
    const id = ev.dataTransfer && ev.dataTransfer.getData("text/plain"); const it = id && findItem(id);
    if (!it) return; ev.preventDefault(); placeItem(it, toWorld(ev));
  });
}

/* ---------- touch: long-press an item, then tap the canvas ---------- */
function disarm() { armed = null; ready = false; document.querySelectorAll(".li.armed").forEach(e => e.classList.remove("armed")); }
function wireTouch() {
  const list = $("libList"), vp = $("vp"); if (!list || !vp) return;
  list.addEventListener("pointerdown", ev => {
    suppress = false; if (ev.pointerType !== "touch") return;
    const li = ev.target.closest && ev.target.closest(".li"); if (!li || ev.target.closest("button")) return;
    holdXY = { x: ev.clientX, y: ev.clientY }; clearTimeout(holdT);
    holdT = setTimeout(() => {
      const it = findItem(li.dataset.libId); if (!it) return;
      disarm(); armed = it; suppress = true; setTimeout(() => { suppress = false; }, 900); /* swallow only the release click */ li.classList.add("armed");
      $("app").classList.remove("palopen");
      say("placing '" + it.name + "': tap the canvas where it should go (Esc cancels)", "ok");
    }, 450);
  });
  const stop = () => clearTimeout(holdT);
  list.addEventListener("pointerup", stop); list.addEventListener("pointercancel", stop);
  list.addEventListener("pointermove", ev => { if (holdXY && Math.hypot(ev.clientX - holdXY.x, ev.clientY - holdXY.y) > 10) stop(); });
  list.addEventListener("contextmenu", ev => { if (ev.target.closest(".li")) ev.preventDefault(); });
  list.addEventListener("click", ev => { if (suppress) { suppress = false; ev.stopImmediatePropagation(); ev.preventDefault(); } }, true);
  vp.addEventListener("pointerdown", ev => { if (armed) { ready = true; tapXY = { clientX: ev.clientX, clientY: ev.clientY }; } }, true); /* the long-press release click must not place; only a fresh tap does */
  vp.addEventListener("click", ev => {
    if (!armed) return; ev.stopPropagation(); ev.preventDefault(); if (!ready) return;
    const it = armed, at = toWorld(tapXY || ev); disarm(); placeItem(it, at); /* pointerdown coords: a touch click event is retargeted by a few px */
  }, true);
  window.addEventListener("keydown", ev => { if (ev.key === "Escape" && armed) { disarm(); say("placement cancelled"); } });
}

/* ---------- library module items: click -> insert at centre ---------- */
document.addEventListener("wb:library-module", ev => { insertModule(ev.detail); });

function init(n) { n = n || 0; if (!$("libList") && n < 60) { setTimeout(() => init(n + 1), 50); return; } buildPanel(); wireDrop(); wireTouch(); }
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => init()); else init();
window.wbModules = { insertModule, placeItem, saveModule, get armed() { return armed; } };
