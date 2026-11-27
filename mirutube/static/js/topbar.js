// top_bar: the logo, search, the address other devices open MiruTube at with
// its QR code (behaviour#connect), and settings.

import { $, el } from "./dom.js";
import { getJSON, request } from "./api.js";
import { t } from "./i18n.js";
import { state, listen, notify, onWatch, currentRoute, watchHref, entryOf, byTitle, navigate, loadCatalogue } from "./state.js";

const MAX_RESULTS = 8;     // entries in the search drop-down on watch

// ------------------------------------------------------------------- search

// On home the grid filters as the user types. On watch typing never leaves
// the page, so the video keeps playing (behaviour#uninterrupted): the
// matching playlists and videos drop down under the box, choosing one opens
// it, and Enter opens home with the query in place.
const search = $("search");
const results = $("search-results");

export function closeResults() {
  results.hidden = true;
  search.setAttribute("aria-expanded", "false");
}

// The same cards the grid would show for the query: playlists, then videos
// outside any, each group by name.
function matches(q) {
  const cat = state.catalogue;
  const playlists = cat.playlists.filter((p) => p.title.toLowerCase().includes(q)).sort(byTitle)
    .map((p) => ({ title: p.title, id: entryOf(p), kind: t(p.series ? "Series" : "Playlist") }));
  const videos = cat.standalone.map((id) => cat.videos[id])
    .filter((v) => v.title.toLowerCase().includes(q)).sort(byTitle)
    .map((v) => ({ title: v.title, id: v.id, kind: t("Video") }));
  return [...playlists, ...videos].slice(0, MAX_RESULTS);
}

export function showResults() {
  const q = state.query.trim().toLowerCase();
  if (!onWatch() || !q || !state.catalogue) return closeResults();
  const found = matches(q);
  results.replaceChildren(...(found.length
    ? found.map((m) => el("li", {}, el("a", { href: watchHref(m.id) },
      el("span", { class: "result-title", text: m.title }),
      el("span", { class: "result-kind", text: m.kind }))))
    : [el("li", { class: "empty", text: t("Nothing found") })]));
  results.hidden = false;
  search.setAttribute("aria-expanded", "true");
}

export function clearSearch() {
  state.query = "";
  search.value = "";
  closeResults();
}

search.addEventListener("input", () => {
  state.query = search.value;
  if (onWatch()) showResults();
  else if (currentRoute().view === "settings") navigate("/"); // the grid it filters is on home
  else notify("query");
});
search.addEventListener("focus", showResults);
search.addEventListener("keydown", (e) => {
  if (!onWatch()) return;
  if (e.key === "Enter") {
    e.preventDefault();
    closeResults();
    navigate("/");
  } else if (e.key === "Escape") {
    closeResults();
  } else if (e.key === "ArrowDown" && !results.hidden) {
    const first = results.querySelector("a");
    if (first) { e.preventDefault(); first.focus(); }
  }
});
results.addEventListener("keydown", (e) => {
  const links = [...results.querySelectorAll("a")];
  const i = links.indexOf(document.activeElement);
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const next = i + (e.key === "ArrowDown" ? 1 : -1);
    if (next < 0) search.focus();
    else if (next < links.length) links[next].focus();
  } else if (e.key === "Escape") {
    closeResults();
    search.focus();
  }
});
// choosing a result opens it, and the search is done with
results.addEventListener("click", (e) => {
  if (!e.target.closest("a")) return;
  clearSearch();
});

// the logo opens home as it first shows: the search is cleared
$("logo").addEventListener("click", () => {
  clearSearch();
  notify("query");
});

// ------------------------------------------------------- address and QR code

// The address other devices open this MiruTube at — never `localhost` — and
// a card with it as a QR code, read by a phone's or a TV's camera.
const qrCard = $("qr-card");

export async function showAddress() {
  let address = null;
  try {
    address = (await getJSON("/api/address")).address;
  } catch (e) {
    console.error(e);
  }
  $("address").textContent = address || "";
  $("address").hidden = !address;
  $("qr-button").hidden = !address;
  $("qr-address").textContent = address || "";
}

function closeQr() {
  qrCard.hidden = true;
  $("qr-button").setAttribute("aria-expanded", "false");
}

$("qr-button").addEventListener("click", (e) => {
  e.stopPropagation();
  if (!qrCard.hidden) return closeQr();
  // asked for each time it opens, so a changed address shows its own code
  $("qr-image").src = "/api/address.svg?t=" + Date.now();
  qrCard.hidden = false;
  $("qr-button").setAttribute("aria-expanded", "true");
});
document.addEventListener("click", (e) => {
  if (!$("search-wrap").contains(e.target)) closeResults();
  if (!qrCard.hidden && !qrCard.contains(e.target)) closeQr();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeQr();
});

listen("language", () => { if (!results.hidden) showResults(); });

// ------------------------------------------------------------ resync_button

// Rescans the library folders now; the arrows turn while it scans.
$("resync-button").addEventListener("click", async () => {
  const button = $("resync-button");
  if (button.classList.contains("busy")) return;
  button.classList.add("busy");
  button.disabled = true;
  try {
    const r = await request("/api/rescan", {});
    if (r.ok) {
      await loadCatalogue();
      notify("rescanned");
    }
  } catch (e) {
    console.error(e);
  } finally {
    button.classList.remove("busy");
    button.disabled = false;
  }
});
