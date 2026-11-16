"""The HTTP server: the page, the catalogue as JSON, videos, thumbnails and
subtitles, positions and subtitle and sound-track choices recorded in store,
the devices — every open page — with what each plays (behaviour#devices), the
settings, and the address other devices reach it at with its QR code.

Started with `python -m mirutube <library folder>`. It answers HTTPS and plain
HTTP on the same port: a connection that opens with a TLS handshake gets HTTPS,
with the certificate MiruTube's own authority signs for this computer
(certificates), or one given on the command line; any other gets plain HTTP
(decisions#own-ca). A request names a video or a playlist by its short id from
library#scan (decisions#short-ids); an id not in the catalogue is refused, so
no request can name a file outside the library folders
(behaviour#library-only) — the server listens on the whole home network, and
any device there can send it anything.

Routes:
  GET  /, /playlist/<id>, /watch/<id>, /settings
                                      the page, at the address of each view
  GET  /mirutube.crt                  the certificate of MiruTube's authority, to install
  GET  /api/ping                      {ok}: how a page over plain HTTP finds out
                                      whether its device trusts HTTPS
  GET  /static/<file>                 the page's scripts, styles, icons and manifest
  GET  /sw.js                         the page's service worker, which caches nothing
  GET  /api/catalogue                 the catalogue with positions, subtitle and sound-track choices
  GET  /api/position?video=<id>      {video, position, watched}
  POST /api/position                  {video, position} -> {video, position, watched}
  POST /api/subtitle-choice           {playlist, label|null, language|null} -> same
  POST /api/audio-choice              {playlist, label|null, language|null} -> same; null is the default
  GET  /api/details?video=<id>[&audio=<track id>]
                                      what the file is and how it reaches the browser:
                                      {video, media: {...}|null, delivery: {...}}
  GET  /video/<video>                 the file, answering Range requests
  GET  /stream/<video>                a `stream` video's plan: {duration, offset, starts, mime}
  GET  /stream/<video>?piece=<n>      its piece n, as fragmented MP4
       ...&audio=<track id>           either, with that sound track — for any playable
                                      video when the track is not its default
  GET  /thumb/<video>                 a JPEG thumbnail
  GET  /subtitle/<video>?track=<id>   one subtitle track as WebVTT
  GET  /api/events?id=<device>&name=<name>
                                      the page's event stream (Server-Sent Events):
                                      `devices` {now, devices: [device...]} on every change,
                                      `command` {action, ...} for this device alone,
                                      `settings` {subtitle_size, seed_torrents} when one changes,
                                      `continue` {continue: [video...], videos: {id: {position,
                                      watched}}, version} when continue_row changes,
                                      `jobs` [job...] when a download changes, at most once a second
  POST /api/report                    {id, name?, video|null, audio|null, subtitle|null,
                                       position, playing, volume?, muted?, speed?} -> {ok}
  POST /api/control                   {device, action: play|pause|stop|previous|next} or
                                      {device, action: seek, position} or
                                      {device, action: volume, volume?, muted?} or
                                      {device, action: speed, speed} or
                                      {device, action: audio, audio|null} or
                                      {device, action: subtitle, subtitle|null} or
                                      {device, action: load, video, position, audio|null,
                                       subtitle|null, volume?, speed?} -> {ok}
  GET  /api/settings                  {local, library_folders: [{name, folder, free}...],
                                       excluded: [...], shared: {subtitle_size, seed_torrents}}
  POST /api/settings/shared           {<setting>: value, ...} -> shared settings; from any device
  POST /api/settings/library-folders  {action: add|remove, folder} -> settings;
                                      from the computer MiruTube runs on alone
  POST /api/library/remove            {id} -> {id, path, kind}
  POST /api/library/restore           {path} -> {path}
  POST /api/forget                    {playlist} or {video} -> {forgotten: [video...]}
  POST /api/rescan                    -> {videos, playlists}
  GET  /api/downloads                 {jobs: [job...]}: what is coming into the library (downloads)
  POST /api/downloads/upload          {folder?, files: [{path, size}], device?} -> {job, received: [bytes...]}
  PUT  /api/downloads/<job>/<n>?offset=<bytes>
                                      a piece of the job's file n -> {received}; 409 with
                                      {received} when the offset is not where the file ends
  PUT  /api/downloads/torrent?folder=<name>&device=<id>
                                      a .torrent file -> {job}
  POST /api/downloads/link            {folder?, link, device?} -> {job}: a site, a .torrent or a magnet
  POST /api/downloads/cancel          {job}: an unfinished one, deleting what it wrote
  POST /api/downloads/stop-seeding    {job}
  POST /api/downloads/dismiss         {job}: a finished one leaves the list
  GET  /api/address                   {address, secure, host, port}
  GET  /api/address.svg               its QR code as SVG
"""

import argparse
import collections
import configparser
import io
import ipaddress
import json
import mimetypes
import os
import re
import select
import shutil
import socket
import ssl
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from mirutube import certificates, media, qr
from mirutube.downloads import PIECE_LIMIT, TORRENT_LIMIT, Downloads, NotSupported
from mirutube.library import Library, VIDEO_EXTENSIONS, normalise, on_host
from mirutube.store import SHARED_SETTINGS, Store

DEFAULT_PORT = 8800
DEFAULT_CACHE = media.platform_cache_dir()
DEFAULT_DATA = certificates.platform_data_dir()


def platform_config_file():
    """server#fields#config_file: `$XDG_CONFIG_HOME/mirutube/mirutube.conf`,
    by default `~/.config/mirutube/mirutube.conf`, on Linux; beside the data
    folder elsewhere."""
    if sys.platform.startswith("win") or sys.platform == "darwin":
        return os.path.join(DEFAULT_DATA, "mirutube.conf")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "mirutube", "mirutube.conf")


DEFAULT_CONFIG = platform_config_file()
CONFIG_KEYS = ("port", "host", "cert", "key", "data", "cache")
STATIC_DIR = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"))
# A video stopped within its last minute — or its last tenth, when it is
# shorter than ten minutes — counts as watched (behaviour#resume).
WATCHED_WITHIN = 60.0
SHORT_VIDEO = 600.0


def watched_within(duration):
    """Seconds before the end that count as watched."""
    return WATCHED_WITHIN if duration >= SHORT_VIDEO else duration / 10
CHUNK = 256 * 1024

# ffmpeg processes making pieces for one viewer at once (server#serve_stream)
PIECES_PER_VIEWER = 2

VIDEO_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
               ".mkv": "video/x-matroska", ".mov": "video/quicktime", ".ogv": "video/ogg",
               ".avi": "video/x-msvideo", ".ts": "video/mp2t", ".m2ts": "video/mp2t"}


def parse_range(header, size):
    """(start, end) inclusive for a `Range: bytes=` header, None to send the
    whole file, or "unsatisfiable"."""
    # Only the first range of a multi-range request is answered; players ask for one.
    m = re.fullmatch(r"\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*", (header or "").split(",")[0])
    if not m or (m.group(1) == "" and m.group(2) == ""):
        return None
    first, last = m.groups()
    if first == "":
        length = int(last)
        if length == 0 or size == 0:
            return "unsatisfiable"
        return max(0, size - length), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or start > end:
        return "unsatisfiable"
    return start, end


