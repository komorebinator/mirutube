// confirm_dialog: MiruTube's own confirmation, in place of the browser's — a
// card centred over the page, dimmed and blurred behind it, with a title, a
// line saying what will happen, Cancel and the action named on its button,
// red when it takes something away. Escape and a click outside cancel; Enter
// confirms. A failed request is reported in it too, with a single OK, and a
// device's new name is asked for in it, with a text field.

import { $, el } from "./dom.js";
import { t, fromServer } from "./i18n.js";

const backdrop = $("dialog");
const card = backdrop.querySelector(".dialog-card");
let settle = null;   // ends the dialog open now: called with what it answers

function close(answer) {
  if (!settle) return;
  const done = settle;
  settle = null;
  backdrop.hidden = true;
  done(answer);
}

// Opens the dialog: `title`, `text`, the `action` button's label — `danger`
// makes it red — and `cancel`, or none for a message alone; with `value`, a
// text field holding it. Answers true, the field's text, or null when
// cancelled.
function open({ title, text, action = t("OK"), cancel = t("Cancel"), danger = false, value = null }) {
  close(null);
  const field = value === null ? null
    : el("input", { type: "text", class: "dialog-field", value, "aria-label": title, spellcheck: "false" });
  const confirm = el("button", { type: "submit", class: "button " + (danger ? "danger" : "primary"), text: action });
  const buttons = el("div", { class: "dialog-buttons" },
    cancel ? el("button", { type: "button", class: "button", text: cancel, "data-cancel": "" }) : null,
    confirm);
  const form = el("form", { method: "dialog" },
    el("h2", { id: "dialog-title", text: title }),
    text ? el("p", { class: "muted", text }) : null,
    field, buttons);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    close(field ? field.value : true);
  });
  card.replaceChildren(form);
  backdrop.hidden = false;
  if (field) {
    field.focus();
    field.select();
  } else {
    confirm.focus();
  }
  return new Promise((resolve) => { settle = resolve; });
}

backdrop.addEventListener("click", (e) => {
  if (e.target === backdrop || e.target.closest("[data-cancel]")) close(null);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && settle) {
    e.stopPropagation();
    close(null);
  }
}, true);

// Whether the user agrees to `action`.
export async function confirmAction(options) {
  return (await open(options)) === true;
}

// A message with a single OK; `text` may be what the server said.
export async function tell(title, text) {
  await open({ title, text: fromServer(text), action: t("OK"), cancel: null });
}

// The text the user typed, or null.
export function ask(options) {
  return open(Object.assign({ value: "" }, options));
}
