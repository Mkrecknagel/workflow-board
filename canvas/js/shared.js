"use strict";
/* workflow-board shared steps UI (module). Self-installing; reads window.wb only, no core hooks (core.js/zoom.js/lenses.js/
   tickets.js/inspector.js untouched). Cards are decorated by a MutationObserver on #cards; the inspector section is injected
   by a MutationObserver on #inspBody. All server text is rendered with textContent only.
   Server: /api/shared/{library,drift,replace,edit-preview,edit,unlink}; [DEDUP-PROPOSAL] tickets from /tickets/<board>.
   Public: window.wbShared {refresh, dupMembers, replaceSelected, openEdit, unlink, lens}. */
const $ = id => document.getElementById(id);
const el = (t, c, x) => { const e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e };
const W = () => window.wb;
const GLYPH = "\u{1F517}"; /* link */
const DRIFT = { synced: ["=", "synced with SOP"], drifted: ["!", "drifted from SOP"], proposed: ["~", "SOP change proposed"], missing: ["?", "SOP heading not found"] };
const TAG = "[DEDUP-PROPOSAL]";

let dupOn = false, dupSet = {}, drift = {}, lib = [], boardKey = null, busy = false, selSig = "";

(function css() {
  const s = document.createElement("style");
  s.textContent =
    ".card.shd-dup{outline:3px solid #CC79A7;outline-offset:2px}.shd-dupchip{position:absolute;left:4px;bottom:-10px;z-index:3;font:600 10px/1 system-ui,sans-serif;padding:2px 6px;border-radius:9px;background:#CC79A7;color:#fff;pointer-events:none;white-space:nowrap}" +
    ".shd-badge{position:absolute;left:4px;top:-11px;z-index:3;display:flex;gap:3px;pointer-events:none;white-space:nowrap}.shd-badge span{font:600 10px/1 system-ui,sans-serif;padding:2px 6px;border-radius:9px;border:1px solid var(--line);background:var(--panel);color:var(--ink)}" +
    ".shd-badge .shd-d{border-color:#0072B2;color:#0072B2}.shd-badge .shd-d.drifted{border-color:#D55E00;color:#D55E00}.shd-badge .shd-d.proposed{border-color:#E69F00;color:#E69F00}.shd-badge .shd-d.missing{border-style:dashed}" +
    ".card.shd-shared{box-shadow:inset 0 0 0 1px #0072B2}" +
    "#bDup[aria-pressed=true]{background:var(--sel);color:#fff;border-color:var(--sel)}#bShdReplace{display:none}#bShdReplace.on{display:inline-block}" +
    "#shdSec{border:1px solid #0072B2;border-radius:6px;padding:6px 8px;margin:8px 0}#shdSec .shdhd{font-weight:600;color:#0072B2;margin-bottom:4px}#shdSec textarea{min-height:56px;width:100%;box-sizing:border-box}#shdSec input.shdk{width:100%;box-sizing:border-box}#shdSec .shda{display:flex;gap:6px;margin-top:6px;flex-wrap:wrap}" +
    "#shdModal{position:fixed;inset:0;z-index:30;background:#0007;display:flex;align-items:center;justify-content:center}#shdModal .shdbox{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:10px;max-width:min(920px,96vw);max-height:90vh;overflow:auto;padding:12px 14px;box-shadow:0 8px 32px #0006}" +
    "#shdModal h3{margin:0 0 6px}#shdModal .shdlanes label{display:block;padding:2px 0}#shdModal .shdcols{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:8px 0}#shdModal .shdcol{border:1px solid var(--line);border-radius:6px;padding:6px;white-space:pre-wrap;word-break:break-word;font-size:12px}#shdModal pre{white-space:pre-wrap;word-break:break-word;font-size:11px;max-height:160px;overflow:auto;border:1px solid var(--line);border-radius:6px;padding:6px;margin:6px 0}#shdModal .shda{display:flex;gap:8px;justify-content:flex-end;margin-top:8px}";
  document.head.appendChild(s);
})();

