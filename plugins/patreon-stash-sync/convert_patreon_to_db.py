#!/usr/bin/env python3
"""Convert patreon-dl on-disk output into a Patreon user_data.db for the
Patreon Metadata Sync Stash plugin.

patreon-dl (patrickkfkan/patreon-dl) downloads Patreon content to disk as:

    <root>/<vanity> - <Creator Name>/
        posts/<postId> - <Post Title>/
            images/                 full-res media       (Stash ingests as a gallery)
            attachments/  audio/    other media
            .thumbnails/            webp thumbnails      (IGNORED)
            post_info/
                post-api.json       raw Patreon JSON:API (PARSED HERE)
        collections/<collectionId> - <Title>/
            <something>.json        raw collection JSON  (PARSED HERE)

Because each post folder becomes a Stash **gallery**, this converter is post- and
collection-oriented (not file-by-file). It emits:

- ``profiles``    creator campaign/user id + vanity.
- ``posts``       one row per post: title, description, date, url, folder path.
- ``collections`` one row per collection: title, description, date, member ids.

The plugin then matches posts to galleries/images by the post folder basename and
creates one gallery per collection, attaching the member posts' images.

Standard library only, so it runs inside the patreon-dl container, a sidecar
container, or a scheduled task. Idempotent: every row is upserted by primary key.

Usage
-----
    python3 convert_patreon_to_db.py [MEDIA_ROOT] [--db PATH]

``MEDIA_ROOT`` defaults to ``/volume1/stash/media/patreon``; the database is
written to ``<MEDIA_ROOT>/user_data.db`` unless ``--db`` overrides it.
"""

import argparse
import html
import json
import os
import re
import sqlite3
import sys

DEFAULT_ROOT = "/volume1/stash/media/patreon"

_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_NL_RE = re.compile(r"\n{3,}")


# --------------------------------------------------------------------------- #
# JSON:API + ProseMirror helpers
# --------------------------------------------------------------------------- #

def _first(d, *keys, default=None):
    """Return d[k] for the first present, non-None key. Patreon renames keys
    between API revisions, so attribute lookups list several candidates."""
    if not isinstance(d, dict):
        return default
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _rel_id(relationships, name):
    rel = _first(relationships, name, default={})
    data = _first(rel, "data", default=None)
    if isinstance(data, dict):
        return data.get("id")
    return None


def _rel_ids(relationships, name):
    rel = _first(relationships, name, default={})
    data = _first(rel, "data", default=None)
    if isinstance(data, list):
        return [i.get("id") for i in data if isinstance(i, dict) and i.get("id")]
    if isinstance(data, dict) and data.get("id"):
        return [data["id"]]
    return []


# ProseMirror/TipTap block node types that should end with a line break.
_BLOCK_NODES = {
    "paragraph", "heading", "blockquote", "list_item", "listItem",
    "code_block", "codeBlock", "horizontal_rule", "horizontalRule",
}


def _prosemirror_text(node):
    """Recursively extract plain text from a ProseMirror doc node.

    Patreon stores the post body in ``content_json_string`` (and the teaser in
    ``teaser_text_json_string``) as a ProseMirror document, e.g.
    ``{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text",
    "text":"..."}]}]}``. Text nodes carry ``text``; hard breaks and block nodes
    become newlines; mentions become ``@label``.
    """
    if isinstance(node, list):
        return "".join(_prosemirror_text(n) for n in node)
    if not isinstance(node, dict):
        return ""
    ntype = node.get("type")
    if ntype == "text":
        return node.get("text", "") or ""
    if ntype in ("hard_break", "hardBreak"):
        return "\n"
    if ntype == "mention":
        attrs = node.get("attrs") or {}
        label = attrs.get("label") or attrs.get("name") or attrs.get("id") or ""
        return "@{}".format(label) if label else ""
    inner = _prosemirror_text(node.get("content", []))
    if ntype in _BLOCK_NODES:
        return inner + "\n"
    return inner


def _clean(text):
    if not text:
        return ""
    text = _MULTI_NL_RE.sub("\n\n", text)
    return text.strip()


