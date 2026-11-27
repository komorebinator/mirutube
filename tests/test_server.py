"""Against a running server on a test library: a Range request returns exactly
the bytes asked for, a stream video's plan and a piece from its middle are
served, and so is a direct video's plan when another sound track is chosen,
an id not in the catalogue and a path outside the library are refused, ids are
short and stay the same, the page is served at the address of every view, a
position recorded is returned to another client, a file copied in appears
after a rescan made in the background and announced to every page, a library
made again serves the catalogue the last one saved at once, the continue list has one entry per playlist at the video to
go on with, two event streams see each other appear, a report from one reach
the other, a command reach only the device it names, and a closed one leave
the list, and HTTPS and plain HTTP answer on one port (behaviour#library-only,
behaviour#resume, behaviour#new-files, behaviour#library-at-once, behaviour#audio-tracks,
behaviour#devices, behaviour#short-links, behaviour#secure-at-home)."""

import ctypes
import ctypes.util
import http.client
import json
import os
import queue
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import quote
from xml.etree import ElementTree

from mirutube import qr
from mirutube.library import Library, RESCAN_AFTER, short_ids
from mirutube.server import (COMMANDS_KEPT, DEVICE_GRACE, Devices, ViewerSlots, _move_old_store, continue_list,
                             is_local_request, read_config)
from mirutube.store import Store
from tests import fmp4
from tests.test_media import beeps_source, ffmpeg, ffmpeg_processes, make_dub, make_h264_eac3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Paths within the library begin with the name of the library folder, `library`
# (library#scan); on disk each lies at the same path under the test's folder.
EPISODE = "library/Show/Show - 01.mp4"
DURATION = 24 * 60.0  # a 24-minute episode (behaviour#resume#scenarios#credits)
SHORT = "library/Short clip.mp4"  # shorter than ten minutes
SHORT_DURATION = 300.0
STREAM = "library/Remux.mkv"  # H.264 with E-AC3 sound in MKV: converted as it plays
DUBBED = "library/Dubbed.mp4"  # played as it is, with a second sound track of its own
DUB = "library/Dubbed.STEPonee.mka"  # and a dub beside it
RECORDINGS = ["library/Recordings/rec %d.mp4" % n for n in (1, 2)]
OLD = "library/Old.avi"  # MPEG-4 Part 2: converted, not copied
SERIES = ["library/Series/Series - %02d.mp4" % n for n in range(1, 7)]  # behaviour#resume#scenarios#one-card


def I(path):
    """The short id the catalogue gives a video or playlist path
    (decisions#short-ids); no two paths of the test library share one."""
    return short_ids([path])[path]


