"""Everything that brings videos into the library from outside it
(behaviour#uploads): files sent from a page in pieces, torrents through
libtorrent (decisions#libtorrent) and links through yt-dlp.

Each is a job with a destination (destination), written first to
`.mirutube-incoming/<job>` in its library folder — which library#scan skips,
as it skips every name starting with a dot — and each file moved to its place
the moment it is complete, so the first episode is in the catalogue, and
plays, while the others still come (decisions#incoming). Jobs are kept in the
data folder, a torrent's with libtorrent's resume data, so a restart goes on
where it stopped. Nothing is written outside a library folder: every name
from a page, a torrent or a link is cleaned before it is used
(behaviour#library-only).

libtorrent and yt-dlp are the two dependencies beyond the standard library and
ffmpeg, both in the image; without them MiruTube runs, and a torrent or a link
is answered as not supported here.
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
import uuid

try:
    import libtorrent as lt
except ImportError:  # running without it: torrents are not supported here
    lt = None

from mirutube.library import (AUDIO_EXTENSIONS, SUBTITLE_EXTENSIONS, VIDEO_EXTENSIONS, natural_key,
                              order_episodes, text_key)

INCOMING = ".mirutube-incoming"    # in each library folder; scan skips names starting with a dot
SMALL = 5 * 1000 * 1000            # bytes: a torrent's files under this come at once
PIECE_LIMIT = 16 * 1024 * 1024     # bytes: the most one piece of an upload may carry
TORRENT_LIMIT = 10 * 1024 * 1024   # bytes: the largest .torrent taken
INTERRUPTED_AFTER = 30.0           # seconds without a piece before an upload counts as interrupted
ANNOUNCE_EVERY = 1.0               # seconds: jobs are sent to the pages at most this often
SAVE_RESUME_EVERY = 60.0           # seconds between libtorrent's resume data being saved
LISTEN = "0.0.0.0:6881,[::]:6881"  # where libtorrent takes connections from peers
FINISHED = ("seeding", "done", "failed", "cancelled")

# the best a browser plays as it is (library#scan's `direct`): H.264, VP9 or
# AV1 video with AAC or Opus sound, joined in MP4
YTDLP_FORMAT = "bv*[vcodec~='^(avc1|vp0?9|av01)']+ba[acodec~='^(mp4a|opus)']/b[vcodec~='^(avc1|vp0?9|av01)']/b"


class NotSupported(Exception):
    """What MiruTube cannot take, said to the page as it is."""


# ------------------------------------------------------------------- names

_REFUSED = re.compile(r'[\x00-\x1f<>:"|?*]')  # what Windows refuses in a name, and control characters


def clean_path(path):
    """A relative path, `/`-separated, made from what a page, a torrent or a
    site names a file: no `..`, no absolute path, no character a file system
    refuses; None when nothing is left (behaviour#library-only)."""
    parts = []
    for part in re.split(r"[\\/]+", path or ""):
        part = _REFUSED.sub("_", part).strip().rstrip(".")
        if part in ("", ".", ".."):
            continue
        parts.append(part[:200])
    return "/".join(parts) or None


def kind_of(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in SUBTITLE_EXTENSIONS:
        return "subtitle"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    if ext == ".torrent":
        return "torrent"
    return "other"


def _top_folder(paths):
    """The one folder every path lies in, or None."""
    tops = {p.split("/", 1)[0] for p in paths}
    if len(tops) == 1 and all("/" in p for p in paths):
        return tops.pop()
    return None


# -------------------------------------------------------------------- jobs

class Downloads:
    """downloads#fields#jobs and the work behind them. `library` is the
    server's Library, `settings` returns store#fields#shared_settings, and
    `announce` is called — at most once a second — when the jobs changed."""

    def __init__(self, data_dir, library, settings, announce, torrent_settings=None):
        self.torrent_settings = torrent_settings or {}  # what a test changes: where to listen, no DHT
        self.path = os.path.join(data_dir, "downloads.json")
        self.resume_dir = os.path.join(data_dir, "torrents")
        self.library = library
        self.settings = settings
        self.announce = announce
        self._lock = threading.RLock()
        self.jobs = {}       # id -> job, as kept in downloads.json
        self.live = {}       # id -> what is not kept: speed, time left, peers, the process or the torrent
        self._dirty = False
        self._stop = threading.Event()
        self.session = None
        self._handles = {}   # info hash -> job id
        self._load()
        threading.Thread(target=self._tick, daemon=True, name="downloads").start()

    # ---------------------------------------------------------- keeping

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                jobs = json.load(f)
        except (OSError, ValueError):
            jobs = []
        for job in jobs:
            self.jobs[job["id"]] = job
            self.live[job["id"]] = {}
        # what was under way goes on (behaviour#uploads#scenarios#restart)
        for job in list(self.jobs.values()):
            if job["kind"] == "torrent" and job["state"] in ("queued", "downloading", "seeding"):
                try:
                    self._resume_torrent(job)
                except Exception as e:  # noqa: BLE001 — whatever libtorrent raises, the job says it
                    self._fail(job, "it could not be resumed: %s" % e)
            elif job["kind"] == "link" and job["state"] in ("queued", "downloading"):
                self._run_link(job)

    def _save(self):
        with self._lock:
            jobs = sorted(self.jobs.values(), key=lambda j: j["created"])
            data = json.dumps(jobs, ensure_ascii=False, indent=1)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, self.path)

    def _changed(self, save=False):
        with self._lock:
            self._dirty = True
        if save:
            self._save()

    def _tick(self):
        last_resume = time.monotonic()
        while not self._stop.wait(ANNOUNCE_EVERY if self.session is None else 0.5):
            if self.session is not None:
                try:
                    self._torrent_alerts()
                    self._torrent_status()
                    if time.monotonic() - last_resume > SAVE_RESUME_EVERY:
                        last_resume = time.monotonic()
                        self._save_resume_all()
                except Exception as e:  # noqa: BLE001 — the loop must outlive one bad alert
                    print("MiruTube: downloads: %s" % e, flush=True)
            with self._lock:
                dirty, self._dirty = self._dirty, False
                uploading = any(j["kind"] == "upload" and j["state"] == "downloading" for j in self.jobs.values())
            if dirty or uploading:  # an upload turns interrupted with no event of its own
                self.announce()

    def close(self):
        self._stop.set()
        with self._lock:
            procs = [l.get("proc") for l in self.live.values() if l.get("proc")]
        for p in procs:
            p.terminate()
        if self.session is not None:
            self._save_resume_all(wait=True)

    # --------------------------------------------------------- what is shown

    def snapshot(self):
        """downloads#fields#jobs as uploads_card shows them, newest first."""
        now = time.time()
        out = []
        with self._lock:
            for job in sorted(self.jobs.values(), key=lambda j: j["created"], reverse=True):
                live = self.live.get(job["id"], {})
                files = job["files"]
                size = live["size"] if "size" in live else sum(f["size"] for f in files if not f.get("skipped"))
                done = live.get("done")
                if done is None:
                    done = sum(f["size"] if f.get("done") else f.get("received", 0)
                               for f in files if not f.get("skipped"))
                state = job["state"]
                if state in ("seeding", "done"):
                    done = size
                if job["kind"] == "upload" and state == "downloading" and \
                        now - live.get("last_piece", job["created"]) > INTERRUPTED_AFTER:
                    state = "interrupted"
                out.append({
                    "id": job["id"], "kind": job["kind"], "title": job["title"], "into": job.get("into"),
                    "state": state, "reason": job.get("reason"), "device": job.get("device_name"),
                    "size": size, "done": min(done, size) if size else done,
                    "speed": live.get("speed", 0) if state == "downloading" else 0,
                    "eta": live.get("eta"), "peers": live.get("peers"), "current": live.get("current"),
                    "uploaded": live.get("uploaded"), "placed": sum(1 for f in files if f.get("placed")),
                    "files": len(files), "item": live.get("item"),
                })
        return out

    # -------------------------------------------------------- destination

    def _root(self, name):
        """The absolute folder of the library folder named `name`, or None."""
        return self.library.roots.get(name)

    def _series_folder(self, names):
        """downloads#destination: the folder, as a path within the library, of
        the series the library has whose episodes these videos continue — the
        text before their episode number is that of its episodes — or None."""
        if not names:
            return None
        try:
            playlists = self.library.catalogue()["playlists"]
        except Exception:  # noqa: BLE001 — no catalogue, no series to continue
            return None
        new = set(names)
        for p in playlists:
            if not p.get("series"):
                continue
            existing = [v.rsplit("/", 1)[-1] for v in p["videos"]]
            is_series, items = order_episodes(existing + [n for n in names if n not in existing])
            if not is_series:
                continue
            new_keys = {text_key(i["before"] or "") for i in items if i["name"] in new}
            old_keys = {text_key(i["before"] or "") for i in items
                        if i["name"] not in new and i["episode"] is not None}
            numbered = all(i["episode"] is not None for i in items if i["name"] in new)
            if numbered and len(new_keys) == 1 and new_keys == old_keys and "" not in new_keys:
                folders = [v.rsplit("/", 1)[0] for v in p["videos"]]
                return max(set(folders), key=folders.count)
        return None

    def _decide(self, job):
        """Sets where each file of `job` goes, as a path within the library
        (`target`), and the folder shown as where the job goes (`into`). A
        folder keeps its name; a single file lies in the library folder
        itself; episodes of a series the library has go into its folder
        (downloads#functions#destination)."""
        paths = [f["path"] for f in job["files"]]
        videos = [p.rsplit("/", 1)[-1] for p in paths if kind_of(p) == "video"]
        series = self._series_folder(videos)
        top = _top_folder(paths)
        if series is not None:
            for f in job["files"]:
                rel = f["path"].split("/", 1)[1] if top else f["path"]
                f["target"] = series + "/" + rel
            job["into"] = series
        else:
            for f in job["files"]:
                f["target"] = job["folder"] + "/" + f["path"]
            job["into"] = job["folder"] + ("/" + top if top else "")

    def _incoming(self, job):
        root = self._root(job["folder"])
        if root is None:
            raise NotSupported("the library folder %s is no longer there" % job["folder"])
        return os.path.join(root, INCOMING, job["id"])

    def _makedirs(self, job, folder):
        """Makes `folder` and the folders above it that are missing, noting
        each made, so cancelling takes them away again."""
        missing = []
        d = folder
        while not os.path.isdir(d):
            missing.append(d)
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
        for d in reversed(missing):
            os.makedirs(d, exist_ok=True)
            job.setdefault("made", []).append(d)

    @staticmethod
    def _free_name(full, size):
        """Where a file of `size` bytes goes for `full`: there when nothing
        is; None when a file the same size is (it is the same file); else with
        ` (2)`, ` (3)`… before its extension. A name taken is never
        overwritten."""
        if not os.path.exists(full):
            return full
        try:
            if os.path.isfile(full) and os.path.getsize(full) == size:
                return None
        except OSError:
            pass
        stem, ext = os.path.splitext(full)
        n = 2
        while os.path.exists("%s (%d)%s" % (stem, n, ext)):
            n += 1
        return "%s (%d)%s" % (stem, n, ext)

    def _target(self, f):
        full = self.library.resolve(f["target"])
        if full is None:  # cleaned names cannot leave the library; this is a last guard
            raise NotSupported("%s would lie outside the library" % f["path"])
        return full

    def _place(self, job, f, src):
        """Moves a complete file into the library (decisions#incoming)."""
        full = self._target(f)
        self._makedirs(job, os.path.dirname(full))
        dest = self._free_name(full, f["size"])
        if dest is None:
            os.remove(src)
            f["skipped"] = True
        else:
            shutil.move(src, dest)
            f["placed"] = dest
            self.library.refresh(changed=True)  # every page learns of it (library)
        f["done"] = True

    def _finish_if_complete(self, job):
        if all(f.get("done") or f.get("skipped") for f in job["files"]):
            job["state"] = "done"
            shutil.rmtree(self._incoming(job), ignore_errors=True)

    def _fail(self, job, reason):
        with self._lock:
            job["state"] = "failed"
            job["reason"] = reason
        self._changed(save=True)

    def _new_job(self, kind, folder, device, device_name, title):
        if self._root(folder) is None:
            raise NotSupported("no such library folder: %s" % folder)
        job = {"id": uuid.uuid4().hex[:12], "kind": kind, "folder": folder, "device": device,
               "device_name": device_name, "title": title, "state": "queued", "reason": None,
               "created": time.time(), "files": [], "into": folder}
        with self._lock:
            self.jobs[job["id"]] = job
            self.live[job["id"]] = {}
        return job

    def job(self, job_id):
        with self._lock:
            return self.jobs.get(job_id)

    # ------------------------------------------------------------ uploads

    def start_upload(self, folder, files, device=None, device_name=None):
        """downloads#upload: a job for files a page is about to send, each
        {path, size}; the same files sent again to the same folder go on with
        the job already there. Returns the job and how much of each file
        arrived."""
        cleaned = []
        for f in files:
            path, size = clean_path(f.get("path")), f.get("size")
            if path is None or isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise ValueError("each file needs a path and a size")
            if "/" not in path and kind_of(path) not in ("video", "subtitle", "audio"):
                # a .torrent never comes here: the page starts its torrent instead
                raise NotSupported("%s is not supported: only videos, subtitles, sound tracks and folders "
                                   "are taken" % path)
            cleaned.append((path, size))
        if not cleaned:
            raise ValueError("no files")
        with self._lock:
            for job in self.jobs.values():
                if job["kind"] == "upload" and job["folder"] == folder and job["state"] == "downloading" and \
                        [(f["path"], f["size"]) for f in job["files"]] == cleaned:
                    return job, self._received(job)
        top = _top_folder([p for p, _ in cleaned])
        title = top or (cleaned[0][0] + (" and %d more" % (len(cleaned) - 1) if len(cleaned) > 1 else ""))
        job = self._new_job("upload", folder, device, device_name, title)
        job["files"] = [{"path": p, "size": s} for p, s in cleaned]
        self._decide(job)
        os.makedirs(self._incoming(job), exist_ok=True)
        job["state"] = "downloading"
        self.live[job["id"]]["last_piece"] = time.time()
        for i, f in enumerate(job["files"]):
            if f["size"] == 0:  # nothing to send
                open(self._part(job, i), "wb").close()
                self._place(job, f, self._part(job, i))
        self._finish_if_complete(job)
        self._changed(save=True)
        return job, self._received(job)

    def _part(self, job, index):
        return os.path.join(self._incoming(job), "%d.part" % index)

    def _received(self, job):
        out = []
        for i, f in enumerate(job["files"]):
            if f.get("done") or f.get("skipped"):
                out.append(f["size"])
            else:
                try:
                    out.append(os.path.getsize(self._part(job, i)))
                except OSError:
                    out.append(0)
        return out

    def receive(self, job_id, index, offset, data):
        """downloads#upload: one piece of a file at `offset`. Returns (whether
        it was taken, how much of the file is there now): a piece at another
        offset than the end of what arrived is refused with that end, so the
        page goes on from there (decisions#upload-in-pieces)."""
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None or job["kind"] != "upload":
                raise KeyError(job_id)
            if job["state"] not in ("downloading",):
                raise NotSupported("this upload is %s" % job["state"])
            if not 0 <= index < len(job["files"]):
                raise KeyError(index)
            f = job["files"][index]
            if f.get("done") or f.get("skipped"):
                return False, f["size"]
            part = self._part(job, index)
            have = os.path.getsize(part) if os.path.exists(part) else 0
            if offset != have:
                return False, have
            if have + len(data) > f["size"]:
                raise ValueError("more than the file's size")
            with open(part, "ab") as out:
                out.write(data)
            have += len(data)
            f["received"] = have
            live = self.live.setdefault(job_id, {})
            now = time.time()
            live["last_piece"] = now
            live["current"] = f["path"].rsplit("/", 1)[-1]
            samples = [s for s in live.get("samples", []) if now - s[0] < 5] + [(now, len(data))]
            live["samples"] = samples
            span = max(1.0, now - samples[0][0])
            live["speed"] = sum(n for _, n in samples) / span
            if have == f["size"]:
                self._place(job, f, part)
                self._finish_if_complete(job)
                self._changed(save=True)
            else:
                self._changed()
            left = sum(s - r for s, r in zip((x["size"] for x in job["files"]), self._received(job)))
            live["eta"] = left / live["speed"] if live["speed"] else None
            return True, have

    # ----------------------------------------------------------- torrents

    def _session(self):
        if lt is None:
            raise NotSupported("torrents are not supported here: libtorrent is not installed")
        if self.session is None:
            mask = 0
            for name in ("error", "status", "storage", "file_progress"):
                mask |= int(getattr(lt.alert_category, name, 0))
            settings = {"listen_interfaces": LISTEN, "alert_mask": mask, "enable_dht": True, "enable_lsd": True}
            settings.update(self.torrent_settings)
            self.session = lt.session(settings)
        return self.session

    def start_torrent(self, folder, data=None, uri=None, device=None, device_name=None):
        """downloads#torrent: from a `.torrent` file's bytes, a link to one,
        or a `magnet:` link."""
        self._session()
        if uri is not None and not uri.startswith("magnet:"):
            data = fetch_torrent(uri)
        if data is not None:
            try:
                info = lt.torrent_info(lt.bdecode(data))
            except Exception as e:  # noqa: BLE001
                raise NotSupported("this is not a torrent file (%s)" % e) from e
            title = info.name()
        else:
            try:
                params = lt.parse_magnet_uri(uri)
            except Exception as e:  # noqa: BLE001
                raise NotSupported("this magnet link cannot be read (%s)" % e) from e
            title = params.name or "Magnet link"
        job = self._new_job("torrent", folder, device, device_name, title)
        os.makedirs(self.resume_dir, exist_ok=True)
        if data is not None:
            with open(os.path.join(self.resume_dir, job["id"] + ".torrent"), "wb") as f:
                f.write(data)
        else:
            job["magnet"] = uri
        job["state"] = "downloading"
        self._add_torrent(job)
        self._changed(save=True)
        return job

    def _add_torrent(self, job, resume=None):
        session = self._session()
        incoming = self._incoming(job)
        os.makedirs(incoming, exist_ok=True)
        if resume is not None:
            params = lt.read_resume_data(resume)
        elif job.get("magnet"):
            params = lt.parse_magnet_uri(job["magnet"])
        else:
            with open(os.path.join(self.resume_dir, job["id"] + ".torrent"), "rb") as f:
                params = lt.add_torrent_params()
                params.ti = lt.torrent_info(lt.bdecode(f.read()))
        params.save_path = incoming
        handle = session.add_torrent(params)
        with self._lock:
            self.live.setdefault(job["id"], {})["handle"] = handle
            self._handles[_key(handle)] = job["id"]
        if handle.status().has_metadata:
            self._metadata(job, handle)

    def _resume_torrent(self, job):
        path = os.path.join(self.resume_dir, job["id"] + ".resume")
        resume = None
        if os.path.exists(path):
            with open(path, "rb") as f:
                resume = f.read()
        self._add_torrent(job, resume)

    def _metadata(self, job, handle):
        """The torrent's files are known: where each goes, and in what order
        they come — subtitles, sound and small files at once, then the
        episodes in their order, the first from its start
        (downloads#functions#torrent)."""
        info = handle.torrent_file()
        if info is None:
            return
        fs = info.files()
        first_time = not job["files"]
        if first_time:
            pad = int(getattr(lt.file_storage, "flag_pad_file", 0))
            files = []
            for i in range(fs.num_files()):
                if pad and fs.file_flags(i) & pad:
                    continue
                path = clean_path(fs.file_path(i).replace(os.sep, "/"))
                if path is None:
                    continue
                files.append({"path": path, "size": fs.file_size(i), "index": i})
            job["files"] = files
            job["title"] = info.name()
            self._decide(job)
        priorities = [0] * fs.num_files()
        videos = [f for f in job["files"] if kind_of(f["path"]) == "video"]
        is_series, items = order_episodes([f["path"].rsplit("/", 1)[-1] for f in videos])
        order = {it["name"]: n for n, it in enumerate(items)}
        videos.sort(key=lambda f: (order.get(f["path"].rsplit("/", 1)[-1], len(order)), natural_key(f["path"])))
        for f in job["files"]:
            if first_time:
                full = self._target(f)
                if self._free_name(full, f["size"]) is None:
                    f["skipped"] = True  # the same file is there already
            if f.get("skipped") or f.get("done"):
                continue
            if kind_of(f["path"]) != "video" or f["size"] < SMALL:
                priorities[f["index"]] = 7
        for n, f in enumerate(videos):
            if not (f.get("skipped") or f.get("done")):
                priorities[f["index"]] = max(1, 7 - n)
        handle.prioritize_files(priorities)
        flags = getattr(lt, "torrent_flags", None)
        if flags is not None:
            handle.set_flags(flags.sequential_download)
        else:
            handle.set_sequential_download(True)
        self._changed(save=True)

    def _job_of(self, handle):
        with self._lock:
            job_id = self._handles.get(_key(handle))
            return self.jobs.get(job_id) if job_id else None

    def _torrent_alerts(self):
        for a in self.session.pop_alerts():
            name = type(a).__name__
            handle = getattr(a, "handle", None)
            job = self._job_of(handle) if handle is not None else None
            if job is None:
                continue
            if name == "metadata_received_alert":
                self._metadata(job, handle)
            elif name == "file_completed_alert":
                self._torrent_file_complete(job, handle, a.index)
            elif name == "file_renamed_alert":
                f = next((x for x in job["files"] if x.get("index") == a.index), None)
                if f is not None:
                    new_name = getattr(a, "new_name", None)
                    if callable(new_name):  # a method in some versions of the binding, a field in others
                        new_name = new_name()
                    f["placed"] = new_name or f.get("moving")
                    f["done"] = True
                    f.pop("moving", None)
                    self._changed(save=True)
                    self.library.refresh(changed=True)  # every page learns of it (library)
            elif name == "file_rename_failed_alert":
                self._fail(job, "a file could not be moved into the library: %s" % a.message())
            elif name == "save_resume_data_alert":
                try:
                    buf = lt.write_resume_data_buf(a.params)
                    with open(os.path.join(self.resume_dir, job["id"] + ".resume"), "wb") as f:
                        f.write(buf)
                except Exception as e:  # noqa: BLE001
                    print("MiruTube: resume data not saved: %s" % e, flush=True)
            elif name in ("torrent_error_alert", "file_error_alert"):
                self._fail(job, a.message())

    def _torrent_file_complete(self, job, handle, index):
        """A file has all its pieces: it moves into the library, and seeding
        goes on from there (decisions#incoming)."""
        f = next((x for x in job["files"] if x.get("index") == index), None)
        if f is None or f.get("skipped") or f.get("done") or f.get("moving"):
            return
        full = self._target(f)
        self._makedirs(job, os.path.dirname(full))
        dest = self._free_name(full, f["size"]) or "%s (2)%s" % os.path.splitext(full)
        f["moving"] = dest
        handle.rename_file(index, dest)
        self._changed(save=True)

    def _torrent_status(self):
        with self._lock:
            torrents = [(j, self.live.get(j["id"], {}).get("handle")) for j in self.jobs.values()
                        if j["kind"] == "torrent" and j["state"] in ("downloading", "seeding")]
        seed = bool(self.settings().get("seed_torrents", True))
        for job, handle in torrents:
            if handle is None or not handle.is_valid():
                continue
            s = handle.status()
            live = self.live.setdefault(job["id"], {})
            live.update(size=s.total_wanted, done=s.total_wanted_done, speed=s.download_rate,
                        peers=s.num_peers, uploaded=s.all_time_upload,
                        eta=(s.total_wanted - s.total_wanted_done) / s.download_rate if s.download_rate else None)
            if job["files"]:
                progress = handle.file_progress()
                pending = [f for f in job["files"] if not (f.get("done") or f.get("skipped"))]
                coming = [f for f in pending if progress[f["index"]] < f["size"]]
                live["current"] = coming[0]["path"].rsplit("/", 1)[-1] if coming else None
                complete = not pending
            else:
                complete = False
            if complete and job["state"] == "downloading":
                job["state"] = "seeding" if seed else "done"
                _prune(self._incoming(job))  # the torrent's own folders, left empty by the moves
                handle.save_resume_data(_resume_flags())
                if not seed:
                    self._stop_torrent(job)
                self._changed(save=True)
            elif job["state"] == "seeding" and not seed:
                self._stop_seeding(job)
            else:
                self._changed()

    def _save_resume_all(self, wait=False):
        with self._lock:
            handles = [l["handle"] for l in self.live.values() if l.get("handle") is not None]
        for h in handles:
            if h.is_valid():
                h.save_resume_data(_resume_flags())
        if wait:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and handles:
                if self.session.wait_for_alert(500) is None:
                    continue
                self._torrent_alerts()

    def _stop_torrent(self, job, delete=False):
        live = self.live.get(job["id"], {})
        handle = live.pop("handle", None)
        if handle is not None and handle.is_valid():
            with self._lock:
                self._handles.pop(_key(handle), None)
            if delete:
                self.session.remove_torrent(handle, _delete_files())
            else:
                self.session.remove_torrent(handle)

    def _stop_seeding(self, job):
        self._stop_torrent(job)
        job["state"] = "done"
        for name in (".resume", ".torrent"):
            try:
                os.remove(os.path.join(self.resume_dir, job["id"] + name))
            except OSError:
                pass
        shutil.rmtree(self._incoming(job), ignore_errors=True)
        self._changed(save=True)

    def stop_seeding(self, job_id):
        """`Stop seeding`: the torrent leaves the session, its files stay."""
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job["state"] != "seeding":
                raise NotSupported("it is not seeding")
            self._stop_seeding(job)

    # -------------------------------------------------------------- links

    def start_link(self, folder, link, device=None, device_name=None):
        """downloads#link: a `magnet:` link or one to a `.torrent` is a
        torrent; any other goes to yt-dlp."""
        link = (link or "").strip()
        if not link:
            raise ValueError("no link")
        if link.startswith("magnet:"):
            return self.start_torrent(folder, uri=link, device=device, device_name=device_name)
        if not re.match(r"^https?://", link, re.I):
            raise NotSupported("this link is not supported: it is neither a web address nor a magnet link")
        if re.search(r"\.torrent(?:$|[?#])", link, re.I):
            return self.start_torrent(folder, uri=link, device=device, device_name=device_name)
        if shutil.which("yt-dlp") is None:
            raise NotSupported("links are not supported here: yt-dlp is not installed")
        job = self._new_job("link", folder, device, device_name, link)
        job["link"] = link
        self._changed(save=True)
        self._run_link(job)
        return job

    def _run_link(self, job):
        threading.Thread(target=self._link_worker, args=(job,), daemon=True, name="yt-dlp").start()

    def _link_worker(self, job):
        live = self.live.setdefault(job["id"], {})
        try:
            incoming = self._incoming(job)
            os.makedirs(incoming, exist_ok=True)
            if "playlist" not in job:
                probe = subprocess.run(["yt-dlp", "--flat-playlist", "--dump-single-json", "--no-warnings",
                                        job["link"]], capture_output=True, text=True, timeout=120)
                if probe.returncode != 0:
                    return self._fail(job, "this link is not supported: %s" % _ytdlp_error(probe.stderr))
                info = json.loads(probe.stdout)
                playlist = info.get("_type") == "playlist"
                job["title"] = info.get("title") or job["link"]
                job["playlist"] = clean_path(job["title"]) if playlist else None
                job["count"] = len(info.get("entries") or []) if playlist else 1
                job["into"] = job["folder"] + ("/" + job["playlist"] if job["playlist"] else "")
            job["state"] = "downloading"
            self._changed(save=True)
            cmd = ["yt-dlp", "--newline", "--no-colors", "--progress", "--ignore-errors",
                   "-P", incoming, "-o", "%(title).150B [%(id)s].%(ext)s",
                   "--download-archive", os.path.join(incoming, "archive.txt"),
                   "-f", YTDLP_FORMAT, "--merge-output-format", "mp4",
                   "--write-subs", "--sub-langs", "all,-live_chat",
                   "--progress-template",
                   "download:MIRU-PROGRESS %(progress.downloaded_bytes)s %(progress.total_bytes)s "
                   "%(progress.total_bytes_estimate)s %(progress.speed)s %(progress.eta)s",
                   "--print", "before_dl:MIRU-ITEM %(playlist_index|1)s %(title)s",
                   "--print", "after_move:MIRU-FILE %(filepath)s",
                   job["link"]]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    errors="replace")
            live["proc"] = proc
            errors = []
            threading.Thread(target=lambda: errors.extend(proc.stderr), daemon=True).start()
            for line in proc.stdout:
                if job["state"] == "cancelled":
                    break
                self._link_line(job, live, line.rstrip("\n"))
            proc.wait()
            live.pop("proc", None)
            if job["state"] == "cancelled":
                return
            placed = [f for f in job["files"] if f.get("placed")]
            if proc.returncode != 0 and not placed:
                return self._fail(job, "this link is not supported: %s" % _ytdlp_error("".join(errors)))
            job["state"] = "done"
            shutil.rmtree(incoming, ignore_errors=True)
            self._changed(save=True)
        except NotSupported as e:
            self._fail(job, str(e))
        except Exception as e:  # noqa: BLE001 — the job says what went wrong
            self._fail(job, str(e))

    def _link_line(self, job, live, line):
        if line.startswith("MIRU-PROGRESS "):
            parts = line.split()[1:]

            def num(v):
                try:
                    return float(v)
                except ValueError:
                    return None
            done, total, estimate, speed, eta = (num(p) for p in (parts + ["NA"] * 5)[:5])
            total = total or estimate or 0
            n = job.get("count") or 1
            item = live.get("item", 1)
            # each video an equal share of the whole, what is known of it
            live["size"] = 100.0
            live["done"] = 100.0 * (min(item, n) - 1 + (done / total if total and done else 0)) / n
            live["speed"] = speed or 0
            live["eta"] = eta
            self._changed()
        elif line.startswith("MIRU-ITEM "):
            index, _, title = line[len("MIRU-ITEM "):].partition(" ")
            try:
                live["item"] = int(index)
            except ValueError:
                live["item"] = 1
            live["current"] = title
            self._changed()
        elif line.startswith("MIRU-FILE "):
            self._link_place(job, line[len("MIRU-FILE "):])

    def _link_place(self, job, video):
        """Moves a video yt-dlp has finished into the library, with the
        subtitles saved beside it under the same name."""
        folder, name = os.path.split(video)
        stem = os.path.splitext(name)[0]
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            return
        for other in names:
            full = os.path.join(folder, other)
            if other != name and not (other.startswith(stem + ".") and kind_of(other) == "subtitle"):
                continue
            if not os.path.isfile(full):
                continue
            path = clean_path(other)
            f = {"path": path, "size": os.path.getsize(full)}
            if job.get("playlist"):
                f["target"] = job["folder"] + "/" + job["playlist"] + "/" + path
            else:
                # a single video may be the next episode of a series the library has
                series = self._series_folder([clean_path(name)]) if kind_of(name) == "video" else None
                f["target"] = (series or job["folder"]) + "/" + path
                job["into"] = series or job["folder"]
            with self._lock:
                job["files"].append(f)
                self._place(job, f, full)
        self._changed(save=True)

    # ----------------------------------------------------------- cancelling

    def cancel(self, job_id):
        """downloads#cancel: an unfinished job stops, and every file it wrote
        is deleted — those still incoming and those already in the library —
        and none that was there before it."""
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job["state"] in ("seeding", "done", "cancelled"):
                raise NotSupported("a finished download is not cancelled: its files stay")
            job["state"] = "cancelled"
            live = self.live.get(job_id, {})
            proc = live.get("proc")
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if job["kind"] == "torrent" and self.session is not None:
            self._stop_torrent(job, delete=True)
        for f in job["files"]:
            for path in (f.get("placed"), f.get("moving")):
                if path:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
        try:
            shutil.rmtree(self._incoming(job), ignore_errors=True)
        except NotSupported:
            pass
        for d in sorted(job.get("made", []), key=len, reverse=True):
            try:
                os.rmdir(d)  # only when empty: a folder made by the job that holds nothing else
            except OSError:
                pass
        for name in (".resume", ".torrent"):
            try:
                os.remove(os.path.join(self.resume_dir, job_id + name))
            except OSError:
                pass
        self._changed(save=True)

    def dismiss(self, job_id):
        """`Remove from list` on a finished job: it leaves the list, its files stay."""
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job["state"] not in ("done", "failed", "cancelled"):
                raise NotSupported("only a finished download leaves the list")
            del self.jobs[job_id]
            self.live.pop(job_id, None)
        self._changed(save=True)


