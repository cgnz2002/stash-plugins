"""Entry point for the OnlyFans Metadata Sync Stash plugin.

Stash runs this as an external 'raw' plugin task: it sends a JSON payload on
stdin (server connection + task args) and reads task output from stdout. We log
progress and messages to the Stash log viewer via stderr (see log.py).
"""

import base64
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import log
from stash import StashClient
from source_database import SourceDatabase
import sources
from media import MediaProcessor, compile_name_pattern

# Plugin id == the manifest filename without extension.
PLUGIN_ID = "of-stash-sync"

DEFAULT_MAX_TITLE_LENGTH = 65
# The site tag, per-creator studio naming, post-URL rule, paid rule and data-path
# setting all live on a SourceProfile (sources.py), picked per database from its
# schema_flags source value -- that is what lets one sync serve several sites.

# Stash is SQLite-backed and SQLite has a single writer, so parallel writes
# don't actually commit concurrently -- they serialise on the DB write lock.
# A little concurrency hides per-request latency, but too much just piles up
# transactions until requests time out (and starves the rest of Stash). 2 is a
# safe default; raise it only if your box handles it, drop to 1 if you see
# "database is locked" / "timed out" in the log.
DEFAULT_WORKERS = 2


def _run_write_task(fn):
    """Run a single write task (a callable returning a counters dict). Retries a
    few times on transient errors. Stash is SQLite-backed, so parallel writes can
    briefly hit 'database is locked' AND intermittent 'FOREIGN KEY constraint
    failed' on the join tables (performers_scenes etc.) when many concurrent
    transactions reference the same row -- these are contention races, not real
    data errors, and succeed once the contention clears. Never raises: logs and
    returns {} on failure so one bad write doesn't sink the batch."""
    transient_markers = (
        "lock", "timeout", "timed out", "connection", "busy", "cancelled",
        "foreign key", "constraint",  # concurrent-write races on join tables
    )
    for attempt in range(5):
        try:
            return fn() or {}
        except Exception as e:  # never let a worker thread crash the batch
            msg = str(e).lower()
            transient = any(m in msg for m in transient_markers)
            if attempt < 4 and transient:
                # Exponential back-off (capped): a lock/FK race clears once
                # threads desync, and a request that timed out under contention
                # needs the write queue to drain -- a longer pause gives Stash's
                # single SQLite writer room rather than piling straight back on.
                time.sleep(min(0.5 * (2 ** attempt), 8))
                continue
            log.LogError("  write failed: {}".format(e))
            return {}


def _media_task(client, kind, update):
    """A write task that applies one scene/image update. All resolution is
    already baked into `update`, so this only performs the mutation."""
    def task():
        if kind == "scene":
            client.update_scene(update)
            return {"scenes": 1}
        client.update_image(update)
        return {"images": 1}
    return task


def run_writes(tasks, workers, totals):
    """Execute write tasks, in parallel when workers > 1, and fold their counter
    dicts into `totals`. Resolution/lookups must already be done (tasks only
    perform Stash mutations) so there are no shared-cache races."""
    if not tasks:
        return
    if workers and workers > 1 and len(tasks) > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_run_write_task, tasks))
    else:
        results = [_run_write_task(t) for t in tasks]
    for r in results:
        for key, value in (r or {}).items():
            totals[key] = totals.get(key, 0) + value


def get_setting(config, key, default):
    value = config.get(key)
    if value is None or value == "":
        return default
    return value


def parse_title_exclusions(raw):
    """Parse the Title Exclusions setting into a list of pattern strings.

    The UI editor stores a JSON array (one pattern per row); a value hand-typed
    into the raw settings field is accepted too, split on newlines. Blank entries
    are dropped. Each surviving entry is compiled as a case-insensitive regex by
    MediaProcessor (a literal phrase like 'new collab:' is itself a valid regex)."""
    if raw is None or raw == "":
        return []
    # Stash may hand back the stored value as a native JSON array (list) or as
    # the JSON string the UI editor writes; a value typed by hand into the
    # settings field arrives as a plain string.
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw if str(x).strip()]
    text = str(raw).strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [str(x) for x in data if str(x).strip()]
        if isinstance(data, str) and data.strip():
            return [data.strip()]
    except (ValueError, TypeError):
        pass
    return [line.strip() for line in text.splitlines() if line.strip()]


