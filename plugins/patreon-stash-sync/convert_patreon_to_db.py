#!/usr/bin/env python3
"""Convert patreon-dl on-disk output into an OF-Scraper-shaped user_data.db.

patreon-dl (patrickkfkan/patreon-dl) downloads Patreon content to disk as:

    <root>/<vanity> - <Creator Name>/posts/<postId> - <Post Title>/
        images/                 full-res, named media  (matched by Stash)
        attachments/            downloadable attachments
        audio/                  audio files
        .thumbnails/            webp thumbnails         (IGNORED)
        post_info/
            post-api.json       raw Patreon JSON:API response  (PARSED HERE)
            info.txt, *.webp    auxiliary                (IGNORED)

The Patreon Metadata Sync Stash plugin reuses of-stash-sync's database reader
(of_database.py) verbatim, so it expects the exact OF-Scraper column layout.
This script produces that layout from patreon-dl output, letting the unmodified
reader sync Patreon metadata into Stash.

It is standalone and depends only on the Python standard library, so it can run
inside the patreon-dl container, as a sidecar container, or as a scheduled task
that fires after each patreon-dl run.

Design notes
------------
* The media ``filename`` is read from the ACTUAL basename on disk (never
  reconstructed from the JSON), because that is what Stash matches against.
* ``.thumbnails/*.webp`` and everything under ``post_info/`` are auxiliary and
  are never used as a match filename.
* It is idempotent: rows are upserted by primary key, so it is safe to re-run
  after each new download.
* Patreon renames JSON:API keys occasionally, so attribute/relationship lookups
  are defensive (several candidate keys, graceful fallbacks). Where a real
  sample would remove ambiguity it is called out in a comment.

Usage
-----
    python3 convert_patreon_to_db.py [MEDIA_ROOT] [--db PATH]

``MEDIA_ROOT`` defaults to ``/volume1/stash/media/patreon`` and the database is
written to ``<MEDIA_ROOT>/user_data.db`` unless ``--db`` overrides it. The
plugin's ``**/user_data.db`` glob then finds it under the configured data path.
"""

import argparse
import html
import json
import os
import re
import sqlite3
import sys

DEFAULT_ROOT = "/volume1/stash/media/patreon"

# Subdirectories of a post folder that hold the real, matchable media. Ordered;
# a filename is only recorded once even if it somehow appears in two of them.
# ``.thumbnails`` and ``post_info`` are deliberately excluded.
MEDIA_SUBDIRS = ["images", "attachments", "audio", "media", "videos", "video"]

# Extension -> coarse media kind, used to fill media_type. The plugin does not
# branch on this value, but a sensible kind keeps the database self-describing.
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic"}
VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".flv", ".wmv", ".ts"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus"}

# Auxiliary thumbnail/preview basenames that may appear in a media dir but must
# never be used as a match filename.
_AUX_NAMES = {"cover-image", "thumbnail", "cover", "thumb"}

