"use strict";
/* Project roles (module): change what a project.json entry IS (input source, output artifact, built skill, workflow
   skill) from the board, proposal first. Two ways in, one flow:
     - a "Role" select in the inspector for cards that carry fields.pj_key (and suggested skills)
     - dragging such a card into another lane (Inputs / Outputs / Project workflows)
   Either opens a panel: Propose -> shows the project.json diff -> Approve. The server writes project.json (backup kept)
   and, when WB_REFRESH_CMD is set, regenerates the boards; the page then reloads the board. The board itself is never
   edited here, so a generated board stays regenerable. Cancel puts a dragged card back. All text via textContent. */
(function () {
  const W = window.wb;
  if (!W) return;
  const E = (t, c, x) => { const e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e };
  const CATS = ["sharepoint", "slack", "wiki", "taskei", "harmony"];
  const OPTIONS = CATS.map(c => ["input:" + c, "Input: " + c]).concat([["output", "Output: artifact"], ["built", "Output: skill this project built"],
    ["uses", "Workflow skill this project uses"]]);
  const LANE_ROLE = { "Inputs": "input", "Outputs": "output", "Project workflows": "uses" };
  const ROLE_LABEL = { input: "Input", output: "Output", built: "Built skill", uses: "Workflow skill", suggested: "Suggested (not registered)" };
  const editable = c => c && c.fields && (c.fields.pj_key || c.fields.role === "suggested") && ROLE_LABEL[c.fields.role];
  const current = c => c.fields.role === "input" ? "input:" + (c.fields.category || "sharepoint") : c.fields.role;

  async function post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let j = null; try { j = await r.json() } catch (_) { }
    return { ok: r.ok, status: r.status, j };
  }

  let panel = null;
  function close() { if (panel) { panel.remove(); panel = null } }
  function open(c, role, onCancel) {
    close();
    panel = E("div", "wb-roles");
    panel.setAttribute("role", "dialog"); panel.setAttribute("aria-label", "Change role in project.json");
    panel.style.cssText = "position:fixed;left:50%;top:64px;transform:translateX(-50%);width:min(760px,92vw);max-height:80vh;overflow:auto;z-index:80;"
      + "background:#fff;color:#111;border:1px solid #888;border-radius:10px;padding:12px;font:13px system-ui,sans-serif;box-shadow:0 6px 24px #0004";
    panel.appendChild(E("div", null, "Change what \u201c" + c.label + "\u201d is for this project"));
    const note = E("div", null, "This edits the project's project.json (a backup is kept). Nothing changes until you approve.");
    note.style.cssText = "font-size:11px;color:#555;margin:4px 0 8px"; panel.appendChild(note);
    const sel = E("select"); sel.setAttribute("aria-label", "New role");
    const opts = c.fields.role === "suggested" ? OPTIONS.filter(o => o[0] === "uses" || o[0] === "built") : OPTIONS;
    opts.forEach(([v, t]) => { const o = E("option", null, t); o.value = v; sel.appendChild(o) });
    sel.value = role && opts.some(o => o[0] === role) ? role : opts[0][0];
    const pb = E("button", null, "Propose"), ab = E("button", "pri", "Approve"), cb = E("button", null, "Cancel");
    ab.style.display = "none";
    const pre = E("pre"); pre.style.cssText = "white-space:pre-wrap;font:11px ui-monospace,monospace;max-height:46vh;overflow:auto;background:#f6f6f6;padding:6px;display:none";
    const row = E("div"); row.style.cssText = "display:flex;gap:6px;align-items:center;flex-wrap:wrap";
    row.append(E("label", null, "New role"), sel, pb, ab, cb); panel.append(row, pre);
    document.body.appendChild(panel); sel.focus();
    let pid = null;
    cb.onclick = () => { close(); if (onCancel) onCancel() };
    sel.onchange = () => { ab.style.display = "none"; pid = null };
    pb.onclick = async () => {
      if (sel.value === current(c)) { pre.style.display = "block"; pre.textContent = "That is already its role."; return }
      pre.style.display = "block"; pre.textContent = "proposing...";
      const r = await post("/project/propose", Object.assign({ board: W.boardName, card: c.id, role: sel.value }, c.row ? { row: c.row } : {}));
      if (!r.ok) { pre.textContent = "Not proposed: " + ((r.j && r.j.error) || r.status); return }
      pid = r.j.plan.id; pre.textContent = r.j.diff; ab.style.display = "";
    };
    ab.onclick = async () => {
      if (!pid) return;
      const r = await post("/project/apply", { id: pid, approve: true });
      if (!r.ok) { pre.textContent += "\n\nNot applied: " + ((r.j && r.j.error) || r.status); return }
      if (r.j.refreshed) { close(); await W.openBoard(W.boardName); return }
      pre.textContent += "\n\nApplied to project.json (backup: " + r.j.plan.backup + "). Run refresh.sh to update the boards."
        + (r.j.refresh_output ? "\n" + r.j.refresh_output : "");
      ab.style.display = "none"; cb.textContent = "Close"; cb.onclick = () => { close(); if (onCancel) onCancel() };
    };
  }

  /* inspector: a Role select under the card's fields */
  function inspector() {
    const b = document.getElementById("inspBody"); if (!b || !W.board || W.sel.size !== 1) return;
    const c = W.board.cards.find(x => x.id === [...W.sel][0]); if (!editable(c)) return;
    if (b.querySelector(".wb-role-row")) return;
    const r = E("div", "wb-role-row"); r.style.cssText = "margin:8px 0;padding:6px;border:1px solid #ccc;border-radius:6px";
    r.appendChild(E("div", null, "Role: " + ROLE_LABEL[c.fields.role]));
    const s = E("select"); s.setAttribute("aria-label", "Change role");
    const ph = E("option", null, "Change role\u2026"); ph.value = ""; s.appendChild(ph);
    (c.fields.role === "suggested" ? OPTIONS.filter(o => o[0] === "uses" || o[0] === "built") : OPTIONS)
      .forEach(([v, t]) => { if (v !== current(c)) { const o = E("option", null, t); o.value = v; s.appendChild(o) } });
    s.onchange = () => { if (s.value) open(c, s.value); s.value = "" };
    r.appendChild(s); b.prepend(r);
  }
  const ib = document.getElementById("inspBody");
  if (ib) new MutationObserver(() => inspector()).observe(ib, { childList: true });

  /* drag into another lane */
  let down = null;
  document.addEventListener("pointerdown", ev => {
    const el = ev.target.closest && ev.target.closest("#cards > .card[data-id]"); if (!el || !W.board) { down = null; return }
    const c = W.board.cards.find(x => x.id === el.dataset.id);
    down = editable(c) ? { c, x: c.pos.x, y: c.pos.y } : null;
  }, true);
  window.addEventListener("pointerup", () => {
    const d = down; down = null; if (!d) return;
    setTimeout(() => {
      const c = d.c; if (c.pos.x === d.x && c.pos.y === d.y) return;
      const cx = c.pos.x + W.CW / 2, cy = c.pos.y + 40;
      for (const lane of Object.keys(LANE_ROLE)) {
        if (lane === c.group) continue;
        const r = W.groupRect(lane); if (!r) continue;
        if (cx >= r.x && cx <= r.x + r.w && cy >= r.y && cy <= r.y + r.h) {
          let role = LANE_ROLE[lane];
          if (role === "input") role = "input:" + (c.fields.category || "sharepoint");
          if (role === "output" && c.type === "skill") role = "built";
          if (current(c) === role) return;
          const back = () => { c.pos.x = d.x; c.pos.y = d.y; W.render() };
          open(c, role, back);
          return;
        }
      }
    }, 0);
  });
  window.wbRoles = { open };
})();