def html_to_text(value):
    if not value:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    text = re.sub(r"</(?:p|div|li|ul|ol|h[1-6]|blockquote)>", "\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub("", text)
    return _clean(html.unescape(text))


def extract_text(attrs, json_key, html_key):
    """Prefer the ProseMirror JSON body, fall back to any legacy HTML body."""
    raw_json = _first(attrs, json_key)
    if raw_json:
        try:
            doc = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
            text = _clean(_prosemirror_text(doc))
            if text:
                return text
        except (ValueError, TypeError):
            pass
    return html_to_text(_first(attrs, html_key, default=""))


# --------------------------------------------------------------------------- #
# Creator identity + paid detection
# --------------------------------------------------------------------------- #

def vanity_from_included(included_by_id, relationships, attrs):
    """Best creator vanity: the included ``user`` object's ``vanity``, then the
    campaign/user name, then the patreon_url slug."""
    user_id = _rel_id(relationships, "user")
    user = included_by_id.get(user_id) if user_id else None
    if user:
        vanity = _first(user.get("attributes") or {}, "vanity")
        if vanity:
            return vanity
    camp_id = _rel_id(relationships, "campaign")
    camp = included_by_id.get(camp_id) if camp_id else None
    if camp:
        # A campaign has no vanity, but its url ends with the vanity.
        url = _first(camp.get("attributes") or {}, "url", default="")
        slug = url.rstrip("/").rsplit("/", 1)[-1] if url else ""
        if slug:
            return slug
    # Fall back to the post's own patreon_url: /<vanity>/posts/...
    patreon_url = _first(attrs, "patreon_url", "url", default="")
    m = re.search(r"patreon\.com/([^/]+)/posts", patreon_url) or re.match(
        r"/([^/]+)/posts", patreon_url or ""
    )
    return m.group(1) if m else ""


def detect_paid_and_price(attrs, relationships, included_by_id):
    """Return (paid: int, price: float). Paid is de-emphasised for Patreon; it is
    still recorded so the plugin can optionally tag patron-only posts.

    A post is locked when its access rule is not ``public`` (the real signal in
    the JSON; ``is_paid``/``min_cents`` are usually null for patron-only posts).
    Price is the smallest positive tier amount if one is attached, else a nominal
    1.0 when locked so the optional 'paid' tag can fire.
    """
    locked = False
    for rid in _rel_ids(relationships, "access_rules"):
        rule = included_by_id.get(rid) or {}
        rtype = _first(rule.get("attributes") or {}, "access_rule_type", default="")
        if rtype and rtype != "public":
            locked = True
    if not locked:
        if _first(attrs, "is_paid"):
            locked = True
        elif _first(attrs, "is_public") is False:
            locked = True
        else:
            mc = _first(attrs, "min_cents_pledged_to_view", default=0)
            if isinstance(mc, (int, float)) and mc > 0:
                locked = True
    if not locked:
        return 0, 0.0

    price = None
    for rid in _rel_ids(relationships, "access_rules"):
        rule = included_by_id.get(rid) or {}
        cents = _first(rule.get("attributes") or {}, "amount_cents", default=None)
        if isinstance(cents, (int, float)) and cents > 0:
            price = cents / 100.0 if price is None else min(price, cents / 100.0)
    return 1, (price if price and price > 0 else 1.0)


# --------------------------------------------------------------------------- #
# Filesystem walk
# --------------------------------------------------------------------------- #

def find_post_info_dirs(root):
    """Yield every ``post_info`` directory that has an info.txt or post-api.json."""
    for dirpath, _dirs, files in os.walk(root):
        if os.path.basename(dirpath) != "post_info":
            continue
        if "info.txt" in files or "post-api.json" in files:
            yield dirpath


# Keys patreon-dl writes in post_info/info.txt (a flat "Key: value" summary).
_INFO_KEYS = ["ID", "Type", "Title", "Teaser", "Content", "Published", "Last Edited", "URL"]
_INFO_KEY_RE = re.compile(r"^(" + "|".join(re.escape(k) for k in _INFO_KEYS) + r"):[ ]?(.*)$")


def parse_info_txt(path):
    """Parse patreon-dl's post_info/info.txt into a dict keyed by its labels.

    info.txt is a simpler, already-rendered summary than post-api.json: it has
    the post ``Content`` as HTML even when the API's ``content`` field is null
    (the real body then only lives in ``content_json_string``). A value may span
    several lines, so lines that don't start a known key are appended to the
    current field.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().split("\n")
    except OSError:
        return {}
    fields = {}
    current = None
    for line in lines:
        m = _INFO_KEY_RE.match(line)
        if m:
            current = m.group(1)
            fields[current] = m.group(2)
        elif current is not None:
            fields[current] += "\n" + line
    return {k: v.strip() for k, v in fields.items()}


def vanity_from_url(url):
    """'https://www.patreon.com/spicehead1/posts/...' -> 'spicehead1'."""
    if not url:
        return ""
    m = re.search(r"patreon\.com/([^/]+)/posts", url) or re.match(r"/([^/]+)/posts", url)
    return m.group(1) if m else ""


def find_collection_json(root):
    """Yield every .json under a ``collections`` directory. The exact filename
    patreon-dl uses is not fixed, so any collection-typed JSON is accepted."""
    for dirpath, _dirs, files in os.walk(root):
        parts = dirpath.replace("\\", "/").lower().split("/")
        if "collections" not in parts:
            continue
        for name in files:
            if name.lower().endswith(".json"):
                yield os.path.join(dirpath, name)


def creator_folder_of(path, marker):
    """Return the ``<vanity> - <Name>`` folder above a ``posts``/``collections``
    marker directory in ``path``, or None."""
    parts = path.replace("\\", "/").split("/")
    for i in range(len(parts) - 1, 0, -1):
        if parts[i].lower() == marker:
            return "/".join(parts[:i])
    return None


def vanity_from_folder(creator_folder):
    if not creator_folder:
        return ""
    base = os.path.basename(creator_folder.rstrip("/\\"))
    return base.split(" - ", 1)[0].strip() if " - " in base else base.strip()


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    user_id  TEXT PRIMARY KEY,
    username TEXT
);
CREATE TABLE IF NOT EXISTS posts (
    post_id   TEXT PRIMARY KEY,
    title     TEXT,
    content   TEXT,
    posted_at TEXT,
    url       TEXT,
    paid      INTEGER,
    price     REAL,
    archived  INTEGER,
    model_id  TEXT,
    directory TEXT,
    api_type  TEXT
);
CREATE TABLE IF NOT EXISTS collections (
    collection_id TEXT PRIMARY KEY,
    title         TEXT,
    description   TEXT,
    posted_at     TEXT,
    url           TEXT,
    model_id      TEXT,
    username      TEXT,
    post_ids      TEXT
);
CREATE INDEX IF NOT EXISTS idx_posts_model ON posts(model_id);
"""


def open_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


class Converter:
    def __init__(self, conn):
        self.conn = conn
        # creator folder -> (model_id, vanity), so posts and collections under
        # one creator agree on model_id even if a collection JSON lacks a
        # campaign id.
        self.creators = {}
        self.posts = 0
        self.collections = 0
        self.skipped = 0

    def _remember_creator(self, folder, model_id, vanity):
        cached = self.creators.get(folder)
        if cached:
            return cached
        if not model_id:
            model_id = "vanity:{}".format(vanity or os.path.basename(folder or ""))
        identity = (str(model_id), vanity)
        self.creators[folder] = identity
        self.conn.execute(
            "INSERT INTO profiles (user_id, username) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username",
            identity,
        )
        return identity

    # ---- posts --------------------------------------------------------- #

    def convert_post(self, post_info_dir):
        post_dir = os.path.dirname(post_info_dir)  # .../<post folder>
        info = parse_info_txt(os.path.join(post_info_dir, "info.txt"))

        # post-api.json is read only for what info.txt lacks: the real campaign id
        # (model_id) and the authoritative creator vanity, plus a body fallback.
        attrs, rels, by_id, api_id = {}, {}, {}, None
        api_path = os.path.join(post_info_dir, "post-api.json")
        if os.path.isfile(api_path):
            try:
                with open(api_path, "r", encoding="utf-8") as f:
                    doc = json.load(f)
                data = _first(doc, "data", default={}) or {}
                attrs = _first(data, "attributes", default={}) or {}
                rels = _first(data, "relationships", default={}) or {}
                included = _first(doc, "included", default=[]) or []
                by_id = {o.get("id"): o for o in included if isinstance(o, dict) and o.get("id")}
                api_id = _first(data, "id")
            except (OSError, ValueError):
                pass

        post_id = info.get("ID") or (str(api_id) if api_id else None)
        if not post_id:
            sys.stderr.write("skip {}: no post id\n".format(post_info_dir))
            self.skipped += 1
            return
        post_id = str(post_id)

        # Title and body are separate Patreon fields: title = the post title,
        # description = the post body. Prefer info.txt (its Content is already
        # rendered to HTML even when the API's content field is null); fall back
        # to the API's ProseMirror body.
        title = info.get("Title") or (_first(attrs, "title", default="") or "")
        title = title.strip()
        content = html_to_text(info.get("Content", "")) or extract_text(
            attrs, "content_json_string", "content"
        )
        if not content:
            content = html_to_text(info.get("Teaser", "")) or extract_text(
                attrs, "teaser_text_json_string", "teaser_text"
            )
        if content and content.strip() == title:
            content = ""

        url = info.get("URL") or _first(attrs, "url", default="")
        if url and url.startswith("/"):
            url = "https://www.patreon.com" + url
        if not url:
            url = "https://www.patreon.com/posts/{}".format(post_id)

        creator_folder = creator_folder_of(post_info_dir, "posts")
        vanity = (
            vanity_from_included(by_id, rels, attrs)
            or vanity_from_url(url)
            or vanity_from_folder(creator_folder)
        )
        model_id_src = _rel_id(rels, "campaign") or _rel_id(rels, "user")
        model_id, vanity = self._remember_creator(creator_folder, model_id_src, vanity)

        posted_at = (
            info.get("Published")
            or _first(attrs, "published_at", "created_at", "edited_at", default="")
            or ""
        )
        api_type = info.get("Type") or _first(attrs, "post_type", default="")
        paid, price = detect_paid_and_price(attrs, rels, by_id) if attrs else (0, 0.0)

        self.conn.execute(
            "INSERT INTO posts "
            "(post_id, title, content, posted_at, url, paid, price, archived, model_id, directory, api_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?) "
            "ON CONFLICT(post_id) DO UPDATE SET "
            "title=excluded.title, content=excluded.content, posted_at=excluded.posted_at, "
            "url=excluded.url, paid=excluded.paid, price=excluded.price, "
            "model_id=excluded.model_id, directory=excluded.directory, api_type=excluded.api_type",
            (post_id, title, content, posted_at, url, paid, price, model_id, post_dir,
             api_type),
        )
        self.posts += 1

    # ---- collections --------------------------------------------------- #

    def convert_collection(self, json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            return

        # A collection JSON may be the bare object or wrapped in {"data": {...}}.
        obj = _first(doc, "data", default=None)
        if not isinstance(obj, dict):
            obj = doc
        if not isinstance(obj, dict) or obj.get("type") != "collection":
            return

        coll_id = obj.get("id")
        attrs = _first(obj, "attributes", default={}) or {}
        if not coll_id:
            return
        coll_id = str(coll_id)

        creator_folder = creator_folder_of(json_path, "collections")
        cached = self.creators.get(creator_folder)
        if cached:
            model_id, username = cached
        else:
            username = vanity_from_folder(creator_folder)
            model_id = "vanity:{}".format(username) if username else ""

        post_ids = [str(p) for p in (_first(attrs, "post_ids", default=[]) or []) if p is not None]
        title = (_first(attrs, "title", default="") or "").strip()
        description = html_to_text(_first(attrs, "description", default="")) or ""
        posted_at = _first(attrs, "created_at", "edited_at", default="") or ""
        url = "https://www.patreon.com/collection/{}".format(coll_id)

        self.conn.execute(
            "INSERT INTO collections "
            "(collection_id, title, description, posted_at, url, model_id, username, post_ids) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(collection_id) DO UPDATE SET "
            "title=excluded.title, description=excluded.description, posted_at=excluded.posted_at, "
            "url=excluded.url, model_id=excluded.model_id, username=excluded.username, "
            "post_ids=excluded.post_ids",
            (coll_id, title, description, posted_at, url, model_id, username, ",".join(post_ids)),
        )
        self.collections += 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("root", nargs="?", default=DEFAULT_ROOT,
                        help="patreon-dl media root (default: {})".format(DEFAULT_ROOT))
    parser.add_argument("--db", default=None,
                        help="output database path (default: <root>/user_data.db)")
    opts = parser.parse_args(argv)

    root = os.path.abspath(opts.root)
    if not os.path.isdir(root):
        parser.error("media root does not exist: {}".format(root))
    db_path = opts.db or os.path.join(root, "user_data.db")

    conn = open_db(db_path)
    conv = Converter(conn)
    try:
        # Posts first so the creator cache is populated before collections,
        # which may not carry a campaign id of their own.
        for post_info_dir in find_post_info_dirs(root):
            conv.convert_post(post_info_dir)
        for jp in find_collection_json(root):
            conv.convert_collection(jp)
        conn.commit()
    finally:
        conn.close()

    print(
        "Wrote {db}\n  posts: {posts}  collections: {colls}  skipped: {skipped}".format(
            db=db_path, posts=conv.posts, colls=conv.collections, skipped=conv.skipped
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
