/* workflow-board v5 run_mode (module). Card field run_mode: "inline" | "background" | "wait"; absent means inline and the field is
   written ONLY when non-inline (so removing it restores the old hash). run_mode is HASHED behaviour: changing it makes the board
   behaviour-dirty and it is never part of a visual autosave. Guidance only: nothing here enforces anything.
   Pure exports (usable from node): MODES, RUNMODE_BLOCKERS, GLYPH, isEligible, blockerFor, nextMode, setMode, cycle, counts.
   Browser: decorates eligible cards with a chip (glyph + text, never colour only) via wbKinds.registerDecorator, shows a count
   badge when the zoom level is the silhouette level (data-lod=1), and exposes window.wbRunMode for inspector.js.
   RUNMODE_BLOCKERS is data only (keyed by ref.name): a card with an entry shows "needs change" + the text instead of toggling. */
export const MODES = ["inline", "background", "wait"];
export const GLYPH = { inline: "\u25CF", background: "\u21C9", wait: "\u29D7" };
export const RUNMODE_BLOCKERS = {
  "demo-compile": "background/wait blocked today: KB writes are gated to the main session by the .compile-active sentinel. Would need: a dedicated KB-writer agent that is allowed to hold the sentinel (a owner governance decision)."
};
const ELIGIBLE_TYPES = ["skill", "tool", "agent", "step"];

export function isEligible(c) {
  if (!c || c.immutable === true || !ELIGIBLE_TYPES.includes(c.type)) return false;
  if (c.type === "step") return !!c.ref && c.ref.kind === "task";
  return true;
}
export const blockerFor = c => (c && c.ref && typeof c.ref.name === "string" && Object.prototype.hasOwnProperty.call(RUNMODE_BLOCKERS, c.ref.name)) ? RUNMODE_BLOCKERS[c.ref.name] : null;
export const modeOf = c => (c && MODES.includes(c.run_mode)) ? c.run_mode : "inline";
export const nextMode = m => MODES[(MODES.indexOf(MODES.includes(m) ? m : "inline") + 1) % MODES.length];
/* write the field only when non-inline; returns the stored mode */
export function setMode(c, m) {
  if (m === "inline" || !MODES.includes(m)) delete c.run_mode; else c.run_mode = m;
  return modeOf(c);
}
/* cycle inline -> background -> wait -> inline. Ineligible: {ok:false}. Blocked card: stays inline, returns {ok:false, blocker}. */
export function cycle(c) {
  if (!isEligible(c)) return { ok: false, reason: "not a dispatchable action card" };
  const b = blockerFor(c);
  if (b) { setMode(c, "inline"); return { ok: false, blocker: b, mode: "inline" }; }
  return { ok: true, mode: setMode(c, nextMode(modeOf(c))) };
}
export function counts(board) {
  const o = { inline: 0, background: 0, wait: 0, nonInline: 0 };
  for (const c of (board && board.cards) || []) { if (!isEligible(c)) continue; const m = modeOf(c); o[m]++; if (m !== "inline") o.nonInline++; }
  return o;
}

if (typeof window !== "undefined" && typeof document !== "undefined") (function () {
  const W = window.wb;
  if (!W) return;
  const E = (t, c, x) => { const e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e };
  const st = E("style");
  st.textContent = `.card .rm{margin:2px 6px 4px;font-size:11px;line-height:1.3}
.card .rm button{font:inherit;padding:1px 7px;border:1px solid var(--line,#555);border-radius:10px;background:var(--panel,#222);color:var(--ink,#eee);cursor:pointer}
.card .rm.need button{border-style:dashed}
.card .rm .rm-need{display:block;margin-top:2px;color:var(--mut,#999);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%} /* one line: the full blocker text is in the DOM + title; a wrapping paragraph made the card 100+px taller and its lane overlap the next lane header */
#rm-badge{position:absolute;right:10px;top:10px;z-index:5;display:none;padding:3px 10px;border:1px solid var(--line,#555);border-radius:12px;background:var(--panel,#222);color:var(--ink,#eee);font:12px system-ui,sans-serif;pointer-events:none}
#vp[data-lod="1"] #rm-badge.on{display:block}`;
  document.head.appendChild(st);

  function say(m, cls) { const e = document.getElementById("msg"); if (e) { e.textContent = m; e.className = cls || ""; e.title = m } }
  function touched() {
    document.dispatchEvent(new CustomEvent("wb:runmode", { detail: { board: W.board && W.board.board } }));
    if (window.wbDirty && window.wbDirty.check) window.wbDirty.check();
    refreshBadge();
  }
  function cardById(id) { return W.board && W.board.cards.find(c => c.id === id) }

  function toggle(c) {
    const r = cycle(c);
    if (r.blocker) say("run_mode '" + c.label + "': " + r.blocker, "err");
    else if (r.ok) say("run_mode " + c.label + " = " + r.mode + " (behaviour change: Save + deploy needed)", "ok");
    touched();
    if (window.wbKinds) window.wbKinds.decorateAll(); else W.render();
    return r;
  }

  function decorate(d, c) {
    const old = d.querySelector(":scope > .rm"); if (old) old.remove();
    if (!isEligible(c)) return;
    const m = modeOf(c), b = blockerFor(c);
    const w = E("div", "rm" + (b ? " need" : ""));
    const btn = E("button", null, b ? GLYPH.inline + " inline \u00B7 needs change" : GLYPH[m] + " " + m);
    btn.type = "button"; btn.dataset.runmode = b ? "blocked" : m;
    btn.title = b ? b : "run mode: " + m + " (click to cycle inline, background, wait). Guidance only; Save + deploy to take effect.";
    btn.addEventListener("pointerdown", ev => ev.stopPropagation());
    btn.addEventListener("click", ev => { ev.stopPropagation(); toggle(c) });
    w.appendChild(btn);
    if (b) { const n = E("span", "rm-need", "Would need to change: " + b); n.title = b; w.appendChild(n) }
    d.appendChild(w);
  }

  function refreshBadge() {
    const vp = document.getElementById("vp"); if (!vp) return;
    let bd = document.getElementById("rm-badge");
    if (!bd) { bd = E("div"); bd.id = "rm-badge"; bd.setAttribute("role", "status"); vp.appendChild(bd) }
    const n = counts(W.board);
    bd.classList.toggle("on", n.nonInline > 0);
    bd.textContent = n.nonInline > 0 ? GLYPH.background + " " + n.nonInline + " non-inline (" + n.background + " background, " + n.wait + " wait)" : "";
    bd.dataset.count = String(n.nonInline);
  }

  window.wbRunMode = { MODES, GLYPH, RUNMODE_BLOCKERS, isEligible, blockerFor, modeOf, cycle: id => { const c = cardById(id); return c ? toggle(c) : { ok: false } }, counts: () => counts(W.board), refreshBadge };
  const reg = () => { if (window.wbKinds && window.wbKinds.registerDecorator) { window.wbKinds.registerDecorator((d, c) => { try { decorate(d, c) } catch (e) { console.warn("runmode decorate: " + e) } refreshBadge() }); return true } return false };
  if (!reg()) { let n = 0; const t = setInterval(() => { if (reg() || ++n > 50) clearInterval(t) }, 100) }
  document.addEventListener("wb:level", refreshBadge);
  refreshBadge();
})();
