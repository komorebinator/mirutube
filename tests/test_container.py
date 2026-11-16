"""Runs image as service does — the one tests/image.py picks: named by
MIRUTUBE_TEST_IMAGE and checked to be main's, else built from the working
tree — against a test library outside the user's home: the page and a video
are served, a folder
added in settings by its path on the computer is scanned, what the server
keeps is written to the mounted data folder and owned by the user, ffmpeg
in the image has the VAAPI encoders and NVENC, a piece of a video is
transcoded on this computer's GPU, libtorrent, yt-dlp and Deno are there and
tests#downloads pass inside the image; Quadlet, run as a dry run on
service and its NVIDIA and experimental drop-ins, makes a user service with the network, user,
groups, mounts and devices described (behaviour#runs-as-service).

Needs Podman; skipped where there is none."""

import http.client
import json
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import unittest
import uuid

from mirutube.library import short_ids
from tests.image import ROOT, gpu_options, image_under_test, run_mirutube
from tests.test_media import ffmpeg

QUADLET = ["/usr/libexec/podman/quadlet", "/usr/lib/podman/quadlet",
           "/usr/lib/systemd/user-generators/podman-user-generator"]


def podman(*args, check=True):
    return subprocess.run(["podman", *args], capture_output=True, text=True, check=check)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def I(path):
    return short_ids([path])[path]


@unittest.skipIf(shutil.which("podman") is None, "no Podman to build and run the image with")
class Container(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = image_under_test()
        # outside the user's home, as a second disk would be
        cls.tmp = tempfile.mkdtemp(prefix="mirutube-container-", dir="/tmp")
        cls.lib = os.path.join(cls.tmp, "media", "Series")
        os.makedirs(cls.lib)
        for n in (1, 2):
            ffmpeg("-f", "lavfi", "-i", "color=c=blue:size=64x36:rate=5:duration=4", "-c:v", "libx264",
                   "-preset", "ultrafast", "-pix_fmt", "yuv420p", os.path.join(cls.lib, "Series - %02d.mp4" % n))
        # converted as it plays: MPEG-4 Part 2, which no browser decodes
        cls.clips = os.path.join(cls.tmp, "media", "Clips")
        os.makedirs(cls.clips)
        ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:duration=12", "-c:v", "mpeg4",
               os.path.join(cls.clips, "Old.avi"))
        cls.folders = {name: os.path.join(cls.tmp, name) for name in ("data", "cache", "config")}
        for folder in cls.folders.values():
            os.makedirs(folder)
        cls.port = free_port()
        cls.name = "mirutube-test-" + uuid.uuid4().hex[:8]
        run_mirutube(cls.name, cls.folders, cls.port)

    @classmethod
    def tearDownClass(cls):
        podman("rm", "-f", "-t", "2", cls.name, check=False)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def request(cls, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=30)
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

    def test_folder_added_by_its_path_on_the_computer(self):
        # behaviour#runs-as-service#scenarios#other-disk
        status, _, body = self.request("POST", "/api/settings/library-folders", {"action": "add", "folder": self.lib})
        self.assertEqual(status, 200, body)
        self.assertIn({"name": "Series", "folder": self.lib},
                      [{k: f[k] for k in ("name", "folder")} for f in json.loads(body)["library_folders"]])
        cat = json.loads(self.request("GET", "/api/catalogue")[2])
        episode = I("Series/Series - 01.mp4")
        self.assertIn(episode, cat["videos"])
        status, _, body = self.request("GET", "/video/" + episode, headers={"Range": "bytes=0-9"})
        self.assertEqual((status, len(body)), (206, 10))
        self.assertEqual(self.request("GET", "/thumb/" + episode)[0], 200)

    def test_page_served(self):
        status, _, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"/static/app.js", body)

    def test_kept_in_the_data_folder_and_owned_by_the_user(self):
        data = self.folders["data"]
        for name in ("mirutube.db", "mirutube-ca.crt", "mirutube-ca.key", "server.crt"):
            path = os.path.join(data, name)
            self.assertTrue(os.path.exists(path), name)
            self.assertEqual(os.stat(path).st_uid, os.getuid(), name)

    def test_https_with_the_authority(self):
        pem = self.request("GET", "/mirutube.crt")[2]
        cafile = os.path.join(self.tmp, "trusted.crt")
        with open(cafile, "wb") as f:
            f.write(pem)
        conn = http.client.HTTPSConnection("127.0.0.1", self.port, timeout=30,
                                           context=ssl.create_default_context(cafile=cafile))
        try:
            conn.request("GET", "/api/ping")
            self.assertEqual(conn.getresponse().status, 200)
        finally:
            conn.close()

    def test_ffmpeg_has_the_gpu_encoders(self):
        encoders = podman("exec", self.name, "ffmpeg", "-hide_banner", "-encoders").stdout
        for name in ("h264_vaapi", "hevc_vaapi", "h264_nvenc", "h264_qsv", "libx264"):
            self.assertIn(name, encoders, name)

    def test_download_tools_are_in_the_image(self):
        # decisions#libtorrent, decisions#ytdlp-in-image: torrents and links
        # are taken in the image, as MiruTube runs it
        run = podman("exec", self.name, "python3", "-c", "import libtorrent; print(libtorrent.__version__)", check=False)
        self.assertEqual(run.returncode, 0, run.stderr)
        for tool in ("yt-dlp", "deno"):
            run = podman("exec", self.name, tool, "--version", check=False)
            self.assertEqual(run.returncode, 0, tool + ": " + run.stderr)

    def test_transcodes_on_the_gpu(self):
        # behaviour#runs-as-service#scenarios#gpu: a video no browser decodes is
        # converted on this computer's GPU, as the server's log says
        if not gpu_options():
            self.skipTest("no GPU on this computer")
        status, _, body = self.request("POST", "/api/settings/library-folders", {"action": "add", "folder": self.clips})
        self.assertIn(status, (200, 409), body)
        deadline = time.monotonic() + 60
        while "converting with" not in (log := podman("logs", self.name, check=False).stdout):
            self.assertLess(time.monotonic(), deadline, "the server never said what it converts with")
            time.sleep(0.5)
        encoder = log.split("converting with", 1)[1].split()[0]
        self.assertNotEqual(encoder, "libx264", "a GPU is there, and the server converts on the processor")
        old = I("Clips/Old.avi")
        status, _, body = self.request("GET", "/stream/" + old)
        self.assertEqual(status, 200, body)
        status, _, piece = self.request("GET", "/stream/" + old + "?piece=0")
        self.assertEqual(status, 200)
        out = os.path.join(self.tmp, "piece.mp4")
        with open(out, "wb") as f:
            f.write(piece)
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                "stream=codec_name", "-of", "csv=p=0", out], capture_output=True, text=True)
        self.assertEqual(probe.stdout.strip(), "h264", probe.stderr)

    def test_downloads_inside_the_image(self):
        # tests#downloads, where libtorrent and yt-dlp are: in the image
        run = podman("run", "--rm", "--network", "host", "--entrypoint", "python3",
                     "-v", ROOT + ":/src:ro", "-w", "/src", "-e", "PYTHONDONTWRITEBYTECODE=1",
                     "-e", "MIRUTUBE_HOST_ROOT=/", "--security-opt", "label=disable",
                     self.image, "-W", "ignore", "-m", "unittest", "tests.test_downloads", check=False)
        self.assertEqual(run.returncode, 0, run.stdout[-3000:] + run.stderr[-5000:])
        self.assertNotIn("skipped", run.stderr)  # every part ran: the tools are there

    def test_vaapi_drivers_are_in_the_image(self):
        listing = podman("exec", self.name, "sh", "-c", "ls /usr/lib/x86_64-linux-gnu/dri/").stdout
        for driver in ("iHD_drv_video.so", "radeonsi_drv_video.so"):
            self.assertIn(driver, listing, driver)


