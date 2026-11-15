"""Turns the library folders into the catalogue — playlists, the videos in
each in order, and the subtitle and sound tracks of every video. A video's
path within the library begins with the name of its library folder.

It rescans when a catalogue request finds the last scan older than a few
seconds, which is how a video copied into the folder appears without a
restart (behaviour#new-files).
"""

import hashlib
import os
import re
import threading
import time

from mirutube import media

RESCAN_AFTER = 3.0  # seconds

VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mkv", ".webm", ".mov", ".avi", ".wmv", ".flv",
                    ".mpg", ".mpeg", ".ts", ".m2ts", ".ogv", ".3gp", ".vob", ".divx"}
SUBTITLE_EXTENSIONS = {".srt", ".vtt", ".ass", ".ssa"}
# External sound files: dubs that come beside the video or in folders of
# their own, `[Audio]/STEPonee/Show - 01.STEPonee.mka`.
AUDIO_EXTENSIONS = {".mka", ".ac3", ".eac3", ".dts", ".flac", ".aac", ".m4a", ".mp3", ".opus"}
SUBTITLE_FOLDERS = {"subs", "subtitles", "субтитры"}
# Words a folder named for subtitles or for signs is made of: `RUS Subs`,
# `[Subtitle]`, `Signs`, `Надписи`.
SUBTITLE_WORDS = {"sub", "subs", "subtitle", "subtitles", "субтитры", "сабы"}
SIGNS_WORDS = {"sign", "signs", "надписи"}
# Words a folder named for sound tracks is made of: `[Audio]`, `RUS Sound`,
# `Озвучка` — never a dub's own name.
AUDIO_WORDS = {"audio", "audios", "sound", "sounds", "dub", "dubs", "voice", "voices",
               "звук", "аудио", "озвучка", "озвучки", "дорожки"}

# What current Chrome and Firefox both play as it is, sent with Range
# (decisions#stream): the container — by the file's extension, and its name
# among ffprobe's format names — and the codecs; H.264 only at 8 bits and
# 4:2:0 (media.DIRECT_PIX_FMTS).
DIRECT_CONTAINERS = {".mp4": "mp4", ".m4v": "mp4", ".webm": "webm"}
DIRECT_VIDEO_CODECS = {"vp9", "av1"}
DIRECT_AUDIO_CODECS = {"aac", "mp3", "opus", "vorbis"}

LANGUAGES = {}
for _code, _name, _words in [
    ("ru", "Russian", "ru rus russian русский русские рус"),
    ("en", "English", "en eng english английский"),
    ("uk", "Ukrainian", "uk ukr ukrainian українська украинский"),
    ("be", "Belarusian", "bel belarusian"),
    ("kk", "Kazakh", "kk kaz kazakh"),
    ("de", "German", "de ger deu german deutsch"),
    ("fr", "French", "fr fre fra french français"),
    ("es", "Spanish", "es spa spanish español"),
    ("it", "Italian", "ita italian"),
    ("pt", "Portuguese", "pt por portuguese"),
    ("pl", "Polish", "pl pol polish"),
    ("cs", "Czech", "cs cze ces czech"),
    ("nl", "Dutch", "nl dut nld dutch"),
    ("sv", "Swedish", "sv swe swedish"),
    ("fi", "Finnish", "fi fin finnish"),
    ("tr", "Turkish", "tr tur turkish"),
    ("ja", "Japanese", "ja jp jpn japanese"),
    ("zh", "Chinese", "zh chi zho chinese chs cht"),
    ("ko", "Korean", "ko kor korean"),
    ("ar", "Arabic", "ar ara arabic"),
    ("he", "Hebrew", "he heb hebrew"),
    ("hu", "Hungarian", "hu hun hungarian"),
    ("ro", "Romanian", "ro rum ron romanian"),
    ("el", "Greek", "el gre ell greek"),
]:
    for _w in _words.split():
        LANGUAGES[_w] = (_code, _name)
FORCED_WORDS = {"forced", "форсированные"}

_TAG_RE = re.compile(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}")
_YEAR_RE = re.compile(r"^[\[(]\s*(?:19|20)\d\d\s*[\])]$")
# A season folder is named for its season alone: `Season 1`, `S02`, `Сезон 3`,
# `2 сезон`. A name with more in it, such as `Show Season 2`, is a show of its own.
_SEASON_FOLDER_RE = re.compile(r"^(?:(?:season|сезон|s)\s?(\d{1,3})|(\d{1,3})\s?(?:season|сезон))$", re.I)
_MARKERS = [
    (re.compile(r"(?<![^\W\d_])s(\d{1,2})\s?e(\d{1,4})(?!\d)", re.I), True),
    (re.compile(r"(?<![\w])(\d{1,2})x(\d{1,4})(?![\w])", re.I), True),
    (re.compile(r"(?<![^\W\d_])(?:episode|ep|e|серия|эпизод)\s?(\d{1,4})(?!\d)", re.I), False),
    (re.compile(r"(?<!\d)(\d{1,4})\s?(?:серия|эпизод)(?![^\W\d_])", re.I), False),
]


