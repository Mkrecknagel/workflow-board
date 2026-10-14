"use strict";
/* workflow-board phonebar.js (F4 E2). Phone (<=699px): the top bar keeps only Board picker, Save, Add, Inspect and a "More" button;
   every other control moves into a drop-down menu (#barMenu) so the bar never scrolls sideways. Controls keep their ids and handlers
   (they are MOVED, not cloned), and return to the bar in their original order when the window is wide again.
   Also raises touch targets to >= 40px on phone for toolbar / drawer / dialog buttons (card-internal buttons are left alone). */
(function () {
  const $ = id => document.getElementById(id);
  const bar = $("bar"); if (!bar || $("bMore")) return;
  const mq = window.matchMedia("(max-width:699px)");
  const KEEP = new Set(["picker", "bSave", "bPal", "bInsp", "bMore", "msg", "v4Tb"]);
  let ord = 0;

  const style = document.createElement("style");
  style.textContent = "#bMore{display:none}#barMenu{display:none}" +
    "@media (max-width:699px){#bar{overflow:visible}#bMore{display:inline-block}" +
    "#barMenu{position:fixed;top:44px;right:0;z-index:20;width:min(260px,85vw);max-height:calc(100vh - 100px);overflow:auto;flex-direction:column;gap:6px;padding:8px;background:var(--panel);border:1px solid var(--line);border-radius:0 0 0 10px;box-shadow:0 4px 16px #0005}" +
    "#barMenu .v4-history-btn{position:static!important;margin:0!important;border-radius:6px!important;padding:4px 8px!important;font-size:16px!important;text-align:left}#barMenu.open{display:flex}#barMenu>*{flex:0 0 auto}#barMenu button{width:100%;text-align:left}#barMenu #tkPill{font-size:12px;color:var(--mut);padding:4px 2px}" +
    "#bar>#picker,#bar>button{min-height:40px}button,select{min-height:40px}#world button,#world select{min-height:0}}";
  document.head.appendChild(style);

  const more = document.createElement("button"); more.id = "bMore"; more.type = "button"; more.textContent = "More"; more.title = "More controls";
  more.setAttribute("aria-haspopup", "true"); more.setAttribute("aria-expanded", "false");
  const menu = document.createElement("div"); menu.id = "barMenu"; menu.setAttribute("role", "menu"); menu.setAttribute("aria-label", "More controls");
  bar.insertBefore(more, $("msg") || null); document.body.appendChild(menu);

  const setOpen = o => { menu.classList.toggle("open", !!o); more.setAttribute("aria-expanded", o ? "true" : "false") };
  more.addEventListener("click", () => setOpen(!menu.classList.contains("open")));
  menu.addEventListener("click", e => { if (e.target.closest("button")) setTimeout(() => setOpen(false), 0) });
  document.addEventListener("pointerdown", e => { if (menu.classList.contains("open") && !menu.contains(e.target) && e.target !== more) setOpen(false) }, true);
  document.addEventListener("keydown", e => { if (e.key === "Escape") setOpen(false) }, true);

  function sync() {
    const phone = mq.matches;
    if (phone) {
      const hb = document.querySelector("body > .v4-history-btn"); if (hb) { hb.dataset.pbOrd = "9999"; hb.dataset.pbBody = "1"; menu.appendChild(hb) }
      [...bar.children].forEach(el => { if (el.tagName === "B" || KEEP.has(el.id)) return; if (el.dataset.pbOrd == null) el.dataset.pbOrd = String(ord++); menu.appendChild(el) });
    } else {
      setOpen(false);
      [...menu.children].sort((a, b) => (+a.dataset.pbOrd) - (+b.dataset.pbOrd)).forEach(el => { if (el.dataset.pbBody) document.body.appendChild(el); else bar.insertBefore(el, more) });
    }
  }
  /* controls added later by other modules land in #bar: re-sync on child changes (cheap: only when the set of direct children changes). */
  new MutationObserver(() => { if (mq.matches && [...bar.children].some(el => el.tagName !== "B" && !KEEP.has(el.id))) sync() }).observe(bar, { childList: true });
  new MutationObserver(() => { if (mq.matches && document.querySelector("body > .v4-history-btn")) sync() }).observe(document.body, { childList: true });
  (mq.addEventListener ? mq.addEventListener("change", sync) : mq.addListener(sync));
  sync();
  window.wbPhoneBar = { sync, menu };
})();
