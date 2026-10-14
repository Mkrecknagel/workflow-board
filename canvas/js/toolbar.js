"use strict";
/* workflow-board v4 toolbar.js (N4, F-B11). Layout/UX action strip: Tidy, Undo tidy, Collapse/Expand lane, Collapse all, Expand all.
   Desktop: inline in the top bar (before the message area). Phone (<=699px): a fixed bottom strip that wraps (no horizontal scroll), buttons >=44px tall.
   Shortcuts use ALT only, so they never shadow select.js (Ctrl/Cmd + C X V A) or core.js (Delete/Backspace) or zoom.js (Enter/Escape):
     Alt+T tidy   Alt+Z undo tidy   Alt+L collapse/expand the selected lane(s)   Alt+Shift+L expand all
   Actions resolve window.wbTidy / window.wbCollapse at click time (module load order is not guaranteed). */
(function () {
  const $ = id => document.getElementById(id);
  const say = (m, cls) => { const e = $("msg"); if (!e) return; e.textContent = m; e.className = cls || ""; e.title = m };
  const has = (o, f) => window[o] && typeof window[o][f] === "function";
  const needBoard = () => { if (!window.wb || !window.wb.board) { say("open a board first", "err"); return false } return true };

  const ACTIONS = {
    tidy: () => { if (needBoard() && has("wbTidy", "run")) window.wbTidy.run() },
    undo: () => { if (has("wbTidy", "undo")) window.wbTidy.undo() },
    autolayout: () => { if (needBoard() && has("wbAutoLayout", "run")) window.wbAutoLayout.run() },
    undolayout: () => { if (has("wbAutoLayout", "undo") && !window.wbAutoLayout.undo()) say("nothing to undo", "err") },
    collapse: () => {
      if (!needBoard() || !has("wbCollapse", "toggleSelected")) return;
      if (!window.wbCollapse.toggleSelected()) say("select a lane (click its header or a card in it) first", "err");
    },
    collapseAll: () => { if (needBoard() && has("wbCollapse", "setAll")) say("collapsed " + window.wbCollapse.setAll(true) + " lane(s) (autosaves)", "ok") },
    expandAll: () => { if (needBoard() && has("wbCollapse", "setAll")) say("expanded " + window.wbCollapse.setAll(false) + " lane(s) (autosaves)", "ok") },
  };

  const style = document.createElement("style");
  style.textContent = "#v4Tb{display:inline-flex;gap:6px;align-items:center;flex:0 0 auto}#v4Tb button{white-space:nowrap}" +
    "@media (max-width:699px){#v4Tb{position:fixed;left:0;right:0;bottom:0;z-index:25;display:flex;flex-wrap:wrap;gap:6px;padding:6px 8px calc(6px + env(safe-area-inset-bottom));background:var(--panel);border-top:1px solid var(--line);box-shadow:0 -2px 10px #0003}" +
    "#v4Tb button{flex:1 1 30%;min-height:44px;touch-action:manipulation}}";
  document.head.appendChild(style);

  function layTray() {
    let tr = $("wb-laytools"); if (tr) return tr;
    const st = document.createElement("style");
    st.textContent = "#wb-laytools{position:fixed;left:12px;bottom:12px;z-index:24;display:flex;gap:6px;align-items:center;flex-wrap:wrap;max-width:calc(100vw - 24px);font:12px system-ui,sans-serif}#wb-laytools button{white-space:nowrap}@media (max-width:699px){#wb-laytools{bottom:132px}}";
    document.head.appendChild(st); tr = document.createElement("div"); tr.id = "wb-laytools"; tr.setAttribute("role", "toolbar"); tr.setAttribute("aria-label", "Auto layout"); document.body.appendChild(tr); return tr;
  }
  function mk(id, txt, title, fn) { const b = document.createElement("button"); b.id = id; b.textContent = txt; b.title = title; b.type = "button"; b.addEventListener("click", fn); return b }
  function build() {
    const bar = $("bar"); if (!bar || $("v4Tb")) return;
    const tb = document.createElement("div"); tb.id = "v4Tb"; tb.setAttribute("role", "toolbar"); tb.setAttribute("aria-label", "Layout tools");
    [["v4Tidy", "Tidy", "Tidy layout: columns by edge depth, even gaps (Alt+T)", ACTIONS.tidy],
     ["v4TidyUndo", "Undo tidy", "Undo the last tidy (Alt+Z)", ACTIONS.undo],
     ["v4Collapse", "Collapse lane", "Collapse / expand the selected lane(s) (Alt+L)", ACTIONS.collapse],
     ["v4CollapseAll", "Collapse all", "Collapse every lane", ACTIONS.collapseAll],
     ["v4ExpandAll", "Expand all", "Expand every lane (Alt+Shift+L)", ACTIONS.expandAll]].forEach(a => tb.appendChild(mk(...a)));
    bar.insertBefore(tb, $("msg"));
    /* auto layout + readability badge live in a small floating tray (bottom-left), not in the top bar: the bar is already full at 1440px */
    const tray = layTray(); if (!$("v4AutoLayout")) [["v4AutoLayout", "Auto layout", "ELK auto layout: no overlaps, loops drawn as loops, lines routed around boxes (Alt+G)", ACTIONS.autolayout],
     ["v4AutoUndo", "Undo layout", "Undo the last auto layout (Alt+Shift+Z)", ACTIONS.undolayout]].forEach(a => tray.insertBefore(mk(...a), tray.firstChild));
  }
  build();

  window.addEventListener("keydown", ev => {
    const t = ev.target && ev.target.tagName; if (t === "INPUT" || t === "TEXTAREA" || t === "SELECT") return;
    if (!ev.altKey || ev.ctrlKey || ev.metaKey) return;
    const k = ev.key.toLowerCase();
    if (k === "t") { ev.preventDefault(); ACTIONS.tidy() }
    else if (k === "g") { ev.preventDefault(); ACTIONS.autolayout() }
    else if (k === "z" && ev.shiftKey) { ev.preventDefault(); ACTIONS.undolayout() }
    else if (k === "z") { ev.preventDefault(); ACTIONS.undo() }
    else if (k === "l") { ev.preventDefault(); if (ev.shiftKey) ACTIONS.expandAll(); else ACTIONS.collapse() }
  });
  window.wbToolbar = { setSaveLabel: b => { const e = $("bSave"); if (e) e.title = b ? "Behaviour edits are unsaved: Save, then request a deploy. Visual edits autosave." : "Visual edits autosave; Save is for behaviour edits." }, actions: ACTIONS, shortcuts: { "Alt+T": "tidy", "Alt+Z": "undo tidy", "Alt+L": "collapse/expand selected lane", "Alt+Shift+L": "expand all" } };
})();
