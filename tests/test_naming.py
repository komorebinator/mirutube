"""Orders and matches a set of real-world folder layouts, each with the order
and the subtitle and sound matches expected (behaviour#series-order,
behaviour#subtitles-found, behaviour#audio-tracks), and the library made of
several library folders with some paths removed. The files are empty:
naming is all that is read, so probing is replaced by a stub."""

import os
import random
import shutil
import tempfile
import unittest

from mirutube.library import file_title, folder_names, order_episodes, scan, normalise


def no_probe(path):
    """A file with one audio stream that says nothing about itself."""
    return {"duration": 60.0, "video_codec": "h264", "audio_codec": "aac", "audio_stream": 1,
            "audio": [{"stream": 1, "codec": "aac", "language": None, "title": None}], "subtitles": []}


class Layout:
    """A temporary library folder, `Library`, built from a list of relative
    file paths."""

    NAME = "Library"

    def __init__(self, files):
        self.tmp = tempfile.mkdtemp(prefix="mirutube-naming-")
        self.root = os.path.join(self.tmp, self.NAME)
        os.makedirs(self.root)
        for rel in files:
            path = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "wb").close()

    def scan(self, probe=no_probe):
        """The catalogue, its paths read from the library folder: every path
        within the library begins with the name of its library folder
        (library#scan), checked here once so the layouts below can name
        their files as they lie in the folder."""
        return without_prefix(scan([self.root], probe=probe), self.NAME)

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def without_prefix(cat, name):
    """The catalogue with `name/` taken off the front of every path in it,
    each of which must begin with it."""
    def strip(path):
        if path is None:
            return None
        assert path.startswith(name + "/"), path
        return path[len(name) + 1:]
    videos = {}
    for vid, v in cat["videos"].items():
        v = dict(v, id=strip(v["id"]), playlist=strip(v["playlist"]))
        for field in ("subtitles", "audio"):
            v[field] = [dict(t, path=strip(t["path"])) if "path" in t else t for t in v[field]]
        videos[strip(vid)] = v
    return {"playlists": [dict(p, id=strip(p["id"]), videos=[strip(v) for v in p["videos"]])
                          for p in cat["playlists"]],
            "standalone": [strip(v) for v in cat["standalone"]], "videos": videos}


def shuffled(names):
    names = list(names)
    random.Random(7).shuffle(names)
    return names


