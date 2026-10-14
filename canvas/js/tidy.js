"use strict";
/* workflow-board v4 tidy.js (N4, F-B6). One-click tidy: lay cards out in left-to-right columns by edge depth, stacked top-down with even gaps, on the 8px grid.
   Scope: the selected cards, or the whole board when nothing is selected (one set, so cross-lane flow reads left to right).
   Collapsed lanes, boundary cards and cards without a measured size are left alone.
   Idempotent: layers come from the edges only (never from positions), the anchor is the set's own top-left, and within a column order is by current y, so a second run moves 0 cards.
   Undo: snapshot has the SAME shape as arrange.js's undoSnap ({b, pos:[[card,x,y]], gs}); arrange.js keeps that variable module-private, so tidy keeps its own copy and ALSO
   hands it to window.wbArrange.setUndo(snap) when arrange exposes that hook (see N4 report). window.wbTidy = {run, undo, canUndo, layout}. Writes nothing but card positions. */
(function () {
  const W = () => window.wb, $ = id => document.getElementById(id);
  const GAPX = 64, GAPY = 24;
  const epCard = s => s.slice(0, s.lastIndexOf("."));
  const say = (m, cls) => { const e = $("msg"); if (!e) return; e.textContent = m; e.className = cls || ""; e.title = m };
  let undoSnap = null;

  const gridUp = n => { const G = W().GRID || 8; return Math.ceil(n / G) * G };
  const heightOf = c => { const e = document.querySelector('.card[data-id="' + c.id + '"]'); return gridUp(e && e.offsetHeight ? e.offsetHeight : 112) };
  const eligible = c => c.type !== "boundary";

  function snapshot() {
    const b = W().board;
    return { b: b, pos: b.cards.map(c => [c, c.pos.x, c.pos.y]), gs: b.group_styles ? JSON.parse(JSON.stringify(b.group_styles)) : null };
  }
  function restore(s) {
    s.pos.forEach(a => { a[0].pos.x = a[1]; a[0].pos.y = a[2] });
    if (s.gs) s.b.group_styles = s.gs; else delete s.b.group_styles;
  }

  /* target positions for one set of cards -> [[card,x,y]] (pure: reads the DOM for heights only) */
  function layout(cards, edges) {
    const S = W().snap, CW = W().CW || 192, ids = new Set(cards.map(c => c.id)), n = cards.length;
    if (!n) return [];
    const layer = {}; cards.forEach(c => { layer[c.id] = 0 });
    const inner = edges.map(e => [epCard(e.from), epCard(e.to)]).filter(([f, t]) => f !== t && ids.has(f) && ids.has(t));
    for (let it = 0; it < n; it++) {
      let ch = false;
      inner.forEach(([f, t]) => { if (layer[t] < layer[f] + 1) { layer[t] = layer[f] + 1; ch = true } });
      if (!ch) break;
    }
    const lo = Math.min(...cards.map(c => layer[c.id])); cards.forEach(c => { layer[c.id] -= lo });
    const minX = S(Math.min(...cards.map(c => c.pos.x))), minY = S(Math.min(...cards.map(c => c.pos.y)));
    const cols = {}; cards.forEach((c, i) => { (cols[layer[c.id]] = cols[layer[c.id]] || []).push([c, i]) });
    const out = [];
    Object.keys(cols).forEach(k => {
      const x = S(minX + Number(k) * (CW + GAPX)); let y = minY;
      cols[k].sort((a, b) => a[0].pos.y - b[0].pos.y || a[1] - b[1]).forEach(([c]) => { out.push([c, x, S(y)]); y = S(y) + heightOf(c) + GAPY });
    });
    return out;
  }

  function sets() {
    const w = W(), b = w.board, ok = c => eligible(c) && !(window.wbCollapse && c.group && window.wbCollapse.isCollapsed(c.group));
    return [b.cards.filter(c => (!w.sel || !w.sel.size || w.sel.has(c.id)) && ok(c))];
  }

  /* returns the number of cards that moved (0 = already tidy) */
  function run() {
    const w = W(); if (!w || !w.board) { say("open a board first", "err"); return -1 }
    const b = w.board, snap = snapshot(), plan = [];
    sets().forEach(s => { if (s.length) plan.push(...layout(s, b.edges)) });
    let moved = 0;
    plan.forEach(([c, x, y]) => { if (c.pos.x !== x || c.pos.y !== y) { c.pos.x = x; c.pos.y = y; moved++ } });
    if (!moved) { say("already tidy (0 cards moved)", "ok"); return 0 }
    undoSnap = snap;
    if (window.wbArrange && typeof window.wbArrange.setUndo === "function") { try { window.wbArrange.setUndo(snap) } catch (e) { /* hook optional */ } }
    w.render();
    say("tidied " + moved + " card(s) (autosaves; Undo tidy available)", "ok");
    return moved;
  }
  function undo() {
    const w = W(), s = undoSnap;
    if (!s || !w || s.b !== w.board) { say("nothing to undo", "err"); return false }
    restore(s); undoSnap = null; w.render(); say("tidy undone (autosaves)", "ok"); return true;
  }
  window.wbTidy = { run, undo, layout, canUndo: () => !!(undoSnap && W() && undoSnap.b === W().board) };
})();
