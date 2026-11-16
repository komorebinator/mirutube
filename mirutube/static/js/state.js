// What the page knows and shares between its parts (page#fields), the way the
// parts tell each other that something changed, and where the page is.

import { getJSON } from "./api.js";

export const state = {
  catalogue: null,   // page#fields#catalogue: as the server last returned it
  query: "",         // page#fields#query: the text in the search box
  device: null,      // page#fields#device: {id, name}, set below
  devices: [],       // page#fields#devices: as the server last sent them, this one among them
  devicesClock: 0,   // the server's clock minus this one's, in seconds, when they came
  current: null,     // the video on the watch view, from the catalogue
  audio: null,       // the sound track it plays with, one of current.audio, or null
  subtitle: null,    // the subtitle track shown, one of current.subtitles, or null
  arriving: null,    // {video, position, audio, subtitle, volume, speed}: playback moving here, until watch shows it
  sentTo: null,      // the device this one sent the video on screen to, which the bar then acts on
  shared: { subtitle_size: 100, seed_torrents: true }, // page#fields#shared_settings, as the server last sent them
  jobs: [],          // page#fields#jobs: what is coming into the library, as the server last sent it
};

// ------------------------------------------------------------- notifications

// The parts listen for what concerns them rather than calling each other:
// `catalogue`, `devices`, `player` (what the local video does), `tracks`,
// `route`, `ended`, `settings` (the shared settings), `continue`
// (continue_row changed on some device) and `jobs` (what is coming in).
const listeners = new Map();

export function listen(name, fn) {
  if (!listeners.has(name)) listeners.set(name, []);
  listeners.get(name).push(fn);
}

export function notify(name, ...args) {
  for (const fn of listeners.get(name) || []) {
    try {
      fn(...args);
    } catch (e) {
      console.error(e);
    }
  }
}

// ------------------------------------------------------------------- routes

// Every view has a short address of its own (behaviour#short-links): / home,
// /playlist/<id> home narrowed to one playlist, /watch/<id>, /settings.
export const watchHref = (id) => "/watch/" + encodeURIComponent(id);
export const playlistHref = (id) => "/playlist/" + encodeURIComponent(id);

export function currentRoute() {
  const path = location.pathname;
  for (const view of ["watch", "playlist"]) {
    const prefix = "/" + view + "/";
    if (path.startsWith(prefix)) {
      let arg = path.slice(prefix.length);
      try { arg = decodeURIComponent(arg); } catch (e) { /* kept as it is */ }
      return { view, arg };
    }
  }
  if (path === "/settings") return { view: "settings", arg: null };
  return { view: "home", arg: null };
}

export const onWatch = () => currentRoute().view === "watch";

let router = () => {};
export function setRouter(fn) {
  router = fn;
}

// Goes to `href`, one of the addresses above, without loading the page
// again; going where the page already is shows it again, as when playback
// moves here for the video already on screen.
export function navigate(href) {
  if (location.pathname !== href) history.pushState(null, "", href);
  router();
}

// ---------------------------------------------------------------- catalogue

export async function loadCatalogue() {
  state.catalogue = await getJSON("/api/catalogue");
  // a change to continue_row that came while the catalogue was on its way
  if (lastContinue && lastContinue.version > (state.catalogue.continue_version || 0)) applyContinue(lastContinue, false);
  notify("catalogue");
  return state.catalogue;
}

// continue_row as the server sent it, whenever it changed on any device
// (behaviour#resume#scenarios#live-row): the row's videos and their
// positions, numbered, so the newer of it and the catalogue is the one kept.
let lastContinue = null;

export function applyContinue(data, tell = true) {
  if (!lastContinue || data.version > lastContinue.version) lastContinue = data;
  const cat = state.catalogue;
  if (!cat || data.version <= (cat.continue_version || 0)) return;
  for (const [id, p] of Object.entries(data.videos)) {
    if (!cat.videos[id]) continue;
    cat.videos[id].position = p.position;
    cat.videos[id].watched = p.watched;
  }
  cat.continue = data.continue.filter((id) => cat.videos[id]);
  cat.continue_version = data.version;
  if (tell) notify("continue");
}

export const videoOf = (id) => (state.catalogue && id ? state.catalogue.videos[id] || null : null);

export function playlistOf(video) {
  if (!state.catalogue || !video || video.playlist === null || video.playlist === undefined) return null;
  return state.catalogue.playlists.find((p) => p.id === video.playlist) || null;
}

export const byTitle = (a, b) => a.title.localeCompare(b.title, undefined, { numeric: true, sensitivity: "base" });

