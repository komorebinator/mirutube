"""Everything that runs ffprobe or ffmpeg.

Results are cached in the cache folder, keyed by the file's path, size and
modification time, so each is computed once and again only when the file
changes.
"""

import glob
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time



def platform_cache_dir():
    """MiruTube's folder in the platform's own cache folder: `~/.cache` on
    Linux (or `$XDG_CACHE_HOME`), `%LOCALAPPDATA%` on Windows,
    `~/Library/Caches` on macOS."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "mirutube")


cache_dir = platform_cache_dir()

# ffmpeg is heavy; a home page full of new thumbnails must not start one
# process per card at once.
_ffmpeg_slots = threading.BoundedSemaphore(max(2, os.cpu_count() or 2))

TEXT_SUBTITLE_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text", "microdvd", "subviewer", "jacosub"}
IMAGE_SUBTITLE_NAMES = {
    "hdmv_pgs_subtitle": "PGS",
    "dvd_subtitle": "VobSub",
    "dvb_subtitle": "DVB",
    "xsub": "XSUB",
}


# The fields of a cached probe result change with the code; an entry from an
# older version is probed again.
PROBE_VERSION = 4


def set_cache_dir(path):
    global cache_dir
    cache_dir = os.path.abspath(path)
    os.makedirs(cache_dir, exist_ok=True)


def _cache_path(path, kind, suffix):
    st = os.stat(path)
    key = "\0".join((os.path.abspath(path), str(st.st_size), str(st.st_mtime_ns), kind))
    digest = hashlib.sha1(key.encode("utf-8", "surrogateescape")).hexdigest()
    folder = os.path.join(cache_dir, kind)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, digest + suffix)


def _write_atomic(target, data):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target), prefix=".tmp-")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, target)


def _run(args, timeout=120):
    with _ffmpeg_slots:
        return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)


# What muxers write as an MP4 track's handler name when nobody named the
# track: `SoundHandler`, `Core Media Audio`, `GPAC ISO Audio Handler`.
_GENERIC_HANDLER = re.compile(r"handler|core media|iso media|^\s*$", re.I)


def _title(tags):
    """A stream's title: its `title` tag, else — MP4 names a track by its
    handler — a handler name that is not a muxer's default."""
    title = tags.get("title")
    if title:
        return title
    handler = tags.get("handler_name") or ""
    return None if _GENERIC_HANDLER.search(handler) else handler.strip()


def _rate(text):
    """Frames a second from ffprobe's `24000/1001`, or None."""
    try:
        num, _, den = (text or "").partition("/")
        rate = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return None
    return round(rate, 3) if rate > 0 else None


def _bit_depth(stream):
    """Bits a sample of a video stream: from ffprobe's own count, else from
    its pixel format (`yuv420p10le` is 10)."""
    try:
        bits = int(stream.get("bits_per_raw_sample"))
        if bits > 0:
            return bits
    except (TypeError, ValueError):
        pass
    m = re.search(r"p(\d{1,2})(?:le|be)?$", stream.get("pix_fmt") or "") or \
        re.match(r"p0(\d{2})", stream.get("pix_fmt") or "")
    if m:
        return int(m.group(1))
    return 8 if stream.get("pix_fmt") else None


