"use strict";
/* workflow-board router.js: deterministic in-canvas line router (pure functions, no DOM).
   Recomputed from card positions on EVERY render, nothing is saved. Same board + same positions -> same lines.
   - findLoops: DFS from the entry cards, in edge order. An edge whose target is on the DFS stack is a BACK-EDGE (a loop return).
   - routeAll : per edge, keep core.js's bezier when it is clean; draw DFS back-edges as a loop (right stub, down to a return lane
                BELOW every card and lane frame it spans, left along the lane, up, in-port stub); route any other edge that would
                touch a card / lane header band / boundary label strip with a deterministic orthogonal A* over a Hanan grid.
   All lines are polylines with 8px rounded corners (M/L/Q only). Obstacles = cards, group header bands (40px), boundary label strips (30px). */
export const STUB = 18, LEG = 32, RAD = 8, CLR = 10, LANE = 32, BAND = 40, STRIP = 30, BEND = 24;

/* ---------- loops ---------- */
export function findLoops(cards, edges) {
  const ok = c => c.type !== "note" && c.type !== "boundary", nodes = cards.filter(ok), idx = new Map(nodes.map((c, i) => [c.id, i]));
  const cid = s => s.slice(0, s.lastIndexOf("."));
  if (!nodes.length) return [];
  const out = nodes.map(() => []), indeg = nodes.map(() => 0);
  edges.forEach((e, i) => { const f = idx.get(cid(e.from)), t = idx.get(cid(e.to)); if (f === undefined || t === undefined) return; out[f].push([t, i]); if (f !== t) indeg[t]++; });
  const state = nodes.map(() => 0), back = new Map();
  const entries = [];
  nodes.forEach((c, i) => { if (!indeg[i]) entries.push(i); });
  if (!entries.length) { const j = nodes.findIndex(c => c.type === "trigger" || c.type === "input"); entries.push(j < 0 ? 0 : j); }
  const visit = s => {
    if (state[s]) return;
    const st = [[s, 0]]; state[s] = 1; const path = [s];
    while (st.length) {
      const fr = st[st.length - 1], u = fr[0];
      if (fr[1] >= out[u].length) { state[u] = 2; st.pop(); path.pop(); continue; }
      const [v, ei] = out[u][fr[1]++];
      if (state[v] === 1) { const k = path.indexOf(v); back.set(ei, { from: nodes[u].id, to: nodes[v].id, body: path.slice(k).map(n => nodes[n].id) }); }
      else if (state[v] === 0) { state[v] = 1; st.push([v, 0]); path.push(v); }
    }
  };
  entries.forEach(visit);
  nodes.forEach((c, i) => visit(i));
  const loops = [...back.entries()].sort((a, b) => a[0] - b[0]).map(([edge, l]) => {
    const fc = nodes[idx.get(l.from)];
    return { edge, from: l.from, to: l.to, body: l.body, self: l.from === l.to, kind: fc.type === "gate" ? "hitl" : (["agent", "tool", "step"].includes(fc.type) ? "retry" : "cycle") };
  });
  return loops;
}

/* ---------- sampling ---------- */
export function parsePath(d) {
  const pts = []; let cur = null; const re = /([MLCQ])([^MLCQ]*)/g; let m;
  while ((m = re.exec(d))) {
    const n = m[2].trim().split(/[\s,]+/).filter(Boolean).map(Number), k = m[1];
    if (k === "M" || k === "L") { const p = [n[0], n[1]]; if (k === "L" && cur) seg(pts, cur, p); else pts.push(p); cur = p; }
    else if (k === "C") { const p0 = cur, p1 = [n[0], n[1]], p2 = [n[2], n[3]], p3 = [n[4], n[5]]; const N = Math.max(8, Math.ceil(Math.hypot(p3[0] - p0[0], p3[1] - p0[1]) / 5));
      for (let i = 1; i <= N; i++) { const t = i / N, u = 1 - t; pts.push([u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0], u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]]); } cur = p3; }
    else if (k === "Q") { const p0 = cur, p1 = [n[0], n[1]], p2 = [n[2], n[3]]; const N = 6;
      for (let i = 1; i <= N; i++) { const t = i / N, u = 1 - t; pts.push([u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0], u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]]); } cur = p2; }
  }
  return pts;
}
function seg(pts, a, b) { const L = Math.hypot(b[0] - a[0], b[1] - a[1]), N = Math.max(1, Math.ceil(L / 4)); for (let i = 1; i <= N; i++) pts.push([a[0] + (b[0] - a[0]) * i / N, a[1] + (b[1] - a[1]) * i / N]); }
export function lengths(pts) { const s = [0]; for (let i = 1; i < pts.length; i++) s.push(s[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])); return s; }

