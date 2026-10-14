/* workflow-board v4 dirty-vs-active (F-B10). Port of executor/hooks/register.tsx semanticText (layout/style/pin/notes excluded, keys sorted,
   compact JSON) + sha256. Shows a pill: whether the board in the editor differs, SEMANTICALLY, from the deployed hash in /active.
   Moving/resizing cards, group_styles and saved_at never make it dirty. Read-only: no writes. Exports are pure (usable from bun/node). */
const LAYOUT_TOP = new Set(["saved_at", "group_styles", "integrity_hash"]);
const LAYOUT_CARD = new Set(["pos", "size", "style", "pin"]);
const NOTE_TYPE = "note";
function canon(v) {
  if (Array.isArray(v)) return v.map(canon);
  if (v && typeof v === "object") { const o = {}; for (const k of Object.keys(v).sort()) o[k] = canon(v[k]); return o }
  return v;
}
function endId(p) { const s = String(p == null ? "" : p), i = s.lastIndexOf("."); return i < 0 ? s : s.slice(0, i) }
export function semanticText(doc) {
  const top = {};
  for (const k of Object.keys(doc || {})) if (!LAYOUT_TOP.has(k)) top[k] = doc[k];
  if (Array.isArray(top.cards)) {
    const noteIds = new Set();
    for (const c of top.cards) if (c && c.type === NOTE_TYPE) noteIds.add(String(c.id));
    top.cards = top.cards.filter(c => !(c && c.type === NOTE_TYPE)).map(c => { const o = {}; for (const k of Object.keys(c || {})) if (!LAYOUT_CARD.has(k)) o[k] = c[k]; return o });
    if (noteIds.size && Array.isArray(top.edges)) top.edges = top.edges.filter(e => !(e && typeof e === "object" && (noteIds.has(endId(e.from)) || noteIds.has(endId(e.to)))));
  }
  return JSON.stringify(canon(top));
}
export async function sha256(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, "0")).join("");
}
export async function semanticHash(doc) { return sha256(semanticText(doc)) }

if (typeof window !== "undefined" && typeof document !== "undefined") (function () {
  const W = window.wb;
  if (!W) return;
  const pill = document.createElement("div");
  pill.id = "wb-dirty";
  pill.setAttribute("role", "status");
  pill.style.cssText = "position:fixed;left:12px;bottom:12px;z-index:60;font:12px system-ui,sans-serif;padding:4px 10px;border-radius:12px;background:#222;color:#eee;border:1px solid #555;display:none;pointer-events:none";
  document.body.appendChild(pill);
  let active = null, lastText = "", lastBoard = "", busy = false;
  async function loadActive() {
    try { const r = await fetch("/active", { cache: "no-store" }); active = r.ok ? await r.json() : null } catch (e) { active = null }
  }
  async function check() {
    if (busy || !W.board) return;
    busy = true;
    try {
      const text = semanticText(W.board), name = W.board.board || "";
      if (text === lastText && name === lastBoard && active) return;
      lastText = text; lastBoard = name;
      await loadActive();
      const a = active && active.active;
      if (!a || a.board !== name) { pill.style.display = "none"; window.wbDirty.state = "not-active"; return }
      const h = await sha256(text);
      const dirty = h !== a.hash;
      window.wbDirty.state = dirty ? "dirty" : "clean";
      pill.style.display = "block";
      pill.textContent = dirty ? "edited since deploy (hash differs)" : "matches deployed hash";
      pill.title = "editor " + h.slice(0, 8) + " vs deployed " + String(a.hash || "").slice(0, 8) + " (layout fields ignored)";
      pill.style.borderColor = dirty ? "#d9822b" : "#4a9d5b";
    } finally { busy = false }
  }
  window.wbDirty = { semanticText, sha256, semanticHash, check, state: "unknown" };
  setInterval(check, 1200);
  check();
})();
