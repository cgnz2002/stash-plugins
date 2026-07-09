"""Read patreon-dl's on-disk output directly.

patreon-dl already writes the metadata next to the media, so there is no database
step: this module walks the data path and reads each post's ``post_info`` and each
``collections`` entry straight off disk.

Layout produced by patreon-dl:

    <data path>/<vanity> - <Creator Name>/
        posts/<postId> - <Post Title>/
            images/ attachments/ audio/     media (Stash ingests each folder as a gallery)
            .thumbnails/  post_info/*.webp  auxiliary (ignored)
            post_info/
                info.txt                    flat "Key: value" summary  (primary source)
                post-api.json               raw Patreon JSON:API        (fallback + ids/vanity)
        collections/<collectionId> - <Title>/
            *.json                          raw collection JSON

Everything here is standard library only. The plugin runs on the Stash host, which
already has the media path mounted, so it reads these files with no extra step.
"""

import html
import json
import os
import re

_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_NL_RE = re.compile(r"\n{3,}")
_LEADING_ID_RE = re.compile(r"^\s*(\d+)")


# --------------------------------------------------------------------------- #
# JSON:API + ProseMirror helpers
# --------------------------------------------------------------------------- #

def _first(d, *keys, default=None):
    """Return d[k] for the first present, non-None key (Patreon renames keys
    between API revisions, so lookups list several candidates)."""
    if not isinstance(d, dict):
        return default
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _rel_id(relationships, name):
    rel = _first(relationships, name, default={})
    data = _first(rel, "data", default=None)
    return data.get("id") if isinstance(data, dict) else None


# ProseMirror/TipTap block node types that should end with a line break.
_BLOCK_NODES = {
    "paragraph", "heading", "blockquote", "list_item", "listItem",
    "code_block", "codeBlock", "horizontal_rule", "horizontalRule",
}


def _prosemirror_text(node):
    """Extract plain text from a ProseMirror doc node. Patreon stores the post
    body in ``content_json_string`` (teaser in ``teaser_text_json_string``) as a
    ProseMirror document; text nodes carry ``text``, hard breaks/block nodes
    become newlines, and mentions become ``@label``.
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
    return inner + "\n" if ntype in _BLOCK_NODES else inner


def _clean(text):
    return _MULTI_NL_RE.sub("\n\n", text).strip() if text else ""


def html_to_text(value):
    """Strip HTML to plain text (Patreon post bodies and info.txt Content are
    HTML). Block tags become newlines; entities are unescaped."""
    if not value:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    text = re.sub(r"</(?:p|div|li|ul|ol|h[1-6]|blockquote)>", "\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub("", text)
    return _clean(html.unescape(text))


def _prosemirror_string(raw):
    if not raw:
        return ""
    try:
        doc = json.loads(raw) if isinstance(raw, str) else raw
        return _clean(_prosemirror_text(doc))
    except (ValueError, TypeError):
        return ""


# --------------------------------------------------------------------------- #
# info.txt parsing
# --------------------------------------------------------------------------- #

_INFO_KEYS = ["ID", "Type", "Title", "Teaser", "Content", "Published", "Last Edited", "URL"]
_INFO_KEY_RE = re.compile(r"^(" + "|".join(re.escape(k) for k in _INFO_KEYS) + r"):[ ]?(.*)$")


def parse_info_txt(path):
    """Parse patreon-dl's post_info/info.txt (flat "Key: value"; a value may span
    lines, so lines that don't start a known key are appended to the current one).
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().split("\n")
    except OSError:
        return {}
    fields, current = {}, None
    for line in lines:
        m = _INFO_KEY_RE.match(line)
        if m:
            current = m.group(1)
            fields[current] = m.group(2)
        elif current is not None:
            fields[current] += "\n" + line
    return {k: v.strip() for k, v in fields.items()}


# --------------------------------------------------------------------------- #
# Creator vanity
# --------------------------------------------------------------------------- #

def vanity_from_url(url):
    """'https://www.patreon.com/spicehead1/posts/...' -> 'spicehead1'."""
    if not url:
        return ""
    m = re.search(r"patreon\.com/([^/]+)/posts", url) or re.match(r"/([^/]+)/posts", url)
    return m.group(1) if m else ""


def vanity_from_folder(creator_folder):
    if not creator_folder:
        return ""
    base = os.path.basename(creator_folder.rstrip("/\\"))
    return base.split(" - ", 1)[0].strip() if " - " in base else base.strip()


def _vanity_from_api(doc):
    """The included ``user`` object's vanity is the authoritative creator handle."""
    included = _first(doc, "included", default=[]) or []
    by_id = {o.get("id"): o for o in included if isinstance(o, dict) and o.get("id")}
    data = _first(doc, "data", default={}) or {}
    rels = _first(data, "relationships", default={}) or {}
    user = by_id.get(_rel_id(rels, "user"))
    if user:
        v = _first(user.get("attributes") or {}, "vanity")
        if v:
            return v
    return ""


def creator_folder_of(path, marker):
    """Return the ``<vanity> - <Name>`` folder above a ``posts``/``collections``
    marker directory in ``path``, or None."""
    parts = path.replace("\\", "/").split("/")
    for i in range(len(parts) - 1, 0, -1):
        if parts[i].lower() == marker:
            return "/".join(parts[:i])
    return None


