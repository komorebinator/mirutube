// settings: the library folders — changed only on the computer MiruTube runs
// on, shown read-only elsewhere (decisions#settings-local) — and what was
// removed from the library, each with Bring back (behaviour#library-control).

import { confirmAction, tell } from "./dialog.js";
import { $, el } from "./dom.js";
import { getJSON, request } from "./api.js";
import { listen } from "./state.js";

const KINDS = { folder: "Folder", video: "Video", missing: "No longer on the disk" };

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
        el("div", { class: "folder-name", text: "Shown as " + f.name })));
    if (local) {
      const remove = el("button", { type: "button", class: "folder-remove", text: "Remove" });
      remove.addEventListener("click", () => removeFolder(f.folder));
      item.append(remove);
    }
    return item;
  }));
  if (!settings.library_folders.length) {
    $("folders").append(el("li", { class: "empty", text: "No library folder yet." }));
  }
  $("add-folder").hidden = !local;
  $("folders-local-only").hidden = local;
}

function renderExcluded() {
  $("excluded").replaceChildren(...settings.excluded.map((x) => {
    const back = el("button", { type: "button", class: "bring-back", text: "Bring back" });
    back.addEventListener("click", () => bringBack(x.path));
    return el("li", {},
      el("div", { class: "folder-text" },
        el("div", { class: "folder-path", text: x.title }),
        el("div", { class: "folder-name", text: (KINDS[x.kind] || x.kind) + " · " + x.path })),
      back);
  }));
  $("nothing-removed").hidden = settings.excluded.length > 0;
}

function render() {
  renderFolders();
  renderExcluded();
}

async function changeFolders(action, folder) {
  const r = await request("/api/settings/library-folders", { action, folder });
  if (!r.ok) {
    showError(r.status === 403 ? "The library folders are changed on the computer MiruTube runs on."
      : `${action === "add" ? "It could not be added" : "It could not be removed"}: ${r.error}`);
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
    title: "Remove this library folder?",
    text: `${folder} — its videos leave the library. Nothing is deleted from the disk.`,
    action: "Remove", danger: true,
  });
  if (!sure) return false;
  return changeFolders("remove", folder);
}

$("add-folder").addEventListener("submit", async (e) => {
  e.preventDefault();
  const folder = $("folder-path").value.trim();
  if (!folder) return showError("Type the full path of a folder.");
  if (await changeFolders("add", folder)) $("folder-path").value = "";
});

export async function bringBack(path) {
  const r = await request("/api/library/restore", { path });
  if (!r.ok) await tell("It could not be brought back", r.error);
  await showSettings(false);
}

export async function showSettings(fresh = true) {
  document.title = "Settings · MiruTube";
  if (fresh) {
    showError(null);
    $("folder-path").value = "";
  }
  settings = await getJSON("/api/settings");
  render();
}

listen("rescanned", () => {
  if (!$("settings").hidden) showSettings(false);
});
