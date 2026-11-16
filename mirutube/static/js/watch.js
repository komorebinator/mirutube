// watch: player with its details, and playlist_panel beside them. The video
// has no controls of its own — a click on it plays or pauses, and everything
// else is in now_playing (decisions#one-bar).

import { $, el, formatTime } from "./dom.js";
import { getJSON } from "./api.js";
import {
  state, listen, loadCatalogue, videoOf, playlistOf, episodeOf, nextOf, startOf, watchHref, playlistHref,
  navigate, onWatch,
} from "./state.js";
import {
  player, playsHere, loadPlayer, showUnplayable, startSubtitles, initialAudio, reportDevice, leave,
} from "./player.js";
import { setVideoHere } from "./screen.js";
import { target, act, elsewhereDevice } from "./devices.js";
import { thumbBox } from "./home.js";
import { detailsRows } from "./format.js";

const COUNTDOWN = 5;       // seconds before the next video starts

// ------------------------------------------------------------ next_countdown

let countdownTimer = null;

export function cancelCountdown() {
  clearInterval(countdownTimer);
  countdownTimer = null;
  $("countdown").hidden = true;
}

function startCountdown(next) {
  cancelCountdown();
  let left = COUNTDOWN;
  const show = () => { $("countdown-text").textContent = `Next: ${next.title} in ${left}`; };
  show();
  $("countdown").hidden = false;
  countdownTimer = setInterval(() => {
    left -= 1;
    show();
    if (left <= 0) {
      cancelCountdown();
      navigate(watchHref(next.id));
    }
  }, 1000);
}

$("cancel").addEventListener("click", cancelCountdown);
listen("ended", () => {
  const next = state.current && onWatch() && nextOf(state.current);
  if (next) startCountdown(next);
});
// played again or moved: the countdown is over
player.addEventListener("play", cancelCountdown);
player.addEventListener("seeking", cancelCountdown);

// ------------------------------------------------------------------- player

// A click on the video plays or pauses it — on the device that plays it.
player.addEventListener("click", () => {
  const t = target();
  if (t.kind !== "none") act(t.playing ? "pause" : "play");
});

// The elsewhere state: the video on screen plays on another device, or this
// one sent it there and it stands paused there. The page then shows it under
// `Playing on <device>`, and the bar acts on that device; full screen shows
// the video only while it plays here.
function renderElsewhere() {
  const sent = state.sentTo && state.devices.find((x) => x.id === state.sentTo);
  const d = sent || (playsHere() ? elsewhereDevice() : null);
  $("elsewhere").hidden = !d;
  if (d) $("elsewhere").textContent = "Playing on " + d.name;
  setVideoHere(playsHere() && !(d && player.paused));
}

listen("devices", () => { if (onWatch()) renderElsewhere(); });
listen("player", () => { if (onWatch()) renderElsewhere(); });

// ------------------------------------------------------------ playlist_panel

function markPlaylist() {
  for (const li of $("playlist-items").children) {
    const v = state.catalogue.videos[li.dataset.id];
    if (!v) continue;
    li.classList.toggle("current", !!state.current && v.id === state.current.id);
    li.querySelector(".watched-mark").textContent = v.watched ? "✓" : "";
  }
}

listen("position", markPlaylist);

// Hidden for a video outside any playlist; player and details then stand in
// one centred column.
function setPlaylistVisible(visible) {
  $("playlist-panel").hidden = !visible;
  $("watch-layout").classList.toggle("no-playlist", !visible);
}

function renderPlaylist(video) {
  const p = playlistOf(video);
  setPlaylistVisible(!!p);
  if (!p) {
    $("playlist-items").replaceChildren();
    return;
  }
  $("playlist-title").textContent = p.title;
  $("playlist-items").replaceChildren(...p.videos.map((id) => {
    const v = state.catalogue.videos[id];
    const meta = el("div", { class: "item-meta" },
      el("span", { text: formatTime(v.duration) }),
      el("span", { class: "watched-mark", title: "Watched" }));
    if (!v.playable) meta.append(el("span", { class: "not-playable", text: "Can't play in the browser" }));
    return el("li", { "data-id": id },
      el("a", { href: watchHref(id) },
        thumbBox(id, { small: true }),
        el("div", { class: "item-text" }, el("div", { class: "item-title", text: v.title }), meta)));
  }));
  markPlaylist();
  // scrolled so the current one is in view, without scrolling the page
  const list = $("playlist-items");
  const li = list.querySelector("li.current");
  if (li) list.scrollTop = Math.max(0, li.offsetTop - (list.clientHeight - li.offsetHeight) / 2);
}

// ------------------------------------------------------------------ details

