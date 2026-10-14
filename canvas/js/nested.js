"use strict";
/* workflow-board v5 N3: continuous infinite zoom (module). The visuals are always there; only which board is ACTIVE changes (at ~50%).
   - child boards of drill-capable cards render as live read-only PEEKS inside the card (<= 6 at once, <= 2 levels below the active board)
   - the card nearest the viewport centre is STAGED (>= 25%); when its host card fills >= 50% of the viewport for a 250 ms dwell the child becomes the
     ACTIVE board (view re-based so the screen does not move); when the active board's host card falls below 35% it hands back to the parent
   - ancestors keep drawing read-only behind the active board (#nest-ctx) so nothing cuts; label "Active: <board>" names the active board
   - handoff flushes pending autosave jobs to the OLD board first (N2: wbAutosave.flush); behaviour edits stay in a per-board Map
   - an empty card (no inner board) zooms into a VIRTUAL in-memory board; the file is created only on the first add, the parent's drill is set second
   Pure, node-testable: window.wbNestedPure {lodFor, thresholds, fFrac, pickStaged, activeBoard, fitInto, bboxOf, compose, inverse, ...}
   Runtime: window.wbNested {ready, swapping, enter, exit, exitTo, debug, ...}. WB_ZOOM_MODE="hard" (or ?zoom=hard) leaves everything to zoom.js. */
