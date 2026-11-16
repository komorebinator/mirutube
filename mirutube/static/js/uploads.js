// upload_button, uploads_card and drop_zone: videos brought into the library
// from this device, a torrent or a link (behaviour#uploads). What is coming
// in on every device is downloads#fields#jobs, sent by the server over the
// event stream (page#fields#jobs); files from this device are sent in pieces,
// each with its offset, so a broken connection costs one piece
// (decisions#upload-in-pieces).

import { $, el } from "./dom.js";
import { getJSON, request } from "./api.js";
import { confirmAction, tell } from "./dialog.js";
import { state, listen, notify, loadCatalogue } from "./state.js";
import { formatSize } from "./format.js";

const PIECE = 8 * 1024 * 1024;   // bytes in one piece of a file
const RETRY_AFTER = 3000;        // ms before a piece that did not get through is sent again
const RING = 2 * Math.PI * 18;   // the length of upload_button's ring

const card = $("uploads-card");
const natural = (a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
let folders = [];                // the library folders, with the free space on each

// ------------------------------------------------------------------ the card

async function loadFolders() {
  try {
    folders = (await getJSON("/api/settings")).library_folders;
  } catch (e) {
    folders = [];
  }
  const select = $("upload-folder-select");
  const chosen = select.value;
  select.replaceChildren(...folders.map((f) => el("option", {
    value: f.name, text: f.name + (typeof f.free === "number" ? " — " + formatSize(f.free) + " free" : ""),
  })));
  if (folders.some((f) => f.name === chosen)) select.value = chosen;
  // nothing to choose with one folder; episodes of a series the library has
  // go to its folder whatever is chosen (downloads#destination)
  $("upload-target").hidden = folders.length < 2;
}

const chosenFolder = () => $("upload-folder-select").value || (folders[0] && folders[0].name) || null;

export function openUploads() {
  card.hidden = false;
  showError(null);
  loadFolders();
  renderJobs();
  $("uploads-close").focus();
}

function closeUploads() {
  card.hidden = true;
}

function showError(message) {
  $("upload-error").textContent = message || "";
  $("upload-error").hidden = !message;
}

$("upload-button").addEventListener("click", openUploads);
$("uploads-close").addEventListener("click", closeUploads);
card.addEventListener("click", (e) => { if (e.target === card) closeUploads(); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !card.hidden && $("dialog").hidden) closeUploads();
});

// ------------------------------------------------------------------- the jobs

const STATES = {
  queued: "Waiting", downloading: "Downloading", interrupted: "Interrupted", seeding: "Seeding",
  done: "Done", failed: "Failed", cancelled: "Cancelled",
};

function timeLeft(seconds) {
  if (!seconds || !isFinite(seconds)) return "";
  if (seconds < 60) return "less than a minute left";
  if (seconds < 3600) return Math.round(seconds / 60) + " min left";
  return (seconds / 3600).toFixed(1) + " h left";
}

function jobItem(job) {
  const unfinished = ["queued", "downloading", "interrupted", "failed"].includes(job.state);
  const fraction = job.size ? Math.min(1, job.done / job.size) : 0;
  const meta = [
    job.current,
    job.kind === "link" ? (job.state === "done" ? "" : Math.round(fraction * 100) + "%")
      : job.size ? formatSize(job.done) + " of " + formatSize(job.size) : "",
    job.speed ? formatSize(job.speed) + "/s" : "",
    job.state === "downloading" ? timeLeft(job.eta) : "",
    job.kind === "torrent" && typeof job.peers === "number" && job.state !== "done" ? job.peers + " peers" : "",
    job.state === "seeding" && job.uploaded ? formatSize(job.uploaded) + " seeded" : "",
    job.device ? "from " + job.device : "",
  ].filter(Boolean).join(" · ");
  const actions = [];
  if (unfinished) actions.push(button("Cancel", () => cancel(job)));
  if (job.state === "seeding") actions.push(button("Stop seeding", () => act("stop-seeding", job)));
  if (["done", "failed", "cancelled"].includes(job.state)) actions.push(button("Remove from list", () => act("dismiss", job)));
  const bar = el("div", { class: "job-bar" }, el("div"));
  bar.firstChild.style.width = (fraction * 100).toFixed(1) + "%";
  return el("li", { class: "job", "data-id": job.id },
    el("div", { class: "job-head" },
      el("span", { class: "job-title", text: job.title, title: job.title }),
      el("span", { class: "job-state" + (job.state === "failed" ? " failed" : ""), text: STATES[job.state] || job.state })),
    job.into ? el("div", { class: "job-into", text: "→ " + job.into }) : null,
    ["seeding", "done", "cancelled"].includes(job.state) ? null : bar,
    meta ? el("div", { class: "job-meta", text: meta }) : null,
    job.reason ? el("div", { class: "job-reason", text: job.reason }) : null,
    actions.length ? el("div", { class: "job-actions" }, ...actions) : null);
}

