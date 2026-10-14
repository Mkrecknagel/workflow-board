/* workflow-board v4 history (F-B7): list boards/.history snapshots, diff against the current board (added/removed/changed card ids;
   pos/size/saved_at ignored), restore. Restore = fetch the snapshot and PUT it to the EXISTING /board/{name} (validated, backed up by
   the server's backup_and_write). It never touches boards/active.json; a restored board shows the dirty pill until re-deployed.
   Also exposes wbHistory.view(doc,label): a read-only viewer used by runs.js "open board as of this run". textContent only. */
(function () {
  const W = window.wb;
  if (!W) return;
  const E = (t, c, x) => { const e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e };
  const say = t => { const s = document.getElementById("msg"); if (s) { s.textContent = t; s.title = t } else console.info(t) };
  let panel = null;
  const base = "position:fixed;left:12px;bottom:48px;width:380px;max-height:55vh;overflow:auto;z-index:70;background:#1b1b1b;color:#eee;border:1px solid #666;border-radius:8px;padding:10px;font:12px system-ui,sans-serif";
  function close() { if (panel) { panel.remove(); panel = null } }
  function shell(title) {
    close();
    panel = E("div", "v4-history-panel"); panel.style.cssText = base;
    const x = E("button", null, "close"); x.type = "button"; x.style.float = "right"; x.addEventListener("click", close);
    const h = E("div", null, title); h.style.fontWeight = "600";
    panel.append(x, h); document.body.appendChild(panel);
    return panel;
  }
  const fmt = iso => iso ? iso.replace("T", " ").slice(0, 19) + "Z" : "?";
  function lines(p, label, ids) {
    const d = E("div"); d.style.marginTop = "4px";
    d.appendChild(E("div", null, label + ": " + (ids.length ? ids.join(", ") : "none")));
    p.appendChild(d);
  }
  async function showDiff(b, sid, host) {
    let j = null;
    try { const r = await fetch("/history/" + encodeURIComponent(b) + "/" + encodeURIComponent(sid) + "?diff=current", { cache: "no-store" }); j = r.ok ? await r.json() : null } catch (e) { j = null }
    const box = E("div"); box.style.cssText = "margin-top:6px;padding:6px;background:#111;border-radius:4px";
    if (!j) box.textContent = "diff unavailable";
    else {
      box.appendChild(E("div", null, "Changes from this snapshot to the current board" + (j.same_semantics ? " - none (layout-only differences ignored)" : "")));
      lines(box, "added", j.added); lines(box, "removed", j.removed); lines(box, "changed", j.changed);
      if (j.board_fields_changed.length) lines(box, "board fields", j.board_fields_changed);
      if (j.edges_changed) box.appendChild(E("div", null, "edges differ"));
    }
    const old = host.querySelector(".v4-diff"); if (old) old.remove();
    box.classList.add("v4-diff"); host.appendChild(box);
  }
  async function restore(b, sid) {
    if (!window.confirm("Restore " + b + " from snapshot " + sid + "? The current file is backed up first. The deployed (active) board is not changed.")) return;
    let raw; try { const r = await fetch("/history/" + encodeURIComponent(b) + "/" + encodeURIComponent(sid), { cache: "no-store" }); if (!r.ok) throw 0; raw = await r.text() } catch (e) { say("restore failed: snapshot unreadable", "err"); return }
    let r; try { r = await fetch("/board/" + encodeURIComponent(b), { method: "PUT", headers: { "Content-Type": "application/json", "X-WB-Source": "canvas" }, body: raw }) } catch (e) { r = null }
    if (!r || !r.ok) { let m = "restore refused"; try { m += ": " + (await r.json()).error } catch (e) { /* keep */ } say(m, "err"); return }
    say("restored " + b + " from " + sid + " (re-deploy to activate)");
    close();
    if (W.openBoard) await W.openBoard(b);
  }
  async function open() {
    const b = W.board && W.board.board; if (!b) return;
    const p = shell("History: " + b);
    let j = null;
    try { const r = await fetch("/history/" + encodeURIComponent(b), { cache: "no-store" }); j = r.ok ? await r.json() : null } catch (e) { j = null }
    if (!j || !j.snapshots.length) { p.appendChild(E("div", null, "No snapshots yet.")); return }
    j.snapshots.forEach(s => {
      const row = E("div"); row.style.cssText = "margin-top:8px;border-top:1px solid #333;padding-top:6px";
      row.appendChild(E("div", null, fmt(s.iso) + " · " + s.sha8 + " · " + Math.round(s.bytes / 1024) + " KB"));
      const d = E("button", null, "diff vs current"); d.type = "button"; d.addEventListener("click", () => showDiff(b, s.id, row));
      const r = E("button", null, "restore"); r.type = "button"; r.style.marginLeft = "6px"; r.addEventListener("click", () => restore(b, s.id));
      row.append(d, r); p.appendChild(row);
    });
  }
  /* read-only viewer for a historical board document */
  function view(doc, label) {
    const p = shell(label || "board snapshot");
    const cards = (doc && doc.cards) || [];
    p.appendChild(E("div", null, cards.length + " cards, " + ((doc && doc.edges) || []).length + " edges (read-only view; nothing was loaded or saved)"));
    const ul = E("div"); ul.style.cssText = "margin-top:6px;max-height:36vh;overflow:auto";
    cards.forEach(c => ul.appendChild(E("div", null, c.id + " · " + c.type + (c.enabled === false ? " (disabled)" : ""))));
    p.appendChild(ul);
  }
  const btn = E("button", "v4-history-btn", "History"); btn.type = "button";
  btn.style.cssText = "position:fixed;left:12px;bottom:12px;z-index:61;font:12px system-ui,sans-serif;padding:3px 10px;margin-bottom:28px;border-radius:12px;border:1px solid #666;background:#1d1d1d;color:#eee;cursor:pointer";
  btn.addEventListener("click", open);
  document.body.appendChild(btn);
  window.wbHistory = { open, view, restore, close };
})();
