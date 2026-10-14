"use strict";
/* workflow-board v3 semantic zoom (module, B2). Hook-free: reads window.wb (board, view, groupRect, sel) and drives the board
   switch through wb.openBoard if core exposes it, else through the #picker select. Owns:
   - level of detail (LOD): Level 1 collapses every group / boundary to a silhouette; hysteresis 0.40 down / 0.55 up
   - drill into a card's `drill` board, return to the parent, breadcrumb HUD, hash route, pushState / popstate
   - touch (pointer: coarse): never auto-loads; drillable cards show [Open >] (data-open) instead
   Every level change and every drill / return fires only after the view has been still for SETTLE ms (A1 whiplash).
   Public: window.wbZoom; DOM event "wb:level" on document, detail {lod, sem, k, board}. */
const SETTLE = 250, HASH_SETTLE = 500;
const T = { lo: 0.40, hi: 0.55, drill: 1.6, back: 0.25, fill: 0.6, maxDepth: 4 };
const CW = 192, CHIP_H = 34;
const $ = id => document.getElementById(id);
const E = (t, c, h) => { const e = document.createElement(t); if (c) e.className = c; if (h != null) e.textContent = h; return e };
const W = () => window.wb;
const V3 = ["boundary", "component", "datastore", "external", "person"];
/* v5 continuous zoom: nested.js (loaded by the /v4/modules loader) owns peeks + active-board handoff. `WB_ZOOM_MODE = "hard"` (window flag,
   ?zoom=hard, or localStorage) keeps the v3/v4 hard-cut drill exactly as before; cont() is false until nested.js reports ready. */
const hardMode = () => { try { return window.WB_ZOOM_MODE === "hard" || /[?&]zoom=hard\b/.test(location.search) || localStorage.getItem("WB_ZOOM_MODE") === "hard" } catch (_) { return window.WB_ZOOM_MODE === "hard" } };
const cont = () => !hardMode() && !!(window.wbNested && window.wbNested.ready && window.wbNestedPure);
const mq = window.matchMedia ? window.matchMedia("(pointer: coarse)") : { matches: false };
const coarse = () => !!mq.matches;

let pendingName = null, lod = 2, stack = [], names = null, navBusy = false, changed = false, lastChange = 0, lastKey = "", lastBoard = null;
let lvl, baseKey = "", baseBoard = null, settledK = null, userZoomed = false, hashDirty = false, applyQ = 0;

const CSS = `
#world{transition:opacity .2s ease}
#vp[data-lod="1"] .card{display:none!important;visibility:hidden!important;pointer-events:none!important}
#vp[data-lod="1"] #edges path.e{display:none}
#zm-lod{position:absolute;left:0;top:0;width:1px;height:1px;overflow:visible;pointer-events:none;display:none}
#vp[data-lod="1"] #zm-lod{display:block}
#zm-lod svg{position:absolute;left:0;top:0;width:1px;height:1px;overflow:visible}
#zm-lod path{fill:none;stroke:var(--edge);stroke-width:2;vector-effect:non-scaling-stroke}
#zm-lod text{fill:var(--mut);font-size:calc(12px / var(--k,1));paint-order:stroke;stroke:var(--bg);stroke-width:calc(3px / var(--k,1))}
.grp.lod1{border:2px solid var(--gc,var(--edge));background:color-mix(in srgb,var(--gc,#7b8499) 20%,var(--card))}
.grp.lod1>b{display:none}
.grp .sil{display:none}
.grp.lod1 .sil,.zm-sil .sil{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;text-align:center;padding:4px;font-weight:600;color:var(--ink);font-size:calc(15px / var(--k,1));line-height:1.25;overflow:hidden}
.zm-sil{position:absolute;border:2px solid var(--edge);border-radius:12px;background:color-mix(in srgb,var(--edge) 16%,var(--card))}
.zm-sil.chip{border-radius:8px;border-width:1.5px;background:var(--card)}
.zm-sil.bnd{border-style:dashed}.zm-sil small{display:block;font-weight:400;color:var(--mut);font-size:.8em}
.zm-d{position:absolute;right:6px;bottom:-11px;z-index:3;font-size:11px;line-height:1;padding:3px 8px;border-radius:10px;border:1px solid var(--sel);background:var(--panel);color:var(--sel);cursor:pointer;white-space:nowrap}
.zm-d.miss{border-color:#e5484d;color:#e5484d;cursor:not-allowed}.zm-d.up{border-style:dashed}
#zm-crumb{position:absolute;left:8px;top:8px;z-index:5;display:flex;flex-wrap:wrap;gap:4px;align-items:center;max-width:calc(100% - 16px);padding:3px 8px;border:1px solid var(--line);border-radius:8px;background:var(--panel);box-shadow:0 1px 6px #0003;font-size:12px}
#zm-crumb button{border:0;background:none;padding:0 2px;color:var(--sel);cursor:pointer;font-size:12px}#zm-crumb i{font-style:normal;color:var(--mut)}
#vp[data-nl="0"] #zm-lod .sil small{display:none}
@media (max-width:699px){#zm-crumb button,.zm-d{min-height:28px}}
`;

