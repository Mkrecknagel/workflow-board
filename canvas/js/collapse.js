"use strict";
/* workflow-board v4 collapse.js (N4, F-B5). Collapse / expand a lane (group).
   State lives ONLY in board.group_styles[group].collapsed (layout-only, hash-excluded); nothing else is stored anywhere (no browser storage, no extra card keys).
   A collapsed lane: member cards are hidden, the lane outline shrinks to a header + "exposed port" chips, and the core edges that cross the lane boundary are
   redrawn (overlay paths) to those chips. Exposed ports are computed from boundary-crossing edges only; edges fully inside the lane are hidden.
   Self-installing, reads window.wb only. Public API: window.wbCollapse. Board text goes in through textContent only. */
(function () {
  const W = () => window.wb, $ = id => document.getElementById(id);
  const NS = "http://www.w3.org/2000/svg", ROW = 22, HEAD = 44;
  const epCard = s => s.slice(0, s.lastIndexOf(".")), epPort = s => s.slice(s.lastIndexOf(".") + 1);
  const GR = () => (W() && W().GRID) || 8;
  const gridUp = n => Math.ceil(n / GR()) * GR();
  const E = (t, c, x) => { const e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e };
  const board = () => (W() ? W().board : null);
  const cardGroup = id => { const b = board(), c = b && b.cards.find(x => x.id === id); return c ? c.group : undefined };

  function isCollapsed(g) { const b = board(), s = b && b.group_styles && b.group_styles[g]; return !!(s && s.collapsed === true) }
  function groups() { const b = board(), n = []; if (b) b.cards.forEach(c => { if (c.group && !n.includes(c.group)) n.push(c.group) }); return n }
  function collapsedGroups() { return groups().filter(isCollapsed) }

  /* exposed ports of lane g = endpoints (inside g) of edges that cross the lane boundary */
  function exposed(g) {
    const b = board(), out = { in: [], out: [] }; if (!b) return out;
    b.edges.forEach(e => {
      const f = cardGroup(epCard(e.from)), t = cardGroup(epCard(e.to));
      if (f === g && t !== g && !out.out.includes(e.from)) out.out.push(e.from);
      if (t === g && f !== g && !out.in.includes(e.to)) out.in.push(e.to);
    });
    return out;
  }

  function set(g, v, noRender) {
    const b = board(); if (!b || !groups().includes(g)) return false;
    if (v) { b.group_styles = b.group_styles || {}; b.group_styles[g] = Object.assign({}, b.group_styles[g], { collapsed: true }) }
    else if (b.group_styles && b.group_styles[g]) {
      delete b.group_styles[g].collapsed;
      if (!Object.keys(b.group_styles[g]).length) delete b.group_styles[g];
      if (!Object.keys(b.group_styles).length) delete b.group_styles;
    }
    if (!noRender) W().render();
    return true;
  }
  function toggle(g) { return set(g, !isCollapsed(g)) }
  function selectedGroups() {
    const w = W(), b = board(), s = new Set();
    if (window.wbArrange && window.wbArrange.gsel) window.wbArrange.gsel.forEach(g => s.add(g));
    if (b && w.sel) b.cards.forEach(c => { if (c.group && w.sel.has(c.id)) s.add(c.group) });
    return [...s];
  }
  function toggleSelected() {
    const gs = selectedGroups(); if (!gs.length) return 0;
    const to = !gs.every(isCollapsed); gs.forEach(g => set(g, to, true)); W().render(); return gs.length;
  }
  function setAll(v) { const gs = groups(); gs.forEach(g => set(g, v, true)); W().render(); return gs.length }

  /* ---- DOM ---- */
  const px = (el, k) => parseFloat(el.style[k]);
  const chipY = (top, i) => top + HEAD + i * ROW + ROW / 2;
  const num = "-?[\\d.]+(?:e-?\\d+)?";
  const reStart = new RegExp("^M(" + num + "),(" + num + ")"), reEnd = new RegExp("(" + num + "),(" + num + ")$");

  function apply() {
    const w = W(), b = board(); if (!w || !b) return;
    document.querySelectorAll("#edges path.cx").forEach(p => p.remove());
    document.querySelectorAll(".card.cx-hid").forEach(c => c.classList.remove("cx-hid"));
    const col = collapsedGroups(), geo = {};
    /* toggle button on every lane header; collapsed lanes get the chip layout */
    document.querySelectorAll("#groups .grp").forEach(d => {
      const g = d.dataset.group;
      d.querySelectorAll(":scope>.cx-tg,:scope>.cx-chip").forEach(x => x.remove());
      const on = isCollapsed(g), btn = E("button", "cx-tg", on ? "▸" : "▾");
      btn.title = (on ? "Expand " : "Collapse ") + g; btn.dataset.cxToggle = g; btn.setAttribute("aria-label", btn.title);
      ["pointerdown", "pointerup", "dblclick"].forEach(t => btn.addEventListener(t, ev => ev.stopPropagation()));
      btn.addEventListener("click", ev => { ev.stopPropagation(); toggle(g) });
      d.appendChild(btn);
      d.classList.toggle("cx-collapsed", on);
      if (!on) return;
      const x = exposed(g), n = Math.max(x.in.length, x.out.length, 0), left = px(d, "left"), top = px(d, "top");
      const wd = Math.min(px(d, "width"), 288), h = gridUp(HEAD + n * ROW + 8);
      d.style.width = wd + "px"; d.style.height = h + "px";
      geo[g] = { left: left, top: top, w: wd, x: x };
      [["in", x.in], ["out", x.out]].forEach(([side, list]) => list.forEach((ep, i) => {
        const ch = E("span", "cx-chip " + side, epPort(ep)); ch.dataset.ep = ep; ch.dataset.side = side; ch.title = ep;
        ch.style.top = (HEAD + i * ROW) + "px"; d.appendChild(ch);
      }));
      b.cards.forEach(c => { if (c.group === g) { const el = document.querySelector('.card[data-id="' + c.id + '"]'); if (el) el.classList.add("cx-hid") } });
    });
    if (!col.length) return;
    /* edges: hide those touching a collapsed lane, re-draw boundary-crossing ones to chips */
    const paths = [...document.querySelectorAll("#edges path.e")];
    if (paths.length !== b.edges.length) return;       /* index mapping unsafe (an edge had no measurable port): leave core edges alone */
    const svg = $("edges");
    b.edges.forEach((e, i) => {
      const fg = cardGroup(epCard(e.from)), tg = cardGroup(epCard(e.to)), fcol = fg && geo[fg], tcol = tg && geo[tg];
      if (!fcol && !tcol) return;
      paths[i].style.display = "none";
      if (fg === tg) return;                           /* internal to a collapsed lane */
      const d = paths[i].getAttribute("d") || "", ms = reStart.exec(d), me = reEnd.exec(d); if (!ms || !me) return;
      const A = fcol ? { x: fcol.left + fcol.w, y: chipY(fcol.top, fcol.x.out.indexOf(e.from)) } : { x: +ms[1], y: +ms[2] };
      const B = tcol ? { x: tcol.left, y: chipY(tcol.top, tcol.x.in.indexOf(e.to)) } : { x: +me[1], y: +me[2] };
      const dx = Math.max(40, Math.abs(B.x - A.x) / 2), p = document.createElementNS(NS, "path");
      p.setAttribute("class", "cx"); p.setAttribute("marker-end", "url(#ah)"); p.dataset.from = e.from; p.dataset.to = e.to;
      p.setAttribute("d", "M" + A.x + "," + A.y + " C" + (A.x + dx) + "," + A.y + " " + (B.x - dx) + "," + B.y + " " + B.x + "," + B.y);
      svg.appendChild(p);
    });
  }

  let q = 0;
  function queue() { if (q) return; q = requestAnimationFrame(() => { q = 0; try { apply() } catch (err) { console.warn("collapse.js: " + err) } }) }
  const own = n => n.nodeType === 1 && n.classList.contains("cx");
  function boot() {
    const st = document.createElement("style");
    st.textContent = ".card.cx-hid{visibility:hidden!important;pointer-events:none!important}" +
      "#edges path.cx{fill:none;stroke:var(--edge);stroke-width:1.75;stroke-dasharray:6 3;pointer-events:none}" +
      ".grp .cx-tg{position:absolute;right:8px;top:6px;z-index:2;pointer-events:auto;touch-action:manipulation;min-width:28px;min-height:28px;padding:0 6px;line-height:1;cursor:pointer}" +
      ".grp.cx-collapsed{border-style:solid;background:color-mix(in srgb,var(--gc,#7b8499) 14%,var(--card))}" +
      ".grp .cx-chip{position:absolute;max-width:48%;height:" + ROW + "px;line-height:" + ROW + "px;font-size:11px;color:var(--mut);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;pointer-events:none}" +
      ".grp .cx-chip.in{left:-1px;padding-left:12px}.grp .cx-chip.out{right:-1px;padding-right:12px;text-align:right}" +
      ".grp .cx-chip::before{content:\"\";position:absolute;top:" + (ROW / 2 - 5) + "px;width:10px;height:10px;border-radius:50%;background:var(--card);border:2px solid var(--edge)}" +
      ".grp .cx-chip.in::before{left:-6px}.grp .cx-chip.out::before{right:-6px}" +
      "@media (max-width:699px){.grp .cx-tg{min-width:40px;min-height:40px;right:4px;top:2px}}";
    document.head.appendChild(st);
    const mo = new MutationObserver(recs => { if (recs.every(r => (r.type === "attributes" ? own(r.target) : [...r.addedNodes, ...r.removedNodes].every(own)))) return; queue() });
    ["cards", "groups"].forEach(id => { const e = $(id); if (e) mo.observe(e, { childList: true }) });
    const ed = $("edges"); if (ed) mo.observe(ed, { childList: true, subtree: true, attributes: true, attributeFilter: ["d"] });
    queue();
  }
  window.wbCollapse = { isCollapsed, set, toggle, toggleSelected, setAll, exposed, groups, collapsedGroups, apply, selectedGroups };
  boot();
})();
