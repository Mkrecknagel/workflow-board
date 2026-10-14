"use strict";
/* workflow-board autolayout.js: automatic card placement with ELK (vendored elkjs, EPL-2.0, canvas/vendor/elk.bundled.js, unmodified).
   ELK decides card POSITIONS (and boundary box sizes) ONLY. Lines are drawn by router.js on every render, so a layout and its check agree after any reload or drag.
   - Layered, left to right. Cycles are broken by the DFS rule in router.js findLoops: each loop-return edge is handed to ELK REVERSED, so ranks follow execution order
     and the return line is what gets drawn back (router.js puts it in a lane below the cards).
   - Swimlane boards (lanes with a declared wide minimum size) keep ELK for each lane's inside and shelf-pack the lanes in board order.
   - Lanes (group) and boundaries are compound nodes with padding equal to the canvas frame (top 40 lane / 36 boundary, 16 elsewhere); SEPARATE_CHILDREN, so lane frames are sibling nodes and cannot overlap.
   - Writes only pos (+ size of boundary cards). group_styles[*].size (a lane's minimum frame size) is honoured as the compound's minimum size. Pinned cards keep their position.
   window.wbAutoLayout = {compute, apply, run, undo, canUndo, ensureElk}. */
import { findLoops } from "./router.js";

const GRID = 8, sn = n => Math.round(n / GRID) * GRID, up = n => Math.ceil(n / GRID) * GRID, dn = n => Math.floor(n / GRID) * GRID;
const cid = s => s.slice(0, s.lastIndexOf("."));
let elkP = null, undoSnap = null;

export function ensureElk() {
  if (window.ELK) return Promise.resolve();
  if (!elkP) elkP = new Promise((res, rej) => { const s = document.createElement("script"); s.src = "/canvas/vendor/elk.bundled.js"; s.onload = res; s.onerror = () => { elkP = null; rej(new Error("elkjs failed to load")); }; document.head.appendChild(s); });
  return elkP;
}

const FLOW = { "elk.algorithm": "layered", "elk.direction": "RIGHT", "elk.randomSeed": "1", "elk.edgeRouting": "ORTHOGONAL",
  "elk.layered.cycleBreaking.strategy": "MODEL_ORDER", "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
  "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP", "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
  "elk.spacing.nodeNode": "40", "elk.layered.spacing.nodeNodeBetweenLayers": "112", "elk.spacing.edgeNode": "24", "elk.layered.spacing.edgeNodeBetweenLayers": "24",
  "elk.spacing.componentComponent": "56", "elk.separateConnectedComponents": "true", "elk.hierarchyHandling": "SEPARATE_CHILDREN" };

