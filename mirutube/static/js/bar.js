// now_playing: the bar along the bottom of every page, and the player's only
// set of controls (decisions#one-bar). It shows the device that plays —
// `target()` — and every control acts there, through `act()`.

import { $, el, formatTime, typing } from "./dom.js";
import { ask } from "./dialog.js";
import { idUrl } from "./api.js";
import {
  state, listen, notify, titleInPlaylist, previousOf, nextOf, watchHref, renameDevice, videoOf,
} from "./state.js";
import { player, MSE } from "./player.js";
import { isFullscreen, holdBar } from "./screen.js";
import { target, act, moveTo, openEvents } from "./devices.js";

const bar = $("now-playing");
const seek = $("np-seek");
const volume = $("np-volume");
export const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];

let seeking = false;       // while the seek bar is dragged
let volumeMoving = false;  // while the volume slider is dragged
let volumeTimer = null;    // the volume about to be sent while it moves
let shown = null;          // the target last drawn

const active = (t) => t.kind === "local" || t.kind === "remote";
export const speedLabel = (speed) => String(Math.round(speed * 100) / 100) + "×";

// ------------------------------------------------------------------ drawing

export function render() {
  const t = target();
  shown = t;
  bar.dataset.kind = t.kind;

  // the video, and where it plays
  const v = t.video;
  const title = v ? titleInPlaylist(v) : t.videoId ? t.videoId.split("/").pop() : "Nothing playing";
  $("np-title").textContent = title;
  $("np-title").title = title;
  $("np-where").textContent = t.kind === "local" ? "on this device"
    : t.kind === "remote" ? "on " + t.name + (t.playing ? "" : " · Paused")
      : t.kind === "idle" ? "Last watched" : "";
  const thumb = $("np-thumb");
  if (thumb.dataset.video !== (t.videoId || "")) {
    thumb.dataset.video = t.videoId || "";
    if (t.videoId) thumb.src = idUrl("/thumb/", t.videoId);
    else thumb.removeAttribute("src");
  }
  thumb.hidden = !t.videoId;
  const link = $("np-video");
  if (t.videoId) link.setAttribute("href", watchHref(t.videoId));
  else link.removeAttribute("href");

  // progress
  const duration = t.duration || 0;
  seek.max = String(duration || 1);
  seek.disabled = !(active(t) && duration);
  if (!seeking) seek.value = String(Math.min(t.position || 0, duration || 0));
  const at = seeking ? Number(seek.value) : t.position || 0;
  seek.style.setProperty("--progress", duration ? (Math.min(1, at / duration) * 100).toFixed(2) + "%" : "0%");
  $("np-time").textContent = t.videoId ? formatTime(at) : "";
  $("np-length").textContent = duration ? formatTime(duration) : "";

  // transport
  $("np-previous").disabled = !(active(t) && v && previousOf(v));
  $("np-next").disabled = !(active(t) && v && nextOf(v));
  const toggle = $("np-toggle");
  toggle.disabled = t.kind === "none";
  toggle.classList.toggle("playing", !!t.playing);
  toggle.setAttribute("aria-label", t.playing ? "Pause" : "Play");
  toggle.title = t.playing ? "Pause" : "Play";

  // the bar's own tools
  const local = t.kind === "local";
  $("subs-button").disabled = !(active(t) && v && v.subtitles.length);           // subtitles_menu#no-tracks
  $("audio-button").disabled = !(active(t) && v && v.audio.length > 1 && (MSE || !local)); // audio_menu#one-track
  $("np-mute").disabled = !active(t);
  volume.disabled = !active(t);
  const quiet = !!t.muted || t.volume === 0;
  $("np-mute").classList.toggle("muted", quiet);
  $("np-mute").setAttribute("aria-label", quiet ? "Unmute" : "Mute");
  if (!volumeMoving) volume.value = String(t.muted ? 0 : t.volume === undefined ? 1 : t.volume);
  volume.style.setProperty("--progress", (Number(volume.value) * 100).toFixed(0) + "%");
  $("speed-button").disabled = !active(t);
  $("speed-button").textContent = speedLabel(t.speed || 1);
  $("pip-button").hidden = !(local && document.pictureInPictureEnabled && !player.disablePictureInPicture);
  // only on the device the bar acts on: another device's browser would not
  // go full screen without a touch on its own page (decisions#fullscreen-here)
  const full = isFullscreen();
  $("fullscreen-button").hidden = t.kind === "remote";
  $("fullscreen-button").classList.toggle("on", full);
  $("fullscreen-button").setAttribute("aria-label", full ? "Exit full screen" : "Full screen");
  $("fullscreen-button").title = full ? "Exit full screen" : "Full screen";

  for (const name of Object.keys(menus)) if (!$(name + "-list").hidden) menus[name](t);
  for (const name of ["subs", "audio"]) if ($(name + "-button").disabled) closeMenu(name);
}