class OrderEpisodes(unittest.TestCase):
    def order(self, names, **kw):
        is_series, items = order_episodes(shuffled(names), **kw)
        return is_series, [i["name"] for i in items]

    def test_normalise(self):
        self.assertEqual(normalise("[Group] Show 1080p - 01 [x264].mkv"), "Show 1080p - 01")
        self.assertEqual(normalise("Some.Movie.(2019).[BDRip].(x264).mkv"), "Some Movie (2019)")
        self.assertEqual(normalise("my_holiday_video.mp4"), "my holiday video")

    def test_release_tags(self):
        names = ["Show 1080p - %02d [x264].mkv" % i for i in range(1, 13)]
        is_series, order = self.order(names)
        self.assertTrue(is_series)
        self.assertEqual(order, names)

    def test_release_group_prefix(self):
        names = ["[SubsGroup] Long Show Name - %02d [1080p][ABCD1234].mkv" % i for i in range(1, 25)]
        is_series, order = self.order(names)
        self.assertTrue(is_series)
        self.assertEqual(order, names)

    def test_sxxeyy(self):
        names = ["Show.S01E%02d.720p.WEB-DL.x264.mkv" % i for i in range(1, 11)] + \
                ["Show.S02E%02d.720p.WEB-DL.x264.mkv" % i for i in range(1, 4)]
        is_series, items = order_episodes(shuffled(names))
        self.assertTrue(is_series)
        self.assertEqual([i["name"] for i in items], names)
        self.assertEqual((items[10]["season"], items[10]["episode"]), (2, 1))

    def test_other_markers(self):
        names = ["Show 1x01.mkv", "Show 1x02.mkv", "Show 1x10.mkv"]
        self.assertEqual(self.order(names), (True, names))
        names = ["Show E1.mkv", "Show E2.mkv", "Show E11.mkv"]
        self.assertEqual(self.order(names), (True, names))
        names = ["Show Ep 1 Pilot.mkv", "Show Ep 2 Another.mkv", "Show Ep 10 Finale.mkv"]
        self.assertEqual(self.order(names), (True, names))
        names = ["Show Episode 1.mkv", "Show Episode 2.mkv", "Show Episode 12.mkv"]
        self.assertEqual(self.order(names), (True, names))

    def test_russian_names(self):
        names = ["Сериал. Серия %d.mkv" % i for i in range(1, 13)]
        is_series, order = self.order(names)
        self.assertTrue(is_series)
        self.assertEqual(order, names)
        names = ["Мастер и Маргарита (2005) %d серия.avi" % i for i in range(1, 11)]
        self.assertEqual(self.order(names), (True, names))

    def test_titles_after_the_number(self):
        names = ["Show - 01 - Pilot.mkv", "Show - 02 - The Return.mkv", "Show - 03 - 2 Days Later.mkv",
                 "Show - 10 - Finale.mkv"]
        self.assertEqual(self.order(names), (True, names))

    def test_natural_sort(self):
        names = ["ep%d.mp4" % i for i in range(1, 11)]
        _, order = self.order(names)
        self.assertEqual(order, names)
        self.assertEqual(order.index("ep10.mp4"), order.index("ep9.mp4") + 1)

    def test_no_marker_at_all(self):
        names = ["Beach.mp4", "birthday party.mp4", "City walk.mkv", "Mountains.mov"]
        is_series, order = self.order(names)
        self.assertFalse(is_series)
        self.assertEqual(order, names)

    def test_numbers_that_do_not_vary(self):
        # the year and the resolution are the same in every file: no episode number
        names = ["Holiday 2019 1080p.mp4"]
        is_series, order = self.order(names)
        self.assertFalse(is_series)
        names = ["Trip 2019 Alps.mp4", "Trip 2019 Rome.mp4"]
        self.assertEqual(self.order(names), (False, names))

    def test_season_from_folder(self):
        _, items = order_episodes(["Show - 01.mkv", "Show - 02.mkv"], season=3)
        self.assertEqual([(i["season"], i["episode"]) for i in items], [(3, 1), (3, 2)])


