// The video element on watch — what plays on this device: loading a video as
// it is or piece by piece, its sound and subtitle tracks, starting it with
// sound or not at all, sending it away to another device, the position and
// what this device plays reported to the server, and `local`: every control
// of now_playing, done here.

import { $, el } from "./dom.js";
import { getJSON, idUrl, postJSON } from "./api.js";
import {
  state, notify, listen, navigate, watchHref, choiceKey, nextOf, previousOf,
} from "./state.js";
import { enterFullscreen, exitFullscreen } from "./screen.js";
import { showSubtitleTrack } from "./subtitles.js";
import { t, fromServer } from "./i18n.js";

export const player = $("player");

const REPORT_EVERY = 5000;   // ms between position reports while playing
let reportTimer = null;

// The video on screen plays here, as opposed to being shown while it plays
// elsewhere, sent away from here, or not playable at all.
export const playsHere = () => !!(state.current && state.current.playable && !player.hidden && !state.sentTo);

// --------------------------------------------------------------- subtitles

function clearTracks() {
  showSubtitleTrack(null);
  for (const t of [...player.querySelectorAll("track")]) t.remove();
}

// The browser loads the track and keeps it hidden; player#subtitles draws it
// (decisions#own-subtitles).
function showTrack(video, track) {
  clearTracks();
  if (!track) return;
  const node = el("track", {
    kind: "subtitles", label: track.label, srclang: track.language || "und",
    src: idUrl("/subtitle/", video.id) + "?track=" + encodeURIComponent(track.id),
  });
  player.append(node);
  showSubtitleTrack(node);
}

// The track a video starts with: subtitles switched on stay on for the next
// video of the same playlist — the track with the same label (the same
// translation, signs or not), else a track in the same language that is not
// signs-only, else none; the choice, kept as it was, comes back with the next
// video that has it (behaviour#subtitles-follow).
export function initialTrack(video) {
  const choice = state.catalogue.subtitle_choices[choiceKey(video)];
  if (!choice) return null;
  const tracks = video.subtitles.filter((t) => t.available);
  return tracks.find((t) => t.label === choice.label)
    || (choice.language ? tracks.find((t) => t.language === choice.language && !t.signs) : null)
    || null;
}

// The subtitles a video starts with: `given`, when not undefined, is the
// track by its id, or null for none — as when playback moves here.
export function startSubtitles(video, given) {
  state.subtitle = given === undefined ? initialTrack(video)
    : video.subtitles.find((t) => t.id === given && t.available) || null;
  showTrack(video, state.subtitle);
  notify("tracks");
}

// subtitles_menu: the track chosen, or null for Off; kept for the playlist.
export function chooseSubtitle(track) {
  const video = state.current;
  if (!video || (track && !track.available)) return;
  state.subtitle = track;
  showTrack(video, track);
  const choice = track ? { label: track.label, language: track.language || null } : null;
  state.catalogue.subtitle_choices[choiceKey(video)] = choice;
  postJSON("/api/subtitle-choice", {
    playlist: choiceKey(video),
    label: choice ? choice.label : null,
    language: choice ? choice.language : null,
  });
  reportDevice();
  notify("tracks");
}

// ------------------------------------------------------------------- sound

// A browser plays only a file's first audio stream, so any other track — of
// the file's own or an external dub — is played by sending the video in
// pieces with that sound in them (decisions#audio-in-pieces).
export const MSE = window.MediaSource || window.ManagedMediaSource || null;

function streamed(video, track) {
  return video.delivery === "stream" || (!!track && !track.default);
}

export const defaultAudio = (video) => video.audio.find((t) => t.default) || video.audio[0] || null;

// The track a video starts with: the choice stays for the next videos of the
// same playlist — the track with the same label, else one in the same
// language, else the default, the file's first track (behaviour#audio-tracks).
export function initialAudio(video) {
  const choice = state.catalogue.audio_choices[choiceKey(video)];
  const fallback = defaultAudio(video);
  if (!choice) return fallback;
  return video.audio.find((t) => t.label === choice.label)
    || (choice.language ? video.audio.find((t) => t.language === choice.language) : null)
    || fallback;
}