# ---------------------------------------------------------------- naming

def normalise(name, is_file=True):
    """The name as order_episodes reads it: the extension dropped, dots and
    underscores read as spaces, bracketed release tags removed, a bracketed
    year kept."""
    if is_file:
        name = os.path.splitext(name)[0]
    name = re.sub(r"[._]", " ", name)
    name = _TAG_RE.sub(lambda m: m.group(0) if _YEAR_RE.match(m.group(0)) else " ", name)
    return " ".join(name.split()).strip(" -")


def natural_key(text):
    """Natural sort: `2` before `10`."""
    parts = re.split(r"(\d+)", text.casefold())
    return [int(p) if i % 2 else p for i, p in enumerate(parts)]


def explicit_marker(norm, with_prefix=False):
    """(season or None, episode) from an explicit marker, or None; with
    `with_prefix`, the text before the marker is added as a third item."""
    for regex, has_season in _MARKERS:
        m = regex.search(norm)
        if m:
            if has_season:
                found = int(m.group(1)), int(m.group(2))
            else:
                found = None, int(m.group(1))
            return found + (norm[:m.start()],) if with_prefix else found
    return None


def text_key(text):
    """Text with case and punctuation set aside: what is compared when the text
    before an episode number must be the same in two names."""
    return re.sub(r"[\W_]+", "", text.casefold())


def varying_numbers(norms):
    """For each normalised name, the one number whose value differs between the
    names while the text before it is the same in them, or nothing. Only the
    text before the number is compared: what follows an episode number is often
    the episode's own title."""
    slots = {}  # prefix template -> {name: (value, text before it)}
    for norm in set(norms):
        parts = re.split(r"(\d+)", norm.casefold())
        for i in range(1, len(parts), 2):
            before = "".join(parts[:i])
            prefix = re.sub(r"\d+", "#", before)
            slots.setdefault(prefix, {})[norm] = (int(parts[i]), before)
    varying = [values for values in slots.values()
               if len(values) >= 2 and len({v for v, _ in values.values()}) >= 2]
    if len(varying) != 1:
        return {}
    return dict(varying[0])


def episode_info(names, is_file=True):
    """name -> (season or None, episode or None, text before the number or
    None), in order of trust: an explicit marker, otherwise the one number that
    varies between the names."""
    norms = {n: normalise(n, is_file) for n in names}
    varying = varying_numbers(list(norms.values()))
    result = {}
    for name, norm in norms.items():
        marker = explicit_marker(norm, with_prefix=True)
        if marker:
            result[name] = marker
        elif norm in varying:
            result[name] = (None,) + varying[norm]
        else:
            result[name] = (None, None, None)
    return result


def episode_numbers(names, is_file=True):
    """name -> (season or None, episode or None), as episode_info reads them."""
    return {n: info[:2] for n, info in episode_info(names, is_file).items()}


def order_episodes(names, season=None):
    """Orders the videos of one folder and decides whether it is a series.

    Returns (is_series, items) where items are dicts {name, title, season,
    episode, before} in playing order, `before` being the text before the
    episode number. `season` is the season the folder stands for,
    used for videos whose names do not carry one."""
    numbers = episode_info(names)
    items = []
    for name in names:
        s, e, before = numbers[name]
        items.append({"name": name, "title": normalise(name) or name,
                      "season": s if s is not None else season,
                      "episode": e, "before": before})
    numbered = [i for i in items if i["episode"] is not None]
    is_series = bool(items) and len(numbered) * 2 > len(items)
    if is_series:
        rest = [i for i in items if i["episode"] is None]
        numbered.sort(key=lambda i: (i["season"] or 0, i["episode"], natural_key(i["name"])))
        rest.sort(key=lambda i: natural_key(i["name"]))
        return True, numbered + rest
    items.sort(key=lambda i: natural_key(i["name"]))
    return False, items


def season_of_folder(name):
    m = _SEASON_FOLDER_RE.match(re.sub(r"\s*\((?:19|20)\d\d\)", "", normalise(name, is_file=False)))
    if not m:
        return None
    return int(next(g for g in m.groups() if g))


# ------------------------------------------------------------- subtitles

def _language_in(tokens):
    """(code, name) of a language among the last few tokens, scanning from the end."""
    for tok in reversed(tokens[-3:]):
        lang = LANGUAGES.get(tok.casefold())
        if lang:
            return lang
    return None


def _tokens(text):
    return [t for t in re.split(r"[\s._\-()\[\]{}&,+]+", text) if t]


def _named_for(name, words):
    """Whether the name is made of nothing but `words` and languages:
    `Subs`, `RUS Subs`, `[Subtitle]`, `Russian` for subtitles, `[Audio]` or
    `RUS Sound` for sound."""
    tokens = [t.casefold() for t in _tokens(name)]
    return bool(tokens) and all(t in words or t in LANGUAGES for t in tokens)