def _prune(folder):
    """Removes the empty folders under `folder`, and it when it is empty."""
    for d, _, _ in sorted(os.walk(folder), key=lambda w: len(w[0]), reverse=True):
        try:
            os.rmdir(d)
        except OSError:
            pass


def _key(handle):
    """What tells one torrent from another, across libtorrent's versions."""
    try:
        return str(handle.info_hashes().get_best())
    except (AttributeError, RuntimeError):
        return str(handle.info_hash())


def _resume_flags():
    flags = getattr(lt, "save_resume_flags_t", None)
    return getattr(flags, "save_info_dict", 0) if flags is not None else 0


def _delete_files():
    options = getattr(lt, "options_t", None)
    return getattr(options, "delete_files", None) or getattr(lt.session, "delete_files", 1)


def _ytdlp_error(stderr):
    lines = [l.strip() for l in (stderr or "").splitlines() if l.strip()]
    errors = [l[len("ERROR:"):].strip() for l in lines if l.startswith("ERROR:")]
    return (errors or lines or ["yt-dlp gave no reason"])[-1]


def fetch_torrent(url):
    """A `.torrent` a link names, at most TORRENT_LIMIT bytes."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "MiruTube"}),
                                    timeout=30) as r:
            data = r.read(TORRENT_LIMIT + 1)
    except OSError as e:
        raise NotSupported("the torrent could not be fetched: %s" % e) from e
    if len(data) > TORRENT_LIMIT:
        raise NotSupported("this is too large to be a torrent file")
    return data
