"""Entry point for the Patreon Metadata Sync Stash plugin.

Stash runs this as an external 'raw' plugin task: it sends a JSON payload on
stdin (server connection + task args) and reads task output from stdout. Progress
and messages go to the Stash log viewer via stderr (see log.py).

patreon-dl saves each Patreon post as a folder (which Stash ingests as a gallery)
and writes the post's metadata beside the media in ``post_info/info.txt`` and
``post_info/post-api.json``; collections are described by JSON under
``collections/``. This plugin reads those files directly off the data path -- no
database, no converter -- and for each post:

- updates the matching Stash **gallery** and every **image** inside the post
  folder (matched by the post folder basename, which embeds the numeric post id)
  with the post's title, description, date, URL, studio and creator performer;

and for each collection, creates a Stash gallery and attaches the member posts'
images to it.

A Patreon post has a single creator, so there is no @mention or crew handling,
and the title is taken as-is (no length-based truncation). The creator's studio
and performer are created if they don't already exist.
"""

import base64
import json
import os
import sys

import log
from stash import StashClient
from patreon_source import PatreonSource
from media import MediaProcessor, compile_name_pattern

# Plugin id == the manifest filename without extension.
PLUGIN_ID = "patreon-stash-sync"

DEFAULT_PARENT_STUDIO = "Patreon (network)"


def get_setting(config, key, default):
    value = config.get(key)
    if value is None or value == "":
        return default
    return value


def creator_url(username):
    return "https://www.patreon.com/{}".format(username)


class PerformerResolver:
    """Resolve a creator to a Stash performer, creating one if it doesn't exist."""

    def __init__(self, client):
        self.client = client
        self.cache = {}

    def resolve(self, username):
        if not username:
            return []
        key = username.lower()
        if key in self.cache:
            return self.cache[key]
        result = self.client.find_performers_by_name(username)
        ids = [p["id"] for p in result["exact"]]
        if not ids:
            # Stash compiles EQUALS to SQL LIKE, so a vanity containing '_' can
            # collide with an existing performer name on create. Attach a single
            # near-match instead of failing to create a duplicate.
            near = result["name_like"]
            if len(near) == 1:
                ids = [near[0]["id"]]
                log.LogInfo("Matched existing performer '{}' for '{}'".format(
                    near[0]["name"], username))
            elif len(near) > 1:
                names = ", ".join("'{}'".format(p["name"]) for p in near)
                log.LogWarning(
                    "'{}' matches several existing performers ({}); not creating one. "
                    "Add the Patreon vanity as an alias to the correct performer.".format(
                        username, names)
                )
            else:
                new_id = self.client.create_performer(username, creator_url(username))
                if new_id:
                    ids = [new_id]
                    log.LogInfo("Created performer '{}'".format(username))
        self.cache[key] = ids
        return ids


class StudioResolver:
    """Resolve a creator to a per-creator sub-studio, creating it if missing."""

    def __init__(self, client, parent_id, icon_data_url):
        self.client = client
        self.parent_id = parent_id
        self.icon = icon_data_url
        self.cache = {}

    def resolve(self, username):
        if not username:
            return None
        if username in self.cache:
            return self.cache[username]
        name = "{} (Patreon)".format(username)
        studio_id = self.client.find_studio(name)
        if not studio_id:
            studio_id = self.client.create_studio(
                name, self.parent_id, creator_url(username),
                "Sub Studio for Patreon content creator", self.icon,
            )
            log.LogInfo("Created studio '{}'".format(name))
        self.cache[username] = studio_id
        return studio_id


class TagResolver:
    def __init__(self, client):
        self.client = client
        self.cache = {}

    def resolve(self, name):
        if name in self.cache:
            return self.cache[name]
        tag_id = self.client.find_tag(name)
        if not tag_id:
            tag_id = self.client.create_tag(name)
            if tag_id:
                log.LogInfo("Created tag '{}'".format(name))
        self.cache[name] = tag_id
        return tag_id


class TagTextMatcher:
    """Match existing Stash tags against post text the way Stash's auto-tagger
    matches names against file paths. Only existing tags are matched (never
    created); tags flagged ignore_auto_tag are skipped."""

    def __init__(self, client):
        self.matchers = []
        for tag in client.find_all_tags():
            if tag.get("ignore_auto_tag"):
                continue
            names = [tag["name"]] + (tag.get("aliases") or [])
            patterns = [p for p in (compile_name_pattern(n) for n in names) if p]
            if patterns:
                self.matchers.append((tag["id"], patterns))

    def match(self, text):
        lowered = text.lower()
        return [tid for tid, patterns in self.matchers if any(p.search(lowered) for p in patterns)]


