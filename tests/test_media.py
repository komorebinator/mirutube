"""On videos and subtitle files the test makes with ffmpeg: durations and
codecs are read and sorted into direct, stream and unplayable; the pieces of a
stream video, made from an HEVC 10-bit file with FLAC sound and from H.264
with E-AC3 in MKV, decode as H.264 and AAC and follow each other in time
without a gap, and so do pieces made with a second audio stream and with an
external sound file in place of the default — whose beeps come out at the
moments they were made for, in sync with the video; an embedded text track
and cp1251 and UTF-8 files come out as readable WebVTT
(behaviour#readable-subtitles, behaviour#no-black-screen,
behaviour#audio-tracks). Hardware encoders are tried in the order
media#stream_segment gives, and the media details of watch#details are
read."""

import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest

from mirutube import media
from mirutube.library import scan
from tests import fmp4

RUSSIAN = "Привет, мир! Съешь же ещё этих мягких французских булок."
SRT = "1\n00:00:00,500 --> 00:00:02,000\n%s\n\n2\n00:00:02,000 --> 00:00:02,800\nВторая строка\n" % RUSSIAN


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", *args], check=True)


def encoders():
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return {line.split()[1] for line in out.splitlines()[10:] if len(line.split()) > 1}


def ffmpeg_processes(fragment):
    """The ids of running ffmpeg processes whose command line holds `fragment`,
    read from /proc — on a system without it the test that asks is skipped."""
    if not os.path.isdir("/proc"):
        raise unittest.SkipTest("no /proc to find running processes in")
    found = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as f:
                args = f.read().split(b"\0")
        except OSError:
            continue
        if args and os.path.basename(args[0]) == b"ffmpeg" and any(fragment.encode() in a for a in args):
            found.append(int(pid))
    return found


def make_hevc10(path, duration, size="128x72"):
    """An HEVC 10-bit video with FLAC sound, by libx265 or else a VAAPI
    encoder; False when this ffmpeg can make neither."""
    src = ["-f", "lavfi", "-i", "testsrc2=size=%s:rate=24000/1001:duration=%d" % (size, duration),
           "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=44100:duration=%d" % duration]
    have = encoders()
    if "libx265" in have:
        ffmpeg(*src, "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "log-level=error",
               "-crf", "35", "-pix_fmt", "yuv420p10le", "-c:a", "flac", path)
        return True
    if "hevc_vaapi" in have and media._render_device():
        try:
            ffmpeg("-vaapi_device", media._render_device(), *src, "-vf", "format=p010,hwupload",
                   "-c:v", "hevc_vaapi", "-profile:v", "main10", "-c:a", "flac", path)
            return True
        except subprocess.CalledProcessError:
            pass
    return False


def make_h264_eac3(path, duration, second=False):
    """H.264 with B-frames and keyframes every 2.5 seconds, E-AC3 sound, in
    MKV; with `second`, a second audio stream of beeps (beeps_source), in
    Russian and titled `Dub [DEEP]`."""
    extra = ["-f", "lavfi", "-i", beeps_source(duration)] if second else []
    maps = ["-map", "0", "-map", "1", "-map", "2", "-metadata:s:a:1", "language=rus",
            "-metadata:s:a:1", "title=Dub [DEEP]"] if second else []
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=128x72:rate=24000/1001:duration=%d" % duration,
           "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=%d" % duration,
           *extra, *maps,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "35", "-bf", "3", "-g", "60",
           "-pix_fmt", "yuv420p", "-c:a", "eac3", "-ac", "2", path)


BEEP_EVERY = 2.5  # seconds between the beeps of beeps_source


def beeps_source(duration, rate=48000):
    """A lavfi source of silence with a tenth of a second of 1 kHz every
    BEEP_EVERY seconds from 0: where its beeps come out of a piece shows
    whether the sound is in sync with the video."""
    return ("aevalsrc=exprs='if(lt(mod(t,%s),0.1),0.5*sin(2*PI*1000*t),0)':s=%d:d=%s"
            % (BEEP_EVERY, rate, duration))


def make_dub(path, duration, codec="ac3", delay=0.0):
    """An external sound file of beeps, as a dub comes beside a video; with
    `delay`, its sound starts that many seconds late, as the file says."""
    ffmpeg("-f", "lavfi", "-i", beeps_source(duration), "-c:a", codec, "-ac", "2",
           "-output_ts_offset", "%.3f" % delay, path)