def continue_list(catalogue, positions):
    """The videos of continue_row, the most recently watched first: per
    playlist being watched the video to go on with — the one started and not
    finished, else the next one not watched after the last watched — and every
    video outside any playlist started and not finished
    (behaviour#resume#scenarios#one-card). A playlist with nothing left to
    watch has none."""
    def started(vid):
        p = positions.get(vid)
        return p is not None and not p["watched"] and p["position"] > 0

    def watched(vid):
        p = positions.get(vid)
        return p is not None and p["watched"]

    entries = []
    for playlist in catalogue["playlists"]:
        ids = playlist["videos"]
        seen = [vid for vid in ids if vid in positions]
        if not seen:
            continue
        latest = max(positions[vid]["updated"] for vid in seen)
        going = [vid for vid in seen if started(vid)]
        if going:
            entries.append((latest, max(going, key=lambda v: positions[v]["updated"])))
            continue
        done = [vid for vid in seen if watched(vid)]
        if not done:
            continue
        last = ids.index(max(done, key=lambda v: positions[v]["updated"]))
        # the next not watched after it, else — the ones after it all watched —
        # the first not watched before it
        after = [vid for vid in ids[last + 1:] + ids[:last] if not watched(vid)]
        if after:
            entries.append((latest, after[0]))
    for vid in catalogue["standalone"]:
        if started(vid):
            entries.append((positions[vid]["updated"], vid))
    entries.sort(key=lambda e: e[0], reverse=True)
    return [vid for _, vid in entries]


class ViewerSlots:
    """At most `limit` ffmpeg processes making pieces for one viewer — a
    viewer being a device, by its address — so seeking around a long video
    does not pile up work (server#serve_stream)."""

    def __init__(self, limit=PIECES_PER_VIEWER):
        self.limit = limit
        self._running = {}
        self._changed = threading.Condition()

    def acquire(self, viewer, cancelled):
        """Waits for a slot; False when `cancelled()` turns true first."""
        with self._changed:
            while self._running.get(viewer, 0) >= self.limit:
                if cancelled():
                    return False
                self._changed.wait(0.2)
            self._running[viewer] = self._running.get(viewer, 0) + 1
            return True

    def release(self, viewer):
        with self._changed:
            left = self._running.get(viewer, 0) - 1
            if left > 0:
                self._running[viewer] = left
            else:
                self._running.pop(viewer, None)
            self._changed.notify_all()

    def running(self, viewer):
        with self._changed:
            return self._running.get(viewer, 0)


# Devices (server#serve_events, server#control, behaviour#devices)

# A device stays in the list this long after its last event stream closed, so
# a reload does not drop it.
DEVICE_GRACE = 5.0
# How long one write to an event stream may take before the page counts as
# gone: a stuck client holds up only its own stream, and not for ever.
EVENT_WRITE_TIMEOUT = 15.0
TLS_HANDSHAKE_TIMEOUT = 20.0  # seconds a client may take over the TLS handshake
KEEPALIVE = 15.0              # seconds between comments on an otherwise quiet stream
EVENT_RETRY = 2000            # ms the page waits before opening a lost stream again
COMMANDS_KEPT = 16            # commands waiting for one stream; older ones are dropped
NAME_LENGTH = 80
ACTIONS = {"play", "pause", "seek", "previous", "next", "stop", "load",
           "volume", "speed", "audio", "subtitle"}
SPEEDS = (0.5, 2.0)           # the playback speeds now_playing#speed offers, lowest and highest


class EventStream:
    """What waits to be sent on one page's event stream. Senders only mark it
    and wake its own thread, which does the writing, so a client that stops
    reading never holds up the others. A change is one flag per topic —
    `devices`, `settings`, `continue`, `jobs` — and what the topic holds is
    taken when it is written, so a slow stream gets the latest once rather than
    every one in between."""

    def __init__(self, device, first=("devices",)):
        self.device = device
        self.changed = set(first)   # sent first thing
        self.commands = collections.deque(maxlen=COMMANDS_KEPT)
        self.closed = False
        self._wake = threading.Condition()

    def mark_changed(self, topic="devices"):
        with self._wake:
            self.changed.add(topic)
            self._wake.notify()

    def push(self, command):
        with self._wake:
            self.commands.append(command)
            self._wake.notify()

    def close(self):
        with self._wake:
            self.closed = True
            self._wake.notify()

    def take(self, timeout):
        """Waits up to `timeout` for something to send; (topics changed,
        commands)."""
        with self._wake:
            if not (self.changed or self.commands or self.closed):
                self._wake.wait(timeout)
            changed, commands = self.changed, list(self.commands)
            self.changed = set()
            self.commands.clear()
            return changed, commands


class ContinueRow:
    """continue_row as the pages were last told it: the row's videos and what
    was sent with them, so a position moving on within a video, which leaves
    the row as it was, sends nothing."""

    def __init__(self):
        self._lock = threading.Lock()
        self.last = None
        self.payload = None
        self.version = 0      # counts the changes; the catalogue says which one it is as new as

    def update(self, ids, payload):
        """Takes the row as it is now; True when it changed. The payload is
        numbered, so a page that loads the catalogue while it comes keeps the
        newer of the two."""
        with self._lock:
            if ids == self.last:
                return False
            self.version += 1
            self.last, self.payload = list(ids), dict(payload, version=self.version)
            return True


class Devices:
    """server#fields#devices: the devices connected now, kept in memory and
    never written down. A device is there from its first event stream until
    DEVICE_GRACE after its last one closed."""

    def __init__(self, grace=DEVICE_GRACE):
        self.grace = grace
        self._lock = threading.Lock()
        self._devices = {}    # id -> {id, name, video, audio, subtitle, position, reported, playing}
        self._streams = {}    # id -> set of EventStream
        self._closed = False

    def connect(self, device_id, name, first=("devices",)):
        stream = EventStream(device_id, first)
        with self._lock:
            if self._closed:
                stream.closed = True
                return stream
            device = self._devices.get(device_id)
            if device is None:
                self._devices[device_id] = {
                    "id": device_id, "name": name, "video": None, "audio": None, "subtitle": None,
                    "position": 0.0, "reported": time.time(), "playing": False,
                    "volume": None, "muted": None, "speed": None}
            else:
                device["name"] = name
            self._streams.setdefault(device_id, set()).add(stream)
        self.broadcast()
        return stream

    def disconnect(self, stream):
        with self._lock:
            streams = self._streams.get(stream.device)
            if streams is None:
                return
            streams.discard(stream)
            last = not streams
        if last:
            timer = threading.Timer(self.grace, self._expire, (stream.device,))
            timer.daemon = True
            timer.start()

    def _expire(self, device_id):
        with self._lock:
            if self._streams.get(device_id):
                return  # opened again in time: a reload
            self._streams.pop(device_id, None)
            gone = self._devices.pop(device_id, None)
        if gone is not None:
            self.broadcast()

    def snapshot(self):
        with self._lock:
            return {"now": time.time(), "devices": [dict(d) for d in self._devices.values()]}

    def broadcast(self, topic="devices"):
        """Marks `topic` changed on every page's stream."""
        with self._lock:
            streams = [s for group in self._streams.values() for s in group]
        for s in streams:
            s.mark_changed(topic)

    def report(self, device_id, state):
        """Records what a device plays; False for a device not connected."""
        with self._lock:
            device = self._devices.get(device_id)
            if device is None:
                return False
            device.update(state, reported=time.time())
        self.broadcast()
        return True

    def command(self, device_id, command):
        """Sends a command to one device alone; False for one not connected."""
        with self._lock:
            if device_id not in self._devices:
                return False
            streams = list(self._streams.get(device_id, ()))
        for s in streams:
            s.push(command)
        return True

    def close(self):
        """Ends every event stream, as the server stops."""
        with self._lock:
            self._closed = True
            streams = [s for group in self._streams.values() for s in group]
        for s in streams:
            s.close()


def _event(name, data):
    return ("event: %s\ndata: %s\n\n" % (name, json.dumps(data, ensure_ascii=False))).encode("utf-8")