def quadlet():
    return next((q for q in QUADLET if os.path.exists(q)), None)


@unittest.skipIf(quadlet() is None, "no Quadlet to read the unit with")
class Unit(unittest.TestCase):
    """service, as Quadlet turns it into a user service."""

    def generate(self, drop_ins=()):
        units = tempfile.mkdtemp(prefix="mirutube-quadlet-")
        self.addCleanup(shutil.rmtree, units, True)
        shutil.copy(os.path.join(ROOT, "mirutube.container"), units)
        os.makedirs(os.path.join(units, "mirutube.container.d"))
        for name in drop_ins:
            shutil.copy(os.path.join(ROOT, name), os.path.join(units, "mirutube.container.d"))
        result = subprocess.run([quadlet(), "-dryrun", "-user"], capture_output=True, text=True,
                                env=dict(os.environ, QUADLET_UNIT_DIRS=units))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_user_service(self):
        unit = self.generate()
        self.assertIn("---mirutube.service---", unit)
        start = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
        for option in ("--network host", "--userns keep-id", "--group-add keep-groups",
                       "--security-opt label=disable", "-v /:/host:rslave",
                       "-v %h/.local/share/mirutube:/data", "-v %h/.cache/mirutube:/cache",
                       "-v %h/.config/mirutube:/config", "ghcr.io/komorebinator/mirutube:latest"):
            self.assertIn(option, start, option)
        self.assertNotIn("nvidia.com/gpu", start)
        self.assertIn("io.containers.autoupdate=registry", start)
        self.assertIn("WantedBy=default.target", unit)
        self.assertIn("ExecStartPre=/usr/bin/mkdir -p %h/.local/share/mirutube", unit)
        if os.path.exists("/dev/dri"):
            self.assertIn("--device /dev/dri", start)

    def test_nvidia_drop_in(self):
        start = next(line for line in self.generate(drop_ins=["nvidia.conf"]).splitlines()
                     if line.startswith("ExecStart="))
        self.assertIn("--device nvidia.com/gpu=all", start)

    def test_experimental_drop_in(self):
        start = next(line for line in self.generate(drop_ins=["experimental.conf"]).splitlines()
                     if line.startswith("ExecStart="))
        self.assertIn("ghcr.io/komorebinator/mirutube:experimental", start)
        self.assertNotIn("mirutube:latest", start)


if __name__ == "__main__":
    unittest.main()