// audio_menu: choosing a track keeps the position — the player is loaded
// again with the new sound and goes on from the same moment, playing if it
// was.
export function chooseAudio(track) {
  const video = state.current;
  if (!video || !video.playable || !track || (state.audio && track.id === state.audio.id)) return;
  if (!track.default && !MSE) return; // no other track can play here
  const at = currentPosition();
  const play = !player.paused && !player.ended;
  state.audio = track;
  const choice = track.default ? null : { label: track.label, language: track.language || null };
  state.catalogue.audio_choices[choiceKey(video)] = choice;
  postJSON("/api/audio-choice", {
    playlist: choiceKey(video),
    label: choice ? choice.label : null,
    language: choice ? choice.language : null,
  });
  loadPlayer(video, at, play);
  reportDevice();
  notify("tracks");
}

// ------------------------------------------------------------------ stream

// A `stream` video, or one with another sound track, is fed to the player
// piece by piece through Media Source Extensions (decisions#stream): its plan
// from /stream/<video>, then its pieces a few ahead of the playhead, and on a
// seek the piece at the new position first. Every piece is a whole
// fragmented MP4 keeping the video's timestamps, so pieces are appended in any
// order, and seeking works as for any other video.
const AHEAD = 3;           // pieces kept ready from the one at the playhead
const KEEP_BEHIND = 60;    // seconds kept behind the playhead
const RETRY_AFTER = 3000;  // ms before asking again after a piece failed
let stream = null;         // the stream feeding the player, if any

// Positions are on the video's own timeline; a stream video's media timeline
// is that moved later by its plan's offset.
export function mediaOffset() {
  return stream ? stream.plan.offset : 0;
}

function pieceAt(plan, t) {
  let i = 0;
  while (i + 1 < plan.starts.length && plan.starts[i + 1] <= t) i++;
  return i;
}

function pieceEnd(plan, i) {
  return i + 1 < plan.starts.length ? plan.starts[i + 1] : plan.duration;
}

function bufferedAt(t) {
  const b = player.buffered;
  for (let k = 0; k < b.length; k++) if (b.start(k) <= t && t < b.end(k)) return true;
  return false;
}

// The playhead, or where the video will start while nothing is loaded yet.
function streamTime(s) {
  return player.readyState > 0 ? player.currentTime : s.startAt;
}

// The first piece missing from the one at the playhead to AHEAD after it,
// or -1.
function missingPiece(s) {
  const first = pieceAt(s.plan, streamTime(s));
  for (let i = first; i < Math.min(first + AHEAD, s.plan.starts.length); i++) {
    if (!s.appended.has(i)) return i;
  }
  return -1;
}

function updated(buffer) {
  return new Promise((resolve, reject) => {
    const off = () => {
      buffer.removeEventListener("updateend", ok);
      buffer.removeEventListener("error", bad);
    };
    const ok = () => { off(); resolve(); };
    const bad = () => { off(); reject(new Error("the browser refused a piece")); };
    buffer.addEventListener("updateend", ok);
    buffer.addEventListener("error", bad);
  });
}

// Drops what lies more than `behind` seconds behind the playhead, and what
// lies past the pieces wanted ahead of it (left there by a seek back).
async function trim(s, behind) {
  const t = streamTime(s);
  const cut = s.plan.starts[pieceAt(s.plan, Math.max(0, t - behind))];
  const last = Math.min(pieceAt(s.plan, t) + AHEAD, s.plan.starts.length) - 1;
  const ahead = pieceEnd(s.plan, last);
  const b = s.buffer.buffered;
  if (cut > 0 && b.length && b.start(0) < cut) {
    s.buffer.remove(0, cut);
    await updated(s.buffer);
  }
  if (ahead < s.plan.duration && b.length && b.end(b.length - 1) > ahead) {
    s.buffer.remove(ahead, s.plan.duration);
    await updated(s.buffer);
  }
  for (const i of [...s.appended]) {
    if (pieceEnd(s.plan, i) <= cut || s.plan.starts[i] >= ahead) s.appended.delete(i);
  }
}

async function append(s, data) {
  try {
    s.buffer.appendBuffer(data);
  } catch (e) {
    if (e.name !== "QuotaExceededError") throw e;
    await trim(s, 0);
    s.buffer.appendBuffer(data);
  }
  await updated(s.buffer);
}