def folders_of(settings):
    """The library folders of a settings answer without the free space, which
    changes as anything on the disk does."""
    return [{k: v for k, v in f.items() if k != "free"} for f in settings["library_folders"]]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="mirutube-server-")
        cls.lib = os.path.join(cls.tmp, "library")
        os.makedirs(os.path.join(cls.lib, "Show"))
        cls.video = os.path.join(cls.tmp, EPISODE)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi",
                        "-i", "color=c=blue:size=32x32:rate=1:duration=%d" % DURATION,
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", cls.video],
                       check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi",
                        "-i", "color=c=red:size=32x32:rate=1:duration=%d" % SHORT_DURATION,
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                        os.path.join(cls.tmp, SHORT)],
                       check=True)
        make_h264_eac3(os.path.join(cls.tmp, STREAM), 35)
        ffmpeg("-f", "lavfi", "-i", "testsrc2=size=64x36:rate=25:duration=25",
               "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=25",
               "-f", "lavfi", "-i", beeps_source(25),
               # MP4 names a track by its handler
               "-map", "0", "-map", "1", "-map", "2", "-metadata:s:a:1", "language=rus",
               "-metadata:s:a:1", "handler_name=Dub [DEEP]", "-c:v", "libx264", "-preset", "ultrafast",
               "-g", "50", "-pix_fmt", "yuv420p", "-c:a", "aac", os.path.join(cls.tmp, DUBBED))
        make_dub(os.path.join(cls.tmp, DUB), 25)
        os.makedirs(os.path.join(cls.lib, "Series"))
        for episode in SERIES:
            shutil.copy(cls.video, os.path.join(cls.tmp, episode))
        # a folder to remove and bring back (behaviour#library-control)
        os.makedirs(os.path.join(cls.lib, "Recordings"))
        for recording in RECORDINGS:
            shutil.copy(cls.video, os.path.join(cls.tmp, recording))
        # converted as it plays: MPEG-4 Part 2, which no browser decodes
        ffmpeg("-f", "lavfi", "-i", "testsrc=size=64x36:rate=10:duration=3",
               "-c:v", "mpeg4", os.path.join(cls.tmp, OLD))
        # another folder, to add to the library folders
        os.makedirs(os.path.join(cls.tmp, "more"))
        shutil.copy(cls.video, os.path.join(cls.tmp, "more", "Extra clip.mp4"))
        with open(os.path.join(cls.tmp, "secret.txt"), "w") as f:
            f.write("not for the network")
        cls.port = free_port()
        cls.proc = subprocess.Popen(
            [sys.executable, "-m", "mirutube", cls.lib, "--port", str(cls.port),
             "--host", "127.0.0.1", "--cache", os.path.join(cls.tmp, "cache"),
             "--data", os.path.join(cls.tmp, "data"), "--quiet"],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 20
        while True:
            try:
                status, _, _ = cls.request("GET", "/api/catalogue")
                if status == 200:
                    break
            except OSError:
                pass
            if cls.proc.poll() is not None or time.monotonic() > deadline:
                cls.tearDownClass()
                raise RuntimeError("the server did not start")
            time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        if cls.proc.poll() is None:
            cls.proc.terminate()
            try:
                cls.proc.wait(10)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
                cls.proc.wait()
        cls.proc.stderr.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def request(cls, method, path, body=None, headers=None, source=None):
        """One request on a fresh connection — each call is another client;
        `source` is the address it comes from, by default the one it goes to."""
        conn = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=30,
                                          source_address=(source, 0) if source else None)
        try:
            data = json.dumps(body).encode() if body is not None else None
            hdrs = dict(headers or {})
            if data is not None:
                hdrs["Content-Type"] = "application/json"
            conn.request(method, path, body=data, headers=hdrs)
            res = conn.getresponse()
            return res.status, dict(res.getheaders()), res.read()
        finally:
            conn.close()

    def get_json(self, path):
        status, _, body = self.request("GET", path)
        self.assertEqual(status, 200, body)
        return json.loads(body)

    @classmethod
    def put(cls, path, data):
        """A PUT of raw bytes, as a page sends a piece of a file: (status, JSON)."""
        conn = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=30)
        try:
            conn.request("PUT", path, body=data, headers={"Content-Type": "application/octet-stream"})
            res = conn.getresponse()
            body = res.read()
            try:
                return res.status, json.loads(body)
            except ValueError:
                return res.status, body
        finally:
            conn.close()

    def job_state(self, job):
        return next(j for j in self.get_json("/api/downloads")["jobs"] if j["id"] == job)

    # ---------------------------------------------------------- uploads

    def test_upload_arrives_in_pieces(self):
        # behaviour#uploads#scenarios#folder, decisions#upload-in-pieces
        with open(os.path.join(self.tmp, SHORT), "rb") as f:
            video = f.read()
        subs = "1\n00:00:01,000 --> 00:00:02,000\nHello\n".encode()
        folder = os.path.join(self.lib, "Trip 2025")
        self.addCleanup(shutil.rmtree, folder, True)
        files = [{"path": "Trip 2025/clip.mp4", "size": len(video)}, {"path": "Trip 2025/clip.srt", "size": len(subs)}]
        status, started = self.post("/api/downloads/upload", {"files": files})
        self.assertEqual(status, 200, started)
        job = started["job"]
        self.addCleanup(self.post, "/api/downloads/dismiss", {"job": job})
        self.assertEqual(started["received"], [0, 0])
        half = len(video) // 2
        self.assertEqual(self.put("/api/downloads/%s/0?offset=0" % job, video[:half]), (200, {"received": half}))
        # not in the library while it is incoming (decisions#incoming)
        self.post("/api/rescan", {})
        self.assertNotIn(I("library/Trip 2025/clip.mp4"), self.get_json("/api/catalogue")["videos"])
        # the connection broke and the piece is sent again: the page is told where the file ends
        self.assertEqual(self.put("/api/downloads/%s/0?offset=0" % job, video[:half]), (409, {"received": half}))
        # the same files sent again from a page go on with the same job
        status, again = self.post("/api/downloads/upload", {"files": files})
        self.assertEqual((status, again), (200, {"job": job, "received": [half, 0]}))
        self.assertEqual(self.put("/api/downloads/%s/0?offset=%d" % (job, half), video[half:])[0], 200)
        self.assertEqual(self.put("/api/downloads/%s/1?offset=0" % job, subs)[0], 200)
        self.post("/api/rescan", {})
        cat = self.get_json("/api/catalogue")
        vid = I("library/Trip 2025/clip.mp4")
        self.assertIn(vid, cat["videos"])
        self.assertEqual(len(cat["videos"][vid]["subtitles"]), 1)
        with open(os.path.join(folder, "clip.mp4"), "rb") as f:
            self.assertEqual(f.read(), video)
        state = self.job_state(job)
        self.assertEqual((state["state"], state["done"], state["size"]), ("done", len(video) + len(subs),
                                                                         len(video) + len(subs)))
        self.assertFalse(os.path.exists(os.path.join(self.lib, ".mirutube-incoming", job)))
        # a piece for a job there is not
        self.assertEqual(self.put("/api/downloads/%s/0?offset=0" % ("0" * 12), b"x")[0], 404)

    def test_upload_names_stay_inside_the_library(self):
        # behaviour#library-only#scenarios#upload-dot-dot
        status, started = self.post("/api/downloads/upload", {"files": [{"path": "../../evil.mp4", "size": 3}]})
        self.assertEqual(status, 200, started)
        self.addCleanup(self.post, "/api/downloads/dismiss", {"job": started["job"]})
        self.addCleanup(lambda: os.path.exists(os.path.join(self.lib, "evil.mp4"))
                        and os.remove(os.path.join(self.lib, "evil.mp4")))
        self.assertEqual(self.put("/api/downloads/%s/0?offset=0" % started["job"], b"abc")[0], 200)
        self.assertTrue(os.path.isfile(os.path.join(self.lib, "evil.mp4")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "evil.mp4")))
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.tmp), "evil.mp4")))
        # a loose file that is not a video, subtitles or sound is not taken
        status, body = self.post("/api/downloads/upload", {"files": [{"path": "notes.pdf", "size": 3}]})
        self.assertEqual(status, 422)
        self.assertIn(b"not supported", body if isinstance(body, bytes) else str(body).encode())

    def test_next_episode_joins_its_series(self):
        # behaviour#uploads#scenarios#next-episode
        target = os.path.join(self.lib, "Series", "Series - 07.mp4")
        self.addCleanup(lambda: os.path.exists(target) and os.remove(target))
        status, started = self.post("/api/downloads/upload", {"files": [{"path": "Series - 07.mp4", "size": 4}]})
        self.assertEqual(status, 200, started)
        self.addCleanup(self.post, "/api/downloads/dismiss", {"job": started["job"]})
        self.assertEqual(self.job_state(started["job"])["into"], "library/Series")
        self.assertEqual(self.put("/api/downloads/%s/0?offset=0" % started["job"], b"abcd")[0], 200)
        self.assertTrue(os.path.isfile(target))

    def test_cancel_takes_back_what_it_brought(self):
        # behaviour#uploads#scenarios#cancel
        before = os.path.join(self.lib, "Show", "Show - 01.mp4")
        files = [{"path": "Show/extra.mp4", "size": 3}, {"path": "Fresh/a.mp4", "size": 3},
                 {"path": "Fresh/b.mp4", "size": 6}]
        status, started = self.post("/api/downloads/upload", {"files": files})
        self.assertEqual(status, 200, started)
        job = started["job"]
        for n, data in ((0, b"abc"), (1, b"abc"), (2, b"abc")):
            self.assertEqual(self.put("/api/downloads/%s/%d?offset=0" % (job, n), data)[0], 200)
        self.assertTrue(os.path.isfile(os.path.join(self.lib, "Show", "extra.mp4")))
        self.assertTrue(os.path.isfile(os.path.join(self.lib, "Fresh", "a.mp4")))
        self.assertEqual(self.post("/api/downloads/cancel", {"job": job})[0], 200)
        self.assertFalse(os.path.exists(os.path.join(self.lib, "Show", "extra.mp4")))
        self.assertFalse(os.path.exists(os.path.join(self.lib, "Fresh")))
        self.assertTrue(os.path.isfile(before))  # what was there before stays
        self.assertFalse(os.path.exists(os.path.join(self.lib, ".mirutube-incoming", job)))
        self.assertEqual(self.job_state(job)["state"], "cancelled")
        # a finished one is not cancelled, and leaves the list by its ×
        self.assertEqual(self.post("/api/downloads/cancel", {"job": job})[0], 422)
        self.assertEqual(self.post("/api/downloads/dismiss", {"job": job})[0], 200)
        self.assertNotIn(job, [j["id"] for j in self.get_json("/api/downloads")["jobs"]])

    # ------------------------------------------------------------ Range

    def test_range_returns_exactly_the_bytes_asked_for(self):
        with open(self.video, "rb") as f:
            content = f.read()
        url = "/video/" + I(EPISODE)
        status, headers, body = self.request("GET", url, headers={"Range": "bytes=100-199"})
        self.assertEqual(status, 206)
        self.assertEqual(body, content[100:200])
        self.assertEqual(headers["Content-Range"], "bytes 100-199/%d" % len(content))
        self.assertEqual(headers["Content-Length"], "100")

        status, headers, body = self.request("GET", url, headers={"Range": "bytes=%d-" % (len(content) - 50)})
        self.assertEqual(status, 206)
        self.assertEqual(body, content[-50:])

        status, _, body = self.request("GET", url, headers={"Range": "bytes=-30"})
        self.assertEqual((status, body), (206, content[-30:]))

        status, headers, _ = self.request("GET", url, headers={"Range": "bytes=%d-" % (len(content) + 10)})
        self.assertEqual(status, 416)
        self.assertEqual(headers["Content-Range"], "bytes */%d" % len(content))

        status, headers, body = self.request("GET", url)
        self.assertEqual(status, 200)
        self.assertEqual(body, content)
        self.assertEqual(headers["Accept-Ranges"], "bytes")

    # ----------------------------------------------------- library-only

    def test_paths_outside_the_library_are_refused(self):
        for path in ["/video/../../etc/passwd", "/video/..%2F..%2Fetc%2Fpasswd",
                     "/video/%2Fetc%2Fpasswd", "/video/../secret.txt", "/thumb/../../etc/passwd",
                     "/subtitle/../../etc/passwd?track=x0", "/video/Show/../../secret.txt",
                     "/stream/../../etc/passwd", "/stream/../secret.txt?piece=0",
                     "/video/library/../secret.txt", "/video/library/..%2Fsecret.txt",
                     "/video/library/Show/../../secret.txt", "/thumb/library/../../etc/passwd",
                     "/api/details?video=library/../secret.txt", "/video/library%2F..%2Fsecret.txt",
                     "/static/../../../../etc/passwd"]:
            status, _, body = self.request("GET", path)
            self.assertIn(status, (403, 404), path)
            self.assertNotIn(b"root:", body, path)
            self.assertNotIn(b"not for the network", body, path)

    def test_dot_dot_is_refused(self):
        # behaviour#library-only#scenarios#dot-dot: a path is no id
        status, _, body = self.request("GET", "/video/../../etc/passwd")
        self.assertEqual(status, 404)
        self.assertNotIn(b"root:", body)

    def test_symlink_out_of_the_library_is_refused(self):
        link = os.path.join(self.lib, "escape.mp4")
        os.symlink(os.path.join(self.tmp, "secret.txt"), link)
        self.addCleanup(os.unlink, link)
        self.request("POST", "/api/rescan", {})
        for url in ("/video/library/escape.mp4", "/video/" + I("library/escape.mp4"),
                    "/thumb/" + I("library/escape.mp4")):
            status, _, body = self.request("GET", url)
            self.assertIn(status, (403, 404), url)
        self.assertNotIn(b"not for the network", body)

    # ------------------------------------------------------------ stream

    def test_stream_plan_and_a_piece_from_its_middle(self):
        cat = self.get_json("/api/catalogue")
        self.assertEqual(cat["videos"][I(STREAM)]["delivery"], "stream")
        self.assertEqual(cat["videos"][I(EPISODE)]["delivery"], "direct")
        plan = self.get_json("/stream/" + I(STREAM))
        self.assertEqual(set(plan), {"duration", "offset", "starts", "mime"})
        self.assertGreaterEqual(len(plan["starts"]), 3)
        self.assertAlmostEqual(plan["duration"] - plan["offset"], 35, delta=0.2)
        self.assertTrue(plan["mime"].startswith('video/mp4; codecs="avc1.'), plan["mime"])
        middle = len(plan["starts"]) // 2
        status, headers, body = self.request("GET", "/stream/%s?piece=%d" % (I(STREAM), middle))
        self.assertEqual(status, 200, body[:200])
        self.assertEqual(headers["Content-Type"], "video/mp4")
        self.assertEqual(int(headers["Content-Length"]), len(body))
        self.assertEqual(fmp4.top_level(body)[:3], ["ftyp", "moov", "moof"])
        video = next(t for t in fmp4.tracks(body).values() if t["kind"] == "vide")
        first = min(s[1] for s in video["samples"])
        self.assertAlmostEqual(first, plan["starts"][middle], delta=0.001)

    def test_stream_pieces_that_do_not_exist(self):
        status, _, _ = self.request("GET", "/stream/%s?piece=99" % I(STREAM))
        self.assertEqual(status, 404)
        status, _, _ = self.request("GET", "/stream/%s?piece=x" % I(STREAM))
        self.assertEqual(status, 400)
        # a video the browser plays as it is has no plan
        status, _, _ = self.request("GET", "/stream/" + I(EPISODE))
        self.assertEqual(status, 404)

    def test_piece_nobody_waits_for_is_stopped(self):
        # behaviour of server#serve_stream: the page gave up on the piece
        plan = self.get_json("/stream/" + I(STREAM))
        for _ in range(3):
            with socket.create_connection(("127.0.0.1", self.port)) as s:
                s.sendall(("GET /stream/%s?piece=%d HTTP/1.1\r\nHost: x\r\n\r\n"
                           % (I(STREAM), len(plan["starts"]) - 1)).encode())
        deadline = time.monotonic() + 10
        while ffmpeg_processes(self.lib) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertEqual(ffmpeg_processes(self.lib), [])
        # and its slot is free again
        status, _, body = self.request("GET", "/stream/%s?piece=0" % I(STREAM))
        self.assertEqual(status, 200)
        self.assertEqual(fmp4.top_level(body)[:2], ["ftyp", "moov"])

    # ------------------------------------------------------- sound tracks

    def test_sound_tracks_are_listed(self):
        video = self.get_json("/api/catalogue")["videos"][I(DUBBED)]
        self.assertEqual(video["delivery"], "direct")
        self.assertEqual([(t["kind"], t["label"], t["language"], t["default"]) for t in video["audio"]],
                         [("embedded", "Track 1", None, True),
                          ("embedded", "Russian — Dub [DEEP]", "ru", False),
                          ("external", "STEPonee", None, False)])
        self.assertNotIn("path", video["audio"][2])  # the server's business, not the page's
        self.assertEqual(len(self.get_json("/api/catalogue")["videos"][I(STREAM)]["audio"]), 1)

    def test_direct_video_with_another_sound_track_is_streamed(self):
        audio = self.get_json("/api/catalogue")["videos"][I(DUBBED)]["audio"]
        url = "/stream/" + I(DUBBED)
        # with its default track it is played as it is
        self.assertEqual(self.request("GET", url)[0], 404)
        self.assertEqual(self.request("GET", url + "?audio=" + audio[0]["id"])[0], 404)
        for track in audio[1:]:
            plan = self.get_json(url + "?audio=" + track["id"])
            self.assertEqual(set(plan), {"duration", "offset", "starts", "mime"})
            self.assertIn("mp4a.40.2", plan["mime"])
            self.assertGreaterEqual(len(plan["starts"]), 2)
            middle = len(plan["starts"]) // 2
            status, headers, body = self.request(
                "GET", "%s?piece=%d&audio=%s" % (url, middle, track["id"]))
            self.assertEqual(status, 200, body[:200])
            self.assertEqual(headers["Content-Type"], "video/mp4")
            kinds = sorted(t["kind"] for t in fmp4.tracks(body).values())
            self.assertEqual(kinds, ["soun", "vide"], track["label"])
            sound = next(t for t in fmp4.tracks(body).values() if t["kind"] == "soun")
            self.assertAlmostEqual(min(s[1] for s in sound["samples"]), plan["starts"][middle], delta=0.03)

    def test_sound_tracks_that_do_not_exist(self):
        self.assertEqual(self.request("GET", "/stream/%s?audio=x99" % I(DUBBED))[0], 404)
        self.assertEqual(self.request("GET", "/stream/%s?piece=0&audio=e99" % I(STREAM))[0], 404)

    def test_audio_choice_is_kept(self):
        # the label and the language of the track chosen (behaviour#audio-tracks)
        choice = {"playlist": I("library/Series"), "label": "Russian — Dub [DEEP]", "language": "ru"}
        status, _, body = self.request("POST", "/api/audio-choice", choice)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), choice)
        cat = self.get_json("/api/catalogue")
        self.assertEqual(cat["audio_choices"][I("library/Series")], {"label": "Russian — Dub [DEEP]", "language": "ru"})
        self.assertNotIn(I("library/Series"), cat["subtitle_choices"] or {})
        # back to the default: none
        status, _, _ = self.request("POST", "/api/audio-choice",
                                    {"playlist": I("library/Series"), "label": None, "language": "ru"})
        self.assertEqual(status, 200)
        self.assertIsNone(self.get_json("/api/catalogue")["audio_choices"][I("library/Series")])
        status, _, _ = self.request("POST", "/api/audio-choice", {"playlist": 3, "label": None})
        self.assertEqual(status, 400)

    # ------------------------------------------------------------ resume

    def test_position_is_returned_to_another_client(self):
        status, _, body = self.request("POST", "/api/position", {"video": I(EPISODE), "position": 750})
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body), {"video": I(EPISODE), "position": 750, "watched": False})
        # another client: a fresh connection, nothing shared but the server
        pos = self.get_json("/api/position?video=" + I(EPISODE))
        self.assertEqual((pos["position"], pos["watched"]), (750, False))
        cat = self.get_json("/api/catalogue")
        self.assertEqual(cat["videos"][I(EPISODE)]["position"], 750)
        self.assertIn(I(EPISODE), cat["continue"])

    def test_stopped_in_the_last_minute_counts_as_watched(self):
        # behaviour#resume#scenarios#credits: stopped 40 seconds before the end
        status, _, body = self.request("POST", "/api/position", {"video": I(EPISODE), "position": DURATION - 40})
        self.assertEqual(status, 200, body)
        pos = self.get_json("/api/position?video=" + I(EPISODE))
        self.assertEqual((pos["position"], pos["watched"]), (0, True))
        self.assertNotIn(I(EPISODE), self.get_json("/api/catalogue")["continue"])

    def test_short_video_counts_as_watched_in_its_last_tenth(self):
        # 300 s long: its last tenth is 30 s, so 40 s before the end is not watched yet
        status, _, body = self.request("POST", "/api/position", {"video": I(SHORT), "position": SHORT_DURATION - 40})
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body), {"video": I(SHORT), "position": SHORT_DURATION - 40, "watched": False})
        status, _, body = self.request("POST", "/api/position", {"video": I(SHORT), "position": SHORT_DURATION - 20})
        self.assertEqual(status, 200, body)
        pos = self.get_json("/api/position?video=" + I(SHORT))
        self.assertEqual((pos["position"], pos["watched"]), (0, True))

    def test_continue_is_most_recent_first(self):
        self.request("POST", "/api/position", {"video": I(SHORT), "position": 10})
        time.sleep(0.05)
        self.request("POST", "/api/position", {"video": I(EPISODE), "position": 100})
        self.assertEqual(self.get_json("/api/catalogue")["continue"][:2], [I(EPISODE), I(SHORT)])
        time.sleep(0.05)
        self.request("POST", "/api/position", {"video": I(SHORT), "position": 20})
        self.assertEqual(self.get_json("/api/catalogue")["continue"][:2], [I(SHORT), I(EPISODE)])

    def test_position_outside_the_library_is_refused(self):
        for path in ("../secret.txt", "library/../secret.txt", EPISODE):
            status, _, _ = self.request("POST", "/api/position", {"video": path, "position": 3})
            self.assertEqual(status, 404, path)

    def test_subtitle_choice_is_kept(self):
        # the label and the language of the track chosen (behaviour#subtitles-follow)
        choice = {"playlist": I("library/Series"), "label": "Russian — CafeSubs", "language": "ru"}
        status, _, body = self.request("POST", "/api/subtitle-choice", choice)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), choice)
        self.assertEqual(self.get_json("/api/catalogue")["subtitle_choices"][I("library/Series")],
                         {"label": "Russian — CafeSubs", "language": "ru"})
        # switched off: none
        status, _, _ = self.request("POST", "/api/subtitle-choice",
                                    {"playlist": I("library/Series"), "label": None, "language": None})
        self.assertEqual(status, 200)
        self.assertIsNone(self.get_json("/api/catalogue")["subtitle_choices"][I("library/Series")])

    # ---------------------------------------------------------- new files

    def test_file_copied_in_appears_after_a_rescan(self):
        # decisions#background-scan: a request that finds the scan old gets the
        # catalogue as it stands, and the scan it starts tells every page
        stream = self.events("dev-new", "Laptop")
        self.get_json("/api/catalogue")
        target = os.path.join(self.lib, "Show", "Show - 02.mp4")
        shutil.copy(self.video, target)
        self.addCleanup(os.unlink, target)
        time.sleep(RESCAN_AFTER + 0.5)
        cat = self.get_json("/api/catalogue")
        self.assertNotIn(I("library/Show/Show - 02.mp4"), cat["videos"])
        deadline = time.monotonic() + 10
        while I("library/Show/Show - 02.mp4") not in cat["videos"]:
            # an announcement left by another test's scan may come first
            stream.wait_topic("catalogue", timeout=max(0.1, deadline - time.monotonic()))
            cat = self.get_json("/api/catalogue")
        show = next(p for p in cat["playlists"] if p["id"] == I("library/Show"))
        self.assertEqual(show["videos"], [I("library/Show/Show - 01.mp4"), I("library/Show/Show - 02.mp4")])

    def test_continue_has_one_entry_per_playlist(self):
        # behaviour#resume#scenarios#one-card: episodes 1 to 4 watched to the
        # end, episode 5 stopped at 08:10
        for episode in SERIES[:4]:
            self.request("POST", "/api/position", {"video": I(episode), "position": DURATION - 5})
        self.request("POST", "/api/position", {"video": I(SERIES[4]), "position": 490})
        cat = self.get_json("/api/catalogue")
        self.assertEqual([vid for vid in cat["continue"] if vid in map(I, SERIES)], [I(SERIES[4])])
        self.assertEqual(cat["continue"][0], I(SERIES[4]))  # the most recently watched first
        self.assertEqual(cat["videos"][I(SERIES[4])]["position"], 490)
        # episode 5 finished: the series goes on with episode 6
        self.request("POST", "/api/position", {"video": I(SERIES[4]), "position": DURATION})
        cat = self.get_json("/api/catalogue")
        self.assertEqual([vid for vid in cat["continue"] if vid in map(I, SERIES)], [I(SERIES[5])])
        # all of it watched: nothing left, no entry
        self.request("POST", "/api/position", {"video": I(SERIES[5]), "position": DURATION})
        cat = self.get_json("/api/catalogue")
        self.assertEqual([vid for vid in cat["continue"] if vid in map(I, SERIES)], [])

    # ------------------------------------------------------------ devices

    def events(self, device_id, name):
        stream = EventReader(self.port, device_id, name)
        self.addCleanup(stream.close)
        return stream

    def test_event_streams_see_each_other_appear(self):
        a = self.events("dev-a1", "Living room TV")
        a.wait_devices(lambda ds: "dev-a1" in ds)
        b = self.events("dev-b1", "Laptop")
        seen = a.wait_devices(lambda ds: "dev-b1" in ds)
        self.assertEqual(seen["dev-b1"]["name"], "Laptop")
        seen = b.wait_devices(lambda ds: {"dev-a1", "dev-b1"} <= set(ds))
        self.assertEqual(seen["dev-a1"]["name"], "Living room TV")
        self.assertIsNone(seen["dev-a1"]["video"])

    def test_report_reaches_the_other(self):
        a = self.events("dev-a2", "Living room TV")
        b = self.events("dev-b2", "Laptop")
        b.wait_devices(lambda ds: "dev-a2" in ds)
        report = {"id": "dev-a2", "video": I(EPISODE), "audio": "e1", "subtitle": "x0",
                  "position": 750, "playing": True}
        status, _, body = self.request("POST", "/api/report", report)
        self.assertEqual(status, 200, body)
        d = b.wait_devices(lambda ds: ds.get("dev-a2", {}).get("video") == I(EPISODE))["dev-a2"]
        self.assertEqual((d["audio"], d["subtitle"], d["position"], d["playing"]), ("e1", "x0", 750, True))
        self.assertIsInstance(d["reported"], float)
        # paused: every device sees it paused (behaviour#devices#scenarios#remote)
        self.request("POST", "/api/report", dict(report, playing=False, position=752.5))
        for stream in (a, b):
            d = stream.wait_devices(lambda ds: ds.get("dev-a2", {}).get("playing") is False
                                    and ds["dev-a2"]["video"] == I(EPISODE))["dev-a2"]
            self.assertEqual(d["position"], 752.5)
        # renamed
        self.request("POST", "/api/report", dict(report, name="Big TV"))
        b.wait_devices(lambda ds: ds.get("dev-a2", {}).get("name") == "Big TV")
        # nothing plays any more: the tracks and the position go with the video
        self.request("POST", "/api/report", {"id": "dev-a2", "video": None, "audio": "e1", "position": 3})
        d = b.wait_devices(lambda ds: ds.get("dev-a2", {}).get("video") is None)["dev-a2"]
        self.assertEqual((d["audio"], d["subtitle"], d["position"], d["playing"]), (None, None, 0, False))

    def test_reports_that_are_refused(self):
        self.events("dev-a3", "TV").wait_devices(lambda ds: "dev-a3" in ds)
        self.assertEqual(self.request("POST", "/api/report", {"id": "nobody", "video": None})[0], 404)
        self.assertEqual(self.request("POST", "/api/report", {"id": "dev-a3", "video": "../secret.txt"})[0], 404)
        for bad in ({"id": 3}, {"id": "dev-a3", "position": "x"}, {"id": "dev-a3", "playing": "yes"},
                    {"id": "dev-a3", "video": 5}):
            self.assertEqual(self.request("POST", "/api/report", bad)[0], 400, bad)

    def test_command_reaches_only_the_device_it_names(self):
        a = self.events("dev-a4", "Living room TV")
        b = self.events("dev-b4", "Phone")
        a.wait_devices(lambda ds: "dev-b4" in ds)
        b.wait_devices(lambda ds: "dev-a4" in ds)
        status, _, body = self.request("POST", "/api/control", {"device": "dev-a4", "action": "pause"})
        self.assertEqual(status, 200, body)
        self.assertEqual(a.wait_command(), {"action": "pause"})
        self.request("POST", "/api/control", {"device": "dev-a4", "action": "seek", "position": 61.5})
        self.assertEqual(a.wait_command(), {"action": "seek", "position": 61.5})
        # moving playback (behaviour#devices#scenarios#move): play a video from
        # a position with these tracks
        move = {"device": "dev-b4", "action": "load", "video": I(EPISODE), "position": 750,
                "audio": "e1", "subtitle": None}
        self.assertEqual(self.request("POST", "/api/control", move)[0], 200)
        self.assertEqual(b.wait_command(), {"action": "load", "video": I(EPISODE), "position": 750,
                                            "audio": "e1", "subtitle": None})
        for action in ("play", "next", "stop"):
            self.request("POST", "/api/control", {"device": "dev-b4", "action": action})
            self.assertEqual(b.wait_command(), {"action": action})
        # neither saw the other's commands
        time.sleep(1.0)
        self.assertEqual(a.commands(), [])
        self.assertEqual(b.commands(), [])

    def test_commands_that_are_refused(self):
        self.events("dev-a5", "TV").wait_devices(lambda ds: "dev-a5" in ds)
        self.assertEqual(self.request("POST", "/api/control", {"device": "nobody", "action": "pause"})[0], 404)
        for bad in ({"device": "dev-a5", "action": "explode"}, {"device": "dev-a5", "action": "seek"},
                    {"device": "dev-a5", "action": "load"}, {"action": "pause"}):
            self.assertEqual(self.request("POST", "/api/control", bad)[0], 400, bad)
        load = {"device": "dev-a5", "action": "load", "position": 0}
        self.assertEqual(self.request("POST", "/api/control", dict(load, video="../secret.txt"))[0], 404)
        self.assertEqual(self.request("POST", "/api/control", dict(load, video=I("library/Gone.mp4")))[0], 404)
        status, _, _ = self.request("GET", "/api/events")
        self.assertEqual(status, 400)

    def test_closed_stream_leaves_the_list(self):
        # behaviour#devices#scenarios#gone
        a = self.events("dev-a6", "Phone")
        b = self.events("dev-b6", "Laptop")
        a.wait_devices(lambda ds: "dev-b6" in ds)
        closed = time.monotonic()
        b.close()
        a.wait_devices(lambda ds: "dev-b6" not in ds, timeout=DEVICE_GRACE + 5)
        self.assertGreaterEqual(time.monotonic() - closed, DEVICE_GRACE - 0.5)
        self.assertEqual(self.request("POST", "/api/control", {"device": "dev-b6", "action": "pause"})[0], 404)

    def test_reload_keeps_the_device(self):
        a = self.events("dev-a7", "Phone")
        b = self.events("dev-b7", "Laptop")
        a.wait_devices(lambda ds: "dev-b7" in ds)
        since = len(a.all_lists()) - 1  # the lists from the one where it appeared
        self.request("POST", "/api/report", {"id": "dev-b7", "video": I(EPISODE), "position": 10, "playing": True})
        b.close()
        time.sleep(1.0)
        again = self.events("dev-b7", "Laptop")
        seen = again.wait_devices(lambda ds: "dev-b7" in ds)
        self.assertEqual(seen["dev-b7"]["video"], I(EPISODE))  # what it played is still known
        time.sleep(DEVICE_GRACE + 1)
        self.assertIn("dev-b7", a.latest())
        self.assertNotIn(False, [("dev-b7" in ds) for ds in a.all_lists()[since:]])

    def test_stuck_client_does_not_hold_up_the_others(self):
        # a page that opened its stream and stopped reading it
        stuck = socket.socket()
        stuck.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        stuck.connect(("127.0.0.1", self.port))
        self.addCleanup(stuck.close)
        stuck.sendall(b"GET /api/events?id=dev-stuck&name=Stuck HTTP/1.1\r\nHost: x\r\n\r\n")
        a = self.events("dev-a8", "Phone")
        a.wait_devices(lambda ds: "dev-stuck" in ds)
        long_name = "N" * 80
        began = time.monotonic()
        for i in range(300):
            status, _, _ = self.request("POST", "/api/report", {
                "id": "dev-a8", "name": long_name, "video": I(EPISODE), "position": i, "playing": True})
            self.assertEqual(status, 200)
        self.assertLess(time.monotonic() - began, 30)
        d = a.wait_devices(lambda ds: ds.get("dev-a8", {}).get("position") == 299, timeout=3)["dev-a8"]
        self.assertEqual(d["name"], long_name)
        status, _, _ = self.request("POST", "/api/control", {"device": "dev-a8", "action": "pause"})
        self.assertEqual(status, 200)
        self.assertEqual(a.wait_command(timeout=3), {"action": "pause"})

    # --------------------------------------------------- library-control

    def post(self, path, body, source=None):
        status, _, data = self.request("POST", path, body, source=source)
        return status, (json.loads(data) if data and status == 200 else data)

    def test_removed_video_and_folder_leave_and_come_back(self):
        # behaviour#library-control#scenarios#remove-folder
        cat = self.get_json("/api/catalogue")
        self.assertIn(I("library/Recordings"), [p["id"] for p in cat["playlists"]])
        self.assertEqual(self.post("/api/library/remove", {"id": I("library/Recordings")}),
                         (200, {"id": I("library/Recordings"), "path": "library/Recordings", "kind": "folder"}))
        self.addCleanup(self.request, "POST", "/api/library/restore", {"path": "library/Recordings"})
        self.assertEqual(self.post("/api/library/remove", {"id": I(SHORT)}),
                         (200, {"id": I(SHORT), "path": SHORT, "kind": "video"}))
        self.addCleanup(self.request, "POST", "/api/library/restore", {"path": SHORT})
        cat = self.get_json("/api/catalogue")  # at once, not after the next rescan
        self.assertNotIn(I("library/Recordings"), [p["id"] for p in cat["playlists"]])
        for vid in RECORDINGS + [SHORT]:
            self.assertNotIn(I(vid), cat["videos"])
            self.assertTrue(os.path.isfile(os.path.join(self.tmp, vid)))  # nothing deleted on disk
        excluded = self.get_json("/api/settings")["excluded"]
        self.assertEqual([(e["path"], e["kind"]) for e in excluded],
                         [("library/Recordings", "folder"), (SHORT, "video")])
        self.assertEqual(excluded[0]["title"], "Recordings")
        # brought back
        self.assertEqual(self.post("/api/library/restore", {"path": "library/Recordings"}),
                         (200, {"path": "library/Recordings"}))
        self.assertEqual(self.post("/api/library/restore", {"path": SHORT})[0], 200)
        cat = self.get_json("/api/catalogue")
        self.assertIn(I("library/Recordings"), [p["id"] for p in cat["playlists"]])
        self.assertIn(I(SHORT), cat["videos"])
        self.assertEqual(self.get_json("/api/settings")["excluded"], [])

    def test_removing_what_cannot_be_removed(self):
        # only what the catalogue names can be removed, by its id
        for bad in ({"id": "library/../secret.txt"}, {"id": I("library/Nothing here.mp4")},
                    {"id": "library"}, {"id": I("library")}, {"id": 5}, {"path": SHORT}):
            self.assertEqual(self.post("/api/library/remove", bad)[0], 404, bad)
        self.assertEqual(self.post("/api/library/restore", {"path": "library/Show"})[0], 404)

    def test_forgetting_a_playlist_clears_its_positions(self):
        # behaviour#library-control#scenarios#forget-series
        for vid in RECORDINGS:
            self.request("POST", "/api/position", {"video": I(vid), "position": 120})
        self.request("POST", "/api/position", {"video": I(SHORT), "position": 30})
        self.assertIn(I(RECORDINGS[1]), self.get_json("/api/catalogue")["continue"])
        status, body = self.post("/api/forget", {"playlist": I("library/Recordings")})
        self.assertEqual((status, sorted(body["forgotten"])), (200, sorted(map(I, RECORDINGS))))
        cat = self.get_json("/api/catalogue")
        self.assertEqual([v for v in cat["continue"] if v in map(I, RECORDINGS)], [])
        for vid in RECORDINGS:
            self.assertEqual((cat["videos"][I(vid)]["position"], cat["videos"][I(vid)]["watched"]), (0, False))
        self.assertEqual(cat["videos"][I(SHORT)]["position"], 30)  # the others are kept
        # one video outside any playlist
        self.assertEqual(self.post("/api/forget", {"video": I(SHORT)}), (200, {"forgotten": [I(SHORT)]}))
        self.assertNotIn(I(SHORT), self.get_json("/api/catalogue")["continue"])
        self.assertEqual(self.post("/api/forget", {"playlist": I("library/Nothing")})[0], 404)
        self.assertEqual(self.post("/api/forget", {"video": "../secret.txt"})[0], 404)
        self.assertEqual(self.post("/api/forget", {})[0], 400)

    def test_library_folders_from_another_address_are_refused(self):
        # behaviour#library-control#scenarios#remote-folders: a phone sees the
        # folders and changes nothing
        try:
            with socket.socket() as probe:
                probe.bind(("127.0.0.2", 0))
        except OSError:
            self.skipTest("no second loopback address to come from")
        status, _, body = self.request("GET", "/api/settings", source="127.0.0.2")
        settings = json.loads(body)
        self.assertEqual(status, 200)
        self.assertFalse(settings["local"])
        self.assertEqual(folders_of(settings), [{"name": "library", "folder": self.lib}])
        more = os.path.join(self.tmp, "more")
        for change in ({"action": "add", "folder": more}, {"action": "remove", "folder": self.lib}):
            status, _ = self.post("/api/settings/library-folders", change, source="127.0.0.2")
            self.assertEqual(status, 403, change)
        self.assertEqual(len(self.get_json("/api/settings")["library_folders"]), 1)
        # removing a video works from any device
        self.assertEqual(self.post("/api/library/remove", {"id": I(SHORT)}, source="127.0.0.2")[0], 200)
        self.assertEqual(self.post("/api/library/restore", {"path": SHORT}, source="127.0.0.2")[0], 200)

    def test_library_folders_from_this_computer(self):
        self.assertTrue(self.get_json("/api/settings")["local"])
        more = os.path.join(self.tmp, "more")
        status, settings = self.post("/api/settings/library-folders", {"action": "add", "folder": more})
        self.assertEqual(status, 200, settings)
        self.addCleanup(self.request, "POST", "/api/settings/library-folders", {"action": "remove", "folder": more})
        self.assertEqual(folders_of(settings), [{"name": "library", "folder": self.lib},
                                                {"name": "more", "folder": more}])
        # each with the free space on its disk (behaviour#library-control)
        self.assertTrue(all(isinstance(f["free"], int) and f["free"] > 0 for f in settings["library_folders"]))
        cat = self.get_json("/api/catalogue")
        self.assertIn(I("more/Extra clip.mp4"), cat["videos"])
        self.assertEqual(self.request("GET", "/video/" + I("more/Extra clip.mp4"))[0], 200)
        # the same folder again, one inside a library folder, one not there, a relative path
        for folder, status in ((more, 409), (os.path.join(self.lib, "Show"), 409), (self.tmp, 409),
                               (os.path.join(self.tmp, "nowhere"), 404), ("more", 400)):
            self.assertEqual(self.post("/api/settings/library-folders", {"action": "add", "folder": folder})[0],
                             status, folder)
        status, settings = self.post("/api/settings/library-folders", {"action": "remove", "folder": more})
        self.assertEqual(status, 200)
        self.assertEqual(len(settings["library_folders"]), 1)
        self.assertNotIn(I("more/Extra clip.mp4"), self.get_json("/api/catalogue")["videos"])
        self.assertEqual(self.request("GET", "/video/" + I("more/Extra clip.mp4"))[0], 404)
        self.assertEqual(self.post("/api/settings/library-folders", {"action": "remove", "folder": more})[0], 404)
        self.assertEqual(self.post("/api/settings/library-folders", {"action": "wipe", "folder": more})[0], 400)

    def test_rescan_on_request(self):
        target = os.path.join(self.lib, "Copied now.mp4")
        shutil.copy(self.video, target)
        self.addCleanup(os.unlink, target)
        status, body = self.post("/api/rescan", {})
        self.assertEqual(status, 200)
        self.assertIn(I("library/Copied now.mp4"), self.get_json("/api/catalogue")["videos"])
        self.assertGreaterEqual(body["videos"], 10)
        # with no body at all
        self.assertEqual(self.request("POST", "/api/rescan")[0], 200)

    # ------------------------------------------------------------ details

    def test_media_details_and_delivery(self):
        d = self.get_json("/api/details?video=" + I(EPISODE))
        self.assertEqual(d["video"], I(EPISODE))
        self.assertEqual(d["delivery"], {"kind": "direct", "encoder": None, "reason": None})
        self.assertEqual((d["media"]["container"], d["media"]["width"], d["media"]["height"]), ("MP4", 32, 32))
        self.assertEqual((d["media"]["video_codec"], d["media"]["bit_depth"]), ("h264", 8))
        self.assertEqual(d["media"]["size"], os.path.getsize(self.video))
        # sent in pieces, the video copied and the sound converted
        d = self.get_json("/api/details?video=" + I(STREAM))
        self.assertEqual(d["delivery"]["kind"], "copy")
        self.assertEqual((d["media"]["container"], d["media"]["audio_codec"]), ("MKV", "eac3"))
        # converted as it plays, by the encoder named
        d = self.get_json("/api/details?video=" + I(OLD))
        self.assertEqual(d["delivery"]["kind"], "transcode")
        self.assertRegex(d["delivery"]["encoder"],
                         r"^(h264_nvenc|h264_vaapi on /dev/dri/renderD\d+|h264_qsv|h264_amf|h264_videotoolbox|libx264)$")
        # another sound track: a direct video goes in pieces, with that track's sound
        audio = self.get_json("/api/catalogue")["videos"][I(DUBBED)]["audio"]
        d = self.get_json("/api/details?video=%s&audio=%s" % (I(DUBBED), audio[2]["id"]))
        self.assertEqual(d["delivery"]["kind"], "copy")
        self.assertEqual(d["media"]["audio_codec"], "ac3")
        self.assertEqual(self.get_json("/api/details?video=" + I(DUBBED))["delivery"]["kind"], "direct")
        self.assertEqual(self.request("GET", "/api/details?video=%s&audio=x9" % I(DUBBED))[0], 404)
        self.assertEqual(self.request("GET", "/api/details?video=library/None.mp4")[0], 404)

    # ------------------------------------------------------------ address

    def test_address_and_its_qr_code(self):
        address = self.get_json("/api/address")
        self.assertEqual(address["port"], self.port)
        if address["host"] is None:
            self.assertIsNone(address["address"])
            self.assertEqual(self.request("GET", "/api/address.svg")[0], 404)
            self.skipTest("this computer has no address on a network")
        self.assertNotIn("localhost", address["address"])
        self.assertFalse(address["host"].startswith("127."))
        self.assertEqual(address["address"], "http://%s:%d" % (address["host"], self.port))
        self.assertEqual(address["secure"], "https://%s:%d" % (address["host"], self.port))
        status, headers, body = self.request("GET", "/api/address.svg")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("image/svg+xml"))
        svg = ElementTree.fromstring(body)
        self.assertEqual(svg.find("{http://www.w3.org/2000/svg}title").text, address["address"])
        self.assertEqual(matrix_of_svg(body), masked_for_svg(qr.encode(address["address"])))

    # ---------------------------------------------------------- controls

    def test_every_control_reaches_the_device(self):
        # server#control: previous, volume, speed, sound track and subtitles
        # join play, pause, seek, next and stop
        tv = self.events("dev-c1", "Living room TV")
        phone = self.events("dev-c2", "Phone")
        phone.wait_devices(lambda ds: "dev-c1" in ds)
        for command in ({"action": "previous"}, {"action": "volume", "volume": 0.4},
                        {"action": "volume", "muted": True}, {"action": "volume", "volume": 1, "muted": False},
                        {"action": "speed", "speed": 1.5}, {"action": "audio", "audio": "x0"},
                        {"action": "audio", "audio": None}, {"action": "subtitle", "subtitle": "x1"},
                        {"action": "subtitle", "subtitle": None}):
            status, _, body = self.request("POST", "/api/control", dict(command, device="dev-c1"))
            self.assertEqual(status, 200, body)
            self.assertEqual(tv.wait_command(), command)
        for bad in ({"action": "volume"}, {"action": "volume", "volume": 1.5}, {"action": "volume", "volume": "x"},
                    {"action": "volume", "muted": "yes"}, {"action": "speed", "speed": 3},
                    {"action": "speed", "speed": 0.1}, {"action": "speed"}, {"action": "audio", "audio": 3},
                    {"action": "subtitle", "subtitle": ["x"]},
                    # full screen is pressed on the device itself (decisions#fullscreen-here)
                    {"action": "fullscreen"}, {"action": "fullscreen", "fullscreen": True}):
            self.assertEqual(self.request("POST", "/api/control", dict(bad, device="dev-c1"))[0], 400, bad)
        time.sleep(0.5)
        self.assertEqual(tv.commands(), [])
        self.assertEqual(phone.commands(), [])

    def test_load_carries_volume_and_speed(self):
        # behaviour#devices#scenarios#move: the video goes on as loud and as fast
        tv = self.events("dev-l1", "Living room TV")
        phone = self.events("dev-l2", "Phone")
        phone.wait_devices(lambda ds: "dev-l1" in ds)
        load = {"action": "load", "video": I(EPISODE), "position": 750, "audio": None, "subtitle": None,
                "volume": 0.4, "speed": 1.5}
        status, _, body = self.request("POST", "/api/control", dict(load, device="dev-l1"))
        self.assertEqual(status, 200, body)
        self.assertEqual(tv.wait_command(), load)
        for bad in ({"volume": 1.5}, {"volume": "x"}, {"speed": 3}):
            self.assertEqual(self.request("POST", "/api/control", dict(load, device="dev-l1", **bad))[0], 400, bad)
        time.sleep(0.3)
        self.assertEqual(tv.commands(), [])

    def test_shared_setting_reaches_every_page(self):
        # behaviour#subtitle-size#scenarios#shared-size: changed on a phone,
        # applied on every device at once
        tv = self.events("dev-s1", "Living room TV")
        phone = self.events("dev-s2", "Phone")
        self.assertEqual(tv.wait_topic("settings"), {"subtitle_size": 100, "seed_torrents": False, "language": "auto"})
        self.addCleanup(self.post, "/api/settings/shared", {"subtitle_size": 100})
        status, shared = self.post("/api/settings/shared", {"subtitle_size": 150})
        self.assertEqual((status, shared["subtitle_size"]), (200, 150))
        for page in (tv, phone):
            page.wait_topic("settings", lambda d: d["subtitle_size"] == 150)
        self.assertEqual(self.get_json("/api/settings")["shared"]["subtitle_size"], 150)
        for bad in ({"subtitle_size": 120}, {"subtitle_size": True}, {"seed_torrents": "yes"},
                    {"language": "de"}, {"language": 1},
                    {"nothing": 1}, {"subtitle_size": 125, "nothing": 1}):
            self.assertEqual(self.post("/api/settings/shared", bad)[0], 400, bad)
        self.assertEqual(self.get_json("/api/settings")["shared"]["subtitle_size"], 150)  # none half-applied

    def test_continue_row_reaches_every_page(self):
        # behaviour#resume#scenarios#live-row: the row is sent when it
        # changes, and a position moving on within a video sends nothing
        laptop = self.events("dev-r1", "Laptop")
        laptop.wait_devices(lambda ds: "dev-r1" in ds)
        self.post("/api/forget", {"playlist": I("library/Series")})  # what another test left there
        self.addCleanup(self.post, "/api/forget", {"playlist": I("library/Series")})
        third, fourth = I(SERIES[2]), I(SERIES[3])
        self.request("POST", "/api/position", {"video": third, "position": 100})
        row = laptop.wait_topic("continue", lambda d: third in d["continue"])
        self.assertEqual(row["videos"][third], {"position": 100, "watched": False})
        self.request("POST", "/api/position", {"video": third, "position": 200})
        time.sleep(0.5)
        self.assertEqual([d for d in laptop.topic_events("continue") if third in d["videos"]], [])
        # watched to its end: the card goes on to the next episode
        self.request("POST", "/api/position", {"video": third, "position": DURATION})
        row = laptop.wait_topic("continue", lambda d: fourth in d["continue"])
        self.assertNotIn(third, row["continue"])
        self.assertEqual(row["videos"][third], {"position": 0, "watched": True})

    def test_report_carries_volume_and_speed(self):
        tv = self.events("dev-c3", "TV")
        phone = self.events("dev-c4", "Phone")
        phone.wait_devices(lambda ds: "dev-c3" in ds)
        self.assertIsNone(phone.latest()["dev-c3"]["volume"])
        report = {"id": "dev-c3", "video": I(EPISODE), "position": 5, "playing": True,
                  "volume": 0.25, "muted": False, "speed": 1.25}
        self.assertEqual(self.request("POST", "/api/report", report)[0], 200)
        d = phone.wait_devices(lambda ds: ds.get("dev-c3", {}).get("volume") == 0.25)["dev-c3"]
        self.assertEqual((d["muted"], d["speed"]), (False, 1.25))
        # a report without them keeps them
        self.request("POST", "/api/report", {"id": "dev-c3", "video": I(EPISODE), "position": 9, "playing": True})
        d = phone.wait_devices(lambda ds: ds.get("dev-c3", {}).get("position") == 9)["dev-c3"]
        self.assertEqual((d["volume"], d["speed"]), (0.25, 1.25))
        for bad in ({"volume": 2}, {"volume": "x"}, {"muted": 1}, {"speed": 0}):
            self.assertEqual(self.request("POST", "/api/report", dict(report, **bad))[0], 400, bad)

    # --------------------------------------------------------------- page

    def test_page_and_thumbnail_are_served(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"/static/app.js", body)
        # every view at an address of its own (behaviour#short-links)
        for url in ("/watch/" + I(EPISODE), "/playlist/" + I("library/Series"), "/settings"):
            status, _, page = self.request("GET", url)
            self.assertEqual((status, page), (200, body), url)
        for url in ("/watch/", "/watch/A..B", "/playlist/x/y", "/settingsx"):
            self.assertEqual(self.request("GET", url)[0], 404, url)
        status, headers, body = self.request("GET", "/thumb/" + I(EPISODE))
        self.assertEqual(status, 200)
        self.assertEqual(body[:2], b"\xff\xd8")


    # ------------------------------------------------------ short-links

    def test_ids_are_short_and_stay_the_same(self):
        # behaviour#short-links: eight characters from a hash of the path, the
        # same on every scan and for any server, so a link outlives a restart
        self.request("POST", "/api/rescan", {})  # what other tests copied in and took out is gone
        cat = self.get_json("/api/catalogue")
        ids = list(cat["videos"]) + [p["id"] for p in cat["playlists"]]
        self.assertTrue(all(re.fullmatch(r"[a-z2-7]{8}", i) for i in ids), ids)
        self.assertEqual(len(set(ids)), len(ids))
        self.assertEqual(cat["videos"][I(SERIES[0])]["playlist"], I("library/Series"))
        self.assertEqual(self.request("POST", "/api/rescan", {})[0], 200)
        again = self.get_json("/api/catalogue")
        for vid, v in cat["videos"].items():
            self.assertEqual(again["videos"][vid]["title"], v["title"])
        self.assertNotIn(b"library/", json.dumps(cat["playlists"]).encode())

    def test_ids_that_would_coincide_get_longer(self):
        import mirutube.library as library
        real = library._hashed_id
        self.addCleanup(setattr, library, "_hashed_id", real)
        library._hashed_id = lambda path, n: ("same" * 4)[:n] if n == 8 else real(path, n)
        ids = library.short_ids(["a/1.mp4", "a/2.mp4", "b"])
        self.assertEqual({len(i) for i in ids.values()}, {12})
        self.assertEqual(len(set(ids.values())), 3)

    # ------------------------------------------------------------ https

    def https(self, path, cafile):
        """A request over HTTPS to the same port, trusting only `cafile`."""
        context = ssl.create_default_context(cafile=cafile)
        conn = http.client.HTTPSConnection("127.0.0.1", self.port, timeout=30, context=context)
        try:
            conn.request("GET", path)
            res = conn.getresponse()
            return res.status, res.read()
        finally:
            conn.close()

    def test_https_and_http_on_one_port(self):
        # behaviour#secure-at-home: the authority's certificate is served over
        # plain HTTP, and a client trusting it alone opens HTTPS with no warning
        status, headers, pem = self.request("GET", "/mirutube.crt")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/x-x509-ca-cert")
        self.assertTrue(pem.startswith(b"-----BEGIN CERTIFICATE-----"), pem[:40])
        self.assertNotIn(b"PRIVATE", pem)
        cafile = os.path.join(self.tmp, "trusted.crt")
        with open(cafile, "wb") as f:
            f.write(pem)
        status, body = self.https("/api/ping", cafile)
        self.assertEqual((status, json.loads(body)), (200, {"ok": True}))
        status, body = self.https("/api/catalogue", cafile)
        self.assertIn(I(EPISODE), json.loads(body)["videos"])
        # a client trusting nothing of MiruTube's is refused by its own check
        with self.assertRaises(ssl.SSLCertVerificationError):
            self.https("/api/ping", None)
        # and plain HTTP goes on working beside it
        self.assertEqual(self.request("GET", "/api/ping")[0], 200)
        # the key is never sent
        for url in ("/mirutube-ca.key", "/static/../data/mirutube-ca.key", "/data/mirutube-ca.key"):
            status, _, body = self.request("GET", url)
            self.assertEqual(status, 404, url)
            self.assertNotIn(b"PRIVATE KEY", body)

    def test_https_names_the_address_others_open(self):
        address = self.get_json("/api/address")
        if address["host"] is None:
            self.skipTest("this computer has no address on a network")
        cafile = os.path.join(self.tmp, "trusted-lan.crt")
        with open(cafile, "wb") as f:
            f.write(self.request("GET", "/mirutube.crt")[2])
        context = ssl.create_default_context(cafile=cafile)
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as raw:
            # the name checked is the network address, as a phone would check it
            with context.wrap_socket(raw, server_hostname=address["host"]) as tls:
                self.assertIn(("IP Address", address["host"]), tls.getpeercert()["subjectAltName"])


