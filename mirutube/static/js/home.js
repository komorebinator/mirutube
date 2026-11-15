// home: continue_row and grid under their headings, or the grid narrowed to
// one playlist, opened from a video's details. A card's × removes from the
// library, and from Continue watching, after a confirmation
// (behaviour#library-control).

import { confirmAction, tell } from "./dialog.js";
import { $, el, formatTime } from "./dom.js";
import { idUrl, request } from "./api.js";
import {
  state, listen, loadCatalogue, watchHref, entryOf, episodeOf, titleInPlaylist, playlistOf, byTitle,
} from "./state.js";

function progressOf(video) {
  if (video.watched) return 1;
  if (!video.duration) return 0;
  return Math.min(1, (video.position || 0) / video.duration);
}

function playlistProgress(playlist) {
  let total = 0, seen = 0;
  for (const id of playlist.videos) {
    const v = state.catalogue.videos[id];
    const d = v.duration || 0;
    total += d;
    seen += v.watched ? d : Math.min(d, v.position || 0);
  }
  return total ? seen / total : 0;
}

export function thumbBox(videoId, { badge, progress, small } = {}) {
  const box = el("div", { class: "thumb" + (small ? " small" : "") },
    el("img", { src: idUrl("/thumb/", videoId), alt: "", loading: "lazy" }));
  if (badge) box.append(el("span", { class: "badge", text: badge }));
  if (progress > 0) {
    const bar = el("div", { class: "progress" }, el("div"));
    bar.firstChild.style.width = Math.round(progress * 100) + "%";
    box.append(bar);
  }
  return box;
}

// A card: a link to watch, with a round × in its top-right corner, shown
// while the pointer is over the card, that does `onRemove` (forget_button,
// remove_button).
const CROSS = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" fill="none" '
  + 'stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';

function card(href, title, thumb, labels, removeLabel, onRemove, extraClass = "") {
  const x = el("button", {
    type: "button", class: "card-x", "aria-label": removeLabel, title: removeLabel,
  });
  x.innerHTML = CROSS;
  x.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    onRemove();
  });
  return el("div", { class: "card" + extraClass, "data-title": title.toLowerCase() },
    el("a", { class: "card-link", href },
      thumb,
      el("div", { class: "card-title", text: title, title }),
      ...labels),
    x);
}

function videoCard(v, title, removeLabel, onRemove) {
  const label = v.playable ? null : el("div", { class: "card-label not-playable", text: "Can't play in the browser" });
  return card(watchHref(v.id), title, thumbBox(v.id, { badge: formatTime(v.duration), progress: progressOf(v) }),
    [label], removeLabel, onRemove, v.playable ? "" : " unplayable");
}

function playlistCard(p) {
  const count = p.videos.length;
  const what = p.series
    ? `Series · ${count} episode${count === 1 ? "" : "s"}`
    : `Playlist · ${count} video${count === 1 ? "" : "s"}`;
  return card(watchHref(entryOf(p)), p.title,
    thumbBox(p.videos[0], { badge: formatTime(p.duration), progress: playlistProgress(p) }),
    [el("div", { class: "card-label", text: what })],
    "Remove from library", () => removeFromLibrary(p.id, p.title, "folder"));
}

// ------------------------------------------------------------------ removing

async function afterChange() {
  await loadCatalogue();
  render();
}

// forget_button: after a confirmation, the positions of the card's playlist
// — or of its video, outside any — are forgotten, so the card leaves the row
// and the videos count as not started.
export async function forget(video) {
  const p = playlistOf(video);
  const title = p ? p.title : video.title;
  const sure = await confirmAction({
    title: "Remove from Continue watching?",
    text: `“${title}” leaves the row. Its progress is forgotten, and ${p ? "its videos count" : "it counts"} as not started.`,
    action: "Forget", danger: true,
  });
  if (!sure) return false;
  const r = await request("/api/forget", p ? { playlist: p.id } : { video: video.id });
  if (!r.ok) await tell("It could not be removed", r.error);
  await afterChange();
  return r.ok;
}

// remove_button: after a confirmation, the video — or for a playlist's card,
// its folder — leaves the library; nothing is deleted on disk, and settings
// lists it to bring back.
export async function removeFromLibrary(id, title, kind) {
  const sure = await confirmAction({
    title: "Remove from the library?",
    text: `“${title}” leaves the library. Nothing is deleted from the disk: the ${kind} can be brought back in Settings.`,
    action: "Remove", danger: true,
  });
  if (!sure) return false;
  const r = await request("/api/library/remove", { id });
  if (!r.ok) await tell("It could not be removed", r.error);
  await afterChange();
  return r.ok;
}