def probe(path):
    """Duration, codecs, and the audio and subtitle streams with their
    languages and titles, of one file, from ffprobe.

    Returns a dict with `duration` (seconds or None), `start_time`,
    `container` (ffprobe's format name), `video_codec`, `video_stream` (its
    index), `pix_fmt`, `profile`, `level`, `audio_codec` and `audio_stream` —
    the video and the audio being the first of each, cover art left out —
    `audio` (a list of {stream, codec, language, title, channels, layout},
    the first being the default sound track) and `subtitles` (a list of
    {stream, codec, language, title, forced, image}); for watch#details also
    `width`, `height`, `frame_rate` and `bit_depth` of the video; and `title`,
    the title stored in the file, or None. On failure `error` is set.
    """
    try:
        cached = _cache_path(path, "probe", ".json")
    except OSError as e:
        return {"error": str(e), "duration": None, "audio": [], "subtitles": []}
    if os.path.exists(cached):
        try:
            with open(cached, encoding="utf-8") as f:
                info = json.load(f)
            if info.get("version") == PROBE_VERSION:
                return info
        except (OSError, ValueError):
            pass
    try:
        res = _run(["ffprobe", "-v", "error", "-print_format", "json",
                    "-show_format", "-show_streams", path], timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"error": str(e), "duration": None, "audio": [], "subtitles": []}
    if res.returncode != 0:
        # Not cached: a file still being copied in is probed again once it changes,
        # but a transient failure must not stick either.
        return {"error": res.stderr.decode("utf-8", "replace").strip() or "ffprobe failed",
                "duration": None, "audio": [], "subtitles": []}
    data = json.loads(res.stdout.decode("utf-8", "replace") or "{}")
    fmt = data.get("format", {})
    format_tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    info = {"version": PROBE_VERSION, "duration": None, "container": fmt.get("format_name"),
            "title": (format_tags.get("title") or "").strip() or None,
            "video_codec": None, "video_stream": None, "pix_fmt": None,
            "profile": None, "level": None, "width": None, "height": None,
            "frame_rate": None, "bit_depth": None,
            "audio_codec": None, "audio_stream": None, "audio": [], "subtitles": []}
    try:
        info["duration"] = float(fmt["duration"])
    except (KeyError, TypeError, ValueError):
        pass
    try:
        info["start_time"] = float(fmt["start_time"])
    except (KeyError, TypeError, ValueError):
        info["start_time"] = 0.0
    for s in data.get("streams", []):
        kind = s.get("codec_type")
        codec = s.get("codec_name")
        tags = {k.lower(): v for k, v in (s.get("tags") or {}).items()}
        disposition = s.get("disposition") or {}
        if kind == "video" and info["video_codec"] is None and not disposition.get("attached_pic"):
            info.update(video_codec=codec, video_stream=s.get("index"), pix_fmt=s.get("pix_fmt"),
                        profile=s.get("profile"), level=s.get("level"),
                        width=s.get("width"), height=s.get("height"),
                        frame_rate=_rate(s.get("avg_frame_rate")) or _rate(s.get("r_frame_rate")),
                        bit_depth=_bit_depth(s))
            if info["duration"] is None:
                try:
                    info["duration"] = float(s["duration"])
                except (KeyError, TypeError, ValueError):
                    pass
        elif kind == "audio":
            if info["audio_codec"] is None:
                info.update(audio_codec=codec, audio_stream=s.get("index"))
            info["audio"].append({"stream": s.get("index"), "codec": codec,
                                  "language": tags.get("language"), "title": _title(tags),
                                  "channels": s.get("channels"), "layout": s.get("channel_layout")})
        elif kind == "subtitle":
            info["subtitles"].append({
                "stream": s.get("index"),
                "codec": codec,
                "language": tags.get("language"),
                "title": _title(tags),
                "forced": bool(disposition.get("forced")),
                "image": codec in IMAGE_SUBTITLE_NAMES or codec not in TEXT_SUBTITLE_CODECS,
            })
    _write_atomic(cached, json.dumps(info).encode("utf-8"))
    return info