async function call(method, path, body) {
  try {
    const r = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
    let j = null; try { j = await r.json() } catch (_) { }
    return { ok: r.ok, status: r.status, j };
  } catch (e) { return { ok: false, status: 0, j: null } }
}
const bname = () => ($("picker") && $("picker").value) || (W() && W().board && W().board.board) || "";
const say = (m, cls) => { const e = $("msg"); if (!e) return; e.textContent = m; e.className = cls || ""; e.title = m };
const cardOf = id => { const b = W() && W().board; return b ? b.cards.find(c => c.id === id) : null };
const entryOf = sid => lib.find(e => e.id === sid) || ((W() && W().board && W().board.shared_library) || []).find(e => e.id === sid);
const places = sid => { const b = W() && W().board; return b ? b.cards.filter(c => c.shared && c.shared.step_id === sid).length : 0 };

/* ---------- unsaved-layout guard ----------
   Every shared op (replace / edit / unlink) is applied by the server to the STORED board and the page then reloads it, so unsaved
   layout would be lost. Before any op: if the board differs from its last loaded/saved copy, press the Save button (same path as a
   user click); only run the op when that save succeeds. A failed save (422) aborts the op and shows the reason.
   Baselines come from observing /board/<name> GET (load) and PUT (save) traffic; saved_at is ignored. */
const baseSig = {}; let lastPut = { seq: 0, ok: true, status: 0, error: "" };
const sigOf = b => { const o = Object.assign({}, b); delete o.saved_at; return JSON.stringify(o) };
(function wrapFetch() {
  const orig = window.fetch.bind(window);
  window.fetch = async function (input, init) {
    const res = await orig(input, init);
    try {
      const u = new URL(typeof input === "string" ? input : input.url, location.href), m = /^\/board\/([^/]+)$/.exec(u.pathname);
      if (m) {
        const nm = decodeURIComponent(m[1]), method = String((init && init.method) || (typeof input !== "string" && input.method) || "GET").toUpperCase();
        if (method === "GET" && res.ok) res.clone().json().then(j => { baseSig[nm] = sigOf(j) }).catch(() => { });
        else if (method === "PUT") {
          if (res.ok && init && typeof init.body === "string") baseSig[nm] = sigOf(JSON.parse(init.body));
          res.clone().json().catch(() => null).then(j => { lastPut = { seq: lastPut.seq + 1, ok: res.ok, status: res.status, error: (j && j.error) || "" } });
        }
      }
    } catch (_) { }
    return res;
  };
})();
async function isDirty() {
  const w = W(), n = bname(); if (!w || !w.board || !n) return false;
  if (baseSig[n] === undefined) {   /* load happened before this module's fetch hook existed: compare against the stored copy */
    const r = await call("GET", "/board/" + encodeURIComponent(n)); if (!r.ok || !r.j) return false; baseSig[n] = sigOf(r.j);
  }
  return sigOf(w.board) !== baseSig[n];
}
/* resolves true when the board is clean or was saved; false (op must abort) when the save failed */
async function saveFirst(what) {
  if (!await isDirty()) return true;
  const btn = $("bSave"), seq = lastPut.seq; if (!btn) return true;
  say("saving layout before " + what + "...");
  btn.click();
  const t0 = Date.now(); while (lastPut.seq === seq && Date.now() - t0 < 8000) await new Promise(r => setTimeout(r, 25));
  if (lastPut.seq === seq || !lastPut.ok) {
    const why = lastPut.seq === seq ? "no response" : (lastPut.error || ("HTTP " + lastPut.status));
    say(what + " cancelled: the board has unsaved changes and saving failed (" + why + "). Nothing was changed.", "err"); return false;
  }
  return true;
}

/* ---------- data ---------- */
async function loadTickets() {
  dupSet = {}; const n = bname(); if (!n) return;
  const r = await call("GET", "/tickets/" + encodeURIComponent(n)); if (!r.ok || !r.j) return;
  (r.j.tickets || []).forEach(t => {
    if (!t.text || !t.text.startsWith(TAG) || t.status === "done") return;
    const m = /^cluster: ([0-9a-f]{64})$/m.exec(t.text); if (!m) return;
    const re = /^- (\S+) \| ([^|]+) \| ([^|]+) \| (\w+) \| [0-9a-f]{64}$/gm; let x;
    while ((x = re.exec(t.text))) dupSet[x[1]] = { ticket: t.id, cluster: m[1], scope: x[4] };
  });
}
async function loadDrift() {
  drift = {}; const n = bname(); if (!n) return;
  const r = await call("GET", "/api/shared/drift?board=" + encodeURIComponent(n)); if (!r.ok || !r.j) return;
  (r.j.instances || []).forEach(i => { drift[i.card_id] = i.state });
}
async function loadLib() {
  lib = []; const n = bname(); if (!n) return;
  const r = await call("GET", "/api/shared/library?board=" + encodeURIComponent(n)); if (r.ok && r.j) lib = r.j.shared_library || [];
}
async function refresh() { await Promise.all([loadTickets(), loadDrift(), loadLib()]); decorateAll(); updateAction() }