// ------------------------------------------------------------------ the view

// continue_row: scrolled sideways by an arrow button at either end, shown
// while there is more on that side, by the wheel or by touch; no scrollbar.
const row = $("continue");

function updateRowArrows() {
  const max = row.scrollWidth - row.clientWidth;
  $("continue-left").hidden = row.scrollLeft <= 1;
  $("continue-right").hidden = row.scrollLeft >= max - 1;
}

function scrollRow(direction) {
  row.scrollBy({ left: direction * Math.max(row.clientWidth * 0.8, 280), behavior: "smooth" });
}

$("continue-left").addEventListener("click", () => scrollRow(-1));
$("continue-right").addEventListener("click", () => scrollRow(1));
row.addEventListener("scroll", updateRowArrows, { passive: true });
window.addEventListener("resize", updateRowArrows);
// A vertical wheel over the row moves it sideways while it has more that
// way; at either end the page scrolls as usual.
row.addEventListener("wheel", (e) => {
  if (e.ctrlKey || Math.abs(e.deltaX) >= Math.abs(e.deltaY)) return;
  const max = row.scrollWidth - row.clientWidth;
  const delta = e.deltaMode === 1 ? e.deltaY * 40 : e.deltaMode === 2 ? e.deltaY * row.clientWidth : e.deltaY;
  if ((delta < 0 && row.scrollLeft <= 0) || (delta > 0 && row.scrollLeft >= max - 1)) return;
  e.preventDefault();
  row.scrollLeft += delta;
}, { passive: false });

let narrowedTo = null;     // the playlist the grid is narrowed to, or null
let shownOnce = false;     // whether the view was drawn since it was opened

// The grid filtered by card title; continue_row is hidden while searching
// and while the grid is narrowed to a playlist, and no_results shown when a
// search matches no card.
export function applySearch() {
  if (!shownOnce || !state.catalogue) return;
  const q = state.query.trim().toLowerCase();
  let shown = 0;
  for (const c of $("grid").children) {
    const match = !q || c.dataset.title.includes(q);
    c.hidden = !match;
    if (match) shown++;
  }
  $("no-results").hidden = !(q && shown === 0);
  $("continue-row").hidden = narrowedTo !== null || state.catalogue.continue.length === 0 || q !== "";
  updateRowArrows();
}

function render() {
  const cat = state.catalogue;
  // a card per playlist, at the video to go on with, and one per video
  // outside any (the server's `continue`, most recently watched first)
  $("continue").replaceChildren(...cat.continue.filter((id) => cat.videos[id]).map((id) => {
    const v = cat.videos[id];
    return videoCard(v, titleInPlaylist(v), "Remove from Continue watching", () => forget(v));
  }));
  const heading = $("library-heading");
  if (narrowedTo === null) {
    heading.replaceChildren("Library");
    // playlists first, each group by name
    const playlists = [...cat.playlists].sort(byTitle);
    const videos = cat.standalone.map((id) => cat.videos[id]).sort(byTitle);
    $("grid").replaceChildren(...playlists.map(playlistCard),
      ...videos.map((v) => videoCard(v, v.title, "Remove from library", () => removeFromLibrary(v.id, v.title, "video"))));
  } else {
    const p = cat.playlists.find((x) => x.id === narrowedTo);
    heading.replaceChildren(el("a", { href: "/", class: "crumb", text: "Library" }), " › ",
      p ? p.title : "This playlist is no longer in the library");
    $("grid").replaceChildren(...(p ? p.videos.map((id) => {
      const v = cat.videos[id];
      const ep = episodeOf(v);
      return videoCard(v, ep ? `${ep} · ${v.title}` : v.title, "Remove from library",
        () => removeFromLibrary(v.id, v.title, "video"));
    }) : []));
  }
  shownOnce = true;
  applySearch();
}

// `playlist`, when given, narrows the grid to that playlist's videos.
export async function showHome(playlist = null) {
  narrowedTo = playlist;
  shownOnce = false;
  document.title = "MiruTube";
  await loadCatalogue();
  row.scrollLeft = 0;
  render();
}

listen("query", applySearch);
listen("rescanned", () => {
  if (!$("home").hidden && state.catalogue) render();
});
