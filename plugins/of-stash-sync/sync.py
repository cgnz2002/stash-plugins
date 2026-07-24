"""Entry point for the OnlyFans Metadata Sync Stash plugin.

Stash runs this as an external 'raw' plugin task: it sends a JSON payload on
stdin (server connection + task args) and reads task output from stdout. We log
progress and messages to the Stash log viewer via stderr (see log.py).
"""

import base64
import json
import os
import sys

import log
from stash import StashClient
from of_database import OFDatabase
from media import MediaProcessor, compile_name_pattern

# Plugin id == the manifest filename without extension.
PLUGIN_ID = "of-stash-sync"

DEFAULT_PARENT_STUDIO = "OnlyFans (network)"
DEFAULT_MAX_TITLE_LENGTH = 65
# Tag added to every synced scene, image and gallery so all OnlyFans media is
# filterable by one tag. Created on first use if missing.
DEFAULT_SITE_TAG = "OnlyFans"


def get_setting(config, key, default):
    value = config.get(key)
    if value is None or value == "":
        return default
    return value


def of_url(username):
    return "https://www.onlyfans.com/{}".format(username)


class PerformerResolver:
    """Find performers by name/alias, optionally creating missing ones."""

    def __init__(self, client, auto_create, crew_tag_id=""):
        self.client = client
        self.auto_create = auto_create
        # Matched by tag id (stable) rather than name, so renaming the tag in
        # Stash doesn't silently disable crew handling. Empty disables it.
        self.crew_tag_id = str(crew_tag_id or "").strip()
        self.cache = {}
        # username -> {"roles": set(), "name": str} for the crew-credit logic
        self.info_cache = {}

    def creator_credit(self, username):
        """Return (roles, name) for a creator: its crew roles (empty, or both
        'director' and 'photographer' when the matched performer carries the crew
        tag) and its display name. resolve() must have been called first.
        """
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
        # A single crew tag credits the performer to both the scene director and
        # the image photographer field (each applies on its own media type), so
        # record both roles when any matched performer carries the crew tag. The
        # credit uses the performer's display name (never the alias/username);
        # when several performers match, prefer the crew-tagged one's name.
        roles = set()
        credit_name = None
        for p in exact:
            ptag_ids = {t.get("id") for t in (p.get("tags") or [])}
            if self.crew_tag_id and self.crew_tag_id in ptag_ids:
                roles = {"director", "photographer"}
                credit_name = p.get("name")
                break
        if credit_name is None and exact:
            credit_name = exact[0]["name"]
        self.info_cache[key] = {"roles": roles, "name": credit_name}
        if not ids and self.auto_create:
            # Stash treats EQUALS as a SQL LIKE, so a username containing '_'
            # (a wildcard) can collide with an existing performer name on
            # create. If Stash already has such a near-match, attach it instead
            # of trying to create a duplicate (which Stash would reject).
            near = result["name_like"]
            if len(near) == 1:
                ids = [near[0]["id"]]
                log.LogInfo(
                    "Matched existing performer '{}' for '{}'".format(
                        near[0]["name"], username
                    )
                )
            elif len(near) > 1:
                names = ", ".join("'{}'".format(p["name"]) for p in near)
                log.LogWarning(
                    "'{}' matches several existing performers ({}); not creating "
                    "or attaching any. Add the OF username as an alias to the "
                    "correct performer.".format(username, names)
                )
            else:
                new_id = self.client.create_performer(username, of_url(username))
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
        name = "{} (OnlyFans)".format(username)
        studio_id = self.client.find_studio(name)
        if not studio_id:
            studio_id = self.client.create_studio(
                name,
                self.parent_id,
                of_url(username),
                "Sub Studio for OnlyFans content creator",
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
    auto-tagger matches names against file paths. Only existing tags are
    matched (never created), and tags flagged ignore_auto_tag are skipped.
    """

    def __init__(self, client):
        self.matchers = []  # list of (tag_id, [compiled patterns])
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
    icon_path = os.path.join(plugin_dir, "onlyfans.png")
    if os.path.isfile(icon_path):
        with open(icon_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
        return "data:image/png;base64,{}".format(encoded)
    return None


def collect_tag_ids(processor, meta, text, tags, tag_matcher, site_tag=DEFAULT_SITE_TAG):
    """Tag ids implied by a post: the site tag (always), plus paid/archived and
    text matches. Applied to every synced scene, image and gallery; the surgical
    crew pass never calls this, so it stays tag-free."""
    tag_ids = []
    if site_tag:
        tag_id = tags.resolve(site_tag)
        if tag_id:
            tag_ids.append(tag_id)
    if meta:
        price = meta["price"] or 0
        if meta["paid"] and price and int(price) > 0:
            tag_id = tags.resolve("paid")
            if tag_id:
                tag_ids.append(tag_id)
        if meta["archived"]:
            tag_id = tags.resolve("archived")
            if tag_id:
                tag_ids.append(tag_id)
    if tag_matcher is not None and text:
        for tag_id in tag_matcher.match(processor.remove_html_tags(text)):
            if tag_id not in tag_ids:
                tag_ids.append(tag_id)
    return tag_ids


def build_tag_only_update(db, processor, media_row, tags, tag_matcher,
                          existing_tag_ids):
    """Return an update that only adds tags, or None if nothing new to add.

    Leaves every other field untouched (Stash only changes fields that are
    sent), so manual edits are preserved.
    """
    meta = db.post_meta(media_row["post_id"])
    text = meta["text"] if (meta and meta["text"]) else ""
    new_tags = collect_tag_ids(processor, meta, text, tags, tag_matcher)

    merged = list(existing_tag_ids)
    added = 0
    for tag_id in new_tags:
        if tag_id not in merged:
            merged.append(tag_id)
            added += 1
    if added == 0:
        return None, 0
    return {"tag_ids": merged}, added


def collect_crew(processor, resolver, text, creator_roles, creator_name, creator_ids):
    """Split a post's credited people into crew and plain performers.

    Anyone tagged as crew (director/photographer) -- the creator or an @mentioned
    collaborator -- belongs in the director/photographer field, not the
    performers list. Returns (director_names, photographer_names, crew_ids,
    mention_performer_ids), where crew_ids are the performer ids to keep out of
    the performers list and mention_performer_ids are the non-crew @mentions.
    """
    crew = []          # (roles, display name)
    crew_ids = set()
    if creator_roles and creator_name:
        crew.append((creator_roles, creator_name))
        crew_ids.update(creator_ids)

    mention_performer_ids = []
    if text:
        for mention in processor.parse_mentions(text):
            ids = resolver.resolve(mention, from_mention=True)
            m_roles, m_name = resolver.creator_credit(mention)
            if m_roles:
                if m_name:
                    crew.append((m_roles, m_name))
                crew_ids.update(ids)
            else:
                for pid in ids:
                    if pid not in mention_performer_ids:
                        mention_performer_ids.append(pid)

    director_names, photographer_names = [], []
    for roles, name in crew:
        if "director" in roles and name not in director_names:
            director_names.append(name)
        if "photographer" in roles and name not in photographer_names:
            photographer_names.append(name)
    return director_names, photographer_names, crew_ids, mention_performer_ids


def build_crew_only_update(db, processor, media_row, creator_ids, creator_roles,
                           creator_name, resolver, kind, existing_performer_ids,
                           existing_credit):
    """Return an update that only fixes the crew credit: move director/
    photographer-tagged people out of the existing performers list and into the
    director/photographer field. Leaves title, details, date, studio, tags and
    organized untouched. Returns (None, None) when nothing needs to change.
    """
    meta = db.post_meta(media_row["post_id"])
    text = meta["text"] if (meta and meta["text"]) else ""
    director_names, photographer_names, crew_ids, _ = collect_crew(
        processor, resolver, text, creator_roles, creator_name, creator_ids
    )

    # Prune credited crew from the existing performers; never leave it empty.
    new_perf = [pid for pid in existing_performer_ids if pid not in crew_ids]
    if not new_perf:
        new_perf = list(creator_ids)

    credit = None
    if kind == "scene" and director_names:
        credit = ", ".join(director_names)
    elif kind == "image" and photographer_names:
        credit = ", ".join(photographer_names)

    perf_changed = new_perf != list(existing_performer_ids)
    credit_changed = credit is not None and credit != (existing_credit or "")
    if not perf_changed and not credit_changed:
        return None, None

    update = {}
    parts = []
    if perf_changed:
        update["performer_ids"] = new_perf
        parts.append("performers {}->{}".format(len(existing_performer_ids), len(new_perf)))
    if credit_changed:
        field = "director" if kind == "scene" else "photographer"
        update[field] = credit
        parts.append("{}={}".format(field, credit))
    return update, ", ".join(parts)


def build_update(db, processor, profile, media_row, creator_ids, studio_id,
                 resolver, tags, tag_matcher, kind, creator_roles, creator_name):
    username = profile["username"]
    post_id = media_row["post_id"]
    filename = media_row["filename"]
    date = processor.format_date(media_row["posted_at"])

    meta = db.post_meta(post_id)
    text = meta["text"] if (meta and meta["text"]) else ""

    director_names, photographer_names, _crew_ids, mention_performer_ids = collect_crew(
        processor, resolver, text, creator_roles, creator_name, creator_ids
    )

    if text:
        title, details = processor.process_text(text)
    else:
        api_type = media_row["api_type"]
        title = "{}: {}".format(api_type, date) if api_type else date
        details = ""

    # Build the performer list: the creator (unless they are crew) plus any
    # @mentioned performers who aren't crew. If everyone credited turned out to
    # be crew, fall back to the creator so the media is never performer-less.
    performer_ids = [] if creator_roles else list(creator_ids)
    for pid in mention_performer_ids:
        if pid not in performer_ids:
            performer_ids.append(pid)
    if not performer_ids:
        performer_ids = list(creator_ids)

    tag_ids = collect_tag_ids(processor, meta, text, tags, tag_matcher)

    update = {
        "title": title,
        "code": processor.studio_code(filename),
        "date": date,
        "studio_id": studio_id,
        "performer_ids": performer_ids,
        "details": details,
        "tag_ids": tag_ids,
        "organized": True,
    }
    # director exists only on scenes, photographer only on images (verified
    # against the Stash schema), so credit each on the media type that has it.
    if kind == "scene" and director_names:
        update["director"] = ", ".join(director_names)
    elif kind == "image" and photographer_names:
        update["photographer"] = ", ".join(photographer_names)
    # Real posts have a numeric OF post id; profile/avatar/header assets use a
    # hash and would produce a junk URL, so only set the URL for numeric ids.
    if str(post_id).isdigit():
        update["urls"] = [
            "https://www.onlyfans.com/{}/{}".format(post_id, username)
        ]
    return update, title


def _gallery_meta(db, processor, profile, post_id, group, performers, tags,
                  tag_matcher, studio_id, creator_ids, creator_roles, creator_name,
                  url, scene_ids):
    """Build a gallery input for one post, from the same post text/date/studio/
    performers/tags used for its scenes and images. Crew are credited in the
    gallery photographer field (galleries have no director)."""
    username = profile["username"]
    date = processor.format_date(group["posted_at"])
    meta = db.post_meta(post_id)
    text = meta["text"] if (meta and meta["text"]) else ""

    _director, photographer_names, _crew_ids, mention_ids = collect_crew(
        processor, performers, text, creator_roles, creator_name, creator_ids
    )
    if text:
        title, details = processor.process_text(text)
    else:
        api_type = group.get("api_type")
        title = "{}: {}".format(api_type, date) if api_type else date
        details = ""

    performer_ids = [] if creator_roles else list(creator_ids)
    for pid in mention_ids:
        if pid not in performer_ids:
            performer_ids.append(pid)
    if not performer_ids:
        performer_ids = list(creator_ids)

    gallery_input = {
        "title": title,
        "details": details,
        "studio_id": studio_id,
        "performer_ids": performer_ids,
        "tag_ids": collect_tag_ids(processor, meta, text, tags, tag_matcher),
        "urls": [url],
        "organized": True,
    }
    if date:
        gallery_input["date"] = date
    if photographer_names:
        gallery_input["photographer"] = ", ".join(photographer_names)
    if scene_ids:
        gallery_input["scene_ids"] = scene_ids
    return gallery_input, title


def build_post_galleries(client, db, profile, processor, performers, tags,
                         tag_matcher, studio_id, creator_ids, creator_roles,
                         creator_name, full_sync, totals):
    """Group a creator's media by post and make one gallery per post.

    A gallery is created when a post has 2+ images, or an image alongside a video
    (Stash relates scenes to galleries, not to images, so the gallery carries the
    scene link). Galleries are keyed by the post URL: a plain sync creates missing
    ones and adds images; a full sync also refreshes their metadata.
    """
    user_id = profile["user_id"]
    username = profile["username"]

    # filename -> (kind, stash id), organized media included, so a gallery holds
    # all of a post's media regardless of the sync/full mode.
    index = {}
    for scene in client.find_scenes(username, True):
        for f in scene.get("files") or []:
            index[os.path.basename(f["path"])] = ("scene", scene["id"])
    for image in client.find_images(username, True):
        for vf in image.get("visual_files") or []:
            basename = vf.get("basename")
            if basename:
                index[basename] = ("image", image["id"])

    groups = {}
    for row in db.medias_for_model(user_id):
        post_id = row["post_id"]
        if post_id is None:
            continue
        post_id = str(post_id)
        g = groups.setdefault(post_id, {
            "images": [], "scenes": [], "posted_at": row["posted_at"],
            "api_type": row["api_type"],
        })
        entry = index.get(row["filename"])
        if not entry:
            continue
        kind, stash_id = entry
        bucket = g["images"] if kind == "image" else g["scenes"]
        if stash_id not in bucket:
            bucket.append(stash_id)

    # Existing per-post galleries for this creator's studio, keyed by url.
    by_url = {}
    if studio_id:
        for gal in client.find_galleries_for_studio(studio_id):
            for u in gal.get("urls") or []:
                by_url[u] = gal["id"]

    for post_id, group in groups.items():
        images, scenes = group["images"], group["scenes"]
        # 2+ images, or an image alongside a video.
        if not (len(images) >= 2 or (images and scenes)):
            continue
        if not post_id.isdigit():
            continue
        url = "https://www.onlyfans.com/{}/{}".format(post_id, username)
        existing = by_url.get(url)
        try:
            if existing:
                if full_sync:
                    gallery_input, _title = _gallery_meta(
                        db, processor, profile, post_id, group, performers, tags,
                        tag_matcher, studio_id, creator_ids, creator_roles,
                        creator_name, url, scenes,
                    )
                    gallery_input["id"] = existing
                    client.update_gallery(gallery_input)
                client.add_gallery_images(existing, images)
            else:
                gallery_input, title = _gallery_meta(
                    db, processor, profile, post_id, group, performers, tags,
                    tag_matcher, studio_id, creator_ids, creator_roles,
                    creator_name, url, scenes,
                )
                gid = client.create_gallery(gallery_input)
                if not gid:
                    continue
                client.add_gallery_images(gid, images)
                log.LogInfo("Created gallery '{}' ({} image(s){})".format(
                    title, len(images), ", linked scene" if scenes else ""))
            totals["galleries"] += 1
        except RuntimeError as e:
            log.LogError("  Failed gallery for post {}: {}".format(post_id, e))


def process_profile(client, db, profile, processor, studios, performers, tags,
                    tag_matcher, full_sync, tag_only, crew_only, multiple_ok,
                    skip_multi_file, totals):
    user_id = profile["user_id"]
    username = profile["username"]
    log.LogInfo("Processing {} (user_id {})".format(username, user_id))

    studio_id = None
    performer_ids = []
    creator_roles, creator_name = set(), None
    # The tag-only pass needs neither the creator performer nor the studio. The
    # crew pass needs the creator performer (for role/name and the fallback) but
    # not the studio; the sync passes need both.
    if not tag_only:
        performer_ids = performers.resolve(username)
        if not performer_ids:
            log.LogWarning(
                "No performer matches '{}' (enable Create Missing Performers to add "
                "it); skipping creator".format(username)
            )
            return
        if len(performer_ids) > 1 and not multiple_ok:
            log.LogWarning(
                "'{}' matches multiple performers; skipping "
                "(enable Allow Multiple Performer Matches to attach all)".format(username)
            )
            return

        creator_roles, creator_name = performers.creator_credit(username)
        if creator_roles:
            log.LogInfo("  '{}' tagged as crew".format(username))

        if not crew_only:
            studio_id = studios.resolve(username)
            if not studio_id:
                log.LogError("Could not resolve studio for {}; skipping".format(username))
                return

    # Map each Stash media file's basename to (kind, stash id, existing tag ids).
    # We route the update by where the media actually lives in Stash so the id
    # always matches the mutation (scenes -> sceneUpdate, images -> imageUpdate).
    # Tag-only and full passes look at organized media too.
    # The skip-multi-file guard protects merged scenes (multiple files from
    # different OF pages) from having their performers/metadata overwritten. It
    # only applies to the destructive sync tasks, not the additive tag pass.
    skip_multi = skip_multi_file and not tag_only

    include_all = full_sync or tag_only or crew_only
    media_map = {}
    skipped_multi = 0
    scenes = client.find_scenes(username, include_all)
    for scene in scenes:
        files = scene.get("files") or []
        if skip_multi and len(files) > 1:
            skipped_multi += 1
            continue
        entry = (
            "scene", scene["id"],
            [t["id"] for t in scene.get("tags") or []],
            [p["id"] for p in scene.get("performers") or []],
            scene.get("director"),
        )
        for f in files:
            media_map[os.path.basename(f["path"])] = entry
    images = client.find_images(username, include_all)
    for image in images:
        visual_files = image.get("visual_files") or []
        if skip_multi and len(visual_files) > 1:
            skipped_multi += 1
            continue
        entry = (
            "image", image["id"],
            [t["id"] for t in image.get("tags") or []],
            [p["id"] for p in image.get("performers") or []],
            image.get("photographer"),
        )
        for vf in visual_files:
            basename = vf.get("basename")
            if basename:
                media_map[basename] = entry
    log.LogInfo(
        "  {} scenes, {} images to consider".format(len(scenes), len(images))
    )
    if skipped_multi:
        totals["skipped_multifile"] += skipped_multi
        log.LogInfo(
            "  Skipped {} multi-file scene(s)/image(s)".format(skipped_multi)
        )

    for basename, (kind, stash_id, existing_tags, existing_perf, existing_credit) in media_map.items():
        media_row = db.media_by_filename(user_id, basename)
        if not media_row:
            totals["skipped"] += 1
            continue

        if tag_only:
            update, added = build_tag_only_update(
                db, processor, media_row, tags, tag_matcher, existing_tags
            )
            if update is None:
                totals["skipped"] += 1
                continue
            label = "+{} tags".format(added)
        elif crew_only:
            update, label = build_crew_only_update(
                db, processor, media_row, performer_ids, creator_roles,
                creator_name, performers, kind, existing_perf, existing_credit,
            )
            if update is None:
                totals["skipped"] += 1
                continue
        else:
            update, label = build_update(
                db, processor, profile, media_row, performer_ids, studio_id,
                performers, tags, tag_matcher, kind, creator_roles, creator_name,
            )
        update["id"] = stash_id
        try:
            if kind == "scene":
                client.update_scene(update)
                totals["scenes"] += 1
            else:
                client.update_image(update)
                totals["images"] += 1
            log.LogDebug("  {} <- {}: {}".format(kind, username, str(label)[:60]))
        except RuntimeError as e:
            log.LogError("  Failed to update {} {}: {}".format(kind, stash_id, e))

    # Group each post's media into a gallery (and link its scene). Only on the
    # sync/full passes -- the tag and crew passes stay surgical.
    if not tag_only and not crew_only:
        build_post_galleries(
            client, db, profile, processor, performers, tags, tag_matcher,
            studio_id, performer_ids, creator_roles, creator_name, full_sync, totals,
        )


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw else {}
    except ValueError:
        payload = {}

    server = payload.get("server_connection") or {}
    args = payload.get("args") or {}
    mode = args.get("mode")
    full_sync = mode == "full"
    tag_only = mode == "tag"
    crew_only = mode == "crew"

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
    multiple_ok = bool(get_setting(config, "multiplePerformersOk", False))
    auto_create = bool(get_setting(config, "autoCreatePerformers", False))
    auto_tag_from_text = bool(get_setting(config, "autoTagFromText", False))
    skip_multi_file = bool(get_setting(config, "skipMultiFile", False))
    crew_tag_id = get_setting(config, "crewTagId", "")

    if not data_path:
        msg = "No data path configured. Set 'OF-Scraper Data Path' in the plugin settings."
        log.LogError(msg)
        return msg

    if tag_only:
        log.LogInfo("Starting OnlyFans tag-only pass. Data path: {}".format(data_path))
    elif crew_only:
        log.LogInfo("Starting OnlyFans crew-credit pass. Data path: {}".format(data_path))
    else:
        log.LogInfo(
            "Starting OnlyFans {}metadata sync. Data path: {}".format(
                "FULL " if full_sync else "", data_path
            )
        )

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

    databases = OFDatabase.find_databases(data_path)
    log.LogInfo("Found {} user_data.db file(s)".format(len(databases)))
    if not databases:
        log.LogWarning("No user_data.db files found under {}".format(data_path))
        return

    processor = MediaProcessor(max_title_length)
    studios = StudioResolver(client, parent_studio_id, load_icon(server))
    # The crew pass is surgical maintenance: it must never create performers as
    # a side effect of resolving @mentions, even if Create Missing Performers is
    # enabled for the sync tasks.
    performers = PerformerResolver(client, auto_create and not crew_only, crew_tag_id)
    tags = TagResolver(client)
    # The tag-only task always matches tags from text; the regular sync only
    # does so when the setting is enabled.
    tag_matcher = None
    if tag_only or auto_tag_from_text:
        tag_matcher = TagTextMatcher(client)
        log.LogInfo(
            "Auto-tagging from post text enabled ({} tags loaded)".format(
                len(tag_matcher.matchers)
            )
        )
    totals = {"scenes": 0, "images": 0, "galleries": 0, "skipped": 0, "skipped_multifile": 0}

    for index, db_path in enumerate(databases):
        log.LogProgress(index / len(databases))
        try:
            db = OFDatabase(db_path)
        except Exception as e:
            log.LogError("Could not open {}: {}".format(db_path, e))
            continue
        try:
            profiles = db.profiles()
            for profile in profiles:
                process_profile(
                    client, db, profile, processor, studios, performers, tags,
                    tag_matcher, full_sync, tag_only, crew_only, multiple_ok,
                    skip_multi_file, totals,
                )
        except Exception as e:
            log.LogError("Error processing {}: {}".format(db_path, e))
        finally:
            db.close()

    log.LogProgress(1.0)
    verb = "Tagging" if tag_only else ("Crew update" if crew_only else "Sync")
    summary = "{} complete. Scenes updated: {}, Images updated: {}, Skipped: {}".format(
        verb, totals["scenes"], totals["images"], totals["skipped"]
    )
    if totals["galleries"]:
        summary += ", Galleries: {}".format(totals["galleries"])
    if totals["skipped_multifile"]:
        summary += ", Skipped multi-file: {}".format(totals["skipped_multifile"])
    log.LogInfo(summary)


if __name__ == "__main__":
    # Raw plugins return their result as JSON on stdout. A non-empty error is
    # logged by Stash at the error level and marks the task as failed.
    error = main()
    print(json.dumps({"error": error} if error else {"output": "ok"}))