class ScanLayouts(unittest.TestCase):
    def layout(self, files, probe=no_probe):
        lay = Layout(files)
        self.addCleanup(lay.close)
        return lay.scan(probe)

    def playlist(self, cat, pid):
        return next(p for p in cat["playlists"] if p["id"] == pid)

    def test_playlists_and_standalone(self):
        cat = self.layout(["Loose video.mp4", "Holiday/b.mp4", "Holiday/a.mp4",
                           "Holiday/Day 2/x.mp4", "Holiday/Day 2/y.mp4", "Empty/readme.txt"])
        self.assertEqual(cat["standalone"], ["Loose video.mp4"])
        self.assertEqual(sorted(p["id"] for p in cat["playlists"]), ["Holiday", "Holiday/Day 2"])
        self.assertEqual(self.playlist(cat, "Holiday")["videos"], ["Holiday/a.mp4", "Holiday/b.mp4"])
        self.assertIsNone(cat["videos"]["Loose video.mp4"]["playlist"])
        self.assertEqual(cat["videos"]["Holiday/a.mp4"]["playlist"], "Holiday")

    def test_folder_with_one_video_is_not_a_playlist(self):
        cat = self.layout(["Zoo trip.mp4", "Film (2010)/Film.2010.1080p.mkv", "Holiday/a.mp4", "Holiday/b.mp4",
                           "Holiday/Day 2/x.mp4"])
        self.assertEqual([p["id"] for p in cat["playlists"]], ["Holiday"])
        # shown on its own like a video in the library folder, ordered by title
        self.assertEqual(cat["standalone"], ["Film (2010)/Film.2010.1080p.mkv", "Holiday/Day 2/x.mp4", "Zoo trip.mp4"])
        self.assertIsNone(cat["videos"]["Holiday/Day 2/x.mp4"]["playlist"])
        self.assertIsNone(cat["videos"]["Film (2010)/Film.2010.1080p.mkv"]["playlist"])

    def test_release_group_series(self):
        names = ["Show 1080p - %02d [x264].mkv" % i for i in range(1, 13)]
        cat = self.layout(["Show/" + n for n in names])
        p = self.playlist(cat, "Show")
        self.assertTrue(p["series"])
        self.assertEqual(p["videos"], ["Show/" + n for n in names])

    def test_seasons_in_subfolders(self):
        files = ["Show/Season 1/Show - 01.mkv", "Show/Season 1/Show - 02.mkv", "Show/Season 1/Show - 10.mkv",
                 "Show/S02/Show.S02E01.mkv", "Show/S02/Show.S02E02.mkv",
                 "Show/Сезон 3/Серия 1.mkv", "Show/Сезон 3/Серия 2.mkv"]
        cat = self.layout(files)
        self.assertEqual([p["id"] for p in cat["playlists"]], ["Show"])
        p = cat["playlists"][0]
        self.assertTrue(p["series"])
        self.assertEqual(p["videos"], files)
        self.assertEqual([(cat["videos"][v]["season"], cat["videos"][v]["episode"]) for v in p["videos"]],
                         [(1, 1), (1, 2), (1, 10), (2, 1), (2, 2), (3, 1), (3, 2)])

    def test_shows_named_with_season_are_not_seasons(self):
        cat = self.layout(["Show Season 1/Show - 01.mkv", "Show Season 1/Show - 02.mkv",
                           "Other Season 2/Other - 01.mkv", "Other Season 2/Other - 02.mkv"])
        self.assertEqual(sorted(p["id"] for p in cat["playlists"]), ["Other Season 2", "Show Season 1"])

    def subtitles(self, cat, video):
        return [t["label"] for t in cat["videos"][video]["subtitles"]]

    def test_subtitles_beside_by_name(self):
        cat = self.layout(["Show/Show - 01.mkv", "Show/Show - 02.mkv",
                           "Show/Show - 01.rus.srt", "Show/Show - 01.en.forced.srt", "Show/Show - 02.srt",
                           "Show/Show - 02.Russian.ass"])
        self.assertEqual(sorted(self.subtitles(cat, "Show/Show - 01.mkv")), ["English (forced)", "Russian"])
        self.assertEqual(sorted(self.subtitles(cat, "Show/Show - 02.mkv")), ["Russian", "Subtitles"])
        track = next(t for t in cat["videos"]["Show/Show - 01.mkv"]["subtitles"] if t["label"] == "Russian")
        self.assertEqual(track["language"], "ru")
        self.assertEqual(track["path"], "Show/Show - 01.rus.srt")

    def test_subtitles_in_subs_by_episode(self):
        # the scenario of behaviour#subtitles-found#scenarios#by-episode
        cat = self.layout(["Show/Show - 01 [1080p].mkv", "Show/Show - 02 [1080p].mkv",
                           "Show/Subs/Show.S01E01.Russian.srt", "Show/Subs/Show.S01E02.Russian.srt",
                           "Show/Subs/Show.S01E07.Russian.srt"])
        self.assertEqual(self.subtitles(cat, "Show/Show - 02 [1080p].mkv"), ["Russian"])
        self.assertEqual(self.subtitles(cat, "Show/Show - 01 [1080p].mkv"), ["Russian"])
        paths = [t["path"] for v in cat["videos"].values() for t in v["subtitles"]]
        self.assertNotIn("Show/Subs/Show.S01E07.Russian.srt", paths)  # matches no video: left out

    def test_subtitles_subfolder_names(self):
        cat = self.layout(["Show/Show - 01.mkv", "Show/Show - 02.mkv",
                           "Show/Subtitles/Show - 01.ru.srt", "Show/Субтитры/Show - 02.ru.srt"])
        self.assertEqual(self.subtitles(cat, "Show/Show - 01.mkv"), ["Russian"])
        self.assertEqual(self.subtitles(cat, "Show/Show - 02.mkv"), ["Russian"])

    def test_subtitles_numbered_without_marker(self):
        cat = self.layout(["Show/[Grp] Show - %02d [720p].mkv" % i for i in range(1, 4)] +
                          ["Show/Subs/Show %02d rus.srt" % i for i in range(1, 4)])
        for i in range(1, 4):
            v = cat["videos"]["Show/[Grp] Show - %02d [720p].mkv" % i]
            self.assertEqual([t["path"] for t in v["subtitles"]], ["Show/Subs/Show %02d rus.srt" % i])

    def test_subtitles_one_subfolder_per_episode(self):
        cat = self.layout(["Show/Show.S01E01.mkv", "Show/Show.S01E02.mkv",
                           "Show/Subs/Show.S01E01/2_English.srt", "Show/Subs/Show.S01E01/3_Russian.srt",
                           "Show/Subs/Show.S01E02/2_English.srt"])
        self.assertEqual(sorted(self.subtitles(cat, "Show/Show.S01E01.mkv")), ["English", "Russian"])
        self.assertEqual(self.subtitles(cat, "Show/Show.S01E02.mkv"), ["English"])

    def test_subtitles_in_season_folders(self):
        cat = self.layout(["Show/Season 1/Show - 01.mkv", "Show/Season 2/Show - 01.mkv",
                           "Show/Season 1/Subs/Show.S01E01.rus.srt", "Show/Season 2/Subs/Show.S01E01.rus.srt"])
        self.assertEqual(self.subtitles(cat, "Show/Season 1/Show - 01.mkv"), ["Russian"])
        # a season-1 file in the season-2 folder belongs to no video there
        self.assertEqual(self.subtitles(cat, "Show/Season 2/Show - 01.mkv"), [])

    def test_folder_with_one_video_takes_every_subtitle(self):
        cat = self.layout(["Film (2010)/Film.2010.1080p.mkv", "Film (2010)/Subs/English.srt",
                           "Film (2010)/Subs/Russian.srt", "Film (2010)/commentary.srt"])
        self.assertEqual(sorted(self.subtitles(cat, "Film (2010)/Film.2010.1080p.mkv")),
                         ["English", "Russian", "commentary"])

    def test_folder_with_one_video_leaves_out_other_episodes(self):
        # one episode downloaded, the subtitles of the whole season beside it
        cat = self.layout(["Show/Show - 01 [1080p].mkv", "Show/Subs/Show.S01E01.Russian.srt",
                           "Show/Subs/Show.S01E02.Russian.srt", "Show/Subs/Show.S01E03.Russian.srt",
                           "Show/English.srt"])
        v = cat["videos"]["Show/Show - 01 [1080p].mkv"]
        self.assertEqual(sorted(t["path"] for t in v["subtitles"]),
                         ["Show/English.srt", "Show/Subs/Show.S01E01.Russian.srt"])
        cat = self.layout(["Show/Show.S01E04.mkv", "Show/Show.S01E04.rus.srt", "Show/Show.S01E05.rus.srt"])
        self.assertEqual([t["path"] for t in cat["videos"]["Show/Show.S01E04.mkv"]["subtitles"]],
                         ["Show/Show.S01E04.rus.srt"])

    def test_unmatched_subtitles_are_left_out(self):
        cat = self.layout(["Clips/a.mp4", "Clips/b.mp4", "Clips/random.srt"])
        self.assertEqual(self.subtitles(cat, "Clips/a.mp4"), [])
        self.assertEqual(self.subtitles(cat, "Clips/b.mp4"), [])

    def test_extras_of_a_series_are_titled_after_it(self):
        cat = self.layout(["Show/Season 1/Show - 01.mkv", "Show/Season 1/Show - 02.mkv",
                           "Show/Season 2/Show - 01.mkv", "Show/Season 2/Show - 02.mkv",
                           "Show/Bonus/Making of.mkv", "Show/Bonus/Bloopers.mkv",
                           "Other/S01/Other - 01.mkv", "Other/S01/Other - 02.mkv",
                           "Other/Bonus/a.mkv", "Other/Bonus/b.mkv"])
        titles = {p["id"]: p["title"] for p in cat["playlists"]}
        self.assertEqual(titles, {"Show": "Show", "Show/Bonus": "Show · Bonus",
                                  "Other": "Other", "Other/Bonus": "Other · Bonus"})
        self.assertEqual(cat["videos"]["Show/Bonus/Bloopers.mkv"]["playlist"], "Show/Bonus")

    def test_extras_of_a_series_without_seasons_are_titled_after_it(self):
        # the episodes lie directly in the series' folder, the extras beside them
        cat = self.layout(["Show/Show - 01.mkv", "Show/Show - 02.mkv", "Show/Show - 03.mkv",
                           "Show/OVA/Show - OVA 1.mkv", "Show/OVA/Show - OVA 2.mkv",
                           "Other/Other - 01.mkv", "Other/Other - 02.mkv",
                           "Other/OVA/a.mkv", "Other/OVA/b.mkv",
                           "Holiday/a.mp4", "Holiday/b.mp4", "Holiday/Day 2/x.mp4", "Holiday/Day 2/y.mp4"])
        titles = {p["id"]: p["title"] for p in cat["playlists"]}
        self.assertEqual(titles, {"Show": "Show", "Show/OVA": "Show · OVA",
                                  "Other": "Other", "Other/OVA": "Other · OVA",
                                  # a folder that is not a series titles its subfolders alone
                                  "Holiday": "Holiday", "Holiday/Day 2": "Day 2"})
        self.assertEqual(cat["videos"]["Show/OVA/Show - OVA 2.mkv"]["playlist"], "Show/OVA")

    def test_subtitles_per_translator(self):
        # the scenario of behaviour#subtitles-found#scenarios#translators, with
        # the episode alone and among others
        for videos in (["Show/Show - 01 [1080p].mkv"],
                       ["Show/Show - 01 [1080p].mkv", "Show/Show - 02 [1080p].mkv"]):
            cat = self.layout(videos + ["Show/RUS Subs/CafeSubs/Show - 01 [1080p].ass",
                                        "Show/RUS Subs/Crunchyroll/Show - 01 [1080p].ass"])
            self.assertEqual(sorted(self.subtitles(cat, "Show/Show - 01 [1080p].mkv")),
                             ["Russian — CafeSubs", "Russian — Crunchyroll"])
            track = cat["videos"]["Show/Show - 01 [1080p].mkv"]["subtitles"][0]
            self.assertEqual(track["language"], "ru")
            if len(videos) == 2:
                self.assertEqual(self.subtitles(cat, "Show/Show - 02 [1080p].mkv"), [])

    def test_subtitles_signs_and_deep_folders(self):
        cat = self.layout(["Show/Show - 01.mkv", "Show/Show - 02.mkv",
                           "Show/[Subtitle]/Crunchyroll/Signs/Show - 01.rus.ass",
                           "Show/[Subtitle]/Crunchyroll/Show - 01.rus.ass",
                           "Show/Subs/Show - 02.eng.signs.ass",
                           "Show/Subs/Show - 02 commentary.srt", "Show/Subs/Russian/Show - 02.srt"])
        self.assertEqual(sorted(self.subtitles(cat, "Show/Show - 01.mkv")),
                         ["Russian — Crunchyroll", "Russian — Crunchyroll (signs)"])
        self.assertEqual(sorted(self.subtitles(cat, "Show/Show - 02.mkv")),
                         ["English (signs)", "Russian", "commentary"])
        # signs-only tracks are marked, for behaviour#subtitles-follow
        signs = {t["label"]: t["signs"] for v in cat["videos"].values() for t in v["subtitles"]}
        self.assertEqual(signs, {"Russian — Crunchyroll": False, "Russian — Crunchyroll (signs)": True,
                                 "English (signs)": True, "Russian": False, "commentary": False})

    def test_subtitles_of_another_group(self):
        # the scenario of behaviour#subtitles-found#scenarios#other-group
        cat = self.layout(["Show/Show - 01.mkv", "Show/OVA/Show - OVA 1.mkv",
                           "Show/[Subtitle]/OVA/Show - OVA 1.rus.ass"])
        self.assertEqual(self.subtitles(cat, "Show/Show - 01.mkv"), [])
        self.assertEqual(self.subtitles(cat, "Show/OVA/Show - OVA 1.mkv"), ["Russian"])

    def test_episode_number_needs_the_same_text_before_it(self):
        # `Show - OVA 2.ass` does not find episode 2
        cat = self.layout(["Show/Show - 01.mkv", "Show/Show - 02.mkv",
                           "Show/Subs/Show - OVA 1.ass", "Show/Subs/Show - OVA 2.ass",
                           "Show/Subs/Show.S01E02.Russian.srt"])
        self.assertEqual(self.subtitles(cat, "Show/Show - 01.mkv"), [])
        self.assertEqual(self.subtitles(cat, "Show/Show - 02.mkv"), ["Russian"])

    def test_subtitles_of_a_season_folder_stay_with_its_season(self):
        # the same file names in two seasons: each season's Subs is its own
        cat = self.layout(["Show/Season 1/Show - 01.mkv", "Show/Season 1/Show - 02.mkv",
                           "Show/Season 2/Show - 01.mkv", "Show/Season 2/Show - 02.mkv",
                           "Show/Season 1/Subs/Show - 01.rus.srt", "Show/Season 2/Subs/Show - 01.rus.srt",
                           "Show/Subs/Season 2/Show - 02.eng.srt"])
        self.assertEqual([t["path"] for t in cat["videos"]["Show/Season 1/Show - 01.mkv"]["subtitles"]],
                         ["Show/Season 1/Subs/Show - 01.rus.srt"])
        self.assertEqual([t["path"] for t in cat["videos"]["Show/Season 2/Show - 01.mkv"]["subtitles"]],
                         ["Show/Season 2/Subs/Show - 01.rus.srt"])
        self.assertEqual([t["path"] for t in cat["videos"]["Show/Season 2/Show - 02.mkv"]["subtitles"]],
                         ["Show/Subs/Season 2/Show - 02.eng.srt"])
        self.assertEqual(cat["videos"]["Show/Season 1/Show - 02.mkv"]["subtitles"], [])

    def test_other_series_keep_their_subtitles(self):
        # two shows in a folder of their own: neither sees the other's subtitles
        cat = self.layout(["Anime/A/A - 01.mkv", "Anime/A/A - 02.mkv",
                           "Anime/B/Film.mkv", "Anime/A/Subs/A - 01.rus.srt", "Anime/B/Subs/English.srt"])
        self.assertEqual(self.subtitles(cat, "Anime/A/A - 01.mkv"), ["Russian"])
        self.assertEqual(self.subtitles(cat, "Anime/B/Film.mkv"), ["English"])
        self.assertEqual(self.subtitles(cat, "Anime/A/A - 02.mkv"), [])


    # ---------------------------------------------------------------- sound

    def audio(self, cat, video):
        return [t["label"] for t in cat["videos"][video]["audio"]]

    def test_external_dub(self):
        # the scenario of behaviour#audio-tracks#scenarios#external-dub, with
        # the episode alone and among others
        for videos in (["Show/Show - 01.mkv"], ["Show/Show - 01.mkv", "Show/Show - 02.mkv"]):
            cat = self.layout(videos + ["Show/[Audio]/STEPonee/Show - 01.STEPonee.mka"])
            tracks = cat["videos"]["Show/Show - 01.mkv"]["audio"]
            self.assertEqual([(t["kind"], t["label"], t["default"]) for t in tracks],
                             [("embedded", "Track 1", True), ("external", "STEPonee", False)])
            self.assertEqual(tracks[1]["path"], "Show/[Audio]/STEPonee/Show - 01.STEPonee.mka")
            if len(videos) == 2:
                self.assertEqual(self.audio(cat, "Show/Show - 02.mkv"), ["Track 1"])

    def test_dubs_per_folder_by_episode(self):
        cat = self.layout(["Show/Show - 01 [1080p].mkv", "Show/Show - 02 [1080p].mkv",
                           "Show/RUS Sound/AniDub/Show.S01E01.mka", "Show/RUS Sound/AniDub/Show.S01E02.mka",
                           "Show/RUS Sound/STEPonee/Show - 02 [1080p].ac3",
                           "Show/Show - 01 [1080p].eng.flac",
                           "Show/[Audio]/AniDub/Show.S01E07.mka"])
        self.assertEqual(self.audio(cat, "Show/Show - 01 [1080p].mkv"),
                         ["Track 1", "English", "Russian — AniDub"])
        self.assertEqual(self.audio(cat, "Show/Show - 02 [1080p].mkv"),
                         ["Track 1", "Russian — AniDub", "Russian — STEPonee"])
        languages = {t["label"]: t["language"] for v in cat["videos"].values() for t in v["audio"]}
        self.assertEqual(languages["Russian — AniDub"], "ru")
        # matches no video: left out
        paths = [t.get("path") for v in cat["videos"].values() for t in v["audio"]]
        self.assertNotIn("Show/[Audio]/AniDub/Show.S01E07.mka", paths)

    def test_dubs_of_another_group(self):
        cat = self.layout(["Show/Show - 01.mkv", "Show/OVA/Show - OVA 1.mkv",
                           "Show/[Audio]/OVA/Show - OVA 1.rus.mka"])
        self.assertEqual(self.audio(cat, "Show/Show - 01.mkv"), ["Track 1"])
        self.assertEqual(self.audio(cat, "Show/OVA/Show - OVA 1.mkv"), ["Track 1", "Russian"])

    def test_sound_and_subtitles_keep_apart(self):
        cat = self.layout(["Show/Show - 01.mkv", "Show/Show - 02.mkv",
                           "Show/Subs/CafeSubs/Show - 01.ass", "Show/[Audio]/STEPonee/Show - 01.mka"])
        v = cat["videos"]["Show/Show - 01.mkv"]
        self.assertEqual([t["label"] for t in v["subtitles"]], ["CafeSubs"])
        self.assertEqual([t["label"] for t in v["audio"]], ["Track 1", "STEPonee"])

    def test_labels_of_the_streams(self):
        streams = {".mkv": [{"stream": 1, "codec": "flac", "language": "jpn", "title": None},
                            {"stream": 2, "codec": "eac3", "language": "rus", "title": "Dub [DEEP]"},
                            {"stream": 3, "codec": "aac", "language": None, "title": None},
                            {"stream": 4, "codec": "aac", "language": "eng", "title": "English commentary"}],
                   ".mka": [{"stream": 0, "codec": "ac3", "language": "rus", "title": "AniLibria"}]}

        def probe(path):
            return dict(no_probe(path), audio=streams[os.path.splitext(path)[1]])

        # the default is the file's first track; an external file's stream
        # says its language and its dub's name when its name and folders do not
        cat = self.layout(["Film/Film.mkv", "Film/Film.mka"], probe=probe)
        tracks = cat["videos"]["Film/Film.mkv"]["audio"]
        self.assertEqual([(t["label"], t["language"], t["default"]) for t in tracks],
                         [("Japanese", "ja", True), ("Russian — Dub [DEEP]", "ru", False),
                          ("Track 3", None, False), ("English commentary", "en", False),
                          ("Russian — AniLibria", "ru", False)])
        self.assertEqual([t["id"] for t in tracks if t["kind"] == "embedded"], ["e1", "e2", "e3", "e4"])