def matrix_of_svg(body):
    """The modules an SVG QR code draws as dots, as rows of booleans."""
    svg = ElementTree.fromstring(body)
    full = int(svg.get("viewBox").split()[2])
    size = full - 2 * qr.QUIET
    dark = [[False] * size for _ in range(size)]
    for c in svg.iter("{http://www.w3.org/2000/svg}circle"):
        if float(c.get("r")) != qr.DOT:
            continue  # the eye of an alignment pattern
        x, y = float(c.get("cx")) - qr.QUIET - 0.5, float(c.get("cy")) - qr.QUIET - 0.5
        dark[round(y)][round(x)] = True
    return dark


def masked_for_svg(matrix):
    """The modules of a QR code that the SVG draws as single dots: all but the
    finder squares, the alignment patterns drawn whole and the square under
    the mark."""
    size = len(matrix)
    first, last = qr.mark_area(size)
    out = [row[:] for row in matrix]
    for y in range(size):
        for x in range(size):
            if qr._in_finder(x, y, size) or (first <= x <= last and first <= y <= last):
                out[y][x] = False
    for cx, cy in qr._alignment_centres((size - 17) // 4):
        if any(first <= cx + dx <= last and first <= cy + dy <= last for dx in (-2, 2) for dy in (-2, 2)):
            continue
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                out[cy + dy][cx + dx] = False
    return out


class EventReader:
    """One page's event stream (server#serve_events), read on a thread of its
    own: every `devices` list and every `command` as they come."""

    def __init__(self, port, device_id, name):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=30)
        self.sock.sendall(("GET /api/events?id=%s&name=%s HTTP/1.1\r\nHost: x\r\n\r\n"
                           % (quote(device_id), quote(name))).encode())
        self.file = self.sock.makefile("rb")
        status = self.file.readline()
        if b" 200 " not in status:
            raise AssertionError("the event stream was refused: %r" % status)
        while self.file.readline() not in (b"\r\n", b"\n", b""):
            pass
        self.lists = queue.Queue()
        self.command_queue = queue.Queue()
        self.topics = {"settings": queue.Queue(), "continue": queue.Queue(), "catalogue": queue.Queue()}
        self._seen = []
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        event, data = None, []
        try:
            for line in self.file:
                line = line.decode("utf-8").rstrip("\r\n")
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
                elif line == "":
                    if event == "devices":
                        self.lists.put({d["id"]: d for d in json.loads("\n".join(data))["devices"]})
                    elif event == "command":
                        self.command_queue.put(json.loads("\n".join(data)))
                    elif event in self.topics:
                        self.topics[event].put(json.loads("\n".join(data)))
                    event, data = None, []
        except (OSError, ValueError):
            pass

    def wait_devices(self, wanted, timeout=5):
        """The first list that `wanted` accepts, waiting for it."""
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise AssertionError("no such devices list came; the last: %r" % (self._seen[-1:],))
            try:
                devices = self.lists.get(timeout=left)
            except queue.Empty:
                continue
            self._seen.append(devices)
            if wanted(devices):
                return devices

    def all_lists(self):
        while True:
            try:
                self._seen.append(self.lists.get_nowait())
            except queue.Empty:
                return self._seen

    def latest(self):
        return self.all_lists()[-1]

    def wait_topic(self, topic, wanted=lambda data: True, timeout=5):
        """The first `settings`, `continue` or `catalogue` event that `wanted`
        accepts."""
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise AssertionError("no such %s event came" % topic)
            try:
                data = self.topics[topic].get(timeout=left)
            except queue.Empty:
                continue
            if wanted(data):
                return data

    def topic_events(self, topic):
        found = []
        while True:
            try:
                found.append(self.topics[topic].get_nowait())
            except queue.Empty:
                return found

    def wait_command(self, timeout=5):
        return self.command_queue.get(timeout=timeout)

    def commands(self):
        found = []
        while True:
            try:
                found.append(self.command_queue.get_nowait())
            except queue.Empty:
                return found

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
        self.file.close()


