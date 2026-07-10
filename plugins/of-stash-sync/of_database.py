"""Reader for OF-Scraper's user_data.db sqlite databases.

Schema verified against OF-Scraper 3.14.7. Older OF-Scraper databases differ in
two ways that this reader detects and adapts to at open time (see
``_detect_schema``):

- ``medias`` has no ``model_id`` column and stores the post date in
  ``created_at`` instead of ``posted_at``.
- the ``profiles`` table is left empty, so the creator name has to be recovered
  from the ``medias.directory`` path instead.

Databases are opened read-only so a running OF-Scraper or a locked file never
causes a write or "readonly database" error.
"""

import glob
import os
import sqlite3


class OFDatabase:
    # Post text can live in any of these tables, all sharing the same columns.
    # 'others' and 'products' are present in OF-Scraper 3.14.7 and were missed
    # by the original sync tool.
    TEXT_TABLES = ["posts", "stories", "messages", "others", "products"]

    # OF-Scraper lays media out as <base>/<username>/[Archived/]<category>/...
    # When the profiles table is empty (older databases) we recover the creator
    # name from the directory segment immediately preceding one of these folders.
    CATEGORY_DIRS = {
        "Posts", "Messages", "Stories", "Archived", "Profile", "Products",
        "Highlights", "Streams", "Stream", "Pinned",
    }

    def __init__(self, path):
        self.path = path
        uri = "file:{}?mode=ro".format(os.path.abspath(path))
        self.conn = sqlite3.connect(uri, uri=True)
        self.conn.row_factory = sqlite3.Row
        self._detect_schema()

    def _detect_schema(self):
        """Newer OF-Scraper databases have medias.model_id and medias.posted_at;
        older ones have neither (the post date lives in created_at). Decide which
        columns to query so the same code works against both layouts.
        """
        cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(medias)")}
        self._has_model_id = "model_id" in cols
        self._date_col = "posted_at" if "posted_at" in cols else "created_at"

    @staticmethod
    def find_databases(data_path):
        search = os.path.join(data_path, "**", "user_data.db")
        return sorted(glob.glob(search, recursive=True))

    @classmethod
    def _username_from_directory(cls, directory):
        """Recover a creator name from a media directory, e.g.
        '/data/jaxthirio/Posts/Free/Images' -> 'jaxthirio'. Returns None if no
        known category folder is found.
        """
        if not directory:
            return None
        parts = [p for p in directory.replace("\\", "/").split("/") if p]
        for i, part in enumerate(parts):
            if part in cls.CATEGORY_DIRS and i > 0:
                return parts[i - 1]
        return None

    def profiles(self):
        """Return ``[{"user_id", "username"}, ...]``.

        Prefer the profiles table. Older databases leave it empty, so fall back
        to the distinct creator names recovered from medias.directory (user_id is
        unknown then, but it is only used for logging and for the model_id filter
        that those databases don't have).
        """
        rows = self.conn.execute("SELECT user_id, username FROM profiles").fetchall()
        if rows:
            return [{"user_id": r["user_id"], "username": r["username"]} for r in rows]

        usernames = []
        seen = set()
        for r in self.conn.execute("SELECT DISTINCT directory FROM medias"):
            name = self._username_from_directory(r["directory"])
            if name and name not in seen:
                seen.add(name)
                usernames.append(name)
        return [{"user_id": None, "username": name} for name in usernames]

    def media_by_filename(self, user_id, filename):
        date = "{} AS posted_at".format(self._date_col)
        if self._has_model_id:
            return self.conn.execute(
                "SELECT media_id, post_id, link, filename, api_type, media_type, {} "
                "FROM medias WHERE model_id = ? AND filename = ?".format(date),
                (user_id, filename),
            ).fetchone()
        # Older databases have no model_id column. They are single-creator
        # databases, so matching on filename alone is unambiguous.
        return self.conn.execute(
            "SELECT media_id, post_id, link, filename, api_type, media_type, {} "
            "FROM medias WHERE filename = ?".format(date),
            (filename,),
        ).fetchone()

    def medias_for_model(self, user_id):
        """Return every media row for a creator (post_id, filename, media_type,
        api_type, link, posted_at), for grouping a post's media into a gallery.

        Older single-creator databases have no model_id column, so they return
        all rows (which is correct there -- one creator per database).
        """
        date = "{} AS posted_at".format(self._date_col)
        cols = "post_id, filename, media_type, api_type, link, {}".format(date)
        if self._has_model_id:
            return self.conn.execute(
                "SELECT {} FROM medias WHERE model_id = ?".format(cols), (user_id,)
            ).fetchall()
        return self.conn.execute("SELECT {} FROM medias".format(cols)).fetchall()

    def post_meta(self, post_id):
        """Return the first matching post/story/message/other/product row, if any."""
        for table in self.TEXT_TABLES:
            try:
                row = self.conn.execute(
                    "SELECT text, price, paid, archived FROM {} WHERE post_id = ?".format(
                        table
                    ),
                    (post_id,),
                ).fetchone()
            except sqlite3.Error:
                # Table may not exist in older databases; skip it.
                continue
            if row:
                return row
        return None

    def close(self):
        self.conn.close()