# --------------------------------------------------------------------------- #
# Walking
# --------------------------------------------------------------------------- #

def find_post_info_dirs(root):
    for dirpath, _dirs, files in os.walk(root):
        if os.path.basename(dirpath) == "post_info" and (
            "info.txt" in files or "post-api.json" in files
        ):
            yield dirpath


def find_collection_json(root):
    for dirpath, _dirs, files in os.walk(root):
        parts = dirpath.replace("\\", "/").lower().split("/")
        if "collections" not in parts:
            continue
        for name in files:
            if name.lower().endswith(".json"):
                yield os.path.join(dirpath, name)


# --------------------------------------------------------------------------- #
# Parsing a post / collection into a plain dict
# --------------------------------------------------------------------------- #

def parse_post(post_info_dir):
    """Return a post dict for one post_info directory, or None if unusable.

    Prefers info.txt (its Content is already rendered HTML even when the API's
    ``content`` field is null); falls back to post-api.json for the body and for
    the authoritative creator vanity.
    """
    post_dir = os.path.dirname(post_info_dir)
    info = parse_info_txt(os.path.join(post_info_dir, "info.txt"))

    doc = {}
    api_path = os.path.join(post_info_dir, "post-api.json")
    if os.path.isfile(api_path):
        try:
            with open(api_path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            doc = {}
    attrs = _first(_first(doc, "data", default={}) or {}, "attributes", default={}) or {}

    post_id = info.get("ID") or _first(_first(doc, "data", default={}) or {}, "id")
    if not post_id:
        m = _LEADING_ID_RE.match(os.path.basename(post_dir))
        post_id = m.group(1) if m else None
    if not post_id:
        return None
    post_id = str(post_id)

    title = (info.get("Title") or _first(attrs, "title", default="") or "").strip()
    content = html_to_text(info.get("Content", "")) or _prosemirror_string(
        _first(attrs, "content_json_string")
    ) or html_to_text(_first(attrs, "content", default=""))
    if not content:
        content = html_to_text(info.get("Teaser", "")) or _prosemirror_string(
            _first(attrs, "teaser_text_json_string")
        )
    if content and content.strip() == title:
        content = ""

    url = info.get("URL") or _first(attrs, "url", default="")
    if url and url.startswith("/"):
        url = "https://www.patreon.com" + url
    if not url:
        url = "https://www.patreon.com/posts/{}".format(post_id)

    creator_folder = creator_folder_of(post_info_dir, "posts")
    vanity = _vanity_from_api(doc) or vanity_from_url(url) or vanity_from_folder(creator_folder)

    return {
        "post_id": post_id,
        "title": title,
        "content": content,
        "date": info.get("Published") or _first(attrs, "published_at", "created_at", "edited_at", default="") or "",
        "url": url,
        "api_type": info.get("Type") or _first(attrs, "post_type", default=""),
        "directory": post_dir,
        "vanity": vanity,
    }


def parse_collection(json_path):
    """Return a collection dict, or None if the file isn't a collection."""
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None

    obj = _first(doc, "data", default=None)
    if not isinstance(obj, dict):
        obj = doc
    if not isinstance(obj, dict) or obj.get("type") != "collection" or not obj.get("id"):
        return None
    attrs = _first(obj, "attributes", default={}) or {}

    creator_folder = creator_folder_of(json_path, "collections")
    return {
        "collection_id": str(obj["id"]),
        "title": (_first(attrs, "title", default="") or "").strip(),
        "description": html_to_text(_first(attrs, "description", default="")),
        "date": _first(attrs, "created_at", "edited_at", default="") or "",
        "url": "https://www.patreon.com/collection/{}".format(obj["id"]),
        "post_ids": [str(p) for p in (_first(attrs, "post_ids", default=[]) or []) if p is not None],
        "vanity": vanity_from_folder(creator_folder),
    }


# --------------------------------------------------------------------------- #
# Source
# --------------------------------------------------------------------------- #

class PatreonSource:
    """Directly reads patreon-dl output under a data path."""

    def __init__(self, data_path):
        self.data_path = data_path
        self._post_info_dirs = list(find_post_info_dirs(data_path))
        # post id -> post folder, so a collection can locate a member post's
        # images even before that post is synced. Keyed off the folder name
        # (``<postId> - <title>``), which patreon-dl always prefixes with the id.
        self._index = {}
        for info_dir in self._post_info_dirs:
            post_dir = os.path.dirname(info_dir)
            m = _LEADING_ID_RE.match(os.path.basename(post_dir))
            if m:
                self._index[m.group(1)] = post_dir

    def iter_posts(self):
        for info_dir in self._post_info_dirs:
            post = parse_post(info_dir)
            if post:
                yield post

    def has_posts(self):
        return bool(self._post_info_dirs)

    def directory_for_post(self, post_id):
        return self._index.get(str(post_id))

    def iter_collections(self):
        for json_path in find_collection_json(self.data_path):
            coll = parse_collection(json_path)
            if coll:
                yield coll
