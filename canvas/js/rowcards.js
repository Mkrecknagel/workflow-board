"use strict";
/* Grouped tool cards (module): a card with `rows` (one per registered entry of one tool: Slack, SharePoint, Code...) is
   drawn as a list. Each row has its label (click: open its board when it has one, else its link), an "open" link,
   a drag handle and a role select. Dragging a row into the other lane, or picking a role, goes through the same
   project.json proposal flow as single cards (projroles.js). The first ROWS_SHOWN rows show; "show all" expands the card.
   Read-only decoration: the board is never edited here. All text via textContent. */
(function () {
  const W = window.wb, K = window.wbKinds;
  if (!W || !K || !K.registerDecorator) return;
  const ROWS_SHOWN = 5;
  const E = (t, c, x) => { const e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e };
  const SAFE = /^(https?:\/\/|\/(kb|report)[/?])/i;
  const expanded = new Set();
  const CATS = ["sharepoint", "slack", "wiki", "taskei", "harmony"];
  const OPTIONS = CATS.map(c => ["input:" + c, "Input: " + c]).concat([["output", "Output: artifact"], ["built", "Output: skill this project built"],
    ["uses", "Workflow skill this project uses"]]);
  const LANE_ROLE = { "Inputs": "input", "Outputs": "output", "Project workflows": "uses" };
  const roleOf = r => r.role === "input" ? "input:" + (r.category || "sharepoint") : r.role;
  const pseudo = (c, r) => ({ id: c.id, row: r.id, label: r.label, fields: { role: r.role, category: r.category, pj_key: r.pj_key } });
  const stop = ev => ev.stopPropagation();

  function relayout() { if (W.drawGroups) W.drawGroups(); if (W.updateEdges) W.updateEdges() }

  function openRow(c, r) {
    if (r.drill && window.wbZoom && window.wbZoom.navigate) {
      const st = (window.wbZoom.stackFull || []).concat([{ board: W.boardName, card: c.id }]);
      window.wbZoom.navigate(r.drill, { stack: st, push: true }); return;
    }
    const href = r.open || r.url;
    if (href && SAFE.test(href)) window.open(href, "_blank", "noopener,noreferrer");
  }

  /* drag a row: a ghost follows the pointer; dropping inside another role lane proposes the move */
  function startDrag(ev, c, r) {
    ev.preventDefault(); ev.stopPropagation();
    const g = E("div", "wb-row-ghost", r.label);
    g.style.cssText = "position:fixed;z-index:90;pointer-events:none;padding:2px 8px;border-radius:6px;background:#222;color:#fff;font:11px system-ui,sans-serif;opacity:.9";
    document.body.appendChild(g);
    const mv = e => { g.style.left = (e.clientX + 8) + "px"; g.style.top = (e.clientY + 8) + "px" };
    mv(ev);
    const up = e => {
      window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up); g.remove();
      const vp = document.getElementById("vp"); if (!vp) return;
      const b = vp.getBoundingClientRect(), v = W.view, x = (e.clientX - b.left - v.x) / v.k, y = (e.clientY - b.top - v.y) / v.k;
      for (const lane of Object.keys(LANE_ROLE)) {
        if (lane === c.group) continue;
        const rr = W.groupRect(lane); if (!rr) continue;
        if (x >= rr.x && x <= rr.x + rr.w && y >= rr.y && y <= rr.y + rr.h) {
          let role = LANE_ROLE[lane];
          if (role === "input") role = "input:" + (r.category || "sharepoint");
          if (window.wbRoles) window.wbRoles.open(pseudo(c, r), role);
          return;
        }
      }
    };
    window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up);
  }

  K.registerDecorator((el, c) => {
    const old = el.querySelector(":scope > .wb-rows"); if (old) old.remove();
    if (!Array.isArray(c.rows)) return;
    const box = E("div", "wb-rows");
    box.style.cssText = "margin:2px 4px 4px;font:11px system-ui,sans-serif";
    box.addEventListener("pointerdown", stop); box.addEventListener("dblclick", stop);
    const all = expanded.has(c.id), show = all ? c.rows : c.rows.slice(0, ROWS_SHOWN);
    show.forEach(r => {
      const row = E("div", "wb-row"); row.dataset.row = r.id;
      row.style.cssText = "display:flex;align-items:center;gap:4px;padding:2px 0;border-top:1px solid #0001";
      const h = E("span", "wb-row-drag", "\u22ee\u22ee"); h.title = "Drag to another lane to change its role";
      h.style.cssText = "cursor:grab;color:#888;user-select:none;padding:0 2px";
      const editable = r.pj_key && r.role !== "report";
      if (editable) h.addEventListener("pointerdown", ev => startDrag(ev, c, r)); else h.style.visibility = "hidden";
      const lab = E("span", "wb-row-label", r.label + (r.drill ? " \u25b8" : ""));
      lab.title = (r.drill ? "Open its board. " : "") + (r.url || r.open || "") + (r.src_anchor ? "  (" + r.src_anchor + ")" : "");
      lab.style.cssText = "flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;cursor:" + (r.drill || r.open || r.url ? "pointer" : "default");
      lab.addEventListener("click", () => openRow(c, r));
      row.append(h, lab);
      const href = r.open || r.url;
      if (href && SAFE.test(href)) {
        const a = E("a", null, "\u2197"); a.href = href; a.target = "_blank"; a.rel = "noopener noreferrer"; a.title = "Open " + href; row.appendChild(a);
      }
      if (editable) {
        const s = E("select", "wb-row-role"); s.title = "Change role"; s.setAttribute("aria-label", "Change role of " + r.label);
        s.style.cssText = "width:18px;padding:0;border:0;background:transparent;cursor:pointer";
        const ph = E("option", null, "\u25be"); ph.value = ""; s.appendChild(ph);
        OPTIONS.forEach(([v, t]) => { if (v !== roleOf(r)) { const o = E("option", null, t); o.value = v; s.appendChild(o) } });
        s.addEventListener("change", () => { if (s.value && window.wbRoles) window.wbRoles.open(pseudo(c, r), s.value); s.value = "" });
        row.appendChild(s);
      }
      box.appendChild(row);
    });
    if (c.rows.length > ROWS_SHOWN) {
      const more = E("button", "wb-row-more", all ? "\u25be show fewer" : "\u25b8 show all " + c.rows.length);
      more.type = "button"; more.style.cssText = "margin-top:2px;font:10px system-ui,sans-serif;border:0;background:none;color:#2563eb;cursor:pointer;padding:0";
      more.addEventListener("click", ev => { ev.stopPropagation(); if (all) expanded.delete(c.id); else expanded.add(c.id); K.decorateAll(); relayout() });
      box.appendChild(more);
    }
    const hd = el.querySelector(":scope > .hd");
    if (hd && hd.nextSibling) el.insertBefore(box, hd.nextSibling); else el.appendChild(box);
  });
  if (K.decorateAll) { K.decorateAll(); relayout() }
  window.wbRows = { expanded };
})();
