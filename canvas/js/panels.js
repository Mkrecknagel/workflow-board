"use strict";
/* workflow-board canvas panels.js (module, v2.1). Resizable left (palette/library) and right (inspector) panels,
   collapse toggles, colour legend. Widths and collapse state persist in localStorage (wb.palw, wb.inspw, wb.palc, wb.inspc, wb.legend).
   Desktop only: the phone layout (<700px) keeps the Add / Inspect toggles and hides the handles via CSS. */
const $ = id => document.getElementById(id);
const MIN = 180, KEY = { pal: "wb.palw", insp: "wb.inspw" }, DEF = { pal: 220, insp: 280 };
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* private mode */ } }
};
const maxW = () => Math.floor(window.innerWidth * 0.5);
const clamp = n => Math.max(MIN, Math.min(maxW(), Math.round(n)));
const widths = { pal: DEF.pal, insp: DEF.insp };

function apply() {
  const r = document.documentElement.style;
  r.setProperty("--palw", widths.pal + "px");
  r.setProperty("--inspw", widths.insp + "px");
}
function load() {
  ["pal", "insp"].forEach(k => { const v = parseInt(store.get(KEY[k]), 10); if (Number.isFinite(v)) widths[k] = clamp(v); });
  $("app").classList.toggle("palc", store.get("wb.palc") === "1");
  $("app").classList.toggle("inspc", store.get("wb.inspc") === "1");
  apply();
}
function wire(side) {
  const h = $(side === "pal" ? "hPal" : "hInsp");
  h.addEventListener("pointerdown", ev => {
    ev.preventDefault(); ev.stopPropagation();
    h.setPointerCapture(ev.pointerId); h.classList.add("on");
    const move = e => {
      const w = side === "pal" ? e.clientX : window.innerWidth - e.clientX;
      widths[side] = clamp(w); apply();
    };
    const up = e => {
      h.classList.remove("on"); h.removeEventListener("pointermove", move); h.removeEventListener("pointerup", up); h.removeEventListener("pointercancel", up);
      try { h.releasePointerCapture(e.pointerId); } catch (_) { /* gone */ }
      store.set(KEY[side], String(widths[side]));
    };
    h.addEventListener("pointermove", move); h.addEventListener("pointerup", up); h.addEventListener("pointercancel", up);
  });
  h.addEventListener("dblclick", () => { widths[side] = DEF[side]; apply(); store.set(KEY[side], String(widths[side])); });
}
function collapse(side) {
  const cls = side === "pal" ? "palc" : "inspc", on = $("app").classList.toggle(cls);
  store.set("wb." + cls, on ? "1" : "0");
}

function legend() {
  const box = $("legend"); box.innerHTML = "";
  const t = document.createElement("b"); t.textContent = "Card types"; box.appendChild(t);
  const w = window.wb; if (!w) return;
  Object.keys(w.TYPES).forEach(k => {
    const row = document.createElement("div"), sw = document.createElement("i");
    sw.textContent = w.TYPES[k][0]; sw.style.background = w.TYPE_COLOR[k]; sw.style.color = w.textOn(w.TYPE_COLOR[k]);
    row.appendChild(sw); row.appendChild(document.createTextNode(k)); box.appendChild(row);
  });
  const g = w.board && w.board.group_styles;
  if (g && Object.keys(g).some(k => g[k].color)) {
    const t2 = document.createElement("b"); t2.textContent = "Group lanes"; t2.style.display = "block"; t2.style.marginTop = "6px"; box.appendChild(t2);
    Object.keys(g).filter(k => g[k].color).forEach(k => {
      const row = document.createElement("div"), sw = document.createElement("i");
      sw.style.background = g[k].color; row.appendChild(sw); row.appendChild(document.createTextNode(k)); box.appendChild(row);
    });
  }
  /* v3: teaching badges + lenses + the level meaning (kept in the legend so it is findable without the panel) */
  const t3 = document.createElement("b"); t3.textContent = "Badges"; t3.style.cssText = "display:block;margin-top:6px"; box.appendChild(t3);
  BADGES.forEach(([k, d]) => { const row = document.createElement("div"), sw = document.createElement("i"); sw.textContent = k; sw.style.width = "auto"; sw.style.padding = "0 5px"; row.appendChild(sw); row.appendChild(document.createTextNode(d)); box.appendChild(row); });
  const t4 = document.createElement("b"); t4.textContent = "Lenses (top right of the canvas)"; t4.style.cssText = "display:block;margin-top:6px"; box.appendChild(t4);
  LENS_HELP.forEach(([k, d]) => { const row = document.createElement("div"); const s = document.createElement("i"); s.textContent = k; s.style.width = "auto"; s.style.padding = "0 5px"; row.appendChild(s); row.appendChild(document.createTextNode(d)); box.appendChild(row); });
  const t5 = document.createElement("b"); t5.textContent = "What each level is for"; t5.style.cssText = "display:block;margin-top:6px"; box.appendChild(t5);
  Object.keys(LEVELS).forEach(n => { const d = document.createElement("div"), L = LEVELS[n]; d.style.display = "block"; d.style.maxWidth = "320px"; d.textContent = "Level " + n + " — " + L.name + ". " + L.line; box.appendChild(d); });
}
function toggleLegend() {
  const on = $("legend").classList.toggle("open"); if (on) legend(); store.set("wb.legend", on ? "1" : "0");
}