def _named_for_subtitles(name):
    return _named_for(name, SUBTITLE_WORDS) or name.casefold() in SUBTITLE_FOLDERS


def _named_for_signs(name):
    return any(t.casefold() in SIGNS_WORDS for t in _tokens(name))


def _stem_match(name, stem):
    """Whether `name` starts with the video's own name `stem`, ending there or
    going on after something that is not a letter or digit — so `Show - 1`
    does not claim the subtitles of `Show - 10`."""
    n, v = name.casefold(), stem.casefold()
    return n.startswith(v) and (len(n) == len(v) or not n[len(v)].isalnum())


def _external_files(top, skip, extensions, words, only_named_folders=False):
    """Every file with one of `extensions` under `top`, at any depth, leaving
    out the folders in `skip` — other series' top folders, whose files are
    theirs. With `only_named_folders`, only the files in `top` itself and in
    folders named for them (made of `words`) are taken."""
    found = []
    stack = [top]
    while stack:
        d = stack.pop()
        try:
            entries = sorted(os.scandir(d), key=lambda e: natural_key(e.name))
        except OSError:
            continue
        subdirs = []
        for e in entries:
            if e.name.startswith("."):
                continue
            if e.is_dir():
                if e.path in skip:
                    continue
                if only_named_folders and d == top and not _named_for(e.name, words):
                    continue
                subdirs.append(e.path)
            elif e.is_file() and os.path.splitext(e.name)[1].lower() in extensions:
                found.append(e.path)
        stack.extend(reversed(subdirs))
    return found


def _embedded_tracks(probe_info):
    tracks = []
    for s in probe_info.get("subtitles") or []:
        lang = LANGUAGES.get((s.get("language") or "").casefold())
        label = s.get("title") or (lang[1] if lang else None) or \
            (s.get("language") if s.get("language") not in (None, "und") else None) or \
            "Track %d" % (len(tracks) + 1)
        if s.get("forced") and "forced" not in label.casefold():
            label += " (forced)"
        track = {"id": "e%d" % s["stream"], "kind": "embedded", "stream": s["stream"],
                 "label": label, "language": lang[0] if lang else s.get("language"),
                 "signs": _named_for_signs(s.get("title") or ""),
                 "available": not s.get("image"), "reason": None}
        if s.get("image"):
            kind = media.IMAGE_SUBTITLE_NAMES.get(s.get("codec"), s.get("codec") or "unknown")
            track["reason"] = "image subtitles (%s) cannot be shown as text" % kind
        tracks.append(track)
    return tracks


def _names_other_episode(names, video):
    """Whether an explicit marker in the file's name, or in a folder it is in,
    names an episode other than the video's. A video alone in its folder
    rarely has an episode of its own — no other file to compare its numbers
    with — so there a marker names another episode unless its number is one
    of the numbers in the video's name: `Show.S01E02.srt` beside
    `Show - 01 [1080p].mkv` is episode 2's, not this one's."""
    for name in names:
        marker = explicit_marker(normalise(name, is_file=False))
        if marker:
            season, episode = marker
            if season is not None and video.get("season") is not None and season != video["season"]:
                return True
            if video.get("episode") is not None:
                return episode != video["episode"]
            own = {int(n) for n in re.findall(r"\d+", normalise(video["name"]))}
            return episode not in own
    return False


def _under(path, folder):
    return path == folder or path.startswith(folder + os.sep)


