"""Builds image with Podman and runs it as service does, against a test
library outside the user's home: the page and a video are served, a folder
added in settings by its path on the computer is scanned, what the server
keeps is written to the mounted data folder and owned by the user, and ffmpeg
in the image has the VAAPI encoders and NVENC; Quadlet, run as a dry run on
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
from tests.test_media import ffmpeg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE = "localhost/mirutube:test"
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
        build = podman("build", "-t", IMAGE, ROOT, check=False)
        if build.returncode:
            raise RuntimeError("the image did not build:\n" + build.stdout[-3000:] + build.stderr[-3000:])
        # outside the user's home, as a second disk would be
        cls.tmp = tempfile.mkdtemp(prefix="mirutube-container-", dir="/tmp")
        cls.lib = os.path.join(cls.tmp, "media", "Series")
        os.makedirs(cls.lib)
        for n in (1, 2):
            ffmpeg("-f", "lavfi", "-i", "color=c=blue:size=64x36:rate=5:duration=4", "-c:v", "libx264",
                   "-preset", "ultrafast", "-pix_fmt", "yuv420p", os.path.join(cls.lib, "Series - %02d.mp4" % n))
        cls.folders = {name: os.path.join(cls.tmp, name) for name in ("data", "cache", "config")}
        for folder in cls.folders.values():
            os.makedirs(folder)
        cls.port = free_port()
        cls.name = "mirutube-test-" + uuid.uuid4().hex[:8]
        # the options service gives the container (mirutube.container)
        options = ["--network", "host", "--userns", "keep-id", "--group-add", "keep-groups",
                   "--security-opt", "label=disable", "-v", "/:/host:rslave",
                   "-v", cls.folders["data"] + ":/data", "-v", cls.folders["cache"] + ":/cache",
                   "-v", cls.folders["config"] + ":/config"]
        if os.path.exists("/dev/dri"):
            options += ["--device", "/dev/dri"]
        podman("run", "-d", "--rm", "--name", cls.name, *options, IMAGE,
               "--port", str(cls.port), "--host", "127.0.0.1")
        deadline = time.monotonic() + 60
        while True:
            try:
                if cls.request("GET", "/api/ping")[0] == 200:
                    break
            except OSError:
                pass
            if time.monotonic() > deadline:
                logs = podman("logs", cls.name, check=False)
                cls.tearDownClass()
                raise RuntimeError("the container did not start:\n" + logs.stdout + logs.stderr)
            time.sleep(0.5)

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
        self.assertIn({"name": "Series", "folder": self.lib}, json.loads(body)["library_folders"])
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