/* first obstacle id hit by the sampled line, or null. m > 0 inflates the rect, m < 0 shrinks it.
   The line's own source card may hold only its first 12px, its own target card only its last 12px. */
export function firstHit(pts, obst, from, to, m) {
  if (!pts.length) return null;
  const s = lengths(pts), L = s[s.length - 1];
  let x0 = 1e18, y0 = 1e18, x1 = -1e18, y1 = -1e18;
  pts.forEach(p => { if (p[0] < x0) x0 = p[0]; if (p[0] > x1) x1 = p[0]; if (p[1] < y0) y0 = p[1]; if (p[1] > y1) y1 = p[1]; });
  for (const o of obst) {
    if (o.x - m > x1 || o.x + o.w + m < x0 || o.y - m > y1 || o.y + o.h + m < y0) continue;
    for (let i = 0; i < pts.length; i++) {
      const p = pts[i];
      if (p[0] > o.x - m && p[0] < o.x + o.w + m && p[1] > o.y - m && p[1] < o.y + o.h + m) {
        if (o.id === from && s[i] <= 12) continue;
        if (o.id === to && s[i] >= L - 12) continue;
        return o.id;
      }
    }
  }
  return null;
}

/* ---------- path building ---------- */
function clean(pts) {
  const o = [];
  pts.forEach(p => { const q = o[o.length - 1]; if (q && q[0] === p[0] && q[1] === p[1]) return; o.push(p); });
  const r = [];
  o.forEach(p => { while (r.length >= 2) { const a = r[r.length - 2], b = r[r.length - 1]; if ((a[0] === b[0] && b[0] === p[0]) || (a[1] === b[1] && b[1] === p[1])) r.pop(); else break; } r.push(p); });
  return r;
}
export function roundedD(pts0) {
  const pts = clean(pts0); if (pts.length < 2) return "";
  let d = "M" + pts[0][0] + "," + pts[0][1];
  for (let i = 1; i < pts.length - 1; i++) {
    const a = pts[i - 1], b = pts[i], c = pts[i + 1], l1 = Math.hypot(b[0] - a[0], b[1] - a[1]), l2 = Math.hypot(c[0] - b[0], c[1] - b[1]), r = Math.min(RAD, l1 / 2, l2 / 2);
    const p = [b[0] + (a[0] - b[0]) / l1 * r, b[1] + (a[1] - b[1]) / l1 * r], q = [b[0] + (c[0] - b[0]) / l2 * r, b[1] + (c[1] - b[1]) / l2 * r];
    d += " L" + p[0] + "," + p[1] + " Q" + b[0] + "," + b[1] + " " + q[0] + "," + q[1];
  }
  const z = pts[pts.length - 1]; return d + " L" + z[0] + "," + z[1];
}

/* ---------- A* on the Hanan grid of the (inflated) obstacles ---------- */
class Heap { constructor() { this.a = []; }
  lt(x, y) { return x[0] < y[0] || (x[0] === y[0] && x[1] < y[1]); }
  push(f, o, v) { const a = this.a; a.push([f, o, v]); let i = a.length - 1; while (i) { const p = (i - 1) >> 1; if (!this.lt(a[i], a[p])) break; [a[p], a[i]] = [a[i], a[p]]; i = p; } }
  pop() { const a = this.a, t = a[0], l = a.pop(); if (a.length) { a[0] = l; let i = 0; for (;;) { let m = i; const L = 2 * i + 1, R = L + 1; if (L < a.length && this.lt(a[L], a[m])) m = L; if (R < a.length && this.lt(a[R], a[m])) m = R; if (m === i) break; [a[m], a[i]] = [a[i], a[m]]; i = m; } } return t; }
  get size() { return this.a.length; } }
const uniq = arr => { arr.sort((a, b) => a - b); const o = []; arr.forEach(v => { if (!o.length || o[o.length - 1] !== v) o.push(v); }); return o; };
const lb = (a, v) => { let lo = 0, hi = a.length - 1; while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] < v) lo = m + 1; else hi = m; } return lo; };