// Fetches and appends the missing pieces one at a time; called again by
// timeupdate and seeking, it does nothing while it already runs.
async function feed(s) {
  if (s.busy) { s.again = true; return; }
  if (stream !== s || !s.buffer || Date.now() - s.failedAt < RETRY_AFTER) return;
  s.busy = true;
  try {
    for (let i = missingPiece(s); i >= 0 && stream === s; i = missingPiece(s)) {
      s.fetching = i;
      s.controller = new AbortController();
      const res = await fetch(idUrl("/stream/", s.video.id) + "?piece=" + i + s.audioQuery,
        { cache: "no-store", signal: s.controller.signal });
      if (!res.ok) throw new Error("piece " + i + ": " + res.status);
      const data = await res.arrayBuffer();
      s.fetching = null;
      if (stream !== s) return;
      await trim(s, KEEP_BEHIND);
      await append(s, data);
      s.appended.add(i);
    }
    // every piece to the end is there: the video can end
    const t = streamTime(s);
    const all = s.plan.starts.every((_, i) => i < pieceAt(s.plan, t) || s.appended.has(i));
    if (stream === s && all && s.source.readyState === "open" && !s.buffer.updating) s.source.endOfStream();
  } catch (e) {
    if (e.name !== "AbortError") {
      console.error(e);
      s.failedAt = Date.now();
      setTimeout(() => feed(s), RETRY_AFTER + 100);
    }
  } finally {
    s.busy = false;
    s.fetching = null;
    if (s.again && stream === s) {
      s.again = false;
      feed(s);
    }
  }
}

// `at` is where it starts, in seconds of the video's own timeline; `track`
// the sound track, named in every request unless it is the default.
// `stale()` tells it a later load has taken over.
async function startStream(video, at, track, stale) {
  const audioQuery = track && !track.default ? "&audio=" + encodeURIComponent(track.id) : "";
  let plan;
  try {
    plan = await getJSON(idUrl("/stream/", video.id) + (audioQuery ? "?" + audioQuery.slice(1) : ""));
  } catch (e) {
    if (state.current === video && !stale()) {
      showUnplayable(track && !track.default && video.delivery !== "stream"
        ? () => t("the sound track {} could not be loaded ({})", track.label, e.message)
        : () => t("its conversion could not be started ({})", e.message));
    }
    return;
  }
  if (state.current !== video || stale()) return;
  if (!MSE || !MSE.isTypeSupported(plan.mime)) {
    return showUnplayable(() => t("this browser cannot play it converted ({})", plan.mime));
  }
  const s = {
    video, plan, source: new MSE(), buffer: null, appended: new Set(),
    busy: false, again: false, fetching: null, controller: null, failedAt: 0,
    startAt: at + plan.offset, audioQuery,
  };
  stream = s;
  if (window.ManagedMediaSource && s.source instanceof window.ManagedMediaSource) {
    player.disableRemotePlayback = true;
  }
  s.url = URL.createObjectURL(s.source);
  s.source.addEventListener("sourceopen", () => {
    if (stream !== s || s.buffer) return;
    s.source.duration = plan.duration;
    s.buffer = s.source.addSourceBuffer(plan.mime);
    s.buffer.mode = "segments";
    feed(s);
  }, { once: true });
  player.src = s.url;
}

function stopStream() {
  if (!stream) return;
  if (stream.controller) stream.controller.abort();
  URL.revokeObjectURL(stream.url);
  stream = null;
}

player.addEventListener("seeking", () => {
  const s = stream;
  if (!s || !s.buffer) return;
  const t = player.currentTime;
  if (!bufferedAt(t) && !bufferedAt(t + 0.5)) s.appended.delete(pieceAt(s.plan, t));
  // the piece at the new position first: one being fetched for elsewhere is
  // given up, and the server stops making it
  if (s.fetching !== null && s.fetching !== missingPiece(s)) s.controller.abort();
  feed(s);
});
player.addEventListener("timeupdate", () => {
  if (stream) feed(stream);
});
// The pieces join within a fraction of a frame, and the first frame of a
// copied video lies a little after 0: playback stuck just before buffered
// media moves on to it.
player.addEventListener("waiting", () => {
  if (!stream) return;
  feed(stream);
  const t = player.currentTime, b = player.buffered;
  for (let k = 0; k < b.length; k++) {
    if (b.start(k) > t && b.start(k) - t < 0.5) {
      player.currentTime = b.start(k);
      return;
    }
  }
});

// ------------------------------------------------------------------ loading

// A video that cannot play says so, instead of a player that stays black
// (behaviour#no-black-screen).
// `reason` is what the server said, or a function writing the page's own
// reason in the language in use, so a change of language writes it again.
let unplayableReason = null;

export function showUnplayable(reason) {
  unplayableReason = reason;
  player.hidden = true;
  $("unplayable").hidden = false;
  writeUnplayable();
  notify("player");
}

