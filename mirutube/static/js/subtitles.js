// player#subtitles: the chosen track drawn by the page over the picture rather
// than by the browser (decisions#own-subtitles). The browser loads the track's
// cues and keeps them `hidden`; this layer shows the active ones, sized from
// the height of the picture times the household's Subtitle size, and lifted
// above now_playing wherever the bar lies over the picture
// (behaviour#subtitle-size).

import { $ } from "./dom.js";
import { state, listen } from "./state.js";

const player = $("player");
const layer = $("subtitle-layer");
const top = $("subtitles-top");
const bottom = $("subtitles-bottom");
const bar = $("now-playing");

const SIZE_OF_HEIGHT = 1 / 22;  // the text's size at 100%, of the picture's height
const MIN_SIZE = 14;            // px: still readable on a phone in the small player
const MARGIN_OF_HEIGHT = 0.05;  // the gap under the lowest line, of the picture's height
const ABOVE_BAR = 12;           // px between the lines and a bar over the picture

let track = null;          // the TextTrack shown, or null
let nativeFull = false;    // in the browser's own full screen for the video alone

// ------------------------------------------------------------------- cues

// Where a cue goes: one that places itself near the top — a sign — keeps to
// the top; any other sits at the bottom.
function atTop(cue) {
  if (cue.line === "auto" || typeof cue.line !== "number") return false;
  return cue.snapToLines ? cue.line >= 0 : cue.line < 50;
}

function cueNode(cue) {
  const line = document.createElement("div");
  line.className = "subtitle-cue";
  const text = document.createElement("span");
  // italic and bold kept: the cue's own markup, as the browser reads it
  if (cue.getCueAsHTML) text.append(cue.getCueAsHTML());
  else text.textContent = cue.text;
  line.append(text);
  return line;
}

function draw() {
  const cues = track && !nativeFull ? [...(track.activeCues || [])] : [];
  top.replaceChildren(...cues.filter(atTop).map(cueNode));
  bottom.replaceChildren(...cues.filter((c) => !atTop(c)).map(cueNode));
}

// Shows `node`'s track, a <track> in the player, or nothing for null.
export function showSubtitleTrack(node) {
  if (track) track.removeEventListener("cuechange", draw);
  track = node ? node.track : null;
  if (track) {
    track.mode = nativeFull ? "showing" : "hidden";
    track.addEventListener("cuechange", draw);
  }
  draw();
}

// In the browser's own full screen for the video alone, as on an iPhone, the
// page draws nothing there: the browser shows the track itself.
player.addEventListener("webkitbeginfullscreen", () => {
  nativeFull = true;
  if (track) track.mode = "showing";
  draw();
});
player.addEventListener("webkitendfullscreen", () => {
  nativeFull = false;
  if (track) track.mode = "hidden";
  draw();
});

// ---------------------------------------------------------------- placing

// The picture within the player — a video of another shape than the player
// leaves bars beside or above it — in the player-wrap's coordinates.
function picture() {
  const w = player.clientWidth, h = player.clientHeight;
  const vw = player.videoWidth || 16, vh = player.videoHeight || 9;
  const scale = Math.min(w / vw, h / vh) || 0;
  const pw = vw * scale, ph = vh * scale;
  return { left: player.offsetLeft + (w - pw) / 2, top: player.offsetTop + (h - ph) / 2, width: pw, height: ph };
}

function place() {
  if (player.hidden || !player.clientHeight) return;
  const p = picture();
  layer.style.left = p.left + "px";
  layer.style.top = p.top + "px";
  layer.style.width = p.width + "px";
  layer.style.height = p.height + "px";
  const size = (state.shared && state.shared.subtitle_size) || 100;
  layer.style.fontSize = Math.max(MIN_SIZE, p.height * SIZE_OF_HEIGHT * size / 100).toFixed(1) + "px";
  // above the bar wherever it lies over the picture and shows
  let lift = p.height * MARGIN_OF_HEIGHT;
  if (!document.body.classList.contains("bar-hidden")) {
    const pictureBottom = layer.getBoundingClientRect().bottom;
    const over = pictureBottom - bar.getBoundingClientRect().top;
    if (over > 0) lift = Math.max(lift, Math.min(over + ABOVE_BAR, p.height * 0.6));
  }
  bottom.style.bottom = lift.toFixed(1) + "px";
}

const placing = new ResizeObserver(place);
placing.observe(player);
placing.observe(bar);
for (const type of ["loadedmetadata", "resize"]) player.addEventListener(type, place);
window.addEventListener("resize", place);
window.addEventListener("scroll", place, { passive: true });
document.addEventListener("fullscreenchange", place);
document.addEventListener("webkitfullscreenchange", place);
// the bar shows and hides over a full-screen video by the body's classes
new MutationObserver(place).observe(document.body, { attributes: true, attributeFilter: ["class"] });
listen("settings", place);
listen("player", () => { if (!layer.style.width) place(); });