export async function compute(board, opts) {
  opts = opts || {};
  await ensureElk(); if (document.fonts && document.fonts.ready) await document.fonts.ready;
  const cards = board.cards, byId = new Map(cards.map(c => [c.id, c]));
  const els = new Map(); document.querySelectorAll("#cards .card").forEach(e => els.set(e.dataset.id, e));
  const sz = new Map(); window.wb.unlod(() => cards.forEach(c => { const e = els.get(c.id); sz.set(c.id, { w: (e && e.offsetWidth) || 192, h: (e && e.offsetHeight) || 112 }); }));
  const size = c => sz.get(c.id);
  const parentKey = c => (c.type !== "boundary" || true) && c.boundary && byId.has(c.boundary) ? c.boundary : (c.group ? "g:" + c.group : null);
  /* swimlane boards (lanes declared as wide rows via group_styles size) stack their lanes top to bottom; every other board flows left to right */
  const rows = Object.values(board.group_styles || {}).some(v => v && v.size && v.size.w >= 2 * v.size.h);
  const nodes = new Map(), root = { id: "root", layoutOptions: Object.assign({}, FLOW, { "elk.direction": "RIGHT", "elk.separateConnectedComponents": "true" }, { "elk.spacing.nodeNode": "64", "elk.layered.spacing.nodeNodeBetweenLayers": "140", "elk.padding": "[top=0,left=0,bottom=0,right=0]" }), children: [], edges: [] };
  const mk = (id, extra) => { const n = Object.assign({ id, children: [], edges: [] }, extra); nodes.set(id, n); return n; };
  const compOpts = (top) => Object.assign({}, FLOW, { "elk.padding": "[top=" + top + ",left=16,bottom=16,right=16]", "elk.nodeSize.constraints": "MINIMUM_SIZE", "elk.nodeSize.minimum": "(0,0)" });
  cards.forEach(c => { if (c.type === "boundary" && cards.some(x => x.boundary === c.id)) mk(c.id, { layoutOptions: compOpts(36) }); else { const s = size(c); mk(c.id, { width: s.w, height: s.h }); } });
  const gnames = []; cards.forEach(c => { if (c.group && !c.boundary && !gnames.includes(c.group)) gnames.push(c.group); });
  const gsz = g => { const z = board.group_styles && board.group_styles[g] && board.group_styles[g].size; return z && Number.isFinite(z.w) && Number.isFinite(z.h) ? z : null; };
  gnames.forEach(g => { const n = mk("g:" + g, { layoutOptions: compOpts(40) }), z = gsz(g); if (z) n.layoutOptions["elk.nodeSize.minimum"] = "(" + z.w + "," + z.h + ")"; }); /* group_styles size = a minimum frame size: ELK honours it so frames cannot collide */
  const parentOf = new Map(); /* node id -> parent node id ("root") */
  cards.forEach(c => { const p = parentKey(c); parentOf.set(c.id, p && nodes.has(p) ? p : "root"); });
  gnames.forEach(g => parentOf.set("g:" + g, "root"));
  const order = []; cards.forEach(c => { order.push(c.id); }); gnames.forEach(g => order.push("g:" + g));
  /* children in board order; compounds are placed where their first member sits */
  const seen = new Set();
  const attach = id => { if (seen.has(id)) return; seen.add(id); const p = parentOf.get(id); const par = p === "root" ? root : nodes.get(p); if (p !== "root") attach(p); par.children.push(nodes.get(id)); };
  cards.forEach(c => { const p = parentOf.get(c.id); if (p !== "root") attach(p); attach(c.id); });
  gnames.forEach(g => attach("g:" + g));
  const chain = id => { const a = [id]; let p = parentOf.get(id); while (p && p !== "root") { a.push(p); p = parentOf.get(p); } a.push("root"); return a; };
  const loops = findLoops(cards, board.edges), back = new Set(loops.map(l => l.edge));
  const seenE = new Set(); let ec = 0;
  board.edges.forEach((e, i) => {
    let f = cid(e.from), t = cid(e.to); if (!byId.has(f) || !byId.has(t) || f === t) return;
    if (back.has(i)) [f, t] = [t, f];
    const cf = chain(f), ct = chain(t), set = new Set(ct); const lca = cf.find(x => set.has(x));
    const sf = cf[cf.indexOf(lca) - 1] || f, st = ct[ct.indexOf(lca) - 1] || t;
    if (sf === st) return;
    const key = lca + "|" + sf + "|" + st; if (seenE.has(key)) return; seenE.add(key);
    const holder = lca === "root" ? root : nodes.get(lca); holder.edges.push({ id: "e" + (ec++), sources: [sf], targets: [st] });
  });
  const elk = new window.ELK();
  const out = await elk.layout(root);
  if (rows) { /* swimlane boards: lanes are rigid blocks laid out by ELK inside, then shelf-packed in board order (rows of lanes, left to right, wrapping) */
    const kids = out.children || [], maxW = Math.max(4400, ...kids.map(k => k.width)); let x = 0, y = 0, rh = 0;
    kids.forEach(k => { if (x > 0 && x + k.width > maxW) { x = 0; y += rh + 64; rh = 0; } k.x = x; k.y = y; x += k.width + 64; rh = Math.max(rh, k.height); });
  }
  const abs = new Map(); (function walk(n, ox, oy) { (n.children || []).forEach(ch => { const x = ox + ch.x, y = oy + ch.y; abs.set(ch.id, { x, y, w: ch.width, h: ch.height }); walk(ch, x, y); }); })(out, 0, 0);
  /* normalise: top-left of the board at (40,40) on the grid */
  let minX = 1e9, minY = 1e9; cards.forEach(c => { const a = abs.get(c.id); if (a) { minX = Math.min(minX, a.x); minY = Math.min(minY, a.y); } });
  gnames.forEach(g => { const a = abs.get("g:" + g); if (a) { minX = Math.min(minX, a.x + 16); minY = Math.min(minY, a.y + 40); } });
  const offx = 64 - minX, offy = 64 - minY, res = { cards: {}, moved: 0, loops: loops.length };
  cards.forEach(c => {
    const a = abs.get(c.id); if (!a) return;
    if (c.pin) return;
    if (c.type === "boundary" && cards.some(x => x.boundary === c.id)) { const x = dn(a.x + offx), y = dn(a.y + offy), w = up(a.x + offx + a.w) - x, h = up(a.y + offy + a.h) - y; res.cards[c.id] = { pos: { x, y }, size: { w, h } }; }
    else res.cards[c.id] = { pos: { x: sn(a.x + offx), y: sn(a.y + offy) } };
  });
  return res;
}

