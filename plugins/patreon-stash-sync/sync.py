"""Entry point for the Patreon Metadata Sync Stash plugin.

Stash runs this as an external 'raw' plugin task: it sends a JSON payload on
stdin (server connection + task args) and reads task output from stdout. Progress
and messages go to the Stash log viewer via stderr (see log.py).

Unlike of-stash-sync (which matches OF-Scraper media by filename and syncs
scenes/images), Patreon posts are downloaded by patreon-dl as folders that Stash
ingests as **galleries**. So this plugin:

- syncs each Patreon post's metadata onto the matching Stash **gallery** and onto
  every **image** inside that post folder (matched by the post folder basename,
  which embeds the numeric post id);
- creates one Stash gallery per Patreon **collection** and attaches the member
  posts' images to it.

The database is produced from patreon-dl output by convert_patreon_to_db.py.
"""

import base64
import json
import os
import sys

import log
from stash import StashClient
from patreon_db import PatreonDatabase
from media import MediaProcessor, compile_name_pattern

# Plugin id == the manifest filename without extension.
PLUGIN_ID = "patreon-stash-sync"

DEFAULT_PARENT_STUDIO = "Patreon (network)"
DEFAULT_MAX_TITLE_LENGTH = 65


def get_setting(config, key, default):
    value = config.get(key)
    if value is None or value == "":
        return default
    return value


def creator_url(username):
    """Creator/campaign page URL, used for performer and studio URLs."""
    return "https://www.patreon.com/{}".format(username)


class PerformerResolver:
    """Find performers by name/alias, optionally creating missing ones."""

    def __init__(self, client, auto_create, crew_tag_id=""):
        self.client = client
        self.auto_create = auto_create
        # Matched by tag id (stable) rather than name, so renaming the tag in
        # Stash doesn't silently disable crew handling. Empty disables it.
        self.crew_tag_id = str(crew_tag_id or "").strip()
        self.cache = {}
        self.info_cache = {}

    def creator_credit(self, username):
        info = self.info_cache.get(username.lower())
        if not info:
            return set(), None
        return info["roles"], info["name"]

    def resolve(self, username, from_mention=False):
        key = username.lower()
        if key in self.cache:
            return self.cache[key]
        result = self.client.find_performers_by_name(username)
        exact = result["exact"]
        ids = [p["id"] for p in exact]
        # A performer carrying the crew tag is credited in the photographer field
        # (galleries/images have no director field), for both the creator and any
        # @mentioned collaborator. Prefer the crew-tagged performer's display name.
        roles = set()
        credit_name = None
        for p in exact:
            ptag_ids = {t.get("id") for t in (p.get("tags") or [])}
            if self.crew_tag_id and self.crew_tag_id in ptag_ids:
                roles = {"photographer"}
                credit_name = p.get("name")
                break
        if credit_name is None and exact:
            credit_name = exact[0]["name"]
        self.info_cache[key] = {"roles": roles, "name": credit_name}
        if not ids and self.auto_create:
            near = result["name_like"]
            if len(near) == 1:
                ids = [near[0]["id"]]
                log.LogInfo(
                    "Matched existing performer '{}' for '{}'".format(near[0]["name"], username)
                )
            elif len(near) > 1:
                names = ", ".join("'{}'".format(p["name"]) for p in near)
                log.LogWarning(
                    "'{}' matches several existing performers ({}); not creating "
                    "or attaching any. Add the Patreon vanity as an alias to the "
                    "correct performer.".format(username, names)
                )
            else:
                new_id = self.client.create_performer(username, creator_url(username))
                if new_id:
                    ids = [new_id]
                    source = " (from @mention)" if from_mention else ""
                    log.LogInfo("Created performer '{}'{}".format(username, source))
        self.cache[key] = ids
        return ids


class StudioResolver:
    def __init__(self, client, parent_id, icon_data_url):
        self.client = client
        self.parent_id = parent_id
        self.icon = icon_data_url
        self.cache = {}

    def resolve(self, username):
        if username in self.cache:
            return self.cache[username]
        name = "{} (Patreon)".format(username)
        studio_id = self.client.find_studio(name)
        if not studio_id:
            studio_id = self.client.create_studio(
                name,
                self.parent_id,
                creator_url(username),
                "Sub Studio for Patreon content creator",
                self.icon,
            )
            log.LogInfo("Created studio '{}'".format(name))
        self.cache[username] = studio_id
        return studio_id