def _match_external(top, videos, extensions, words, skip=(), loose=False):
    """Matches the external files of one kind — subtitles or sound — to the
    videos of one series' top folder, by the rules of match_subtitles, which
    match_audio shares because releases lay both out the same way.

    `words` are what a folder named for this kind of file is made of. Returns
    [(path, video, rest, inner, translator)] for every file that matches a
    video: `rest` is what follows the video's name in the file's (None when
    it was matched otherwise), `inner` the folders between the file and its
    videos, nearest first, and `translator` the name of the nearest of them
    that names a translator or a dub, or None."""
    stems = {v["path"]: os.path.splitext(v["name"])[0] for v in videos}
    video_folders = {}
    for v in videos:
        video_folders.setdefault(v["folder"], []).append(v)
    # groups of videos by folder name: what `[Subtitle]/OVA` is named after
    group_keys = {}
    for f in video_folders:
        if f != top:
            group_keys.setdefault(text_key(normalise(os.path.basename(f), is_file=False)), []).append(f)
    group_keys.pop("", None)

    candidates = _external_files(top, set(skip), extensions, words, only_named_folders=loose)

    # Episode numbers of the files, read with the rules of order_episodes
    # among the files of the same folder, and of the folders holding them
    # among their siblings (one folder per episode).
    by_dir = {}
    for path in candidates:
        by_dir.setdefault(os.path.dirname(path), []).append(os.path.basename(path))
    file_numbers = {}
    for d, names in by_dir.items():
        for n, info in episode_info(names).items():
            file_numbers[os.path.join(d, n)] = info
    siblings = {}
    for d in by_dir:
        if d != top:
            siblings.setdefault(os.path.dirname(d), set()).add(os.path.basename(d))
    folder_numbers = {}
    for parent, names in siblings.items():
        for n, info in episode_info(sorted(names), is_file=False).items():
            folder_numbers[os.path.join(parent, n)] = info

    def by_name(names, scope):
        """(video, the rest of the name after the video's) for the video the
        first of `names` starts with — the longest such name, and only one."""
        for name in names:
            hits = [v for v in scope if _stem_match(name, stems[v["path"]])]
            if not hits:
                continue
            longest = max(len(stems[v["path"]]) for v in hits)
            hits = [v for v in hits if len(stems[v["path"]]) == longest]
            if len(hits) == 1:
                return hits[0], name[longest:]
        return None, None

    def by_episode(info, scope):
        """The one video with the episode number, where the text before the
        number is the same in both names once case and punctuation are set
        aside: `Show - OVA 2` is not episode 2 of `Show`."""
        season, episode, before = info
        if episode is None:
            return None
        hits = [v for v in scope if v.get("episode") == episode and
                v.get("before") is not None and text_key(v["before"]) == text_key(before) and
                (season is None or v.get("season") is None or v.get("season") == season)]
        return hits[0] if len(hits) == 1 else None

    def is_translator(folder, scope):
        """The translator — or the dub — is the nearest folder above the file
        not named for this kind of file, for signs, after a group of videos or
        after one episode."""
        name = os.path.basename(folder)
        if _named_for(name, words) or _named_for_signs(name):
            return False
        key = text_key(normalise(name, is_file=False))
        if not key or key in group_keys or season_of_folder(name) is not None:
            return False
        if folder_numbers.get(folder, (None, None, None))[1] is not None:
            return False
        return by_name([name], scope)[0] is None

    matched = []
    for path in candidates:
        name = os.path.basename(path)
        stem = os.path.splitext(name)[0]
        # the folders between the file and its videos, nearest first
        inner = []
        d = os.path.dirname(path)
        while d not in video_folders and d != top:
            inner.append(d)
            d = os.path.dirname(d)
        base = d
        scope = [v for v in videos if _under(v["folder"], base)]
        for f in inner:
            groups = [g for g in group_keys.get(text_key(normalise(os.path.basename(f), is_file=False)), [])
                      if _under(g, base) and g != base]
            if groups:
                scope = [v for v in videos if any(_under(v["folder"], g) for g in groups)]
                break
        if not scope:
            continue
        folder_names = [os.path.basename(f) for f in inner]

        # 1. by name: the file's, or that of a folder it is in
        target, rest = by_name([stem], scope)
        if target is None:
            target = by_name(folder_names, scope)[0]
        # 2. by episode number
        if target is None:
            target = by_episode(file_numbers[path], scope)
        if target is None and inner:
            target = by_episode(folder_numbers.get(inner[0], (None, None, None)), scope)
        # 3. a folder with one video takes every such file in it whose name
        # does not mark another episode, since subtitles or sound from
        # another episode are worse than none (behaviour#subtitles-found)
        if target is None and len(scope) == 1 and \
                not _names_other_episode([stem] + folder_names, scope[0]):
            target = scope[0]
        if target is None:
            continue
        translator = next((normalise(os.path.basename(f), is_file=False) or os.path.basename(f)
                           for f in inner if is_translator(f, scope)), None)
        matched.append((path, target, rest, inner, translator))
    return matched


def _language_of(path, rest, inner):
    """(code, name) of the language a file's name gives — from what follows
    the video's name, else from a folder above it — or None."""
    stem = os.path.splitext(os.path.basename(path))[0]
    lang = _language_in(_tokens(rest if rest is not None else stem))
    if not lang:
        for f in inner:
            lang = _language_in(_tokens(os.path.basename(f)))
            if lang:
                break
    return lang


def _number_labels(tracks):
    """Labels made unique within one video's tracks: `Russian`, `Russian 2`."""
    seen = {}
    for t in tracks:
        n = seen.get(t["label"], 0) + 1
        seen[t["label"]] = n
        if n > 1:
            t["label"] = "%s %d" % (t["label"], n)