def beep_onsets(path):
    """The moments the beeps of a file's sound begin, on its own timeline,
    to the sample: where silencedetect finds each silence end."""
    res = subprocess.run(["ffmpeg", "-nostdin", "-copyts", "-i", path, "-map", "0:a",
                          "-af", "silencedetect=noise=-40dB:duration=0.05", "-f", "null", "-"],
                         capture_output=True, text=True)
    return [float(t) for t in re.findall(r"silence_end: ([0-9.]+)", res.stderr)]


class Media(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="mirutube-media-")
        cls.lib = os.path.join(cls.tmp, "library")
        cls.folder = os.path.join(cls.lib, "Clips")
        os.makedirs(cls.folder)
        media.set_cache_dir(os.path.join(cls.tmp, "cache"))
        src = ["-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=3",
               "-f", "lavfi", "-i", "sine=frequency=440:duration=3"]
        sub = os.path.join(cls.tmp, "embedded.srt")
        with open(sub, "w", encoding="utf-8") as f:
            f.write(SRT)
        cls.playable = os.path.join(cls.folder, "playable.mkv")
        ffmpeg(*src, "-i", sub, "-map", "0", "-map", "1", "-map", "2",
               "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-c:s", "srt", "-metadata:s:s:0", "language=rus", cls.playable)
        cls.unplayable = os.path.join(cls.folder, "old-divx.avi")
        ffmpeg(*src, "-c:v", "mpeg4", "-c:a", "libmp3lame", cls.unplayable)
        cls.utf8 = os.path.join(cls.folder, "playable.ru.srt")
        with open(cls.utf8, "w", encoding="utf-8") as f:
            f.write(SRT)
        cls.cp1251 = os.path.join(cls.folder, "old-divx.rus.srt")
        with open(cls.cp1251, "wb") as f:
            f.write(SRT.encode("cp1251"))
        # one of each way a video reaches the browser
        kinds = os.path.join(cls.lib, "Kinds")
        os.makedirs(kinds)
        ffmpeg(*src, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
               "-c:a", "aac", os.path.join(kinds, "direct.mp4"))
        if {"libvpx-vp9", "libopus"} <= encoders():
            ffmpeg(*src, "-c:v", "libvpx-vp9", "-deadline", "realtime", "-c:a", "libopus",
                   os.path.join(kinds, "direct.webm"))
        ffmpeg(*src, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p10le",
               "-c:a", "aac", os.path.join(kinds, "high10.mp4"))
        ffmpeg(*src, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
               "-c:a", "eac3", os.path.join(kinds, "eac3.mp4"))
        # a recording cut off before its index was written: MP4 writes it last
        whole = os.path.join(cls.tmp, "whole.mp4")
        ffmpeg(*src, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
               "-c:a", "aac", whole)
        with open(whole, "rb") as f:
            data = f.read()
        with open(os.path.join(kinds, "cut off.mp4"), "wb") as f:
            f.write(data[:len(data) // 2])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_probe_reads_duration_and_codecs(self):
        info = media.probe(self.playable)
        self.assertAlmostEqual(info["duration"], 3.0, delta=0.2)
        self.assertEqual(info["video_codec"], "h264")
        self.assertEqual(info["audio_codec"], "aac")
        self.assertEqual(len(info["subtitles"]), 1)
        self.assertEqual(info["subtitles"][0]["codec"], "subrip")
        self.assertFalse(info["subtitles"][0]["image"])
        info = media.probe(self.unplayable)
        self.assertAlmostEqual(info["duration"], 3.0, delta=0.2)
        self.assertEqual(info["video_codec"], "mpeg4")

    def test_media_details(self):
        # watch#details: what the file is
        d = media.details(self.playable)
        self.assertEqual((d["container"], d["width"], d["height"], d["frame_rate"]), ("MKV", 160, 90, 10))
        self.assertEqual((d["video_codec"], d["bit_depth"], d["audio_codec"], d["channels"]), ("h264", 8, "aac", 1))
        self.assertEqual(d["size"], os.path.getsize(self.playable))
        self.assertIn(d["profile"], ("High", "Constrained Baseline", "Baseline", "Main"))
        d = media.details(os.path.join(self.lib, "Kinds", "high10.mp4"))
        self.assertEqual((d["container"], d["bit_depth"]), ("MP4", 10))
        self.assertIsNone(media.details(os.path.join(self.lib, "Kinds", "cut off.mp4")))

    def test_probe_is_cached(self):
        media.probe(self.playable)
        cached = media._cache_path(self.playable, "probe", ".json")
        self.assertTrue(os.path.exists(cached))

    def test_sorted_into_direct_stream_and_unplayable(self):
        videos = scan(self.lib)["videos"]
        expected = {
            "library/Kinds/direct.mp4": "direct",           # H.264 8-bit 4:2:0 and AAC in MP4
            "library/Kinds/high10.mp4": "stream",           # H.264 at 10 bits
            "library/Kinds/eac3.mp4": "stream",             # E-AC3 sound
            "library/Clips/playable.mkv": "stream",         # H.264 and AAC, but in MKV
            "library/Clips/old-divx.avi": "stream",         # MPEG-4 Part 2 in AVI
            "library/Kinds/cut off.mp4": "unplayable",      # behaviour#no-black-screen#scenarios#unreadable
        }
        if "library/Kinds/direct.webm" in videos:
            expected["library/Kinds/direct.webm"] = "direct"  # VP9 and Opus in WebM
        for vid, how in expected.items():
            self.assertEqual(videos[vid]["delivery"], how, vid)
            self.assertEqual(videos[vid]["playable"], how != "unplayable", vid)
        for vid, how in expected.items():
            if how != "unplayable":
                self.assertIsNone(videos[vid]["reason"], vid)
        bad = videos["library/Kinds/cut off.mp4"]
        self.assertIn("could not be read", bad["reason"])

    def test_tracks_are_listed(self):
        cat = scan(self.lib)
        tracks = cat["videos"]["library/Clips/playable.mkv"]["subtitles"]
        kinds = sorted((t["kind"], t["label"]) for t in tracks)
        self.assertEqual(kinds, [("embedded", "Russian"), ("external", "Russian 2")])
        self.assertEqual([t["label"] for t in cat["videos"]["library/Clips/old-divx.avi"]["subtitles"]], ["Russian"])

    def assertReadableVtt(self, vtt):
        self.assertTrue(vtt.lstrip("﻿").startswith("WEBVTT"), vtt[:40])
        self.assertIn(RUSSIAN, vtt)
        self.assertIn("Вторая строка", vtt)
        # an embedded track keeps the container's start offset (the AAC priming, ~23 ms)
        m = re.search(r"00:00\.(\d{3}) --> 00:02\.(\d{3})", vtt)
        self.assertIsNotNone(m, vtt)
        self.assertAlmostEqual(int(m.group(1)), 500, delta=50)

    def test_embedded_text_track_to_vtt(self):
        stream = media.probe(self.playable)["subtitles"][0]["stream"]
        self.assertReadableVtt(media.subtitles_vtt(self.playable, stream=stream))

    def test_utf8_file_to_vtt(self):
        self.assertReadableVtt(media.subtitles_vtt(self.utf8))

    def test_cp1251_file_to_vtt(self):
        self.assertReadableVtt(media.subtitles_vtt(self.cp1251))

    def test_decode_order(self):
        self.assertEqual(media.decode_text("ёж".encode("utf-8")), "ёж")
        self.assertEqual(media.decode_text("ёж".encode("cp1251")), "ёж")
        self.assertEqual(media.decode_text(b"caf\xe9 \x98"), "caf\xe9 \x98")  # 0x98 is unused in cp1251

    def test_thumbnail_is_jpeg(self):
        path = media.thumbnail(self.playable)
        with open(path, "rb") as f:
            self.assertEqual(f.read(2), b"\xff\xd8")
        self.assertEqual(media.thumbnail(self.playable), path)


class Pieces(unittest.TestCase):
    """The pieces of `stream` videos as a Media Source Extensions SourceBuffer
    takes them (media#stream_plan, media#stream_segment)."""

    DURATION = 25  # seconds: three pieces

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="mirutube-pieces-")
        media.set_cache_dir(os.path.join(cls.tmp, "cache"))
        cls.hevc = os.path.join(cls.tmp, "hevc10 flac.mkv")
        cls.have_hevc = make_hevc10(cls.hevc, cls.DURATION)
        cls.eac3 = os.path.join(cls.tmp, "h264 eac3.mkv")
        make_h264_eac3(cls.eac3, cls.DURATION)
        # a second audio stream of the file's own, and dubs in external files
        cls.dual = os.path.join(cls.tmp, "h264 two tracks.mkv")
        make_h264_eac3(cls.dual, cls.DURATION, second=True)
        cls.dub = os.path.join(cls.tmp, "h264 eac3.STEPonee.mka")
        make_dub(cls.dub, cls.DURATION)
        cls.hevc_dub = os.path.join(cls.tmp, "hevc10 flac.STEPonee.mka")
        make_dub(cls.hevc_dub, cls.DURATION, codec="flac")
        cls.short_dub = os.path.join(cls.tmp, "short.mka")
        make_dub(cls.short_dub, 12)
        cls.late_dub = os.path.join(cls.tmp, "late.mka")
        make_dub(cls.late_dub, cls.DURATION, delay=1.0)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.encoder = media._encoder

    def tearDown(self):
        media._encoder = self.encoder

    def pieces_of(self, path, audio=None):
        plan = media.stream_plan(path, audio)
        return plan, [media.stream_segment(path, i, audio=audio) for i in range(len(plan["starts"]))]

    def assertPiecesFollow(self, path, copy, audio=None, beeps=None):
        """The pieces of the video at `path` with the sound track `audio`;
        with `beeps` — the moment of the first beep on the track's own clock —
        the track's beeps come out where they were made for."""
        plan, pieces = self.pieces_of(path, audio)
        self.assertEqual(plan["copy"], copy)
        self.assertEqual(len(plan["starts"]), 3, plan["starts"])
        self.assertEqual(plan["starts"][0], 0)
        self.assertEqual(plan["starts"], sorted(plan["starts"]))
        m = re.fullmatch(r'video/mp4; codecs="avc1\.([0-9a-f]{2})[0-9a-f]{2}([0-9a-f]{2}),mp4a\.40\.2"',
                         plan["mime"])
        self.assertIsNotNone(m, plan["mime"])
        profile, level = int(m.group(1), 16), int(m.group(2), 16)
        if not copy:
            self.assertTrue(all(abs(b - a - media.PIECE) < 1e-6
                                for a, b in zip(plan["starts"], plan["starts"][1:])), plan["starts"])
        descriptions = None
        ends = {}
        for i, data in enumerate(pieces):
            where = "%s piece %d" % (os.path.basename(path), i)
            # an initialization segment, then media segments
            kinds = fmp4.top_level(data)
            self.assertEqual(kinds[:2], ["ftyp", "moov"], where)
            self.assertGreater(len(kinds), 2, where)
            self.assertEqual(kinds[2:], ["moof", "mdat"] * ((len(kinds) - 2) // 2), where)
            # every piece configures the decoders the same way
            if descriptions is None:
                descriptions = fmp4.sample_descriptions(data)
            else:
                self.assertEqual(fmp4.sample_descriptions(data), descriptions, where)
            # H.264 and AAC, as the MIME type says
            probed = probe_streams(data)
            self.assertEqual([s["codec_name"] for s in probed], ["h264", "aac"], where)
            self.assertEqual(probed[0]["pix_fmt"], "yuv420p", where)
            self.assertEqual(H264_PROFILES[probed[0]["profile"]], profile, where)
            self.assertEqual(probed[0]["level"], level, where)
            # timestamps continue the piece before, without a gap
            for track in fmp4.tracks(data).values():
                samples = track["samples"]
                self.assertTrue(samples, where)
                kind = track["kind"]
                first = min(s[1] for s in samples)
                end = max(s[1] + s[2] for s in samples)
                self.assertGreaterEqual(min(s[0] for s in samples), 0, where)
                self.assertTrue(all(a[0] < b[0] for a, b in zip(samples, samples[1:])), where)
                self.assertTrue(all(s[0] <= s[1] + 1e-9 for s in samples), where)
                if kind == "vide":
                    self.assertTrue(samples[0][3], "%s starts without a keyframe" % where)
                    if i:
                        # copied: at the keyframe the plan names; transcoded: the first frame from there
                        self.assertGreaterEqual(first, plan["starts"][i] - 0.001, where)
                        self.assertLess(first, plan["starts"][i] + (0.001 if copy else 0.05), where)
                    tolerance = 0.002
                else:
                    if i:
                        self.assertGreaterEqual(first, plan["starts"][i] - 0.001, where)
                        self.assertLess(first, plan["starts"][i] + 1024 / 48000 + 0.001, where)
                    tolerance = 1 / 48000
                if kind in ends:
                    self.assertAlmostEqual(first, ends[kind], delta=tolerance,
                                           msg="%s %s: %.6f after %.6f" % (where, kind, first, ends[kind]))
                ends[kind] = end
        for kind, end in ends.items():
            self.assertAlmostEqual(end, plan["duration"], delta=0.1, msg=kind)
        # appended one after another, they decode as one video
        joined = os.path.join(self.tmp, "joined.mp4")
        with open(joined, "wb") as f:
            f.write(fmp4.init_segment(pieces[0]))
            for data in pieces:
                f.write(fmp4.media_segments(data))
        res = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-xerror", "-i", joined, "-f", "null", "-"],
                             capture_output=True, text=True)
        self.assertEqual((res.returncode, res.stderr), (0, ""))
        if beeps is not None:
            # In sync: each beep begins with the picture of the same moment —
            # the sound's timestamps and the video's are one clock, which the
            # pieces start at the video's start time and move later by the
            # plan's offset, like the pictures.
            info = media.probe(path)
            source, shift = info["duration"], plan["offset"] - info["start_time"]
            moments = [beeps + k * BEEP_EVERY for k in range(int(source / BEEP_EVERY) + 1)]
            expected = [t + shift for t in moments if 0.05 < t < source - 0.2]
            # (silencedetect ends the silence it is in at the end of the sound too)
            onsets = [t for t in beep_onsets(joined) if t < plan["duration"] - 0.15]
            self.assertEqual(len(onsets), len(expected), onsets)
            for want, got in zip(expected, onsets):
                self.assertAlmostEqual(got, want, delta=0.002, msg="beep %.6f came at %.6f" % (want, got))
        return plan

    def test_hevc_10bit_with_flac_is_transcoded(self):
        if not self.have_hevc:
            self.skipTest("this ffmpeg cannot make HEVC at 10 bits")
        self.assertEqual(media.probe(self.hevc)["pix_fmt"], "yuv420p10le")
        self.assertEqual(scan_one(self.hevc), "stream")
        plan = self.assertPiecesFollow(self.hevc, copy=False)
        self.assertEqual(plan["offset"], 0)

    def test_hevc_with_libx264(self):
        # where no hardware encoder opens
        if not self.have_hevc:
            self.skipTest("this ffmpeg cannot make HEVC at 10 bits")
        media._encoder = media.SOFTWARE_ENCODER
        self.assertPiecesFollow(self.hevc, copy=False)

    def test_h264_with_eac3_in_mkv_is_copied(self):
        self.assertEqual(scan_one(self.eac3), "stream")
        plan = self.assertPiecesFollow(self.eac3, copy=True)
        # pieces start at keyframes, every 2.5 s in this file, at least ten seconds apart
        cuts = plan["cuts"]
        self.assertTrue(all(b - a >= media.PIECE for a, b in zip(cuts, cuts[1:])), cuts)
        self.assertTrue(all(abs(c / 2.5025 - round(c / 2.5025)) < 0.01 for c in cuts), cuts)

    def test_sound_to_the_end_of_every_piece(self):
        # decisions#sound-input: with keyframes four seconds apart, the one
        # before a piece's seek lies two seconds before it, and the sound
        # must still reach the end of the piece — a steady tone, never silent
        path = os.path.join(self.tmp, "h264 sparse keyframes.mkv")
        ffmpeg("-f", "lavfi", "-i", "testsrc2=size=128x72:rate=24000/1001:duration=30",
               "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=30",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "35", "-g", "96", "-keyint_min", "96",
               "-sc_threshold", "0", "-pix_fmt", "yuv420p", "-c:a", "eac3", "-ac", "2", path)
        plan, pieces = self.pieces_of(path)
        self.assertTrue(plan["copy"])
        self.assertEqual([round(c, 2) for c in plan["cuts"]], [0, 12.01, 24.02])
        for i, data in enumerate(pieces):
            piece = os.path.join(self.tmp, "sparse piece %d.mp4" % i)
            with open(piece, "wb") as f:
                f.write(data)
            self.assertEqual(silences(piece), [], "piece %d has silence in its sound" % i)

    def test_second_audio_stream(self):
        # decisions#audio-in-pieces: the other stream of the file's own, in place of the first
        streams = media.probe(self.dual)["audio"]
        self.assertEqual([(a["codec"], a["language"], a["title"]) for a in streams],
                         [("eac3", None, None), ("eac3", "rus", "Dub [DEEP]")])
        self.assertPiecesFollow(self.dual, copy=True, audio=streams[1]["stream"], beeps=0)

    def test_external_sound_file_with_a_copied_video(self):
        self.assertPiecesFollow(self.eac3, copy=True, audio=self.dub, beeps=0)

    def test_external_sound_file_keeps_its_delay(self):
        # a dub whose sound starts a second late, as its file says, and runs
        # on past the video: it is heard a second late, and ends with the video
        self.assertPiecesFollow(self.eac3, copy=True, audio=self.late_dub, beeps=1.0)

    def test_external_sound_file_with_a_transcoded_video(self):
        if not self.have_hevc:
            self.skipTest("this ffmpeg cannot make HEVC at 10 bits")
        self.assertPiecesFollow(self.hevc, copy=False, audio=self.hevc_dub, beeps=0)

    def test_external_sound_file_shorter_than_the_video(self):
        # silence goes on where the dub ends, so the sound has no hole to stop at
        self.assertPiecesFollow(self.eac3, copy=True, audio=self.short_dub)

    def test_plan_names_the_sound_of_the_track_chosen(self):
        silent = os.path.join(self.tmp, "silent.mkv")
        ffmpeg("-f", "lavfi", "-i", "testsrc2=size=128x72:rate=25:duration=3",
               "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", silent)
        self.assertNotIn("mp4a", media.stream_plan(silent)["mime"])
        self.assertIn("mp4a", media.stream_plan(silent, self.dub)["mime"])
        probed = probe_streams(media.stream_segment(silent, 0, audio=self.dub))
        self.assertEqual([s["codec_name"] for s in probed], ["h264", "aac"])

    def test_track_that_is_not_sound(self):
        with self.assertRaises(RuntimeError):
            media.stream_segment(self.eac3, 0, audio=0)  # the video stream
        with self.assertRaises(RuntimeError):
            media.stream_segment(self.eac3, 0, audio=self.eac3 + ".missing")

    def test_piece_takes_a_fraction_of_its_length(self):
        # behaviour#no-black-screen#scenarios#hevc: moved to the middle, it plays within a few seconds
        path = self.hevc if self.have_hevc else self.eac3
        plan = media.stream_plan(path)
        began = time.monotonic()
        data = media.stream_segment(path, 1)
        took = time.monotonic() - began
        self.assertTrue(data)
        self.assertLess(took, plan["starts"][2] - plan["starts"][1])

    def test_piece_nobody_waits_for_is_stopped(self):
        for path in (self.eac3, self.hevc if self.have_hevc else None):
            if path is None:
                continue
            asked = []

            def cancelled():
                asked.append(ffmpeg_processes(os.path.basename(path)))
                return True

            self.assertIsNone(media.stream_segment(path, 1, cancelled=cancelled))
            self.assertEqual(len(asked[0]), 1, "ffmpeg was running when it was asked")
            self.assertEqual(ffmpeg_processes(os.path.basename(path)), [])

    def test_missing_piece(self):
        with self.assertRaises(IndexError):
            media.stream_segment(self.eac3, 99)


H264_PROFILES = {"Constrained Baseline": 66, "Baseline": 66, "Main": 77, "High": 100}


class EncoderOrder(unittest.TestCase):
    """media#stream_segment: NVENC, then VAAPI on each render device in turn
    (Linux), then QSV, AMF (Windows) and VideoToolbox (macOS), else libx264 —
    tried with ffmpeg standing in, so the order holds whatever GPUs and
    system this computer has."""

    def setUp(self):
        self.saved = media._encoder, media._render_devices, media.subprocess.run, media.PLATFORM
        media._encoder = None
        media._render_devices = lambda: ["/dev/dri/renderD128", "/dev/dri/renderD129"]
        media.PLATFORM = "linux"
        self.tried = []

    def tearDown(self):
        media._encoder, media._render_devices, media.subprocess.run, media.PLATFORM = self.saved

    def fake_ffmpeg(self, works):
        """ffmpeg that opens only the encoders in `works`, each named as
        transcoder() names it."""
        def run(args, **kwargs):
            name = args[args.index("-c:v") + 1]
            if name == "h264_vaapi":
                name += ":" + args[args.index("-vaapi_device") + 1]
            self.tried.append(name)
            return subprocess.CompletedProcess(args, 0 if name in works else 1)
        media.subprocess.run = run

    def test_order(self):
        self.fake_ffmpeg(set())
        self.assertEqual(media.transcoder(), media.SOFTWARE_ENCODER)
        self.assertEqual(self.tried, ["h264_nvenc", "h264_vaapi:/dev/dri/renderD128",
                                      "h264_vaapi:/dev/dri/renderD129", "h264_qsv"])

    def test_nvenc_before_vaapi(self):
        self.fake_ffmpeg({"h264_nvenc", "h264_vaapi:/dev/dri/renderD128"})
        self.assertEqual(media.transcoder(), "h264_nvenc")
        self.assertEqual(self.tried, ["h264_nvenc"])

    def test_vaapi_on_the_second_render_device(self):
        self.fake_ffmpeg({"h264_vaapi:/dev/dri/renderD129", "h264_qsv"})
        encoder = media.transcoder()
        self.assertEqual(encoder, "h264_vaapi:/dev/dri/renderD129")
        self.assertEqual(media.describe_encoder(encoder), "h264_vaapi on /dev/dri/renderD129")
        pre, _, out = media._encoder_args(encoder)
        self.assertEqual(pre, ["-vaapi_device", "/dev/dri/renderD129"])
        self.assertIn("h264_vaapi", out)
        # found once
        self.tried.clear()
        self.assertEqual(media.transcoder(), encoder)
        self.assertEqual(self.tried, [])

    def test_windows_and_macos(self):
        # VAAPI only where there are render devices, on Linux; AMF on
        # Windows, VideoToolbox on macOS
        media.PLATFORM = "win32"
        self.fake_ffmpeg({"h264_amf"})
        self.assertEqual(media.transcoder(), "h264_amf")
        self.assertEqual(self.tried, ["h264_nvenc", "h264_qsv", "h264_amf"])
        self.assertEqual(media.hardware_encoders("darwin"), ["h264_nvenc", "h264_qsv", "h264_videotoolbox"])
        for encoder in ("h264_amf", "h264_videotoolbox"):
            pre, filters, out = media._encoder_args(encoder)
            self.assertEqual(out[:2], ["-c:v", encoder])
            self.assertIn("-bf", out)
        # no device file is looked for where there are none
        media._render_devices = self.saved[1]
        self.assertEqual(media._render_devices(), [])

    def test_render_devices_in_number_order(self):
        media._render_devices = self.saved[1]
        found = media._render_devices()
        self.assertEqual(found, sorted(found, key=lambda d: int(d.rsplit("D", 1)[1])))


def silences(path):
    """The silent spans of a file's sound, as (start, end) in seconds."""
    res = subprocess.run(["ffmpeg", "-v", "info", "-nostats", "-nostdin", "-i", path, "-map", "0:a",
                          "-af", "silencedetect=noise=-50dB:d=0.05", "-f", "null", "-"],
                         capture_output=True, text=True)
    starts = [float(m) for m in re.findall(r"silence_start: ([\d.]+)", res.stderr)]
    ends = [float(m) for m in re.findall(r"silence_end: ([\d.]+)", res.stderr)]
    return list(zip(starts, ends + [None] * (len(starts) - len(ends))))


def probe_streams(data):
    """The streams ffprobe finds in one piece."""
    import json
    res = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_name,profile,level,pix_fmt", "-of", "json", "-i", "pipe:0"],
                         input=data, capture_output=True, check=True)
    return json.loads(res.stdout)["streams"]


def scan_one(path):
    """How the video at `path` reaches the browser, as the catalogue says."""
    from mirutube.library import delivery
    return delivery(media.probe(path), path)[0]


if __name__ == "__main__":
    unittest.main()