/* ---------- card decoration ---------- */
function decorate(d) {
  const id = d.dataset.id, c = cardOf(id); if (!c) return;
  d.querySelectorAll(":scope > .shd-badge,:scope > .shd-dupchip").forEach(n => n.remove());
  d.classList.remove("shd-dup", "shd-shared");
  if (c.shared && c.shared.step_id) {
    const n = places(c.shared.step_id), b = el("div", "shd-badge"); b.setAttribute("data-shared", c.shared.step_id);
    b.appendChild(el("span", "shd-n", GLYPH + " used in " + n + " place" + (n === 1 ? "" : "s")));
    const st = drift[id] || (c.shared.sop_sync); const dg = DRIFT[st];
    if (dg) { const s = el("span", "shd-d " + st, "SOP " + dg[0] + " " + st); s.title = dg[1]; b.appendChild(s) }
    d.classList.add("shd-shared"); d.appendChild(b);
  }
  if (dupOn && dupSet[id]) {
    d.classList.add("shd-dup");
    const ch = el("div", "shd-dupchip", "duplicate · " + dupSet[id].scope); ch.title = "Proposed standard step (ticket " + dupSet[id].ticket + ")"; d.appendChild(ch);
  }
}
let qd = 0;
function decorateAll() {
  const w = $("cards"); if (!w) return;
  if (obs) obs.disconnect();
  try { w.querySelectorAll(".card[data-id]").forEach(decorate) } finally { if (obs && w) obs.observe(w, { childList: true, subtree: true }) }
}
let obs = null;
function watchWorld() {
  const w = $("cards"); if (!w) return;
  obs = new MutationObserver(() => { if (qd) return; qd = setTimeout(() => { qd = 0; decorateAll() }, 30) });
  obs.observe(w, { childList: true });  /* render() rebuilds cards as direct children of #cards; badges are grandchildren, so no feedback loop */
}

/* ---------- Duplicates lens + Replace action ---------- */
function toggleDup() {
  dupOn = !dupOn; const b = $("bDup"); if (b) b.setAttribute("aria-pressed", dupOn ? "true" : "false");
  loadTickets().then(() => { decorateAll(); const n = Object.keys(dupSet).length; say(dupOn ? (n ? n + " duplicate candidate card(s) highlighted; select 2+ and click Replace with standard step" : "no open [DEDUP-PROPOSAL] tickets on this board") : "duplicates lens off") });
}
function selected() { return W() ? [...W().sel] : [] }
function updateAction() {
  const b = $("bShdReplace"); if (!b) return;
  const ids = selected().filter(i => { const c = cardOf(i); return c && c.type === "step" && !c.shared });
  b.classList.toggle("on", ids.length >= 2); b.textContent = "Replace with standard step (" + ids.length + ")";
  injectInspector();
}
async function replaceSelected() {
  if (busy) return; const ids = selected().filter(i => { const c = cardOf(i); return c && c.type === "step" && !c.shared });
  if (ids.length < 2) { say("select 2+ unshared step cards", "err"); return }
  const first = cardOf(ids[0]);
  if (!window.confirm("Replace " + ids.length + " cards with ONE shared standard step (\"" + String(first.label).replace(/^\d+\s+/, "") + "\")? Edits to it will then change " + ids.length + " places.")) return;
  busy = true;
  try {
    if (!await saveFirst("replace")) return;
    const members = ids.map(i => ({ card_id: i, scope: (dupSet[i] && dupSet[i].scope) || "whole" }));
    const r = await call("POST", "/api/shared/replace", { board: bname(), members, canonical: ids[0] });
    if (!r.ok) { say("replace failed: " + ((r.j && r.j.error) || r.status), "err"); return }
    say("shared step " + r.j.step_id + " created, used in " + r.j.refcount + " places", "ok");
    await reopen();
  } finally { busy = false }
}
async function reopen() { const n = bname(); if (W() && n) { await W().openBoard(n) } await refresh() }