def thumbnail(path):
    """A frame from a tenth of the way into the video, as JPEG — the first frame
    of most videos is black or a title card. Returns the path of the cached JPEG,
    or None when no frame could be taken."""
    cached = _cache_path(path, "thumb", ".jpg")
    if os.path.exists(cached):
        return cached
    duration = probe(path).get("duration") or 0
    for at in ([duration / 10, 0] if duration else [0]):
        try:
            res = _run(["ffmpeg", "-v", "error", "-nostdin", "-ss", "%.3f" % at, "-i", path,
                        "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4",
                        "-f", "image2", "-c:v", "mjpeg", "pipe:1"], timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if res.returncode == 0 and res.stdout:
            _write_atomic(cached, res.stdout)
            return cached
    return None


def decode_text(raw):
    """An external subtitle file's text: UTF-8, else cp1251, else latin-1."""
    for encoding in ("utf-8-sig", "cp1251", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace")


def subtitles_vtt(path, stream=None):
    """One subtitle track converted to WebVTT, the only format a browser's
    player reads. With `stream`, the embedded subtitle stream of that index in
    the video at `path`; without, the external subtitle file at `path`, decoded
    as UTF-8, else as cp1251, else as latin-1 (behaviour#readable-subtitles).
    Returns the WebVTT text; raises RuntimeError when it cannot be converted."""
    cached = _cache_path(path, "vtt", ".%s.vtt" % ("x" if stream is None else stream))
    if os.path.exists(cached):
        with open(cached, encoding="utf-8") as f:
            return f.read()
    if stream is not None:
        args = ["ffmpeg", "-v", "error", "-nostdin", "-i", path,
                "-map", "0:%d" % int(stream), "-c:s", "webvtt", "-f", "webvtt", "pipe:1"]
        res = _run(args)
    else:
        with open(path, "rb") as f:
            text = decode_text(f.read())
        ext = os.path.splitext(path)[1].lower()
        if ext == ".vtt":
            vtt = text if text.lstrip().startswith("WEBVTT") else "WEBVTT\n\n" + text
            _write_atomic(cached, vtt.encode("utf-8"))
            return vtt
        # ffmpeg reads the text as UTF-8, so the file is handed over re-encoded.
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(cached), suffix=ext or ".srt")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(text.encode("utf-8"))
            res = _run(["ffmpeg", "-v", "error", "-nostdin", "-i", tmp,
                        "-c:s", "webvtt", "-f", "webvtt", "pipe:1"])
        finally:
            os.unlink(tmp)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.decode("utf-8", "replace").strip() or "ffmpeg failed")
    vtt = res.stdout.decode("utf-8", "replace")
    _write_atomic(cached, vtt.encode("utf-8"))
    return vtt


# ------------------------------------------------------------------ stream
#
# A `stream` video reaches the browser in pieces of fragmented MP4 with H.264
# video and AAC sound, which the page appends to a Media Source Extensions
# SourceBuffer (decisions#stream). So does any video played with a sound track
# other than its default (decisions#audio-in-pieces): the sound of a piece
# comes from the chosen track — the file's first audio stream, another of its
# own, or an external sound file read alongside it from the same moment. Each piece is a whole fragmented MP4 — its
# own initialization segment (ftyp, moov) and then media segments (moof,
# mdat) — and keeps the timestamps of the source, so the pieces follow each
# other in time and can be appended in any order, which is how a seek is
# served.
#
# Times here are on the source's own timeline, its timestamps less its start
# time (ffmpeg's -copyts -start_at_zero). The media timeline the page sees is
# that plus the plan's `offset`: a copied video whose frames are reordered
# (B-frames) has decode timestamps before its first presentation timestamp,
# and a fragmented MP4 cannot hold a negative one, so everything moves later
# by that much.

PIECE = 10.0                  # seconds a piece lasts, about
SAMPLE_RATE = 48000           # of the AAC sound
AAC_FRAME = 1024              # samples in one AAC frame
# AAC frames encoded before a piece's first one and dropped: the encoder's
# first frame is priming, and the next ones need the sound before them. The
# frames kept lie on one grid of AAC frames from the start of the video, so
# the sound of consecutive pieces joins without a gap or an overlap.
AUDIO_PREROLL = 4
SEEK_MARGIN = 2.0             # seconds read before a piece, so ffmpeg's seek lands before it
READ_PAST = 1.5               # seconds read after a piece, for the sound the encoder still needs
STREAM_TIMEOUT = 300          # seconds one piece may take at most
SOFTWARE_ENCODER = "libx264"
# What transcoding makes: H.264 High profile at level 4.2, enough for 1080p at
# 60 frames a second; larger videos are scaled down to fit 1920x1080.
TRANSCODE_CODEC = "avc1.64002a"
H264_PROFILES = {"Constrained Baseline": (66, 0x40), "Baseline": (66, 0x00),
                 "Main": (77, 0x00), "Extended": (88, 0x00), "High": (100, 0x00)}
