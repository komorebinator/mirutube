// Full screen (behaviour#full-screen). A browser goes full screen only from
// a touch on its own page, so it is always this page's (decisions#fullscreen-here).

import { $ } from "./dom.js";

// -------------------------------------------------------------- full screen

// Full screen is always the whole page, so the now_playing bar stays on
// screen; while a video is on screen playing here
// (`videoHere`) the page shows it alone, filling the screen, with the bar over
// its bottom — anywhere else it is MiruTube itself that fills the screen.
let videoHere = false;

const fullscreenElement = () => document.fullscreenElement || document.webkitFullscreenElement || null;
export const isFullscreen = () => !!fullscreenElement();

// Whether this browser can put the page in full screen, or — as on an iPhone,
// which has full screen for a video alone — the video playing here.
export function canFullscreen() {
  if (document.fullscreenEnabled || document.webkitFullscreenEnabled) return true;
  return videoHere && !!$("player").webkitEnterFullscreen;
}

// Said on every tick of the video; only a change redraws, or the bar over a
// full-screen video would be shown again four times a second.
export function setVideoHere(on) {
  if (videoHere === !!on) return;
  videoHere = !!on;
  update();
}

function update() {
  const full = isFullscreen();
  document.body.classList.toggle("page-full", full);
  document.body.classList.toggle("video-full", full && videoHere);
  if (full && videoHere) reveal();
  else {
    clearTimeout(hideTimer);
    hideTimer = null;
    document.body.classList.remove("bar-hidden");
  }
}

document.addEventListener("fullscreenchange", update);
document.addEventListener("webkitfullscreenchange", update);

// Whether the page went full screen; false when the browser refused.
export async function enterFullscreen() {
  if (isFullscreen()) return true;
  const root = document.documentElement;
  const request = root.requestFullscreen || root.webkitRequestFullscreen;
  if (request) {
    try {
      await request.call(root, { navigationUI: "hide" });
      return true;
    } catch (e) {
      return false;
    }
  }
  // a browser with full screen for a video alone, as on an iPhone
  const player = $("player");
  if (videoHere && player.webkitEnterFullscreen) {
    try {
      player.webkitEnterFullscreen();
      return true;
    } catch (e) {
      return false;
    }
  }
  return false;
}

export async function exitFullscreen() {
  if (!isFullscreen()) return;
  const exit = document.exitFullscreen || document.webkitExitFullscreen;
  try {
    await exit.call(document);
  } catch (e) { /* already left */ }
}

export function toggleFullscreen() {
  return isFullscreen() ? exitFullscreen() : enterFullscreen();
}

// -------------------------------------------------- the bar over the video

// In full screen over a video the bar shows when the pointer moves, and hides
// HIDE_AFTER after it last moved, wherever the pointer rests — the bar itself
// included, as web players do; it stays only while the video is paused or one
// of its menus is open.
const HIDE_AFTER = 5000;   // ms the bar stays after the pointer last moved
let hideTimer = null;
const held = new Set();    // why the bar stays: the video paused, a menu of it open

function reveal() {
  document.body.classList.remove("bar-hidden");
  clearTimeout(hideTimer);
  hideTimer = document.body.classList.contains("video-full") ? setTimeout(hide, HIDE_AFTER) : null;
}

function hide() {
  hideTimer = null;
  if (!document.body.classList.contains("video-full")) return;
  if (held.size) reveal();
  else document.body.classList.add("bar-hidden");
}

// `reason` keeps the bar on screen while `on`.
// Only a change shows the bar again: the bar is drawn several times a second
// while a video plays, and says each time that no menu is open — a reveal
// for each would keep the bar from ever hiding.
export function holdBar(reason, on) {
  if (held.has(reason) === !!on) return;
  if (on) held.add(reason);
  else held.delete(reason);
  if (document.body.classList.contains("video-full")) reveal();
}

for (const type of ["pointermove", "pointerdown", "keydown"]) {
  document.addEventListener(type, () => {
    if (document.body.classList.contains("video-full")) reveal();
  }, { passive: true });
}
