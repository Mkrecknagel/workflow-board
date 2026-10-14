/* workflow-board v5 autosave.js (N2). Visual edits (pos/size/style/pin, notes, group_styles) are saved automatically through
   PATCH /board/<name>/visual; behaviour edits are never autosaved (Save stays the only path for them, and it stays a whole-board PUT
   issued by core.js, never by this module). The server re-checks semantics (409 if the overlay would change the semantic hash).
   Pure exports (semanticText, isVisual, computeOverlay, applyOverlay) are usable from node. Runtime part needs window.wb. */
const LAYOUT_TOP = new Set(["saved_at", "group_styles", "integrity_hash"]);
const LAYOUT_CARD = new Set(["pos", "size", "style", "pin"]);
const VISUAL_KEYS = ["pos", "size", "style", "pin"];
function canon(v) {
  if (Array.isArray(v)) return v.map(canon);
  if (v && typeof v === "object") { const o = {}; for (const k of Object.keys(v).sort()) o[k] = canon(v[k]); return o }
  return v;
}
const same = (a, b) => JSON.stringify(canon(a === undefined ? null : a)) === JSON.stringify(canon(b === undefined ? null : b));
const clone = v => (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));

/* v5 semantic text: same rule as dirty.js / register.tsx / serve_v4_runs.py (style, pin, note cards and note-touching edges excluded). */
export function semanticText(doc) {
  const top = {};
  for (const k of Object.keys(doc || {})) if (!LAYOUT_TOP.has(k)) top[k] = doc[k];
  let noteIds = new Set();
  if (Array.isArray(top.cards)) {
    noteIds = new Set(top.cards.filter(c => c && c.type === "note").map(c => c.id));
    top.cards = top.cards.filter(c => !(c && c.type === "note")).map(c => { const o = {}; for (const k of Object.keys(c || {})) if (!LAYOUT_CARD.has(k)) o[k] = c[k]; return o });
  }
  if (Array.isArray(top.edges) && noteIds.size) {
    const ep = s => String(s).slice(0, String(s).lastIndexOf("."));
    top.edges = top.edges.filter(e => !(e && (noteIds.has(ep(e.from)) || noteIds.has(ep(e.to)))));
  }
  return JSON.stringify(canon(top));
}
export const isVisual = (before, after) => semanticText(before) === semanticText(after);

const cardMap = d => { const m = new Map(); for (const c of (d && d.cards) || []) if (c && c.id != null) m.set(c.id, c); return m };
/* The visual overlay that turns `base` into `cur`, or null when there is nothing visual to send. Behaviour differences are ignored. */
export function computeOverlay(base, cur) {
  if (!base || !cur) return null;
  const bm = cardMap(base), cm = cardMap(cur), ov = { cards: {}, notes: [], notes_removed: [] };
  for (const [id, c] of cm) {
    const b = bm.get(id);
    if (c.type === "note") { if (!b || b.type !== "note" || !same(b, c)) ov.notes.push(clone(c)); continue }
    if (!b || b.type === "note") continue; /* new non-note card = behaviour edit, not ours */
    const d = {};
    for (const k of VISUAL_KEYS) if (!same(b[k], c[k])) d[k] = c[k] === undefined ? null : clone(c[k]);
    if (Object.keys(d).length) ov.cards[id] = d;
  }
  for (const [id, b] of bm) if (b.type === "note" && !cm.has(id)) ov.notes_removed.push(id);
  if (!same(base.group_styles, cur.group_styles)) ov.group_styles = clone(cur.group_styles || {});
  const empty = !Object.keys(ov.cards).length && !ov.notes.length && !ov.notes_removed.length && !("group_styles" in ov);
  return empty ? null : ov;
}
/* Apply a sent overlay to a baseline document (in place) once the server confirmed it. */
export function applyOverlay(base, ov) {
  const bm = cardMap(base);
  for (const [id, d] of Object.entries(ov.cards || {})) { const b = bm.get(id); if (!b) continue; for (const [k, v] of Object.entries(d)) { if (v === null) delete b[k]; else b[k] = clone(v) } }
  for (const n of ov.notes || []) { const i = base.cards.findIndex(c => c.id === n.id); if (i >= 0) base.cards[i] = clone(n); else base.cards.push(clone(n)) }
  if (ov.notes_removed && ov.notes_removed.length) { const rm = new Set(ov.notes_removed); base.cards = base.cards.filter(c => !rm.has(c.id)) }
  if ("group_styles" in ov) base.group_styles = clone(ov.group_styles);
  return base;
}

