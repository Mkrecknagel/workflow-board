"use strict";
/* workflow-board v4 card kinds (module). Self-installing; reads window.wb only (core.js untouched).
   Adds: card types `note` (sticky note) and `container` (connector shell; its body is drawn by containers.js, N2),
   and decorates cards that carry the optional v4 keys: gate ref.kind=approval, pin{port,sample,synthetic}, on_fail{target,record}.
   All board text is rendered with textContent only. No network access, no writes: this module only changes how cards look.

   EXTENSION POINTS for N2/N3/N4 (all on window.wbKinds):
     wbKinds.registerDecorator(fn)  fn(cardEl, card, board) runs for every rendered card after kinds' own decoration; throw-safe.
                                    N2's containers.js uses it to fill container cards. Decorators run on every render.
     wbKinds.registerType(name, [icon, refKey], color)   add a card type to wb.TYPES / wb.TYPE_COLOR (idempotent).
     wbKinds.decorateAll()          re-run decoration (call after you mutate board state without wb.render()).
     wbKinds.V4_TYPES               ["container","note"]
   Containers wire to workflows with ordinary edges from out port "cap:<tool>" plus target ref.via_container (see C1-design.md). */
(function () {
  const W = window.wb;
  if (!W) { console.warn("kinds.js: window.wb missing (core.js did not load)"); return; }
  const E = (t, c, x) => { const e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e };
  const V4_TYPES = ["container", "note"];
  const decorators = [];

  function registerType(name, def, color) {
    if (!W.TYPES[name]) W.TYPES[name] = def;
    if (!W.TYPE_COLOR[name]) W.TYPE_COLOR[name] = color;
  }
  registerType("note", ["\u{1F4DD}", "kind"], "#C9B037");
  registerType("container", ["\u{1F50C}", "connector"], "#4C78A8");

  const cardById = id => (W.board ? W.board.cards.find(c => c.id === id) : null);

  function badges(d, items) {
    const old = d.querySelector(":scope > .v4-badges"); if (old) old.remove();
    if (!items.length) return;
    const b = E("div", "v4-badges");
    items.forEach(([cls, text, title]) => { const s = E("span", cls, text); if (title) s.title = title; b.appendChild(s) });
    d.appendChild(b);
  }

  function decorate(d, c) {
    const items = [];
    d.querySelectorAll(":scope > .v4-note").forEach(n => n.remove());
    if (c.type === "note") {
      const t = c.fields && typeof c.fields.text === "string" ? c.fields.text : "";
      const n = E("div", "v4-note" + (t ? "" : " empty"), t || "(empty note)");
      const hd = d.querySelector(":scope > .hd");
      if (hd && hd.nextSibling) d.insertBefore(n, hd.nextSibling); else d.appendChild(n);
      if (c.size && Number.isFinite(c.size.w) && Number.isFinite(c.size.h)) { d.style.width = c.size.w + "px"; d.style.minHeight = c.size.h + "px" }
    }
    if (c.type === "gate" && c.ref && c.ref.kind === "approval") {
      d.classList.add("v4-appr-gate");
      items.push(["v4-appr", "\u{1F512} approval: owner", "Approval is the owner's own message to the session, never a node. Draft: " + (c.ref.draft_path || "?")]);
    }
    if (c.pin) items.push(["v4-pin", "\u{1F4CC} pinned (synthetic)", "port " + c.pin.port + ", sample " + JSON.stringify(c.pin.sample || {}).length + " B"]);
    if (c.on_fail) { const t = cardById(c.on_fail.target); items.push(["v4-fail", "on fail \u2192 " + (t ? t.label : c.on_fail.target), "record: " + c.on_fail.record + " (layout only; no executor semantics)"]) }
    badges(d, items);
    decorators.forEach(fn => { try { fn(d, c, W.board) } catch (e) { console.warn("kinds decorator failed: " + e) } });
  }

  function decorateAll() {
    document.querySelectorAll("#cards > .card[data-id]").forEach(d => { const c = cardById(d.dataset.id); if (c) decorate(d, c) });
  }

  let busy = false;
  const obs = new MutationObserver(() => { if (busy) return; busy = true; try { decorateAll() } finally { busy = false } });
  const root = document.getElementById("cards");
  if (root) obs.observe(root, { childList: true });

  /* palette: sticky note (container cards are created from the connector inventory by containers.js) */
  function addNote() {
    const b = W.board; if (!b) return;
    let n = 1, id; do { id = "note-" + String(n++).padStart(2, "0") } while (cardById(id));
    const vp = document.getElementById("vp").getBoundingClientRect(), v = W.view;
    const x = W.snap((vp.width / 2 - v.x) / v.k - 96), y = W.snap((vp.height / 2 - v.y) / v.k - 40);
    b.cards.push({ id, type: "note", label: "Note", immutable: false, enabled: true, execution_mode: "observe", ref: { kind: "note" }, ports: { in: [], out: [] }, pos: { x, y }, fields: { text: "" } });
    W.render();
  }
  const pl = document.getElementById("palList");
  if (pl && !pl.querySelector(".v4-palnote")) {
    const d = E("div", "pi v4-palnote"); d.appendChild(E("i", null, W.TYPES.note[0])); d.appendChild(E("span", null, "note")); d.addEventListener("click", addNote); pl.appendChild(d);
  }

  window.wbKinds = { V4_TYPES, registerDecorator: fn => { if (typeof fn === "function") decorators.push(fn); decorateAll() }, registerType, decorateAll, addNote };
  if (W.board) W.render(); /* a board that loaded before this module ran: re-render with the new kinds known */
})();
