"""tests#browser: the image under test (tests/image.py) run as service runs
it, and its page used in headless Chromium — from tests/browser, a container
of its own — as devices, each a browser context of its own: moving playback
and what comes with it, a device never touched, full screen, subtitles above
the bar and their size, Continue watching following another device, an
upload from the page, and the household's language.

Needs Podman; skipped where there is none."""

import http.client
import json
import os
import shutil
import tempfile
import time
import unittest
import uuid

from mirutube.library import short_ids
from tests.cdp import Browser
from tests.image import ROOT, have_podman, podman, run_mirutube
from tests.test_media import ffmpeg

CHROMIUM = "localhost/mirutube-chromium:test"
EPISODE_SECONDS = 40


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def I(path):
    return short_ids([path])[path]


# the library folder is `media`, so `Show` is a series of its own (library#scan)
EP = [I("media/Show/Show - %02d.mp4" % n) for n in (1, 2, 3)]


@unittest.skipUnless(have_podman(), "no Podman to run the image and the browser with")
class Page(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build = podman("build", "-t", CHROMIUM, os.path.join(ROOT, "tests", "browser"), check=False)
        if build.returncode:
            raise RuntimeError("the browser's image did not build:\n" + build.stderr[-3000:])
        cls.tmp = tempfile.mkdtemp(prefix="mirutube-browser-", dir="/tmp")
        cls.show = os.path.join(cls.tmp, "media", "Show")
        os.makedirs(cls.show)
        for n in (1, 2, 3):
            ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:duration=%d" % EPISODE_SECONDS,
                   "-f", "lavfi", "-i", "sine=frequency=440:duration=%d" % EPISODE_SECONDS,
                   "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "50",
                   "-c:a", "aac", "-shortest", "-movflags", "+faststart",
                   os.path.join(cls.show, "Show - %02d.mp4" % n))
        with open(os.path.join(cls.show, "Show - 01.srt"), "w", encoding="utf-8") as f:
            f.write("1\n00:00:00,000 --> 00:00:%02d,000\nHello from MiruTube\n" % (EPISODE_SECONDS - 1))
        cls.upload = os.path.join(cls.tmp, "to-upload")
        os.makedirs(cls.upload)
        ffmpeg("-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=3", "-c:v", "libx264",
               "-preset", "ultrafast", "-pix_fmt", "yuv420p", os.path.join(cls.upload, "Upload clip.mp4"))
        cls.folders = {name: os.path.join(cls.tmp, name) for name in ("data", "cache", "config")}
        for folder in cls.folders.values():
            os.makedirs(folder)
        cls.port = free_port()
        cls.base = "http://127.0.0.1:%d" % cls.port
        cls.names = ["mirutube-test-" + uuid.uuid4().hex[:8], "mirutube-chromium-" + uuid.uuid4().hex[:8]]
        try:
            run_mirutube(cls.names[0], cls.folders, cls.port)
            cls.media = os.path.dirname(cls.show)
            status, body = cls.post("/api/settings/library-folders", {"action": "add", "folder": cls.media})
            if status != 200:
                raise RuntimeError("the library folder was not added: %s" % body)
            devtools = free_port()
            podman("run", "-d", "--rm", "--name", cls.names[1], "--network", "host",
                   "--security-opt", "label=disable", "-v", cls.tmp + ":" + cls.tmp + ":ro",
                   CHROMIUM, "--remote-debugging-port=%d" % devtools, "about:blank")
            cls.browser = Browser(devtools)
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        for name in getattr(cls, "names", []):
            podman("rm", "-f", "-t", "2", name, check=False)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def post(cls, path, body):
        conn = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=30)
        try:
            conn.request("POST", path, body=json.dumps(body), headers={"Content-Type": "application/json"})
            res = conn.getresponse()
            return res.status, res.read()
        finally:
            conn.close()

    # ------------------------------------------------------------ devices

    def device(self, name, languages=None):
        """A device of its own, named `name`, at home and never touched;
        with `languages`, a browser preferring them, `ru-RU,ru`."""
        page = self.browser.new_device()
        if languages:
            ua = page.eval("navigator.userAgent")
            page.send("Emulation.setUserAgentOverride", userAgent=ua, acceptLanguage=languages)
        # it leaves what it plays before it goes: a closed tab stays in the
        # devices list for a few seconds, as a reload must not drop it
        self.addCleanup(page.close)
        self.addCleanup(lambda: page.goto(self.base + "/settings"))
        page.goto(self.base + "/")
        page.eval("localStorage.setItem('mirutube.device-name', %s)" % json.dumps(name))
        page.goto(self.base + "/")
        return page

    def play_here(self, page, video, at_least=1.0):
        """Opens a video on `page` and starts it by pressing play — the touch
        a browser wants before sound."""
        page.goto(self.base + "/watch/" + video)
        page.wait("document.getElementById('player').readyState >= 1")
        page.click("#np-toggle")
        page.wait("!document.getElementById('player').paused && document.getElementById('player').currentTime > %f"
                  % at_least, message="the video did not play after play was pressed")

    def move(self, page, name):
        """Chooses the device `name` in the devices list on `page`."""
        page.click("#devices-button")
        page.wait("""(() => {
            const b = [...document.querySelectorAll('#devices-list .device-choose')]
              .find((x) => x.querySelector('.device-name').textContent === %s && !x.disabled);
            if (b) b.id = 'pick-device';
            return !!b; })()""" % json.dumps(name), message="%s is not in the devices list, or not choosable" % name)
        page.click("#pick-device")
        page.eval("document.getElementById('pick-device') && document.getElementById('pick-device').removeAttribute('id')")

    def player(self, page, prop):
        return page.eval("document.getElementById('player').%s" % prop)

    # -------------------------------------------------------------- tests

    def test_moving_carries_volume_and_speed_and_stops_the_source(self):
        # behaviour#devices#scenarios#move and #send-away
        tv, phone = self.device("TV move"), self.device("Phone move")
        tv.click("#library-heading")  # someone touched the TV
        self.play_here(phone, EP[0])
        phone.eval("""(() => { const v = document.getElementById('np-volume'); v.value = '0.4';
            v.dispatchEvent(new Event('input')); v.dispatchEvent(new Event('change')); })()""")
        phone.click("#speed-button")
        phone.wait("""(() => { const b = [...document.querySelectorAll('#speed-list button')]
            .find((x) => x.textContent.includes('1.5×')); if (b) b.id = 'pick-speed'; return !!b; })()""")
        phone.click("#pick-speed")
        phone.wait("document.getElementById('player').playbackRate === 1.5")
        at = self.player(phone, "currentTime")
        self.move(phone, "TV move")
        tv.wait("location.pathname === '/watch/%s' && !document.getElementById('player').paused" % EP[0],
                message="the TV did not play the episode")
        self.assertAlmostEqual(self.player(tv, "volume"), 0.4, places=2)
        self.assertEqual(self.player(tv, "playbackRate"), 1.5)
        self.assertGreaterEqual(self.player(tv, "currentTime"), at - 1)
        self.assertFalse(self.player(tv, "muted"))
        # the phone stays on the episode with its player stopped, acting on the TV
        phone.wait("!document.getElementById('elsewhere').hidden && "
                   "document.getElementById('elsewhere').textContent.includes('TV move')")
        # its player let go of the video: nothing loaded, nothing playing
        phone.wait("document.getElementById('player').readyState === 0 && document.getElementById('player').paused",
                   message="the phone's player still holds the video it sent away")
        phone.wait("document.getElementById('np-where').textContent.includes('TV move')")
        # brought back: the phone plays, the TV goes back to home
        self.move(phone, "Phone move")
        phone.wait("!document.getElementById('player').paused", message="the phone did not play it again")
        tv.wait("location.pathname === '/'", message="the TV did not go back to home")

    def test_a_device_never_touched_stays_paused(self):
        # behaviour#devices#scenarios#untouched, decisions#sound-or-paused
        tv, phone = self.device("TV untouched"), self.device("Phone untouched")
        self.play_here(phone, EP[1])
        self.move(phone, "TV untouched")
        tv.wait("location.pathname === '/watch/%s' && document.getElementById('player').readyState >= 1" % EP[1])
        time.sleep(1.5)
        self.assertTrue(self.player(tv, "paused"), "a TV never touched played")
        self.assertFalse(self.player(tv, "muted"), "the video was muted rather than left paused")
        self.assertIsNone(tv.eval("document.getElementById('tap-for-sound')"))
        phone.wait("document.getElementById('np-where').textContent.includes('Paused')",
                   message="the phone's bar does not show the TV paused")
        tv.click("#np-toggle")
        tv.wait("!document.getElementById('player').paused", message="play pressed on the TV did not start it")
        self.assertFalse(self.player(tv, "muted"))

    def test_full_screen_once_holds_for_what_comes(self):
        # behaviour#full-screen#scenarios#ready-on-tv and #elsewhere
        tv, phone = self.device("TV full"), self.device("Phone full")
        tv.click("#fullscreen-button")
        tv.wait("!!document.fullscreenElement", message="full screen did not start from home")
        self.play_here(phone, EP[0])
        self.move(phone, "TV full")
        tv.wait("document.body.classList.contains('video-full') && !document.getElementById('player').paused",
                message="the video sent did not fill the TV's screen")
        # on the phone the bar acts on the TV and still offers full screen, for the phone
        phone.wait("document.getElementById('np-where').textContent.includes('TV full')")
        self.assertFalse(phone.eval("document.getElementById('fullscreen-button').hidden"))
        self.move(phone, "Phone full")
        tv.wait("location.pathname === '/'")
        self.assertTrue(tv.eval("!!document.fullscreenElement && document.body.classList.contains('page-full')"),
                        "the TV left full screen when the video was moved back")

    def test_subtitles_above_the_bar_and_their_size(self):
        # behaviour#subtitle-size#scenarios#bar-over and #shared-size
        self.addCleanup(self.post, "/api/settings/shared", {"subtitle_size": 100})
        tv = self.device("TV subtitles")
        tv.goto(self.base + "/watch/" + EP[0])
        tv.click("#fullscreen-button")
        tv.click("#np-toggle")
        tv.wait("!document.getElementById('player').paused && document.body.classList.contains('video-full')")
        tv.click("#subs-button")
        tv.wait("""(() => { const b = [...document.querySelectorAll('#subs-list button')]
            .find((x) => x.querySelector('.label').textContent !== 'Off');
            if (b) b.id = 'pick-subs'; return !!b; })()""")
        tv.click("#pick-subs")
        tv.wait("document.getElementById('subtitles-bottom').textContent.includes('Hello from MiruTube')",
                message="the subtitles were not drawn")
        gap = ("document.getElementById('now-playing').getBoundingClientRect().top - "
               "document.getElementById('subtitles-bottom').getBoundingClientRect().bottom")
        tv.mouse(640, 300)
        tv.wait("!document.body.classList.contains('bar-hidden')")
        tv.wait("(%s) >= 0" % gap, timeout=3, message="the bar covers the subtitles")
        tv.wait("document.body.classList.contains('bar-hidden')", timeout=10, message="the bar never hid")
        tv.wait("(%s) < 0" % gap, timeout=3, message="the subtitles did not come back down as the bar hid")
        size = "parseFloat(document.getElementById('subtitle-layer').style.fontSize)"
        before = tv.eval(size)
        self.assertEqual(self.post("/api/settings/shared", {"subtitle_size": 150})[0], 200)
        tv.wait("%s > %f" % (size, before * 1.4), message="the Subtitle size set elsewhere did not reach the TV")

    def test_continue_watching_follows_another_device(self):
        # behaviour#resume#scenarios#live-row
        self.post("/api/forget", {"playlist": I("media/Show")})
        laptop = self.device("Laptop row")
        first_card = "(document.querySelector('#continue .card') || {dataset: {}}).dataset.id"
        self.post("/api/position", {"video": EP[1], "position": 10})
        laptop.wait("%s === %s" % (first_card, json.dumps(EP[1])), message="the started episode did not appear")
        self.post("/api/position", {"video": EP[1], "position": EPISODE_SECONDS})
        laptop.wait("%s === %s" % (first_card, json.dumps(EP[2])),
                    message="the card did not go on to the next episode without a reload")

    def test_files_chosen_on_the_page_upload(self):
        # behaviour#uploads, from a page
        target = os.path.join(self.media, "Upload clip.mp4")
        self.addCleanup(lambda: os.path.exists(target) and os.remove(target))
        phone = self.device("Phone upload")
        phone.click("#upload-button")
        phone.wait("!document.getElementById('uploads-card').hidden")
        phone.set_files("#upload-files-input", [os.path.join(self.upload, "Upload clip.mp4")])
        deadline = time.monotonic() + 30
        while not os.path.isfile(target):
            self.assertLess(time.monotonic(), deadline, "the file never reached the library")
            time.sleep(0.3)
        with open(os.path.join(self.upload, "Upload clip.mp4"), "rb") as a, open(target, "rb") as b:
            self.assertEqual(a.read(), b.read())
        phone.wait("[...document.querySelectorAll('#jobs .job-state')].some((x) => x.textContent === 'Done')",
                   message="the upload is not shown as done")

    def test_the_household_chooses_the_language(self):
        # behaviour#languages#scenarios#browser-language, #household and #message
        self.addCleanup(self.post, "/api/settings/shared", {"language": "auto"})
        self.assertEqual(self.post("/api/settings/shared", {"language": "auto"})[0], 200)
        heading = "document.getElementById('library-heading').textContent"
        tv = self.device("TV language")
        russian = self.device("Phone in Russian", languages="ru-RU,ru")
        tv.wait("document.documentElement.lang === 'en' && %s === 'Library'" % heading,
                message="a browser preferring English does not show MiruTube in English")
        russian.wait("document.documentElement.lang === 'ru' && %s === 'Библиотека'" % heading,
                     message="a browser preferring Russian does not show MiruTube in Russian")
        self.assertEqual(russian.eval("document.getElementById('search').placeholder"), "Поиск")
        laptop = self.device("Laptop language")
        laptop.goto(self.base + "/settings")
        laptop.wait("!!document.querySelector('#language button[lang=ru]')")
        # below the fold of the settings page: brought into view, as a user scrolls to it
        laptop.eval("document.getElementById('language').scrollIntoView({block: 'center'})")
        laptop.click("#language button[lang=ru]")
        laptop.wait("document.documentElement.lang === 'ru' && document.title.startsWith('Настройки')",
                    message="Russian chosen in settings did not turn the page Russian")
        tv.wait("document.documentElement.lang === 'ru' && %s === 'Библиотека'" % heading,
                message="Russian chosen on another device did not reach the TV")
        # a refusal from the server is said in Russian, what it names kept
        laptop.eval("document.getElementById('folder-path').value = '/no/such/folder/here';"
                    "document.getElementById('add-folder').scrollIntoView({block: 'center'})")
        laptop.click("#add-folder button[type=submit]")
        laptop.wait("document.getElementById('folders-error').textContent === 'Не удалось добавить: нет такой папки'",
                    message="the server's refusal is not said in Russian")
