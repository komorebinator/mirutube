// settings: the library folders with the free space on each — changed only
// on the computer MiruTube runs on, shown read-only elsewhere
// (decisions#settings-local) — what was removed from the library, each with
// Bring back (behaviour#library-control), and the settings shared by the
// household, changed from any device for every device.

import { confirmAction, tell } from "./dialog.js";
import { $, el } from "./dom.js";
import { getJSON, request } from "./api.js";
import { state, listen, notify } from "./state.js";
import { formatSize } from "./format.js";
import { t, fromServer } from "./i18n.js";

const SUBTITLE_SIZES = [75, 100, 125, 150, 200];

const KINDS = { folder: "Folder", video: "Video", missing: "No longer on the disk" };
// each language named in itself, so it is found whatever the page is in
const LANGUAGE_CHOICES = [["auto", () => t("Browser's language")], ["en", () => "English"], ["ru", () => "Русский"]];

let settings = null;       // as /api/settings last returned it

function showError(message) {
  $("folders-error").textContent = message || "";
  $("folders-error").hidden = !message;
}

function renderFolders() {
  const local = settings.local;
  $("folders").replaceChildren(...settings.library_folders.map((f) => {
    const item = el("li", {},
      el("div", { class: "folder-text" },
        el("div", { class: "folder-path", text: f.folder }),
        el("div", { class: "folder-name", text: t("Shown as {}", f.name) })),
      el("div", { class: "folder-free", text: typeof f.free === "number" ? t("{} free", formatSize(f.free)) : t("Not available") }));
    if (local) {
      const remove = el("button", { type: "button", class: "folder-remove", text: t("Remove") });
      remove.addEventListener("click", () => removeFolder(f.folder));
      item.append(remove);
    }
    return item;
  }));
  if (!settings.library_folders.length) {
    $("folders").append(el("li", { class: "empty", text: t("No library folder yet.") }));
  }
  $("add-folder").hidden = !local;
  $("folders-local-only").hidden = local;
}

function renderExcluded() {
  $("excluded").replaceChildren(...settings.excluded.map((x) => {
    const back = el("button", { type: "button", class: "bring-back", text: t("Bring back") });
    back.addEventListener("click", () => bringBack(x.path));
    return el("li", {},
      el("div", { class: "folder-text" },
        el("div", { class: "folder-path", text: x.title }),
        el("div", { class: "folder-name", text: (KINDS[x.kind] ? t(KINDS[x.kind]) : x.kind) + " · " + x.path })),
      back);
  }));
  $("nothing-removed").hidden = settings.excluded.length > 0;
}

// Subtitle size, seeding and the language: one for the household, sent to
// every page when they change (behaviour#subtitle-size, downloads#torrent,
// behaviour#languages).
function renderShared() {
  const now = state.shared.subtitle_size;
  $("subtitle-size").replaceChildren(...SUBTITLE_SIZES.map((size) => {
    const b = el("button", { type: "button", role: "radio", "aria-checked": String(size === now), text: size + "%" });
    b.addEventListener("click", () => changeShared({ subtitle_size: size }));
    return b;
  }));
  $("seed-torrents").checked = !!state.shared.seed_torrents;
  const language = state.shared.language || "auto";
  $("language").replaceChildren(...LANGUAGE_CHOICES.map(([value, label]) => {
    const b = el("button", { type: "button", role: "radio", "aria-checked": String(value === language), text: label() });
    if (value !== "auto") b.lang = value;
    b.addEventListener("click", () => changeShared({ language: value }));
    return b;
  }));
}

$("seed-torrents").addEventListener("change", (e) => changeShared({ seed_torrents: e.target.checked }));

async function changeShared(change) {
  const r = await request("/api/settings/shared", change);
  if (!r.ok) return tell(t("It could not be changed"), r.error);
  state.shared = r.data;
  notify("settings");
}

function render() {
  renderFolders();
  renderExcluded();
  renderShared();
}

async function changeFolders(action, folder) {
  const r = await request("/api/settings/library-folders", { action, folder });
  if (!r.ok) {
    showError(r.status === 403 ? t("The library folders are changed on the computer MiruTube runs on.")
      : t(action === "add" ? "It could not be added: {}" : "It could not be removed: {}", fromServer(r.error)));
    return false;
  }
  showError(null);
  settings = r.data;
  render();
  return true;
}

// A library folder leaves after a confirmation; nothing on disk changes.
export async function removeFolder(folder) {
  const sure = await confirmAction({
    title: t("Remove this library folder?"),
    text: t("{} — its videos leave the library. Nothing is deleted from the disk.", folder),
    action: t("Remove"), danger: true,
  });
  if (!sure) return false;
  return changeFolders("remove", folder);
}

$("add-folder").addEventListener("submit", async (e) => {
  e.preventDefault();
  const folder = $("folder-path").value.trim();
  if (!folder) return showError(t("Type the full path of a folder."));
  if (await changeFolders("add", folder)) $("folder-path").value = "";
});

export async function bringBack(path) {
  const r = await request("/api/library/restore", { path });
  if (!r.ok) await tell(t("It could not be brought back"), r.error);
  await showSettings(false);
}

export async function showSettings(fresh = true) {
  document.title = t("Settings") + " · MiruTube";
  if (fresh) {
    showError(null);
    $("folder-path").value = "";
  }
  settings = await getJSON("/api/settings");
  state.shared = settings.shared;
  render();
}

listen("settings", () => {
  if (!$("settings").hidden) renderShared();
});

listen("rescanned", () => {
  if (!$("settings").hidden) showSettings(false);
});

listen("language", () => {
  if ($("settings").hidden || !settings) return;
  document.title = t("Settings") + " · MiruTube";
  render();
});