/* ---------- N-places modal ---------- */
function closeModal() { const m = $("shdModal"); if (m) m.remove() }
async function openEdit(sid, changes) {
  if (!await saveFirst("edit")) return;
  const r = await call("POST", "/api/shared/edit-preview", { board: bname(), step_id: sid, changes });
  if (!r.ok) { say("preview failed: " + ((r.j && r.j.error) || r.status), "err"); return }
  const p = r.j; closeModal();
  const m = el("div"); m.id = "shdModal"; const box = el("div", "shdbox"); m.appendChild(box);
  box.appendChild(el("h3", null, "This will change " + p.n_places + " place" + (p.n_places === 1 ? "" : "s") + " in " + p.n_sops + " SOP" + (p.n_sops === 1 ? "" : "s")));
  box.appendChild(el("div", null, "Standard step " + sid + ": v" + p.version + " → v" + p.new_version + ". Uncheck a lane to fork it (keeps the current text, leaves the standard step)."));
  const lanes = el("div", "shdlanes"); const cbs = [];
  p.lanes.forEach(l => { const lb = el("label"); const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = true; cb.value = l.card_id; cbs.push(cb);
    lb.append(cb, document.createTextNode(" " + (l.sop || l.group || "?") + " · " + l.card_id + " · " + l.label)); lanes.appendChild(lb) });
  box.appendChild(lanes);
  const cols = el("div", "shdcols"), side = (t, o) => { const c = el("div", "shdcol"); c.appendChild(el("b", null, t + "\n")); c.appendChild(document.createTextNode(o.label + "\n\n" + o.body + (o.fields && Object.keys(o.fields).length ? "\n\n" + Object.entries(o.fields).map(kv => kv[0] + ": " + kv[1]).join("\n") : ""))); return c };
  cols.append(side("Current v" + p.version, p.before), side("After v" + p.new_version, p.after)); box.appendChild(cols);
  box.appendChild(el("pre", null, p.diff || "(no textual diff)"));
  const a = el("div", "shda"); const cancel = el("button", null, "Cancel"), ok = el("button", "pri", "Confirm: change " + p.n_places + " places"); ok.id = "shdConfirm"; cancel.id = "shdCancel";
  const upd = () => { const n = cbs.filter(c => c.checked).length; ok.textContent = "Confirm: change " + n + " place" + (n === 1 ? "" : "s"); ok.disabled = n === 0 && false };
  cbs.forEach(c => c.addEventListener("change", upd));
  cancel.addEventListener("click", closeModal);
  ok.addEventListener("click", async () => {
    if (busy) return; busy = true; ok.disabled = true;
    try {
      const forks = cbs.filter(c => !c.checked).map(c => c.value);
      const rr = await call("POST", "/api/shared/edit", { board: bname(), step_id: sid, changes, confirm: true, expected_version: p.version, fork_cards: forks });
      if (!rr.ok) { say("edit failed: " + ((rr.j && rr.j.error) || rr.status), "err"); ok.disabled = false; return }
      closeModal(); say("standard step " + sid + " now v" + rr.j.version + " (" + rr.j.n_places + " places)", "ok"); await reopen();
    } finally { busy = false }
  });
  a.append(cancel, ok); box.appendChild(a);
  m.addEventListener("pointerdown", ev => { if (ev.target === m) closeModal() });
  document.body.appendChild(m);
}
async function unlink(cardId) {
  if (busy) return; if (!window.confirm("Unlink " + cardId + " from the standard step? It keeps a plain copy of the current text.")) return;
  busy = true;
  try {
    if (!await saveFirst("unlink")) return;
    const r = await call("POST", "/api/shared/unlink", { board: bname(), card_id: cardId });
    if (!r.ok) { say("unlink failed: " + ((r.j && r.j.error) || r.status), "err"); return }
    say("unlinked " + cardId + "; " + r.j.step_id + " now used in " + r.j.refcount + " places", "ok"); await reopen();
  } finally { busy = false }
}