def match_subtitles(top, videos, probes=None, skip=(), loose=False):
    """Finds the subtitle tracks of the videos of one series' top folder.

    `videos` are dicts {path, name, folder, season, episode, before} for the
    video files whose series' top folder is `top` (`path` and `folder`
    absolute, `before` the text before the episode number); `probes` maps a
    video path to its media#probe result, the source of embedded tracks.
    `skip` holds the other series' top folders below `top`. `loose` is for
    the videos directly in the library folder, which are in no series: their
    subtitles are looked for beside them and in folders named for subtitles.

    External files are looked for beside the videos and in every folder under
    `top` that holds no videos. A file is offered to the videos of the
    nearest folder above it that holds videos — `Season 2/Subs` is season
    2's — narrowed to one group of videos when a folder on its way is named
    after it, as `[Subtitle]/OVA` is. Returns {video path: [track]}, a track
    being {id, kind, label, language, signs, available, reason} plus `stream`
    (embedded) or `path` (external, absolute). A file that matches no video
    is left out rather than shown with the wrong one."""
    probes = probes or {}
    tracks = {v["path"]: _embedded_tracks(probes.get(v["path"]) or {}) for v in videos}
    counter = 0
    for path, target, rest, inner, translator in _match_external(
            top, videos, SUBTITLE_EXTENSIONS, SUBTITLE_WORDS, skip, loose):
        name = os.path.basename(path)
        stem = os.path.splitext(name)[0]
        folder_names = [os.path.basename(f) for f in inner]
        # the label: language, translator, signs or forced
        own = _tokens(rest if rest is not None else stem)
        lang = _language_of(path, rest, inner)
        flags = []
        if any(t.casefold() in SIGNS_WORDS for t in own) or any(_named_for_signs(f) for f in folder_names):
            flags.append("signs")
        if any(t.casefold() in FORCED_WORDS for t in own):
            flags.append("forced")
        if lang and translator:
            label = "%s — %s" % (lang[1], translator)
        elif lang or translator:
            label = lang[1] if lang else translator
        else:
            # nothing but its name: what the name says beside the video's, if
            # anything (`commentary`)
            words = [t for t in (own if rest is not None else _tokens(normalise(name)))
                     if t.casefold() not in FORCED_WORDS and t.casefold() not in SIGNS_WORDS]
            label = " ".join(words) or "Subtitles"
        if flags:
            label += " (%s)" % ", ".join(flags)
        tracks[target["path"]].append({"id": "x%d" % counter, "kind": "external", "path": path,
                                       "label": label, "language": lang[0] if lang else None,
                                       "signs": "signs" in flags,
                                       "available": True, "reason": None})
        counter += 1
    for vtracks in tracks.values():
        _number_labels(vtracks)
    return tracks


# ------------------------------------------------------------------ sound

def _audio_label(lang, name):
    """Language, then the dub's name: `Russian — Dub [DEEP]`, `STEPonee`,
    `Russian`; a name that already says the language stands alone."""
    if lang and name and lang[1].casefold() not in name.casefold():
        return "%s — %s" % (lang[1], name)
    return name or (lang[1] if lang else None)


def _stream_language(stream):
    """(code, name) of a stream's language tag, or None for none or `und`."""
    tag = (stream.get("language") or "").casefold()
    if tag in ("", "und"):
        return None
    return LANGUAGES.get(tag) or (tag, stream["language"])


def match_audio(top, videos, probes=None, skip=(), loose=False, probe=None):
    """Finds the sound tracks of the videos of one series' top folder: each
    video's own audio streams from media#probe, and external sound files
    matched to videos by the same rules as match_subtitles — releases lay them
    out the same way, `[Audio]/STEPonee/Show - 01.STEPonee.mka`, a folder per
    dub. The arguments are those of match_subtitles; `probe` reads an
    external file's first audio stream, for its language and title.

    A track is labelled like a subtitle track — language, then the dub's
    name: for a stream of the video's own, its title; for an external file,
    the folder it lies in, else its stream's title, else what its name says
    beside the video's. The video's first audio stream is its default track
    and comes first, the other streams after it, then the external files.
    Returns {video path: [track]}, a track being {id, kind, label, language,
    default} plus `stream` (embedded) or `path` (external, absolute)."""
    probes = probes or {}
    probe = probe or media.probe
    tracks = {}
    for v in videos:
        own = []
        for n, s in enumerate((probes.get(v["path"]) or {}).get("audio") or []):
            lang = _stream_language(s)
            own.append({"id": "e%d" % s["stream"], "kind": "embedded", "stream": s["stream"],
                        "label": _audio_label(lang, s.get("title")) or "Track %d" % (n + 1),
                        "language": lang[0] if lang else None, "default": n == 0})
        tracks[v["path"]] = own
    counter = 0
    for path, target, rest, inner, translator in _match_external(
            top, videos, AUDIO_EXTENSIONS, AUDIO_WORDS, skip, loose):
        streams = (probe(path) or {}).get("audio") or []
        first = streams[0] if streams else {}
        lang = _language_of(path, rest, inner) or _stream_language(first)
        name = translator or first.get("title")
        label = _audio_label(lang, name)
        if label is None:
            words = _tokens(rest if rest is not None else normalise(os.path.basename(path)))
            label = " ".join(words) or "Audio"
        tracks[target["path"]].append({"id": "x%d" % counter, "kind": "external", "path": path,
                                       "label": label, "language": lang[0] if lang else None,
                                       "default": False})
        counter += 1
    for vtracks in tracks.values():
        _number_labels(vtracks)
    return tracks


# ------------------------------------------------------------------ scan