export function apply(plan) {
  const W = window.wb, b = W.board; let moved = 0;
  undoSnap = { b, pos: b.cards.map(c => [c, c.pos.x, c.pos.y]), sizes: b.cards.map(c => [c, c.size ? { w: c.size.w, h: c.size.h } : null]), gs: b.group_styles ? JSON.parse(JSON.stringify(b.group_styles)) : null };
  b.cards.forEach(c => { const p = plan.cards[c.id]; if (!p) return;
    if (c.pos.x !== p.pos.x || c.pos.y !== p.pos.y) { c.pos.x = p.pos.x; c.pos.y = p.pos.y; moved++; }
    if (p.size && (!c.size || c.size.w !== p.size.w || c.size.h !== p.size.h)) { c.size = { w: p.size.w, h: p.size.h }; moved++; } });
  if (window.wbArrange && typeof window.wbArrange.setUndo === "function") { try { window.wbArrange.setUndo({ b, pos: undoSnap.pos, gs: undoSnap.gs }); } catch (e) { /* optional hook */ } }
  W.render(); return moved;
}
export function undo() {
  const W = window.wb, s = undoSnap; if (!s || !W || s.b !== W.board) return false;
  s.pos.forEach(a => { a[0].pos.x = a[1]; a[0].pos.y = a[2]; }); s.sizes.forEach(a => { if (a[1]) a[0].size = a[1]; else delete a[0].size; });
  if (s.gs) s.b.group_styles = s.gs; else delete s.b.group_styles; undoSnap = null; W.render(); return true;
}
export async function run() {
  const W = window.wb; if (!W || !W.board) return -1;
  const say = (m, cls) => { const e = document.getElementById("msg"); if (e) { e.textContent = m; e.className = cls || ""; e.title = m; } };
  try { say("auto layout: computing..."); const plan = await compute(W.board); const n = apply(plan); W.fitView(); say("auto layout: moved " + n + " item(s), " + plan.loops + " loop(s) (autosaves; Undo layout available)", "ok"); return n; }
  catch (e) { say("auto layout failed: " + e.message + " (use Tidy)", "err"); return -1; }
}
window.wbAutoLayout = { compute: () => compute(window.wb.board), apply, run, undo, canUndo: () => !!(undoSnap && window.wb && undoSnap.b === window.wb.board), ensureElk };
