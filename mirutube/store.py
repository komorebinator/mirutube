"""What the server remembers between runs, in `mirutube.db` in the data
folder (server#fields#data_folder): positions, the subtitle and sound choices, the library folders, the
paths removed from the library and the settings shared by the household. SQLite rather than a JSON file, because the server answers requests in
parallel and two positions written at once must not lose one
(decisions#sqlite). Keyed by a video's path within the library, so it
survives a rescan. There are no accounts: positions are shared by everyone in
the household (decisions#no-accounts).
"""

import json
import os
import sqlite3
import time

# store#fields#shared_settings: each setting with its default and what it may
# be set to (behaviour#subtitle-size, downloads#functions#torrent,
# behaviour#languages). Seeding is off until the user turns it on: it sends the
# household's upload to strangers for as long as MiruTube runs.
SUBTITLE_SIZES = (75, 100, 125, 150, 200)
LANGUAGES = ("auto", "en", "ru")
SHARED_SETTINGS = {
    "subtitle_size": (100, lambda v: not isinstance(v, bool) and v in SUBTITLE_SIZES),
    "seed_torrents": (False, lambda v: isinstance(v, bool)),
    "language": ("auto", lambda v: isinstance(v, str) and v in LANGUAGES),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    path     TEXT PRIMARY KEY,   -- the video, by its path within the library
    position REAL NOT NULL,      -- the second watching stopped at
    watched  INTEGER NOT NULL,   -- whether it was watched to the end
    updated  REAL NOT NULL       -- when, for the row of what was started
);
CREATE TABLE IF NOT EXISTS subtitle_choice (
    playlist TEXT PRIMARY KEY,   -- the playlist, by its path within the library
    label    TEXT,               -- the label of the track last chosen, NULL for none
    language TEXT                -- its language, NULL for none or when unknown
);
CREATE TABLE IF NOT EXISTS audio_choice (
    playlist TEXT PRIMARY KEY,   -- the playlist, by its path within the library
    label    TEXT,               -- the label of the sound track last chosen, NULL for the default
    language TEXT                -- its language, NULL for the default or when unknown
);
CREATE TABLE IF NOT EXISTS library_folders (
    folder   TEXT PRIMARY KEY,   -- an absolute path on this computer
    added    INTEGER NOT NULL    -- the order the folders were added in
);
CREATE TABLE IF NOT EXISTS excluded (
    path     TEXT PRIMARY KEY,   -- a video or a folder, by its path within the library
    removed  REAL NOT NULL       -- when
);
CREATE TABLE IF NOT EXISTS shared_settings (
    name     TEXT PRIMARY KEY,   -- a setting one for the whole household
    value    TEXT NOT NULL       -- as JSON
);
CREATE TABLE IF NOT EXISTS meta (
    key      TEXT PRIMARY KEY,
    value    TEXT
);
"""


class Store:
    def __init__(self, data_dir):
        os.makedirs(data_dir, exist_ok=True)
        self.path = os.path.join(data_dir, "mirutube.db")
        db = self._connect()
        try:
            with db:
                db.executescript(SCHEMA)
        finally:
            db.close()

    def _connect(self):
        # One connection per call: requests are answered on their own threads.
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def _run(self, sql, args=()):
        db = self._connect()
        try:
            with db:
                return db.execute(sql, args).fetchall()
        finally:
            db.close()

    # positions

    def position(self, path):
        rows = self._run("SELECT position, watched, updated FROM positions WHERE path = ?", (path,))
        if not rows:
            return {"position": 0.0, "watched": False, "updated": None}
        r = rows[0]
        return {"position": r["position"], "watched": bool(r["watched"]), "updated": r["updated"]}

    def positions(self):
        rows = self._run("SELECT path, position, watched, updated FROM positions")
        return {r["path"]: {"position": r["position"], "watched": bool(r["watched"]),
                            "updated": r["updated"]} for r in rows}

    def set_position(self, path, position, watched):
        self._run("INSERT INTO positions (path, position, watched, updated) VALUES (?, ?, ?, ?) "
                  "ON CONFLICT(path) DO UPDATE SET position = excluded.position, "
                  "watched = excluded.watched, updated = excluded.updated",
                  (path, float(position), int(bool(watched)), time.time()))

    # subtitle_choice

    def subtitle_choices(self):
        """playlist -> {label, language} of the track last chosen, or None
        when subtitles were switched off."""
        return {r["playlist"]: ({"label": r["label"], "language": r["language"]}
                                if r["label"] is not None else None)
                for r in self._run("SELECT playlist, label, language FROM subtitle_choice")}

    def set_subtitle_choice(self, playlist, label, language):
        """Records the track chosen for a playlist; a `label` of None is none."""
        if label is None:
            language = None
        self._run("INSERT INTO subtitle_choice (playlist, label, language) VALUES (?, ?, ?) "
                  "ON CONFLICT(playlist) DO UPDATE SET label = excluded.label, "
                  "language = excluded.language",
                  (playlist, label, language))

    # audio_choice

    def audio_choices(self):
        """playlist -> {label, language} of the sound track last chosen, or
        None when it was the default."""
        return {r["playlist"]: ({"label": r["label"], "language": r["language"]}
                                if r["label"] is not None else None)
                for r in self._run("SELECT playlist, label, language FROM audio_choice")}

    def set_audio_choice(self, playlist, label, language):
        """Records the sound track chosen for a playlist; a `label` of None is
        the default."""
        if label is None:
            language = None
        self._run("INSERT INTO audio_choice (playlist, label, language) VALUES (?, ?, ?) "
                  "ON CONFLICT(playlist) DO UPDATE SET label = excluded.label, "
                  "language = excluded.language",
                  (playlist, label, language))

    # library_folders

    def library_folders(self):
        """The library folders, in the order added."""
        return [r["folder"] for r in self._run("SELECT folder FROM library_folders ORDER BY added")]

    def first_start(self, folder):
        """Adds the folder given on the command line on the first start —
        the first one given a folder — and never again: from then on the
        folders are the user's to change in settings, and one removed there
        does not come back with the next start. True when it was added."""
        if folder is None:
            return False
        db = self._connect()
        try:
            with db:
                if db.execute("SELECT 1 FROM meta WHERE key = 'folders_set'").fetchone():
                    return False
                db.execute("INSERT INTO meta (key, value) VALUES ('folders_set', '1')")
                db.execute("INSERT OR IGNORE INTO library_folders (folder, added) VALUES "
                           "(?, (SELECT COALESCE(MAX(added), 0) + 1 FROM library_folders))", (folder,))
                return True
        finally:
            db.close()

    def add_library_folder(self, folder):
        self._run("INSERT OR IGNORE INTO library_folders (folder, added) VALUES "
                  "(?, (SELECT COALESCE(MAX(added), 0) + 1 FROM library_folders))", (folder,))

    def remove_library_folder(self, folder):
        self._run("DELETE FROM library_folders WHERE folder = ?", (folder,))

    # excluded

    def excluded(self):
        """The paths removed from the library, the most recent last."""
        return [r["path"] for r in self._run("SELECT path FROM excluded ORDER BY removed")]

    def exclude(self, path):
        self._run("INSERT OR REPLACE INTO excluded (path, removed) VALUES (?, ?)", (path, time.time()))

    def include(self, path):
        """Brings a removed path back; False when it was not removed."""
        db = self._connect()
        try:
            with db:
                return db.execute("DELETE FROM excluded WHERE path = ?", (path,)).rowcount > 0
        finally:
            db.close()

    # forgetting

    def forget_positions(self, paths):
        """Clears the stored positions of these videos, so they count as not
        started."""
        paths = list(paths)
        for i in range(0, len(paths), 500):
            chunk = paths[i:i + 500]
            self._run("DELETE FROM positions WHERE path IN (%s)" % ",".join("?" * len(chunk)), chunk)

    # shared_settings

    def shared_settings(self):
        """Every shared setting, its default where never set."""
        kept = {}
        for r in self._run("SELECT name, value FROM shared_settings"):
            try:
                kept[r["name"]] = json.loads(r["value"])
            except ValueError:
                pass
        return {name: kept[name] if name in kept and valid(kept[name]) else default
                for name, (default, valid) in SHARED_SETTINGS.items()}

    def set_shared_setting(self, name, value):
        """Sets one shared setting; False for an unknown one or a value it
        cannot take."""
        if name not in SHARED_SETTINGS or not SHARED_SETTINGS[name][1](value):
            return False
        self._run("INSERT INTO shared_settings (name, value) VALUES (?, ?) "
                  "ON CONFLICT(name) DO UPDATE SET value = excluded.value", (name, json.dumps(value)))
        return True