def delivery(info, path):
    """(delivery, reason) — how a video reaches the browser with its default
    sound track: `direct` when its container and codecs are what current
    Chrome and Firefox both play as it is — a browser plays a file's first
    audio stream and cannot switch to another, so another track chosen is
    always streamed (server#serve_stream); `stream`, converted as it plays, for anything else ffprobe can read
    (decisions#stream); `unplayable`, with the reason, for a file ffprobe
    cannot read — or one without a picture or a length to stream."""
    if info.get("error") is not None:
        return "unplayable", "the file could not be read: %s" % (
            info["error"].strip().splitlines() or ["ffprobe failed"])[0]
    video = info.get("video_codec")
    if video is None:
        return "unplayable", "the file has no video stream"
    if not info.get("duration"):
        return "unplayable", "the length of the video cannot be read"
    container = DIRECT_CONTAINERS.get(os.path.splitext(path)[1].lower())
    video_ok = video in DIRECT_VIDEO_CODECS or (video == "h264" and info.get("pix_fmt") in media.DIRECT_PIX_FMTS)
    audio_ok = info.get("audio_codec") is None or info["audio_codec"] in DIRECT_AUDIO_CODECS
    if container in (info.get("container") or "").split(",") and video_ok and audio_ok:
        return "direct", None
    return "stream", None


def path_key(vid):
    """A path within the library as it is compared: after os.path.normcase,
    so two spellings of one path on a system that ignores case are one."""
    return os.path.normcase(vid.replace("/", os.sep))


def folder_names(folders):
    """name -> folder for the library folders, in their order: each is named
    after its last part, and a name already taken — two folders both called
    `Videos` — gets a number, `Videos (2)`, so two folders cannot give two
    videos one path."""
    names = {}
    taken = set()
    for folder in folders:
        base = os.path.basename(os.path.normpath(folder))
        base = re.sub(r"[\\/:]", "", base) or re.sub(r"[\\/:]", "", folder) or "Library"
        name, n = base, 2
        while name.casefold() in taken:
            name, n = "%s (%d)" % (base, n), n + 1
        taken.add(name.casefold())
        names[name] = folder
    return names


def excluded_test(excluded):
    """Whether a path within the library is removed from it: one of the
    `excluded` paths, or inside one of them."""
    keys = [path_key(e).rstrip(os.sep) for e in excluded if e]
    def test(vid):
        key = path_key(vid)
        return any(key == k or key.startswith(k + os.sep) for k in keys)
    return test


def file_title(tag, name):
    """The title stored in a file, when it says more than the file's name —
    a tag that only repeats the name, or a part of it, says nothing more —
    else None."""
    tag = " ".join((tag or "").split())
    key = text_key(tag)
    if not key or key in text_key(os.path.splitext(name)[0]):
        return None
    return tag


def _listdir(folder, skip=None):
    """The video files and the subfolders of one folder, leaving out what
    `skip` (an absolute path -> bool) says to."""
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return [], []
    files = [e.name for e in entries if not e.name.startswith(".") and e.is_file()
             and os.path.splitext(e.name)[1].lower() in VIDEO_EXTENSIONS
             and not (skip and skip(e.path))]
    dirs = [e.name for e in entries if not e.name.startswith(".") and e.is_dir()
            and e.name.casefold() not in SUBTITLE_FOLDERS and not (skip and skip(e.path))]
    return files, sorted(dirs, key=natural_key)


def scan(folders, probe=None, excluded=()):
    """Walks every library folder — `folders`, a list of folders or one —
    leaving out the `excluded` paths (store#fields#excluded) and what lies
    inside them. A video's path within the library begins with the name its
    library folder has in folder_names, so two folders cannot give two videos
    one path. Every folder holding videos is a playlist, ordered by
    order_episodes; a folder whose subfolders are seasons is one
    series with a season per subfolder. Any other subfolder with videos
    inside a series — whether its episodes lie in seasons or directly in its
    folder — is a playlist of its own titled after both, `Show · Bonus`; a
    subfolder counts as a season only when its name says nothing else. A folder holding
    one video is not a playlist: its video is shown on its own, as is a video
    directly in the library folder. Each video gets its duration and codecs
    from media#probe, and how it reaches the browser from delivery — `direct`,
    `stream` or `unplayable` (decisions#stream); its subtitles are found by
    match_subtitles and its sound tracks by match_audio, once per series' top
    folder. `delivery` is for the default sound track: with another chosen,
    a `direct` video is played as `stream` (decisions#audio-in-pieces).

    A video's title is the title stored in the file when it says more than
    the file's name (file_title), else the name cleaned of release tags.

    Returns {"playlists": [...], "videos": {path: video}, "standalone": [paths],
    "short": {path: id}, "paths": {id: path}}, paths being within the library
    with `/` separators and ids those of short_ids; playlists and standalone
    videos are each ordered by title."""
    if isinstance(folders, str):
        folders = [folders]
    probe = probe or media.probe
    is_excluded = excluded_test(excluded)
    catalogue = {"playlists": [], "videos": {}, "standalone": []}
    for name, root in folder_names([os.path.abspath(f) for f in folders]).items():
        _scan_folder(root, name, probe, is_excluded, catalogue)
    catalogue["playlists"].sort(key=lambda p: (natural_key(p["title"]), natural_key(p["id"])))
    catalogue["standalone"].sort(key=lambda v: (natural_key(catalogue["videos"][v]["title"]), natural_key(v)))
    catalogue["short"] = short_ids([p["id"] for p in catalogue["playlists"]] + list(catalogue["videos"]))
    catalogue["paths"] = {sid: path for path, sid in catalogue["short"].items()}
    return catalogue


