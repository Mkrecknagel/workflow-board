"use strict";
/* workflow-board readability.js: ONE pure geometry check, used by the live page (toolbar badge) and by scripts/check_board.js (headless Chromium).
   Judges what is actually DRAWN: card boxes from c.pos + the rendered size, lane frames from the .grp elements, lines from the rendered path.e d= strings.
   FAIL conditions (pass = both lists empty):
     overlaps  : two cards closer than M=8px; a card inside a foreign lane frame; two lane frames overlapping. Exempt: a card and its boundary ancestors,
                 nested boundaries (boundaries are regions).
     crossings : a line sampled (4px) strictly inside a non-endpoint card (shrunk 2px), inside any lane header band (40px), or inside a boundary label strip (30px).
                 A line may hold its own source card for its first 12px and its own target card for its last 12px. */
import { parsePath, firstHit, findLoops, BAND, STRIP } from "./router.js";

const M = 8;
const hit = (a, b, m) => a.x < b.x + b.w + m && b.x < a.x + a.w + m && a.y < b.y + b.h + m && b.y < a.y + a.h + m;

export function checkModel(model) {
  const cards = model.cards, byId = new Map(cards.map(c => [c.id, c]));
  const anc = id => { const s = new Set(); let c = byId.get(id), n = 0; while (c && c.boundary && n++ < 8) { s.add(c.boundary); c = byId.get(c.boundary); } return s; };
  const overlaps = [];
  for (let i = 0; i < cards.length; i++) for (let j = i + 1; j < cards.length; j++) {
    const a = cards[i], b = cards[j];
    if (a.type === "boundary" || b.type === "boundary") {
      if (a.type === "boundary" && b.type === "boundary") { if (anc(a.id).has(b.id) || anc(b.id).has(a.id)) continue; }
      else if (anc(a.id).has(b.id) || anc(b.id).has(a.id)) continue;
    }
    if (hit(a, b, M)) overlaps.push([a.id, b.id]);
  }
  model.frames.forEach(f => cards.forEach(c => { if (c.group !== f.name && c.type !== "boundary" && hit(c, f, 0)) overlaps.push([c.id, "frame:" + f.name]); }));
  for (let i = 0; i < model.frames.length; i++) for (let j = i + 1; j < model.frames.length; j++) if (hit(model.frames[i], model.frames[j], 0)) overlaps.push(["frame:" + model.frames[i].name, "frame:" + model.frames[j].name]);
  const obst = [];
  cards.forEach(c => { if (c.type === "boundary") obst.push({ id: c.id, x: c.x, y: c.y, w: c.w, h: STRIP }); else obst.push({ id: c.id, x: c.x, y: c.y, w: c.w, h: c.h }); });
  model.frames.forEach(f => obst.push({ id: "band:" + f.name, x: f.x, y: f.y, w: f.w, h: BAND }));
  const crossings = [];
  model.edges.forEach(e => {
    if (!e.d) return;
    const pts = parsePath(e.d); const seen = new Set();
    /* report every distinct obstacle, not only the first */
    let rest = obst.slice();
    for (let k = 0; k < 8; k++) { const h = firstHit(pts, rest, e.from, e.to, -2); if (!h) break; seen.add(h); rest = rest.filter(o => o.id !== h); }
    seen.forEach(h => crossings.push([e.label, h]));
  });
  return { overlaps, crossings };
}

/* live model from the open canvas (needs window.wb) */
export function liveModel() {
  const W = window.wb, b = W.board, cid = s => s.slice(0, s.lastIndexOf("."));
  const els = new Map(); document.querySelectorAll("#cards .card").forEach(e => els.set(e.dataset.id, e));
  const cards = [];
  W.unlod(() => b.cards.forEach(c => { const el = els.get(c.id); if (!el || !el.offsetWidth || !el.offsetHeight) return;
    cards.push({ id: c.id, type: c.type, group: c.group || null, boundary: c.boundary || null, x: c.pos.x, y: c.pos.y, w: el.offsetWidth, h: el.offsetHeight }); }));
  const frames = []; document.querySelectorAll("#groups .grp").forEach(g => { if (g.offsetWidth) frames.push({ name: g.dataset.group, x: parseFloat(g.style.left), y: parseFloat(g.style.top), w: parseFloat(g.style.width), h: parseFloat(g.style.height) }); });
  const edges = []; const paths = document.querySelectorAll("#edges path.e");
  b.edges.forEach((e, i) => { const p = paths[i]; if (!p || p.style.display === "none") return;
    edges.push({ from: cid(e.from), to: cid(e.to), label: e.from + "->" + e.to, d: p.getAttribute("d"), route: p.dataset.route || "bezier" }); });
  return { cards, frames, edges };
}

export function measure() {
  const W = window.wb, m = liveModel(), r = checkModel(m);
  const routes = { bezier: 0, ortho: 0, loop: 0, fail: 0 }; m.edges.forEach(e => { routes[e.route] = (routes[e.route] || 0) + 1; });
  const loops = findLoops(W.board.cards, W.board.edges);
  return { board: W.boardName, cards: m.cards.length, frames: m.frames.length, edges: m.edges.length, overlaps: r.overlaps, crossings: r.crossings,
    loops: loops.length, loop_edges: loops.map(l => l.from + "->" + l.to + " (" + l.kind + ")"), routes, pass: !r.overlaps.length && !r.crossings.length };
}

/* toolbar badge: re-checks 400ms after the last render */
let t = 0;
export function badge() {
  clearTimeout(t); t = setTimeout(() => {
    try { const W = window.wb; if (!W || !W.board) return; const r = measure(); let el = document.getElementById("wb-read");
      if (!el) { el = document.createElement("span"); el.id = "wb-read"; el.style.cssText = "padding:2px 8px;border-radius:10px;border:1px solid #888;white-space:nowrap;background:var(--panel,#fff);color:var(--ink,#111)";
        let tr = document.getElementById("wb-laytools"); if (!tr) { tr = document.createElement("div"); tr.id = "wb-laytools"; tr.style.cssText = "position:fixed;left:12px;bottom:12px;z-index:24;display:flex;gap:6px;align-items:center;font:12px system-ui,sans-serif"; document.body.appendChild(tr); } tr.appendChild(el); }
      if (el) { el.textContent = r.pass ? "layout ✓" : "layout ✗ " + r.overlaps.length + " overlap, " + r.crossings.length + " line"; el.title = r.pass ? "readability: 0 overlaps, 0 lines through boxes (" + r.loops + " loops)" : JSON.stringify({ overlaps: r.overlaps.slice(0, 6), crossings: r.crossings.slice(0, 6) }); el.dataset.pass = r.pass ? "1" : "0"; }
    } catch (e) { /* badge is best-effort */ }
  }, 400);
}
window.wbReadability = { measure, liveModel, checkModel, badge };