/* F1/M1: three-way re-apply of MY behaviour edit (base -> mine) onto THEIR newer document. Their layout/visual state wins; my semantic
   changes (card field edits, added/removed non-note cards, added/removed edges, other top-level keys) are replayed on top. Pure. */
export function rebase(base, mine, theirs) {
  const out = clone(theirs), bm = cardMap(base), mm = cardMap(mine);
  out.cards = out.cards || []; out.edges = out.edges || [];
  for (const [id, c] of mm) {
    if (c.type === "note") continue;
    const b = bm.get(id), o = out.cards.find(x => x.id === id);
    if (!b) { if (!o) out.cards.push(clone(c)); continue }
    const keys = new Set([...Object.keys(b), ...Object.keys(c)]), d = [];
    for (const k of keys) if (!LAYOUT_CARD.has(k) && !same(b[k], c[k])) d.push(k);
    if (!d.length) continue;
    if (!o) { out.cards.push(clone(c)); continue }
    for (const k of d) { if (c[k] === undefined) delete o[k]; else o[k] = clone(c[k]) }
  }
  const gone = new Set(); for (const [id, b] of bm) if (b.type !== "note" && !mm.has(id)) gone.add(id);
  if (gone.size) out.cards = out.cards.filter(c => !gone.has(c.id));
  const ek = e => JSON.stringify(canon(e)), be = new Set((base.edges || []).map(ek)), me = new Set((mine.edges || []).map(ek));
  const removed = new Set([...be].filter(k => !me.has(k))), cur = new Set(out.edges.map(ek));
  out.edges = out.edges.filter(e => !removed.has(ek(e)));
  for (const e of mine.edges || []) { const k = ek(e); if (!be.has(k) && !cur.has(k)) out.edges.push(clone(e)) }
  const ep = x => String(x).slice(0, String(x).lastIndexOf("."));
  out.edges = out.edges.filter(e => !(gone.has(ep(e.from)) || gone.has(ep(e.to))));
  for (const k of new Set([...Object.keys(base), ...Object.keys(mine)])) {
    if (k === "cards" || k === "edges" || LAYOUT_TOP.has(k)) continue;
    if (!same(base[k], mine[k])) { if (mine[k] === undefined) delete out[k]; else out[k] = clone(mine[k]) }
  }
  return out;
}

const DEBOUNCE_MS = 800, MAX_WAIT_MS = 3000, POLL_MS = 300, BACKOFF = [500, 1500, 3000];
const RETRY_AFTER_MS = 5000;
const states = new Map(); /* boardName -> { baseline, docRef, inflight, retry } : the per-board doc Map */