/* ---------- view helpers ---------- */
function clampK(k) { return Math.max(0.02, Math.min(2.5, k)) }
function vpSize() { const r = $("vp").getBoundingClientRect(); return { w: r.width || 1, h: r.height || 1 } }
/* same effect as core applyView (transform + grid background); used when core does not expose setView */
function setView(v) {
  const w = W(); if (!w) return;
  if (typeof w.setView === "function" && !w.setView.__zm) return w.setView(v);
  const view = w.view; view.k = clampK(v.k); view.x = v.x; view.y = v.y;
  $("world").style.transform = "translate(" + view.x + "px," + view.y + "px) scale(" + view.k + ")";
  const n = Math.max(1, Math.ceil(3 / (w.GRID * view.k))), g = w.GRID * n * view.k;
  $("vp").style.backgroundSize = g + "px " + g + "px"; $("vp").style.backgroundPosition = view.x + "px " + view.y + "px";
  $("world").style.setProperty("--k", view.k);
}
setView.__zm = true;
function centerOn(id, k) {
  const w = W(), c = w.board.cards.find(x => x.id === id); if (!c) return false;
  const s = vpSize(), cw = (c.size && c.size.w) || CW, ch = (c.size && c.size.h) || 90;
  setView({ k, x: s.w / 2 - (c.pos.x + cw / 2) * k, y: s.h / 2 - (c.pos.y + ch / 2) * k }); return true;
}