// The title, for a series `S1 · E2` before it, the playlist as a link to home
// narrowed to it; and under them what the file is and how it reaches this
// browser (behaviour#what-plays).
function renderDetails(video) {
  const p = playlistOf(video);
  const ep = episodeOf(video);
  $("video-title").replaceChildren(
    ...(ep ? [el("span", { class: "episode", text: ep + " " })] : []),
    video.title);
  const link = $("playlist-link");
  link.hidden = !p;
  if (p) {
    link.textContent = p.title;
    link.setAttribute("href", playlistHref(p.id));
  }
  loadMediaDetails(video);
}

let detailsFor = null;     // "<video>|<audio>" the details shown are of

export async function loadMediaDetails(video) {
  const audio = state.audio && !state.audio.default ? state.audio.id : null;
  const key = video.id + "|" + (audio || "");
  if (detailsFor === key) return;
  detailsFor = key;
  $("media-details").replaceChildren();
  let details;
  try {
    details = await getJSON("/api/details?video=" + encodeURIComponent(video.id)
      + (audio ? "&audio=" + encodeURIComponent(audio) : ""));
  } catch (e) {
    console.error(e);
    if (detailsFor === key) detailsFor = null;
    return;
  }
  if (detailsFor !== key) return;
  renderMediaDetails(details);
}

export function renderMediaDetails(details) {
  $("media-details").replaceChildren(...detailsRows(details).flatMap(([label, text]) => [
    el("dt", { text: label }), el("dd", { text }),
  ]));
}

// another sound track can change how the video reaches the browser
listen("tracks", () => {
  if (onWatch() && state.current) loadMediaDetails(state.current);
});

// -------------------------------------------------------------------- views

export function leaveWatch() {
  cancelCountdown();
  leave();
  $("elsewhere").hidden = true;
  setVideoHere(false);
}

export async function showWatch(id) {
  await loadCatalogue();
  const video = videoOf(id);
  if (!video) {
    $("video-title").textContent = "This video is no longer in the library.";
    $("playlist-link").hidden = true;
    $("media-details").replaceChildren();
    detailsFor = null;
    setPlaylistVisible(false);
    $("unplayable").hidden = true;
    player.hidden = true;
    return;
  }
  state.current = video;
  // playback moved here from another device goes on from its moment, with
  // its tracks; otherwise the video starts at its stored position
  const moved = state.arriving && state.arriving.video === id ? state.arriving : null;
  state.arriving = null;
  if (moved) {
    video.position = moved.position;
    video.watched = false;
    // as loud and as fast as where it was, and with sound: a video moved is
    // meant to be heard (behaviour#devices)
    if (typeof moved.volume === "number") player.volume = Math.min(1, Math.max(0, moved.volume));
    if (typeof moved.speed === "number" && moved.speed > 0) player.defaultPlaybackRate = player.playbackRate = moved.speed;
    player.muted = false;
  } else {
    const pos = await getJSON("/api/position?video=" + encodeURIComponent(id));
    if (state.current !== video) return;
    video.position = pos.position;
    video.watched = pos.watched;
  }

  document.title = video.title + " · MiruTube";
  if (!video.playable) {
    state.audio = null;
    renderDetails(video);
    renderPlaylist(video);
    showUnplayable(video.reason);
    startSubtitles(video, null);
    renderElsewhere();
    return;
  }
  state.audio = (moved && video.audio.find((t) => t.id === moved.audio)) || initialAudio(video);
  renderDetails(video);
  renderPlaylist(video);
  // a video already playing on another device opens here paused, under
  // `Playing on <device>`, rather than playing twice
  const play = !!moved || !elsewhereDevice();
  const loaded = loadPlayer(video, startOf(video), play);
  startSubtitles(video, moved ? moved.subtitle : undefined);
  renderElsewhere();
  await loaded;
  if (state.current === video) reportDevice();
}


// ---------------------------------------------------------------- fitting

// On a wide window the view fits between top_bar and now_playing and the page
// does not scroll: the player keeps 16:9 and is as large as the column's width
// and the height details leave it allow. A narrow window, and full screen,
// size it by CSS alone.
const playerCol = document.querySelector(".player-col");
const playerArea = document.querySelector(".player-area");
const detailsBox = document.querySelector(".details");
const wide = window.matchMedia("(min-width: 901px)");

function fitPlayer() {
  if (!wide.matches || document.body.classList.contains("video-full") || !playerCol.clientHeight) {
    playerArea.style.width = "";
    return;
  }
  const gap = parseFloat(getComputedStyle(detailsBox).marginTop) || 0;
  const height = Math.max(0, playerCol.clientHeight - detailsBox.offsetHeight - gap);
  playerArea.style.width = Math.floor(Math.min(playerCol.clientWidth, height * 16 / 9)) + "px";
}

const fitter = new ResizeObserver(fitPlayer);
fitter.observe(playerCol);
fitter.observe(detailsBox);
wide.addEventListener("change", fitPlayer);
document.addEventListener("fullscreenchange", fitPlayer);