class TagResolver:
    """Resolve (and create if missing) the paid/archived tags on demand."""

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
    """Match existing Stash tags against post text, the way Stash's built-in
    auto-tagger matches names against file paths. Only existing tags are matched
    (never created); tags flagged ignore_auto_tag are skipped.
    """

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
        found = []
        for tag_id, patterns in self.matchers:
            if any(p.search(lowered) for p in patterns):
                found.append(tag_id)
        return found


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


# --------------------------------------------------------------------------- #
# Metadata assembly
# --------------------------------------------------------------------------- #

def post_text(post_row):
    """Combined title + description, for @mention and tag-from-text matching."""
    title = post_row["title"] or ""
    content = post_row["content"] or ""
    return (title + "\n" + content).strip()


def collect_tag_ids(processor, post_row, text, tags, tag_matcher):
    tag_ids = []
    price = post_row["price"] or 0
    if post_row["paid"] and price and float(price) > 0:
        tag_id = tags.resolve("paid")
        if tag_id:
            tag_ids.append(tag_id)
    if post_row["archived"]:
        tag_id = tags.resolve("archived")
        if tag_id:
            tag_ids.append(tag_id)
    if tag_matcher is not None and text:
        for tag_id in tag_matcher.match(processor.remove_html_tags(text)):
            if tag_id not in tag_ids:
                tag_ids.append(tag_id)
    return tag_ids


def collect_photographers(processor, resolver, text, creator_roles, creator_name, creator_ids):
    """Return (photographer_names, crew_ids, mention_performer_ids).

    Anyone tagged as crew -- the creator or an @mentioned collaborator -- belongs
    in the gallery/image photographer field, not the performers list.
    """
    photographers = []
    crew_ids = set()
    if creator_roles and creator_name:
        photographers.append(creator_name)
        crew_ids.update(creator_ids)

    mention_performer_ids = []
    if text:
        for mention in processor.parse_mentions(text):
            ids = resolver.resolve(mention, from_mention=True)
            m_roles, m_name = resolver.creator_credit(mention)
            if m_roles:
                if m_name and m_name not in photographers:
                    photographers.append(m_name)
                crew_ids.update(ids)
            else:
                for pid in ids:
                    if pid not in mention_performer_ids:
                        mention_performer_ids.append(pid)
    return photographers, crew_ids, mention_performer_ids


def build_common_meta(processor, post_row, studio_id, performer_ids, photographers,
                      max_title_length):
    """Fields shared by the gallery and its images (everything except tag_ids and
    the record id). Title is truncated and details are HTML-stripped so no markup
    reaches Stash even if a DB row still carried some.
    """
    title = post_row["title"] or ""
    if len(title) > max_title_length:
        title = processor.truncate_title(title, max_title_length)
    details = processor.remove_html_tags(post_row["content"] or "")
    date = processor.format_date(post_row["posted_at"])

    meta = {"title": title, "details": details, "organized": True}
    if date:
        meta["date"] = date
    url = post_row["url"]
    if url:
        meta["urls"] = [url]
    if studio_id:
        meta["studio_id"] = studio_id
    if performer_ids:
        meta["performer_ids"] = performer_ids
    if photographers:
        meta["photographer"] = ", ".join(photographers)
    return meta


def merged_tag_ids(existing, new):
    merged = list(existing)
    for tag_id in new:
        if tag_id not in merged:
            merged.append(tag_id)
    return merged


# --------------------------------------------------------------------------- #
# Per-post sync
# --------------------------------------------------------------------------- #

