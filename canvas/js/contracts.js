"use strict";
/* Station contracts and provenance (module). Read-only decoration via wbKinds.registerDecorator; never edits the board.
   For step/skill/agent/tool cards it shows one line: "in: a, b -> out: c" when the card carries parsed fields.inputs/outputs,
   else "contract missing". A contract is only ever parsed from the source file (importers/common.py), never guessed, so the
   marker doubles as a design hint: a station that declares what it takes and returns is easier for an agent to run.
   A card with fields.src_path gets a tooltip naming its source file and heading. All text via textContent. */
(function () {
  const W = window.wb, K = window.wbKinds;
  if (!W || !K || !K.registerDecorator) return;
  const STATIONS = new Set(["step", "skill", "agent", "tool"]);
  const E = (t, c, x) => { const e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e };
  const SAFE = /^(https?:\/\/|\/(kb|report)[/?])/i;  /* link only to the web or to this server's read-only views */
  function extras(el, c, f) {
    el.querySelectorAll(":scope > .wb-flag, :scope > .wb-open").forEach(n => n.remove());
    const hd = el.querySelector(":scope > .hd");
    const add = n => { if (hd && hd.nextSibling) el.insertBefore(n, hd.nextSibling); else el.appendChild(n) };
    [["drift", "drift", "#fde8e8", "#9b1c1c"], ["duplicate_skills", "duplicate skills", "#fde8e8", "#9b1c1c"],
     ["hint", "split this", "#eef2ff", "#3730a3"], ["suggested", "suggested", "#f3f4f6", "#374151"]].forEach(([k, t, bg, fg]) => {
      if (!f[k]) return;
      const b = E("div", "wb-flag", t); b.title = f[k];
      b.style.cssText = "display:inline-block;font:10px system-ui,sans-serif;padding:0 6px;margin:2px 4px 0;border-radius:6px;background:" + bg + ";color:" + fg;
      add(b);
    });
    el.style.borderStyle = f.role === "suggested" ? "dashed" : "";
    const href = f.open || f.url;
    if (href && SAFE.test(String(href))) {
      const a = E("a", "wb-open", "open \u2197"); a.href = href; a.target = "_blank"; a.rel = "noopener noreferrer";
      a.title = "Open " + href; a.style.cssText = "position:absolute;right:6px;top:-15px;background:#fff;padding:0 4px;border-radius:6px;font:10px system-ui,sans-serif;z-index:2";
      a.addEventListener("pointerdown", ev => ev.stopPropagation());
      el.appendChild(a);
    }
  }
  K.registerDecorator((el, c) => {
    const old = el.querySelector(":scope > .wb-contract"); if (old) old.remove();
    const f = c.fields || {};
    extras(el, c, f);
    if (c.type === "note" && f.banner === "true") { el.style.maxWidth = "none"; el.classList.add("wb-banner") }  /* the status banner spans the board */
    if (f.src_path) el.title = "source: " + f.src_path + (f.src_anchor ? " \u00a7 " + f.src_anchor : "") + (f.src_sha ? " (sha " + f.src_sha + ")" : "");
    if (!STATIONS.has(c.type) || !(f.contract || f.inputs || f.outputs)) return;
    const missing = f.contract === "missing";
    const txt = missing ? "contract missing" : "in: " + (f.inputs || "?") + " \u2192 out: " + (f.outputs || "?");
    const d = E("div", "wb-contract", txt);
    d.title = missing ? "No inputs or outputs are declared in the source. Declare them (an Inputs / Outputs section, or inputs: / outputs: front matter) so an agent knows what this station takes and returns."
                      : (f.contract ? f.contract + ". " : "") + "Parsed from: " + (f.contract_src || "source");
    d.style.cssText = "font:10px system-ui,sans-serif;padding:1px 6px;margin:2px 4px;border-radius:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"
      + (missing ? "background:#fff4e5;color:#8a4b00;border:1px dashed #d9a35b" : "background:#eef6ee;color:#245a24;border:1px solid #9cc79c");
    const hd = el.querySelector(":scope > .hd");
    if (hd && hd.nextSibling) el.insertBefore(d, hd.nextSibling); else el.appendChild(d);
  });
  if (K.decorateAll) K.decorateAll();
})();