export function astar(S, E, rects, box) {
  const rs = rects.filter(r => r.x1 > box.x0 && r.x0 < box.x1 && r.y1 > box.y0 && r.y0 < box.y1);
  const xv = [S.x, E.x, box.x0, box.x1], yv = [S.y, E.y, box.y0, box.y1];
  rs.forEach(r => { xv.push(Math.max(box.x0, Math.min(box.x1, r.x0)), Math.max(box.x0, Math.min(box.x1, r.x1))); yv.push(Math.max(box.y0, Math.min(box.y1, r.y0)), Math.max(box.y0, Math.min(box.y1, r.y1))); });
  const xs = uniq(xv), ys = uniq(yv), NX = xs.length, NY = ys.length, cw = NX - 1, ch = NY - 1;
  const cell = new Uint8Array(Math.max(1, cw * ch));
  rs.forEach(r => { const i0 = lb(xs, Math.max(box.x0, r.x0)), i1 = lb(xs, Math.min(box.x1, r.x1)), j0 = lb(ys, Math.max(box.y0, r.y0)), j1 = lb(ys, Math.min(box.y1, r.y1));
    for (let j = j0; j < j1; j++) for (let i = i0; i < i1; i++) cell[j * cw + i] = 1; });
  const cb = (i, j) => i < 0 || j < 0 || i >= cw || j >= ch ? 1 : cell[j * cw + i]; /* the window edge is a wall: clipped obstacles continue beyond it, so travelling along it would run through them */
  const si = lb(xs, S.x), sj = lb(ys, S.y), ei = lb(xs, E.x), ej = lb(ys, E.y);
  const DX = [1, 0, -1, 0], DY = [0, 1, 0, -1];
  const free = (i, j, d) => { /* step from node (i,j) in direction d: blocked iff both flanking cells are blocked */
    if (d === 0) return !(cb(i, j - 1) && cb(i, j)); if (d === 2) return !(cb(i - 1, j - 1) && cb(i - 1, j));
    if (d === 1) return !(cb(i - 1, j) && cb(i, j)); return !(cb(i - 1, j - 1) && cb(i, j - 1)); };
  const N = NX * NY * 4, g = new Float64Array(N).fill(Infinity), prev = new Int32Array(N).fill(-1);
  const h = (i, j) => Math.abs(xs[i] - xs[ei]) + Math.abs(ys[j] - ys[ej]);
  const heap = new Heap(); let ord = 0; const start = (sj * NX + si) * 4;
  g[start] = 0; heap.push(h(si, sj), ord++, start);
  let goal = -1, it = 0;
  while (heap.size && it++ < 3000000) {
    const [fk, , st] = heap.pop(), dir = st & 3, nd = st >> 2, i = nd % NX, j = (nd / NX) | 0, gc = g[st];
    if (fk - h(i, j) > gc + 1e-6) continue;
    if (i === ei && j === ej && dir !== 2) { goal = st; break; }
    for (let d = 0; d < 4; d++) {
      if (d === ((dir + 2) & 3) && st !== start) continue;
      if (st === start && d === 2) continue;
      const ni = i + DX[d], nj = j + DY[d]; if (ni < 0 || nj < 0 || ni >= NX || nj >= NY || !free(i, j, d)) continue;
      const len = Math.abs(xs[ni] - xs[i]) + Math.abs(ys[nj] - ys[j]), ng = gc + len + (d !== dir && st !== start ? BEND : 0), ns = (nj * NX + ni) * 4 + d;
      if (ng < g[ns] - 1e-9) { g[ns] = ng; prev[ns] = st; heap.push(ng + h(ni, nj), ord++, ns); }
    }
  }
  if (goal < 0) return null;
  const pts = []; for (let s = goal; s >= 0; s = prev[s]) { const nd = s >> 2; pts.push([xs[nd % NX], ys[(nd / NX) | 0]]); }
  return pts.reverse();
}

/* ---------- the router ----------
   in : {cards:[{id,type,x,y,w,h}], frames:[{name,x,y,w,h}], edges:[{from,to,a:{x,y},b:{x,y},d0}], quick}
   out: {routes:[{d,kind}|null], loops:[{edge,from,to,kind,body,lane}], stat:{bezier,ortho,loop,fail}} */
