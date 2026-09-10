"""Read-only reader for the scraper databases this plugin syncs from.

One reader serves both supported sites, because their schemas are the same
shape: OF-Scraper's `profiles` / `medias` / `posts` layout, which jff-scraper
deliberately mirrors (numeric `model_id`, `posted_at`). jff-scraper then adds
two tables of its own, and every accessor for them degrades gracefully so an
OF-Scraper database reads through the identical code path:

- ``jff_posts`` -- one row per post carrying the real ``post_url``
  (``justfor.fans/<creator>?Post=...``), the site ``tags`` (a JSON array), the
  ``tier`` (Free/Paid), ``access_control``, ``store_url`` and ``pinned``.
  Absent on an OF-Scraper database, where ``post_url()`` returns None,
  ``hashtags()`` returns [] and ``tier()``/``is_pinned()`` return None/False.
- ``schema_flags`` -- a ``source`` flag (e.g. ``jff``). ``source()`` returns None
  for an OF-Scraper database, which is what selects the OnlyFans profile.

It also still detects the *older* OF-Scraper layout at open time
(``_detect_schema``): no ``medias.model_id``, the post date in ``created_at``
rather than ``posted_at``, and an empty ``profiles`` table (the creator name is
then recovered from ``medias.directory``).

Databases are opened read-only so a concurrently running scraper, or a locked
file, never causes a write or "readonly database" error.
"""

import glob
import json
import os
import sqlite3


class SourceDatabase:
    # Post text can live in any of these tables, all sharing the same columns.
    TEXT_TABLES = ["posts", "stories", "messages", "others", "products"]

    # Media is laid out <base>/<username>/[Archived/]<category>/... -- used only
    # for the older/empty-profiles fallback (jff-scraper always fills profiles).
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
        cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(medias)")}
        self._has_model_id = "model_id" in cols
        self._date_col = "posted_at" if "posted_at" in cols else "created_at"
        tables = {r["name"] for r in
                  self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self._has_jff_posts = "jff_posts" in tables
        self._has_flags = "schema_flags" in tables
        self._jff_index = None  # post_id -> jff_posts row, bulk-loaded on demand

    @staticmethod
    def find_databases(data_path):
        search = os.path.join(data_path, "**", "user_data.db")
        return sorted(glob.glob(search, recursive=True))

    def source(self):
        """Value of the ``source`` schema flag (e.g. 'jff'), or None. Used to skip
        databases that are not jff-scraper output."""
        if not self._has_flags:
            return None
        try:
            row = self.conn.execute(
                "SELECT flag_value FROM schema_flags WHERE flag_name = 'source'"
            ).fetchone()
            return row["flag_value"] if row else None
        except sqlite3.Error:
            return None

    @classmethod
    def _username_from_directory(cls, directory):
        if not directory:
            return None
        parts = [p for p in directory.replace("\\", "/").split("/") if p]
        for i, part in enumerate(parts):
            if part in cls.CATEGORY_DIRS and i > 0:
                return parts[i - 1]
        return None

    def profiles(self):
        """Return ``[{"user_id", "username"}, ...]`` from the profiles table
        (falling back to distinct creator names in medias.directory if empty)."""
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
        return self.conn.execute(
            "SELECT media_id, post_id, link, filename, api_type, media_type, {} "
            "FROM medias WHERE filename = ?".format(date),
            (filename,),
        ).fetchone()

    def medias_for_model(self, user_id):
        """Every media row for a creator (post_id, filename, media_type, api_type,
        link, posted_at), for grouping a post's media into a gallery."""
        date = "{} AS posted_at".format(self._date_col)
        cols = "post_id, filename, media_type, api_type, link, {}".format(date)
        if self._has_model_id:
            return self.conn.execute(
                "SELECT {} FROM medias WHERE model_id = ?".format(cols), (user_id,)
            ).fetchall()
        return self.conn.execute("SELECT {} FROM medias".format(cols)).fetchall()

    def post_meta(self, post_id):
        """First matching post/story/message/other/product row (text/price/paid/
        archived), if any."""
        for table in self.TEXT_TABLES:
            try:
                row = self.conn.execute(
                    "SELECT text, price, paid, archived FROM {} WHERE post_id = ?".format(
                        table
                    ),
                    (post_id,),
                ).fetchone()
            except sqlite3.Error:
                continue
            if row:
                return row
        return None

    # ----- JustFor.Fans-native metadata (jff_posts) ----------------------

    def _ensure_jff_index(self):
        """Load every jff_posts row once, keyed by post id.

        Each post needs several of these fields (url, hashtags, tier, pinned);
        querying per field would be four round-trips per post and thousands per
        creator. The table is one row per post, so a single bulk read keeps every
        later lookup a dict hit -- the same bulk-cache approach the resolvers use.
        """
        if self._jff_index is not None:
            return
        self._jff_index = {}
        if not self._has_jff_posts:
            return
        try:
            rows = self.conn.execute(
                "SELECT post_id, post_url, tags, tier, access_control, store_url, "
                "pinned FROM jff_posts"
            ).fetchall()
        except sqlite3.Error:
            return
        for row in rows:
            self._jff_index[row["post_id"]] = row

    def _jff_row(self, post_id):
        if post_id is None:
            return None
        # jff_posts.post_id is INTEGER, but callers hand us either the integer
        # from `medias` or the string used as a gallery-group key. Normalise so
        # the lookup never depends on SQLite's type-affinity coercion.
        key = post_id
        if isinstance(key, str):
            key = key.strip()
            if not key.isdigit():
                return None
            key = int(key)
        self._ensure_jff_index()
        return self._jff_index.get(key)

    def post_url(self, post_id):
        """The real JustFor.Fans post URL, or None. Unlike OnlyFans, the URL is
        not reconstructable from the post id alone (it carries an encoded key), so
        it is read straight from what the scraper captured."""
        row = self._jff_row(post_id)
        return row["post_url"] if (row and row["post_url"]) else None

    def hashtags(self, post_id):
        """The post's JFF hashtags as a list (parsed from the JSON `tags` column)."""
        row = self._jff_row(post_id)
        if not row or not row["tags"]:
            return []
        try:
            data = json.loads(row["tags"])
        except (ValueError, TypeError):
            return []
        if not isinstance(data, list):
            return []
        return [str(t).strip() for t in data if str(t).strip()]

    def tier(self, post_id):
        """'Free' or 'Paid' -- the scraper's verdict on the post's access level,
        derived from JFF's access_control.

        This is the authoritative paid signal on JustFor.Fans. OF-Scraper carries
        a per-post `price`, so of-stash-sync tags 'paid' on `paid AND price > 0`;
        JFF exposes no price, so those columns stay 0 and that rule would never
        fire. Tier is what to key on instead.
        """
        row = self._jff_row(post_id)
        value = (row["tier"] if row else None) or ""
        return str(value).strip() or None

    def is_pinned(self, post_id):
        """True when the creator pinned the post to their profile."""
        row = self._jff_row(post_id)
        return bool(row["pinned"]) if row else False

    def close(self):
        self.conn.close()
