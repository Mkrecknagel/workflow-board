/* Board tickets UI (B3). Hooked from core.js via window.wbTickets: decorate(el,card), inspector(card), boardOpened(name).
   All ticket text/replies are rendered with textContent only (phone-reachable page, no markdown/HTML). */
const GL = { draft: "○", submitted: "◔", in_progress: "◑", blocked: "⚠", done: "✓" };
const COL = { draft: "#999999", submitted: "#0072B2", in_progress: "#E69F00", blocked: "#D55E00", done: "#009E73" }; /* Okabe-Ito */
const WORST = ["blocked", "in_progress", "submitted", "draft"];
const $ = id => document.getElementById(id);
const el = (t, c, x) => { const e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e };

let board = null, tickets = [], pending = 0, curCard = null, sig = "", arch = [], archOpen = false, busy = false;

(function css() {
  const s = document.createElement("style");
  s.textContent = ".tkb{position:absolute;top:-9px;right:-9px;min-width:26px;height:20px;padding:0 6px;border-radius:10px;background:var(--tkc,#999);color:#fff;font:600 11px/20px system-ui,sans-serif;text-align:center;box-shadow:0 1px 3px #0005;pointer-events:none;white-space:nowrap}" +
    "#tkPill{font-size:11px;padding:2px 8px;border:1px solid var(--line);border-radius:10px;color:var(--mut);white-space:nowrap;cursor:pointer;background:var(--panel)}#tkPill.on{color:#fff;background:#0072B2;border-color:#0072B2}" +
    "#tkSec .tk{border:1px solid var(--line);border-radius:6px;padding:6px 8px;margin:6px 0;background:var(--card)}#tkSec .tkchip{display:inline-block;font-size:11px;font-weight:600;padding:0 7px;border-radius:9px;color:#fff;background:var(--tkc,#999)}" +
    "#tkSec .tkt{margin:4px 0;white-space:pre-wrap;word-break:break-word}#tkSec .tkr{margin:3px 0 3px 8px;padding-left:6px;border-left:2px solid var(--line);color:var(--mut);font-size:12px;white-space:pre-wrap;word-break:break-word}" +
    "#tkSec .tka{display:flex;gap:6px;margin-top:4px}#tkSec textarea{min-height:56px}#tkSec details{margin-top:8px}#tkPanel{display:none;position:fixed;right:300px;top:50px;z-index:9;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 10px;box-shadow:0 4px 16px #0004;max-width:320px;max-height:60vh;overflow:auto}#tkPanel.open{display:block}#tkPanel div.pi2{padding:4px 2px;cursor:pointer;border-bottom:1px solid var(--line)}";
  document.head.appendChild(s);
})();

async function call(method, path, body) {
  try {
    const r = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
    let j = null; try { j = await r.json() } catch (_) {}
    return { ok: r.ok, status: r.status, j };
  } catch (e) { return { ok: false, status: 0, j: null } }
}
const bname = () => ($("picker") && $("picker").value) || board;
const forCard = id => tickets.filter(t => t.card === id);
const openOf = ts => ts.filter(t => t.status !== "done");

function badgeOf(cardId) {
  const ts = forCard(cardId); if (!ts.length) return null;
  const op = openOf(ts);
  if (op.length) { const w = WORST.find(s => op.some(t => t.status === s)); return { text: GL[w] + op.length, col: COL[w], status: w } }
  return { text: GL.done + ts.length, col: COL.done, status: "done" };
}
function decorate(d, c) {
  const old = d.querySelector(".tkb"); if (old) old.remove();
  const b = badgeOf(c.id); if (!b) return;
  const e = el("div", "tkb", b.text); e.style.setProperty("--tkc", b.col); e.dataset.status = b.status; e.title = forCard(c.id).length + " ticket(s)"; d.appendChild(e);
}
function decorateAll() { if (!window.wb || !window.wb.board) return; window.wb.board.cards.forEach(c => { const d = document.querySelector('.card[data-id="' + c.id + '"]'); if (d) decorate(d, c) }) }

function pill() {
  const p = $("tkPill"); if (!p) return;
  p.textContent = "tickets: " + pending + " pending"; p.classList.toggle("on", pending > 0);
}
async function loadPending() { const r = await call("GET", "/tickets/pending"); if (r.ok && r.j) { pending = r.j.count || 0; pill() } }
async function load() {
  const n = bname(); if (!n) return false; board = n;
  const r = await call("GET", "/tickets/" + encodeURIComponent(n)); if (!r.ok) return false;
  const next = r.j.tickets || [], s = JSON.stringify(next);
  const changed = s !== sig; sig = s; tickets = next;
  await loadPending(); decorateAll();
  return changed;
}
async function loadArch() { if (!board) return; const r = await call("GET", "/tickets/" + encodeURIComponent(board) + "/archive"); arch = r.ok ? (r.j.tickets || []) : arch }

