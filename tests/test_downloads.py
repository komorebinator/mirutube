"""downloads: a torrent of a series downloaded from a seeder on this computer
comes in its order and each episode enters the library as it completes, goes
on seeding from there, and stops seeding with its files kept; a torrent
cancelled takes back what it brought; a plain link to a video is fetched by
yt-dlp into the library (behaviour#uploads).

Needs libtorrent and yt-dlp, which the image has; each part is skipped where
its tool is missing — run them in the image (tests#container)."""

import functools
import http.server
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest

from mirutube import downloads
from mirutube.downloads import Downloads
from mirutube.library import Library
from tests.test_media import ffmpeg

lt = downloads.lt
NO_NETWORK = {"enable_dht": False, "enable_lsd": False, "enable_upnp": False, "enable_natpmp": False}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_videos(folder, names, seconds=2):
    os.makedirs(folder, exist_ok=True)
    for name in names:
        ffmpeg("-f", "lavfi", "-i", "testsrc=size=64x36:rate=10:duration=%d" % seconds, "-c:v", "libx264",
               "-preset", "ultrafast", "-pix_fmt", "yuv420p", os.path.join(folder, name))


def wait(test, what, timeout=60):
    deadline = time.monotonic() + timeout
    while not what():
        if time.monotonic() > deadline:
            raise AssertionError("it did not happen in %d seconds" % timeout)
        time.sleep(0.2)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mirutube-downloads-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.lib = os.path.join(self.tmp, "library")
        os.makedirs(self.lib)
        self.library = Library([self.lib], probe=lambda path: {"duration": 2.0})
        self.seed = {"seed_torrents": True}
        self.dl = Downloads(os.path.join(self.tmp, "data"), self.library, lambda: dict(self.seed), lambda: None,
                            torrent_settings=dict(NO_NETWORK, listen_interfaces="127.0.0.1:%d" % free_port()))
        self.addCleanup(self.dl.close)


@unittest.skipIf(lt is None, "libtorrent is not installed")
class Torrents(Base):
    def make_torrent(self):
        """A series of three episodes with a subtitles folder, and a seeder
        for it on this computer."""
        source = os.path.join(self.tmp, "source")
        show = os.path.join(source, "Show")
        make_videos(show, ["Show - %02d.mp4" % n for n in (1, 2, 3)])
        os.makedirs(os.path.join(show, "Subs"))
        with open(os.path.join(show, "Subs", "Show - 01.srt"), "w") as f:
            f.write("1\n00:00:00,500 --> 00:00:01,500\nHello\n")
        fs = lt.file_storage()
        lt.add_files(fs, show)
        made = lt.create_torrent(fs, 16 * 1024)
        lt.set_piece_hashes(made, source)
        data = lt.bencode(made.generate())
        seeder = lt.session(dict(NO_NETWORK, listen_interfaces="127.0.0.1:%d" % free_port()))
        params = lt.add_torrent_params()
        params.ti = lt.torrent_info(lt.bdecode(data))
        params.save_path = source
        handle = seeder.add_torrent(params)
        wait(self, lambda: handle.status().is_seeding, 30)
        self.seeder = seeder  # kept alive for the test
        return data, seeder.listen_port()

    def test_episodes_arrive_and_seed(self):
        # behaviour#uploads#scenarios#first-episode
        data, port = self.make_torrent()
        job = self.dl.start_torrent("library", data=data)
        self.assertEqual(job["into"], "library/Show")
        self.dl.live[job["id"]]["handle"].connect_peer(("127.0.0.1", port))
        wait(self, lambda: self.dl.job(job["id"])["state"] == "seeding")
        for name in ("Show - 01.mp4", "Show - 02.mp4", "Show - 03.mp4", "Subs/Show - 01.srt"):
            self.assertTrue(os.path.isfile(os.path.join(self.lib, "Show", name)), name)
        # nothing is left incoming: every file moved into the library
        left = [f for _, _, files in os.walk(os.path.join(self.lib, ".mirutube-incoming")) for f in files]
        self.assertEqual(left, [])
        # seeding goes on from the files in the library
        handle = self.dl.live[job["id"]]["handle"]
        self.assertTrue(handle.status().is_seeding)
        self.dl.stop_seeding(job["id"])
        self.assertEqual(self.dl.job(job["id"])["state"], "done")
        self.assertTrue(os.path.isfile(os.path.join(self.lib, "Show", "Show - 03.mp4")))

    def test_cancel_takes_back_everything(self):
        # behaviour#uploads#scenarios#cancel: no peer, so nothing finishes
        data, _ = self.make_torrent()
        job = self.dl.start_torrent("library", data=data)
        self.dl.cancel(job["id"])
        self.assertEqual(self.dl.job(job["id"])["state"], "cancelled")
        self.assertFalse(os.path.exists(os.path.join(self.lib, "Show")))
        self.assertFalse(os.path.exists(os.path.join(self.lib, ".mirutube-incoming", job["id"])))

    def test_not_a_torrent(self):
        with self.assertRaises(downloads.NotSupported):
            self.dl.start_torrent("library", data=b"not a torrent")


@unittest.skipIf(shutil.which("yt-dlp") is None, "yt-dlp is not installed")
class Links(Base):
    def test_a_link_to_a_video(self):
        # behaviour#uploads#scenarios#link, with a plain link to a file
        served = os.path.join(self.tmp, "served")
        make_videos(served, ["clip.mp4"])
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=served)
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        job = self.dl.start_link("library", "http://127.0.0.1:%d/clip.mp4" % httpd.server_address[1])
        wait(self, lambda: self.dl.job(job["id"])["state"] in ("done", "failed"))
        self.assertEqual(self.dl.job(job["id"])["state"], "done", self.dl.job(job["id"]).get("reason"))
        placed = [f for f in os.listdir(self.lib) if f.endswith(".mp4")]
        self.assertEqual(len(placed), 1, os.listdir(self.lib))

    def test_a_link_nothing_reads(self):
        # behaviour#uploads#scenarios#not-supported
        job = self.dl.start_link("library", "http://127.0.0.1:%d/nothing" % free_port())
        wait(self, lambda: self.dl.job(job["id"])["state"] in ("done", "failed"))
        state = self.dl.job(job["id"])
        self.assertEqual(state["state"], "failed")
        self.assertIn("not supported", state["reason"])
        self.assertEqual([f for f in os.listdir(self.lib) if not f.startswith(".")], [])


class Names(unittest.TestCase):
    def test_names_are_cleaned(self):
        # behaviour#library-only#scenarios#upload-dot-dot
        self.assertEqual(downloads.clean_path("../../.bashrc"), ".bashrc")
        self.assertEqual(downloads.clean_path("/etc/passwd"), "etc/passwd")
        self.assertEqual(downloads.clean_path("a\\..\\..\\b.mp4"), "a/b.mp4")
        self.assertEqual(downloads.clean_path('Show: "Best"?.mkv'), "Show_ _Best__.mkv")
        self.assertIsNone(downloads.clean_path("../.."))