function writeUnplayable() {
  const reason = typeof unplayableReason === "function" ? unplayableReason() : fromServer(unplayableReason);
  $("unplayable").textContent = t("This video can't be played in the browser: {}", reason);
}

listen("language", () => { if (!$("unplayable").hidden) writeUnplayable(); });

// Loads `video` into the player with the sound track state.audio, from `at`
// seconds of its own timeline, and plays it when `play`: a `direct` video
// with its default track is sent as it is, anything else is fed piece by
// piece.
let loading = null;        // the latest loadPlayer call; an older one gives way
let playedHere = false;    // whether the video loaded has played on this device
let loadingAt = 0;         // where it starts, the position until the player knows its time
// player#play_flash: play or pause in the middle of the video when it starts
// or pauses here — but not when a video is being loaded, which starts and
// stops it on its own (`flashQuiet`).
let flashQuiet = false;

function flash(kind) {
  const badge = $("play-flash");
  badge.className = "play-flash " + kind;
  void badge.offsetWidth; // the animation starts again when it is shown again
  badge.classList.add("show");
}

export async function loadPlayer(video, at, play) {
  const token = {};
  loading = token;
  loadingAt = at;
  stopStream();
  // the old source goes at once — its sound stops, and with nothing loaded
  // its time is not reported as the position
  flashQuiet = true;
  playedHere = false;
  player.removeAttribute("poster");
  player.removeAttribute("src");
  player.load();
  player.hidden = false;
  $("unplayable").hidden = true;
  player.addEventListener("loadedmetadata", () => {
    if (state.current !== video || loading !== token) return;
    const start = loadingAt + mediaOffset();
    if (loadingAt > 0 && start < player.duration) player.currentTime = start;
    if (play) startPlayback();
    else flashQuiet = false;
  }, { once: true });
  notify("player");
  // the subtitles' <track> stays, and keeps showing, with the new source
  if (streamed(video, state.audio)) await startStream(video, at, state.audio, () => loading !== token);
  else player.src = idUrl("/video/", video.id);
}

// The position of the video on screen, on its own timeline.
export function currentPosition() {
  if (player.readyState === 0) return loadingAt;
  const t = player.currentTime - mediaOffset();
  return isFinite(t) ? Math.max(0, t) : loadingAt;
}

// The length of the video on screen, in seconds.
export function currentDuration() {
  const v = state.current;
  if (v && v.duration) return v.duration;
  const d = player.duration - mediaOffset();
  return isFinite(d) && d > 0 ? d : 0;
}

// ------------------------------------------------------------ sound allowed

// A video never plays without sound: a browser that will not let the page
// start sound before the user touched it refuses play(), and the video stands
// paused at its moment, shown paused on every bar, until play is pressed here
// (decisions#sound-or-paused).
export function startPlayback() {
  const started = player.play();
  if (started && started.catch) {
    started.catch((e) => {
      if (e.name !== "NotAllowedError") return;
      flashQuiet = false;
      reportDevice();
      notify("player");
    });
  }
}

// ---------------------------------------------------------------- reporting

// Positions go to the server every few seconds while the video plays, and on
// pause; so does what this device plays, for the other devices.
export function report(final) {
  const video = state.current;
  if (!video || !video.playable || player.hidden) return;
  // while the player is (re)loading, as when the sound track changes, its
  // time says nothing about the position
  if (final === undefined && player.readyState === 0) return;
  const t = (final !== undefined ? final : player.currentTime) - mediaOffset();
  if (!isFinite(t)) return;
  postJSON("/api/position", { video: video.id, position: Math.max(0, t) }).then((r) => {
    if (r && state.current && r.video === state.current.id) {
      state.current.position = r.position;
      state.current.watched = r.watched;
      notify("position");
    }
  });
}

export function reportDevice() {
  const plays = playsHere();
  postJSON("/api/report", {
    id: state.device.id, name: state.device.name,
    video: plays ? state.current.id : null,
    audio: plays && state.audio ? state.audio.id : null,
    subtitle: plays && state.subtitle ? state.subtitle.id : null,
    position: plays ? currentPosition() : 0,
    playing: plays && !player.paused && !player.ended,
    volume: player.volume, muted: player.muted, speed: player.playbackRate,
  });
}

let reportSoon = null;     // a report of the volume or speed, held while it still moves
function reportLater() {
  clearTimeout(reportSoon);
  reportSoon = setTimeout(reportDevice, 250);
}

function stopReporting() {
  clearInterval(reportTimer);
  reportTimer = null;
}