/* ---------- hash route ---------- */
function curName() { if (pendingName) return pendingName; const w = W(); return (w && w.board && w.board.board) || ($("picker") && $("picker").value) || "" }
function hashStr() {
  const w = W(), v = w.view, r = n => String(Math.round(n * 1000) / 1000);
  return "#b=" + encodeURIComponent(curName()) + "&k=" + r(v.k) + "&x=" + r(v.x) + "&y=" + r(v.y) + (stack.length ? "&p=" + stack.map(s => encodeURIComponent(s.board) + "@" + encodeURIComponent(s.card || "")).join(",") : "");
}
function parseHash() {
  const p = new URLSearchParams((location.hash || "").replace(/^#/, "")), o = {};
  if (p.get("b")) o.b = p.get("b");
  const k = parseFloat(p.get("k")), x = parseFloat(p.get("x")), y = parseFloat(p.get("y"));
  if ([k, x, y].every(Number.isFinite)) o.view = { k, x, y };
  if (p.get("p")) o.p = p.get("p").split(",").filter(Boolean).map(t => { const i = t.indexOf("@"); return { board: decodeURIComponent(i < 0 ? t : t.slice(0, i)), card: i < 0 ? null : (decodeURIComponent(t.slice(i + 1)) || null) } });
  return o;
}
function writeHash(push) {
  try { const h = hashStr(); if (h === location.hash && !push) return; (push ? history.pushState : history.replaceState).call(history, { board: curName() }, "", h) } catch (_) { }
}

/* ---------- board loading ---------- */
async function boardNames(force) {
  if (names && !force) return names;
  try { const r = await fetch("/boards"), j = await r.json(); names = (j && j.boards) || [] } catch (_) { names = names || [] }
  return names;
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function loadBoard(n) {
  const w = W();
  if (typeof w.openBoard === "function" && !w.openBoard.__zm) { await w.openBoard(n); return curName() === n }
  const pick = $("picker"); if (![...pick.options].some(o => o.value === n)) return false;
  const msg = $("msg"); msg.textContent = ""; pick.value = n; pick.dispatchEvent(new Event("change"));
  for (let i = 0; i < 80; i++) { await sleep(50); if (msg.textContent === "loaded " + n) { await sleep(30); return true } if (/^load failed/.test(msg.textContent)) return false }
  return false;
}
async function navigate(n, o) {
  o = o || {}; if (navBusy) return false; navBusy = true;
  $("world").style.opacity = ".15";
  const oldStack = stack, oldName = curName(); let ok = false;
  /* crumb + hash are written BEFORE the board switches so anything that observes the new board already sees them */
  if (o.stack) stack = o.stack; pendingName = n; renderCrumb(); writeHash(o.push !== false);
  try {
    ok = await loadBoard(n); pendingName = null;
    if (!ok) { stack = oldStack; renderCrumb(); const m = $("msg"); m.textContent = "could not open board " + n; m.className = "err"; return false }
    if (o.view) setView(o.view); else if (o.center && centerOn(o.center, 1)) { } else { const f = W().fitView; if (f && !f.__zm) f(); else $("bFit").click() }
    writeHash(false); renderCrumb();
    return true;
  } finally {
    pendingName = null; if (!ok) writeHash(false); $("world").style.opacity = ""; navBusy = false; lastKey = ""; changed = true; lastChange = performance.now(); userZoomed = false; lastBoard = W().board; lod = 2; rebase(); queueApply(); emitLevel() }
}
function drillTargets(c) { return c && c.drill ? c.drill : null }
async function open(card) {
  if (cont()) return window.wbNested.enter(card.id);
  const t = card.drill; if (!t || navBusy) return;
  if (t === curName() || stack.some(s => s.board === t)) { return upTo(stack.findIndex(s => s.board === t)) }
  if (stack.length >= T.maxDepth) { const m = $("msg"); m.textContent = "breadcrumb depth limit (" + T.maxDepth + ")"; m.className = "err"; return }
  if (!(await boardNames()).includes(t)) return;
  return navigate(t, { stack: stack.concat([{ board: curName(), card: card.id }]), push: true });
}
/* index -1 means "the current board is the target" -> nothing to do */
function upTo(i) {
  if (cont()) return i < 0 || i >= stack.length ? undefined : window.wbNested.exitTo(i);
  if (i < 0 || i >= stack.length || navBusy) return;
  const dst = stack[i]; return navigate(dst.board, { stack: stack.slice(0, i), center: dst.card, push: true });
}
const up = () => upTo(stack.length - 1);

/* ---------- LOD / drill decision (runs only after the view has settled) ---------- */
function pickDrill() {
  const w = W(), v = w.view, s = vpSize(); let best = null, bd = 1e18;
  w.board.cards.forEach(c => {
    if (!c.drill) return;
    const el = document.querySelector('.card[data-id="' + c.id + '"]');
    const cw = ((c.size && c.size.w) || CW) * v.k, ch = ((c.size && c.size.h) || (el && el.offsetHeight) || 90) * v.k;
    const x0 = c.pos.x * v.k + v.x, y0 = c.pos.y * v.k + v.y;
    const vw = Math.max(0, Math.min(x0 + cw, s.w) - Math.max(x0, 0)), vh = Math.max(0, Math.min(y0 + ch, s.h) - Math.max(y0, 0));
    if (vw * vh < T.fill * cw * ch) return;
    const d = Math.hypot(x0 + cw / 2 - s.w / 2, y0 + ch / 2 - s.h / 2); if (d < bd) { bd = d; best = c }
  });
  return best;
}
function semantic() { const b = W() && W().board; return lod === 1 ? 1 : (b && b.cards.some(c => V3.includes(c.type)) ? 2 : 3) }
function emitLevel() { document.dispatchEvent(new CustomEvent("wb:level", { detail: { lod, sem: semantic(), k: W().view.k, board: curName() } })) }
const vkey = () => { const v = W().view; return v.k + "|" + v.x + "|" + v.y };
/* the as-loaded (fit / restored) view is the baseline: no automatic level change, drill or return until the view moves, and a level
   change needs the zoom itself to have changed, so Fit on a big board and plain panning never collapse or drill by surprise */
function rebase() { const w = W(); if (!w || !w.board) return; baseBoard = w.board; baseKey = vkey(); settledK = w.view.k }
function settle() {
  const w = W(), k = w.view.k;
  if (vkey() === baseKey) return;
  const kChanged = settledK === null || Math.abs(k - settledK) > 1e-6;
  if (cont()) { /* continuous: fixed deterministic levels (lodFor), no automatic drill / return here (nested.js hands the active board off) */
    if (!navBusy && kChanged) { lvl = window.wbNestedPure.lodFor(k, lvl); const was = lod; lod = lvl <= 1 ? 1 : 2; if (lod !== was) { applyAll(); emitLevel() } else $("vp").dataset.nl = String(lvl) }
    settledK = k; return;
  }
  if (!navBusy && kChanged) {
    const was = lod; if (lod === 2 && k < T.lo) lod = 1; else if (lod === 1 && k > T.hi) lod = 2;
    if (lod !== was) { applyAll(); emitLevel() }
  }
  const prev = settledK; settledK = k;
  if (navBusy || coarse()) return;
  if (stack.length && k < T.back && (userZoomed || (prev != null && prev >= T.back))) return void up();
  if (lod === 2 && k >= T.drill) { const c = pickDrill(); if (c) open(c) }
}

/* ---------- DOM decoration ---------- */
function groupCount(g) { return W().board.cards.filter(c => c.group === g).length }
function decorateGroups() {
  document.querySelectorAll("#groups .grp").forEach(d => {
    const g = d.dataset.group; d.classList.toggle("lod1", lod === 1);
    let s = d.querySelector(":scope>.sil"); if (!s) { s = E("span", "sil"); d.appendChild(s) }
    s.textContent = g + " · " + groupCount(g);
  });
}
function decorateCards() {
  const w = W(); if (!w || !w.board) return; const cn = curName();
  const have = names || [];
  w.board.cards.forEach(c => {
    const el = document.querySelector('.card[data-id="' + c.id + '"]'); if (!el) return;
    el.querySelectorAll(":scope>.zm-d").forEach(x => x.remove());
    if (!c.drill) return;
    let b;
    if (c.drill === cn || stack.some(s => s.board === c.drill)) { b = E("button", "zm-d up", "↑ up to " + c.drill); b.dataset.up = c.drill; b.onclick = ev => { ev.stopPropagation(); open(c) } }
    else if (names && !have.includes(c.drill)) { b = E("span", "zm-d miss", "missing board"); b.title = "drill target not found: " + c.drill; b.dataset.missing = c.drill }
    else if (coarse()) { b = E("button", "zm-d", "Open ▸"); b.dataset.open = c.drill; b.onclick = ev => { ev.stopPropagation(); open(c) } }
    else { b = E("button", "zm-d", "Enter ▸"); b.dataset.enter = c.drill; b.title = "Double-click, Enter, or zoom in to drill"; b.onclick = ev => { ev.stopPropagation(); open(c) } }
    ["pointerdown", "dblclick"].forEach(t => b.addEventListener(t, ev => ev.stopPropagation()));
    el.appendChild(b);
  });
}
function renderCrumb() {
  const vp = $("vp"); let c = $("zm-crumb"); if (!c) { c = E("div"); c.id = "zm-crumb"; vp.appendChild(c) }
  c.innerHTML = "";
  stack.forEach((s, i) => { const b = E("button", null, s.board); b.dataset.crumb = s.board; b.onclick = () => upTo(i); c.appendChild(b); c.appendChild(E("i", null, "›")) });
  const cur = E("b", null, curName()); cur.dataset.current = "1"; c.appendChild(cur);
}

/* Level 1: boundaries collapse into silhouettes with a child count, loose cards become chips, edges aggregate per pair of owners. */
function lodModel() {
  const w = W(), b = w.board, by = {}; b.cards.forEach(c => by[c.id] = c);
  const top = c => { let x = c, g = 0; while (x.boundary && by[x.boundary] && g++ < 8) x = by[x.boundary]; return x };
  const members = {}; // boundary root id -> descendant non-boundary cards
  const rects = {}, label = {}, owner = {};
  b.cards.forEach(c => {
    if (c.group) { owner[c.id] = "g:" + c.group; return }
    const r = top(c);
    if (r.type === "boundary") { owner[c.id] = "b:" + r.id; if (c !== r) (members[r.id] = members[r.id] || []).push(c) }
    else owner[c.id] = "c:" + r.id;
  });
  const sil = [];
  b.cards.forEach(c => {
    if (c.group || c.boundary) return;
    if (c.type === "boundary") {
      const ds = members[c.id] || [], xs = [c.pos.x], ys = [c.pos.y], x1 = [c.pos.x + ((c.size && c.size.w) || CW)], y1 = [c.pos.y + ((c.size && c.size.h) || 90)];
      b.cards.forEach(d => { if (d !== c && top(d) === c) { xs.push(d.pos.x); ys.push(d.pos.y); x1.push(d.pos.x + ((d.size && d.size.w) || CW)); y1.push(d.pos.y + ((d.size && d.size.h) || 90)) } });
      const pad = (c.size ? 0 : 24), r = { x: Math.min(...xs) - pad, y: Math.min(...ys) - pad, w: Math.max(...x1) - Math.min(...xs) + 2 * pad, h: Math.max(...y1) - Math.min(...ys) + 2 * pad };
      const n = ds.filter(d => d.type !== "boundary").length; rects["b:" + c.id] = r;
      sil.push({ key: "b:" + c.id, r, cls: "bnd", title: c.label, sub: ((c.ref && c.ref.kind) || "boundary") + " · " + n })
    } else { const r = { x: c.pos.x, y: c.pos.y, w: CW, h: CHIP_H }; rects["c:" + c.id] = r; sil.push({ key: "c:" + c.id, r, cls: "chip", title: c.label, sub: c.type }) }
  });
  const pairs = {};
  b.edges.forEach(e => {
    const a = by[e.from.slice(0, e.from.lastIndexOf("."))], z = by[e.to.slice(0, e.to.lastIndexOf("."))]; if (!a || !z) return;
    const oa = owner[a.id], oz = owner[z.id]; if (!oa || !oz || oa === oz) return;
    const k = oa + ">" + oz; pairs[k] = pairs[k] || { a: oa, z: oz, n: 0 }; pairs[k].n++;
  });
  const rectOf = k => k.startsWith("g:") ? w.groupRect(k.slice(2)) : rects[k];
  return { sil, pairs: Object.values(pairs), rectOf };
}
function edgePt(r, t) {
  const cx = r.x + r.w / 2, cy = r.y + r.h / 2, dx = t.x - cx, dy = t.y - cy; if (!dx && !dy) return { x: cx, y: cy };
  const s = Math.min(dx ? (r.w / 2) / Math.abs(dx) : 1e9, dy ? (r.h / 2) / Math.abs(dy) : 1e9, 1e9); return { x: cx + dx * Math.min(s, 1), y: cy + dy * Math.min(s, 1) };
}
function buildLod() {
  let L = $("zm-lod"); if (!L) { L = E("div"); L.id = "zm-lod"; $("world").insertBefore(L, $("edges")) }
  L.innerHTML = ""; if (lod !== 1 || !W().board) return;
  const m = lodModel(), NS = "http://www.w3.org/2000/svg", svg = document.createElementNS(NS, "svg");
  m.pairs.forEach(p => {
    const A = m.rectOf(p.a), Z = m.rectOf(p.z); if (!A || !Z) return;
    const ca = { x: A.x + A.w / 2, y: A.y + A.h / 2 }, cz = { x: Z.x + Z.w / 2, y: Z.y + Z.h / 2 }, s = edgePt(A, cz), t = edgePt(Z, ca);
    const path = document.createElementNS(NS, "path"); path.setAttribute("d", "M" + s.x + "," + s.y + " L" + t.x + "," + t.y); path.style.strokeWidth = Math.min(6, 1.5 + Math.log2(p.n + 1)) + "px"; path.dataset.pair = p.a + ">" + p.z; svg.appendChild(path);
    if (p.n > 1) { const tx = document.createElementNS(NS, "text"); tx.setAttribute("x", (s.x + t.x) / 2); tx.setAttribute("y", (s.y + t.y) / 2); tx.setAttribute("text-anchor", "middle"); tx.textContent = p.n; svg.appendChild(tx) }
  });
  L.appendChild(svg);
  m.sil.forEach(s => {
    const d = E("div", "zm-sil " + s.cls); d.dataset.sil = s.key; d.style.cssText = "left:" + s.r.x + "px;top:" + s.r.y + "px;width:" + s.r.w + "px;height:" + s.r.h + "px";
    const t = E("div", "sil"); const inner = E("div"); inner.appendChild(document.createTextNode(s.title)); const sm = E("small", null, s.sub); inner.appendChild(sm); t.appendChild(inner); d.appendChild(t); L.appendChild(d);
  });
}
function applyAll() {
  const w = W(); if (!w || !w.board) return;
  $("vp").dataset.lod = String(lod); if (lvl != null) $("vp").dataset.nl = String(lvl); $("vp").dataset.level = String(semantic()); $("world").style.setProperty("--k", w.view.k);
  decorateGroups(); decorateCards(); buildLod();
}
function queueApply() { if (applyQ) return; applyQ = requestAnimationFrame(() => { applyQ = 0; if (W() && W().board && W().board !== baseBoard) rebase(); applyAll() }) }

/* ---------- loop ---------- */
function tick() {
  const w = W(); if (!w || !w.board) return;
  if (w.board !== lastBoard) {
    const nest = !!(window.wbNested && window.wbNested.swapping), user = !navBusy && !nest; lastBoard = w.board;
    if (user) { stack = []; boardNames(true).then(() => { renderCrumb(); queueApply() }) }
    if (!nest) lod = 2; changed = true; lastChange = performance.now(); lastKey = ""; userZoomed = false; rebase();
    renderCrumb(); queueApply(); emitLevel(); if (user) writeHash(true);
    return;
  }
  const v = w.view, key = v.k + "|" + v.x + "|" + v.y;
  if (key !== lastKey) { lastKey = key; lastChange = performance.now(); changed = true; hashDirty = true; $("world").style.setProperty("--k", v.k) }
  const idle = performance.now() - lastChange;
  if (changed && idle >= SETTLE) { changed = false; settle() }
  if (hashDirty && idle >= HASH_SETTLE) { hashDirty = false; writeHash(false) }
}

/* ---------- boot ---------- */
async function restoreFromHash() {
  const h = parseHash(); if (!h.b) return;
  const st = h.p || [];
  if (h.b !== curName()) await navigate(h.b, { stack: st, view: h.view, push: false });
  else { stack = st; if (h.view) setView(h.view); renderCrumb() }
}
async function boot() {
  const st = E("style"); st.textContent = CSS; document.head.appendChild(st);
  const w0 = W(); if (w0) { if (!w0.fitView) { w0.fitView = () => $("bFit").click(); w0.fitView.__zm = true } if (!w0.openBoard) w0.openBoard = Object.assign(async n => loadBoard(n), { __zm: true }); if (!w0.setView) w0.setView = setView }
  for (let i = 0; i < 160 && !(W() && W().board && /^loaded/.test($("msg").textContent)); i++) await sleep(50);
  if (!W() || !W().board) return;
  await boardNames(); lastBoard = W().board; lod = 2; rebase();
  renderCrumb(); applyAll(); emitLevel();
  try { await restoreFromHash() } catch (_) { }
  writeHash(false);
  setInterval(tick, 50);
}
const obs = new MutationObserver(queueApply);
["cards", "groups"].forEach(id => { const e = $(id); if (e) obs.observe(e, { childList: true }) });
$("vp").addEventListener("wheel", () => { userZoomed = true }, { passive: true, capture: true });
const pinchMark = () => { userZoomed = true }; $("vp").addEventListener("pointerdown", ev => { if (ev.isPrimary === false) pinchMark() }, true);
$("vp").addEventListener("dblclick", ev => { if (coarse()) return; const el = ev.target.closest && ev.target.closest(".card"); if (!el) return; const c = W().board.cards.find(x => x.id === el.dataset.id); if (c && c.drill) { ev.preventDefault(); open(c) } });
window.addEventListener("keydown", ev => {
  const t = ev.target && ev.target.tagName; if (t === "INPUT" || t === "TEXTAREA" || t === "SELECT") return;
  const w = W(); if (!w || !w.board) return;
  if (ev.key === "Enter" && w.sel && w.sel.size === 1) { const c = w.board.cards.find(x => x.id === [...w.sel][0]); if (c && c.drill) { ev.preventDefault(); open(c) } }
  else if (ev.key === "Escape" && stack.length) { ev.preventDefault(); up() }
});
window.addEventListener("popstate", () => { if (navBusy) return; const h = parseHash(); if (h.b) navigate(h.b, { stack: h.p || [], view: h.view, push: false }) });
if (mq.addEventListener) mq.addEventListener("change", queueApply);
window.wbZoom = {
  T, SETTLE, setView, centerOn, navigate, open: id => { const c = W().board.cards.find(x => x.id === id); return c && open(c) }, up,
  setStack: a => { stack = (a || []).map(s => ({ board: s.board, card: s.card || null })); renderCrumb() },
  get stackFull() { return stack.map(s => ({ board: s.board, card: s.card })) }, writeHash, hardMode, cont,
  /* nested.js tells us a board swap it performed is not a user board switch; re-derive the level from the live view */
  acceptBoard: () => { lastBoard = W().board; lastKey = ""; rebase(); renderCrumb(); queueApply() },
  relod: () => { const k = W().view.k; lvl = window.wbNestedPure ? window.wbNestedPure.lodFor(k) : lvl; lod = lvl <= 1 ? 1 : 2; settledK = k; applyAll(); emitLevel(); rebase() },
  get level() { return lod }, get semantic() { return semantic() }, get stack() { return stack.map(s => s.board) }, get busy() { return navBusy }, coarse
};
boot();
