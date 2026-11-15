// What watch#details says of a file and of how it reaches the browser
// (behaviour#what-plays), from /api/details: rows of a label and a text.

const CODECS = {
  h264: "H.264", hevc: "HEVC", h265: "HEVC", av1: "AV1", vp9: "VP9", vp8: "VP8",
  mpeg4: "MPEG-4", mpeg2video: "MPEG-2", mpeg1video: "MPEG-1", msmpeg4v3: "DivX", wmv3: "WMV",
  theora: "Theora", aac: "AAC", flac: "FLAC", eac3: "E-AC-3", ac3: "AC-3", dts: "DTS",
  truehd: "TrueHD", opus: "Opus", vorbis: "Vorbis", mp3: "MP3", mp2: "MP2", alac: "ALAC", wmav2: "WMA",
};

export function codecName(codec) {
  if (!codec) return "";
  if (codec.startsWith("pcm_")) return "PCM";
  return CODECS[codec] || codec.toUpperCase();
}

// `1.2 GB`
export function formatSize(bytes) {
  if (bytes === null || bytes === undefined || !isFinite(bytes)) return "";
  const units = ["bytes", "KB", "MB", "GB", "TB"];
  let n = bytes, u = 0;
  while (n >= 1000 && u < units.length - 1) { n /= 1000; u++; }
  return (u === 0 ? String(n) : n >= 100 ? n.toFixed(0) : n.toFixed(1)) + " " + units[u];
}

// `23.976 fps`
export function formatRate(rate) {
  if (!rate || !isFinite(rate)) return "";
  return String(Math.round(rate * 1000) / 1000) + " fps";
}

// `stereo`, `5.1`, or `3 channels`.
export function channelsName(channels, layout) {
  if (layout) {
    const name = layout.replace(/\(.*\)$/, "");
    if (name) return name;
  }
  if (channels === 1) return "mono";
  if (channels === 2) return "stereo";
  return channels ? `${channels} channels` : "";
}

// How the video reaches this browser.
export function deliveryText(delivery) {
  if (!delivery) return "";
  switch (delivery.kind) {
    case "direct": return "played as it is";
    case "copy": return "sent in pieces — the video copied, the sound converted";
    case "transcode": return "converted as it plays" + (delivery.encoder ? " — " + delivery.encoder : "");
    case "unplayable": return "can't be played" + (delivery.reason ? ": " + delivery.reason : "");
    default: return delivery.kind;
  }
}

const join = (...parts) => parts.filter((p) => p !== "" && p !== null && p !== undefined).join(" · ");

// [[label, text], ...]: the picture, the sound, the file and the delivery,
// leaving out what is not known.
export function detailsRows(details) {
  const rows = [];
  const m = details && details.media;
  if (m) {
    const size = m.width && m.height ? `${m.width}×${m.height}` : "";
    const codec = [codecName(m.video_codec), m.profile].filter(Boolean).join(" ");
    const video = join(size, formatRate(m.frame_rate), codec, m.bit_depth ? `${m.bit_depth}-bit` : "");
    if (video) rows.push(["Video", video]);
    const sound = join(codecName(m.audio_codec), channelsName(m.channels, m.layout));
    if (sound) rows.push(["Sound", sound]);
    const file = join(m.container, formatSize(m.size));
    if (file) rows.push(["File", file]);
  }
  if (details && details.delivery) rows.push(["Plays", deliveryText(details.delivery)]);
  return rows;
}