DIRECT_PIX_FMTS = {"yuv420p", "yuvj420p"}
# no mfra trailer: a SourceBuffer takes only initialization and media segments
MP4_FLAGS = "frag_keyframe+empty_moov+default_base_moof+frag_discont+skip_trailer"

PLATFORM = sys.platform      # which hardware encoders there can be: `linux`, `win32`, `darwin`
_encoder = None               # the H.264 encoder transcoding uses, found by transcoder()
_encoder_lock = threading.Lock()


def _render_devices():
    """The render devices of this computer's GPUs, in order — on Linux, the
    one system that has them; elsewhere none."""
    if not PLATFORM.startswith("linux"):
        return []
    return sorted(glob.glob("/dev/dri/renderD*"), key=lambda d: (len(d), d))


def _render_device():
    found = _render_devices()
    return found[0] if found else None


def hardware_encoders(platform=None):
    """The hardware encoders to try, in order: NVENC, then VAAPI on each render
    device in turn (Linux), then QSV, AMF (AMD, on Windows) and VideoToolbox
    (macOS). NVENC comes first because a computer with an NVIDIA card often
    has another GPU too, and VAAPI on the first render device may be that
    other one. An encoder is named as transcoder() returns it — VAAPI with its
    device after a colon. `platform` is a value of sys.platform, by default
    this computer's (PLATFORM)."""
    platform = platform or PLATFORM
    found = ["h264_nvenc"]
    if platform.startswith("linux"):
        found += ["h264_vaapi:" + d for d in _render_devices()]
    found.append("h264_qsv")
    if platform == "win32":
        found.append("h264_amf")
    if platform == "darwin":
        found.append("h264_videotoolbox")
    return found


def describe_encoder(encoder):
    """An encoder as the server's log names it."""
    name, _, device = encoder.partition(":")
    return name + (" on " + device if device else "")


def _encoder_args(encoder):
    """(options before the input, video filters after scaling, output options)
    for one H.264 encoder — `h264_vaapi:<render device>`, `h264_nvenc`,
    `h264_qsv`, `h264_amf`, `h264_videotoolbox` or libx264. Every encoder
    makes High profile without B-frames, so a piece's decode timestamps are
    its presentation timestamps, and all but VideoToolbox — which sets the
    level itself — at level 4.2."""
    name, _, device = encoder.partition(":")
    common = ["-profile:v", "high", "-level:v", "4.2", "-bf", "0"]
    if name == "h264_vaapi":
        return (["-vaapi_device", device or _render_device() or ""], ["format=nv12", "hwupload"],
                ["-c:v", name, *common, "-rc_mode", "CQP", "-qp", "23"])
    if name == "h264_nvenc":
        return [], ["format=yuv420p"], ["-c:v", name, *common, "-preset", "p4", "-cq", "23"]
    if name == "h264_qsv":
        return [], ["format=nv12"], ["-c:v", name, *common, "-global_quality", "23"]
    if name == "h264_amf":
        return [], ["format=nv12"], ["-c:v", name, *common, "-quality", "balanced",
                                     "-rc", "cqp", "-qp_i", "23", "-qp_p", "23"]
    if name == "h264_videotoolbox":
        # VideoToolbox has no constant quality on every Mac: a bit rate
        # enough for 1080p instead
        return [], ["format=nv12"], ["-c:v", name, "-profile:v", "high", "-bf", "0",
                                     "-b:v", "6M", "-maxrate", "9M", "-bufsize", "12M"]
    return [], ["format=yuv420p"], ["-c:v", SOFTWARE_ENCODER, *common, "-preset", "veryfast", "-crf", "21"]