player.addEventListener("play", () => {
  playedHere = true;
  if (flashQuiet) flashQuiet = false;
  else flash("play");
  stopReporting();
  reportTimer = setInterval(() => { report(); reportDevice(); }, REPORT_EVERY);
  reportDevice();
  notify("player");
});
player.addEventListener("pause", () => {
  if (!flashQuiet && !player.ended && player.currentSrc) flash("pause");
  stopReporting();
  if (!player.ended) report();
  reportDevice();
  notify("player");
});
player.addEventListener("seeked", () => {
  reportDevice();
  notify("player");
});
player.addEventListener("ended", () => {
  stopReporting();
  report(player.duration);
  reportDevice();
  notify("player");
  notify("ended");
});
player.addEventListener("volumechange", () => {
  reportLater();
  notify("player");
});
player.addEventListener("ratechange", () => {
  reportLater();
  notify("player");
});
for (const type of ["timeupdate", "loadedmetadata", "durationchange"]) {
  player.addEventListener(type, () => notify("player"));
}

// ------------------------------------------------------------------ leaving

// Unloads the player: what played stops, and nothing of it is reported.
function unload() {
  stopReporting();
  stopStream();
  loading = null;
  player.pause();
  clearTracks();
  player.removeAttribute("src");
  player.load();
}

// Stops what plays here, recording where it stopped — only when it played
// here: a video opened paused while it plays elsewhere has a position that
// says nothing.
export function leave() {
  if (playsHere() && playedHere && player.currentSrc && !player.ended && player.currentTime > 0) report();
  unload();
  player.removeAttribute("poster");
  const had = state.current !== null;
  state.current = null;
  state.audio = null;
  state.subtitle = null;
  state.sentTo = null;
  if (had) reportDevice();
  notify("player");
}

// The video on screen is sent to the device `to` (behaviour#devices): the
// player stops and lets go of it, watch stays on it under `Playing on
// <device>` with its thumbnail, and now_playing acts on that device, playing
// or paused, until playback is moved back.
export function sendAway(to) {
  if (playsHere() && player.currentSrc && !player.ended) report(currentPosition() + mediaOffset());
  unload();
  if (state.current) player.poster = idUrl("/thumb/", state.current.id);
  state.sentTo = to;
  reportDevice();
  notify("player");
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden" && playsHere() && !player.paused) report();
});

// -------------------------------------------------------------------- local

// Every control of now_playing, done on this device: what the bar does when
// it shows this device, and what a command from another device does here
// (server#functions#control). Each takes the command's fields.
const clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x));

export const local = {
  play() {
    if (!playsHere()) return;
    if (player.ended) player.currentTime = mediaOffset();
    startPlayback();
  },
  pause() {
    if (playsHere()) player.pause();
  },
  seek({ position }) {
    if (!playsHere() || typeof position !== "number" || !isFinite(position)) return;
    const duration = currentDuration();
    const at = clamp(position, 0, duration || position);
    if (player.readyState > 0) player.currentTime = at + mediaOffset();
    else loadingAt = at;
  },
  previous() {
    const v = state.current && previousOf(state.current);
    if (v) navigate(watchHref(v.id));
  },
  next() {
    const v = state.current && nextOf(state.current);
    if (v) navigate(watchHref(v.id));
  },
  volume({ volume, muted }) {
    if (typeof volume === "number" && isFinite(volume)) player.volume = clamp(volume, 0, 1);
    if (typeof muted === "boolean") player.muted = muted;
  },
  speed({ speed }) {
    if (typeof speed !== "number" || !(speed > 0)) return;
    player.defaultPlaybackRate = speed;
    player.playbackRate = speed;
  },
  audio({ audio }) {
    const v = state.current;
    if (!playsHere()) return;
    const track = audio ? v.audio.find((t) => t.id === audio) : defaultAudio(v);
    if (track) chooseAudio(track);
  },
  subtitle({ subtitle }) {
    const v = state.current;
    if (!playsHere()) return;
    if (!subtitle) return chooseSubtitle(null);
    const track = v.subtitles.find((t) => t.id === subtitle && t.available);
    if (track) chooseSubtitle(track);
  },
  // Pressed on this device only: a browser goes full screen only from a
  // touch on its own page (decisions#fullscreen-here).
  fullscreen({ fullscreen }) {
    return fullscreen === false ? exitFullscreen() : enterFullscreen();
  },
  stop() {
    navigate("/");
  },
};