/* ---------- inspector section ---------- */
function injectInspector() {
  const b = $("inspBody"), w = W(); if (!b || !w || !w.board) return;
  const ids = [...w.sel], c = ids.length === 1 ? cardOf(ids[0]) : null;
  const want = c && c.shared && c.shared.step_id ? c.id + "|" + c.shared.step_id + "|" + c.shared.version : "";
  const cur = b.querySelector("#shdSec");
  if (!want) { if (cur) cur.remove(); return }
  if (cur && cur.dataset.key === want) return;
  if (cur) cur.remove();
  const sid = c.shared.step_id, e = entryOf(sid) || { label: c.label, body: (c.ref && c.ref.detail) || "", fields: {} };
  const sec = el("div"); sec.id = "shdSec"; sec.dataset.key = want;
  sec.appendChild(el("div", "shdhd", GLYPH + " Shared step - edits change " + places(sid) + " places"));
  sec.appendChild(el("div", null, sid + " v" + c.shared.version + " · SOP " + (drift[c.id] || c.shared.sop_sync || "?")));
  const lab = document.createElement("input"); lab.className = "shdk"; lab.id = "shdLabel"; lab.value = e.label || ""; lab.maxLength = 200;
  const body = document.createElement("textarea"); body.id = "shdBody"; body.value = e.body || "";
  const gate = document.createElement("input"); gate.className = "shdk"; gate.id = "shdGate"; gate.placeholder = "field: gate"; gate.value = (e.fields && e.fields.gate) || "";
  sec.append(el("label", null, "Label"), lab, el("label", null, "Body"), body, el("label", null, "Field: gate"), gate);
  const act = el("div", "shda"), prev = el("button", "pri", "Preview change..."), un = el("button", null, "Unlink from standard"); prev.id = "shdPreview"; un.id = "shdUnlink";
  prev.addEventListener("click", () => {
    const ch = {}; if (lab.value !== (e.label || "")) ch.label = lab.value; if (body.value !== (e.body || "")) ch.body = body.value;
    if (gate.value !== ((e.fields && e.fields.gate) || "")) ch.fields = Object.assign({}, e.fields || {}, { gate: gate.value });
    if (!Object.keys(ch).length) { say("no change to preview"); return }
    openEdit(sid, ch);
  });
  un.addEventListener("click", () => unlink(c.id));
  act.append(prev, un); sec.appendChild(act); b.insertBefore(sec, b.firstChild);
}
function watchInspector() {
  const b = $("inspBody"); if (!b) return;
  new MutationObserver(() => { if (!b.querySelector("#shdSec")) injectInspector() }).observe(b, { childList: true });
}

/* ---------- install ---------- */
function install() {
  const bar = $("bar"), msg = $("msg"); if (!bar || $("bDup")) return;
  const d = el("button", null, "Duplicates"); d.id = "bDup"; d.title = "Highlight cards proposed as duplicates by the dedup scan"; d.setAttribute("aria-pressed", "false"); d.addEventListener("click", toggleDup);
  const r = el("button", null, "Replace with standard step"); r.id = "bShdReplace"; r.title = "Turn the selected duplicate step cards into ONE shared standard step"; r.addEventListener("click", replaceSelected);
  bar.insertBefore(d, msg); bar.insertBefore(r, msg);
  watchWorld(); watchInspector();
  window.addEventListener("pointerup", () => setTimeout(updateAction, 0), true);
  window.addEventListener("keyup", () => setTimeout(updateAction, 0), true);
  setInterval(() => {
    const w = W(); if (!w || !w.board) return;
    const k = bname() + "|" + (w.board.shared_library ? w.board.shared_library.length : 0);
    if (k !== boardKey) { boardKey = k; refresh() }
    const s = [...w.sel].join(","); if (s !== selSig) { selSig = s; updateAction() }
  }, 600);
}
window.wbShared = { isDirty, saveFirst, refresh, dupMembers: () => dupSet, replaceSelected, openEdit, unlink, lens: toggleDup };
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", install); else install();