def sync_post(client, db, post_row, username_map, processor, studios, performers,
              tags, tag_matcher, mode, totals):
    include_all = mode in ("full", "tag", "crew")
    directory = post_row["directory"] or ""
    basename = os.path.basename(directory.rstrip("/\\"))
    if not basename:
        return

    galleries = [
        g for g in client.find_galleries(basename, include_all)
        if path_contains(gallery_paths(g), basename)
    ]
    images = [
        i for i in client.find_images(basename, include_all)
        if path_contains(image_paths(i), basename)
    ]
    if not galleries and not images:
        return

    username = username_map.get(post_row["model_id"])
    text = post_text(post_row)

    studio_id = None
    creator_ids = []
    creator_roles, creator_name = set(), None
    if username and mode not in ("tag",):
        creator_ids = performers.resolve(username)
        creator_roles, creator_name = performers.creator_credit(username)
        if mode not in ("crew",):
            studio_id = studios.resolve(username)

    if mode == "tag":
        new_tags = collect_tag_ids(processor, post_row, text, tags, tag_matcher)
        _apply_tag_only(client, galleries, images, new_tags, totals)
        return

    photographers, crew_ids, mention_ids = collect_photographers(
        processor, performers, text, creator_roles, creator_name, creator_ids
    )

    if mode == "crew":
        _apply_crew_only(client, galleries, images, photographers, crew_ids,
                         creator_ids, totals)
        return

    # Full / sync: build the performer list (creator unless crew, plus non-crew
    # @mentions; never leave it empty).
    performer_ids = [] if creator_roles else list(creator_ids)
    for pid in mention_ids:
        if pid not in performer_ids:
            performer_ids.append(pid)
    if not performer_ids:
        performer_ids = list(creator_ids)

    meta = build_common_meta(
        processor, post_row, studio_id, performer_ids, photographers, processor.max_title_length
    )
    post_tags = collect_tag_ids(processor, post_row, text, tags, tag_matcher)

    for gallery in galleries:
        update = dict(meta)
        update["id"] = gallery["id"]
        update["tag_ids"] = merged_tag_ids([t["id"] for t in gallery.get("tags") or []], post_tags)
        try:
            client.update_gallery(update)
            totals["galleries"] += 1
        except RuntimeError as e:
            log.LogError("  Failed to update gallery {}: {}".format(gallery["id"], e))
    for image in images:
        update = dict(meta)
        update["id"] = image["id"]
        update["tag_ids"] = merged_tag_ids([t["id"] for t in image.get("tags") or []], post_tags)
        try:
            client.update_image(update)
            totals["images"] += 1
        except RuntimeError as e:
            log.LogError("  Failed to update image {}: {}".format(image["id"], e))


def _apply_tag_only(client, galleries, images, new_tags, totals):
    """Additive: only merge tags in, leave every other field untouched."""
    if not new_tags:
        totals["skipped"] += len(galleries) + len(images)
        return
    for gallery in galleries:
        existing = [t["id"] for t in gallery.get("tags") or []]
        merged = merged_tag_ids(existing, new_tags)
        if merged == existing:
            totals["skipped"] += 1
            continue
        try:
            client.update_gallery({"id": gallery["id"], "tag_ids": merged})
            totals["galleries"] += 1
        except RuntimeError as e:
            log.LogError("  Failed to tag gallery {}: {}".format(gallery["id"], e))
    for image in images:
        existing = [t["id"] for t in image.get("tags") or []]
        merged = merged_tag_ids(existing, new_tags)
        if merged == existing:
            totals["skipped"] += 1
            continue
        try:
            client.update_image({"id": image["id"], "tag_ids": merged})
            totals["images"] += 1
        except RuntimeError as e:
            log.LogError("  Failed to tag image {}: {}".format(image["id"], e))


def _apply_crew_only(client, galleries, images, photographers, crew_ids, creator_ids, totals):
    """Surgical: only set the photographer field and prune crew from performers."""
    credit = ", ".join(photographers) if photographers else None

    def apply(kind, record, update_fn):
        existing_perf = [p["id"] for p in record.get("performers") or []]
        new_perf = [pid for pid in existing_perf if pid not in crew_ids]
        if not new_perf:
            new_perf = list(creator_ids)
        update = {}
        if new_perf != existing_perf:
            update["performer_ids"] = new_perf
        if credit is not None and credit != (record.get("photographer") or ""):
            update["photographer"] = credit
        if not update:
            totals["skipped"] += 1
            return
        update["id"] = record["id"]
        try:
            update_fn(update)
            totals[kind] += 1
        except RuntimeError as e:
            log.LogError("  Failed crew update on {} {}: {}".format(kind, record["id"], e))

    for gallery in galleries:
        apply("galleries", gallery, client.update_gallery)
    for image in images:
        apply("images", image, client.update_image)


# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #

