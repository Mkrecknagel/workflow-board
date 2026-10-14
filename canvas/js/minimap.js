"use strict";
/* workflow-board v5 F1/G1: minimap (module). Charge C navigation = breadcrumb + zoom % (nested.js label) + MINIMAP.
   A small overview of the ACTIVE board: one rect per card, an outline for the visible viewport, tap / click / drag to jump (the point you touch becomes
   the centre of the view). Works at phone width (smaller, lifted above the bottom tool strip), hide / show with the Map button (state kept in localStorage,
   wrapped in try/catch). Read-only: it only calls wb.setView, never edits a board.
   Pure, node-testable: window.wbMinimapPure {layout, viewportRect, toWorld, centreView}. Runtime: window.wbMinimap {visible, show, hide, toggle, redraw, jump}. */
(function () {
  const root = typeof window !== "undefined" ? window : globalThis;
  const CW = 192, MH = 90;
  function bbox(cards) {
    if (!cards || !cards.length) return { x: 0, y: 0, w: 400, h: 240 };
    let x0 = 1e18, y0 = 1e18, x1 = -1e18, y1 = -1e18;
    for (const c of cards) { const p = c.pos || { x: 0, y: 0 }, w = (c.size && c.size.w) || CW, h = (c.size && c.size.h) || MH; x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y); x1 = Math.max(x1, p.x + w); y1 = Math.max(y1, p.y + h) }
    return { x: x0, y: y0, w: Math.max(1, x1 - x0), h: Math.max(1, y1 - y0) };
  }
  /* world rect currently visible in a vp of size (w,h) with view {k,x,y}: screen = k*world + (x,y) */
  const viewportRect = (view, vp) => ({ x: -view.x / view.k, y: -view.y / view.k, w: vp.w / view.k, h: vp.h / view.k });
  const union = (a, b) => { const x0 = Math.min(a.x, b.x), y0 = Math.min(a.y, b.y), x1 = Math.max(a.x + a.w, b.x + b.w), y1 = Math.max(a.y + a.h, b.y + b.h); return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 } };
  /* the minimap covers the cards' bbox united with the viewport (so the viewport rectangle is always inside the map), padded; fitted into (mw,mh) */
  function layout(cards, view, vp, mw, mh) {
    const vr = viewportRect(view, vp), u = union(bbox(cards), vr), pad = Math.max(u.w, u.h) * 0.04;
    const b = { x: u.x - pad, y: u.y - pad, w: u.w + 2 * pad, h: u.h + 2 * pad }, s = Math.min(mw / b.w, mh / b.h);
    return { s, ox: (mw - b.w * s) / 2 - b.x * s, oy: (mh - b.h * s) / 2 - b.y * s, bounds: b };
  }
  const toWorld = (L, mx, my) => ({ x: (mx - L.ox) / L.s, y: (my - L.oy) / L.s });
  const centreView = (view, vp, wx, wy) => ({ k: view.k, x: vp.w / 2 - wx * view.k, y: vp.h / 2 - wy * view.k });
  root.wbMinimapPure = { bbox, viewportRect, layout, toWorld, centreView };

  if (typeof document === "undefined" || !document.getElementById || !document.getElementById("vp")) return;
  const $ = id => document.getElementById(id), W = () => root.wb;
  const NS = "http://www.w3.org/2000/svg";
  const E = (t, id, txt) => { const e = document.createElement(t); if (id) e.id = id; if (txt != null) e.textContent = txt; return e };
  const LS = "wb-minimap-hidden";
  const lsGet = () => { try { return localStorage.getItem(LS) } catch (e) { return null } };
  const lsSet = v => { try { localStorage.setItem(LS, v) } catch (e) { } };
  let hidden = lsGet() === "1", last = "", cur = null;

  const st = E("style"); st.textContent =
    "#wb-minimap{position:absolute;right:10px;bottom:10px;z-index:7;width:168px;height:112px;background:var(--panel);border:1px solid var(--line);border-radius:8px;box-shadow:0 2px 10px #0004;overflow:hidden;touch-action:none;cursor:crosshair}" +
    "#wb-minimap svg{display:block;width:100%;height:100%}#wb-minimap .mm-c{fill:var(--edge);opacity:.75}#wb-minimap .mm-v{fill:color-mix(in srgb,var(--sel) 16%,transparent);stroke:var(--sel);stroke-width:2;vector-effect:non-scaling-stroke}" +
    "#wb-minimap-t{position:absolute;right:10px;bottom:10px;z-index:7;padding:3px 10px;font-size:12px;border-radius:12px;min-height:28px}#wb-minimap-t.up{bottom:130px}" +
    "#wb-minimap-x{position:absolute;right:2px;top:2px;z-index:2;padding:0 6px;font-size:12px;line-height:18px;border-radius:9px;min-height:0;opacity:.85}" +
    "@media (max-width:699px){#wb-minimap{width:112px;height:76px;right:8px;bottom:128px}#wb-minimap-t{bottom:128px;min-height:40px;min-width:44px}#wb-minimap-t.up{bottom:212px}#wb-minimap-x{min-width:40px;min-height:40px;top:0;right:0}}";
  document.head.appendChild(st);

  const box = E("div", "wb-minimap"); box.setAttribute("role", "img"); box.setAttribute("aria-label", "Board minimap: tap to jump");
  const svg = document.createElementNS(NS, "svg"); svg.setAttribute("preserveAspectRatio", "none"); box.appendChild(svg);
  const x = E("button", "wb-minimap-x", "×"); x.type = "button"; x.title = "Hide the minimap"; x.setAttribute("aria-label", "Hide the minimap"); box.appendChild(x);
  const tg = E("button", "wb-minimap-t", "Map"); tg.type = "button"; tg.title = "Show the minimap"; tg.setAttribute("aria-label", "Show the minimap");
  $("vp").appendChild(box); $("vp").appendChild(tg);

  const vpSize = () => { const r = $("vp").getBoundingClientRect(); return { w: r.width || 1, h: r.height || 1 } };
  function setHidden(h) { hidden = h; lsSet(h ? "1" : "0"); box.style.display = h ? "none" : "block"; tg.style.display = h ? "block" : "none"; last = ""; if (!h) redraw() }
  function redraw() {
    const w = W(); if (hidden || !w || !w.board) return;
    const vp = vpSize(), mw = box.clientWidth || 168, mh = box.clientHeight || 112, cards = (w.board.cards || []).filter(c => c && c.pos);
    const key = [w.boardName, w.view.k, w.view.x, w.view.y, vp.w, vp.h, mw, cards.map(c => c.id + ":" + c.pos.x + "," + c.pos.y + (c.size ? "," + c.size.w + "x" + c.size.h : "")).join(";")].join("|");
    if (key === last) return; last = key;
    const L = cur = layout(cards, w.view, vp, mw, mh), vr = viewportRect(w.view, vp);
    svg.setAttribute("viewBox", "0 0 " + mw + " " + mh); svg.setAttribute("width", mw); svg.setAttribute("height", mh);
    let h = "";
    for (const c of cards) { const g = c.size ? c.size : { w: CW, h: MH }; h += '<rect class="mm-c" data-id="' + String(c.id).replace(/"/g, "") + '" x="' + (c.pos.x * L.s + L.ox).toFixed(1) + '" y="' + (c.pos.y * L.s + L.oy).toFixed(1) + '" width="' + Math.max(2, g.w * L.s).toFixed(1) + '" height="' + Math.max(2, g.h * L.s).toFixed(1) + '"/>' }
    h += '<rect class="mm-v" id="wb-minimap-vp" x="' + (vr.x * L.s + L.ox).toFixed(1) + '" y="' + (vr.y * L.s + L.oy).toFixed(1) + '" width="' + (vr.w * L.s).toFixed(1) + '" height="' + (vr.h * L.s).toFixed(1) + '"/>';
    svg.innerHTML = h;
  }
  function jumpTo(ev) {
    const w = W(); if (!w || !w.board || !cur) return false;
    const r = box.getBoundingClientRect(), p = toWorld(cur, ev.clientX - r.left, ev.clientY - r.top);
    w.setView(centreView(w.view, vpSize(), p.x, p.y)); last = ""; redraw(); return true;
  }
  let down = false;
  box.addEventListener("pointerdown", ev => { if (ev.target === x) return; ev.stopPropagation(); ev.preventDefault(); down = true; try { box.setPointerCapture(ev.pointerId) } catch (e) { } jumpTo(ev) });
  box.addEventListener("pointermove", ev => { if (down) { ev.stopPropagation(); jumpTo(ev) } });
  const up = ev => { down = false; ev.stopPropagation() };
  box.addEventListener("pointerup", up); box.addEventListener("pointercancel", up);
  box.addEventListener("wheel", ev => ev.stopPropagation(), { passive: true });
  x.addEventListener("pointerdown", ev => ev.stopPropagation()); tg.addEventListener("pointerdown", ev => ev.stopPropagation());
  x.addEventListener("click", ev => { ev.stopPropagation(); setHidden(true) }); tg.addEventListener("click", ev => { ev.stopPropagation(); setHidden(false) });

  /* nudge the Map toggle above the nested label zone is unnecessary; the toggle is a corner pill, the map itself keeps its corner */
  root.wbMinimap = { get visible() { return !hidden }, show: () => setHidden(false), hide: () => setHidden(true), toggle: () => setHidden(!hidden), redraw: () => { last = ""; redraw() }, jump: (wx, wy) => { const w = W(); if (w) { w.setView(centreView(w.view, vpSize(), wx, wy)); last = ""; redraw() } }, get layout() { return cur } };
  setHidden(hidden); setInterval(redraw, 120);
})();