function ticketNode(t, isArch) {
  const d = el("div", "tk"); d.dataset.id = t.id; d.dataset.status = t.status;
  const top = el("div"); const chip = el("span", "tkchip", GL[t.status] + " " + t.status.replace("_", " ")); chip.style.setProperty("--tkc", COL[t.status] || "#999"); top.appendChild(chip);
  if (t.kind && t.kind !== "note") top.appendChild(el("small", null, "  " + t.kind));
  d.appendChild(top); d.appendChild(el("div", "tkt", t.text));
  (t.replies || []).forEach(r => d.appendChild(el("div", "tkr", r.author + ": " + r.text)));
  const act = el("div", "tka");
  const btn = (label, fn, cls) => { const b = el("button", cls, label); b.addEventListener("click", async () => { if (busy) return; busy = true; try { await fn() } finally { busy = false } await refreshAll() }); act.appendChild(b) };
  const url = "/tickets/" + encodeURIComponent(board) + "/" + encodeURIComponent(t.id);
  if (isArch) btn("Unarchive", () => call("POST", url + "/unarchive"), "tkUn");
  else {
    if (t.status === "draft") btn("Send", () => call("POST", url + "/send"), "tkSend");
    if (t.status === "submitted") btn("Recall", () => call("POST", url + "/recall"), "tkRecall");
    if (t.status !== "done") btn("Done", () => call("POST", url + "/status", { to: "done", no_change: true }), "tkDone");
    if (t.status === "done") btn("Archive", () => call("POST", url + "/archive"), "tkArchive");
    if (t.status === "blocked") {
      const ta = el("textarea"); ta.placeholder = "reply (re-queues)"; d.appendChild(ta);
      btn("Reply", async () => { const v = ta.value.trim(); if (v) await call("POST", url + "/reply", { text: v }) }, "tkReply");
    }
  }
  if (act.childNodes.length) d.appendChild(act);
  return d;
}

function paint() {
  const list = $("tkList"); if (!list || !curCard) return;
  list.textContent = "";
  const ts = forCard(curCard);
  if (!ts.length) list.appendChild(el("div", null, "No tickets on this card."));
  ts.forEach(t => list.appendChild(ticketNode(t, false)));
  const sum = $("tkArchSum"), body = $("tkArchBody"); if (!sum) return;
  const as = arch.filter(t => t.card === curCard);
  sum.textContent = "Archived (" + as.length + ") ▶";
  body.textContent = ""; as.forEach(t => body.appendChild(ticketNode(t, true)));
}
async function refreshAll() { await load(); await loadArch(); paint() }

function inspector(c) {
  const b = $("inspBody"); curCard = c.id;
  const sec = el("div"); sec.id = "tkSec"; sec.appendChild(el("h4", null, "Tickets"));
  const ta = el("textarea"); ta.id = "tkNew"; ta.placeholder = "Comment / instruction for the agent (saved as draft)"; ta.maxLength = 8000;
  const add = el("button", null, "Add comment"); add.id = "tkAdd";
  add.addEventListener("click", async () => {
    const v = ta.value.trim(); if (!v) return;
    const r = await call("POST", "/tickets/" + encodeURIComponent(bname()), { card: c.id, text: v });
    if (r.ok) { ta.value = ""; await refreshAll() }
  });
  sec.append(ta, add);
  const list = el("div"); list.id = "tkList"; sec.appendChild(list);
  const det = el("details"); det.id = "tkArch";
  const sum = el("summary", null, "Archived (0) ▶"); sum.id = "tkArchSum";
  const body = el("div"); body.id = "tkArchBody";
  det.append(sum, body); det.open = archOpen; det.addEventListener("toggle", () => { archOpen = det.open }); sec.appendChild(det);
  b.appendChild(sec); paint(); refreshAll();
}

function boardOpened(n) { board = n; tickets = []; arch = []; sig = ""; load() }

async function togglePanel() {
  const pn = $("tkPanel"); if (pn.classList.contains("open")) { pn.classList.remove("open"); return }
  await load(); pn.textContent = "";
  const ts = openOf(tickets).filter(t => t.status !== "draft");
  pn.appendChild(el("b", null, "Pending on " + board));
  if (!ts.length) pn.appendChild(el("div", null, "none"));
  ts.forEach(t => {
    const r = el("div", "pi2", GL[t.status] + " " + t.card + ": " + t.text.slice(0, 80));
    r.addEventListener("click", () => { pn.classList.remove("open"); const h = document.querySelector('.card[data-id="' + t.card + '"] .hd'); if (h) h.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, cancelable: true })) });
    pn.appendChild(r);
  });
  pn.classList.add("open");
}

function init() {
  const bar = $("bar");
  if (bar && !$("tkPill")) { const p = el("span", null, "tickets: 0 pending"); p.id = "tkPill"; bar.insertBefore(p, $("msg")) }
  const p = $("tkPill"); if (p) { p.title = "Pending tickets (all boards) - click for this board's list"; p.addEventListener("click", togglePanel) }
  const pn = el("div"); pn.id = "tkPanel"; document.body.appendChild(pn);
  load(); window.addEventListener("focus", () => { refreshAll() });
  setInterval(async () => { if (document.hidden) return; if (await load()) { await loadArch(); paint() } }, 10000);
}

window.wbTickets = { decorate, inspector, boardOpened, refresh: refreshAll };
init();