def sync_collection(client, db, coll, username_map, processor, studios, performers,
                    tags, totals):
    title = coll["title"] or "Collection {}".format(coll["collection_id"])
    username = coll["username"] or username_map.get(coll["model_id"])

    studio_id = studios.resolve(username) if username else None
    performer_ids = performers.resolve(username) if username else []
    # A crew-tagged creator is credited as photographer, not a performer.
    photographers = []
    if username:
        roles, name = performers.creator_credit(username)
        if roles and name:
            photographers.append(name)
            performer_ids = []

    date = processor.format_date(coll["posted_at"])
    details = processor.remove_html_tags(coll["description"] or "")
    urls = [coll["url"]] if coll["url"] else []

    gallery_id = client.find_gallery_by_title(title)
    if not gallery_id:
        gallery_id = client.create_gallery(
            title, urls, details, date, studio_id, performer_ids, []
        )
        if not gallery_id:
            return
        log.LogInfo("Created collection gallery '{}'".format(title))
        totals["collections"] += 1
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
        if photographers:
            update["photographer"] = ", ".join(photographers)
        try:
            client.update_gallery(update)
            totals["collections"] += 1
        except RuntimeError as e:
            log.LogError("  Failed to update collection gallery {}: {}".format(gallery_id, e))

    # Attach every member post's images (images can live in multiple galleries).
    image_ids = []
    for pid in coll["post_ids"]:
        directory = db.post_directory(pid)
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

    data_path = get_setting(config, "dataPath", "")
    parent_studio_name = get_setting(config, "parentStudioName", DEFAULT_PARENT_STUDIO)
    try:
        max_title_length = int(get_setting(config, "maxTitleLength", DEFAULT_MAX_TITLE_LENGTH))
    except (TypeError, ValueError):
        max_title_length = DEFAULT_MAX_TITLE_LENGTH
    auto_create = bool(get_setting(config, "autoCreatePerformers", False))
    auto_tag_from_text = bool(get_setting(config, "autoTagFromText", False))
    crew_tag_id = get_setting(config, "crewTagId", "")
    sync_collections = bool(get_setting(config, "syncCollections", True))

    if not data_path:
        msg = "No data path configured. Set 'Patreon Data Path' in the plugin settings."
        log.LogError(msg)
        return msg

    tag_only = mode == "tag"
    crew_only = mode == "crew"
    log.LogInfo("Starting Patreon {} pass. Data path: {}".format(mode, data_path))

    parent_studio_id = None
    if not tag_only and not crew_only:
        parent_studio_id = client.find_studio(parent_studio_name)
        if not parent_studio_id:
            msg = (
                "Parent studio '{}' not found in Stash. Create it (or fix the "
                "Parent Studio Name setting) and retry.".format(parent_studio_name)
            )
            log.LogError(msg)
            return msg

    databases = PatreonDatabase.find_databases(data_path)
    log.LogInfo("Found {} user_data.db file(s)".format(len(databases)))
    if not databases:
        log.LogWarning("No user_data.db files found under {}".format(data_path))
        return

    processor = MediaProcessor(max_title_length)
    studios = StudioResolver(client, parent_studio_id, load_icon(server))
    # The crew pass is surgical maintenance and must never create performers.
    performers = PerformerResolver(client, auto_create and not crew_only, crew_tag_id)
    tags = TagResolver(client)
    tag_matcher = None
    if tag_only or auto_tag_from_text:
        tag_matcher = TagTextMatcher(client)
        log.LogInfo(
            "Auto-tagging from post text enabled ({} tags loaded)".format(len(tag_matcher.matchers))
        )
    totals = {"galleries": 0, "images": 0, "collections": 0, "skipped": 0}

    for index, db_path in enumerate(databases):
        log.LogProgress(index / len(databases))
        try:
            db = PatreonDatabase(db_path)
        except Exception as e:
            log.LogError("Could not open {}: {}".format(db_path, e))
            continue
        try:
            username_map = {p["user_id"]: p["username"] for p in db.profiles()}
            posts = db.posts()
            log.LogInfo("  {} post(s) in {}".format(len(posts), os.path.basename(db_path)))
            for post_row in posts:
                sync_post(
                    client, db, post_row, username_map, processor, studios,
                    performers, tags, tag_matcher, mode, totals,
                )
            if not tag_only and not crew_only and sync_collections:
                collections = db.collections()
                if collections:
                    log.LogInfo("  {} collection(s)".format(len(collections)))
                for coll in collections:
                    sync_collection(
                        client, db, coll, username_map, processor, studios,
                        performers, tags, totals,
                    )
        except Exception as e:
            log.LogError("Error processing {}: {}".format(db_path, e))
        finally:
            db.close()

    log.LogProgress(1.0)
    summary = (
        "{} complete. Galleries updated: {}, Images updated: {}, "
        "Collections: {}, Skipped: {}".format(
            mode, totals["galleries"], totals["images"], totals["collections"], totals["skipped"]
        )
    )
    log.LogInfo(summary)


if __name__ == "__main__":
    # Raw plugins return their result as JSON on stdout. A non-empty error is
    # logged by Stash at the error level and marks the task as failed.
    error = main()
    print(json.dumps({"error": error} if error else {"output": "ok"}))