def _number(value):
    """A finite number of seconds at or after 0, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return max(0.0, value) if value == value and abs(value) != float("inf") else None


def _optional_str(value, limit=1024):
    return value is None or (isinstance(value, str) and len(value) <= limit)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MiruTube"

    # set by make_server
    library = None
    store = None
    slots = None
    devices = None
    continue_row = None
    downloads = None
    quiet = False
    data_dir = None              # where MiruTube's authority is kept, when it is the one in use
    port = DEFAULT_PORT
    server_address_host = None   # the one address the server listens on, if it is told one

    def setup(self):
        # HTTPS or plain HTTP, told apart by the first byte the client sends:
        # 22 opens a TLS handshake (decisions#own-ca). The handshake is made
        # on this connection's own thread, so a client slow to make it holds
        # up no other; a failed one ends the connection unanswered.
        failed = False
        context = self.server.ssl_context
        if context is not None:
            self.request.settimeout(TLS_HANDSHAKE_TIMEOUT)
            try:
                first = self.request.recv(1, socket.MSG_PEEK)
                if first == b"\x16":
                    self.request = context.wrap_socket(self.request, server_side=True)
            except (OSError, ssl.SSLError, ValueError):
                failed = True
            finally:
                try:
                    self.request.settimeout(None)
                except OSError:
                    pass
        super().setup()
        if failed:
            self.close_connection = True
            self.rfile = io.BytesIO()

    @property
    def secure(self):
        return isinstance(self.connection, ssl.SSLSocket)

    def log_message(self, fmt, *args):
        if not self.quiet:
            super().log_message(fmt, *args)

    # ------------------------------------------------------------ helpers

    def send_bytes(self, status, body, content_type, extra=None, head=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def send_json(self, data, status=HTTPStatus.OK):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_bytes(status, body, "application/json; charset=utf-8",
                        {"Cache-Control": "no-store"})

    def fail(self, status, message=None):
        body = (message or status.phrase).encode("utf-8")
        self.send_bytes(status, body, "text/plain; charset=utf-8")

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 1 << 20:
            return None
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def library_file(self, relative):
        """The file a request names, or None after refusing it."""
        full = self.library.resolve(relative)
        if full is None:
            self.fail(HTTPStatus.FORBIDDEN, "outside the library")
            return None
        if not os.path.isfile(full):
            self.fail(HTTPStatus.NOT_FOUND)
            return None
        return full

    def named(self, sid):
        """The path within the library of the video or playlist a short id
        names, or None after refusing a request for one the catalogue does
        not have (behaviour#library-only)."""
        path = self.library.path_of(sid)
        if path is None:
            self.fail(HTTPStatus.NOT_FOUND, "not in the catalogue")
        return path

    def client_gone(self):
        """Whether the client has closed the connection: a page that seeks
        elsewhere stops waiting for the piece it asked for. Over TLS the
        bytes waiting are looked at below the encryption: a client closing
        sends an alert (record type 21) before it goes."""
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            if not readable:
                return False
            peeked = socket.socket.recv(self.connection, 1, socket.MSG_PEEK)
            return peeked == b"" or (isinstance(self.connection, ssl.SSLSocket) and peeked == b"\x15")
        except (OSError, ValueError):
            return True

    # ------------------------------------------------------------- routing

    def do_HEAD(self):
        self.do_GET(head=True)

    def do_GET(self, head=False):
        url = urlsplit(self.path)
        path, query = url.path, parse_qs(url.query)
        try:
            if path in ("/", "/index.html", "/settings") or \
                    re.fullmatch(r"/(watch|playlist)/[a-z2-7]+", path):
                return self.send_static("index.html", head)
            if path == "/mirutube.crt":
                return self.serve_certificate(head)
            if path == "/api/ping":
                return self.send_json({"ok": True})
            if path == "/sw.js":
                # served from the root: a service worker acts only under the
                # path it is served from, and it must cover the whole site
                # for a browser to install it (page#manifest)
                return self.send_static("sw.js", head)
            if path.startswith("/static/"):
                return self.send_static(unquote(path[len("/static/"):]), head)
            if path == "/api/catalogue":
                return self.send_json(self.catalogue())
            if path == "/api/position":
                sid = (query.get("video") or [""])[0]
                vid = self.named(sid)
                if vid is not None:
                    self.send_json(dict(self.store.position(vid), video=sid))
                return
            if path == "/api/details":
                sid = (query.get("video") or [""])[0]
                vid = self.named(sid)
                if vid is not None:
                    self.serve_details(vid, sid, (query.get("audio") or [None])[0])
                return
            if path == "/api/settings":
                return self.send_json(self.settings())
            if path == "/api/downloads":
                return self.send_json({"jobs": self.downloads.snapshot()})
            if path == "/api/address":
                return self.send_json(self.address())
            if path == "/api/address.svg":
                return self.serve_address_svg(head)
            if path == "/api/events" and not head:
                return self.serve_events((query.get("id") or [""])[0],
                                         (query.get("name") or [""])[0])
            for prefix in ("/video/", "/stream/", "/thumb/", "/subtitle/"):
                if path.startswith(prefix):
                    vid = self.named(unquote(path[len(prefix):]))
                    if vid is None:
                        return
                    if prefix == "/video/":
                        return self.serve_video(vid, head)
                    if prefix == "/stream/":
                        return self.serve_stream(vid, (query.get("piece") or [None])[0],
                                                 (query.get("audio") or [None])[0])
                    if prefix == "/thumb/":
                        return self.serve_thumbnail(vid, head)
                    return self.serve_subtitle(vid, (query.get("track") or [""])[0])
            self.fail(HTTPStatus.NOT_FOUND)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def do_POST(self):
        path = urlsplit(self.path).path
        data = self.read_json()
        if data is None and path == "/api/rescan":
            data = {}  # nothing to say: a body is not needed
        if data is None:
            return self.fail(HTTPStatus.BAD_REQUEST, "expected a JSON object")
        if path == "/api/settings/library-folders":
            return self.change_library_folders(data)
        if path == "/api/settings/shared":
            return self.change_shared_settings(data)
        if path in ("/api/library/remove", "/api/library/restore"):
            return self.remove_or_restore(data, path.endswith("remove"))
        if path == "/api/forget":
            return self.forget(data)
        if path == "/api/rescan":
            cat = self.library.rescan()
            return self.send_json({"videos": len(cat["videos"]), "playlists": len(cat["playlists"])})
        if path == "/api/position":
            return self.record_position(data)
        if path in ("/api/subtitle-choice", "/api/audio-choice"):
            playlist, label, language = data.get("playlist"), data.get("label"), data.get("language")
            if not isinstance(playlist, str) or \
                    not all(v is None or isinstance(v, str) for v in (label, language)):
                return self.fail(HTTPStatus.BAD_REQUEST)
            if label is None:
                language = None  # subtitles switched off, or the default sound track
            folder = self.named(playlist)
            if folder is None:
                return
            if path == "/api/subtitle-choice":
                self.store.set_subtitle_choice(folder, label, language)
            else:
                self.store.set_audio_choice(folder, label, language)
            return self.send_json({"playlist": playlist, "label": label, "language": language})
        if path.startswith("/api/downloads/"):
            return self.download_action(path[len("/api/downloads/"):], data)
        if path == "/api/report":
            return self.take_report(data)
        if path == "/api/control":
            return self.control(data)
        self.fail(HTTPStatus.NOT_FOUND)

    # ------------------------------------------------------------ handlers

    def send_static(self, name, head=False):
        full = os.path.realpath(os.path.join(STATIC_DIR, name))
        try:
            inside = os.path.normcase(os.path.commonpath([full, STATIC_DIR])) == os.path.normcase(STATIC_DIR)
        except ValueError:  # another drive
            inside = False
        if not inside or not os.path.isfile(full):
            return self.fail(HTTPStatus.NOT_FOUND)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            self.send_bytes(HTTPStatus.OK, f.read(), ctype, {"Cache-Control": "no-cache"}, head)

    def catalogue(self):
        version = self.continue_row.version  # read first: the row computed below is at least this new
        cat = self.library.catalogue()
        positions = self.store.positions()
        videos = {}
        for vid, v in cat["videos"].items():
            v = dict(v)
            v["subtitles"] = [{k: t[k] for k in ("id", "kind", "label", "language", "signs", "available", "reason")}
                              for t in v["subtitles"]]
            v["audio"] = [{k: t[k] for k in ("id", "kind", "label", "language", "default")}
                          for t in v["audio"]]
            p = positions.get(vid)
            v["position"] = p["position"] if p else 0.0
            v["watched"] = p["watched"] if p else False
            videos[vid] = v
        known = {vid: p for vid, p in positions.items() if vid in videos}
        # every path leaves as its short id (decisions#short-ids)
        short = cat["short"]
        for v in videos.values():
            v["id"] = short[v["id"]]
            if v.get("playlist") is not None:
                v["playlist"] = short.get(v["playlist"])
        playlists = [dict(p, id=short[p["id"]], videos=[short[v] for v in p["videos"]])
                     for p in cat["playlists"]]

        def choices(kept):
            return {short[k]: c for k, c in kept.items() if k in short}
        return {"playlists": playlists, "standalone": [short[v] for v in cat["standalone"]],
                "videos": {short[vid]: v for vid, v in videos.items()},
                "continue": [short[v] for v in continue_list(cat, known)],
                "continue_version": version,
                "subtitle_choices": choices(self.store.subtitle_choices()),
                "audio_choices": choices(self.store.audio_choices())}

    def record_position(self, data):
        sid = data.get("video")
        try:
            position = max(0.0, float(data.get("position")))
        except (TypeError, ValueError):
            return self.fail(HTTPStatus.BAD_REQUEST, "position must be a number")
        vid = self.named(sid)
        if vid is None:
            return
        video = self.library.video(vid)
        if video is None:
            return self.fail(HTTPStatus.NOT_FOUND)
        duration = video.get("duration")
        watched = bool(duration) and position >= duration - watched_within(duration)
        if watched:
            # stopped near the end: watched, and starts from the beginning
            position = 0.0
        self.store.set_position(vid, position, watched)
        self.send_json({"video": sid, "position": position, "watched": watched})
        self.announce_continue((vid,))

    def serve_events(self, device_id, name):
        """The stream of events one page keeps with the server
        (decisions#events). The page is a device from the moment it opens it
        until it closes, and for DEVICE_GRACE after (Devices). Every change to
        the devices is sent as `devices`, a command for this device as
        `command`, the shared settings as `settings` when one changes, and
        continue_row as `continue` when it changes (ContinueRow). The connection is watched for the page going: a closed
        connection ends the stream within a second, and a write that does not
        get through within EVENT_WRITE_TIMEOUT ends it too."""
        name = name.strip()[:NAME_LENGTH] or "Device"
        if not device_id or len(device_id) > 64:
            return self.fail(HTTPStatus.BAD_REQUEST, "a device id is needed")
        stream = self.devices.connect(device_id, name, first=("devices", "settings", "jobs"))
        self.close_connection = True
        try:
            self.connection.settimeout(EVENT_WRITE_TIMEOUT)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(b"retry: %d\n\n" % EVENT_RETRY)
            quiet_since = time.monotonic()
            while not stream.closed:
                changed, commands = stream.take(1.0)
                if stream.closed:
                    break
                out = b""
                for topic in ("devices", "settings", "continue", "jobs"):
                    if topic in changed:
                        data = self.topic(topic)
                        if data is not None:
                            out += _event(topic, data)
                for command in commands:
                    out += _event("command", command)
                if not out and time.monotonic() - quiet_since >= KEEPALIVE:
                    out = b": keepalive\n\n"
                if out:
                    self.wfile.write(out)
                    self.wfile.flush()
                    quiet_since = time.monotonic()
                if self.client_gone():
                    break
        except (OSError, ValueError):
            pass  # the page went: a broken pipe, a reset, or a write that timed out
        finally:
            self.devices.disconnect(stream)

    def topic(self, topic):
        """What one topic of the event stream holds now."""
        if topic == "devices":
            return self.devices.snapshot()
        if topic == "settings":
            return self.store.shared_settings()
        if topic == "continue":
            return self.continue_row.payload
        if topic == "jobs":
            return self.downloads.snapshot()
        return None

    def announce_continue(self, also=()):
        """server#serve_events: continue_row's videos, with the positions of
        those and of the videos in `also`, to every page when the row is no
        longer what it was (behaviour#resume#scenarios#live-row)."""
        cat = self.library.catalogue()
        positions = self.store.positions()
        known = {vid: p for vid, p in positions.items() if vid in cat["videos"]}
        ids = continue_list(cat, known)
        short = cat["short"]
        videos = {}
        for vid in list(ids) + [v for v in also if v in cat["videos"]]:
            p = positions.get(vid)
            videos[short[vid]] = {"position": p["position"] if p else 0.0, "watched": p["watched"] if p else False}
        payload = {"continue": [short[v] for v in ids], "videos": videos}
        if self.continue_row.update([short[v] for v in ids], payload):
            self.devices.broadcast("continue")

    def take_report(self, data):
        """server#control: a page's report of what its device plays — and,
        when the page says, its volume and speed, which the bar on another
        device shows for it (now_playing#volume, #speed)."""
        device_id, name, video = data.get("id"), data.get("name"), data.get("video")
        audio, subtitle, playing = data.get("audio"), data.get("subtitle"), data.get("playing", False)
        position = _number(data.get("position", 0))
        if not isinstance(device_id, str) or not _optional_str(name) or not _optional_str(video) \
                or not _optional_str(audio, 64) or not _optional_str(subtitle, 64) \
                or not isinstance(playing, bool) or position is None:
            return self.fail(HTTPStatus.BAD_REQUEST)
        extra = {}
        if data.get("volume") is not None:
            extra["volume"] = _number(data["volume"])
            if extra["volume"] is None or extra["volume"] > 1:
                return self.fail(HTTPStatus.BAD_REQUEST, "volume runs from 0 to 1")
        if data.get("muted") is not None:
            if not isinstance(data["muted"], bool):
                return self.fail(HTTPStatus.BAD_REQUEST)
            extra["muted"] = data["muted"]
        if data.get("speed") is not None:
            extra["speed"] = _number(data["speed"])
            if not extra["speed"]:
                return self.fail(HTTPStatus.BAD_REQUEST, "speed must be a number above 0")
        if video is not None and self.named(video) is None:
            return
        state = {"video": video, "audio": audio, "subtitle": subtitle,
                 "position": position, "playing": playing, **extra}
        if video is None:
            state.update(audio=None, subtitle=None, position=0.0, playing=False)
        if name is not None and name.strip():
            state["name"] = name.strip()[:NAME_LENGTH]
        if not self.devices.report(device_id, state):
            return self.fail(HTTPStatus.NOT_FOUND, "no such device")
        self.send_json({"ok": True})

    def control(self, data):
        """server#control: a page's command for any device — play, pause,
        seek, previous, next, volume, speed, sound track, subtitles, stop,
        and load: play a video from a position with the given tracks, volume
        and speed, which is how playback moves from one device to another
        (behaviour#devices). The command reaches that device alone; whether it
        can be done there — a track the video has — is that device's to find
        out. Full screen is not among them: a browser goes full screen only
        from a touch on its own page (decisions#fullscreen-here)."""
        device_id, action = data.get("device"), data.get("action")
        if not isinstance(device_id, str) or action not in ACTIONS:
            return self.fail(HTTPStatus.BAD_REQUEST)
        command = {"action": action}
        if action in ("seek", "load"):
            position = _number(data.get("position", 0 if action == "load" else None))
            if position is None:
                return self.fail(HTTPStatus.BAD_REQUEST, "position must be a number")
            command["position"] = position
        elif action == "volume":
            volume, muted = data.get("volume"), data.get("muted")
            if volume is None and muted is None:
                return self.fail(HTTPStatus.BAD_REQUEST, "a volume or muted is needed")
            if volume is not None:
                volume = _number(volume)
                if volume is None or volume > 1:
                    return self.fail(HTTPStatus.BAD_REQUEST, "volume runs from 0 to 1")
                command["volume"] = volume
            if muted is not None:
                if not isinstance(muted, bool):
                    return self.fail(HTTPStatus.BAD_REQUEST, "muted is true or false")
                command["muted"] = muted
        elif action == "speed":
            speed = _number(data.get("speed"))
            if speed is None or not SPEEDS[0] <= speed <= SPEEDS[1]:
                return self.fail(HTTPStatus.BAD_REQUEST, "speed runs from %g to %g" % SPEEDS)
            command["speed"] = speed
        elif action in ("audio", "subtitle"):
            track = data.get(action)
            if not _optional_str(track, 64):
                return self.fail(HTTPStatus.BAD_REQUEST, "%s is a track id or null" % action)
            command[action] = track
        if action == "load":
            video, audio, subtitle = data.get("video"), data.get("audio"), data.get("subtitle")
            if not isinstance(video, str) or not _optional_str(audio, 64) or not _optional_str(subtitle, 64):
                return self.fail(HTTPStatus.BAD_REQUEST)
            vid = self.named(video)
            if vid is None:
                return
            if self.library.video(vid) is None:
                return self.fail(HTTPStatus.NOT_FOUND, "no such video")
            command.update(video=video, audio=audio, subtitle=subtitle)
            # the device it moves to plays as loud and as fast (behaviour#devices)
            volume, speed = data.get("volume"), data.get("speed")
            if volume is not None:
                volume = _number(volume)
                if volume is None or volume > 1:
                    return self.fail(HTTPStatus.BAD_REQUEST, "volume runs from 0 to 1")
                command["volume"] = volume
            if speed is not None:
                speed = _number(speed)
                if speed is None or not SPEEDS[0] <= speed <= SPEEDS[1]:
                    return self.fail(HTTPStatus.BAD_REQUEST, "speed runs from %g to %g" % SPEEDS)
                command["speed"] = speed
        if not self.devices.command(device_id, command):
            return self.fail(HTTPStatus.NOT_FOUND, "no such device")
        self.send_json({"ok": True})

    # ------------------------------------------------------------ settings

    def is_local(self):
        """Whether the request comes from the computer MiruTube runs on: from
        the very address it reached the server at — `127.0.0.1` for
        `localhost`, the computer's own address on the home network when it
        opens that (decisions#settings-local)."""
        return is_local_request(self.client_address[0], self.connection.getsockname()[0])

    def settings(self):
        """server#settings: the library folders with the free space on the
        disk of each, what was removed from the library with what each is — a
        video, a folder, or gone from the disk since — and the shared
        settings."""
        excluded = []
        for path in self.store.excluded():
            full = self.library.resolve(path)
            kind = "missing" if full is None or not os.path.exists(full) else \
                "folder" if os.path.isdir(full) else "video"
            name = path.rsplit("/", 1)[-1]
            excluded.append({"path": path, "kind": kind,
                             "title": normalise(name, is_file=kind != "folder") or name})
        return {"local": self.is_local(),
                "library_folders": [{"name": n, "folder": f, "free": free_space(on_host(f))}
                                    for n, f in self.library.folders.items()],
                "excluded": excluded,
                "shared": self.store.shared_settings()}

    def change_shared_settings(self, data):
        """server#settings: store#fields#shared_settings, changed from any
        device for every device, and sent to every page at once."""
        if not data:
            return self.fail(HTTPStatus.BAD_REQUEST, "expected {setting: value}")
        for name, value in data.items():
            if name not in SHARED_SETTINGS:
                return self.fail(HTTPStatus.BAD_REQUEST, "no such setting: %s" % name)
            if not SHARED_SETTINGS[name][1](value):
                return self.fail(HTTPStatus.BAD_REQUEST, "%s cannot be %s" % (name, json.dumps(value)))
        for name, value in data.items():
            self.store.set_shared_setting(name, value)
        self.devices.broadcast("settings")
        self.send_json(self.store.shared_settings())

    def reconfigure(self):
        self.library.configure(self.store.library_folders(), self.store.excluded())

    def change_library_folders(self, data):
        """server#settings: adds or removes a library folder — only from the
        computer MiruTube runs on, since with no accounts any device could
        otherwise make `/` a library folder (decisions#settings-local)."""
        if not self.is_local():
            return self.fail(HTTPStatus.FORBIDDEN,
                             "the library folders are changed on the computer MiruTube runs on")
        action, folder = data.get("action"), data.get("folder")
        if action not in ("add", "remove") or not isinstance(folder, str) or not folder.strip() \
                or "\0" in folder:
            return self.fail(HTTPStatus.BAD_REQUEST, "expected {action: add|remove, folder}")
        folder = os.path.expanduser(folder.strip())
        known = self.store.library_folders()
        key = os.path.normcase(os.path.abspath(folder))
        same = next((f for f in known if os.path.normcase(os.path.abspath(f)) == key), None)
        if action == "remove":
            if same is None:
                return self.fail(HTTPStatus.NOT_FOUND, "not a library folder")
            self.store.remove_library_folder(same)
        else:
            if not os.path.isabs(folder):
                return self.fail(HTTPStatus.BAD_REQUEST, "the folder's full path is needed")
            if not os.path.isdir(on_host(folder)):
                return self.fail(HTTPStatus.NOT_FOUND, "no such folder")
            if same is not None:
                return self.fail(HTTPStatus.CONFLICT, "already a library folder")
            real = os.path.normcase(os.path.realpath(on_host(folder)))
            for f in known:
                other = os.path.normcase(os.path.realpath(on_host(f)))
                try:
                    nested = os.path.commonpath([real, other]) in (real, other)
                except ValueError:  # another drive
                    nested = False
                if nested:
                    # the same videos would be in the library twice, by two paths
                    return self.fail(HTTPStatus.CONFLICT, "inside or around the library folder %s" % f)
            self.store.add_library_folder(os.path.abspath(folder))
        self.reconfigure()
        self.send_json(self.settings())

    def remove_or_restore(self, data, remove):
        """server#settings: removes a video or a folder from the library, or
        brings it back. Nothing on disk changes: the path joins
        store#fields#excluded, or leaves it (behaviour#library-control). What
        is removed is named by its id in the catalogue; what is brought back,
        no longer in it, by the path settings lists it under."""
        if remove:
            sid = data.get("id")
            path = self.named(sid)
            if path is None:
                return
            full = self.library.resolve(path)
            if full is None:
                return self.fail(HTTPStatus.FORBIDDEN, "outside the library")
            if "/" not in path:
                return self.fail(HTTPStatus.BAD_REQUEST, "a library folder is removed in settings")
            if not os.path.exists(full):
                return self.fail(HTTPStatus.NOT_FOUND)
            self.store.exclude(path)
            kind = "folder" if os.path.isdir(full) else "video"
            self.reconfigure()
            self.send_json({"id": sid, "path": path, "kind": kind})
            return self.announce_continue()
        path = data.get("path")
        if not isinstance(path, str) or not path.strip("/"):
            return self.fail(HTTPStatus.BAD_REQUEST, "expected {path}")
        path = path.strip("/")
        if not self.store.include(path):
            return self.fail(HTTPStatus.NOT_FOUND, "not removed")
        self.reconfigure()
        self.send_json({"path": path})
        self.announce_continue()

    def forget(self, data):
        """server#settings: forgets the positions of a playlist's videos, or of
        one video, so they count as not started — how a card leaves Continue
        watching (home#continue_row#forget_button)."""
        playlist, video = data.get("playlist"), data.get("video")
        if isinstance(playlist, str) and video is None:
            found = self.library.playlist(self.library.path_of(playlist))
            if found is None:
                return self.fail(HTTPStatus.NOT_FOUND, "no such playlist")
            videos = list(found["videos"])
        elif isinstance(video, str) and playlist is None:
            vid = self.named(video)
            if vid is None:
                return
            videos = [vid]
        else:
            return self.fail(HTTPStatus.BAD_REQUEST, "expected {playlist} or {video}")
        self.store.forget_positions(videos)
        self.send_json({"forgotten": [self.library.id_of(v) for v in videos]})
        self.announce_continue(videos)

    # ----------------------------------------------------------- uploads

    def device_name(self, device_id):
        if not isinstance(device_id, str):
            return None
        d = next((d for d in self.devices.snapshot()["devices"] if d["id"] == device_id), None)
        return d["name"] if d else None

    def upload_folder(self, name):
        """The library folder an upload goes to, by its name: the one asked
        for, else the first."""
        folders = list(self.library.folders)
        if name is None and folders:
            return folders[0]
        if name not in folders:
            raise NotSupported("no such library folder: %s" % name)
        return name

    def downloading(self, work):
        """Runs one request of server#uploads, answering what downloads
        refuses as the page shows it."""
        try:
            return work()
        except NotSupported as e:
            self.fail(HTTPStatus.UNPROCESSABLE_ENTITY, str(e))
        except (ValueError, TypeError) as e:
            self.fail(HTTPStatus.BAD_REQUEST, str(e) or "expected another request")
        except KeyError:
            self.fail(HTTPStatus.NOT_FOUND, "no such download")
        except OSError as e:
            self.fail(HTTPStatus.INSUFFICIENT_STORAGE if e.errno == 28 else HTTPStatus.INTERNAL_SERVER_ERROR,
                      str(e))

    def download_action(self, action, data):
        """server#uploads: start an upload or a link, cancel, stop seeding,
        drop a finished one. Any device may upload: what is written stays
        inside the library folders (decisions#upload-anywhere)."""
        device = data.get("device")
        name = self.device_name(device)

        def work():
            if action == "upload":
                files = data.get("files")
                if not isinstance(files, list) or not all(isinstance(f, dict) for f in files):
                    raise ValueError("expected {files: [{path, size}]}")
                job, received = self.downloads.start_upload(self.upload_folder(data.get("folder")), files,
                                                            device, name)
                return self.send_json({"job": job["id"], "received": received})
            if action == "link":
                if not isinstance(data.get("link"), str):
                    raise ValueError("expected {link}")
                job = self.downloads.start_link(self.upload_folder(data.get("folder")), data["link"], device, name)
                return self.send_json({"job": job["id"]})
            job_id = data.get("job")
            if not isinstance(job_id, str):
                raise ValueError("expected {job}")
            if action == "cancel":
                self.downloads.cancel(job_id)
            elif action == "stop-seeding":
                self.downloads.stop_seeding(job_id)
            elif action == "dismiss":
                self.downloads.dismiss(job_id)
            else:
                return self.fail(HTTPStatus.NOT_FOUND)
            return self.send_json({"ok": True})
        self.downloading(work)

    def do_PUT(self):
        """server#uploads: a piece of a file sent from a page, or a .torrent."""
        url = urlsplit(self.path)
        path, query = url.path, parse_qs(url.query)
        try:
            length = int(self.headers.get("Content-Length") or -1)
        except ValueError:
            length = -1
        torrent = path == "/api/downloads/torrent"
        limit = TORRENT_LIMIT if torrent else PIECE_LIMIT
        if length < 0 or length > limit:
            self.close_connection = True
            return self.fail(HTTPStatus.REQUEST_ENTITY_TOO_LARGE if length > limit else HTTPStatus.LENGTH_REQUIRED)
        body = self.rfile.read(length)
        if len(body) != length:
            self.close_connection = True
            return self.fail(HTTPStatus.BAD_REQUEST, "the piece did not arrive whole")
        if torrent:
            def start():
                device = (query.get("device") or [None])[0]
                job = self.downloads.start_torrent(self.upload_folder((query.get("folder") or [None])[0]),
                                                   data=body, device=device, device_name=self.device_name(device))
                self.send_json({"job": job["id"]})
            return self.downloading(start)
        m = re.fullmatch(r"/api/downloads/([0-9a-f]+)/(\d+)", path)
        if not m:
            return self.fail(HTTPStatus.NOT_FOUND)

        def piece():
            offset = int((query.get("offset") or ["x"])[0])
            taken, received = self.downloads.receive(m.group(1), int(m.group(2)), offset, body)
            self.send_json({"received": received}, HTTPStatus.OK if taken else HTTPStatus.CONFLICT)
        self.downloading(piece)

    # ------------------------------------------------------------- address

    def address(self):
        """server#serve_address: the address other devices open MiruTube at —
        over plain HTTP, which every device can open; `secure` is the same
        over HTTPS, where a device that trusts MiruTube's certificate goes on
        to (page#secure_notice)."""
        plain = address_of("http", self.server_address_host, self.port)
        secure = address_of("https", self.server_address_host, self.port)["address"] \
            if self.server.ssl_context is not None else None
        return {"address": plain["address"], "secure": secure, "host": plain["host"], "port": plain["port"]}

    def serve_certificate(self, head=False):
        """server#serve_certificate: the certificate of MiruTube's authority —
        never its key — as a file a device offers to install, over plain
        HTTP too, since a device without it cannot open HTTPS yet."""
        if self.data_dir is None:
            return self.fail(HTTPStatus.NOT_FOUND, "this server uses a certificate it was given")
        pem = certificates.authority_pem(self.data_dir).encode("ascii")
        self.send_bytes(HTTPStatus.OK, pem, "application/x-x509-ca-cert",
                        {"Content-Disposition": 'attachment; filename="MiruTube.crt"',
                         "Cache-Control": "no-cache"}, head)

    def serve_address_svg(self, head=False):
        """server#serve_address: the address as a QR code, drawn as SVG by
        MiruTube's own code (decisions#own-qr)."""
        address = self.address()["address"]
        if address is None:
            return self.fail(HTTPStatus.NOT_FOUND, "this computer has no address on a network")
        self.send_bytes(HTTPStatus.OK, qr_svg(address).encode("utf-8"), "image/svg+xml; charset=utf-8",
                        {"Cache-Control": "no-cache"}, head)

    # ------------------------------------------------------------- details

    def serve_details(self, relative, sid, audio_id=None):
        """What a video's file is and how it reaches the browser, for
        watch#details: `media` from media#details, and `delivery` — `direct`,
        played as it is; `copy`, sent in pieces with the video copied and the
        sound converted; `transcode`, converted as it plays, with the encoder
        that does it; or `unplayable`, with the reason. With `audio`, the
        delivery with that sound track chosen (decisions#audio-in-pieces)."""
        full = self.library_file(relative)
        if full is None:
            return
        video = self.library.video(relative)
        if video is None:
            return self.fail(HTTPStatus.NOT_FOUND, "no such video")
        track = None
        if audio_id is not None:
            track = next((t for t in video.get("audio", []) if t["id"] == audio_id), None)
            if track is None:
                return self.fail(HTTPStatus.NOT_FOUND, "no such sound track")
            if track["default"]:
                track = None
        audio = None
        if track is not None and track["kind"] == "embedded":
            audio = track["stream"]
        elif track is not None:
            audio = self.library.resolve(track["path"])
            if audio is None or not os.path.isfile(audio):
                return self.fail(HTTPStatus.NOT_FOUND, "the sound file is gone")
        delivery = {"kind": video["delivery"], "encoder": None, "reason": video.get("reason")}
        if video["delivery"] == "stream" or (track is not None and video["delivery"] == "direct"):
            try:
                copy = media.stream_plan(full, audio)["copy"]
            except ValueError as e:
                delivery.update(kind="unplayable", reason=str(e))
            else:
                delivery["kind"] = "copy" if copy else "transcode"
                if not copy:
                    delivery["encoder"] = media.describe_encoder(media.transcoder())
        self.send_json({"video": sid, "media": media.details(full, audio), "delivery": delivery})

    def serve_video(self, relative, head=False):
        """Sends a video file, answering an HTTP Range request with just the
        bytes asked for. The player seeks by asking for the bytes at the new
        position, so without Range a seek waits for everything before it to
        download."""
        full = self.library_file(relative)
        if full is None:
            return
        ext = os.path.splitext(full)[1].lower()
        if ext not in VIDEO_EXTENSIONS:
            return self.fail(HTTPStatus.NOT_FOUND)
        size = os.path.getsize(full)
        ctype = VIDEO_TYPES.get(ext) or mimetypes.guess_type(full)[0] or "application/octet-stream"
        rng = parse_range(self.headers.get("Range"), size)
        if rng == "unsatisfiable":
            return self.send_bytes(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, b"", "text/plain",
                                   {"Content-Range": "bytes */%d" % size})
        if rng is None:
            start, end, status = 0, size - 1, HTTPStatus.OK
        else:
            (start, end), status = rng, HTTPStatus.PARTIAL_CONTENT
        length = end - start + 1 if size else 0
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        if head:
            return
        with open(full, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(CHUNK, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def serve_stream(self, relative, piece, audio_id=None):
        """Answers the page's requests for a `stream` video — its plan from
        media#stream_plan, then its pieces from media#stream_segment. At most
        PIECES_PER_VIEWER ffmpeg processes run for one viewer, and one still
        making a piece nobody waits for any more — the page gave up on the
        request — is stopped.

        `audio_id` names the sound track chosen, one of the video's in the
        catalogue. A track other than the default is played this way whatever
        the video's delivery, since a browser plays only a file's first audio
        stream (decisions#audio-in-pieces); without one, or with the default,
        only a `stream` video has pieces."""
        full = self.library_file(relative)
        if full is None:
            return
        video = self.library.video(relative)
        if video is None or not video.get("playable"):
            return self.fail(HTTPStatus.NOT_FOUND, "not a video that plays")
        track = None
        if audio_id is not None:
            track = next((t for t in video.get("audio", []) if t["id"] == audio_id), None)
            if track is None:
                return self.fail(HTTPStatus.NOT_FOUND, "no such sound track")
            if track["default"]:
                track = None
        if track is None and video.get("delivery") != "stream":
            return self.fail(HTTPStatus.NOT_FOUND, "not a video converted as it plays")
        audio = None
        if track is not None and track["kind"] == "embedded":
            audio = track["stream"]
        elif track is not None:
            # an external file, which must lie in the library too
            audio = self.library.resolve(track["path"])
            if audio is None or not os.path.isfile(audio):
                return self.fail(HTTPStatus.NOT_FOUND, "the sound file is gone")
        try:
            plan = media.stream_plan(full, audio)
        except ValueError as e:
            return self.fail(HTTPStatus.NOT_FOUND, str(e))
        if piece is None:
            return self.send_json({k: plan[k] for k in ("duration", "offset", "starts", "mime")})
        try:
            index = int(piece)
        except ValueError:
            return self.fail(HTTPStatus.BAD_REQUEST, "piece must be a number")
        if not 0 <= index < len(plan["starts"]):
            return self.fail(HTTPStatus.NOT_FOUND, "no such piece")
        viewer = self.client_address[0]
        if not self.slots.acquire(viewer, self.client_gone):
            self.close_connection = True
            return
        try:
            data = media.stream_segment(full, index, cancelled=self.client_gone, audio=audio)
        except (RuntimeError, ValueError, IndexError, OSError) as e:
            return self.fail(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))
        finally:
            self.slots.release(viewer)
        if data is None:  # nobody waits for it any more
            self.close_connection = True
            return
        self.send_bytes(HTTPStatus.OK, data, "video/mp4", {"Cache-Control": "no-store"})

    def serve_thumbnail(self, relative, head=False):
        full = self.library_file(relative)
        if full is None:
            return
        jpeg = media.thumbnail(full)
        if jpeg is None:
            return self.fail(HTTPStatus.NOT_FOUND, "no frame could be taken")
        with open(jpeg, "rb") as f:
            self.send_bytes(HTTPStatus.OK, f.read(), "image/jpeg",
                            {"Cache-Control": "max-age=3600"}, head)

    def serve_subtitle(self, relative, track_id):
        full = self.library_file(relative)
        if full is None:
            return
        video = self.library.video(relative)
        track = next((t for t in (video or {}).get("subtitles", []) if t["id"] == track_id), None)
        if track is None:
            return self.fail(HTTPStatus.NOT_FOUND, "no such subtitle track")
        if not track["available"]:
            return self.fail(HTTPStatus.NOT_FOUND, track["reason"])
        try:
            if track["kind"] == "embedded":
                vtt = media.subtitles_vtt(full, stream=track["stream"])
            else:
                source = self.library_file(track["path"])
                if source is None:
                    return
                vtt = media.subtitles_vtt(source)
        except (RuntimeError, OSError) as e:
            return self.fail(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))
        self.send_bytes(HTTPStatus.OK, vtt.encode("utf-8"), "text/vtt; charset=utf-8")


class MiruTubeServer(ThreadingHTTPServer):
    """The server, answering HTTPS and plain HTTP on one port when it has a
    TLS context (decisions#own-ca); which a connection gets is decided on
    that connection's own thread (Handler.setup)."""

    daemon_threads = True
    ssl_context = None


def tls_context(cert, key):
    """A server TLS context from a certificate and its key, PEM files."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return context


def make_server(library_dir=None, port=DEFAULT_PORT, host="", cache_dir=DEFAULT_CACHE, quiet=False,
                cert=None, key=None, data_dir=DEFAULT_DATA):
    """The server for the library folders kept in store — `library_dir`, the
    folder given on the command line, being added on the first start
    (store#fields#library_folders) — with what it keeps in `data_dir` and its
    cache in `cache_dir`. It answers HTTPS beside plain HTTP with the
    certificate and key given, else with one MiruTube's authority in
    `data_dir` signs for this computer's addresses (decisions#own-ca)."""
    media.set_cache_dir(cache_dir)
    _move_old_store(cache_dir, data_dir)
    store = Store(data_dir)
    store.first_start(os.path.abspath(library_dir) if library_dir else None)
    bound = host if host and host not in ("0.0.0.0", "::") and not _loopback(host) else None
    if cert:
        context, authority_dir = tls_context(cert, key), None
    else:
        names = certificates.default_names([bound or lan_address()])
        context = tls_context(*certificates.server_certificate(data_dir, names))
        authority_dir = data_dir
    handler = type("MiruTubeHandler", (Handler,), {
        "library": Library(store.library_folders(), excluded=store.excluded()),
        "store": store,
        "slots": ViewerSlots(),
        "devices": Devices(),
        "continue_row": ContinueRow(),
        "downloads": None,
        "quiet": quiet,
        "data_dir": authority_dir,
        "server_address_host": bound,
    })
    server = MiruTubeServer((host, port), handler)
    server.ssl_context = context
    server.certificate_names = None if cert else names
    handler.port = server.server_address[1]
    server.devices = handler.devices
    server.handler = handler
    devices = handler.devices
    handler.downloads = Downloads(data_dir, handler.library, store.shared_settings,
                                  lambda: devices.broadcast("jobs"))
    server.downloads = handler.downloads
    return server


def _move_old_store(cache_dir, data_dir):
    """server#fields#data_folder: a store an earlier version kept in the cache
    folder moves to the data folder, so its positions are not lost."""
    old, new = os.path.join(cache_dir, "mirutube.db"), os.path.join(data_dir, "mirutube.db")
    if os.path.abspath(old) == os.path.abspath(new) or os.path.exists(new) or not os.path.exists(old):
        return
    os.makedirs(data_dir, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(old + suffix):
            shutil.move(old + suffix, new + suffix)


def read_config(path):
    """server#fields#config_file: the `[server]` keys of the file at `path`
    that are set, as text; nothing when it is missing."""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with open(path, encoding="utf-8") as f:
            parser.read_file(f)
    except FileNotFoundError:
        return {}
    if not parser.has_section("server"):
        return {}
    unknown = set(parser["server"]) - set(CONFIG_KEYS)
    if unknown:
        raise ValueError("unknown key in %s: %s" % (path, ", ".join(sorted(unknown))))
    return {k: v.strip() for k, v in parser["server"].items() if v.strip()}


ADDRESS_CHECK_EVERY = 60.0  # seconds between looks at this computer's address


def watch_address(server, data_dir, bound, stop):
    """certificates#server_certificate: the server certificate made again,
    and put in place for new connections, when this computer's address
    changes — or appears, for a server started before the network was up, as
    a service at boot is."""
    while not stop.wait(ADDRESS_CHECK_EVERY):
        names = certificates.default_names([bound or lan_address()])
        if names == server.certificate_names:
            continue
        try:
            server.ssl_context.load_cert_chain(*certificates.server_certificate(data_dir, names))
        except (OSError, ssl.SSLError) as e:
            print("MiruTube: the certificate could not be made again: %s" % e, flush=True)
            continue
        server.certificate_names = names


def _log_encoder():
    """Writes the encoder transcoding uses to the log, so it is plain what the
    server runs on (media#stream_segment). Found in the background: trying the
    hardware encoders takes a moment the server need not wait for."""
    encoder = media.transcoder()
    print("MiruTube: converting with %s" % media.describe_encoder(encoder), flush=True)


def free_space(folder):
    """The bytes free on the disk a folder is on, or None when it cannot be
    read — a disk not mounted."""
    try:
        return shutil.disk_usage(folder).free
    except OSError:
        return None


def _loopback(host):
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return host == "localhost"


def _plain_ip(address):
    """An address as compared: an IPv4 address mapped into IPv6 as itself."""
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return address
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return str(ip)


def is_local_request(client, server):
    """Whether a connection from `client` to the server's own address
    `server` comes from the computer the server runs on: its address is the
    one it reached the server at, as a connection a computer makes to itself
    is (decisions#settings-local)."""
    return _plain_ip(client) == _plain_ip(server)


def lan_address():
    """This computer's address on the home network: that of the interface the
    default route leaves by — found by pointing a UDP socket at an address
    outside the network, which sends nothing — else any address its name
    resolves to that is not loopback; None when it has neither."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            address = s.getsockname()[0]
            if address and not address.startswith(("0.", "127.")):
                return address
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if not address.startswith("127."):
                return address
    except OSError:
        pass
    return None


def address_of(scheme, bound, port):
    """server#serve_address: {address, scheme, host, port} — the address
    other devices open MiruTube at: the one it listens on when that is a
    single address on the network (`bound`), else lan_address(); `address`
    is None when this computer has no address on a network."""
    host = bound or lan_address()
    if host is None:
        return {"address": None, "scheme": scheme, "host": None, "port": port}
    shown = "[%s]" % host if ":" in host else host
    return {"address": "%s://%s:%d" % (scheme, shown, port), "scheme": scheme, "host": host, "port": port}


_qr_cache = {}
_qr_lock = threading.Lock()


def qr_svg(address):
    """The QR code of an address as SVG, made once per address."""
    with _qr_lock:
        if address not in _qr_cache:
            _qr_cache.clear()
            _qr_cache[address] = qr.svg(address)
        return _qr_cache[address]


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m mirutube",
        description="Serve folders of videos to the home network. Every option may instead be set "
                    "in the config file, under [server]; one given here wins.")
    parser.add_argument("library", nargs="?",
                        help="a library folder, added to the library folders on the first start; "
                             "later they are changed in settings")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="the config file (default: %(default)s)")
    parser.add_argument("--port", type=int, help="the port (default: %d)" % DEFAULT_PORT)
    parser.add_argument("--host", help="address to listen on (default: every interface)")
    parser.add_argument("--data", help="where positions, settings and certificates are kept "
                                       "(default: %s)" % DEFAULT_DATA)
    parser.add_argument("--cache", help="where what can be made again is kept (default: %s)" % DEFAULT_CACHE)
    parser.add_argument("--cert", help="a TLS certificate (PEM), to serve HTTPS with instead of MiruTube's own")
    parser.add_argument("--key", help="the certificate's private key (PEM)")
    parser.add_argument("--quiet", action="store_true", help="do not log requests")
    args = parser.parse_args(argv)
    try:
        config = read_config(args.config)
    except (ValueError, configparser.Error, OSError) as e:
        parser.error(str(e))
    for name in CONFIG_KEYS:
        if getattr(args, name) is None and name in config:
            setattr(args, name, config[name])
    try:
        args.port = int(args.port) if args.port is not None else DEFAULT_PORT
    except ValueError:
        parser.error("port must be a number: %s" % args.port)
    args.host = args.host or ""
    args.data = os.path.expanduser(args.data or DEFAULT_DATA)
    args.cache = os.path.expanduser(args.cache or DEFAULT_CACHE)
    if args.library is not None and not os.path.isdir(args.library):
        parser.error("not a folder: %s" % args.library)
    if bool(args.cert) != bool(args.key):
        parser.error("HTTPS needs both a certificate and its key")
    if args.cert:
        # paths in the config file are the computer's (server#fields#host_root)
        args.cert, args.key = on_host(args.cert), on_host(args.key)
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            parser.error("%s is not installed" % tool)
    try:
        server = make_server(args.library, args.port, args.host, args.cache,
                             args.quiet, args.cert, args.key, args.data)
    except (ssl.SSLError, OSError) as e:
        parser.error(str(e))
    folders = list(server.handler.library.folders.values())
    if not folders:
        print("MiruTube: no library folder yet — add one in settings, on this computer", flush=True)
    handler = server.handler
    address = address_of("http", handler.server_address_host, handler.port)["address"] or \
        "http://localhost:%d" % handler.port
    print("MiruTube: serving %s at %s/ (and over https on the same port)"
          % (", ".join(folders) or "nothing", address), flush=True)
    threading.Thread(target=_log_encoder, daemon=True).start()
    stop = threading.Event()
    if server.certificate_names is not None:
        threading.Thread(target=watch_address, args=(server, args.data, handler.server_address_host, stop),
                         daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.downloads.close()
        server.devices.close()
        server.server_close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