# server#fields#host_root: where the computer's file system is, when MiruTube
# runs in service's container; library folders are named as the computer
# names them and opened under it.
HOST_ROOT = os.environ.get("MIRUTUBE_HOST_ROOT") or os.sep


def on_host(path):
    """The path MiruTube opens for `path`, a path as the computer names it."""
    path = os.path.abspath(path)
    if HOST_ROOT == os.sep:
        return path
    return os.path.join(HOST_ROOT, path.lstrip(os.sep))


SHORT_ID_LENGTH = 8
_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"


def _hashed_id(path, length):
    digest = int.from_bytes(hashlib.sha1(path.encode("utf-8")).digest(), "big")
    out = []
    for _ in range(length):
        digest, i = divmod(digest, 32)
        out.append(_ID_ALPHABET[i])
    return "".join(out)


def short_ids(paths):
    """{path: id}: the short id of every video and playlist path — eight
    letters and digits from a hash of the path, so a file has the same id on
    every scan and a link to it lasts until it is renamed; paths whose ids
    would coincide all get longer ones (decisions#short-ids)."""
    ids, length, pending = {}, SHORT_ID_LENGTH, list(dict.fromkeys(paths))
    while pending:
        groups = {}
        for path in pending:
            groups.setdefault(_hashed_id(path, length), []).append(path)
        pending = []
        for sid, group in groups.items():
            if len(group) == 1:
                ids[group[0]] = sid
            else:
                pending.extend(group)
        length += 4
    return ids


def _scan_folder(root, name, probe, is_excluded, catalogue):
    """scan for one library folder `root`, whose paths begin with `name`,
    adding to `catalogue`."""
    if is_excluded(name) or not os.path.isdir(root):
        return

    def _rel(root, path):
        rel = os.path.relpath(path, root)
        return name if rel == os.curdir else name + "/" + rel.replace(os.sep, "/")

    def skip(path):
        return is_excluded(_rel(root, path))

    folders = {}           # folder holding videos -> its items, in order
    probes = {}            # video path -> media#probe result
    season_parents = set() # folders whose subfolders are seasons

    def make_videos(folder, items):
        folders[folder] = items
        ids = []
        for i in items:
            path = os.path.join(folder, i["name"])
            info = probes[path] = probe(path)
            how, reason = delivery(info, path)
            vid = _rel(root, path)
            catalogue["videos"][vid] = {
                "id": vid, "title": file_title(info.get("title"), i["name"]) or i["title"],
                "playlist": None,
                "season": i["season"], "episode": i["episode"],
                "duration": info.get("duration"), "delivery": how,
                "playable": how != "unplayable", "reason": reason,
                "subtitles": [], "audio": [],
            }
            ids.append(vid)
        return ids

    def add_playlist(folder, title, is_series, ids):
        if len(ids) < 2:
            # a video alone in its folder is outside any playlist
            catalogue["standalone"].extend(ids)
            return
        pid = _rel(root, folder)
        for v in ids:
            catalogue["videos"][v]["playlist"] = pid
        catalogue["playlists"].append({
            "id": pid, "title": title, "series": is_series, "videos": ids,
            "duration": sum(catalogue["videos"][v]["duration"] or 0 for v in ids),
        })

    def walk(folder, series_title=None):
        files, dirs = _listdir(folder, skip)
        title = normalise(os.path.basename(folder), is_file=False) or os.path.basename(folder)
        if series_title:
            # the extras of a series are titled after it, so those of two
            # series cannot be mistaken for each other
            title = "%s · %s" % (series_title, title)
        seasons = []
        for d in dirs:
            s = season_of_folder(d)
            if s is not None and _listdir(os.path.join(folder, d), skip)[0]:
                seasons.append((s, d))
        others = [d for d in dirs if d not in {d for _, d in seasons}]
        if seasons:
            season_parents.add(folder)
            ids = []
            if files:
                _, items = order_episodes(files)
                ids += make_videos(folder, items)
            for s, d in sorted(seasons):
                sfolder = os.path.join(folder, d)
                sfiles, sdirs = _listdir(sfolder, skip)
                _, items = order_episodes(sfiles, season=s)
                ids += make_videos(sfolder, items)
                for sd in sdirs:
                    walk(os.path.join(sfolder, sd), series_title=title)
            ids.sort(key=lambda v: (
                catalogue["videos"][v]["episode"] is None,
                catalogue["videos"][v]["season"] or 0,
                catalogue["videos"][v]["episode"] or 0,
                natural_key(v)))
            add_playlist(folder, title, True, ids)
            for d in others:
                walk(os.path.join(folder, d), series_title=title)
            return
        is_series = False
        if files:
            is_series, items = order_episodes(files)
            ids = make_videos(folder, items)
            if folder == root:
                catalogue["standalone"].extend(ids)
                is_series = False
            else:
                add_playlist(folder, title, is_series, ids)
        for d in others:
            # inside a series whose episodes lie directly in its folder, a
            # subfolder of videos is titled after it too
            walk(os.path.join(folder, d), series_title=title if is_series else None)

    walk(root)

    # The series' top folder of each folder of videos: the folder above it,
    # as long as that one holds videos too or has seasons for subfolders.
    tops = {}
    for folder in folders:
        top = folder
        while top != root:
            parent = os.path.dirname(top)
            if parent == root or not (parent in folders or parent in season_parents):
                break
            top = parent
        tops.setdefault(top, []).append(folder)
    for top, members in tops.items():
        videos = [dict(i, path=os.path.join(f, i["name"]), folder=f)
                  for f in members for i in folders[f]]
        skip = set(tops) - {top}
        for field, found in (
                ("subtitles", match_subtitles(top, videos, probes, skip=skip, loose=top == root)),
                ("audio", match_audio(top, videos, probes, skip=skip, loose=top == root, probe=probe))):
            for path, vtracks in found.items():
                tracks = []
                for t in vtracks:
                    t = dict(t)
                    if "path" in t:
                        t["path"] = _rel(root, t["path"])
                    tracks.append(t)
                catalogue["videos"][_rel(root, path)][field] = tracks