class EncoderLog(unittest.TestCase):
    """media#stream_segment: the encoder chosen is written to the server's log
    at start."""

    def test_encoder_is_logged(self):
        tmp = tempfile.mkdtemp(prefix="mirutube-log-")
        self.addCleanup(shutil.rmtree, tmp, True)
        proc = subprocess.Popen(
            [sys.executable, "-m", "mirutube", tmp, "--port", str(free_port()), "--host", "127.0.0.1",
             "--cache", os.path.join(tmp, "cache"), "--data", os.path.join(tmp, "data"), "--quiet"],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        lines = []
        try:
            reader = threading.Thread(target=lambda: lines.extend(proc.stdout), daemon=True)
            reader.start()
            deadline = time.monotonic() + 120
            while not any("converting with" in line for line in lines) and time.monotonic() < deadline:
                time.sleep(0.1)
        finally:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            reader.join(10)
            proc.stdout.close()
        logged = [line for line in lines if "converting with" in line]
        self.assertEqual(len(logged), 1, lines)
        self.assertRegex(logged[0], r"converting with (h264_nvenc|h264_vaapi on /dev/dri/renderD\d+|h264_qsv|h264_amf|h264_videotoolbox|libx264)$")


class ContinueList(unittest.TestCase):
    """server's continue list: per playlist being watched the video to go on
    with, and every video outside any started and not finished, the most
    recently watched first (behaviour#resume#scenarios#one-card)."""

    CATALOGUE = {"playlists": [{"id": "S", "videos": ["S/1", "S/2", "S/3", "S/4", "S/5"]},
                               {"id": "P", "videos": ["P/a", "P/b"]}],
                 "standalone": ["x", "y"]}

    @staticmethod
    def at(position, updated, watched=False):
        return {"position": position, "watched": watched, "updated": updated}

    def test_started_one_beats_the_next_after_the_last_watched(self):
        positions = {"S/1": self.at(0, 1, True), "S/2": self.at(0, 2, True), "S/3": self.at(0, 3, True),
                     "S/4": self.at(0, 4, True), "S/5": self.at(490, 5)}
        self.assertEqual(continue_list(self.CATALOGUE, positions), ["S/5"])
        # one started earlier, then a later one watched to the end: the started one
        positions = {"S/2": self.at(300, 1), "S/4": self.at(0, 2, True)}
        self.assertEqual(continue_list(self.CATALOGUE, positions), ["S/2"])

    def test_next_not_watched_after_the_last_watched(self):
        positions = {"S/1": self.at(0, 1, True), "S/3": self.at(0, 2, True), "S/4": self.at(0, 0.5, True)}
        self.assertEqual(continue_list(self.CATALOGUE, positions), ["S/5"])
        # the ones after it all watched: the first one not watched
        positions = {"S/4": self.at(0, 1, True), "S/5": self.at(0, 2, True)}
        self.assertEqual(continue_list(self.CATALOGUE, positions), ["S/1"])

    def test_nothing_left_to_watch_has_no_entry(self):
        positions = {vid: self.at(0, i, True) for i, vid in enumerate(self.CATALOGUE["playlists"][0]["videos"])}
        self.assertEqual(continue_list(self.CATALOGUE, positions), [])
        # opened and left at the very start is not started
        self.assertEqual(continue_list(self.CATALOGUE, {"S/1": self.at(0, 1), "x": self.at(0, 2)}), [])

    def test_most_recently_watched_first(self):
        positions = {"S/1": self.at(100, 1), "S/2": self.at(0, 6, True), "P/a": self.at(50, 4),
                     "x": self.at(10, 5), "y": self.at(0, 7, True)}
        # S: S/1 is started (its last activity, S/2 finished, at 6); P at 4; x at 5; y watched
        self.assertEqual(continue_list(self.CATALOGUE, positions), ["S/1", "x", "P/a"])


class DevicesHub(unittest.TestCase):
    """server#fields#devices, without a network."""

    def test_stream_nobody_reads_keeps_only_the_latest(self):
        hub = Devices(grace=0.1)
        stuck = hub.connect("stuck", "Stuck")
        other = hub.connect("other", "Other")
        began = time.monotonic()
        for i in range(1000):
            hub.report("other", {"video": "v", "position": float(i), "playing": True})
            hub.command("stuck", {"action": "seek", "position": float(i)})
        self.assertLess(time.monotonic() - began, 2)  # never waits on a stream
        changed, commands = stuck.take(0)
        self.assertEqual(changed, {"devices"})  # one flag, the list taken when written
        self.assertEqual(len(commands), COMMANDS_KEPT)
        self.assertEqual(commands[-1], {"action": "seek", "position": 999.0})
        changed, commands = other.take(0)
        self.assertEqual(changed, {"devices"})
        self.assertEqual(commands, [])
        self.assertEqual(stuck.take(0), (set(), []))
        hub.close()
        self.assertTrue(stuck.closed and other.closed)

    def test_device_goes_after_its_last_stream(self):
        hub = Devices(grace=0.3)
        one = hub.connect("d", "Tab")
        two = hub.connect("d", "Tab")  # the same device in a second stream
        watcher = hub.connect("w", "Watcher")
        hub.disconnect(one)
        time.sleep(0.5)
        self.assertIn("d", [d["id"] for d in hub.snapshot()["devices"]])
        self.assertTrue(hub.command("d", {"action": "play"}))
        self.assertEqual(two.take(0)[1], [{"action": "play"}])
        hub.disconnect(two)
        watcher.take(0)
        time.sleep(0.5)
        self.assertNotIn("d", [d["id"] for d in hub.snapshot()["devices"]])
        self.assertTrue(watcher.take(0)[0])  # the others were told
        self.assertFalse(hub.command("d", {"action": "play"}))
        self.assertFalse(hub.report("d", {"video": None}))
        hub.close()


class Slots(unittest.TestCase):
    """At most two ffmpeg processes for one viewer (server#serve_stream)."""

    def test_third_piece_waits_for_a_slot(self):
        slots = ViewerSlots(2)
        never = lambda: False
        self.assertTrue(slots.acquire("a", never))
        self.assertTrue(slots.acquire("a", never))
        self.assertTrue(slots.acquire("b", never))  # another viewer has slots of its own
        got = []
        waiter = threading.Thread(target=lambda: got.append(slots.acquire("a", never)))
        waiter.start()
        time.sleep(0.5)
        self.assertEqual(got, [])
        self.assertEqual(slots.running("a"), 2)
        slots.release("a")
        waiter.join(5)
        self.assertEqual(got, [True])
        self.assertEqual(slots.running("a"), 2)

    def test_waiting_ends_when_nobody_waits_any_more(self):
        slots = ViewerSlots(2)
        slots.acquire("a", lambda: False)
        slots.acquire("a", lambda: False)
        began = time.monotonic()
        self.assertFalse(slots.acquire("a", lambda: time.monotonic() - began > 0.3))
        self.assertEqual(slots.running("a"), 2)


# ------------------------------------------------------------- QR code
#
# A reader of QR codes written apart from mirutube.qr, from the standard
# (ISO/IEC 18004) — its tables typed in from it rather than computed — so a
# code the server makes is shown to be read back, not only to be made the way
# the encoder thinks it should be.

# version -> the rows and columns alignment patterns are centred on
ALIGNMENT = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38],
             8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}