def transcoder():
    """The H.264 encoder transcoding uses: the first of hardware_encoders()
    ffmpeg can open on this computer, else libx264. Found once, by encoding a
    fraction of a second with each; the server asks for it at start and writes
    it to its log."""
    global _encoder
    with _encoder_lock:
        if _encoder is None:
            _encoder = SOFTWARE_ENCODER
            for encoder in hardware_encoders():
                pre, filters, out = _encoder_args(encoder)
                try:
                    res = subprocess.run(
                        ["ffmpeg", "-v", "error", "-nostdin", *pre, "-f", "lavfi",
                         "-i", "testsrc2=size=320x180:rate=25:duration=0.4",
                         "-vf", ",".join(filters), *out, "-f", "null", "-"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
                except (OSError, subprocess.TimeoutExpired):
                    continue
                if res.returncode == 0:
                    _encoder = encoder
                    break
        return _encoder


def copies_video(info):
    """Whether a `stream` video keeps its own video: H.264 at 8 bits and 4:2:0
    in a profile browsers decode, with only the container or the sound in the
    way."""
    return (info.get("video_codec") == "h264" and info.get("pix_fmt") in DIRECT_PIX_FMTS
            and info.get("profile") in H264_PROFILES)


def _video_packets(path, info):
    """The video packets of a file in decode order as (pts, dts, key), in
    seconds on the source timeline (dts None where ffprobe has none), or None
    when a packet has no presentation timestamp."""
    try:
        res = _run(["ffprobe", "-v", "error", "-select_streams", str(info["video_stream"]),
                    "-show_entries", "packet=pts_time,dts_time,flags", "-of", "csv=p=0", path],
                   timeout=STREAM_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    shift = info.get("start_time") or 0.0
    packets = []
    for line in res.stdout.decode("ascii", "replace").splitlines():
        fields = line.strip().split(",")
        if len(fields) < 3:
            continue
        try:
            pts = float(fields[0]) - shift
        except ValueError:
            return None
        try:
            dts = float(fields[1]) - shift
        except ValueError:
            dts = None
        packets.append((pts, dts, "K" in fields[2]))
    return packets or None


def _copy_plan(path, info):
    """Pieces starting at the source's keyframes, the only places a copied
    stream can be cut: each at the first keyframe at least PIECE seconds after
    the one before. None when the packets do not allow it."""
    packets = _video_packets(path, info)
    if not packets:
        return None
    keys = [i for i, p in enumerate(packets) if p[2]]
    if not keys:
        return None
    cuts = [0.0]
    for i in keys[1:]:
        if packets[i][0] - cuts[-1] >= PIECE:
            cuts.append(packets[i][0])
    offset = math.ceil(max(0.0, -_first_dts(packets[keys[0]:])) * 1000 - 1e-6) / 1000
    codec, compat = H264_PROFILES[info["profile"]]
    level = info.get("level") if isinstance(info.get("level"), int) and 0 < info["level"] < 100 else 41
    return {"copy": True, "cuts": cuts, "offset": offset,
            "video_codec": "avc1.%02x%02x%02x" % (codec, compat, level)}


def _first_dts(packets):
    """The decode timestamp of the first packet, or a little before it. A
    container that stores none (Matroska) leaves ffprobe without one for the
    first packets, and ffmpeg guesses them when it reads the file again; each
    is taken as a frame before the next, less one frame more for the guess."""
    for i, p in enumerate(packets):
        if p[1] is not None:
            if i == 0:
                return p[1]
            pts = sorted(q[0] for q in packets[:i + 8])
            frame = min((b - a for a, b in zip(pts, pts[1:]) if b > a), default=0.0)
            return p[1] - (i + 1) * frame
    return 0.0


def stream_plan(path, audio=None):
    """How a `stream` video is cut into pieces: its duration, the start of each
    piece, and the MIME type the page gives Media Source Extensions. Pieces are
    about ten seconds long. Where the video is copied they start at the
    source's keyframes; where it is transcoded they start every ten seconds,
    with a keyframe forced at each start — the first frame an encoder makes.
    The pieces are the same whichever sound track is chosen (`audio`, as for
    stream_segment); only whether the MIME type names AAC depends on it.

    Returns {duration, offset, starts, mime} for the page — `starts` and
    `duration` on the media timeline, which is the source's moved later by
    `offset` seconds — together with what stream_segment needs: `copy` and
    `cuts`, the starts on the source timeline. Raises ValueError for a file
    that cannot be streamed."""
    info = probe(path)
    if info.get("error") or info.get("video_stream") is None or not info.get("duration"):
        raise ValueError(info.get("error") or "no video stream or no duration")
    cached = _cache_path(path, "plan", ".json")
    if os.path.exists(cached):
        try:
            with open(cached, encoding="utf-8") as f:
                plan = json.load(f)
            if plan.get("version") == PROBE_VERSION:
                return _with_mime(plan, info, audio)
        except (OSError, ValueError):
            pass
    duration = info["duration"]
    plan = _copy_plan(path, info) if copies_video(info) else None
    if plan is None:
        cuts = [k * PIECE for k in range(max(1, math.ceil(duration / PIECE)))]
        plan = {"copy": False, "cuts": cuts, "offset": 0.0, "video_codec": TRANSCODE_CODEC}
    offset = plan["offset"]
    plan.update(version=PROBE_VERSION, duration=round(duration + offset, 6),
                starts=[0.0] + [round(c + offset, 6) for c in plan["cuts"][1:]])
    _write_atomic(cached, json.dumps(plan).encode("utf-8"))
    return _with_mime(plan, info, audio)


def _with_mime(plan, info, audio):
    """The plan with the MIME type of its pieces: H.264, and AAC when there is
    sound — always with a track chosen, by default when the file has some."""
    sound = audio is not None or info.get("audio_stream") is not None
    return dict(plan, mime='video/mp4; codecs="%s%s"' % (plan["video_codec"], ",mp4a.40.2" if sound else ""))


def _sound_source(info, audio):
    """(input options, input file, stream index, external) of a piece's
    sound, or None for a piece without: by default the file's first audio
    stream; with `audio` an int, that stream of the file; with `audio` a path,
    the first audio stream of that external sound file. Raises RuntimeError
    when the stream is not an audio stream of the file it names."""
    if audio is None:
        stream = info.get("audio_stream")
        return None if stream is None else (None, stream, False)
    if isinstance(audio, int):
        if audio not in {a["stream"] for a in info.get("audio") or []}:
            raise RuntimeError("the video has no audio stream %d" % audio)
        return None, audio, False
    ext = probe(audio)
    if ext.get("error") or ext.get("audio_stream") is None:
        raise RuntimeError("no sound can be read from %s" % os.path.basename(audio))
    return audio, ext["audio_stream"], True

def _audio_frame_at(t):
    """The first sample of the first AAC frame of the grid at or after `t`."""
    return math.ceil(round(t * SAMPLE_RATE, 3) / AAC_FRAME) * AAC_FRAME


def _drop(lo, hi):
    """A noise bitstream filter dropping packets presented before `lo` or from
    `hi` on, in seconds; None for neither."""
    terms = []
    if lo is not None:
        terms.append(r"lt(pts*tb\,%.6f)" % lo)
    if hi is not None:
        terms.append(r"gte(pts*tb\,%.6f)" % hi)
    return "noise=drop=" + "+".join(terms) if terms else None


def _bsf(*filters):
    return ",".join(f for f in filters if f)


def _segment_args(path, plan, index, encoder, audio=None):
    info = probe(path)
    sound = _sound_source(info, audio)
    cuts = plan["cuts"]
    start = cuts[index]
    end = cuts[index + 1] if index + 1 < len(cuts) else None
    pre, filters, out = ([], [], []) if plan["copy"] else _encoder_args(encoder)
    # the offset of the media timeline, added to every timestamp
    shift = ("setts=pts=PTS+floor({0:.6f}/TB+0.5):dts=DTS+floor({0:.6f}/TB+0.5)".format(plan["offset"])
             if plan["offset"] else None)
    args = ["ffmpeg", "-v", "error", "-nostdin", *pre, "-copyts", "-start_at_zero", "-noaccurate_seek"]
    seek = start - SEEK_MARGIN
    if seek > 0:
        args += ["-ss", "%.6f" % seek]
    if plan["copy"] and end is not None:
        # Copied packets past the piece are dropped, but ffmpeg would read on
        # to the end of the file for them: reading stops a little after the
        # piece (with -copyts, a time on the source timeline). A transcoded
        # piece ends with its filters instead — this option would also cut
        # the decoded streams there, at a time counted differently.
        args += ["-to", "%.6f" % (end + READ_PAST)]
    args += ["-i", path]
    if sound and sound[2]:
        # The external file is read from the same moment as the video. The
        # two are aligned by their own timestamps, the way a player that
        # loads an external track plays them: its time is put on the video's
        # timeline — the video's timestamps less the video's start time —
        # rather than started at zero by -start_at_zero.
        ext = probe(sound[0])
        lag = (ext.get("start_time") or 0.0) - (info.get("start_time") or 0.0)
        args += ["-noaccurate_seek", "-itsoffset", "%.6f" % lag]
        if seek - lag > 0:
            args += ["-ss", "%.6f" % (seek - lag)]
        args += ["-i", sound[0]]
    args += ["-map", "0:%d" % info["video_stream"]]
    if plan["copy"]:
        # what is read before and after the piece is dropped by its timestamps
        bsf = _bsf(_drop(start - 0.0005 if index else None, end - 0.0005 if end is not None else None),
                   shift)
        args += ["-c:v", "copy"]
        if bsf:
            args += ["-bsf:v", bsf]
    else:
        trim = "trim=start=%.6f" % start + (":end=%.6f" % end if end is not None else "")
        scale = ("scale=w='min(iw,1920)':h='min(ih,1080)'"
                 ":force_original_aspect_ratio=decrease:force_divisible_by=2")
        args += ["-vf", ",".join([trim, scale, *filters]), "-fps_mode", "passthrough",
                 "-enc_time_base:v", "demux", *out]
    if sound:
        first = _audio_frame_at(start) if index else 0
        if end is not None:
            last = _audio_frame_at(end)
        elif sound[2]:
            # an external file may run on past the video: its sound ends with it
            last = _audio_frame_at(info["duration"])
        else:
            last = None
        fed = max(0, first - AUDIO_PREROLL * AAC_FRAME)
        # silence fills in where the sound ends before the piece does, so the
        # sound of the pieces has no hole the browser would stop playing at
        pad = ["apad"] if last else []
        if fed:
            chain = ["aresample=%d" % SAMPLE_RATE, *pad,
                     "atrim=start_pts=%d" % fed + (":end_pts=%d" % (last + 2 * AAC_FRAME) if last else "")]
        else:
            # from the very start: silence fills in where the sound begins later
            chain = ["aresample=%d:first_pts=0" % SAMPLE_RATE, *pad]
            if last:
                chain.append("atrim=end_pts=%d" % (last + 2 * AAC_FRAME))
        chain.append("asetpts=N+%d" % fed)  # time base 1/SAMPLE_RATE: on the grid exactly
        half = AAC_FRAME / 2
        bsf = _bsf(_drop((first - half) / SAMPLE_RATE, (last - half) / SAMPLE_RATE if last else None),
                   shift)
        args += ["-map", "%d:%d" % (1 if sound[2] else 0, sound[1]), "-af", ",".join(chain),
                 "-c:a", "aac", "-b:a", "160k", "-ac", "2", "-bsf:a", bsf]
    args += ["-map_metadata", "-1", "-map_chapters", "-1", "-max_muxing_queue_size", "4096",
             "-avoid_negative_ts", "disabled", "-use_editlist", "0",
             "-f", "mp4", "-movflags", MP4_FLAGS, "pipe:1"]
    return args


def _run_cancellable(args, cancelled):
    """Runs ffmpeg and returns what it wrote, or None once `cancelled()` is
    true, having stopped it."""
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    deadline = time.monotonic() + STREAM_TIMEOUT
    try:
        while True:
            if cancelled is not None and cancelled():
                return None
            if time.monotonic() > deadline:
                raise RuntimeError("ffmpeg took longer than %d seconds" % STREAM_TIMEOUT)
            try:
                out, err = proc.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    if proc.returncode != 0 or not out:
        raise RuntimeError(err.decode("utf-8", "replace").strip() or "ffmpeg made nothing")
    return out


def stream_segment(path, index, cancelled=None, audio=None):
    """One piece of a `stream` video as fragmented MP4 that continues the one
    before it in time, with H.264 video and AAC sound from the chosen track:
    `audio` None for the file's first audio stream, an int for another of its
    own streams, or the path of an external sound file read alongside it from
    the same moment (library#match_audio). The video is the source's own when
    a browser decodes it and only the container or the sound is the problem
    (copies_video); otherwise it is transcoded by transcoder() — NVENC, then
    VAAPI on each render device in turn (Linux), then QSV, AMF (Windows) and
    VideoToolbox (macOS), else libx264 — and by libx264 when a hardware
    encoder fails on this video. A piece is made when it is
    asked for and not kept.

    `cancelled` is asked a few times a second while ffmpeg runs; once it says
    true, ffmpeg is stopped and None returned. Raises IndexError for a piece
    the plan does not have, RuntimeError when ffmpeg fails."""
    plan = stream_plan(path, audio)
    if not 0 <= index < len(plan["cuts"]):
        raise IndexError("no piece %d" % index)
    encoder = None if plan["copy"] else transcoder()
    try:
        return _run_cancellable(_segment_args(path, plan, index, encoder, audio), cancelled)
    except RuntimeError:
        if encoder in (None, SOFTWARE_ENCODER):
            raise
        return _run_cancellable(_segment_args(path, plan, index, SOFTWARE_ENCODER, audio), cancelled)


def details(path, audio=None):
    """What a file is, for watch#details: its container and size, and from
    probe the resolution, frame rate, video codec with profile and bit depth,
    and the codec and channels of the sound — of `audio` when given (an int
    for another stream of the file's own, a path for an external file, as for
    stream_segment), else of the default track. None for a file that cannot
    be read."""
    info = probe(path)
    if info.get("error"):
        return None
    try:
        size = os.path.getsize(path)
    except OSError:
        size = None
    sound = None
    if audio is None:
        sound = next(iter(info.get("audio") or []), None)
    elif isinstance(audio, int):
        sound = next((a for a in info.get("audio") or [] if a["stream"] == audio), None)
    else:
        sound = next(iter(probe(audio).get("audio") or []), None)
    return {
        "container": os.path.splitext(path)[1].lstrip(".").upper() or None,
        "format": info.get("container"),
        "size": size,
        "duration": info.get("duration"),
        "width": info.get("width"), "height": info.get("height"),
        "frame_rate": info.get("frame_rate"),
        "video_codec": info.get("video_codec"), "profile": info.get("profile"),
        "bit_depth": info.get("bit_depth"), "pix_fmt": info.get("pix_fmt"),
        "audio_codec": sound and sound.get("codec"),
        "channels": sound and sound.get("channels"),
        "layout": sound and sound.get("layout"),
    }