function button(text, onClick) {
  const b = el("button", { type: "button", text });
  b.addEventListener("click", onClick);
  return b;
}

function renderJobs() {
  const jobs = state.jobs || [];
  if (!card.hidden) {
    $("jobs").replaceChildren(...jobs.map(jobItem));
    $("no-jobs").hidden = jobs.length > 0;
  }
  // upload_button's ring: the progress of everything coming in, on any device
  const coming = jobs.filter((j) => ["queued", "downloading", "interrupted"].includes(j.state));
  const size = coming.reduce((n, j) => n + (j.size || 0), 0);
  const done = coming.reduce((n, j) => n + Math.min(j.done || 0, j.size || 0), 0);
  const ring = document.querySelector(".upload-ring");
  ring.hidden = coming.length === 0;
  const fill = $("upload-ring");
  fill.style.strokeDasharray = String(RING);
  fill.style.strokeDashoffset = String(RING * (1 - (size ? done / size : 0)));
  $("upload-button").title = coming.length ? "Upload · " + coming.length + " coming in" : "Upload";
}

// A file that arrived is in the library: the catalogue is read again, so
// home and settings show it (behaviour#uploads#scenarios#folder).
let placed = null;

listen("jobs", async () => {
  renderJobs();
  const now = (state.jobs || []).reduce((n, j) => n + (j.placed || 0), 0);
  const more = placed !== null && now > placed;
  placed = now;
  if (more && state.catalogue) {
    try {
      await loadCatalogue();
      notify("rescanned");
    } catch (e) { /* the next one will do */ }
  }
});

async function cancel(job) {
  const sure = await confirmAction({
    title: "Cancel this download?",
    text: `Everything “${job.title}” brought is deleted, what is already in the library included. Files that were there before stay.`,
    action: "Cancel download", danger: true,
  });
  if (sure) await act("cancel", job);
}

async function act(action, job) {
  const r = await request("/api/downloads/" + action, { job: job.id });
  if (!r.ok) await tell("It could not be done", r.error);
}

// ------------------------------------------------------------- sending files

// Files from this device: `.torrent`s each start a torrent, the rest go as
// one upload — a folder keeping its name (downloads#destination).
async function send(entries) {
  showError(null);
  if (!folders.length) await loadFolders(); // chosen before the card had read them
  const folder = chosenFolder();
  if (!folder) return showError("There is no library folder yet: add one in Settings, on the computer MiruTube runs on.");
  const torrents = entries.filter((x) => /\.torrent$/i.test(x.path) && !x.path.includes("/"));
  const files = entries.filter((x) => !torrents.includes(x));
  for (const t of torrents) {
    const r = await fetch("/api/downloads/torrent?folder=" + encodeURIComponent(folder)
      + "&device=" + encodeURIComponent(state.device.id), { method: "PUT", body: t.file });
    if (!r.ok) showError(t.path + ": " + (await r.text()));
  }
  if (files.length) upload(files, folder);
}