class LibraryFolders(unittest.TestCase):
    """library#scan over several library folders: paths begin with the name
    of their library folder, removed paths are left out, and a title stored
    in the file is used when it says more than the file's name."""

    def make(self, *layouts):
        """Library folders, each given as (folder name, [files])."""
        tmp = tempfile.mkdtemp(prefix="mirutube-folders-")
        self.addCleanup(shutil.rmtree, tmp, True)
        roots = []
        for n, (name, files) in enumerate(layouts):
            root = os.path.join(tmp, str(n), name)
            for rel in files:
                path = os.path.join(root, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, "wb").close()
            os.makedirs(root, exist_ok=True)
            roots.append(root)
        return roots

    def test_paths_begin_with_the_library_folder(self):
        films, shows = self.make(("Films", ["Film.mkv"]), ("Shows", ["Show/Show - 01.mkv", "Show/Show - 02.mkv"]))
        cat = scan([films, shows], probe=no_probe)
        self.assertEqual(cat["standalone"], ["Films/Film.mkv"])
        self.assertEqual([p["id"] for p in cat["playlists"]], ["Shows/Show"])
        self.assertEqual(cat["playlists"][0]["videos"], ["Shows/Show/Show - 01.mkv", "Shows/Show/Show - 02.mkv"])
        self.assertEqual(cat["videos"]["Shows/Show/Show - 01.mkv"]["playlist"], "Shows/Show")

    def test_two_folders_of_one_name_give_two_paths(self):
        one, two = self.make(("Videos", ["Clip.mp4"]), ("Videos", ["Clip.mp4"]))
        self.assertEqual(folder_names([one, two]), {"Videos": one, "Videos (2)": two})
        cat = scan([one, two], probe=no_probe)
        self.assertEqual(sorted(cat["videos"]), ["Videos (2)/Clip.mp4", "Videos/Clip.mp4"])

    def test_removed_paths_are_left_out(self):
        root, = self.make(("Library", ["Recordings/a.mp4", "Recordings/b.mp4", "Recordings/Old/c.mp4",
                                       "Recordings/Old/d.mp4", "Show/Show - 01.mkv", "Show/Show - 02.mkv",
                                       "Show/Show - 03.mkv", "Loose.mp4", "Other.mp4"]))
        cat = scan([root], probe=no_probe, excluded=["Library/Recordings", "Library/Loose.mp4",
                                                    "Library/Show/Show - 02.mkv"])
        self.assertEqual(sorted(cat["videos"]), ["Library/Other.mp4", "Library/Show/Show - 01.mkv",
                                                 "Library/Show/Show - 03.mkv"])
        self.assertEqual([p["id"] for p in cat["playlists"]], ["Library/Show"])
        # a folder removed takes what lies inside it, and nothing beside it
        cat = scan([root], probe=no_probe, excluded=["Library/Recordings/Old", "Library/Show/Show"])
        self.assertNotIn("Library/Recordings/Old/c.mp4", cat["videos"])
        self.assertIn("Library/Recordings/a.mp4", cat["videos"])
        self.assertIn("Library/Show/Show - 01.mkv", cat["videos"])
        # a whole library folder
        self.assertEqual(scan([root], probe=no_probe, excluded=["Library"])["videos"], {})

    def test_title_stored_in_the_file(self):
        root, = self.make(("Library", ["Show/[Grp] Show - 01 [1080p].mkv", "Show/[Grp] Show - 02 [1080p].mkv",
                                       "Show/[Grp] Show - 03 [1080p].mkv"]))
        titles = {"[Grp] Show - 01 [1080p].mkv": "Show - 01 - The Beginning",
                  "[Grp] Show - 02 [1080p].mkv": "[Grp] Show - 02 [1080p]",  # only the name again
                  "[Grp] Show - 03 [1080p].mkv": "Show 03"}                  # a part of the name
        probe = lambda path: dict(no_probe(path), title=titles[os.path.basename(path)])
        cat = scan([root], probe=probe)
        self.assertEqual([cat["videos"]["Library/Show/[Grp] Show - %02d [1080p].mkv" % n]["title"] for n in (1, 2, 3)],
                         ["Show - 01 - The Beginning", "Show - 02", "Show - 03"])
        self.assertEqual(file_title("  ", "a.mkv"), None)


if __name__ == "__main__":
    unittest.main()