// The video a playlist card opens: the video to go on with, as continue_row
// shows it (behaviour#resume#scenarios#one-card), else its first video not
// yet watched.
export function entryOf(playlist) {
  const cat = state.catalogue;
  const going = cat.continue.find((id) => cat.videos[id] && cat.videos[id].playlist === playlist.id);
  if (going) return going;
  const vids = playlist.videos.map((id) => cat.videos[id]);
  return (vids.find((v) => !v.watched) || vids[0]).id;
}

// `E12`, or `S2 · E12`, for an episode of a series; "" for anything else.
export function episodeOf(video) {
  const p = playlistOf(video);
  return p && p.series && video.episode !== null && video.episode !== undefined
    ? (video.season ? `S${video.season} · ` : "") + `E${video.episode}` : "";
}

// A video as continue_row and now_playing title it: for one in a playlist the
// playlist and the episode, `Fullmetal Alchemist Brotherhood · E12`.
export function titleInPlaylist(video) {
  const p = playlistOf(video);
  if (!p) return video.title;
  return p.title + " · " + (episodeOf(video) || video.title);
}

function neighbour(video, step) {
  const p = playlistOf(video);
  if (!p) return null;
  const i = p.videos.indexOf(video.id);
  const j = i + step;
  return i >= 0 && j >= 0 && j < p.videos.length ? state.catalogue.videos[p.videos[j]] : null;
}

export const nextOf = (video) => neighbour(video, 1);
export const previousOf = (video) => neighbour(video, -1);

// Subtitle and sound choices are kept per playlist; a video outside any keeps
// its own.
export function choiceKey(video) {
  return video.playlist !== null && video.playlist !== undefined ? video.playlist : video.id;
}

// Where a video starts, in seconds of its own timeline: the stored position,
// or the beginning once it was watched.
export function startOf(video) {
  return !video.watched && video.position > 0 ? video.position : 0;
}

// The video last watched — the first to go on with — which now_playing shows
// while nothing plays anywhere.
export function lastWatched() {
  const cat = state.catalogue;
  if (!cat) return null;
  return cat.continue.map((id) => cat.videos[id]).find((v) => v && v.playable) || null;
}

// ------------------------------------------------------------------- device

// This page's device: an id made once per tab and kept for the tab's life, so
// a reload keeps it and two tabs are two devices; and a name shared by the
// whole browser, `Chrome on Linux` until the user renames it.
const NAME_KEY = "mirutube.device-name";
const ID_KEY = "mirutube.device-id";
// what LTube, MiruTube's earlier name, kept them under: read once, so a
// device keeps its name across the rename
const OLD_KEYS = { [NAME_KEY]: "ltube.device-name", [ID_KEY]: "ltube.device-id" };

function storage(area) {
  try {
    return window[area] || null;
  } catch (e) {
    return null; // storage refused, as in some private windows
  }
}

export function stored(area, key) {
  const s = storage(area);
  if (!s) return null;
  try {
    let value = s.getItem(key);
    if (value === null && OLD_KEYS[key]) {
      value = s.getItem(OLD_KEYS[key]);
      if (value !== null) {
        s.setItem(key, value);
        s.removeItem(OLD_KEYS[key]);
      }
    }
    return value;
  } catch (e) {
    return null;
  }
}

export function store(area, key, value) {
  const s = storage(area);
  try {
    if (s) s.setItem(key, value);
  } catch (e) { /* not kept: still works until reload */ }
}

// `Chrome on Linux`, from what the browser says of itself.
export function defaultDeviceName(ua = (navigator && navigator.userAgent) || "") {
  const browser = /Edg\//.test(ua) ? "Edge" : /OPR\//.test(ua) ? "Opera"
    : /Firefox\/|FxiOS/.test(ua) ? "Firefox" : /Chrome\/|CriOS/.test(ua) ? "Chrome"
      : /Safari\//.test(ua) ? "Safari" : "Browser";
  const os = /Android/.test(ua) ? "Android" : /iPhone|iPad|iPod/.test(ua) ? "iOS"
    : /CrOS/.test(ua) ? "ChromeOS" : /Windows/.test(ua) ? "Windows"
      : /Macintosh|Mac OS X/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : "";
  return os ? `${browser} on ${os}` : browser;
}

function newDeviceId() {
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes); // randomUUID needs a secure context; the home network is plain http
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

state.device = {
  id: stored("sessionStorage", ID_KEY) || newDeviceId(),
  name: stored("localStorage", NAME_KEY) || defaultDeviceName(),
};
store("sessionStorage", ID_KEY, state.device.id);

export function renameDevice(name) {
  state.device.name = name.slice(0, 80);
  store("localStorage", NAME_KEY, state.device.name);
}