// One upload: its files in the order they are watched — `2` before `10` —
// each from where the server says it ends, so it goes on after a break or a
// reload of the server.
async function upload(entries, folder) {
  entries.sort((a, b) => natural(a.path, b.path));
  const r = await request("/api/downloads/upload", {
    folder, device: state.device.id, files: entries.map((x) => ({ path: x.path, size: x.file.size })),
  });
  if (!r.ok) return showError(r.error);
  const { job, received } = r.data;
  for (let i = 0; i < entries.length; i++) {
    let offset = received[i];
    const file = entries[i].file;
    while (offset < file.size) {
      let res;
      try {
        res = await fetch(`/api/downloads/${job}/${i}?offset=${offset}`,
          { method: "PUT", body: file.slice(offset, Math.min(file.size, offset + PIECE)) });
      } catch (e) {
        await new Promise((ok) => setTimeout(ok, RETRY_AFTER)); // the server is away: again
        continue;
      }
      if (res.ok || res.status === 409) {
        offset = (await res.json()).received;
      } else if (res.status >= 500 && res.status !== 507) {
        await new Promise((ok) => setTimeout(ok, RETRY_AFTER));
      } else {
        // cancelled, from here or another device, or refused: it stops
        if (res.status !== 422 && res.status !== 404) showError(file.name + ": " + (await res.text()));
        return;
      }
    }
  }
}

$("upload-files").addEventListener("click", () => $("upload-files-input").click());
$("upload-folder").addEventListener("click", () => $("upload-folder-input").click());
$("upload-files-input").addEventListener("change", (e) => {
  send([...e.target.files].map((file) => ({ file, path: file.name })));
  e.target.value = "";
});
$("upload-folder-input").addEventListener("change", (e) => {
  send([...e.target.files].map((file) => ({ file, path: file.webkitRelativePath || file.name })));
  e.target.value = "";
});

// a link: a site's video or playlist, a .torrent or a magnet (downloads#link)
$("upload-link").addEventListener("submit", async (e) => {
  e.preventDefault();
  const link = $("link-input").value.trim();
  if (!link) return;
  showError(null);
  const r = await request("/api/downloads/link", { folder: chosenFolder(), link, device: state.device.id });
  if (!r.ok) return showError(r.error);
  $("link-input").value = "";
});

// ---------------------------------------------------------------- drop_zone

// Files and folders dragged over any page: the browser never opens one in
// MiruTube's place, and a drop starts their upload with uploads_card open.
let dragging = 0;
const carriesFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes("Files");

window.addEventListener("dragenter", (e) => {
  if (!carriesFiles(e)) return;
  e.preventDefault();
  dragging++;
  $("drop-zone").hidden = false;
});
window.addEventListener("dragover", (e) => {
  if (!carriesFiles(e)) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "copy";
});
window.addEventListener("dragleave", (e) => {
  if (!carriesFiles(e)) return;
  dragging = Math.max(0, dragging - 1);
  if (!dragging) $("drop-zone").hidden = true;
});
window.addEventListener("drop", async (e) => {
  if (!carriesFiles(e)) return;
  e.preventDefault();
  dragging = 0;
  $("drop-zone").hidden = true;
  const roots = [...e.dataTransfer.items].map((item) => item.webkitGetAsEntry && item.webkitGetAsEntry())
    .filter(Boolean);
  const plain = roots.length ? null : [...e.dataTransfer.files].map((file) => ({ file, path: file.name }));
  openUploads();
  await loadFolders();
  send(plain || (await Promise.all(roots.map((entry) => walk(entry, "")))).flat());
});

// Every file under a dropped entry, with its path from the entry's own name.
async function walk(entry, prefix) {
  const path = prefix + entry.name;
  if (entry.isFile) {
    return [{ file: await new Promise((ok, bad) => entry.file(ok, bad)), path }];
  }
  const reader = entry.createReader();
  const children = [];
  for (;;) {
    const batch = await new Promise((ok, bad) => reader.readEntries(ok, bad)); // a directory comes in batches
    if (!batch.length) break;
    children.push(...batch);
  }
  return (await Promise.all(children.map((c) => walk(c, path + "/")))).flat();
}