(function () {
  const root = typeof window !== "undefined" ? window : globalThis;
  const HH = 30, CW = 192, MH = 90, PAD = 4;
  const FAR = 0.08, STAGE = 0.25, ACT = 0.50, REL = 0.35, KMAX = 2.5, KMIN = 0.02, MAXPEEK = 6, LRU_MAX = 12;
  const LOD_T = [0.12, 0.40, 1.0], LOD_H = 0.10;
  /* F1/G2: anything can nest. Every card type can host an inner board; boundary (a region others sit inside), container and note are the only exceptions (unless they carry a drill) */
  const ELIGIBLE = ["trigger", "input", "skill", "hook", "tool", "agent", "gate", "output", "step", "component", "datastore", "external", "person"];

  /* ---------------- pure ---------------- */
  /* fixed levels by effective on-screen scale s: L0 <0.12 silhouette, L1 0.12-0.40 labels only, L2 0.40-1.0 cards, L3 >=1.0 ports/fields; +-10% hysteresis vs prev */
  function lodFor(s, prev) {
    let l; if (!(prev >= 0 && prev <= 3)) { l = 0; while (l < 3 && s >= LOD_T[l]) l++; return l }
    l = prev; while (l < 3 && s >= LOD_T[l] * (1 + LOD_H)) l++; while (l > 0 && s < LOD_T[l - 1] * (1 - LOD_H)) l--; return l;
  }
  const fFrac = (r, vp) => Math.max(r.w / vp.w, r.h / vp.h);
  const dist = (r, vp) => Math.hypot(r.x + r.w / 2 - vp.w / 2, r.y + r.h / 2 - vp.h / 2);
  const hasCentre = (r, vp) => vp.w / 2 >= r.x && vp.w / 2 <= r.x + r.w && vp.h / 2 >= r.y && vp.h / 2 <= r.y + r.h;
  /* thresholds for a host card of world size ww x wh: the 50% handoff is unreachable for small cards inside the 0.02..2.5 view clamp, so the
     staging / activation / release fractions are scaled by g = min(1, 0.9 * fmax / 0.5) (fmax = fraction the card fills at the max zoom). Big cards: g = 1. */
  function thresholds(ww, wh, vp, kmax) {
    const fmax = fFrac({ w: ww * (kmax || KMAX), h: wh * (kmax || KMAX) }, vp), g = Math.max(0.2, Math.min(1, 0.9 * fmax / ACT)), act = ACT * g;
    return { g, act, rel: REL * g, stage: Math.min(STAGE, 0.8 * act) };
  }
  const wsz = it => ({ ww: it.ww != null ? it.ww : it.rect.w, wh: it.wh != null ? it.wh : it.rect.h });
  /* the single staged card: min distance to the viewport centre among cards containing the centre with f >= stage; ties: smaller area, then id */
  function pickStaged(items, vp) {
    let best = null;
    for (const it of items) {
      const r = it.rect, w = wsz(it), th = thresholds(w.ww, w.wh, vp);
      if (fFrac(r, vp) < th.stage || !hasCentre(r, vp)) continue;
      const d = dist(r, vp), a = r.w * r.h;
      if (!best || d < best.d - 1e-9 || (Math.abs(d - best.d) <= 1e-9 && (a < best.a - 1e-9 || (Math.abs(a - best.a) <= 1e-9 && String(it.id) < String(best.it.id))))) best = { it, d, a };
    }
    return best && best.it;
  }
  /* chain = [..., parent, active, child, grandchild]; rects[i] = screen rect of the host card of chain[i] inside chain[i-1] (rects[0] unused).
     Active = deepest board whose host card holds >= act (entering) / >= rel (staying, i <= prev), contiguous from the top. Entering also needs the centre inside the card. */
  function activeBoard(chain, rects, prev, vp) {
    prev = prev || 0; let idx = 0;
    for (let i = 1; i < chain.length; i++) {
      const r = rects[i]; if (!r) break; const w = wsz(r), th = thresholds(w.ww, w.wh, vp), f = fFrac(r, vp);
      if (i <= prev ? f >= th.rel : (f >= th.act && hasCentre(r, vp))) idx = i; else break;
    }
    return { index: idx, name: chain[idx] };
  }
  function bboxOf(doc) {
    const cs = (doc && doc.cards) || []; if (!cs.length) return { x: 0, y: 0, w: 400, h: 240 };
    let x0 = 1e18, y0 = 1e18, x1 = -1e18, y1 = -1e18;
    cs.forEach(c => { const w = (c.size && c.size.w) || CW, h = (c.size && c.size.h) || MH; x0 = Math.min(x0, c.pos.x); y0 = Math.min(y0, c.pos.y); x1 = Math.max(x1, c.pos.x + w); y1 = Math.max(y1, c.pos.y + h) });
    return { x: x0, y: y0, w: Math.max(1, x1 - x0), h: Math.max(1, y1 - y0) };
  }
  /* child coords -> host coords: p = s*c + (ox, oy); the child bbox is centred and fitted inside rect */
  function fitInto(bb, r) { const s = Math.min(r.w / bb.w, r.h / bb.h); return { s, ox: r.x + (r.w - bb.w * s) / 2 - bb.x * s, oy: r.y + (r.h - bb.h * s) / 2 - bb.y * s } }
  const compose = (M, F) => ({ k: M.k * F.s, x: M.k * F.ox + M.x, y: M.k * F.oy + M.y });             /* child->screen given parent->screen M */
  const inverse = (M, F) => { const k = M.k / F.s; return { k, x: M.x - k * F.ox, y: M.y - k * F.oy } }; /* parent->screen given child->screen M */
  const toScreen = (M, r) => ({ x: M.k * r.x + M.x, y: M.k * r.y + M.y, w: M.k * r.w, h: M.k * r.h });
  const slug = (b, id) => String(b + "-" + id).toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "");
  const pure = { lodFor, thresholds, fFrac, dist, hasCentre, pickStaged, activeBoard, bboxOf, fitInto, compose, inverse, toScreen, slug, ELIGIBLE, K: { FAR, STAGE, ACT, REL, KMAX, KMIN, MAXPEEK, LRU_MAX, LOD_T } };
  root.wbNestedPure = pure;

  /* ---------------- runtime (browser only) ---------------- */
  if (typeof document === "undefined" || !document.getElementById || !document.getElementById("vp")) return;
  const hard = (() => { try { return root.WB_ZOOM_MODE === "hard" || /[?&]zoom=hard\b/.test(location.search) || localStorage.getItem("WB_ZOOM_MODE") === "hard" } catch (_) { return root.WB_ZOOM_MODE === "hard" } })();
  if (hard) { root.wbNested = { ready: false, mode: "hard", swapping: false }; return }

  const $ = id => document.getElementById(id), W = () => root.wb, Z = () => root.wbZoom;
  const E = (t, c, h) => { const e = document.createElement(t); if (c) e.className = c; if (h != null) e.textContent = h; return e };
  const coarse = () => !!(Z() && Z().coarse && Z().coarse());
  const cardEl = id => document.querySelector('#cards .card[data-id="' + String(id).replace(/"/g, '\\"') + '"]');
  const vpSize = () => { const r = $("vp").getBoundingClientRect(); return { w: r.width || 1, h: r.height || 1 } };
  const HEX = /^#[0-9a-fA-F]{6}$/;
  const cardColor = c => (c.style && HEX.test(c.style.color || "") && c.style.color) || (W().TYPE_COLOR && W().TYPE_COLOR[c.type]) || "#7A8396";

  const kept = new Map(), virtual = new Map(), vorigin = new Map(), pending = new Map(), lru = new Map(), fetching = new Map(), fits = new Map(), peekEls = new Map();
  const noEnter = new Set();
  let names = null, anc = [], active = null, swapping = false, ext = null, lastGesture = 0, lastKey = "", dirty = true, hold = { idx: -1, since: 0 }, plan = null, ctxEls = [], ctxKey = "", docsTick = 0, lastDocsTick = -1;
  const dbg = { peeks: 0, maxPeeks: 0, docs: 0, maxDocs: 0, handoffs: 0, active: null, depth: 0, fetches: 0, staged: null };
  const _fetch = root.fetch.bind(root);

  const CSS = `
#nest-label{position:absolute;left:50%;top:8px;transform:translateX(-50%);z-index:6;max-width:calc(100% - 340px);padding:3px 12px;border:1px solid var(--line);border-radius:14px;background:var(--panel);color:var(--ink);box-shadow:0 1px 6px #0003;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;pointer-events:none}
#nest-label b{font-weight:600}#nest-label .nl-z{margin-left:8px;color:var(--mut)}
#nest-ctx{position:absolute;left:0;top:0;width:1px;height:1px;overflow:visible;pointer-events:none}
.nm-layer{position:absolute;left:0;top:0;transform-origin:0 0}
.nm-sw{position:absolute;left:0;top:0;transform-origin:0 0}
.nm-c{position:absolute;box-sizing:border-box;outline:calc(2px / var(--e,1)) solid var(--tc);outline-offset:calc(-2px / var(--e,1));background:color-mix(in srgb,var(--tc) 14%,var(--card));border-radius:calc(6px / var(--e,1));overflow:visible;pointer-events:none}
.nm-c .nm-t,.nm-c .nm-s{display:none;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;padding:0 calc(4px / var(--e,1));pointer-events:none}
.nm-c .nm-t{font-weight:600;color:var(--ink);font-size:min(calc(11px / var(--e,1)),40px);line-height:1.3}
.nm-c .nm-s{color:var(--mut);font-size:min(calc(9px / var(--e,1)),30px)}
[data-nl="0"] .nm-c{background:var(--tc)}
[data-nl="1"] .nm-t,[data-nl="2"] .nm-t,[data-nl="3"] .nm-t{display:block}[data-nl="3"] .nm-s{display:block}
.nm-c.bnd{background:color-mix(in srgb,var(--tc) 6%,transparent);outline-style:dashed}
.nm-peek{position:absolute;left:0;top:${HH}px;right:0;bottom:0;overflow:hidden;z-index:2;background:color-mix(in srgb,var(--edge) 10%,var(--card));cursor:zoom-in;pointer-events:auto}
.card>.nm-peek{left:-2px;right:-2px;bottom:-2px}.nm-c>.nm-peek{border-top:calc(1px / var(--e,1)) solid var(--line)}
.nm-peek .nm-c{pointer-events:auto}.card[data-type=boundary]>.nm-peek{z-index:0}
.nm-empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;text-align:center;color:var(--mut);font-size:10px;padding:2px;pointer-events:none}
.nm-hint{position:absolute;right:3px;bottom:2px;z-index:4;font-size:10px;padding:0 6px;border-radius:8px;background:var(--sel);color:#fff;pointer-events:none;max-width:90%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.card[data-peek]>:not(.hd):not(.nm-peek):not(.nm-hint):not(.zm-d){visibility:hidden}
.card.nm-staged{outline:2px solid var(--sel)}
.card .nm-badge{position:absolute;left:6px;bottom:-11px;z-index:3;font-size:10px;padding:1px 6px;border-radius:8px;background:#b36b00;color:#fff;pointer-events:none}
`;

  /* ---------------- docs ---------------- */
  function lruPut(n, d) { lru.delete(n); lru.set(n, d); while (lru.size > LRU_MAX) lru.delete(lru.keys().next().value); dbg.docs = lru.size; dbg.maxDocs = Math.max(dbg.maxDocs, lru.size) }
  function ensureVirtual(n) { let d = virtual.get(n); if (!d) { d = { schema: "workflow-board/v1", board: n, version: 1, execution_mode: "observe", saved_at: new Date().toISOString(), cards: [], edges: [], integrity_hash: null }; virtual.set(n, d) } return d }
  function docFor(n) {
    if (n === active && W().board) return W().board;
    const k = kept.get(n); if (k) return k.doc;
    if (virtual.has(n)) return virtual.get(n);
    if (lru.has(n)) { const d = lru.get(n); lru.delete(n); lru.set(n, d); return d }
    if (names && !names.includes(n)) return ensureVirtual(n);
    if (!fetching.has(n)) {
      dbg.fetches++;
      fetching.set(n, _fetch("/board/" + encodeURIComponent(n)).then(r => r.ok ? r.json() : null).then(d => { if (d && Array.isArray(d.cards)) lruPut(n, d); else if (names && !names.includes(n)) ensureVirtual(n) }).catch(() => { }).then(() => { fetching.delete(n); docsTick++; dirty = true }));
    }
    return null;
  }
  async function loadNames() { try { const r = await _fetch("/boards"), j = await r.json(); names = (j && j.boards) || [] } catch (_) { names = names || [] } }
  /* child board name for a card: its drill, a pending (unsaved) drill, else the deterministic slug of an inner board that may not exist yet */
  function childOf(board, c) {
    const nm = c.drill || pending.get(board + "|" + c.id) || slug(board, c.id), real = !!(names && names.includes(nm));
    if (!real && !virtual.has(nm)) { vorigin.set(nm, { parent: board, card: c.id, hasDrill: !!c.drill }) }
    return { name: nm, virtual: !real };
  }
  const eligible = c => c && c.type !== "note" && (!!c.drill || ELIGIBLE.includes(c.type));

  /* the board swap re-uses core openBoard; docs with unsaved edits and virtual docs are served from memory instead of the (stale / missing) file */
  const jsonRes = d => new Response(JSON.stringify(d), { status: 200, headers: { "Content-Type": "application/json" } });
  root.fetch = function (input, init) {
    try {
      const url = typeof input === "string" ? input : input.url, m = /^\/board\/([a-z0-9][a-z0-9-]*)$/.exec(new URL(url, location.href).pathname), meth = (init && init.method) || (typeof input !== "string" && input.method) || "GET";
      if (m && meth === "GET") { const n = m[1], k = kept.get(n); if (k) return Promise.resolve(jsonRes(k.doc)); if (virtual.has(n) && !(names && names.includes(n))) return Promise.resolve(jsonRes(virtual.get(n))) }
    } catch (_) { }
    return _fetch(input, init);
  };

  /* ---------------- geometry of cards ---------------- */
  function geomReal(c) { const el = cardEl(c.id), hd = el && el.querySelector(":scope>.hd"); return { x: c.pos.x, y: c.pos.y, w: (c.size && c.size.w) || (el && el.offsetWidth) || CW, h: (el && el.offsetHeight) || (c.size && c.size.h) || MH, hd: (hd && hd.offsetHeight) || HH } }
  const geomMini = c => ({ x: c.pos.x, y: c.pos.y, w: (c.size && c.size.w) || CW, h: (c.size && c.size.h) || MH, hd: HH });
  /* fit of the child's bbox into the host card, RELATIVE to the card's top-left (survives dragging the host card); cached per host+child */
  function fitFor(board, c, g, name, cdoc) {
    const key = board + "|" + c.id + "|" + name; let f = fits.get(key);
    if (!f) { const inner = { x: PAD, y: g.hd + PAD, w: Math.max(10, g.w - 2 * PAD), h: Math.max(10, g.h - g.hd - 2 * PAD) }, F = fitInto(bboxOf(cdoc), inner); f = { s: F.s, rx: F.ox, ry: F.oy, hd: g.hd }; fits.set(key, f) }
    return f;
  }
  const absFit = (pos, f) => ({ s: f.s, ox: pos.x + f.rx, oy: pos.y + f.ry });

  /* ---------------- read-only mini renderer ---------------- */
  function buildMini(doc, excludeId) {
    const sw = E("div", "nm-sw"); const frag = document.createDocumentFragment();
    (doc.cards || []).forEach(c => {
      if (c.id === excludeId) return; const g = geomMini(c);
      const d = E("div", "nm-c" + (c.type === "boundary" ? " bnd" : "")); d.dataset.id = c.id; d.dataset.type = c.type;
      d.style.cssText = "left:" + g.x + "px;top:" + g.y + "px;width:" + g.w + "px;height:" + g.h + "px;--tc:" + cardColor(c);
      d.appendChild(E("div", "nm-t", c.label || c.id)); d.appendChild(E("div", "nm-s", c.type + (c.drill ? " ▸ " + c.drill : "")));
      frag.appendChild(d);
    });
    sw.appendChild(frag); return sw;
  }

  /* ---------------- plan: which peeks, which card is staged, which board is active ---------------- */
  function planLevel(board, doc, M, geomOf, lvl, vp) {
    const items = [];
    for (const c of doc.cards || []) {
      if (!eligible(c)) continue; const g = geomOf(c), sr = toScreen(M, { x: g.x, y: g.y, w: g.w, h: g.h }), f = fFrac(sr, vp);
      if (f < FAR || sr.x > vp.w || sr.y > vp.h || sr.x + sr.w < 0 || sr.y + sr.h < 0) continue;
      items.push({ board, c, id: c.id, g, rect: sr, ww: g.w, wh: g.h, f, d: dist(sr, vp), lvl });
    }
    items.sort((a, b) => a.d - b.d || (a.id < b.id ? -1 : 1)); return items;
  }
  function attachChild(it, M) {
    it.child = childOf(it.board, it.c); it.cdoc = docFor(it.child.name);
    if (it.cdoc) { it.fit = fitFor(it.board, it.c, it.g, it.child.name, it.cdoc); it.abs = absFit(it.g, it.fit); it.M = compose(M, it.abs) }
  }
  function ancInfo(M) { /* screen transforms of up to 2 ancestors: [parent, grandparent] (nearest first) */
    const out = []; let cur = M;
    for (let j = anc.length - 1, n = 0; j >= 0 && n < 2; j--, n++) {
      const link = anc[j], pdoc = docFor(link.board), card = pdoc && (pdoc.cards || []).find(c => c.id === link.card), pos = card ? card.pos : link.pos;
      const childName = j === anc.length - 1 ? active : anc[j + 1].board, cdoc = docFor(childName);
      if (!link.fit) { if (!pdoc || !cdoc) break; const g = { x: pos.x, y: pos.y, w: link.cw || (card && card.size && card.size.w) || CW, h: link.ch || (card && card.size && card.size.h) || MH, hd: HH }; link.fit = fitFor(link.board, card || { id: link.card }, g, childName, cdoc) }
      link.pos = pos;
      const F = absFit(pos, link.fit), MP = inverse(cur, F), cw = link.cw || (card && card.size && card.size.w) || CW, ch = link.ch || (card && card.size && card.size.h) || MH;
      out.push({ link, M: MP, rect: toScreen(MP, { x: pos.x, y: pos.y, w: cw, h: ch }), ww: cw, wh: ch }); cur = MP;
    }
    return out;
  }
  function computePlan() {
    const w = W(), doc = w.board, vp = vpSize(), M = { k: w.view.k, x: w.view.x, y: w.view.y };
    const items1 = planLevel(active, doc, M, geomReal, 1, vp); items1.forEach(it => { it.child = childOf(it.board, it.c) });
    /* only boards that exist are peeked; an empty (virtual) inner board shows only while its card is the staged one */
    const sel1 = items1.filter(it => !it.child.virtual).slice(0, MAXPEEK), s1 = pickStaged(items1.filter(it => !noEnter.has(it.id)), vp);
    if (s1 && !sel1.includes(s1)) { if (sel1.length >= MAXPEEK) sel1.pop(); sel1.push(s1) }
    sel1.forEach(it => attachChild(it, M));
    let sel2 = []; const rem = MAXPEEK - sel1.length;
    if (rem > 0) {
      sel1.forEach(q => { if (q.cdoc) planLevel(q.child.name, q.cdoc, q.M, geomMini, 2, vp).forEach(it => { it.parent = q; it.child = childOf(it.board, it.c); if (!it.child.virtual) sel2.push(it) }) });
      sel2.sort((a, b) => a.d - b.d || (a.id < b.id ? -1 : 1)); sel2 = sel2.slice(0, rem); sel2.forEach(it => attachChild(it, it.parent.M));
    }
    const s2 = s1 && s1.cdoc ? pickStaged(sel2.filter(x => x.parent === s1 && x.cdoc), vp) : null;
    const ai = ancInfo(M), up = ai.slice().reverse();      /* oldest ancestor first */
    const chain = up.map(a => a.link.board).concat([active]), rr = [null];
    for (let i = 1; i < chain.length; i++) { const h = ai[up.length - i]; rr.push({ x: h.rect.x, y: h.rect.y, w: h.rect.w, h: h.rect.h, ww: h.ww, wh: h.wh }) }  /* host rect of chain[i] inside chain[i-1] */
    const prev = up.length, path = [];
    if (s1 && s1.cdoc && s1.M) { chain.push(s1.child.name); rr.push(Object.assign({}, s1.rect, { ww: s1.g.w, wh: s1.g.h })); path.push(s1); if (s2 && s2.M) { chain.push(s2.child.name); rr.push(Object.assign({}, s2.rect, { ww: s2.g.w, wh: s2.g.h })); path.push(s2) } }
    const res = activeBoard(chain, rr, prev, vp);
    return { vp, M, items1, sel1, sel2, s1, s2, ai, up, chain, rects: rr, prev, path, target: res.index };
  }

  /* ---------------- DOM sync ---------------- */
  function syncPeeks(p) {
    const want = new Map();
    p.sel1.forEach(it => { if (it.cdoc) { it.key = active + "|" + it.id; want.set(it.key, it) } });
    p.sel2.forEach(it => { if (it.cdoc && it.parent.key) { it.key = it.parent.key + ">" + it.id; want.set(it.key, it) } });
    for (const [k, pe] of peekEls) { const it = want.get(k); if (!it || pe.doc !== it.cdoc || !pe.host.isConnected) { pe.el.remove(); if (pe.host) pe.host.removeAttribute("data-peek"); peekEls.delete(k) } }
    [1, 2].forEach(lvl => want.forEach((it, key) => {
      if (it.lvl !== lvl) return;
      let host; if (lvl === 1) host = cardEl(it.id); else { const par = peekEls.get(it.parent.key); host = par && par.sw.querySelector('.nm-c[data-id="' + String(it.id).replace(/"/g, '\\"') + '"]') }
      if (!host) return;
      let pe = peekEls.get(key);
      if (!pe || !host.contains(pe.el)) {
        if (pe) pe.el.remove();
        const el = E("div", "nm-peek"), sw = buildMini(it.cdoc, null); el.dataset.board = it.child.name; el.dataset.virtual = it.child.virtual ? "1" : "0"; el.appendChild(sw);
        if (it.child.virtual && !(it.cdoc.cards || []).length) el.appendChild(E("div", "nm-empty", "empty inner board — zoom in to build here"));
        el.addEventListener("pointerdown", ev => { ev.stopPropagation(); ev.preventDefault(); const q = plan && [...(plan.sel1 || []), ...(plan.sel2 || [])].find(x => x.key === key); if (q) descend(q.lvl === 2 ? [q.parent, q] : [q]) });
        host.appendChild(el); host.setAttribute("data-peek", "1"); pe = { el, sw, host, doc: it.cdoc, nl: undefined }; peekEls.set(key, pe);
      }
      const f = it.fit, e = it.M.k; pe.el.style.top = (lvl === 1 ? f.hd - 2 : f.hd) + "px"; pe.sw.style.transform = "translate(" + f.rx + "px," + (f.ry - f.hd) + "px) scale(" + f.s + ")"; pe.sw.style.setProperty("--e", e);
      pe.nl = lodFor(e, pe.nl); pe.el.dataset.nl = String(pe.nl); pe.sw.dataset.nl = String(pe.nl);
    }));
    document.querySelectorAll("#cards .card.nm-staged").forEach(el => { if (!p.s1 || el.dataset.id !== p.s1.id) { el.classList.remove("nm-staged"); const h = el.querySelector(":scope>.nm-hint"); if (h) h.remove() } });
    if (p.s1) { const el = cardEl(p.s1.id); if (el) { el.classList.add("nm-staged"); let h = el.querySelector(":scope>.nm-hint"); if (!h) { h = E("span", "nm-hint"); el.appendChild(h) } h.textContent = "→ " + p.s1.child.name } }
    dbg.staged = p.s1 ? p.s1.id : null; dbg.peeks = peekEls.size; dbg.maxPeeks = Math.max(dbg.maxPeeks, peekEls.size);
  }
  function syncCtx(p) {
    const key = active + "<" + anc.map(a => a.board + "|" + a.card).slice(-2).join("<") + "#" + docsTick;
    if (key !== ctxKey) {
      ctxKey = key; const host = $("nest-ctx"); host.innerHTML = ""; ctxEls = []; let parent = host;
      for (let j = anc.length - 1, n = 0; j >= 0 && n < 2; j--, n++) {
        const link = anc[j], pdoc = docFor(link.board); if (!pdoc || !link.fit) break;
        const card = (pdoc.cards || []).find(c => c.id === link.card), pos = card ? card.pos : link.pos, F = absFit(pos, link.fit);
        const layer = E("div", "nm-layer"); layer.style.transform = "translate(" + (-F.ox / F.s) + "px," + (-F.oy / F.s) + "px) scale(" + (1 / F.s) + ")";
        const mini = buildMini(pdoc, link.card); layer.appendChild(mini); parent.appendChild(layer); ctxEls.push({ layer, mini, nl: undefined }); parent = layer;
      }
    }
    ctxEls.forEach((c, i) => { const a = p.ai[i]; if (!a) return; c.mini.style.setProperty("--e", a.M.k); c.nl = lodFor(a.M.k, c.nl); c.mini.dataset.nl = String(c.nl) });
  }
  function setLabel() {
    const L = $("nest-label"); if (!L) return; const t = L.querySelector(".nl-t"); t.textContent = ""; t.appendChild(document.createTextNode("Active: ")); t.appendChild(E("b", null, active || "")); L.dataset.board = active || ""; L.title = anc.length ? anc.map(a => a.board).concat([active]).join(" › ") : (active || "");
  }

  /* ---------------- handoff ---------------- */
  function behaviourDirty(name, doc) { const A = root.wbAutosave, st = A && A.states.get(name); return !!(st && doc && A.semanticText(st.baseline) !== A.semanticText(doc)) }
  async function ensureCreated(name, doc) {
    if (!virtual.has(name) || !doc || !(doc.cards || []).length || (names && names.includes(name))) return;
    doc.saved_at = new Date().toISOString();
    const r = await _fetch("/board/" + encodeURIComponent(name), { method: "PUT", headers: { "Content-Type": "application/json", "X-WB-Source": "canvas" }, body: JSON.stringify(doc, null, 2) });
    const m = $("msg");
    if (r.ok) {
      virtual.delete(name); names = (names || []).concat([name]); if (root.wbAutosave) root.wbAutosave.saved(name, doc);
      const o = vorigin.get(name); if (o && !o.hasDrill) { pending.set(o.parent + "|" + o.card, name); if (m) { m.textContent = "created inner board " + name + " - Save " + o.parent + " to link it"; m.className = "ok" } }
      else if (m) { m.textContent = "created board " + name; m.className = "ok" }
    } else { let j = null; try { j = await r.json() } catch (_) { } if (m) { m.textContent = "inner board not created (" + r.status + "): " + ((j && j.error) || "error"); m.className = "err" } }
  }
  async function swap(name, view, newAnc) {
    if (swapping) return false; swapping = true; root.wbNested.swapping = true;
    const w = W(), old = w.boardName || (w.board && w.board.board), oldDoc = w.board, oldAnc = anc, oldActive = active;
    try {
      if (root.wbAutosave) await root.wbAutosave.flush(false);                       /* pending visual jobs go to the OLD board first */
      const st = root.wbAutosave && root.wbAutosave.states.get(old);
      if (oldDoc && behaviourDirty(old, oldDoc)) kept.set(old, { doc: oldDoc, baseline: st.baseline }); else { kept.delete(old); if (oldDoc) lruPut(old, oldDoc) }
      await ensureCreated(old, oldDoc);
      anc = newAnc; active = name;
      await w.openBoard(name);
      if (W().boardName !== name) { anc = oldAnc; active = oldActive; return false }
      w.setView(view);
      const k = kept.get(name); if (k) { const s = root.wbAutosave && root.wbAutosave.states.get(name); if (s) s.baseline = k.baseline; kept.delete(name) }
      const live = W().board; pending.forEach((v, key) => { const i = key.lastIndexOf("|"); if (key.slice(0, i) === name) { const c = live.cards.find(x => x.id === key.slice(i + 1)); if (c && !c.drill) c.drill = v } });
      dbg.handoffs++; dbg.active = name; dbg.depth = anc.length;
      Z().setStack(anc.map(a => ({ board: a.board, card: a.card }))); Z().acceptBoard(); Z().relod(); Z().writeHash(true);
      peekEls.forEach(pe => { pe.el.remove(); pe.host && pe.host.removeAttribute("data-peek") }); peekEls.clear(); ctxKey = ""; hold = { idx: -1, since: 0 }; dirty = true;
      setLabel(); return true;
    } finally { swapping = false; root.wbNested.swapping = false; lastKey = ""; dirty = true }
  }
  const linkOf = it => ({ board: it.board, card: it.id, fit: it.fit, cw: it.g.w, ch: it.g.h, pos: { x: it.g.x, y: it.g.y } });
  function descend(items) { const last = items[items.length - 1]; if (!last || !last.M || swapping) return Promise.resolve(false); return swap(last.child.name, last.M, anc.concat(items.map(linkOf))) }
  function ascend(n, p) { p = p || plan || computePlan(); const t = p.ai[n - 1]; if (!t) return Promise.resolve(false); return swap(t.link.board, t.M, anc.slice(0, anc.length - n)) }

  /* ---------------- tick ---------------- */
  function update() {
    const w = W(); if (!w || !w.board || swapping || !$("nest-label")) return;
    const cur = w.boardName || w.board.board;
    if (cur !== active) { /* hard cut (dropdown / popstate / hash restore): re-sync, adopt the zoom.js stack once it has settled */
      active = cur; anc = []; peekEls.forEach(pe => pe.el.remove()); peekEls.clear(); ctxKey = ""; setLabel(); dirty = true; dbg.active = cur; clearTimeout(ext);
      ext = setTimeout(() => { const zs = Z() && Z().stackFull || []; if (!swapping && (W().boardName || "") === active) { anc = zs.map(s => ({ board: s.board, card: s.card, fit: null, pos: { x: 0, y: 0 } })); dbg.depth = anc.length; setLabel(); ctxKey = ""; dirty = true } }, 150); return;
    }
    if (Z() && Z().busy) return;
    const v = w.view, key = v.k + "|" + v.x + "|" + v.y + "|" + docsTick;
    if (key === lastKey && !dirty) { if (hold.idx >= 0) tryHandoff(); return }
    lastKey = key; dirty = false;
    const p = plan = computePlan(); syncPeeks(p); syncCtx(p);
    const z = $("nest-label").querySelector(".nl-z"); if (z) z.textContent = "zoom " + Math.round(v.k * 100) + "%";
    if (p.target !== p.prev) { if (hold.idx !== p.target) hold = { idx: p.target, since: performance.now() } } else hold = { idx: -1, since: 0 };
    tryHandoff();
  }
  function tryHandoff() {
    const p = plan; if (!p || swapping || hold.idx < 0) return; const now = performance.now(), dw = coarse() ? 400 : 250;
    if (now - hold.since < dw || now - lastGesture < 250) { dirty = true; return }   /* dwell, and no handoff while a gesture is still running */
    const t = hold.idx; hold = { idx: -1, since: 0 };
    dbg.last = { t, prev: p.prev, chain: p.chain.join(">"), rects: p.rects.map(r => r && [Math.round(r.x), Math.round(r.y), Math.round(r.w), Math.round(r.h), +fFrac(r, p.vp).toFixed(3)]), k: W().view.k };
    if (t > p.prev) descend(p.path.slice(0, t - p.prev)); else if (t < p.prev) ascend(p.prev - t, p);
  }

  /* ---------------- programmatic zoom (Open / Enter / Escape / breadcrumb) ---------------- */
  function animateTo(tv, ms) {
    return new Promise(res => {
      const w = W(), a = { k: w.view.k, x: w.view.x, y: w.view.y }, t0 = performance.now(), g0 = lastGesture;
      const step = now => {
        if (lastGesture !== g0 || swapping) return res(false);
        const q = Math.min(1, (now - t0) / ms), e = q < .5 ? 2 * q * q : 1 - Math.pow(-2 * q + 2, 2) / 2, k = Math.exp(Math.log(a.k) + (Math.log(tv.k) - Math.log(a.k)) * e);
        w.setView({ k, x: a.x + (tv.x - a.x) * e, y: a.y + (tv.y - a.y) * e }); if (q < 1) requestAnimationFrame(step); else res(true);
      };
      requestAnimationFrame(step);
    });
  }
  async function enter(id) {
    const w = W(), c = w.board && w.board.cards.find(x => x.id === id); if (!c || swapping) return; const vp = vpSize(), g = geomReal(c), th = thresholds(g.w, g.h, vp);
    const fk = fFrac({ w: g.w, h: g.h }, vp), k = Math.min(KMAX, Math.max(w.view.k, th.act * 1.12 / fk));
    await animateTo({ k, x: vp.w / 2 - (g.x + g.w / 2) * k, y: vp.h / 2 - (g.y + g.h / 2) * k }, 550);
    lastGesture = 0; dirty = true;
  }
  async function exit() { return exitTo(anc.length - 1) }
  async function exitTo(i) {
    if (swapping || i < 0 || i >= anc.length) return; const n = anc.length - i, link = anc[i];
    const p = computePlan(); let ok;
    if (n === 1 && p.ai[0]) ok = await ascend(1, p); else ok = await swap(link.board, { k: 1, x: 0, y: 0 }, anc.slice(0, i));
    if (!ok) return; const w = W(), c = w.board.cards.find(x => x.id === link.card), vp = vpSize();
    if (c) { const g = geomReal(c), tv = { k: 1, x: vp.w / 2 - (g.x + g.w / 2), y: vp.h / 2 - (g.y + g.h / 2) }; if (n === 1) await animateTo(tv, 450); else w.setView(tv) }
    lastGesture = 0; dirty = true;
  }

  /* ---------------- boot ---------------- */
  function boot() {
    const st = E("style"); st.textContent = CSS; document.head.appendChild(st);
    const L = E("div"); L.id = "nest-label"; L.setAttribute("role", "status"); L.setAttribute("aria-live", "polite"); L.appendChild(E("span", "nl-t")); L.appendChild(E("span", "nl-z")); $("vp").appendChild(L);
    const ctx = E("div"); ctx.id = "nest-ctx"; $("world").insertBefore(ctx, $("world").firstChild);
    root.wbNested = { ready: true, mode: "continuous", swapping: false, enter, exit, exitTo, debug: dbg, pure, guardNew: id => { noEnter.add(id); hold = { idx: -1, since: 0 }; dirty = true },
      get guarded() { return [...noEnter] },
      get anc() { return anc.map(a => a.board) }, get active() { return active }, get plan() { return plan }, get virtual() { return [...virtual.keys()] }, get pending() { return [...pending.entries()] }, get kept() { return [...kept.keys()] }, update: () => { dirty = true; update() } };
    const g = () => { lastGesture = performance.now() };
    $("vp").addEventListener("wheel", ev => { if (swapping) { ev.preventDefault(); ev.stopImmediatePropagation(); return } noEnter.clear(); g() }, { capture: true, passive: false });
    $("vp").addEventListener("pointerdown", ev => { if (swapping) { ev.preventDefault(); ev.stopImmediatePropagation(); return } if (ev.isPrimary === false) { noEnter.clear(); g() } }, true);
    window.addEventListener("pointermove", ev => { if (ev.pointerType === "touch" && ev.buttons) g() });
    new MutationObserver(() => { dirty = true; docsTick++ }).observe($("cards"), { childList: true });
    const poll = async () => {
      /* first add inside a virtual board creates its file (parent drill is set second, in memory) */
      const w = W(); if (w && w.board && !swapping && virtual.has(active) && (w.board.cards || []).length) await ensureCreated(active, w.board);
    };
    setInterval(poll, 300); setInterval(update, 40);
    active = W().boardName || (W().board && W().board.board) || null; dbg.active = active; setLabel();
    loadNames().then(() => { dirty = true; const zs = Z() && Z().stackFull; if (zs && zs.length && !anc.length) { anc = zs.map(s => ({ board: s.board, card: s.card, fit: null, pos: { x: 0, y: 0 } })); dbg.depth = anc.length } });
  }
  (async function wait() {
    for (let i = 0; i < 200 && !(root.wb && root.wb.board && root.wbZoom); i++) await new Promise(r => setTimeout(r, 50));
    if (root.wb && root.wb.board && root.wbZoom) boot();
  })();
})();