if (typeof window !== "undefined" && typeof document !== "undefined") (function () {
  const $ = id => document.getElementById(id);
  const W = () => window.wb;
  let lastSig = "", firstAt = 0, lastAt = 0, warn = null, calls = 0;
  const log = [];

  function say(m, cls) { const e = $("msg"); if (!e) return; e.textContent = m; e.className = cls || ""; e.title = m }
  function retryNow() {
    const w = W(); if (!w || !w.board) return;
    const s = states.get(w.boardName || w.board.board); if (s) { s.failedSig = null; s.inflight = false }
    say("retrying autosave...", ""); flush(false);
  }
  function showWarn(on) {
    if (!warn) { warn = document.createElement("div"); warn.id = "wb-autosave-warn"; warn.setAttribute("role", "alert");
      warn.style.cssText = "position:fixed;right:12px;bottom:44px;z-index:70;font:12px system-ui,sans-serif;padding:6px 12px;border-radius:12px;background:#5a1f1f;color:#fff;border:1px solid #d96a6a;display:none";
      warn.textContent = "visual changes not saved (offline?) ";
      const rb = document.createElement("button"); rb.id = "wb-autosave-retry"; rb.type = "button"; rb.textContent = "Retry"; rb.style.cssText = "margin-left:6px;padding:2px 10px;min-height:" + (window.matchMedia("(max-width:699px)").matches ? 40 : 28) + "px;cursor:pointer";
      rb.addEventListener("click", retryNow); warn.appendChild(rb); document.body.appendChild(warn) }
    warn.style.display = on ? "block" : "none";
  }
  function setSaveLabel(behaviourDirty) {
    const b = $("bSave"); if (!b) return;
    const t = behaviourDirty ? "Save + deploy needed" : "Save";
    if (b.textContent !== t) b.textContent = t;
    b.classList.toggle("wb-behaviour-dirty", !!behaviourDirty);
    if (window.wbToolbar && typeof window.wbToolbar.setSaveLabel === "function") window.wbToolbar.setSaveLabel(!!behaviourDirty);
  }

  /* ---- F1/M2: local draft of an unsaved behaviour edit, offered back on the next open of that board ---- */
  const DKEY = n => "wb-draft:" + n;
  const dget = n => { try { const t = localStorage.getItem(DKEY(n)); return t ? JSON.parse(t) : null } catch (e) { return null } };
  const dset = (n, v) => { try { localStorage.setItem(DKEY(n), JSON.stringify(v)) } catch (e) { } };
  const ddel = n => { try { localStorage.removeItem(DKEY(n)) } catch (e) { } };
  const draftShown = new Set(); let draftSig = "";
  function writeDraft(name, s, doc) {
    const t = semanticText(doc); if (t === semanticText(s.baseline)) { if (!draftShown.has(name)) { ddel(name); draftSig = "" } return }
    if (t === draftSig) return; draftSig = t; dset(name, { name, ts: new Date().toISOString(), base: s.baseline, doc });
  }
  function banner(id, text, buttons) {
    let b = $(id); if (b) b.remove();
    b = document.createElement("div"); b.id = id; b.setAttribute("role", "alertdialog");
    b.style.cssText = "position:fixed;left:50%;top:54px;transform:translateX(-50%);z-index:80;max-width:min(560px,94vw);font:13px system-ui,sans-serif;padding:10px 14px;border-radius:10px;background:var(--panel,#222);color:var(--ink,#eee);border:1px solid #d9822b;box-shadow:0 4px 18px #0006";
    const t = document.createElement("div"); t.textContent = text; b.appendChild(t);
    const row = document.createElement("div"); row.style.cssText = "display:flex;gap:8px;margin-top:8px;flex-wrap:wrap";
    for (const [bid, label, fn] of buttons) { const x = document.createElement("button"); x.id = bid; x.type = "button"; x.textContent = label; x.style.minHeight = "40px"; x.addEventListener("click", () => { b.remove(); fn() }); row.appendChild(x) }
    b.appendChild(row); document.body.appendChild(b); return b;
  }
  function offerDraft(name, doc) {
    const d = dget(name); if (!d || !d.doc || !d.base) return;
    if (semanticText(d.doc) === semanticText(doc)) { ddel(name); return }
    draftShown.add(name);
    banner("wb-draft-offer", "Unsaved behaviour edit from an earlier session found for " + name + " (" + String(d.ts || "").slice(0, 16).replace("T", " ") + "). Restore it onto the current board?", [
      ["wb-draft-restore", "Restore my edit", () => { draftShown.delete(name); const w = W(); if (w && w.board && (w.boardName || w.board.board) === name && w.adoptBoard) { w.adoptBoard(rebase(d.base, d.doc, w.board)); say("restored your unsaved edit - review, then Save", "ok") } }],
      ["wb-draft-discard", "Discard", () => { draftShown.delete(name); ddel(name); draftSig = "" }]]);
  }
  /* ---- F1/M1: stale-tab Save ---- */
  function offerRebase(name, mine, adopt, info) {
    say("not saved: another tab or session saved " + name + " first", "err");
    banner("wb-conflict", "Another tab or session saved this board after you opened it, so your Save was refused (nothing was overwritten). Reload the latest version and re-apply your behaviour edit on top of it?", [
      ["wb-reapply", "Reload and re-apply my edit", async () => {
        const s = states.get(name); let theirs = null;
        try { const r = await fetch("/board/" + encodeURIComponent(name), { cache: "no-store" }); if (r.ok) theirs = await r.json() } catch (e) { }
        if (!theirs || !s) { say("could not reload " + name, "err"); return }
        const merged = rebase(s.baseline, mine, theirs);
        s.baseline = clone(theirs); s.baseSavedAt = theirs.saved_at || null; adopt(merged);
        say("re-applied your edit on the latest version - review, then Save", "ok");
      }],
      ["wb-conflict-keep", "Keep editing", () => { say("not saved - you are on an older version", "err") }]]);
  }
  function opened(name, doc) { states.set(name, { baseline: clone(doc), docRef: doc, inflight: false, retry: 0, baseSavedAt: (doc && doc.saved_at) || null }); setTimeout(() => offerDraft(name, doc), 0) }
  function saved(name, doc) { const s = states.get(name); if (s) { s.baseline = clone(doc); s.docRef = doc; s.baseSavedAt = doc.saved_at || s.baseSavedAt } else opened(name, doc); ddel(name); draftSig = "" }
  /* F1/M1: an explicit Save must not race a visual PATCH of the SAME tab (the PATCH moves the server's saved_at; the PUT's base would look stale).
     beginSave waits for the in-flight job, sends any pending visual overlay, and pauses autosave until endSave. */
  const wait = ms => new Promise(r => setTimeout(r, ms));
  async function beginSave(name, doc) {
    const s = states.get(name); if (!s) return;
    for (let i = 0; i < 100 && s.inflight; i++) await wait(50);
    if (doc && !s.inflight) { await flushBoard(name, doc, false); for (let i = 0; i < 100 && s.inflight; i++) await wait(50) }
    s.saving = true;
  }
  function endSave(name) { const s = states.get(name); if (s) s.saving = false }
  function setBase(name, v) { const s = states.get(name); if (s) s.baseSavedAt = v }

  /* send one overlay for one board; the job owns {name, overlay}: a board swap mid-flight cannot redirect it. */
  async function send(name, overlay, keepalive, attempt) {
    const s = states.get(name); if (!s) return false;
    s.inflight = true; calls++;
    let ok = false, status = 0, newBase = null, prevBase = null;
    try {
      const r = await fetch("/board/" + encodeURIComponent(name) + "/visual", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(overlay), keepalive: !!keepalive && JSON.stringify(overlay).length < 60000 });
      status = r.status; ok = r.ok;
      if (ok) { try { const j = await r.json(); if (j && j.saved_at) newBase = j.saved_at; if (j) prevBase = j.prev_saved_at == null ? null : j.prev_saved_at } catch (e) { } }
    } catch (e) { status = 0 }
    log.push({ name, status, attempt });
    if (ok) {
      /* F3/D1: the base only advances when nobody else wrote in between (the PATCH's previous saved_at is the base I already held). Otherwise this tab still lacks the other writer's changes and a later Save must stay stale. */
      if (newBase && (prevBase === null || !s.baseSavedAt || prevBase === s.baseSavedAt)) s.baseSavedAt = newBase;
      s.inflight = false; s.failedSig = null; s.retry = 0; applyOverlay(s.baseline, overlay); showWarn(false);
      if (W() && W().board && W().board.board === name) say("autosaved", "ok");
      return true;
    }
    if (attempt < BACKOFF.length) {
      await new Promise(r => setTimeout(r, BACKOFF[attempt]));
      return send(name, overlay, false, attempt + 1);
    }
    s.inflight = false; s.failedSig = JSON.stringify(canon(overlay)); s.failedAt = Date.now(); showWarn(true);
    if (W() && W().board && W().board.board === name) say("visual changes not saved (" + status + ")", "err");
    return false;
  }

  /* F3/D4: a deletion that is part of a behaviour edit (cards removed too) is ONE edit: the note removals wait for Save with everything else.
     A notes-only deletion is visual and still autosaves (core.js offers Undo for it). */
  function pendingOverlay(s, doc) {
    const ov = computeOverlay(s.baseline, doc); if (!ov) return null;
    if (ov.notes_removed.length && semanticText(s.baseline) !== semanticText(doc)) {
      ov.notes_removed = [];
      if (!Object.keys(ov.cards).length && !ov.notes.length && !("group_styles" in ov)) return null;
    }
    return ov;
  }

  /* compute + send the pending overlay for a board right now (called by the timer, flush events and the handoff). */
  function flushBoard(name, doc, keepalive) {
    const s = states.get(name); if (!s || !doc || s.inflight || s.saving) return Promise.resolve(false);
    const ov = pendingOverlay(s, doc); if (!ov) return Promise.resolve(false);
    return send(name, ov, keepalive, 0);
  }
  /* core.js calls this BEFORE swapping boards: pending visual writes go to the OLD board first. */
  function beforeSwap(name, doc) { if (name && doc) return flushBoard(name, doc, true); return Promise.resolve(false) }
  function flush(keepalive) { const w = W(); if (!w || !w.board) return Promise.resolve(false); return flushBoard(w.boardName || w.board.board, w.board, keepalive) }

  function tick() {
    const w = W(); if (!w || !w.board) return;
    const name = w.boardName || w.board.board, s = states.get(name);
    if (!s) return;
    if (s.docRef !== w.board) s.docRef = w.board;
    setSaveLabel(semanticText(s.baseline) !== semanticText(w.board));
    writeDraft(name, s, w.board);
    if (s.inflight || s.saving) return;
    const ov = pendingOverlay(s, w.board);
    if (!ov) { lastSig = ""; firstAt = 0; s.failedSig = null; return }
    if (s.failedSig && s.failedSig === JSON.stringify(canon(ov))) { if (Date.now() - (s.failedAt || 0) >= RETRY_AFTER_MS) { s.failedAt = Date.now(); retryNow() } return }  /* F3/D2: failed edits re-send by themselves (periodic + on reconnect), Retry stays as the manual control */
    const sig = name + JSON.stringify(ov), now = Date.now();
    if (sig !== lastSig) { lastSig = sig; lastAt = now; if (!firstAt) firstAt = now }
    if (now - lastAt >= DEBOUNCE_MS || now - firstAt >= MAX_WAIT_MS) { firstAt = 0; lastSig = ""; flush(false) }
  }

  window.addEventListener("online", () => { const w = W(); const s = w && w.board && states.get(w.boardName || w.board.board); if (s && s.failedSig) retryNow() });
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flush(true) });
  window.addEventListener("pagehide", () => { const w = W(); if (w && w.board) { const n = w.boardName || w.board.board, s = states.get(n); if (s) writeDraft(n, s, w.board) } flush(true) });
  window.onbeforeunload = ev => {  /* F3: assigned handler (not only a listener) so the unsaved-edit guard is always present and inspectable */
    const w = W(); if (!w || !w.board) return; const n = w.boardName || w.board.board, s = states.get(n); if (!s) return;
    if (semanticText(s.baseline) !== semanticText(w.board)) { writeDraft(n, s, w.board); ev.preventDefault(); ev.returnValue = "You have an unsaved behaviour edit (a local draft is kept)."; return ev.returnValue }
  };
  window.wbAutosave = { opened, saved, setBase, beginSave, endSave, offerRebase, rebase, retryNow, beforeSwap, flush, tick, states, semanticText, isVisual, computeOverlay, applyOverlay,
    get calls() { return calls }, get log() { return log }, DEBOUNCE_MS, MAX_WAIT_MS, BACKOFF };
  /* a board may already be open when this module loads (dynamic import order is not guaranteed) */
  const w0 = W(); if (w0 && w0.board) opened(w0.boardName || w0.board.board, w0.board);
  setInterval(tick, POLL_MS);
})();