def load_icon(server_connection):
    plugin_dir = server_connection.get("PluginDir")
    if not plugin_dir:
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
    icon_path = os.path.join(plugin_dir, "patreon.png")
    if os.path.isfile(icon_path):
        with open(icon_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
        return "data:image/png;base64,{}".format(encoded)
    return None


# --------------------------------------------------------------------------- #
# Matching helpers
# --------------------------------------------------------------------------- #

def gallery_paths(gallery):
    paths = []
    folder = gallery.get("folder") or {}
    if folder.get("path"):
        paths.append(folder["path"])
    for f in gallery.get("files") or []:
        if f.get("path"):
            paths.append(f["path"])
    return paths


def image_paths(image):
    return [vf.get("path") for vf in (image.get("visual_files") or []) if vf.get("path")]


def path_contains(paths, needle):
    return any(needle in p for p in paths if p)


def merged_tag_ids(existing, new):
    merged = list(existing)
    for tag_id in new:
        if tag_id not in merged:
            merged.append(tag_id)
    return merged


def build_meta(processor, post, studio_id, performer_ids):
    """Fields shared by a post's gallery and its images (title as-is, details
    HTML-stripped as a safety net, date normalised)."""
    details = processor.remove_html_tags(post["content"] or "")
    date = processor.format_date(post["date"])
    meta = {"title": post["title"] or "", "details": details, "organized": True}
    if date:
        meta["date"] = date
    if post["url"]:
        meta["urls"] = [post["url"]]
    if studio_id:
        meta["studio_id"] = studio_id
    if performer_ids:
        meta["performer_ids"] = performer_ids
    return meta


# --------------------------------------------------------------------------- #
# Per-post / per-collection sync
# --------------------------------------------------------------------------- #

def sync_post(client, post, processor, studios, performers, tags, tag_matcher, mode, totals):
    include_all = mode in ("full", "tag")
    basename = os.path.basename((post["directory"] or "").rstrip("/\\"))
    if not basename:
        return

    galleries = [g for g in client.find_galleries(basename, include_all)
                 if path_contains(gallery_paths(g), basename)]
    images = [i for i in client.find_images(basename, include_all)
              if path_contains(image_paths(i), basename)]
    if not galleries and not images:
        return
    totals["matched"] += len(galleries) + len(images)

    text = "{}\n{}".format(post["title"] or "", post["content"] or "").strip()

    if mode == "tag":
        new_tags = tag_matcher.match(processor.remove_html_tags(text)) if (tag_matcher and text) else []
        _apply_tag_only(client, galleries, images, new_tags, totals)
        return

    studio_id = studios.resolve(post["vanity"])
    performer_ids = performers.resolve(post["vanity"])
    meta = build_meta(processor, post, studio_id, performer_ids)
    post_tags = tag_matcher.match(processor.remove_html_tags(text)) if (tag_matcher and text) else []

    for gallery in galleries:
        update = dict(meta, id=gallery["id"])
        update["tag_ids"] = merged_tag_ids([t["id"] for t in gallery.get("tags") or []], post_tags)
        try:
            client.update_gallery(update)
            totals["galleries"] += 1
        except RuntimeError as e:
            log.LogError("  Failed to update gallery {}: {}".format(gallery["id"], e))
    for image in images:
        update = dict(meta, id=image["id"])
        update["tag_ids"] = merged_tag_ids([t["id"] for t in image.get("tags") or []], post_tags)
        try:
            client.update_image(update)
            totals["images"] += 1
        except RuntimeError as e:
            log.LogError("  Failed to update image {}: {}".format(image["id"], e))


def _apply_tag_only(client, galleries, images, new_tags, totals):
    """Additive: only merge tags in, leaving every other field untouched."""
    if not new_tags:
        totals["skipped"] += len(galleries) + len(images)
        return
    for record, kind, fn in (
        [(g, "galleries", client.update_gallery) for g in galleries]
        + [(i, "images", client.update_image) for i in images]
    ):
        existing = [t["id"] for t in record.get("tags") or []]
        merged = merged_tag_ids(existing, new_tags)
        if merged == existing:
            totals["skipped"] += 1
            continue
        try:
            fn({"id": record["id"], "tag_ids": merged})
            totals[kind] += 1
        except RuntimeError as e:
            log.LogError("  Failed to tag {} {}: {}".format(kind, record["id"], e))


def sync_collection(client, source, coll, processor, studios, performers, totals):
    title = coll["title"] or "Collection {}".format(coll["collection_id"])
    studio_id = studios.resolve(coll["vanity"])
    performer_ids = performers.resolve(coll["vanity"])
    date = processor.format_date(coll["date"])
    details = processor.remove_html_tags(coll["description"] or "")
    urls = [coll["url"]] if coll["url"] else []

    gallery_id = client.find_gallery_by_title(title)
    if not gallery_id:
        gallery_id = client.create_gallery(title, urls, details, date, studio_id, performer_ids, [])
        if not gallery_id:
            return
        log.LogInfo("Created collection gallery '{}'".format(title))
    else:
        update = {"id": gallery_id, "title": title, "details": details, "organized": True}
        if date:
            update["date"] = date
        if urls:
            update["urls"] = urls
        if studio_id:
            update["studio_id"] = studio_id
        if performer_ids:
            update["performer_ids"] = performer_ids
        try:
            client.update_gallery(update)
        except RuntimeError as e:
            log.LogError("  Failed to update collection gallery {}: {}".format(gallery_id, e))
    totals["collections"] += 1

    # Attach every member post's images (images can belong to several galleries).
    image_ids = []
    for pid in coll["post_ids"]:
        directory = source.directory_for_post(pid)
        if not directory:
            continue
        basename = os.path.basename(directory.rstrip("/\\"))
        for image in client.find_images(basename, True):
            if path_contains(image_paths(image), basename) and image["id"] not in image_ids:
                image_ids.append(image["id"])
    if image_ids:
        try:
            client.add_gallery_images(gallery_id, image_ids)
            log.LogInfo("  '{}': attached {} image(s)".format(title, len(image_ids)))
        except RuntimeError as e:
            log.LogError("  Failed to attach images to '{}': {}".format(title, e))


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw else {}
    except ValueError:
        payload = {}

    server = payload.get("server_connection") or {}
    args = payload.get("args") or {}
    mode = args.get("mode") or "sync"

    client = StashClient(server)
    try:
        config = client.get_plugin_config(PLUGIN_ID)
    except RuntimeError as e:
        msg = "Could not read plugin settings: {}".format(e)
        log.LogError(msg)
        return msg

    client.dry_run = bool(args.get("dryRun")) or bool(get_setting(config, "dryRun", False))

    data_path = get_setting(config, "dataPath", "")
    parent_studio_name = get_setting(config, "parentStudioName", DEFAULT_PARENT_STUDIO)
    auto_tag_from_text = bool(get_setting(config, "autoTagFromText", False))
    sync_collections = bool(get_setting(config, "syncCollections", True))

    if not data_path:
        msg = "No data path configured. Set 'Patreon Data Path' in the plugin settings."
        log.LogError(msg)
        return msg

    tag_only = mode == "tag"
    log.LogInfo("Starting Patreon {} pass. Data path: {}".format(mode, data_path))
    if client.dry_run:
        log.LogInfo("DRY RUN - no changes will be written; logging intended changes only.")

    parent_studio_id = None
    if not tag_only:
        parent_studio_id = client.find_studio(parent_studio_name)
        if not parent_studio_id:
            # Create the parent studio instead of aborting, so a fresh Stash
            # doesn't require any manual setup.
            parent_studio_id = client.create_studio(
                parent_studio_name, None, "https://www.patreon.com",
                "Patreon creators", None,
            )
            if parent_studio_id:
                log.LogInfo("Created parent studio '{}'".format(parent_studio_name))
            else:
                msg = "Could not create parent studio '{}'.".format(parent_studio_name)
                log.LogError(msg)
                return msg

    source = PatreonSource(data_path)
    posts = list(source.iter_posts())
    log.LogInfo("Found {} Patreon post(s) under {}".format(len(posts), data_path))
    if not source.has_posts():
        log.LogWarning(
            "No Patreon posts found under {}. Expected '<vanity> - <Name>/posts/"
            "<id> - <title>/post_info/' folders as Stash sees them.".format(data_path)
        )
        return

    processor = MediaProcessor()
    studios = StudioResolver(client, parent_studio_id, load_icon(server))
    performers = PerformerResolver(client)
    tags = TagResolver(client)
    tag_matcher = None
    if tag_only or auto_tag_from_text:
        tag_matcher = TagTextMatcher(client)
        log.LogInfo("Auto-tagging from post text enabled ({} tags loaded)".format(
            len(tag_matcher.matchers)))

    totals = {"galleries": 0, "images": 0, "collections": 0, "matched": 0, "skipped": 0}

    for index, post in enumerate(posts):
        log.LogProgress(index / len(posts))
        try:
            sync_post(client, post, processor, studios, performers, tags, tag_matcher, mode, totals)
        except Exception as e:
            log.LogError("Error on post {}: {}".format(post.get("post_id"), e))

    if not tag_only and sync_collections:
        collections = list(source.iter_collections())
        if collections:
            log.LogInfo("Found {} collection(s)".format(len(collections)))
        for coll in collections:
            try:
                sync_collection(client, source, coll, processor, studios, performers, totals)
            except Exception as e:
                log.LogError("Error on collection {}: {}".format(coll.get("collection_id"), e))

    log.LogProgress(1.0)
    if totals["matched"] == 0:
        log.LogWarning(
            "Matched 0 Stash galleries/images. Check that Stash has scanned the "
            "Patreon folders and that the Patreon Data Path is the path Stash sees."
        )
    verb = "would update" if client.dry_run else "updated"
    log.LogInfo(
        "{} complete. Galleries {}: {}, Images {}: {}, Collections: {}, Skipped: {}".format(
            mode, verb, totals["galleries"], verb, totals["images"],
            totals["collections"], totals["skipped"]
        )
    )


if __name__ == "__main__":
    # Raw plugins return their result as JSON on stdout. A non-empty error is
    # logged by Stash at the error level and marks the task as failed.
    error = main()
    print(json.dumps({"error": error} if error else {"output": "ok"}))
