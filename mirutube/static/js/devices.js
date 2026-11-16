// Every open page is a device the others see, and any device controls what
// plays on any other (behaviour#devices). The server sends the devices and
// this device's commands over one event stream (decisions#events); this page
// sends what it plays and its commands with ordinary requests.
//
// `target()` is the device now_playing shows and acts on — the device that
// plays — and `act()` does a control there: here through `local`, elsewhere
// through server#functions#control, which that device's own `obey()` turns
// into the same `local` call (decisions#one-bar).

import { postJSON } from "./api.js";
import {
  state, notify, navigate, watchHref, videoOf, lastWatched, startOf, applyContinue,
} from "./state.js";
import {
  player, playsHere, local, currentPosition, currentDuration, reportDevice, initialAudio, initialTrack, sendAway,
} from "./player.js";

let events = null;

export function openEvents() {
  if (events) events.close();
  events = new EventSource("/api/events?id=" + encodeURIComponent(state.device.id)
    + "&name=" + encodeURIComponent(state.device.name));
  // after a reload, or the server's restart, it learns again what plays here
  events.addEventListener("open", () => reportDevice());
  events.addEventListener("devices", (e) => {
    const data = JSON.parse(e.data);
    state.devices = data.devices;
    state.devicesClock = data.now - Date.now() / 1000;
    settlePending();
    notify("devices");
  });
  events.addEventListener("command", (e) => obey(JSON.parse(e.data)));
  // the household's settings, applied as they come (store#fields#shared_settings)
  events.addEventListener("settings", (e) => {
    state.shared = JSON.parse(e.data);
    notify("settings");
  });
  // what is coming into the library, on every device (page#fields#jobs)
  events.addEventListener("jobs", (e) => {
    state.jobs = JSON.parse(e.data);
    notify("jobs");
  });
  // continue_row as it is now, whenever it changes on any device
  // (behaviour#resume#scenarios#live-row)
  events.addEventListener("continue", (e) => applyContinue(JSON.parse(e.data)));
}

export function control(id, command) {
  return postJSON("/api/control", Object.assign({ device: id }, command));
}

// Where a device's video is now, in seconds: its last report, moved on by the
// time since while it plays.
export function positionOf(d) {
  let t = d.position || 0;
  if (d.playing) t += Math.max(0, Date.now() / 1000 + state.devicesClock - d.reported) * (d.speed || 1);
  const v = videoOf(d.video);
  return v && v.duration ? Math.min(t, v.duration) : t;
}

// Another device playing the video on screen here: the player's elsewhere
// state, `Playing on <device>`.
export function elsewhereDevice() {
  const v = state.current;
  if (!v) return null;
  return state.devices.find((d) => d.id !== state.device.id && d.playing && d.video === v.id) || null;
}

// The other devices with a video, the ones playing first, the latest report
// first.
function others() {
  const rank = (d) => (d.playing ? 1 : 0);
  return state.devices.filter((d) => d.video && d.id !== state.device.id)
    .sort((a, b) => rank(b) - rank(a) || b.reported - a.reported);
}

// ------------------------------------------------------------------ target

// What a command sent to another device is expected to change there, shown
// at once until that device's report says so, or for a little while — each
// change on its own, so a later command does not keep an earlier one alive.
const PENDING_FOR = 2000;  // ms
let pending = null;        // {id, fields: {name: {value, at, until}}}

function expect(id, action, args) {
  const fields = {
    play: { playing: true }, pause: { playing: false }, seek: { position: args.position },
    volume: args, speed: { speed: args.speed }, audio: { audio: args.audio }, subtitle: { subtitle: args.subtitle },
  }[action];
  if (!fields) return;
  if (!pending || pending.id !== id) pending = { id, fields: {} };
  const at = Date.now();
  for (const [k, value] of Object.entries(fields)) pending.fields[k] = { value, at, until: at + PENDING_FOR };
}

// Expected changes a report shows, or that waited long enough, end.
function settlePending() {
  if (!pending) return;
  const d = state.devices.find((x) => x.id === pending.id);
  const now = Date.now();
  for (const [k, f] of Object.entries(pending.fields)) {
    const shown = d && (k === "position"
      ? Math.abs(positionOf(d) - f.value - (d.playing ? (now - f.at) / 1000 : 0)) < 2
      : d[k] === f.value);
    if (!d || shown || now >= f.until) delete pending.fields[k];
  }
  if (!Object.keys(pending.fields).length) pending = null;
}

