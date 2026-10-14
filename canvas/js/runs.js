/* workflow-board v4 last-run overlay (F-B1) + open-run-on-board (F-B9). Read-only: GET /runs/{board}. All text via textContent.
   Each card gets a small chip "last run <age> (N)". Clicking opens a panel with the last 5 rows for that card; "open board as of this
   run" loads the board version recorded for the run (history snapshot) read-only into a viewer, or says "board changed since". */
(function () {
  const W = window.wb, K = window.wbKinds;
  if (!W) return;
  const E = (t, c, x) => { const e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e };
  let sum = {}, sumBoard = "";
  const age = iso => { const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000); if (!(s >= 0)) return "?"; return s < 90 ? Math.round(s) + "s" : s < 5400 ? Math.round(s / 60) + "m" : s < 129600 ? Math.round(s / 3600) + "h" : Math.round(s / 86400) + "d" };
  function say(t) { const s = document.getElementById("msg"); if (s) { s.textContent = t; s.title = t } else console.info(t) }
  async function refresh() {
    const b = W.board && W.board.board; if (!b) return;
    try { const r = await fetch("/runs/" + encodeURIComponent(b) + "?summary=1", { cache: "no-store" }); sum = r.ok ? (await r.json()).last || {} : {}; sumBoard = b } catch (e) { sum = {} }
    if (K && K.decorateAll) K.decorateAll();
  }
  function chip(el, card) {
    const old = el.querySelector(":scope > .v4-lastrun"); if (old) old.remove();
    const s = sumBoard === (W.board && W.board.board) ? sum[card.id] : null;
    const c = E("button", "v4-lastrun", s ? "last run " + age(s.ts) + " (" + s.count + ")" : "never run");
    c.type = "button"; c.title = s ? s.event + " at " + s.ts : "no telemetry rows for this card";
    c.style.cssText = "position:absolute;right:4px;bottom:-9px;font:10px system-ui,sans-serif;padding:0 6px;border-radius:8px;border:1px solid #666;background:#1d1d1d;color:" + (s ? "#cfe8cf" : "#999") + ";cursor:pointer;z-index:2";
    c.addEventListener("click", ev => { ev.stopPropagation(); openPanel(card) });
    c.addEventListener("pointerdown", ev => ev.stopPropagation());
    el.appendChild(c);
  }
  let panel = null;
  function closePanel() { if (panel) { panel.remove(); panel = null } }
  async function openPanel(card) {
    closePanel();
    const b = W.board.board;
    panel = E("div", "v4-runs-panel");
    panel.style.cssText = "position:fixed;right:12px;bottom:48px;width:360px;max-height:50vh;overflow:auto;z-index:70;background:#1b1b1b;color:#eee;border:1px solid #666;border-radius:8px;padding:10px;font:12px system-ui,sans-serif";
    const head = E("div", null, "Runs for " + card.id); head.style.fontWeight = "600";
    const x = E("button", null, "close"); x.type = "button"; x.style.cssText = "float:right"; x.addEventListener("click", closePanel);
    panel.append(x, head);
    document.body.appendChild(panel);
    let j = null;
    try { const r = await fetch("/runs/" + encodeURIComponent(b) + "?card=" + encodeURIComponent(card.id), { cache: "no-store" }); j = r.ok ? await r.json() : null } catch (e) { j = null }
    if (!j || !j.rows || !j.rows.length) { panel.appendChild(E("div", null, "No runs recorded.")); return }
    j.rows.forEach(row => {
      const d = E("div"); d.style.cssText = "margin-top:8px;border-top:1px solid #333;padding-top:6px";
      d.appendChild(E("div", null, row.event + " · " + age(row.ts) + " ago"));
      d.appendChild(E("div", null, row.ts + (row.subagentType ? " · " + row.subagentType : "")));
      d.appendChild(E("div", null, row.snapshot_note + (row.snapshot_id !== "current" ? " (snapshot " + row.snapshot_id.slice(-8) + ")" : "")));
      const ob = E("button", null, "open board as of this run"); ob.type = "button";
      ob.addEventListener("click", () => openRun(row));
      d.appendChild(ob); panel.appendChild(d);
    });
  }
  /* F-B9: show the recorded board version. Nothing is saved or replaced on disk. */
  async function openRun(row) {
    const b = W.board.board;
    if (row.snapshot_id === "current") { say("this run used the board as it is now"); return }
    let r; try { r = await fetch("/history/" + encodeURIComponent(b) + "/" + encodeURIComponent(row.snapshot_id), { cache: "no-store" }) } catch (e) { r = null }
    if (!r || !r.ok) { say("recorded board version not found - board changed since"); return }
    const doc = await r.json();
    const label = "board as of run " + row.ts + (row.board_changed_since ? " (board changed since)" : "");
    if (window.wbHistory && window.wbHistory.view) window.wbHistory.view(doc, label);
    else say("history module not loaded");
  }
  if (K && K.registerDecorator) K.registerDecorator(chip);
  window.wbRuns = { refresh, openPanel, openRun };
  refresh(); setInterval(refresh, 15000);
})();