/* ---- v3 level panel: "what this level is for" (shown on level change, collapsible, also summarised in the legend) ---- */
const LEVELS = {
  1: { name: "Context", line: "Who and where.", ask: "Whose is it, what does it cost, what is outside my control, where are the entity lines?", shows: "People, entities, machines, vendors, network pipes.", hides: "Everything that runs." },
  2: { name: "Containers", line: "What runs where, and where state comes to rest.", ask: "What dies together, which copy is the truth, where are the secrets, what crosses a trust boundary?", shows: "Daemons, services, models, datastores, boundaries.", hides: "Steps." },
  3: { name: "Workflow", line: "How work flows.", ask: "What triggers it, what gates it, what does it output?", shows: "Triggers, skills, hooks, gates, outputs.", hides: "Nothing; this is the working board." }
};
const BADGES = [["SPOF", "single point of failure (computed)"], ["unverified", "fact not confirmed yet"], ["SoT / cache / snapshot", "datastore authority"], ["key", "holds secrets"], ["ext", "outside your control"], ["$", "metered or fixed cost"], ["M K shared vendor", "owner"]];
const LENS_HELP = [["SPOF", "red outline on single points of failure"], ["Trust", "amber edges cross a trust boundary; key cards outlined"], ["Cost", "metered amber, zero green, fixed blue"], ["Entity", "colour by LLC / entity; dashed amber = cross-entity edge"], ["External", "outside services outlined, the rest dimmed"], ["Blast", "select a card: downstream cards on the same machine"]];
let lvlSem = null;
function levelPanel(sem) {
  /* owner 2026-10-06: bottom-right level panel removed as too noisy. Level is still shown in the legend. */
  const ex = $("wb-level"); if (ex) ex.remove(); return;
  const L = LEVELS[sem]; if (!L) return;
  let p = $("wb-level"); const vp = $("vp"); if (!vp) return;
  if (!p) {
    p = document.createElement("div"); p.id = "wb-level"; p.setAttribute("aria-live", "polite");
    p.style.cssText = "position:absolute;right:8px;bottom:8px;z-index:5;max-width:min(340px,calc(100% - 16px));padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:var(--panel);box-shadow:0 1px 6px #0003;font-size:12px;line-height:1.35";
    p.addEventListener("pointerdown", e => e.stopPropagation()); vp.appendChild(p);
    p.addEventListener("click", () => { const c = p.classList.toggle("min"); store.set("wb.levelmin", c ? "1" : "0"); levelPanel(lvlSem); });
    if (store.get("wb.levelmin") === "1") p.classList.add("min");
  }
  lvlSem = sem; p.dataset.level = String(sem); p.textContent = "";
  const h = document.createElement("b"); h.textContent = "Level " + sem + " — " + L.name + ": " + L.line; p.appendChild(h);
  if (!p.classList.contains("min")) {
    [["Ask", L.ask], ["Shows", L.shows], ["Hides", L.hides]].forEach(([k, v]) => { const d = document.createElement("div"); d.style.color = "var(--mut)"; d.textContent = k + ": " + v; p.appendChild(d); });
  }
  p.title = "Click to collapse or expand";
}
function levelNow() { return window.wbZoom ? window.wbZoom.semantic : null; }
document.addEventListener("wb:level", e => levelPanel(e.detail.sem));

function init() {
  load(); wire("pal"); wire("insp");
  $("bColL").onclick = () => collapse("pal");
  $("bColR").onclick = () => collapse("insp");
  $("bLegend").onclick = toggleLegend;
  window.addEventListener("resize", () => { ["pal", "insp"].forEach(k => { if (widths[k] > maxW()) { widths[k] = clamp(widths[k]); apply(); } }); });
  import("./lenses.js").catch(() => {});
  let n = 0; const iv = setInterval(() => { const s = levelNow(); if (s) { levelPanel(s); clearInterval(iv); } else if (++n > 100) clearInterval(iv); }, 100);
  if (store.get("wb.legend") === "1") setTimeout(() => { $("legend").classList.add("open"); legend(); }, 400);
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
window.wbPanels = { widths, legend };
