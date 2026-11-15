// Small helpers for building the page: element lookup and creation, and times
// as the page writes them.

export const $ = (id) => document.getElementById(id);

// An element with attributes and children; `class` and `text` are shorthands,
// and a null, undefined or false attribute or child is left out.
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children) if (c) node.append(c);
  return node;
}

// `1:02:03`, or `2:03` under an hour; "" for an unknown time.
export function formatTime(seconds) {
  if (seconds === null || seconds === undefined || !isFinite(seconds)) return "";
  seconds = Math.max(0, Math.round(seconds));
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

// Whether a key press belongs to a field the user types in, so the page's own
// keys leave it alone.
export function typing(target) {
  if (!target || !target.tagName) return false;
  const tag = target.tagName.toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || !!target.isContentEditable;
}
