"use strict";
/* workflow-board v4 connector containers (module, F-A). Loaded by the index.html v4 loader only when /v4/modules lists it.
   Fills `container` cards (ref.kind=connector) through wbKinds.registerDecorator: account, counts, tools, hooks, skills from GET /inventory.
   READ-ONLY by construction: tool rows that are `blocked` (everything outside the read/draft allow-list, incl. send_message, reply,
   forward, share_file, and every unknown tool) render with a lock glyph and NO button / draggable / data-cap attribute - nothing in
   the DOM offers to run them. Allowed rows get a "wire" button: wiring only edits the board (an edge from the container's
   out port `cap:<tool>` to a target card + target ref.via_container); nothing is ever executed. All text via textContent. */
(function () {
  const W = window.wb, K = window.wbKinds;
  if (!W || !K) { console.warn("containers.js: wb/wbKinds missing"); return; }
  const E = (t, c, x) => { const e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e };
  const say = (m, cls) => { const e = document.getElementById("msg"); if (e) { e.textContent = m; e.className = cls || ""; e.title = m } };
  const GLYPH = { blocked: "\u{1F512}", draft: "✎", write: "✎", read: "\u{1F441}" };
  let inv = null, loading = false, pending = null;

  function css() {
    const s = E("style");
    s.textContent = ".cx-body{margin-top:6px;font-size:10px;line-height:1.35;max-width:300px}.cx-acct{color:var(--mut);margin-bottom:3px;word-break:break-word}" +
      ".cx-counts span{display:inline-block;margin-right:6px;padding:0 5px;border-radius:8px;background:var(--line);color:var(--mut)}" +
      ".cx-sec{margin-top:4px}.cx-sec>summary{cursor:pointer;font-weight:600}.cx-row{display:flex;gap:4px;align-items:center;padding:1px 0}" +
      ".cx-row .g{width:14px;text-align:center}.cx-row .n{flex:1;word-break:break-all}.cx-row .s{color:var(--mut)}.cx-row.blocked .n{text-decoration:line-through;color:#e5484d}" +
      ".cx-row.blocked .s{color:#e5484d}.cx-wire{padding:0 6px;font-size:10px}.cx-pend{outline:2px dashed var(--sel)}.cx-li{color:var(--mut)}";
    document.head.appendChild(s);
  }

  function section(title, n, items) {
    const d = E("details", "cx-sec"); d.appendChild(E("summary", null, title + " (" + n + ")"));
    items.forEach(x => d.appendChild(x));
    return d;
  }

  function toolRow(cid, t, card) {
    const blocked = t.status === "blocked";
    const r = E("div", "cx-row" + (blocked ? " blocked" : ""));
    r.dataset.tool = t.name; r.dataset.status = t.status;
    r.appendChild(E("span", "g", GLYPH[blocked ? "blocked" : t.class] || ""));
    r.appendChild(E("span", "n", t.name));
    r.appendChild(E("span", "s", blocked ? "blocked" : t.status.indexOf("channel-reply") === 0 ? "reply (main session)" : t.status));
    r.title = t.source + " · " + t.class + " · " + t.status + (t.source === "unverified" ? " (unverified)" : "");
    /* wire button only for allow-listed, wireable tools; channel-reply stays main-session-only (no affordance) */
    if (!blocked && t.status.indexOf("channel-reply") !== 0) {
      const b = E("button", "cx-wire", "wire"); b.title = "Pick a target card to receive this capability (adds an edge; nothing runs)";
      b.addEventListener("click", ev => { ev.stopPropagation(); startWire(card.id, t.name); });
      r.appendChild(b);
    }
    return r;
  }

  function fill(d, c) {
    d.querySelectorAll(":scope > .cx-body").forEach(n => n.remove());
    const body = E("div", "cx-body"); body.dataset.connector = (c.ref && c.ref.connector) || "";
    const key = (c.fields && c.fields.inventory_key) || (c.ref && c.ref.connector);
    const ci = inv && inv.connectors ? inv.connectors[key] : null;
    body.appendChild(E("div", "cx-acct", "account: " + ((c.ref && c.ref.account) || (ci && ci.account) || "?")));
    if (!ci) { body.appendChild(E("div", "cx-li", inv ? "no inventory entry for '" + key + "'" : "loading inventory…")); d.appendChild(body); return; }
    const cs = E("div", "cx-counts");
    ["tools", "hooks", "skills"].forEach(k => cs.appendChild(E("span", null, ci.counts[k] + " " + k)));
    body.appendChild(cs);
    const t = section("tools", ci.tools.length, ci.tools.map(x => toolRow(ci.connector, x, c))); t.open = true; t.classList.add("cx-tools");
    const h = section("hooks", ci.hooks.length, ci.hooks.map(x => E("div", "cx-li", x.name))); h.classList.add("cx-hooks");
    const s = section("skills", ci.skills.length, ci.skills.map(x => E("div", "cx-li", x.name))); s.classList.add("cx-skills");
    body.append(t, h, s);
    body.appendChild(E("div", "cx-li", "inventory " + (ci.generated_at || "").slice(0, 16).replace("T", " ")));
    d.appendChild(body);
  }

  function decorate(d, c) {
    if (c.type !== "container") return;
    fill(d, c);
    if (!inv && !loading) load();
  }

  async function load() {
    loading = true;
    try {
      const r = await fetch("/inventory"); const j = await r.json();
      if (!r.ok) throw new Error((j && j.error) || r.status);
      inv = j;
    } catch (e) { inv = { connectors: {} }; say("inventory load failed: " + e, "err"); }
    loading = false; K.decorateAll();
  }

  /* ---- wiring: capability -> edge + via_container (board edit only; Save persists) ---- */
  function wire(containerId, tool, targetId) {
    const b = W.board; if (!b) return { ok: false, error: "no board" };
    const cont = b.cards.find(c => c.id === containerId), tgt = b.cards.find(c => c.id === targetId);
    if (!cont || cont.type !== "container") return { ok: false, error: "not a container" };
    if (!tgt || tgt.id === cont.id) return { ok: false, error: "pick a different target card" };
    const ci = inv && inv.connectors && inv.connectors[(cont.fields && cont.fields.inventory_key) || (cont.ref && cont.ref.connector)];
    const t = ci && ci.tools.find(x => x.name === tool);
    if (!t) return { ok: false, error: "tool not in inventory (unknown tools are blocked)" };
    if (t.status === "blocked" || t.status.indexOf("channel-reply") === 0) return { ok: false, error: "tool is not wireable: " + t.status };
    const tin = tgt.ports && tgt.ports.in && tgt.ports.in[0];
    if (!tin) return { ok: false, error: "target has no input port" };
    const port = "cap:" + tool;
    cont.ports = cont.ports || { in: [], out: [] }; cont.ports.out = cont.ports.out || [];
    if (!cont.ports.out.includes(port)) cont.ports.out.push(port);
    b.edges = b.edges || [];
    const from = cont.id + "." + port, to = tgt.id + "." + tin;
    if (!b.edges.some(e => e.from === from && e.to === to)) b.edges.push({ from, to });
    tgt.ref = tgt.ref || {}; tgt.ref.via_container = cont.id;
    W.render();
    return { ok: true, edge: { from, to }, via_container: cont.id };
  }

  function cancel() { pending = null; document.querySelectorAll(".cx-pend").forEach(n => n.classList.remove("cx-pend")); document.removeEventListener("click", onPick, true); document.removeEventListener("keydown", onKey, true); }
  function onKey(ev) { if (ev.key === "Escape") { cancel(); say("wiring cancelled", "") } }
  function onPick(ev) {
    const card = ev.target.closest && ev.target.closest("#cards > .card[data-id]"); if (!card || !pending) return;
    ev.preventDefault(); ev.stopPropagation();
    const p = pending; cancel();
    const r = wire(p.container, p.tool, card.dataset.id);
    say(r.ok ? "wired " + p.tool + " → " + card.dataset.id + " (Save to persist)" : "wire failed: " + r.error, r.ok ? "ok" : "err");
  }
  function startWire(containerId, tool) {
    cancel(); pending = { container: containerId, tool };
    say("click the target card for " + tool + " (Esc cancels)", "");
    setTimeout(() => { document.addEventListener("click", onPick, true); document.addEventListener("keydown", onKey, true) }, 0);
  }

  css();
  K.registerDecorator(decorate);
  window.wbContainers = { wire, startWire, cancel, get inventory() { return inv }, reload: () => { inv = null; return load() } };
  load();
})();