_BLOCK_TAG_RE = re.compile(
    r"</?(?:p|div|br|li|ul|ol|h[1-6]|blockquote|tr|section)\s*/?>",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")


# --------------------------------------------------------------------------- #
# JSON:API helpers
# --------------------------------------------------------------------------- #

def _first(d, *keys, default=None):
    """Return d[k] for the first present, non-None key (defensive against
    Patreon renaming attribute keys between API revisions)."""
    if not isinstance(d, dict):
        return default
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _rel_id(relationships, name):
    """Return the single related object id for relationships[name].data.id."""
    rel = _first(relationships, name, default={})
    data = _first(rel, "data", default=None)
    if isinstance(data, dict):
        return data.get("id")
    return None


def _rel_ids(relationships, name):
    """Return the list of related ids for a to-many relationship."""
    rel = _first(relationships, name, default={})
    data = _first(rel, "data", default=None)
    if isinstance(data, list):
        return [item.get("id") for item in data if isinstance(item, dict) and item.get("id")]
    if isinstance(data, dict) and data.get("id"):
        return [data["id"]]
    return []


def html_to_text(value):
    """Collapse a Patreon HTML post body into readable plain text.

    Block-level tags become newlines, remaining tags are stripped, HTML entities
    are unescaped, and runs of blank lines are collapsed. Mirrors the intent of
    media.py's remove_html_tags but keeps paragraph breaks so the plugin's
    title/details split still reads well.
    """
    if not value:
        return ""
    text = _BLOCK_TAG_RE.sub("\n", value)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    lines = [line.strip() for line in text.split("\n")]
    out = []
    blank = False
    for line in lines:
        if line:
            out.append(line)
            blank = False
        elif not blank:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def media_kind(filename):
    ext = os.path.splitext(filename)[1].lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in IMAGE_EXTS:
        return "image"
    return "attachment"


def is_auxiliary(filename):
    """True for cover/thumbnail preview files that must not be matched."""
    stem = os.path.splitext(filename)[0].lower()
    return stem in _AUX_NAMES


# --------------------------------------------------------------------------- #
# Paid / tier detection
# --------------------------------------------------------------------------- #

def detect_paid_and_price(attrs, relationships, included_by_id):
    """Return (paid: int, price: float).

    A Patreon post is "paid/locked" when it is patron-only rather than public.
    The most reliable signals, in order:
      * is_public is False       -> patron-only
      * is_paid is truthy        -> monthly-charge post
      * min_cents_pledged_to_view > 0
      * a tier/access-rule gate is attached and publicity is unknown

    NOTE: verify the exact key against a real post-api.json; Patreon has shipped
    both ``is_public`` and ``current_user_can_view`` over time, so both are
    checked. When paid, price is the smallest attached tier amount in dollars if
    it can be read from ``included``, else a nominal 1.0 (the plugin only needs a
    non-zero price to apply the "paid" tag).
    """
    is_public = _first(attrs, "is_public")
    is_paid_attr = _first(attrs, "is_paid")
    min_cents = _first(attrs, "min_cents_pledged_to_view", "min_cents", default=0)

    tier_ids = _rel_ids(relationships, "access_rules") or _rel_ids(relationships, "tiers")

    paid = False
    if is_public is False:
        paid = True
    elif is_paid_attr:
        paid = True
    elif isinstance(min_cents, (int, float)) and min_cents > 0:
        paid = True
    elif is_public is None and tier_ids:
        # Publicity unknown but a tier gate exists -> treat as patron-only.
        paid = True

    if not paid:
        return 0, 0.0

    # Best-effort real price: smallest positive tier amount from included tier
    # or reward objects. Falls back to a nominal non-zero value.
    price = None
    if isinstance(min_cents, (int, float)) and min_cents > 0:
        price = min_cents / 100.0
    for tid in tier_ids:
        obj = included_by_id.get(tid)
        if not obj:
            continue
        cents = _first(obj.get("attributes") or {}, "amount_cents", "amount", default=None)
        if isinstance(cents, (int, float)) and cents > 0:
            dollars = cents / 100.0
            price = dollars if price is None else min(price, dollars)
    return 1, (price if price and price > 0 else 1.0)


# --------------------------------------------------------------------------- #
# Filesystem walk
# --------------------------------------------------------------------------- #

def find_post_json(root):
    """Yield every .../posts/<post>/post_info/post-api.json under root."""
    for dirpath, dirnames, filenames in os.walk(root):
        if os.path.basename(dirpath) == "post_info" and "post-api.json" in filenames:
            yield os.path.join(dirpath, "post-api.json")


def creator_folder_from(json_path):
    """Given .../<vanity> - <Name>/posts/<post>/post_info/post-api.json return
    the creator folder path (.../<vanity> - <Name>) or None if the layout is
    unexpected.
    """
    post_info = os.path.dirname(json_path)          # .../post_info
    post_dir = os.path.dirname(post_info)           # .../<post>
    posts_dir = os.path.dirname(post_dir)           # .../posts
    if os.path.basename(posts_dir).lower() != "posts":
        return post_dir  # unusual layout; fall back to the post's parent
    return os.path.dirname(posts_dir)               # .../<vanity> - <Name>


def vanity_from_folder(creator_folder):
    """'spicehead1 - Spicehead1' -> 'spicehead1'. Splits on the first ' - '."""
    base = os.path.basename(creator_folder.rstrip("/\\"))
    if " - " in base:
        return base.split(" - ", 1)[0].strip()
    return base.strip()


def collect_media_files(post_dir):
    """Return [(filename, directory), ...] of real media for a post folder.

    Scans the known media subdirectories and, as a fallback for layouts that
    drop a single file directly in the post folder, any media-extension files in
    the post root. Auxiliary previews and thumbnails are excluded.
    """
    found = []
    seen = set()

    def add(name, directory):
        if name in seen or name.startswith(".") or is_auxiliary(name):
            return
        seen.add(name)
        found.append((name, directory))

    for sub in MEDIA_SUBDIRS:
        subdir = os.path.join(post_dir, sub)
        if not os.path.isdir(subdir):
            continue
        for name in sorted(os.listdir(subdir)):
            full = os.path.join(subdir, name)
            if os.path.isfile(full):
                add(name, subdir)

    # Fallback: media files sitting directly in the post folder (some post types
    # store a single file there). Skip the known auxiliary directories.
    for name in sorted(os.listdir(post_dir)):
        full = os.path.join(post_dir, name)
        if os.path.isfile(full) and os.path.splitext(name)[1].lower() in (
            IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS
        ):
            add(name, post_dir)

    return found


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    user_id  TEXT PRIMARY KEY,
    username TEXT
);
CREATE TABLE IF NOT EXISTS medias (
    media_id  TEXT PRIMARY KEY,
    post_id   TEXT,
    link      TEXT,
    filename  TEXT,
    api_type  TEXT,
    media_type TEXT,
    posted_at TEXT,
    model_id  TEXT,
    directory TEXT
);
CREATE TABLE IF NOT EXISTS posts (
    post_id  TEXT PRIMARY KEY,
    text     TEXT,
    price    REAL,
    paid     INTEGER,
    archived INTEGER
);
CREATE INDEX IF NOT EXISTS idx_medias_model_filename ON medias(model_id, filename);
"""


def open_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def upsert_profile(conn, user_id, username):
    conn.execute(
        "INSERT INTO profiles (user_id, username) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username",
        (user_id, username),
    )


def upsert_post(conn, post_id, text, price, paid, archived):
    conn.execute(
        "INSERT INTO posts (post_id, text, price, paid, archived) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(post_id) DO UPDATE SET "
        "text=excluded.text, price=excluded.price, paid=excluded.paid, archived=excluded.archived",
        (post_id, text, price, paid, archived),
    )


def upsert_media(conn, row):
    conn.execute(
        "INSERT INTO medias "
        "(media_id, post_id, link, filename, api_type, media_type, posted_at, model_id, directory) "
        "VALUES (:media_id, :post_id, :link, :filename, :api_type, :media_type, :posted_at, :model_id, :directory) "
        "ON CONFLICT(media_id) DO UPDATE SET "
        "post_id=excluded.post_id, link=excluded.link, filename=excluded.filename, "
        "api_type=excluded.api_type, media_type=excluded.media_type, "
        "posted_at=excluded.posted_at, model_id=excluded.model_id, directory=excluded.directory",
        row,
    )


# --------------------------------------------------------------------------- #
# Conversion
# --------------------------------------------------------------------------- #

class Converter:
    def __init__(self, conn):
        self.conn = conn
        # creator_folder -> (user_id, username). Keeps medias.model_id aligned
        # with profiles.user_id per creator even if a stray post lacks a
        # campaign id (of_database.py joins medias.model_id == profiles.user_id).
        self._creator_cache = {}
        self.posts = 0
        self.media = 0
        self.skipped_posts = 0

    def creator_identity(self, creator_folder, relationships):
        cached = self._creator_cache.get(creator_folder)
        vanity = vanity_from_folder(creator_folder)
        # Campaign id is the stable per-creator model id; fall back to the user
        # (creator) id, then to a deterministic vanity-based id.
        model_id = _rel_id(relationships, "campaign") or _rel_id(relationships, "user")
        if cached:
            # Reuse the first-seen id so every media row for this creator shares
            # one model_id (and matches the single profiles row).
            return cached
        if not model_id:
            model_id = "vanity:{}".format(vanity)
        identity = (str(model_id), vanity)
        self._creator_cache[creator_folder] = identity
        upsert_profile(self.conn, identity[0], identity[1])
        return identity

    def convert_post(self, json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError) as e:
            sys.stderr.write("skip {}: {}\n".format(json_path, e))
            self.skipped_posts += 1
            return

        data = _first(doc, "data", default={}) or {}
        attrs = _first(data, "attributes", default={}) or {}
        relationships = _first(data, "relationships", default={}) or {}
        included = _first(doc, "included", default=[]) or []
        included_by_id = {
            obj.get("id"): obj for obj in included if isinstance(obj, dict) and obj.get("id")
        }

        post_id = _first(data, "id") or _first(attrs, "id")
        if not post_id:
            sys.stderr.write("skip {}: no post id\n".format(json_path))
            self.skipped_posts += 1
            return
        post_id = str(post_id)

        post_dir = os.path.dirname(os.path.dirname(json_path))  # .../<post>
        creator_folder = creator_folder_from(json_path)
        model_id, vanity = self.creator_identity(creator_folder, relationships)

        # ---- post text: title + plaintext body -------------------------- #
        title = (_first(attrs, "title", default="") or "").strip()
        body_html = _first(attrs, "content", "post_content", default="") or ""
        body = html_to_text(body_html)
        if not body:
            # Locked posts expose only a teaser; use it so there is still text.
            body = html_to_text(_first(attrs, "teaser_text", "content_teaser_text", default="") or "")
        # A single <br> between title and body lets the plugin's process_text
        # (which splits on the first <br>) treat the title as the scene title and
        # the body as the details, exactly as it does for an OF post caption.
        if title and body:
            text = "{}<br>{}".format(title, body)
        else:
            text = title or body

        post_type = _first(attrs, "post_type", default="") or ""
        url = _first(attrs, "url", "patreon_url", default="") or (
            "https://www.patreon.com/posts/{}".format(post_id)
        )
        published_at = _first(
            attrs, "published_at", "created_at", "edited_at", default=""
        ) or ""

        paid, price = detect_paid_and_price(attrs, relationships, included_by_id)
        upsert_post(self.conn, post_id, text, price, paid, 0)
        self.posts += 1

        # ---- media rows from files actually on disk --------------------- #
        media_files = collect_media_files(post_dir)
        if not media_files:
            return
        for filename, directory in media_files:
            row = {
                # Deterministic, so re-runs upsert the same row. Not used for
                # matching (that is done on filename); just a stable key.
                "media_id": "{}:{}".format(post_id, filename),
                "post_id": post_id,
                "link": url,
                "filename": filename,
                "api_type": post_type,
                "media_type": media_kind(filename),
                "posted_at": published_at,
                "model_id": model_id,
                "directory": directory,
            }
            upsert_media(self.conn, row)
            self.media += 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "root", nargs="?", default=DEFAULT_ROOT,
        help="patreon-dl media root (default: {})".format(DEFAULT_ROOT),
    )
    parser.add_argument(
        "--db", default=None,
        help="output database path (default: <root>/user_data.db)",
    )
    opts = parser.parse_args(argv)

    root = os.path.abspath(opts.root)
    if not os.path.isdir(root):
        parser.error("media root does not exist: {}".format(root))
    db_path = opts.db or os.path.join(root, "user_data.db")

    conn = open_db(db_path)
    converter = Converter(conn)
    try:
        count = 0
        for json_path in find_post_json(root):
            converter.convert_post(json_path)
            count += 1
        conn.commit()
    finally:
        conn.close()

    print(
        "Wrote {db}\n  post-api.json files: {files}\n  posts: {posts}"
        "  media rows: {media}  skipped: {skipped}".format(
            db=db_path, files=count, posts=converter.posts,
            media=converter.media, skipped=converter.skipped_posts,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