function remoteTarget(d) {
  const t = {
    kind: "remote", id: d.id, name: d.name, video: videoOf(d.video), videoId: d.video,
    position: positionOf(d), playing: !!d.playing, audio: d.audio || null, subtitle: d.subtitle || null,
    volume: typeof d.volume === "number" ? d.volume : 1, muted: !!d.muted,
    speed: typeof d.speed === "number" ? d.speed : 1,
  };
  if (pending && pending.id === d.id) {
    const now = Date.now();
    const live = (k) => pending.fields[k] && now < pending.fields[k].until;
    for (const k of ["playing", "audio", "subtitle", "volume", "muted", "speed"]) if (live(k)) t[k] = pending.fields[k].value;
    if (live("position")) {
      const f = pending.fields.position;
      t.position = f.value + (t.playing ? (now - f.at) / 1000 * t.speed : 0);
    }
  }
  t.duration = t.video && t.video.duration ? t.video.duration : 0;
  if (t.duration) t.position = Math.min(t.position, t.duration);
  return t;
}

function localTarget() {
  return {
    kind: "local", id: state.device.id, name: state.device.name, video: state.current, videoId: state.current.id,
    position: currentPosition(), duration: currentDuration(), playing: !player.paused && !player.ended,
    audio: state.audio ? state.audio.id : null, subtitle: state.subtitle ? state.subtitle.id : null,
    volume: player.volume, muted: player.muted, speed: player.playbackRate,
  };
}

// The device now_playing shows and acts on: the device this one sent its
// video to, playing or paused; this one while its video is on screen —
// unless it stands paused while another device plays it — else another
// device with a video, else the video last watched, which play starts here
// (`idle`), else nothing.
export function target() {
  const sent = state.sentTo && state.devices.find((d) => d.id === state.sentTo);
  if (sent) return remoteTarget(sent);
  if (playsHere()) {
    const away = elsewhereDevice();
    return away && player.paused ? remoteTarget(away) : localTarget();
  }
  const d = others()[0];
  if (d) return remoteTarget(d);
  const v = lastWatched();
  if (v) {
    return {
      kind: "idle", id: null, name: null, video: v, videoId: v.id, position: startOf(v), duration: v.duration || 0,
      playing: false, audio: null, subtitle: null,
      volume: player.volume, muted: player.muted, speed: player.playbackRate,
    };
  }
  return { kind: "none", id: null, video: null, videoId: null, position: 0, duration: 0, playing: false };
}

// One control of now_playing, done on the device that plays. With nothing
// playing anywhere, play starts the video last watched here, and full screen
// is this page's.
export function act(action, args = {}) {
  const t = target();
  // full screen is always this page's own (decisions#fullscreen-here)
  if (action === "fullscreen") return local.fullscreen(args);
  if (t.kind === "local") return local[action](args);
  if (t.kind === "remote") {
    expect(t.id, action, args);
    notify("devices");
    return control(t.id, Object.assign({ action }, args));
  }
  if (t.kind === "idle" && action === "play") navigate(watchHref(t.video.id));
}

// ------------------------------------------------------------------- moving

// Playback that moves here: the video goes on from `position` with these
// tracks, volume and speed.
export function playHere(video, position, audio, subtitle, volume, speed) {
  state.arriving = { video, position, audio, subtitle, volume, speed };
  navigate(watchHref(video));
}

// Moves playback to the device `to` (behaviour#devices): the video goes on
// there from the same moment with the same sound, subtitles, volume and
// speed, and the device it leaves stops — another device goes back to home,
// this one stays on the video with its player stopped, the bar acting on
// `to`. The video last watched, with nothing playing, starts there with the
// tracks it would start with here.
export function moveTo(to) {
  const t = target();
  if (t.kind === "none" || t.id === to) return;
  const from = {
    id: t.id, video: t.videoId, position: t.position, audio: t.audio, subtitle: t.subtitle,
    volume: typeof t.volume === "number" ? t.volume : player.volume,
    speed: typeof t.speed === "number" ? t.speed : player.playbackRate,
  };
  if (t.kind === "idle") {
    const a = initialAudio(t.video), s = initialTrack(t.video);
    from.audio = a && !a.default ? a.id : null;
    from.subtitle = s ? s.id : null;
  }
  pending = null; // what was expected of either device no longer holds
  if (to === state.device.id) {
    if (from.id) control(from.id, { action: "stop" });
    return playHere(from.video, from.position, from.audio, from.subtitle, from.volume, from.speed);
  }
  if (from.id === state.device.id) sendAway(to);
  else if (from.id) control(from.id, { action: "stop" });
  else state.sentTo = to; // the video last watched, sent from here: the bar follows it
  control(to, {
    action: "load", video: from.video, position: from.position, audio: from.audio, subtitle: from.subtitle,
    volume: from.volume, speed: from.speed,
  });
  notify("devices");
}

// A command from another device, for this one.
export function obey(c) {
  if (c.action === "load") return playHere(c.video, c.position || 0, c.audio, c.subtitle, c.volume, c.speed);
  const fn = local[c.action];
  if (fn) fn(c);
}