class PerformerResolver:
    """Find performers by name/alias, optionally creating missing ones."""

    def __init__(self, client, auto_create, crew_tag_id=""):
        self.client = client
        self.auto_create = auto_create
        # Matched by tag id (stable) rather than name, so renaming the tag in
        # Stash doesn't silently disable crew handling. Empty disables it.
        self.crew_tag_id = str(crew_tag_id or "").strip()
        self.cache = {}
        # The site currently being processed; only used for the URL put on a
        # performer this resolver creates. Set per database by process_profile,
        # since databases are processed one at a time.
        self.source = sources.ONLYFANS
        # username -> {"roles": set(), "name": str} for the crew-credit logic
        self.info_cache = {}
        # lowercase name/alias -> [performers]; built once from a single bulk
        # fetch so the common "performer exists" path needs no per-username query.
        self._index = None

    def _ensure_index(self):
        if self._index is not None:
            return
        self._index = {}
        for performer in self.client.find_all_performers():
            keys = [performer.get("name") or ""] + (performer.get("alias_list") or [])
            for key in keys:
                key = key.strip().lower()
                if key:
                    self._index.setdefault(key, []).append(performer)

    def _exact(self, username):
        """Performers whose name or an alias equals the username (from the index)."""
        self._ensure_index()
        out, seen = [], set()
        for performer in self._index.get(username.strip().lower(), []):
            if performer["id"] not in seen:
                seen.add(performer["id"])
                out.append(performer)
        return out

    def _register(self, performer):
        """Add a newly created performer to the index so later lookups find it."""
        if self._index is None:
            return
        keys = [performer.get("name") or ""] + (performer.get("alias_list") or [])
        for key in keys:
            key = key.strip().lower()
            if key:
                self._index.setdefault(key, []).append(performer)

    def creator_credit(self, username):
        """Return (roles, name) for a creator: its crew roles (empty, or both
        'director' and 'photographer' when the matched performer carries the crew
        tag) and its display name. resolve() must have been called first.
        """
        info = self.info_cache.get(username.lower())
        if not info:
            return set(), None
        return info["roles"], info["name"]

    def resolve(self, username, from_mention=False, source=None):
        """Performer ids for a username, creating one if allowed.

        ``source`` is the site the *credit* came from, used only for the URL of
        a performer this call creates. Pass it when the credit was a profile
        link, since a post can link a collaborator on a different site than the
        post's own; it defaults to the site being synced.
        """
        key = username.lower()
        if key in self.cache:
            return self.cache[key]
        # Exact match from the in-memory index (no query). Only the auto-create
        # path below falls back to a live query for the near-match logic.
        exact = self._exact(username)
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
            # of trying to create a duplicate (which Stash would reject). This
            # near-match uses Stash's real LIKE semantics, so only this rare
            # create path falls back to a live query.
            near = self.client.find_performers_by_name(username)["name_like"]
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
                # The URL follows the site the credit came from, not the site
                # being synced: a post can credit a collaborator with a link to
                # another site, and pointing that performer at the wrong one
                # would give them a profile URL that doesn't exist.
                site = source or self.source
                new_id = self.client.create_performer(
                    username, site.profile_url(username)
                )
                if new_id:
                    ids = [new_id]
                    self._register({"id": new_id, "name": username, "alias_list": [], "tags": []})
                    origin = " (from @mention)" if from_mention else ""
                    log.LogInfo(
                        "Created performer '{}' [{}]{}".format(
                            username, site.label, origin
                        )
                    )
        self.cache[key] = ids
        return ids


class StudioResolver:
    def __init__(self, client):
        self.client = client
        # Keyed by (site, creator): the parent studio, name suffix and icon all
        # come from the source profile, so one resolver serves every site.
        self.cache = {}
        self._map = None  # lowercase studio name/alias -> id, built once
        self._no_image = set()  # studio ids Stash reports as having no image

    def _ensure_map(self):
        if self._map is not None:
            return
        self._map = {}
        # Key by name AND alias (mirroring TagResolver/PerformerResolver), so a
        # per-creator studio whose "<username> (OnlyFans)" name already exists as
        # another studio's alias resolves to it instead of hitting Stash's
        # name-already-exists error on create (Stash enforces studio uniqueness
        # across names and aliases).
        for studio in self.client.find_all_studios():
            names = [studio.get("name") or ""] + (studio.get("aliases") or [])
            for candidate in names:
                key = (candidate or "").strip().lower()
                if key and key not in self._map:
                    self._map[key] = studio["id"]
            # Stash appends "&default=true" to image_path when a studio has no
            # image of its own, so this set is exactly the studios whose logo can
            # be filled in without overwriting one somebody chose.
            if "default=true" in (studio.get("image_path") or ""):
                self._no_image.add(studio["id"])

    def resolve(self, username, source):
        # Cached per (site, creator): the same username can exist on two sites,
        # and each gets its own studio.
        cache_key = (source.key, username)
        if cache_key in self.cache:
            return self.cache[cache_key]
        self._ensure_map()
        name = source.studio_name(username)
        studio_id = self._map.get(name.strip().lower())
        if not studio_id:
            studio_id = self.client.create_studio(
                name,
                source.parent_id,
                source.profile_url(username),
                "Sub Studio for {} content creator".format(source.label),
                source.icon,
            )
            if studio_id:
                self._map[name.strip().lower()] = studio_id
            log.LogInfo("Created studio '{}'".format(name))
        elif source.icon and studio_id in self._no_image:
            # Back-fill the logo onto a studio created before the plugin shipped
            # an icon for this site: Stash only accepts a studio image on create,
            # so it would otherwise stay blank forever. Guarded by _no_image, so a
            # studio with any image of its own is never overwritten.
            self._no_image.discard(studio_id)
            try:
                self.client.update_studio({"id": studio_id, "image": source.icon})
                log.LogInfo("Added logo to existing studio '{}'".format(name))
            except RuntimeError as e:
                log.LogWarning("Could not set logo on studio '{}': {}".format(name, e))
        self.cache[cache_key] = studio_id
        return studio_id


