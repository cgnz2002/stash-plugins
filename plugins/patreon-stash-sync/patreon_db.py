"""Read-only reader for the Patreon user_data.db produced by
convert_patreon_to_db.py.

Unlike of-stash-sync (which matches OF-Scraper media by filename and syncs
scenes/images), Patreon posts are downloaded as folders that Stash ingests as
**galleries**. So this database is post/collection oriented, not file oriented:

- ``profiles``    - one row per creator (campaign/user id + vanity).
- ``posts``       - one row per Patreon post: title, description, date, url, the
                    post folder ``directory``, and its creator ``model_id``. The
                    plugin matches these to Stash galleries/images by the post
                    folder basename (which embeds the numeric post id).
- ``collections`` - one row per Patreon collection: title, description, date and
                    the ``post_ids`` it groups. The plugin creates a Stash
                    gallery per collection and attaches the member posts' images.

The database is opened read-only so a concurrently running converter never
causes a write or "readonly database" error.
"""

import glob
import os
import sqlite3


class PatreonDatabase:
    def __init__(self, path):
        self.path = path
        uri = "file:{}?mode=ro".format(os.path.abspath(path))
        self.conn = sqlite3.connect(uri, uri=True)
        self.conn.row_factory = sqlite3.Row
        self._tables = {
            row["name"]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    @staticmethod
    def find_databases(data_path):
        search = os.path.join(data_path, "**", "user_data.db")
        return sorted(glob.glob(search, recursive=True))

    def profiles(self):
        """Return ``[{"user_id", "username"}, ...]`` (creator campaign/user id +
        vanity)."""
        rows = self.conn.execute("SELECT user_id, username FROM profiles").fetchall()
        return [{"user_id": r["user_id"], "username": r["username"]} for r in rows]

    def posts(self, model_id=None):
        """Yield post rows, optionally limited to one creator's model_id."""
        if model_id is None:
            cur = self.conn.execute("SELECT * FROM posts")
        else:
            cur = self.conn.execute(
                "SELECT * FROM posts WHERE model_id = ?", (model_id,)
            )
        return cur.fetchall()

    def post_directory(self, post_id):
        """Return the on-disk post folder for a post id, or None. Used to find a
        collection member's images even when they live under another creator."""
        row = self.conn.execute(
            "SELECT directory FROM posts WHERE post_id = ?", (str(post_id),)
        ).fetchone()
        return row["directory"] if row else None

    def collections(self, model_id=None):
        """Yield collection rows with ``post_ids`` split into a list. Returns an
        empty list if the converter produced no collections table."""
        if "collections" not in self._tables:
            return []
        if model_id is None:
            rows = self.conn.execute("SELECT * FROM collections").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM collections WHERE model_id = ?", (model_id,)
            ).fetchall()
        out = []
        for r in rows:
            raw = r["post_ids"] or ""
            post_ids = [p for p in raw.split(",") if p]
            out.append(
                {
                    "collection_id": r["collection_id"],
                    "title": r["title"],
                    "description": r["description"],
                    "posted_at": r["posted_at"],
                    "url": r["url"],
                    "model_id": r["model_id"],
                    "username": r["username"],
                    "post_ids": post_ids,
                }
            )
        return out

    def close(self):
        self.conn.close()