listen("devices", render);
listen("player", render);
listen("tracks", render);
listen("route", render);
listen("catalogue", render);
// a remote device's position moves on between its reports
setInterval(() => {
  if (shown && shown.kind === "remote" && shown.playing) render();
}, 1000);
document.addEventListener("fullscreenchange", render);
document.addEventListener("webkitfullscreenchange", render);

// ------------------------------------------------------------------- menus

// subtitles_menu, audio_menu, speed and devices_button: each a button
// opening a list upward from the bar, one at a time.
const menus = {
  subs: renderSubtitles,
  audio: renderAudio,
  speed: renderSpeeds,
  devices: renderDevices,
};

function closeMenu(name) {
  $(name + "-list").hidden = true;
  $(name + "-button").setAttribute("aria-expanded", "false");
  holdBar("menu", Object.keys(menus).some((n) => !$(n + "-list").hidden));
}

export function closeMenus() {
  for (const name of Object.keys(menus)) closeMenu(name);
}

function toggleMenu(name) {
  const open = $(name + "-list").hidden;
  closeMenus();
  if (!open) return;
  menus[name](shown || target());
  $(name + "-list").hidden = false;
  $(name + "-button").setAttribute("aria-expanded", "true");
  holdBar("menu", true);
}

for (const name of Object.keys(menus)) {
  $(name + "-button").addEventListener("click", (e) => {
    e.stopPropagation();
    toggleMenu(name);
  });
}
document.addEventListener("click", (e) => {
  for (const name of Object.keys(menus)) {
    if (!$(name + "-menu").contains(e.target)) closeMenu(name);
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeMenus();
});

// Fills an open menu's list — only when what it lists has changed since it
// was last drawn (`key`). The bar is drawn several times a second while a
// video plays, and an entry replaced between the press and the release of
// the mouse button never receives the click.
function fill(list, key, build) {
  if (list.dataset.key === key && list.childElementCount) return;
  list.dataset.key = key;
  list.replaceChildren(...build());
}

// One entry of a menu list, checked or not; `reason` greys it out.
function menuItem(label, checked, reason, onChoose) {
  const button = el("button", {
    type: "button", role: "menuitemradio", "aria-checked": String(checked),
    class: checked ? "checked" : null, disabled: !!reason,
  },
  el("span", { class: "check", text: checked ? "✓" : "" }),
  el("span", { class: "label", text: label }),
  reason ? el("span", { class: "reason", text: reason }) : null);
  button.addEventListener("click", (e) => {
    e.stopPropagation();
    onChoose();
  });
  return el("li", {}, button);
}

// The playing video's subtitle tracks by label with `Off` first; an
// unavailable one greyed out with the reason beside it.
function renderSubtitles(t) {
  const tracks = t.video ? t.video.subtitles : [];
  fill($("subs-list"), JSON.stringify([t.videoId, t.subtitle || null, tracks.map((x) => [x.id, x.available])]), () => [
    menuItem("Off", !t.subtitle, null, () => choose("subs", "subtitle", { subtitle: null })),
    ...tracks.map((track) => menuItem(track.label, t.subtitle === track.id, track.available ? null : track.reason,
      () => choose("subs", "subtitle", { subtitle: track.id })))]);
}

// The playing video's sound tracks by label; choosing one keeps the position.
function renderAudio(t) {
  const tracks = t.video ? t.video.audio : [];
  const chosen = t.audio || (tracks.find((a) => a.default) || {}).id;
  fill($("audio-list"), JSON.stringify([t.videoId, chosen || null, tracks.map((x) => x.id)]), () =>
    tracks.map((track) => menuItem(track.label, track.id === chosen, null,
      () => choose("audio", "audio", { audio: track.default ? null : track.id }))));
}

function renderSpeeds(t) {
  const now = t.speed || 1;
  fill($("speed-list"), String(now), () => SPEEDS.map((s) => menuItem(speedLabel(s), Math.abs(s - now) < 0.01, null,
    () => choose("speed", "speed", { speed: s }))));
}

function choose(menu, action, args) {
  closeMenu(menu);
  act(action, args);
}

// devices_button: every device with what it plays, this one marked `This
// device` with a rename action; choosing one moves playback there.
function renderDevices(t) {
  const key = JSON.stringify([t.kind, t.id, state.device.name,
    state.devices.map((d) => [d.id, d.name, d.video, d.playing])]);
  fill($("devices-list"), key, () => deviceItems(t));
}

function deviceItems(t) {
  const items = state.devices.map((d) => {
    const v = videoOf(d.video);
    const what = !d.video ? "Nothing playing"
      : (v ? titleInPlaylist(v) : d.video.split("/").pop()) + (d.playing ? "" : " · Paused");
    const mine = d.id === state.device.id;
    const movable = t.kind !== "none" && t.id !== d.id;
    const choose = el("button", {
      type: "button", class: "device-choose", role: "menuitem", disabled: !movable,
      title: movable ? "Play on " + d.name : null,
    },
    el("span", { class: "device-name", text: d.name }),
    mine ? el("span", { class: "device-mine", text: "This device" }) : null,
    el("span", { class: "device-what", text: what }));
    choose.addEventListener("click", (e) => {
      e.stopPropagation();
      closeMenu("devices");
      moveTo(d.id);
    });
    const item = el("li", { class: mine ? "mine" : null }, choose);
    if (mine) {
      const rename = el("button", { type: "button", class: "device-rename", text: "Rename" });
      rename.addEventListener("click", async (e) => {
        e.stopPropagation();
        closeMenu("devices");
        const answer = await ask({
          title: "Name this device", text: "Other devices show it under this name.",
          action: "Rename", value: state.device.name,
        });
        const name = (answer || "").trim();
        if (!name || name === state.device.name) return;
        renameDevice(name);
        openEvents(); // the stream names the device anew; its report follows on open
        render();
      });
      item.append(rename);
    }
    return item;
  });
  return items.length ? items : [el("li", { class: "empty", text: "Connecting…" })];
}

// ----------------------------------------------------------------- controls

$("np-toggle").addEventListener("click", () => {
  const t = target();
  act(t.playing ? "pause" : "play");
});
$("np-previous").addEventListener("click", () => act("previous"));
$("np-next").addEventListener("click", () => act("next"));

// progress: the time shown follows the drag, and the seek is done on release
seek.addEventListener("input", () => {
  seeking = true;
  render();
});
seek.addEventListener("change", () => {
  seeking = false;
  act("seek", { position: Number(seek.value) });
  render();
});

// volume: the speaker mutes and unmutes; the slider sets the volume, sent
// to another device a few times a second while it moves
$("np-mute").addEventListener("click", () => {
  const t = target();
  const quiet = !!t.muted || t.volume === 0;
  if (quiet && t.volume === 0) act("volume", { volume: 0.5, muted: false });
  else act("volume", { muted: !quiet });
});
volume.addEventListener("input", () => {
  volumeMoving = true;
  volume.style.setProperty("--progress", (Number(volume.value) * 100).toFixed(0) + "%");
  if (volumeTimer) return;
  volumeTimer = setTimeout(sendVolume, 150);
});
volume.addEventListener("change", () => {
  clearTimeout(volumeTimer);
  sendVolume();
  volumeMoving = false;
  render();
});
function sendVolume() {
  volumeTimer = null;
  const level = Number(volume.value);
  act("volume", level > 0 ? { volume: level, muted: false } : { volume: 0 });
}

$("pip-button").addEventListener("click", () => {
  if (document.pictureInPictureElement) document.exitPictureInPicture().catch(() => {});
  else if (player.requestPictureInPicture) player.requestPictureInPicture().catch(() => {});
});
player.addEventListener("enterpictureinpicture", render);
player.addEventListener("leavepictureinpicture", render);

// full screen: the page — over a video playing here, the video
// (behaviour#full-screen)
$("fullscreen-button").addEventListener("click", () => {
  act("fullscreen", { fullscreen: !isFullscreen() });
});

// over a full-screen video the bar stays while the video is paused here
player.addEventListener("pause", () => holdBar("paused", true));
player.addEventListener("play", () => holdBar("paused", false));

// Space plays and pauses what the bar shows, as on any video site
document.addEventListener("keydown", (e) => {
  if (e.key !== " " || e.ctrlKey || e.altKey || e.metaKey || typing(e.target)) return;
  const tag = e.target && e.target.tagName ? e.target.tagName.toLowerCase() : "";
  if (tag === "button" || tag === "a") return;
  const t = target();
  if (t.kind === "none") return;
  e.preventDefault();
  act(t.playing ? "pause" : "play");
});