class TagResolver:
    """Resolve (and create if missing) plugin tags: the OnlyFans site tag and the
    paid/archived status tags.

    Matching respects tag ALIASES, mirroring how performers are resolved. If the
    name we want already exists as another tag's alias (e.g. 'archived' set as an
    alias on some tag), we reuse that tag instead of trying to create it -- Stash
    rejects a tagCreate whose name collides with an existing name *or* alias, so
    a name-only lookup would keep hitting that error. Built once from a single
    bulk fetch."""

    def __init__(self, client):
        self.client = client
        self.cache = {}
        self._index = None  # lowercase name/alias -> tag id

    def _ensure_index(self):
        if self._index is not None:
            return
        self._index = {}
        for tag in self.client.find_all_tags():
            names = [tag["name"]] + (tag.get("aliases") or [])
            for candidate in names:
                key = (candidate or "").strip().lower()
                # Stash enforces global uniqueness across names and aliases, so a
                # key maps to one tag; the guard just avoids needless overwrites.
                if key and key not in self._index:
                    self._index[key] = tag["id"]

    def resolve(self, name):
        key = (name or "").strip().lower()
        if not key:
            return None
        if key in self.cache:
            return self.cache[key]
        self._ensure_index()
        tag_id = self._index.get(key)
        if not tag_id:
            tag_id = self.client.create_tag(name)
            if tag_id:
                log.LogInfo("Created tag '{}'".format(name))
                self._index[key] = tag_id
        self.cache[key] = tag_id
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