export function routeAll(inp) {
  const cards = inp.cards, frames = inp.frames || [], edges = inp.edges, cid = s => s.slice(0, s.lastIndexOf("."));
  const obst = [];
  cards.forEach(c => { if (!(c.w > 0 && c.h > 0)) return; if (c.type === "boundary") obst.push({ id: c.id, x: c.x, y: c.y, w: c.w, h: STRIP, kind: "strip" }); else obst.push({ id: c.id, x: c.x, y: c.y, w: c.w, h: c.h, kind: "card" }); });
  frames.forEach(f => obst.push({ id: "band:" + f.name, x: f.x, y: f.y, w: f.w, h: BAND, kind: "band" }));
  const loops = findLoops(cards, edges.map(e => ({ from: e.from, to: e.to }))).filter(l => edges[l.edge].a && edges[l.edge].b);
  const loopAt = new Map(loops.map(l => [l.edge, l]));
  const res = new Array(edges.length).fill(null), stat = { bezier: 0, ortho: 0, loop: 0, fail: 0 };
  const hits = (pts, e, m) => firstHit(pts, obst, cid(e.from), cid(e.to), m === undefined ? 3 : m);
  /* extents used by lane height + A* windows */
  const solids = cards.filter(c => c.w > 0 && c.h > 0).map(c => ({ x: c.x, y: c.y, w: c.w, h: c.h })).concat(frames.map(f => ({ x: f.x, y: f.y, w: f.w, h: f.h })));
  const mkRects = (cc, bc) => obst.map(o => { const k = o.kind === "band" ? bc : cc; return { x0: o.x - k, y0: o.y - k, x1: o.x + o.w + k, y1: o.y + o.h + k, id: o.id }; });
  const rectSets = [mkRects(CLR, 6), mkRects(5, 3)]; /* normal clearance first; tight lanes (little room between a header band and the cards under it) get a second, tighter pass */
  const allBox = (() => { let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9; solids.forEach(s => { x0 = Math.min(x0, s.x); y0 = Math.min(y0, s.y); x1 = Math.max(x1, s.x + s.w); y1 = Math.max(y1, s.y + s.h); }); return { x0: x0 - 300, y0: y0 - 300, x1: x1 + 300, y1: y1 + 400 }; })();
  const ortho = e => {
    const S = { x: e.a.x + STUB, y: e.a.y }, T = { x: e.b.x - STUB, y: e.b.y };
    for (const rects of rectSets) for (const pad of [300, 1200, 0]) {
      const box = pad ? { x0: Math.min(S.x, T.x) - pad, x1: Math.max(S.x, T.x) + pad, y0: Math.min(S.y, T.y) - pad, y1: Math.max(S.y, T.y) + pad } : allBox;
      const r = astar(S, T, rects, { x0: Math.min(box.x0, S.x - 1, T.x - 1), y0: Math.min(box.y0, S.y - 1, T.y - 1), x1: Math.max(box.x1, S.x + 1, T.x + 1), y1: Math.max(box.y1, S.y + 1, T.y + 1) });
      if (r) return [[e.a.x, e.a.y]].concat(r, [[e.b.x, e.b.y]]);
    }
    return null;
  };
  /* loops: widest spans get the lowest lanes; nested/overlapping spans stack +LANE */
  const spans = loops.map(l => { const e = edges[l.edge], sx = e.a.x + LEG, ex = e.b.x - LEG; return { l, e, lo: Math.min(sx, ex) - 8, hi: Math.max(sx, ex) + 8 }; })
    .sort((p, q) => (p.hi - p.lo) - (q.hi - q.lo) || p.l.edge - q.l.edge);
  const placed = [];
  spans.forEach(sp => {
    const { l, e } = sp, sx = e.a.x + LEG, ex = e.b.x - LEG; let pts = null;
    let base = -1e9; solids.forEach(s => { if (s.x < sp.hi && s.x + s.w > sp.lo) base = Math.max(base, s.y + s.h); });
    base = Math.max(base, e.a.y, e.b.y) + LANE;
    placed.forEach(q => { if (q.lo < sp.hi && q.hi > sp.lo) base = Math.max(base, q.lane + LANE); });
    if (sx > ex) {
      const cand = [[e.a.x, e.a.y], [sx, e.a.y], [sx, base], [ex, base], [ex, e.b.y], [e.b.x, e.b.y]];
      if (!hits(parsePath(roundedD(cand)), e)) pts = cand;
    }
    sp.lane = l.lane = base; placed.push(sp);
    if (!pts && !inp.quick) pts = ortho(e);
    if (pts) { res[l.edge] = { d: roundedD(pts), kind: "loop" }; stat.loop++; }
  });
  edges.forEach((e, i) => {
    if (res[i]) return;
    if (!e.a || !e.b) return;
    const bez = e.d0 ? parsePath(e.d0) : null;
    if (bez && e.a.x + STUB < e.b.x - STUB && !hits(bez, e)) { res[i] = { d: e.d0, kind: "bezier" }; stat.bezier++; return; }
    if (inp.quick) { res[i] = { d: e.d0, kind: "bezier" }; stat.bezier++; return; }
    const pts = ortho(e);
    if (pts && !hits(parsePath(roundedD(pts)), e, 0)) { res[i] = { d: roundedD(pts), kind: "ortho" }; stat.ortho++; }
    else { res[i] = { d: e.d0, kind: "fail", why: pts ? "hit:" + hits(parsePath(roundedD(pts)), e, 0) : "noroute" }; stat.fail++; }
  });
  return { routes: res, loops, stat };
}