# version -> (codewords in all, error correction codewords per block, blocks) at level H
BLOCKS_H = {1: (26, 17, 1), 2: (44, 28, 1), 3: (70, 22, 2), 4: (100, 16, 4), 5: (134, 22, 4),
            6: (172, 28, 4), 7: (196, 26, 5), 8: (242, 26, 6), 9: (292, 24, 8), 10: (346, 28, 8)}
VERSION_INFO = {7: 0x07C94, 8: 0x085BC, 9: 0x09A99, 10: 0x0A4D3}
LEVELS = {0b01: "L", 0b00: "M", 0b11: "Q", 0b10: "H"}
READ_MASKS = [lambda r, c: (r + c) % 2 == 0, lambda r, c: r % 2 == 0, lambda r, c: c % 3 == 0,
              lambda r, c: (r + c) % 3 == 0, lambda r, c: (r // 2 + c // 3) % 2 == 0,
              lambda r, c: (r * c) % 2 + (r * c) % 3 == 0, lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
              lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0]


def _gf_tables():
    exp, log, x = [0] * 255, [0] * 256, 1
    for i in range(255):
        exp[i], log[x] = x, i
        x = x << 1 ^ (0x11D if x & 0x80 else 0)
    return exp, log


GF_EXP, GF_LOG = _gf_tables()


def syndromes(block):
    """The block's codeword polynomial at a^0 .. a^(n-1): all zero for a
    block without an error, n being its error correction codewords."""
    def at(power):
        value = 0
        for b in block:
            value = (GF_EXP[(GF_LOG[value] + power) % 255] if value else 0) ^ b
        return value
    return at


def bch_format(data):
    """15 bits of format information for 5 bits of data, unmasked."""
    rem = data << 10
    for bit in range(14, 9, -1):
        if rem >> bit & 1:
            rem ^= 0x537 << (bit - 10)
    return data << 10 | rem


def read_qr(m):
    """(text, level, mask, version) of a QR code given as rows of booleans,
    raising AssertionError where it does not hold to the standard."""
    size = len(m)
    version = (size - 17) // 4
    assert size == 17 + 4 * version and all(len(row) == size for row in m)
    # finder patterns and timing patterns
    for r0, c0 in ((0, 0), (0, size - 7), (size - 7, 0)):
        for r in range(7):
            for c in range(7):
                assert m[r0 + r][c0 + c] == (max(abs(r - 3), abs(c - 3)) != 2), (r0, c0)
    for i in range(8, size - 8):
        assert m[6][i] == m[i][6] == (i % 2 == 0)
    # format information, both copies
    first = [m[8][c] for c in (0, 1, 2, 3, 4, 5, 7, 8)] + [m[r][8] for r in (7, 5, 4, 3, 2, 1, 0)]
    second = [m[r][8] for r in range(size - 1, size - 8, -1)] + [m[8][c] for c in range(size - 8, size)]
    assert first == second, "the two copies of the format information differ"
    word = int("".join("1" if b else "0" for b in first), 2) ^ 0x5412
    assert bch_format(word >> 10) == word, "format information"
    level, mask = LEVELS[word >> 13], word >> 10 & 7
    assert m[size - 8][8], "the dark module"
    # which modules hold data
    function = [[False] * size for _ in range(size)]
    def mark(r0, c0, h, w):
        for r in range(max(r0, 0), min(r0 + h, size)):
            for c in range(max(c0, 0), min(c0 + w, size)):
                function[r][c] = True
    mark(0, 0, 9, 9), mark(0, size - 8, 9, 8), mark(size - 8, 0, 8, 9)
    mark(6, 0, 1, size), mark(0, 6, size, 1)
    centres = ALIGNMENT[version]
    corners = {(centres[0], centres[0]), (centres[0], centres[-1]), (centres[-1], centres[0])} if centres else ()
    for r in centres:
        for c in centres:
            if (r, c) not in corners:  # where the finder patterns are
                mark(r - 2, c - 2, 5, 5)
                for dr in range(-2, 3):
                    for dc in range(-2, 3):
                        assert m[r + dr][c + dc] == (max(abs(dr), abs(dc)) != 1), "alignment at %d,%d" % (r, c)
    if version >= 7:
        mark(0, size - 11, 6, 3), mark(size - 11, 0, 3, 6)
        bits = [m[i // 3][size - 11 + i % 3] for i in range(18)]
        assert bits == [m[size - 11 + i % 3][i // 3] for i in range(18)]
        assert sum(b << i for i, b in enumerate(bits)) == VERSION_INFO[version], "version information"
    # the bits, two columns at a time from the right, up and down in turn
    bits = []
    col, upward = size - 1, True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for r in rows:
            for c in (col, col - 1):
                if not function[r][c]:
                    bits.append(m[r][c] ^ READ_MASKS[mask](r, c))
        col, upward = col - 2, not upward
    total, ec, count = BLOCKS_H[version]
    assert len(bits) // 8 == total
    stream = [int("".join("1" if b else "0" for b in bits[i * 8:i * 8 + 8]), 2) for i in range(total)]
    # de-interleaved into blocks, the short ones first
    short = total // count - ec
    longs = total % count
    lengths = [short] * (count - longs) + [short + 1] * longs
    blocks = [[] for _ in range(count)]
    at = 0
    for i in range(short + 1):
        for b in range(count):
            if i < lengths[b]:
                blocks[b].append(stream[at])
                at += 1
    for i in range(ec):
        for b in range(count):
            blocks[b].append(stream[at])
            at += 1
    for b in blocks:
        at_power = syndromes(b)
        assert all(at_power(i) == 0 for i in range(ec)), "error correction does not hold"
    data = [w for b, n in zip(blocks, lengths) for w in b[:n]]
    # byte mode, then padding
    bits = "".join("{:08b}".format(w) for w in data)
    assert bits[:4] == "0100", "not byte mode"
    count_bits = 8 if version < 10 else 16
    length = int(bits[4:4 + count_bits], 2)
    start = 4 + count_bits
    text = bytes(int(bits[start + 8 * i:start + 8 * i + 8], 2) for i in range(length))
    rest = bits[start + 8 * length:]
    assert set(rest[:4]) <= {"0"}, "terminator"
    pad = rest[4 + (-(start + 8 * length + 4) % 8):] if len(rest) >= 4 else ""
    expected = "".join("{:08b}".format((0xEC, 0x11)[i % 2]) for i in range(len(pad) // 8))
    assert pad == expected, "padding"
    return text.decode("utf-8"), level, mask, version


class QrCode(unittest.TestCase):
    """server#serve_address: the QR code is read back by a reader written
    apart from the encoder; its style keeps every module where it was."""

    TEXTS = ["http://192.168.31.5:8800", "https://192.168.100.200:8443", "x", "http://10.0.0.2:8800",
             "https://mirutube.local:8800/" + "a" * 34, "Привет, мир — " * 4, "http://[fe80::1]:8800"]

    def test_read_back(self):
        versions = set()
        for text in self.TEXTS:
            m = qr.encode(text)
            read, level, mask, version = read_qr(m)
            self.assertEqual((read, level), (text, "H"), text)
            # the smallest version that holds it
            self.assertTrue(version == 1 or len(text.encode()) > qr.data_codewords(version - 1) - 2 - (version > 10))
            versions.add(version)
        self.assertTrue({1, 3, 4, 7, 10} <= versions, versions)

    def test_every_mask_reads_and_the_lowest_penalty_is_chosen(self):
        text = self.TEXTS[0]
        penalties = []
        for mask in range(8):
            m = qr.encode(text, mask=mask)
            self.assertEqual(read_qr(m)[:3], (text, "H", mask))
            penalties.append(qr.penalty(m))
        self.assertEqual(read_qr(qr.encode(text))[2], penalties.index(min(penalties)))

    def test_alignment_and_versions_follow_the_standard(self):
        for version, centres in ALIGNMENT.items():
            self.assertEqual(qr.alignment_positions(version), centres, version)
            self.assertEqual(qr.raw_modules(version) // 8, BLOCKS_H[version][0], version)
        self.assertEqual(qr.alignment_positions(32), [6, 34, 60, 86, 112, 138])
        self.assertEqual([qr.version_bits(v) for v in VERSION_INFO], list(VERSION_INFO.values()))
        # byte capacity at level H, from the standard's table
        capacity = {1: 7, 2: 14, 3: 24, 4: 34, 5: 44, 7: 64, 10: 119, 40: 1273}
        for version, n in capacity.items():
            self.assertEqual(qr.smallest_version(b"a" * n), version)
            if version < 40:
                self.assertGreater(qr.smallest_version(b"a" * (n + 1)), version)

    def test_svg_draws_the_modules(self):
        for text in self.TEXTS[:5]:
            body = qr.svg(text).encode("utf-8")
            svg = ElementTree.fromstring(body)
            self.assertEqual(matrix_of_svg(body), masked_for_svg(qr.encode(text)), text)
            ns = "{http://www.w3.org/2000/svg}"
            gradients = [g.get("id") for g in svg.iter(ns + "linearGradient")]
            self.assertEqual(gradients, ["mirutube-qr-dots"])
            # the mark: the folder, and the sakura's five petals
            petals = [p for p in svg.iter(ns + "path") if (p.get("transform") or "").startswith("rotate(")]
            self.assertEqual(len(petals), 5)
            size = len(qr.encode(text))
            # the mark covers little enough for error correction H to read through
            first, last = qr.mark_area(size)
            self.assertLessEqual((last - first + 1) ** 2, 0.06 * size * size)
        self.assertIn("&amp;", qr.svg("http://a/?x=1&y=2"))

    def test_read_by_quirc_where_there_is_one(self):
        """The styled code read by another reader: libquirc, given the SVG as
        ffmpeg draws it with librsvg; skipped where either is missing."""
        lib = ctypes.util.find_library("quirc")
        if lib is None or "librsvg" not in subprocess.run(
                ["ffmpeg", "-hide_banner", "-decoders"], capture_output=True, text=True).stdout:
            self.skipTest("no libquirc, or no SVG in ffmpeg, here")
        tmp = tempfile.mkdtemp(prefix="mirutube-qr-")
        self.addCleanup(shutil.rmtree, tmp, True)
        for text in self.TEXTS[:4]:
            svg = os.path.join(tmp, "code.svg")
            with open(svg, "w") as f:
                f.write(qr.svg(text))
            # dark dots on a light card, read as they are: no reader needs
            # the image turned over
            raw = subprocess.run(["ffmpeg", "-v", "error", "-width", "800", "-height", "800", "-i", svg,
                                  "-vf", "format=gray", "-f", "rawvideo", "pipe:1"],
                                 capture_output=True, check=True).stdout
            self.assertEqual(len(raw), 800 * 800)
            self.assertEqual(quirc_read(lib, raw, 800, 800), [text])


def quirc_read(lib, gray, width, height):
    """The texts libquirc reads in a grayscale image."""
    quirc = ctypes.CDLL(lib)

    class Code(ctypes.Structure):
        _fields_ = [("corners", ctypes.c_int * 8), ("size", ctypes.c_int), ("cells", ctypes.c_uint8 * 3917)]

    class Data(ctypes.Structure):
        _fields_ = [("version", ctypes.c_int), ("ecc_level", ctypes.c_int), ("mask", ctypes.c_int),
                    ("data_type", ctypes.c_int), ("payload", ctypes.c_uint8 * 8896),
                    ("payload_len", ctypes.c_int), ("eci", ctypes.c_uint32)]
    quirc.quirc_new.restype = ctypes.c_void_p
    quirc.quirc_resize.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    quirc.quirc_begin.restype = ctypes.POINTER(ctypes.c_uint8)
    quirc.quirc_begin.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    quirc.quirc_end.argtypes = quirc.quirc_count.argtypes = quirc.quirc_destroy.argtypes = [ctypes.c_void_p]
    quirc.quirc_extract.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(Code)]
    quirc.quirc_decode.argtypes = [ctypes.POINTER(Code), ctypes.POINTER(Data)]
    q = quirc.quirc_new()
    try:
        quirc.quirc_resize(q, width, height)
        ctypes.memmove(quirc.quirc_begin(q, None, None), gray, width * height)
        quirc.quirc_end(q)
        texts = []
        for i in range(quirc.quirc_count(q)):
            code, data = Code(), Data()
            quirc.quirc_extract(q, i, ctypes.byref(code))
            if quirc.quirc_decode(ctypes.byref(code), ctypes.byref(data)) == 0:
                texts.append(bytes(data.payload[:data.payload_len]).decode("utf-8"))
        return texts
    finally:
        quirc.quirc_destroy(q)


class LocalRequests(unittest.TestCase):
    """decisions#settings-local: a request from the computer MiruTube runs on
    comes from the very address it reached the server at."""

    def test_own_address(self):
        self.assertTrue(is_local_request("127.0.0.1", "127.0.0.1"))
        self.assertTrue(is_local_request("192.168.31.5", "192.168.31.5"))
        self.assertTrue(is_local_request("::ffff:192.168.31.5", "192.168.31.5"))
        self.assertTrue(is_local_request("::1", "::1"))
        self.assertFalse(is_local_request("192.168.31.20", "192.168.31.5"))
        self.assertFalse(is_local_request("127.0.0.2", "127.0.0.1"))


class SavedCatalogue(unittest.TestCase):
    """library#fields#saved_catalogue: a library made again — as the server
    is on a restart — serves the catalogue the last one saved before its own
    first scan ends, tells when that scan finds something new, and ignores
    one saved for other library folders (behaviour#library-at-once)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mirutube-saved-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.lib = os.path.join(self.tmp, "library")
        os.makedirs(os.path.join(self.lib, "Show"))
        for n in (1, 2):
            open(os.path.join(self.lib, "Show", "Show - %02d.mp4" % n), "w").close()
        self.saved = os.path.join(self.tmp, "cache", "catalogue.json")
        self.probing = threading.Event()
        self.probing.set()

    def probe(self, path):
        self.probing.wait(10)
        return {"duration": 60.0, "container": "mov,mp4", "video_codec": "h264", "pix_fmt": "yuv420p",
                "audio_codec": "aac", "audio": [], "subtitles": []}

    def test_restart_serves_the_saved_catalogue_at_once(self):
        first = Library([self.lib], probe=self.probe, saved=self.saved).catalogue()
        self.assertTrue(os.path.exists(self.saved))
        open(os.path.join(self.lib, "Show", "Show - 03.mp4"), "w").close()
        self.probing.clear()  # the next scan cannot end until this is set
        changed = threading.Event()
        library = Library([self.lib], probe=self.probe, saved=self.saved, on_change=changed.set)
        library.refresh()
        started = time.monotonic()
        self.assertEqual(library.catalogue(), first)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertFalse(changed.is_set())
        self.probing.set()
        self.assertTrue(changed.wait(10))
        self.assertIn("library/Show/Show - 03.mp4", library.catalogue()["videos"])
        # and what that scan found is what the next start begins with
        again = Library([self.lib], probe=self.probe, saved=self.saved)
        self.assertIn("library/Show/Show - 03.mp4", again.catalogue()["videos"])

    def test_file_placed_during_a_scan_is_scanned_again(self):
        # downloads: a file put in place while a scan runs that has passed it
        library = Library([self.lib], probe=self.probe)
        library.catalogue()
        entered = threading.Event()
        probe = self.probe

        def slow(path):
            entered.set()
            return probe(path)
        library.probe = slow
        self.probing.clear()
        library.refresh()
        self.assertTrue(entered.wait(5))  # the folder is listed: the new file is not in this scan
        open(os.path.join(self.lib, "Show", "Show - 03.mp4"), "w").close()
        library.refresh(changed=True)
        self.probing.set()
        deadline = time.monotonic() + 10
        while "library/Show/Show - 03.mp4" not in library.catalogue()["videos"]:
            self.assertLess(time.monotonic(), deadline, "the file was not scanned")
            time.sleep(0.05)

    def test_saved_for_other_folders_is_not_used(self):
        Library([self.lib], probe=self.probe, saved=self.saved).catalogue()
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        open(os.path.join(other, "Clip.mp4"), "w").close()
        cat = Library([other], probe=self.probe, saved=self.saved).catalogue()
        self.assertEqual(list(cat["videos"]), ["other/Clip.mp4"])

    def test_unreadable_saved_catalogue_is_scanned_again(self):
        os.makedirs(os.path.dirname(self.saved))
        with open(self.saved, "w") as f:
            f.write("{not json")
        cat = Library([self.lib], probe=self.probe, saved=self.saved).catalogue()
        self.assertIn("library/Show/Show - 01.mp4", cat["videos"])


class FirstStart(unittest.TestCase):
    """store#fields#library_folders: the folder given on the command line is
    added on the first start, and one removed in settings stays removed."""

    def test_added_once(self):
        tmp = tempfile.mkdtemp(prefix="mirutube-store-")
        self.addCleanup(shutil.rmtree, tmp, True)
        store = Store(tmp)
        self.assertFalse(store.first_start(None))
        self.assertTrue(store.first_start("/videos"))
        self.assertEqual(store.library_folders(), ["/videos"])
        store.add_library_folder("/more")
        store.remove_library_folder("/videos")
        self.assertFalse(Store(tmp).first_start("/videos"))
        self.assertEqual(Store(tmp).library_folders(), ["/more"])


class Config(unittest.TestCase):
    """server#fields#config_file, #data_folder: the server needs no arguments
    — how it serves is read from the config file, an option on the command
    line wins over it — and what it keeps lives in the data folder, a store
    from the cache folder moved there."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mirutube-config-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, text):
        path = os.path.join(self.tmp, "mirutube.conf")
        with open(path, "w") as f:
            f.write(text)
        return path

    def test_keys_that_are_set(self):
        self.assertEqual(read_config(os.path.join(self.tmp, "missing.conf")), {})
        self.assertEqual(read_config(self.write("[server]\nport = 9000\nhost =\n")), {"port": "9000"})
        with self.assertRaises(ValueError):
            read_config(self.write("[server]\nprot = 9000\n"))

    def test_old_store_moves_to_the_data_folder(self):
        cache, data = os.path.join(self.tmp, "cache"), os.path.join(self.tmp, "data")
        old = Store(cache)
        old.set_position("Videos/a.mp4", 42.0, False)
        _move_old_store(cache, data)
        self.assertFalse(os.path.exists(os.path.join(cache, "mirutube.db")))
        self.assertEqual(Store(data).position("Videos/a.mp4")["position"], 42.0)
        _move_old_store(cache, data)  # nothing left to move: nothing happens
        self.assertTrue(os.path.exists(os.path.join(data, "mirutube.db")))

    def test_server_started_from_the_config_file(self):
        lib = os.path.join(self.tmp, "Videos")
        os.makedirs(lib)
        port, other = free_port(), free_port()
        conf = self.write("[server]\nport = %d\nhost = 127.0.0.1\ndata = %s\ncache = %s\n"
                          % (other, os.path.join(self.tmp, "data"), os.path.join(self.tmp, "cache")))
        proc = subprocess.Popen(
            [sys.executable, "-m", "mirutube", lib, "--config", conf, "--port", str(port), "--quiet"],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def stop():
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        self.addCleanup(stop)
        deadline = time.monotonic() + 20
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            except OSError:
                if proc.poll() is not None or time.monotonic() > deadline:
                    raise
                time.sleep(0.2)
        # the port on the command line won; the folders came from the file
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "data", "mirutube.db")))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "data", "mirutube-ca.crt")))
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", other), timeout=1).close()


class HostRoot(unittest.TestCase):
    """server#fields#host_root: in service's container the computer's file
    system is under `/host`; a library folder is added and shown by the path
    the computer gives it (behaviour#runs-as-service#scenarios#other-disk)."""

    def test_folder_added_by_its_path_on_the_computer(self):
        tmp = tempfile.mkdtemp(prefix="mirutube-host-")
        self.addCleanup(shutil.rmtree, tmp, True)
        host = os.path.join(tmp, "host")
        series = os.path.join(host, "mnt", "media", "Series")
        os.makedirs(series)
        for n in (1, 2):
            ffmpeg("-f", "lavfi", "-i", "color=c=blue:size=32x32:rate=1:duration=3", "-c:v", "libx264",
                   "-preset", "ultrafast", "-pix_fmt", "yuv420p", os.path.join(series, "Series - %02d.mp4" % n))
        port = free_port()
        env = dict(os.environ, MIRUTUBE_HOST_ROOT=host)
        proc = subprocess.Popen(
            [sys.executable, "-m", "mirutube", "--port", str(port), "--host", "127.0.0.1",
             "--data", os.path.join(tmp, "data"), "--cache", os.path.join(tmp, "cache"),
             "--config", os.path.join(tmp, "none.conf"), "--quiet"],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def stop():
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        self.addCleanup(stop)
        def call(method, path, body=None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
            try:
                data = json.dumps(body).encode() if body is not None else None
                conn.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
                res = conn.getresponse()
                body = res.read()
                return res.status, json.loads(body) if res.getheader("Content-Type", "").startswith(
                    "application/json") else body
            finally:
                conn.close()
        deadline = time.monotonic() + 20
        while True:
            try:
                call("GET", "/api/ping")
                break
            except OSError:
                if proc.poll() is not None or time.monotonic() > deadline:
                    raise
                time.sleep(0.2)
        status, settings = call("POST", "/api/settings/library-folders", {"action": "add", "folder": "/mnt/media/Series"})
        self.assertEqual(status, 200, settings)
        self.assertEqual(folders_of(settings), [{"name": "Series", "folder": "/mnt/media/Series"}])
        self.assertEqual(call("POST", "/api/settings/library-folders",
                              {"action": "add", "folder": "/mnt/nothing"})[0], 404)
        _, cat = call("GET", "/api/catalogue")
        self.assertEqual(sorted(cat["videos"]), sorted(I("Series/Series - %02d.mp4" % n) for n in (1, 2)))
        status, _ = call("GET", "/thumb/" + I("Series/Series - 01.mp4"))
        self.assertEqual(status, 200)


class AddressChange(unittest.TestCase):
    """certificates#server_certificate: made again, and put in place, when
    this computer's address changes or appears after the server started."""

    def test_certificate_follows_the_address(self):
        import mirutube.server as server_module
        tmp = tempfile.mkdtemp(prefix="mirutube-address-")
        self.addCleanup(shutil.rmtree, tmp, True)
        addresses = [None, "192.168.1.20", "192.168.1.20", "192.168.1.31"]  # the last one stays
        seen = []
        real_check, real_lan = server_module.ADDRESS_CHECK_EVERY, server_module.lan_address
        self.addCleanup(setattr, server_module, "ADDRESS_CHECK_EVERY", real_check)
        self.addCleanup(setattr, server_module, "lan_address", real_lan)
        server_module.ADDRESS_CHECK_EVERY = 0.01
        stop = threading.Event()
        server_module.lan_address = lambda: addresses.pop(0) if len(addresses) > 1 else addresses[0]

        class Context:
            def load_cert_chain(self, cert, key):
                with open(cert, "rb") as f:
                    seen.append(f.read())

        class Fake:
            ssl_context = Context()
            certificate_names = server_module.certificates.default_names([None])

        fake = Fake()
        thread = threading.Thread(target=server_module.watch_address, args=(fake, tmp, None, stop))
        thread.start()
        deadline = time.monotonic() + 5
        while "192.168.1.31" not in fake.certificate_names and time.monotonic() < deadline:
            time.sleep(0.01)
        stop.set()
        thread.join()
        self.assertIn("192.168.1.31", fake.certificate_names)
        self.assertEqual(len(seen), 2)  # the address appeared, then changed; the same one twice is kept


class Https(unittest.TestCase):
    """server: HTTPS with a certificate and key given on the command line,
    in place of MiruTube's own (decisions#own-ca)."""

    def test_served_with_a_certificate_given(self):
        if shutil.which("openssl") is None:
            self.skipTest("no openssl to make a certificate with")
        tmp = tempfile.mkdtemp(prefix="mirutube-https-")
        self.addCleanup(shutil.rmtree, tmp, True)
        lib = os.path.join(tmp, "Videos")
        os.makedirs(lib)
        ffmpeg("-f", "lavfi", "-i", "color=c=blue:size=32x32:rate=1:duration=5", "-c:v", "libx264",
               "-preset", "ultrafast", "-pix_fmt", "yuv420p", os.path.join(lib, "Clip.mp4"))
        cert, key = os.path.join(tmp, "cert.pem"), os.path.join(tmp, "key.pem")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
                        "-keyout", key, "-out", cert],
                       check=True, capture_output=True)
        port = free_port()
        proc = subprocess.Popen(
            [sys.executable, "-m", "mirutube", lib, "--port", str(port), "--host", "127.0.0.1",
             "--cache", os.path.join(tmp, "cache"), "--data", os.path.join(tmp, "data"),
             "--cert", cert, "--key", key, "--quiet"],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def stop():
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        self.addCleanup(stop)
        context = ssl.create_default_context(cafile=cert)
        def get(path, headers=None, secure=True):
            conn = http.client.HTTPSConnection("127.0.0.1", port, timeout=30, context=context) if secure \
                else http.client.HTTPConnection("127.0.0.1", port, timeout=30)
            try:
                conn.request("GET", path, headers=headers or {})
                res = conn.getresponse()
                return res.status, res.read()
            finally:
                conn.close()
        deadline = time.monotonic() + 20
        while True:
            try:
                status, body = get("/api/catalogue")
                break
            except OSError:
                if proc.poll() is not None or time.monotonic() > deadline:
                    raise
                time.sleep(0.2)
        self.assertEqual(status, 200)
        clip = I("Videos/Clip.mp4")
        self.assertIn(clip, json.loads(body)["videos"])
        status, body = get("/video/" + clip, {"Range": "bytes=0-9"})
        self.assertEqual((status, len(body)), (206, 10))
        # plain HTTP is answered on the same port too
        self.assertEqual(get("/api/ping", secure=False)[0], 200)
        # no authority of MiruTube's is made or offered with a certificate given
        self.assertEqual(get("/mirutube.crt", secure=False)[0], 404)
        self.assertFalse(os.path.exists(os.path.join(tmp, "data", "mirutube-ca.crt")))


if __name__ == "__main__":
    unittest.main()