def load_icon(server_connection, filename):
    plugin_dir = server_connection.get("PluginDir")
    if not plugin_dir:
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
    icon_path = os.path.join(plugin_dir, filename)
    if os.path.isfile(icon_path):
        with open(icon_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
        return "data:image/png;base64,{}".format(encoded)
    return None


def collect_tag_ids(processor, meta, text, tags, tag_matcher, source, db, post_id):
    """Tag ids implied by a post: the site tag (always), plus paid/archived, any
    hashtags the post carries, and text matches. Applied to every synced scene,
    image and gallery; the surgical crew pass never calls this, so it stays
    tag-free.

    What counts as "paid" is site-specific (see SourceProfile.is_paid): OnlyFans
    has a real per-post price, JustFor.Fans has only a Free/Paid tier.

    Hashtags come from the source database when it records them (jff-scraper
    does; OF-Scraper doesn't, and hashtags() is simply empty there). Unlike the
    fuzzy text matches -- which only ever attach tags that already exist -- these
    are deliberate creator metadata, so they are created when missing.
    """
    tag_ids = []
    if source.site_tag:
        tag_id = tags.resolve(source.site_tag)
        if tag_id:
            tag_ids.append(tag_id)
    if source.is_paid(db, post_id, meta):
        tag_id = tags.resolve("paid")
        if tag_id:
            tag_ids.append(tag_id)
    if meta and meta["archived"]:
        tag_id = tags.resolve("archived")
        if tag_id:
            tag_ids.append(tag_id)
    if db.is_pinned(post_id):
        tag_id = tags.resolve("pinned")
        if tag_id and tag_id not in tag_ids:
            tag_ids.append(tag_id)
    for hashtag in db.hashtags(post_id):
        tag_id = tags.resolve(hashtag)
        if tag_id and tag_id not in tag_ids:
            tag_ids.append(tag_id)
    if tag_matcher is not None and text:
        for tag_id in tag_matcher.match(processor.remove_html_tags(text)):
            if tag_id not in tag_ids:
                tag_ids.append(tag_id)
    return tag_ids


def build_tag_only_update(db, processor, media_row, tags, tag_matcher, source,
                          existing_tag_ids):
    """Return an update that only adds tags, or None if nothing new to add.

    Leaves every other field untouched (Stash only changes fields that are
    sent), so manual edits are preserved.
    """
    meta = db.post_meta(media_row["post_id"])
    text = meta["text"] if (meta and meta["text"]) else ""
    new_tags = collect_tag_ids(processor, meta, text, tags, tag_matcher, source, db,
                               media_row["post_id"])

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
        for mention, domain in processor.parse_mentions(text):
            # A credit given as a profile link names its own site, which may not
            # be the site the post came from; a bare @mention names none, and
            # resolve() then falls back to the site being synced.
            ids = resolver.resolve(
                mention, from_mention=True,
                source=sources.profile_for_domain(domain),
            )
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
                 resolver, tags, tag_matcher, kind, creator_roles, creator_name,
                 source,
                 existing_performer_ids=None, existing_tag_ids=None,
                 keep_manual_edits=False):
    username = profile["username"]
    post_id = media_row["post_id"]
    filename = media_row["filename"]
    date = processor.format_date(media_row["posted_at"])

    meta = db.post_meta(post_id)
    text = meta["text"] if (meta and meta["text"]) else ""

    director_names, photographer_names, crew_ids, mention_performer_ids = collect_crew(
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
    # Non-destructive mode: keep any performers already on the media (e.g. ones
    # you added by hand) instead of replacing the list. Crew-tagged people are
    # still pulled out (crew_ids), so the crew feature keeps working.
    if keep_manual_edits and existing_performer_ids:
        for pid in existing_performer_ids:
            if pid not in performer_ids:
                performer_ids.append(pid)
        performer_ids = [pid for pid in performer_ids if pid not in crew_ids]
    if not performer_ids:
        performer_ids = list(creator_ids)

    tag_ids = collect_tag_ids(processor, meta, text, tags, tag_matcher, source, db,
                              post_id)
    # Non-destructive mode: keep any tags already on the media (manual tags)
    # instead of replacing the list; the post's tags are added alongside.
    if keep_manual_edits and existing_tag_ids:
        for tid in existing_tag_ids:
            if tid not in tag_ids:
                tag_ids.append(tid)

    update = {
        "title": title,
        "code": source.media_code(processor, media_row, post_id),
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
    # How a post URL is built is site-specific: OnlyFans rebuilds it from the
    # post id, JustFor.Fans has to read the captured link (its URLs carry an
    # encoded key). Either may decline to produce one -- OnlyFans skips
    # profile/avatar assets, whose hash ids would give a junk URL.
    post_url = source.post_url(db, post_id, username)
    if post_url:
        update["urls"] = [post_url]
    return update, title


def _gallery_meta(db, processor, profile, post_id, group, performers, tags,
                  tag_matcher, studio_id, creator_ids, creator_roles, creator_name,
                  url, scene_ids, source):
    """Build a gallery input for one post, from the same post text/date/studio/
    performers/tags used for its scenes and images. Crew are credited in the
    gallery photographer field (galleries have no director)."""
    username = profile["username"]
    date = processor.format_date(group["posted_at"])
    meta = db.post_meta(post_id)
    text = meta["text"] if (meta and meta["text"]) else ""

    if text:
        title, details = processor.process_text(text)
    else:
        api_type = group.get("api_type")
        title = "{}: {}".format(api_type, date) if api_type else date
        details = ""

    # On galleries, crew are KEPT as linked performers. Stash's director/
    # photographer fields are free text with no link back to a performer, so
    # browsing a director's/photographer's work is hard; a gallery groups a whole
    # post, so it's the natural place to carry that link. (Scenes and images still
    # move crew out of the performers list into the director/photographer field --
    # see build_update / build_crew_only_update.) So a gallery's performers are
    # everyone credited: the creator plus every @mentioned account, crew or not.
    performer_ids = list(creator_ids)
    if text:
        for mention, domain in processor.parse_mentions(text):
            for pid in performers.resolve(
                mention, from_mention=True,
                source=sources.profile_for_domain(domain),
            ):
                if pid not in performer_ids:
                    performer_ids.append(pid)
    if not performer_ids:
        performer_ids = list(creator_ids)

    gallery_input = {
        "title": title,
        # The post id, stamped on the gallery so it can be correlated straight
        # back to its post. An OnlyFans link embeds the id, but a JustFor.Fans
        # one carries an encoded key instead, so it cannot be recovered from the
        # URL -- the code is the reliable route for both.
        "code": str(post_id),
        "details": details,
        "studio_id": studio_id,
        "performer_ids": performer_ids,
        "tag_ids": collect_tag_ids(processor, meta, text, tags, tag_matcher, source,
                                   db, post_id),
        "urls": [url],
        "organized": True,
        # Crew are linked performers on galleries, so the free-text photographer
        # field is left empty (and any value left by older versions is cleared on
        # a full sync).
        "photographer": "",
    }
    if date:
        gallery_input["date"] = date
    if scene_ids:
        gallery_input["scene_ids"] = scene_ids
    return gallery_input, title


def build_post_galleries(client, db, profile, processor, performers, tags,
                         tag_matcher, studio_id, creator_ids, creator_roles,
                         creator_name, full_sync, keep_manual_edits, workers,
                         all_scenes, all_images, totals, source):
    """Group a creator's media by post and make one gallery per post.

    A gallery is created when a post has 2+ images, or an image alongside a video
    (Stash relates scenes to galleries, not to images, so the gallery carries the
    scene link). Galleries are keyed by the post URL: a plain sync creates missing
    ones and adds images; a full sync also refreshes their metadata.

    ``all_scenes``/``all_images`` are the creator's media already fetched by
    process_profile (organized included), reused here instead of re-querying.
    """
    user_id = profile["user_id"]

    # filename -> (kind, stash id), organized media included, so a gallery holds
    # all of a post's media regardless of the sync/full mode.
    index = {}
    for scene in all_scenes:
        for f in scene.get("files") or []:
            index[os.path.basename(f["path"])] = ("scene", scene["id"])
    for image in all_images:
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
                by_url[u] = gal

    username = profile["username"]
    # Resolve everything sequentially (no cache races), collecting one write task
    # per gallery; the tasks (create/update + attach images) run in parallel.
    tasks = []
    for post_id, group in groups.items():
        images, scenes = group["images"], group["scenes"]
        # 2+ images, or an image alongside a video.
        if not (len(images) >= 2 or (images and scenes)):
            continue
        url = source.post_url(db, post_id, username)
        if not url:
            # No usable link for this post (e.g. an OnlyFans profile/avatar asset
            # whose hash id can't form a URL). Galleries are keyed by URL, so skip.
            continue
        existing = by_url.get(url)
        if existing:
            gallery_input = None
            if full_sync:
                gallery_input, _title = _gallery_meta(
                    db, processor, profile, post_id, group, performers, tags,
                    tag_matcher, studio_id, creator_ids, creator_roles,
                    creator_name, url, scenes, source,
                )
                # Non-destructive mode: keep performers and tags already there.
                if keep_manual_edits:
                    merged = list(gallery_input["performer_ids"])
                    for p in existing.get("performers") or []:
                        if p["id"] not in merged:
                            merged.append(p["id"])
                    gallery_input["performer_ids"] = merged
                    merged_tags = list(gallery_input.get("tag_ids") or [])
                    for t in existing.get("tags") or []:
                        if t["id"] not in merged_tags:
                            merged_tags.append(t["id"])
                    gallery_input["tag_ids"] = merged_tags
                gallery_input["id"] = existing["id"]

            def _update_task(gi=gallery_input, gid=existing["id"], imgs=list(images)):
                if gi is not None:
                    client.update_gallery(gi)
                client.add_gallery_images(gid, imgs)
                return {"galleries": 1}
            tasks.append(_update_task)
        else:
            gallery_input, title = _gallery_meta(
                db, processor, profile, post_id, group, performers, tags,
                tag_matcher, studio_id, creator_ids, creator_roles,
                creator_name, url, scenes, source,
            )

            def _create_task(gi=gallery_input, imgs=list(images), t=title,
                             has_scene=bool(scenes)):
                gid = client.create_gallery(gi)
                if not gid:
                    return {}
                client.add_gallery_images(gid, imgs)
                log.LogInfo("Created gallery '{}' ({} image(s){})".format(
                    t, len(imgs), ", linked scene" if has_scene else ""))
                return {"galleries": 1}
            tasks.append(_create_task)

    run_writes(tasks, workers, totals)


def tag_post_galleries(client, db, profile, processor, tags, tag_matcher, workers,
                       totals, source):
    """Additive tag pass over the creator's per-post galleries: merge in the
    OnlyFans (plus paid/archived/text) tags, leaving every other gallery field
    untouched. Used by the tag task, which otherwise doesn't touch galleries.
    """
    username = profile["username"]
    # Find (never create) the creator studio, so the tag task stays surgical.
    studio_id = client.find_studio(source.studio_name(username))
    if not studio_id:
        return
    tasks = []
    # Correlate each gallery back to its post. Galleries carry the post id in
    # `code`, which is the direct route; the url fallback covers galleries made
    # before the code was stamped (an OnlyFans link embeds the post id, a
    # JustFor.Fans one does not, so only the former can be parsed).
    for gal in client.find_galleries_for_studio(studio_id):
        post_id = None
        code = str(gal.get("code") or "").strip()
        if code.isdigit():
            post_id = code
        else:
            for u in gal.get("urls") or []:
                if "onlyfans.com" in u:
                    parts = u.rstrip("/").split("/")
                    if len(parts) >= 2 and parts[-2].isdigit():
                        post_id = parts[-2]
                        break
        meta = db.post_meta(post_id) if post_id else None
        text = meta["text"] if (meta and meta["text"]) else ""
        new_tags = collect_tag_ids(processor, meta, text, tags, tag_matcher, source,
                                   db, post_id)

        existing = [t["id"] for t in gal.get("tags") or []]
        merged = list(existing)
        added = 0
        for tag_id in new_tags:
            if tag_id not in merged:
                merged.append(tag_id)
                added += 1
        if added == 0:
            continue

        def _tag_task(gid=gal["id"], tag_ids=merged):
            client.update_gallery({"id": gid, "tag_ids": tag_ids})
            return {"galleries": 1}
        tasks.append(_tag_task)
    run_writes(tasks, workers, totals)


def folder_gallery_title(username, folder_path, source):
    """A readable title for a scanned image folder, e.g.
    "jake_od OnlyFans Images (Posts/Free)".

    A creator has several media folders (Posts/Free/Images, Posts/Paid/Images,
    Messages/..., Archived/...), so the path segments between the creator's own
    directory and the media folder are kept as a qualifier -- without them every
    one of those galleries would end up with the same title.
    """
    parts = [p for p in str(folder_path or "").replace("\\", "/").split("/") if p]
    if not parts:
        return "{} {} Images".format(username, source.site_tag)
    kind = parts[-1]
    lowered = [p.lower() for p in parts]
    target = username.strip().lower()
    context = []
    if target in lowered:
        # Last occurrence: the creator directory, not a coincidental match higher
        # up the path (e.g. a data root that happens to share the name).
        idx = len(lowered) - 1 - lowered[::-1].index(target)
        context = parts[idx + 1:-1]
    title = "{} {} {}".format(username, source.site_tag, kind)
    if context:
        title += " ({})".format("/".join(context))
    return title


def build_folder_gallery_update(gal, username, folder_path, studio_id,
                                creator_ids, site_tag_id, source):
    """Update for one scanned folder gallery, or None when nothing would change.

    Deliberately ADDITIVE for performers and tags: a folder is not a post, so
    there is no authoritative cast to replace the gallery's with, and anything
    curated by hand should survive. Only the title and studio are asserted.
    """
    update = {"id": gal["id"]}

    title = folder_gallery_title(username, folder_path, source)
    if (gal.get("title") or "") != title:
        update["title"] = title

    if studio_id and ((gal.get("studio") or {}).get("id")) != studio_id:
        update["studio_id"] = studio_id

    existing_performers = [p["id"] for p in gal.get("performers") or []]
    performer_ids = list(existing_performers)
    for pid in creator_ids:
        if pid not in performer_ids:
            performer_ids.append(pid)
    if performer_ids != existing_performers:
        update["performer_ids"] = performer_ids

    existing_tags = [t["id"] for t in gal.get("tags") or []]
    if site_tag_id and site_tag_id not in existing_tags:
        update["tag_ids"] = existing_tags + [site_tag_id]

    if not gal.get("organized"):
        update["organized"] = True

    # Only "id" means the gallery already matches -- skip the write entirely.
    return update if len(update) > 1 else None


def sync_folder_galleries(client, profile, studio_id, creator_ids, tags,
                          full_sync, workers, totals, source):
    """Adopt the galleries Stash generates from scanned image folders.

    Stash makes one gallery per scanned folder of images; unlike the per-post
    galleries this plugin builds, they arrive with no performer, no studio and a
    bare folder name for a title. This gives each of a creator's folder galleries
    their performer, their studio and a readable title.

    Follows the same organized idiom as the rest of the sync: a plain sync only
    touches unorganized folder galleries, a full sync refreshes them all.
    """
    username = profile["username"]
    site_tag_id = tags.resolve(source.site_tag) if tags else None
    try:
        galleries = client.find_folder_galleries(username)
    except RuntimeError as e:
        log.LogWarning(
            "Could not list folder galleries for '{}': {}".format(username, e)
        )
        return

    target = username.strip().lower()
    tasks = []
    for gal in galleries:
        folder_path = (gal.get("folder") or {}).get("path") or ""
        segments = [p.lower() for p in folder_path.replace("\\", "/").split("/") if p]
        # The server-side path filter is a substring match, so confirm the
        # creator's name is a whole path segment -- otherwise 'jake' would also
        # claim '/data/jakeson/...'.
        if target not in segments:
            continue
        if not full_sync and gal.get("organized"):
            continue
        update = build_folder_gallery_update(
            gal, username, folder_path, studio_id, creator_ids, site_tag_id, source
        )
        if update:
            tasks.append(_folder_gallery_task(client, update))

    if tasks:
        log.LogInfo("  {} folder gallery/galleries to update".format(len(tasks)))
    run_writes(tasks, workers, totals)


def _folder_gallery_task(client, update):
    def task():
        client.update_gallery(update)
        return {"galleries": 1}
    return task


def process_profile(client, db, profile, processor, studios, performers, tags,
                    tag_matcher, full_sync, tag_only, crew_only, multiple_ok,
                    skip_multi_file, keep_manual_edits, workers, totals, source):
    user_id = profile["user_id"]
    username = profile["username"]
    log.LogInfo("Processing {} {} (user_id {})".format(
        source.label, username, user_id))

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
            studio_id = studios.resolve(username, source)
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
    # Fetch the creator's media once (organized included) and reuse it for the
    # gallery pass too. The plain sync only *processes* unorganized media, so
    # organized items are skipped when building the update map (but still count
    # toward galleries).
    all_scenes = client.find_scenes(username, True)
    all_images = client.find_images(username, True)
    media_map = {}
    skipped_multi = 0
    for scene in all_scenes:
        if not include_all and scene.get("organized"):
            continue
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
    for image in all_images:
        if not include_all and image.get("organized"):
            continue
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
        "  {} scenes, {} images".format(len(all_scenes), len(all_images))
    )
    if skipped_multi:
        totals["skipped_multifile"] += skipped_multi
        log.LogInfo(
            "  Skipped {} multi-file scene(s)/image(s)".format(skipped_multi)
        )

    # Resolve everything sequentially (no cache races), collecting one write task
    # per media; the update mutations then run in parallel.
    media_tasks = []
    for basename, (kind, stash_id, existing_tags, existing_perf, existing_credit) in media_map.items():
        media_row = db.media_by_filename(user_id, basename)
        if not media_row:
            totals["skipped"] += 1
            continue

        if tag_only:
            update, added = build_tag_only_update(
                db, processor, media_row, tags, tag_matcher, source, existing_tags
            )
            if update is None:
                totals["skipped"] += 1
                continue
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
                source, existing_perf, existing_tags, keep_manual_edits,
            )
        update["id"] = stash_id
        media_tasks.append(_media_task(client, kind, update))
    run_writes(media_tasks, workers, totals)

    # Galleries: the sync/full passes build (and metadata-sync) per-post
    # galleries; the tag pass additively tags the existing ones; the crew pass
    # leaves galleries alone.
    if tag_only:
        tag_post_galleries(client, db, profile, processor, tags, tag_matcher,
                           workers, totals, source)
    elif not crew_only:
        build_post_galleries(
            client, db, profile, processor, performers, tags, tag_matcher,
            studio_id, performer_ids, creator_roles, creator_name, full_sync,
            keep_manual_edits, workers, all_scenes, all_images, totals, source,
        )
        # Stash's own folder galleries (one per scanned image folder) arrive with
        # no performer, studio or real title -- adopt them too.
        sync_folder_galleries(
            client, profile, studio_id, performer_ids, tags, full_sync,
            workers, totals, source,
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
    # 'performer' is a full re-sync scoped to a single Stash performer (triggered
    # from a performer's page). It behaves like a full sync but only for the
    # profile(s) whose OF username matches that performer's name/aliases.
    performer_scope = mode == "performer"
    full_sync = mode == "full" or performer_scope
    tag_only = mode == "tag"
    crew_only = mode == "crew"

    client = StashClient(server)
    # Adopt the non-expiring API key up front. A full sync can run long enough to
    # outlive Stash's session cookie, which then 401s every remaining request
    # (whole creators fail near the end of the run). The API key avoids that; if
    # none is configured we fall back to the cookie and just warn.
    try:
        if client.use_api_key():
            log.LogInfo("Using Stash API key for authentication (survives long runs).")
        else:
            log.LogWarning(
                "No Stash API key configured; using the session cookie. On a very "
                "large Full Sync the cookie can expire mid-run and cause HTTP 401 "
                "errors. Set an API key in Stash Settings > Security to avoid this."
            )
    except RuntimeError:
        pass  # non-fatal: keep using the session cookie
    try:
        config = client.get_plugin_config(PLUGIN_ID)
    except RuntimeError as e:
        msg = "Could not read plugin settings: {}".format(e)
        log.LogError(msg)
        return msg

    # Each site has its own data path and parent studio; everything else is
    # shared. A site with no path configured is simply not scanned, so an
    # OnlyFans-only setup behaves exactly as it did before other sites existed.
    configured_sources = []
    for source in sources.ALL_PROFILES:
        path = str(get_setting(config, source.path_setting, "") or "").strip()
        if not path:
            continue
        source.parent_default_name = get_setting(
            config, source.parent_setting, source.parent_default
        )
        configured_sources.append((source, path))
    try:
        max_title_length = int(get_setting(config, "maxTitleLength", DEFAULT_MAX_TITLE_LENGTH))
    except (TypeError, ValueError):
        max_title_length = DEFAULT_MAX_TITLE_LENGTH
    multiple_ok = bool(get_setting(config, "multiplePerformersOk", False))
    auto_create = bool(get_setting(config, "autoCreatePerformers", False))
    auto_tag_from_text = bool(get_setting(config, "autoTagFromText", False))
    skip_multi_file = bool(get_setting(config, "skipMultiFile", False))
    crew_tag_id = get_setting(config, "crewTagId", "")
    keep_manual_edits = bool(get_setting(config, "keepManualEdits", False))
    title_exclusions = parse_title_exclusions(get_setting(config, "titleExclusions", ""))
    try:
        workers = int(get_setting(config, "syncWorkers", DEFAULT_WORKERS))
    except (TypeError, ValueError):
        workers = DEFAULT_WORKERS
    workers = max(1, min(workers, 16))  # clamp: 1 = sequential, cap concurrency

    if not configured_sources:
        msg = (
            "No data path configured. Set at least one of {} in the plugin "
            "settings.".format(
                " / ".join("'{}'".format(s.path_setting) for s in sources.ALL_PROFILES)
            )
        )
        log.LogError(msg)
        return msg

    # Scoped performer sync: map the Stash performer back to its OF username(s)
    # via name/aliases, so only that creator's profile is processed. Required for
    # 'performer' mode -- with no performerId we do NOT fall back to syncing
    # everyone (that would be the opposite of the intent).
    scoped_usernames = None
    if performer_scope:
        performer_id = args.get("performerId") or args.get("performer_id")
        if not performer_id:
            msg = "No performer specified. Trigger 'Sync Performer' from a performer's page."
            log.LogError(msg)
            return msg
        try:
            performer = client.find_performer(performer_id)
        except RuntimeError as e:
            msg = "Could not look up performer {}: {}".format(performer_id, e)
            log.LogError(msg)
            return msg
        if not performer:
            msg = "Performer id {} not found in Stash.".format(performer_id)
            log.LogError(msg)
            return msg
        names = [performer.get("name") or ""] + (performer.get("alias_list") or [])
        scoped_usernames = {n.strip().lower() for n in names if n and n.strip()}
        log.LogInfo(
            "Scoped sync for performer '{}' (id {}). Matching OF username(s): {}".format(
                performer.get("name"), performer_id,
                ", ".join(sorted(scoped_usernames)) or "(none)")
        )

    pass_name = ("tag-only pass" if tag_only else
                 "crew-credit pass" if crew_only else
                 "scoped performer re-sync" if performer_scope else
                 "{}metadata sync".format("FULL " if full_sync else ""))
    log.LogInfo("Starting {} for: {}".format(
        pass_name,
        ", ".join("{} ({})".format(src.label, path) for src, path in configured_sources)))

    # The parent studio is only needed by the passes that create studios, and it
    # must already exist -- the plugin never creates it.
    if not tag_only and not crew_only:
        for source, _path in configured_sources:
            name = source.parent_default_name
            source.parent_id = client.find_studio(name)
            if not source.parent_id:
                msg = (
                    "Parent studio '{}' not found in Stash. Create it (or fix the "
                    "{} setting) and retry.".format(name, source.parent_setting)
                )
                log.LogError(msg)
                return msg
            source.icon = load_icon(server, source.icon_file)

    # Discover every database once, remembering which path found it, so a db is
    # never scanned twice when two sites share a parent directory.
    databases = []
    seen_paths = set()
    for source, path in configured_sources:
        found = SourceDatabase.find_databases(path)
        for db_path in found:
            if db_path in seen_paths:
                continue
            seen_paths.add(db_path)
            databases.append(db_path)
        if not found:
            log.LogWarning("No user_data.db files found under {}".format(path))
    log.LogInfo("Found {} user_data.db file(s){}".format(
        len(databases), "" if workers <= 1 else " ({} parallel writers)".format(workers)))
    if not databases:
        return

    processor = MediaProcessor(max_title_length, title_exclusions)
    if title_exclusions:
        log.LogInfo("Loaded {} title exclusion pattern(s).".format(len(title_exclusions)))
    studios = StudioResolver(client)
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
            db = SourceDatabase(db_path)
        except Exception as e:
            log.LogError("Could not open {}: {}".format(db_path, e))
            continue
        # Which site this database came from is read from the database itself
        # (its schema_flags source), not from a setting -- so a data path holding
        # more than one kind of library sorts itself out.
        source = sources.profile_for_source(db.source())
        # Only used for the URL on a performer this run creates.
        performers.source = source
        try:
            profiles = db.profiles()
            if scoped_usernames is not None:
                profiles = [
                    p for p in profiles
                    if (p["username"] or "").strip().lower() in scoped_usernames
                ]
            for profile in profiles:
                process_profile(
                    client, db, profile, processor, studios, performers, tags,
                    tag_matcher, full_sync, tag_only, crew_only, multiple_ok,
                    skip_multi_file, keep_manual_edits, workers, totals, source,
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