class Library:
    """The catalogue of the library folders, rescanned when a request finds it
    older than RESCAN_AFTER seconds, or at once when the folders or the
    removed paths change."""

    def __init__(self, folders, probe=None, rescan_after=RESCAN_AFTER, excluded=()):
        self.probe = probe
        self.rescan_after = rescan_after
        self._lock = threading.Lock()
        self.configure(folders, excluded)

    def configure(self, folders, excluded=()):
        """Takes the library folders, in order, and the removed paths; the
        next catalogue is a new scan."""
        names = folder_names([os.path.abspath(f) for f in folders])
        with self._lock:
            self.folders = names                      # name -> folder, as given
            self.roots = {n: os.path.realpath(on_host(f)) for n, f in names.items()}
            self.excluded = list(excluded)
            self._catalogue = None
            self._scanned_at = 0.0

    def rescan(self):
        """Scans now, rather than when the last scan is found old."""
        with self._lock:
            self._catalogue = None
        return self.catalogue()

    def catalogue(self):
        with self._lock:
            if self._catalogue is None or time.monotonic() - self._scanned_at > self.rescan_after:
                self._catalogue = scan([on_host(f) for f in self.folders.values()], self.probe, self.excluded)
                self._scanned_at = time.monotonic()
            return self._catalogue

    def video(self, vid):
        return self.catalogue()["videos"].get(vid)

    def playlist(self, pid):
        return next((p for p in self.catalogue()["playlists"] if p["id"] == pid), None)

    def path_of(self, sid):
        """The path of the video or playlist a short id names, or None when
        the catalogue has no such id."""
        if not isinstance(sid, str):
            return None
        return self.catalogue()["paths"].get(sid)

    def id_of(self, path):
        return self.catalogue()["short"].get(path)

    def resolve(self, vid):
        """The absolute path a path within the library names, or None when it
        names no library folder or resolves outside its folder — through
        `..`, an absolute path or a symbolic link (behaviour#library-only).
        Paths are compared after os.path.normcase."""
        if not isinstance(vid, str) or "\0" in vid:
            return None
        name, _, rest = vid.lstrip("/").partition("/")
        with self._lock:
            root = self.roots.get(name) or next(
                (r for n, r in self.roots.items() if os.path.normcase(n) == os.path.normcase(name)), None)
        if root is None:
            return None
        full = os.path.realpath(os.path.join(root, rest))
        try:
            if os.path.normcase(os.path.commonpath([full, root])) != os.path.normcase(root):
                return None
        except ValueError:  # another drive
            return None
        return full

    def relative(self, full):
        """The path within the library of an absolute path inside one of the
        library folders, or None."""
        full = os.path.realpath(full)
        with self._lock:
            roots = list(self.roots.items())
        for name, root in roots:
            try:
                if os.path.normcase(os.path.commonpath([full, root])) == os.path.normcase(root):
                    rel = os.path.relpath(full, root)
                    return name if rel == os.curdir else name + "/" + rel.replace(os.sep, "/")
            except ValueError:
                continue
        return None
