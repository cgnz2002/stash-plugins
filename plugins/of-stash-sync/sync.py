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

# Put on media whose credits include a Sponsor-tagged account. Resolved through
# TagResolver like 'paid'/'archived'/'pinned', so an existing tag carrying this
# as a name OR an alias is reused rather than colliding on create.
SPONSORED_TAG = "sponsored"

# Another plugin in this repo, comic-reader, marks comics with a tag tree: a
# parent "Comic" tag whose children ("Webtoon" on galleries, "Comic Page" on
# the images inside one) say what a piece of media IS. It keeps the parent's id
# in its own plugin settings, so the id is discovered rather than guessed --
# nothing here depends on those tags' names, which the user can change.
COMIC_PLUGIN_ID = "comic-reader"
COMIC_TAG_SETTING = "comicTagId"

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

    def __init__(self, client, auto_create, crew_tag_id="", sponsor_tag_id=""):
        self.client = client
        self.auto_create = auto_create
        # Matched by tag id (stable) rather than name, so renaming the tag in
        # Stash doesn't silently disable crew handling. Empty disables it.
        self.crew_tag_id = str(crew_tag_id or "").strip()
        # Same idea for sponsors: a performer carrying this tag is a brand/
        # advertiser, not someone in the media, so they are dropped from the
        # performers list and the media is tagged 'sponsored' instead.
        self.sponsor_tag_id = str(sponsor_tag_id or "").strip()
        self.cache = {}
        # The site currently being processed; only used for the URL put on a
        # performer this resolver creates. Set per database by process_profile,
        # since databases are processed one at a time.
        self.source = sources.ONLYFANS
        # username -> {"roles": set(), "name": str, "sponsor": bool} for the
        # crew-credit and sponsor logic
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

    def is_sponsor(self, username):
        """Whether a credited account carries the Sponsor Tag. resolve() must
        have been called first (same contract as creator_credit)."""
        info = self.info_cache.get(username.lower())
        return bool(info and info.get("sponsor"))

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
        # The sponsor flag is read in the same pass. Every match is inspected
        # (rather than stopping at the first crew hit) so a performer can be
        # both -- crew still takes the director/photographer credit, and the
        # sponsored tag is applied alongside.
        roles = set()
        credit_name = None
        sponsor = False
        for p in exact:
            ptag_ids = {t.get("id") for t in (p.get("tags") or [])}
            if self.sponsor_tag_id and self.sponsor_tag_id in ptag_ids:
                sponsor = True
            if self.crew_tag_id and self.crew_tag_id in ptag_ids and not roles:
                roles = {"director", "photographer"}
                credit_name = p.get("name")
        if credit_name is None and exact:
            credit_name = exact[0]["name"]
        self.info_cache[key] = {
            "roles": roles, "name": credit_name, "sponsor": sponsor,
        }
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


class ProtectedTags:
    """Tags another plugin owns, which this sync must never strip.

    A sync/full pass REPLACES a media's `tag_ids` with the post's derived tags
    (that is the point -- it is how a re-sync corrects stale tags). The
    `keepManualEdits` setting turns that into a merge, but it is off by default
    and it protects the user's *manual* tags, which is a different question from
    another plugin's bookkeeping. Where the two plugins' libraries overlap, a
    Full Sync was quietly deleting comic-reader's marks and, with them, whether
    Stash still knew a gallery was a comic.

    So these ids are kept regardless of `keepManualEdits`: they are not a
    preference, they are someone else's data. The set is the configured Comic
    tag plus every descendant, resolved once per run -- a tag tree can grow new
    children (a new comic format) without this needing to know their names.

    Protecting nothing is the correct behaviour when comic-reader is absent or
    unconfigured, and is what an empty set does: `keep()` returns nothing and
    every call site is left exactly as it was.
    """

    def __init__(self, client):
        self.ids = frozenset()
        try:
            config = client.get_plugin_config(COMIC_PLUGIN_ID)
        except RuntimeError as e:
            log.LogWarning("Could not read {} settings: {}".format(
                COMIC_PLUGIN_ID, e))
            return
        root = str(get_setting(config, COMIC_TAG_SETTING, "") or "").strip()
        if not root:
            return

        ids = {root}
        try:
            ids.update(str(t) for t in client.find_tag_descendants(root))
        except RuntimeError as e:
            # Keep the root rather than nothing: a failed descendant lookup is
            # a reason to protect less precisely, never a reason to resume
            # deleting the tag we already know about.
            log.LogWarning(
                "Could not expand the Comic tag tree ({}); protecting only the "
                "parent tag".format(e))
        self.ids = frozenset(ids)
        log.LogInfo("Protecting {} comic tag(s) from being replaced".format(
            len(self.ids)))

    def keep(self, existing_tag_ids):
        """The protected ids among the tags a media already carries."""
        if not self.ids or not existing_tag_ids:
            return []
        return [tid for tid in existing_tag_ids if tid in self.ids]

    def merge_into(self, tag_ids, existing_tag_ids):
        """Append any protected tag the media already had, in place."""
        for tid in self.keep(existing_tag_ids):
            if tid not in tag_ids:
                tag_ids.append(tid)
        return tag_ids


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


def collect_crew(processor, resolver, text, creator_roles, creator_name,
                 creator_ids, creator_sponsor=False):
    """Split a post's credited people into crew, sponsors and plain performers.

    Two kinds of credited account don't belong in the performers list, and both
    can be the creator or a collaborator credited by @mention or profile link:

    - **Crew** (director/photographer-tagged) move into the scene ``director`` /
      image ``photographer`` field.
    - **Sponsors** (Sponsor-tagged) are brands, not people in the media, so they
      are simply dropped and the media gets the ``sponsored`` tag instead --
      there is no Stash field to credit them in.

    A performer can be both: crew still takes the director/photographer credit
    and the sponsored tag is applied alongside.

    Returns (director_names, photographer_names, crew_ids, mention_performer_ids,
    sponsor_ids). crew_ids and sponsor_ids are the performer ids to keep out of
    the performers list; mention_performer_ids are the credited accounts that are
    neither.
    """
    crew = []          # (roles, display name)
    crew_ids = set()
    sponsor_ids = set()
    if creator_roles and creator_name:
        crew.append((creator_roles, creator_name))
        crew_ids.update(creator_ids)
    if creator_sponsor:
        sponsor_ids.update(creator_ids)

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
            m_sponsor = resolver.is_sponsor(mention)
            if m_sponsor:
                sponsor_ids.update(ids)
            if m_roles:
                if m_name:
                    crew.append((m_roles, m_name))
                crew_ids.update(ids)
            elif not m_sponsor:
                for pid in ids:
                    if pid not in mention_performer_ids:
                        mention_performer_ids.append(pid)

    director_names, photographer_names = [], []
    for roles, name in crew:
        if "director" in roles and name not in director_names:
            director_names.append(name)
        if "photographer" in roles and name not in photographer_names:
            photographer_names.append(name)
    return (director_names, photographer_names, crew_ids, mention_performer_ids,
            sponsor_ids)


def build_crew_only_update(db, processor, media_row, creator_ids, creator_roles,
                           creator_name, resolver, kind, existing_performer_ids,
                           existing_credit, tags=None, existing_tag_ids=None,
                           creator_sponsor=False):
    """Return an update that only fixes the crew and sponsor credits: move
    director/photographer-tagged people out of the existing performers list and
    into the director/photographer field, and drop Sponsor-tagged accounts from
    it in favour of a ``sponsored`` tag.

    Still surgical: it touches only ``performer_ids``, the ``director`` /
    ``photographer`` field, and -- purely additively -- ``tag_ids``. Title,
    details, date, studio and organized are left untouched, so manual edits
    survive. Returns (None, None) when nothing needs to change.
    """
    meta = db.post_meta(media_row["post_id"])
    text = meta["text"] if (meta and meta["text"]) else ""
    director_names, photographer_names, crew_ids, _, sponsor_ids = collect_crew(
        processor, resolver, text, creator_roles, creator_name, creator_ids,
        creator_sponsor,
    )

    # Prune credited crew and sponsors from the existing performers; never leave
    # it empty (the creator goes back in rather than stripping the media bare).
    drop = crew_ids | sponsor_ids
    new_perf = [pid for pid in existing_performer_ids if pid not in drop]
    if not new_perf:
        new_perf = list(creator_ids)

    credit = None
    if kind == "scene" and director_names:
        credit = ", ".join(director_names)
    elif kind == "image" and photographer_names:
        credit = ", ".join(photographer_names)

    # Sponsors have no Stash field to be credited in, so the tag is the record.
    # Added only, never removed: un-sponsoring is a manual call.
    existing_tag_ids = list(existing_tag_ids or [])
    merged_tags = list(existing_tag_ids)
    if sponsor_ids and tags is not None:
        sponsor_tag_id = tags.resolve(SPONSORED_TAG)
        if sponsor_tag_id and sponsor_tag_id not in merged_tags:
            merged_tags.append(sponsor_tag_id)

    perf_changed = new_perf != list(existing_performer_ids)
    credit_changed = credit is not None and credit != (existing_credit or "")
    tags_changed = merged_tags != existing_tag_ids
    if not perf_changed and not credit_changed and not tags_changed:
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
    if tags_changed:
        update["tag_ids"] = merged_tags
        parts.append("+{}".format(SPONSORED_TAG))
    return update, ", ".join(parts)


def build_update(db, processor, profile, media_row, creator_ids, studio_id,
                 resolver, tags, tag_matcher, kind, creator_roles, creator_name,
                 source,
                 existing_performer_ids=None, existing_tag_ids=None,
                 keep_manual_edits=False, creator_sponsor=False,
                 protected_tags=None):
    username = profile["username"]
    post_id = media_row["post_id"]
    filename = media_row["filename"]
    date = processor.format_date(media_row["posted_at"])

    meta = db.post_meta(post_id)
    text = meta["text"] if (meta and meta["text"]) else ""

    (director_names, photographer_names, crew_ids, mention_performer_ids,
     sponsor_ids) = collect_crew(
        processor, resolver, text, creator_roles, creator_name, creator_ids,
        creator_sponsor,
    )

    api_type = media_row["api_type"]
    title, details = source.title(
        processor, meta, text, "{}: {}".format(api_type, date) if api_type else date
    )

    # Build the performer list: the creator (unless they are crew or a sponsor)
    # plus any credited accounts that are neither. If everyone credited turned
    # out to be crew or a sponsor, fall back to the creator so the media is
    # never performer-less.
    performer_ids = [] if (creator_roles or creator_sponsor) else list(creator_ids)
    for pid in mention_performer_ids:
        if pid not in performer_ids:
            performer_ids.append(pid)
    # Non-destructive mode: keep any performers already on the media (e.g. ones
    # you added by hand) instead of replacing the list. Crew- and Sponsor-tagged
    # accounts are still pulled out, so both features keep working.
    if keep_manual_edits and existing_performer_ids:
        drop = crew_ids | sponsor_ids
        for pid in existing_performer_ids:
            if pid not in performer_ids:
                performer_ids.append(pid)
        performer_ids = [pid for pid in performer_ids if pid not in drop]
    if not performer_ids:
        performer_ids = list(creator_ids)

    tag_ids = collect_tag_ids(processor, meta, text, tags, tag_matcher, source, db,
                              post_id)
    # A sponsor has no Stash field to be credited in, so the tag carries it.
    if sponsor_ids:
        sponsor_tag_id = tags.resolve(SPONSORED_TAG)
        if sponsor_tag_id and sponsor_tag_id not in tag_ids:
            tag_ids.append(sponsor_tag_id)
    # Non-destructive mode: keep any tags already on the media (manual tags)
    # instead of replacing the list; the post's tags are added alongside.
    if keep_manual_edits and existing_tag_ids:
        for tid in existing_tag_ids:
            if tid not in tag_ids:
                tag_ids.append(tid)
    # ...and another plugin's tags survive the replace whatever that setting
    # says -- they record what the media IS, not how the user annotated it.
    if protected_tags:
        protected_tags.merge_into(tag_ids, existing_tag_ids)

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
                  url, scene_ids, source, creator_sponsor=False):
    """Build a gallery input for one post, from the same post text/date/studio/
    performers/tags used for its scenes and images. Crew are credited in the
    gallery photographer field (galleries have no director)."""
    username = profile["username"]
    date = processor.format_date(group["posted_at"])
    meta = db.post_meta(post_id)
    text = meta["text"] if (meta and meta["text"]) else ""

    api_type = group.get("api_type")
    title, details = source.title(
        processor, meta, text, "{}: {}".format(api_type, date) if api_type else date
    )

    # On galleries, crew are KEPT as linked performers. Stash's director/
    # photographer fields are free text with no link back to a performer, so
    # browsing a director's/photographer's work is hard; a gallery groups a whole
    # post, so it's the natural place to carry that link. (Scenes and images still
    # move crew out of the performers list into the director/photographer field --
    # see build_update / build_crew_only_update.) So a gallery's performers are
    # everyone credited: the creator plus every @mentioned account, crew or not.
    #
    # SPONSORS are the exception to that exception: they are dropped here too.
    # The reason crew stay is that their credit field loses the link -- but a
    # sponsor has no credit field at all, the 'sponsored' tag below is the whole
    # record, so keeping them would just leave a brand sitting in the cast list.
    sponsor_ids = set(creator_ids) if creator_sponsor else set()
    performer_ids = [] if creator_sponsor else list(creator_ids)
    if text:
        for mention, domain in processor.parse_mentions(text):
            # resolve() first: is_sponsor() reads the info cache that resolve()
            # fills, so asking before resolving would always say "not a sponsor".
            ids = performers.resolve(
                mention, from_mention=True,
                source=sources.profile_for_domain(domain),
            )
            if performers.is_sponsor(mention):
                sponsor_ids.update(ids)
                continue
            for pid in ids:
                if pid not in performer_ids:
                    performer_ids.append(pid)
    if not performer_ids:
        performer_ids = list(creator_ids)

    gallery_tag_ids = collect_tag_ids(processor, meta, text, tags, tag_matcher,
                                      source, db, post_id)
    if sponsor_ids:
        sponsor_tag_id = tags.resolve(SPONSORED_TAG)
        if sponsor_tag_id and sponsor_tag_id not in gallery_tag_ids:
            gallery_tag_ids.append(sponsor_tag_id)

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
        "tag_ids": gallery_tag_ids,
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
    # sponsor_ids goes back so the non-destructive merge can prune a sponsor
    # that an earlier sync (or a hand edit) left on the gallery.
    return gallery_input, title, sponsor_ids


def is_outside(paths, roots):
    """True when none of `paths` sits under any of `roots`.

    Media with no path at all is treated as inside, i.e. left alone: the
    cleanup pass only ever acts on media it can positively place OUTSIDE the
    configured libraries.
    """
    if not paths or not roots:
        return False
    # Stripped, because a whitespace-only setting would otherwise normalise to a
    # prefix nothing matches -- making the whole library look "outside" and
    # eligible for cleanup.
    prefixes = [os.path.normpath(r.strip()) + os.sep
                for r in roots if r and r.strip()]
    if not prefixes:
        return False
    return not any(
        (os.path.normpath(p) + os.sep).startswith(prefix)
        for p in paths for prefix in prefixes
    )


def plugin_wrote_this(item, source):
    """Whether this plugin is what put its metadata on `item`.

    A studio somewhere under the site's parent studio is NOT evidence. Users
    nest their OWN studios there -- a real library had `ZacknRhys`,
    `Gang Bang Guys`, `Juice Anime` and others filed under `OnlyFans (network)`,
    holding hand-curated media this plugin never touched. Selecting on the
    studio tree alone swept all of it into the cleanup, which would have
    stripped titles, dates, tags and the organized flag from media whose only
    sin was being filed sensibly.

    The test is a **post URL on the site's own domain**, which `build_update`
    writes and nothing else does. The site tag is deliberately NOT enough: a
    user filing torrented OnlyFans content under `<creator> (OnlyFans)` may
    reasonably tag it `OnlyFans` too, and those studios ARE real creators, so
    neither the studio name nor the tag distinguishes their work from ours.
    Nobody hand-types `patreon.com/posts/12345` onto a torrent download.

    Biased towards leaving things alone: a missed stray keeps some wrong
    metadata until someone fixes it by hand, while a false positive destroys
    curation that cannot be recovered.
    """
    for url in item.get("urls") or []:
        if source.link_domain and source.link_domain in (url or ""):
            return True
    return False


def build_cleanup_update(item, kind, source, username, tags):
    """Undo what a stray sync wrote onto one media, or None if nothing to undo.

    These are files OUTSIDE every configured data path that nevertheless carry
    one of this plugin's studios -- media the plugin reached through Stash's
    substring path filter and had no business touching (see
    media_under_data_path). It wrote a title, details, date, code, studio, the
    creator performer, its tags, a post URL and `organized: True` over whatever
    was there.

    The originals are gone, so this clears those fields rather than restoring
    them. Clearing is the useful direction anyway: Stash falls back to the
    filename for an empty title, and `organized: False` puts the media back in
    whatever "not yet sorted" workflow it came from. Fields this plugin never
    writes are not touched.
    """
    update = {"id": item["id"]}

    if item.get("studio"):
        update["studio_id"] = None
    if item.get("title"):
        update["title"] = ""
    if item.get("organized"):
        update["organized"] = False
    # Guarded like the fields above rather than cleared unconditionally. Every
    # field sent is a field overwritten, and some of this media was re-curated
    # by hand AFTER the bad sync -- a blind clear would wipe that repair. Only
    # what actually holds a value is touched.
    if item.get("details"):
        update["details"] = ""
    if item.get("date"):
        update["date"] = None
    if item.get("code"):
        update["code"] = ""

    # Only the URLs pointing at this site; anything else on the media is not
    # ours and stays.
    urls = [u for u in (item.get("urls") or [])
            if source.link_domain not in (u or "")]
    if len(urls) != len(item.get("urls") or []):
        update["urls"] = urls

    # The creator performer, identified from the studio name this plugin built
    # ("<username> (Patreon)"), so only that one is dropped.
    target = (username or "").strip().lower()
    performers = [p for p in (item.get("performers") or [])
                  if (p.get("name") or "").strip().lower() != target]
    if len(performers) != len(item.get("performers") or []):
        update["performer_ids"] = [p["id"] for p in performers]

    # The plugin's own tags. Matched by NAME here rather than by resolving them
    # -- a cleanup should not create a tag it is trying to remove.
    plugin_tags = {source.site_tag.strip().lower(), SPONSORED_TAG,
                   "paid", "archived", "pinned"}
    kept = [t for t in (item.get("tags") or [])
            if (t.get("name") or "").strip().lower() not in plugin_tags]
    if len(kept) != len(item.get("tags") or []):
        update["tag_ids"] = [t["id"] for t in kept]

    credit = "director" if kind == "scene" else "photographer"
    if item.get(credit):
        update[credit] = ""

    # Nothing but the id means there is nothing of ours left on this media --
    # already cleaned, or it only ever carried the studio that found it. Return
    # None so the caller skips it entirely rather than sending a write that
    # changes nothing.
    if len(update) == 1:
        return None
    return update


def cleanup_stray_media(client, configured, tags, workers, totals):
    """Undo metadata written onto media outside every configured data path.

    The damage this repairs was caused by Stash's `path` filter being a
    SUBSTRING match over the whole library: a creator name occurring anywhere
    else in the library pulled unrelated files into that creator's sync. The
    guard is now in media_under_data_path, but nothing re-derives media the
    plugin has stopped being able to see, so the writes it already made have to
    be undone deliberately.

    Runs under the normal dry-run chokepoint, so the preview task lists exactly
    what it would change without writing.
    """
    roots = [p for _s, p in configured if p]
    if not roots:
        log.LogError("No data paths configured; nothing to compare against")
        return
    log.LogInfo("Looking for stray media outside: {}".format(", ".join(roots)))

    tasks = []
    skipped_not_ours = 0
    for source, _path in configured:
        if not source.parent_id:
            continue
        for kind in ("scene", "image"):
            try:
                items = client.find_media_under_studio(source.parent_id, kind)
            except RuntimeError as e:
                log.LogWarning("Could not list {}s for {}: {}".format(
                    kind, source.label, e))
                continue
            for item in items:
                if not is_outside(_media_paths(item), roots):
                    continue
                # A studio under the parent is not evidence the plugin wrote
                # this -- users nest their own studios there too.
                if not plugin_wrote_this(item, source):
                    skipped_not_ours += 1
                    continue
                studio_name = ((item.get("studio") or {}).get("name") or "")
                username = studio_name.split(" (")[0]
                update = build_cleanup_update(item, kind, source, username, tags)
                if update is None:
                    continue
                paths = _media_paths(item)
                # The studio names the creator whose username matched this
                # path, which is the only thing that explains why the file was
                # ever touched -- "a creator you do subscribe to is a substring
                # of a folder name you don't recognise".
                log.LogInfo("  Stray {} {} [{}]: {}".format(
                    kind, item["id"], studio_name or "no studio",
                    paths[0] if paths else "(no path)"))
                tasks.append(_media_task(client, kind, update))

    if skipped_not_ours:
        log.LogInfo(
            "Left alone: {} item(s) outside the data paths that carry one of "
            "these studios but none of this plugin's own metadata -- not this "
            "plugin's work, most likely your own studios filed under the same "
            "parent"
            .format(skipped_not_ours))
    if not tasks:
        log.LogInfo("No stray media found.")
        return
    log.LogInfo("{} stray item(s) to clean up".format(len(tasks)))
    run_writes(tasks, workers, totals)


def _media_paths(item):
    """Every file path of a scene or image, whichever shape Stash returned."""
    paths = [f.get("path") for f in (item.get("files") or [])]
    paths += [vf.get("path") for vf in (item.get("visual_files") or [])]
    return [p for p in paths if p]


def media_under_data_path(items, source, username, kind):
    """Drop media that isn't inside this site's configured data path.

    Stash's `path` filter is a plain SUBSTRING match over the WHOLE library, and
    the media queries pass only the creator's username -- so a creator called
    'Mirenac' matched
    `/torrents/downloads/whisparr/[Mirenac] <title>/1.png`, a file with no
    connection to the library at all. It was then claimed by a post whose own
    image happened to be named `1.png`, and got that post's title, URL, date,
    studio and `organized` flag written onto it.

    So the substring result is confined to the data path the user configured for
    this site. That boundary is the only one that holds: matching the creator as
    a whole path SEGMENT (what sync_folder_galleries does) would reject Patreon's
    own `<vanity> - <Name>` folders, and the torrent path above has 'Mirenac'
    inside a segment rather than as one.

    A source with no configured path is left alone, since there is nothing to
    confine it to.
    """
    root = (getattr(source, "data_path", "") or "").strip()
    if not root:
        return items
    prefix = os.path.normpath(root) + os.sep
    kept, dropped = [], 0
    for item in items:
        paths = _media_paths(item)
        if paths and not any(
            (os.path.normpath(p) + os.sep).startswith(prefix) for p in paths
        ):
            dropped += 1
            continue
        kept.append(item)
    if dropped:
        log.LogDebug(
            "  Ignored {} {}(s) matching '{}' from outside {}".format(
                dropped, kind, username, root))
    return kept


def group_media_by_post(db, user_id, all_scenes, all_images):
    """post id -> {"images": [stash ids], "scenes": [...], posted_at, api_type}.

    Organized media is included deliberately: a gallery should hold all of a
    post's media regardless of the sync/full mode, and collections group whole
    posts the same way.
    """
    # path -> (kind, stash id), plus a basename fallback. Keyed by path because
    # a basename is not unique on Patreon (the same video posted at two tiers
    # lands in two post folders under one name), and a basename-keyed index
    # would let one of those files evict the other.
    index, by_name = {}, {}

    def remember(name, entry):
        """Record a basename, or mark it ambiguous by storing None.

        An ambiguous name is worse than a missing one: it produces a confident
        wrong answer. Two Stash media sharing a basename can never be told apart
        by name, so neither is offered.
        """
        if name in by_name and by_name[name] != entry:
            by_name[name] = None
        else:
            by_name.setdefault(name, entry)

    for scene in all_scenes:
        for f in scene.get("files") or []:
            index[os.path.normpath(f["path"])] = ("scene", scene["id"])
            remember(os.path.basename(f["path"]), ("scene", scene["id"]))
    for image in all_images:
        for vf in image.get("visual_files") or []:
            if vf.get("path"):
                index[os.path.normpath(vf["path"])] = ("image", image["id"])
            if vf.get("basename"):
                remember(vf["basename"], ("image", image["id"]))

    # Which source basenames belong to more than one post. Built up front
    # because the fallback below has to know before it answers.
    seen_owner, ambiguous = {}, set()
    for row in db.medias_for_model(user_id):
        name, post_id = row["filename"], row["post_id"]
        if post_id is None:
            continue
        if seen_owner.setdefault(name, str(post_id)) != str(post_id):
            ambiguous.add(name)

    groups = {}
    matched_by_path = 0
    wanted_paths = 0
    for row in db.medias_for_model(user_id):
        post_id = row["post_id"]
        if post_id is None:
            continue
        post_id = str(post_id)
        g = groups.setdefault(post_id, {
            "images": [], "scenes": [], "posted_at": row["posted_at"],
            "api_type": row["api_type"],
        })
        # Patreon rows carry the media's path; the sqlite-backed sources' rows
        # (sqlite3.Row) have no such column, so they fall through to the name.
        #
        # When a row HAS a path, a miss is final -- it means Stash doesn't hold
        # that exact file, and the basename fallback would then match a
        # DIFFERENT post's file of the same name. That is how one image ended up
        # in four unrelated comics' galleries: every post whose folder contained
        # a same-named image claimed it, and addGalleryImages only ever adds, so
        # each wrong claim stuck permanently.
        row_path = row["path"] if "path" in row.keys() else None
        name = row["filename"]
        entry = None
        if row_path:
            wanted_paths += 1
            entry = index.get(os.path.normpath(row_path))
            if entry:
                matched_by_path += 1
        if entry is None and name not in ambiguous:
            # Falling back on NAME is safe only while the name points at one
            # post on both sides. A shared basename is exactly what put one
            # image into four unrelated comics' galleries: every post holding a
            # same-named file claimed it, and addGalleryImages only ever adds,
            # so each wrong claim stuck. Keeping the fallback for unique names
            # means a data path that doesn't quite match Stash's library path
            # still syncs most of a library instead of silently matching none.
            entry = by_name.get(name)
        if not entry:
            continue
        kind, stash_id = entry
        bucket = g["images"] if kind == "image" else g["scenes"]
        if stash_id not in bucket:
            bucket.append(stash_id)

    # A source that supplies paths but matches none of them means the plugin's
    # data path and Stash's library path disagree -- a wholly recoverable
    # misconfiguration that would otherwise look like "the sync just does
    # nothing", since only uniquely-named files would still be found by name.
    if wanted_paths and not matched_by_path and (all_images or all_scenes):
        log.LogWarning(
            "None of the {} source file paths matched a path in Stash. The "
            "data path setting probably doesn't match the library path Stash "
            "scanned (e.g. /data/patreon vs a different mount). Matching fell "
            "back to filenames, which cannot tell same-named files apart."
            .format(wanted_paths))
    return groups


def reconcile_post_gallery(client, gallery_id, want_image_ids, owned_elsewhere,
                           totals):
    """Detach images that belong to a DIFFERENT post from this post's gallery.

    `addGalleryImages` only ever adds, so a wrong membership is permanent: a
    basename collision once let several posts each claim the same image, and the
    galleries kept it long after the matching was fixed. Nothing re-derives a
    gallery's contents, so the mistakes had to be undone by hand.

    The safety rule is that an image is removed ONLY when this run can name the
    other post it belongs to (`owned_elsewhere`). An image the plugin does not
    recognise is left alone -- it may have been added by hand, or by another
    plugin, and this pass has no business deciding it doesn't belong. That makes
    the operation narrow enough to run unattended: it can only ever undo a claim
    the plugin itself made wrongly.
    """
    if not owned_elsewhere:
        return
    try:
        current = client.find_gallery_image_ids(gallery_id)
    except RuntimeError as e:
        log.LogWarning("Could not list images in gallery {}: {}".format(
            gallery_id, e))
        return

    want = set(want_image_ids)
    stale = [i for i in current if i not in want and i in owned_elsewhere]
    if not stale:
        return
    client.remove_gallery_images(gallery_id, stale)
    totals["detached"] = totals.get("detached", 0) + len(stale)
    log.LogInfo("  Detached {} image(s) that belong to another post".format(
        len(stale)))


def sync_collection_galleries(client, db, profile, processor, studios, performers,
                              tags, studio_id, creator_ids, full_sync, workers,
                              all_scenes, all_images, totals, source,
                              protected_tags=None):
    """One Stash gallery per creator-curated collection.

    Stash has no nested galleries and no gallery-to-group link, so a collection
    cannot be "a gallery of galleries". It is flattened instead: the collection
    gallery holds every member post's images and links every member post's
    scenes, which is the whole of the collection's media in the one place Stash
    will accept it.

    Keyed by the collection URL rather than its title -- a creator can rename a
    collection, and matching on the title would then create a second gallery
    beside the first. Same rule the per-post galleries use.
    """
    collections = db.collections()
    if not collections:
        return

    groups = group_media_by_post(db, profile["user_id"], all_scenes, all_images)
    by_url = {}
    if studio_id:
        for gal in client.find_galleries_for_studio(studio_id):
            for u in gal.get("urls") or []:
                by_url[u] = gal

    site_tag_id = tags.resolve(source.site_tag) if tags else None
    tasks = []
    for coll in collections:
        url = coll["url"]
        # Exclusions apply here as they do to every other title the plugin
        # writes: a creator who prefixes their post titles with boilerplate
        # generally prefixes their collection titles with it too. The synthetic
        # fallback isn't authored text, so it is left alone.
        title = processor.apply_title_exclusions(coll["title"]) or \
            "Collection {}".format(coll["collection_id"])
        image_ids, scene_ids = [], []
        for post_id in coll["post_ids"]:
            group = groups.get(str(post_id))
            if not group:
                continue
            for image_id in group["images"]:
                if image_id not in image_ids:
                    image_ids.append(image_id)
            for scene_id in group["scenes"]:
                if scene_id not in scene_ids:
                    scene_ids.append(scene_id)
        # A collection whose posts aren't in Stash yet would otherwise create an
        # empty gallery that never fills in; skip until its media exists.
        if not image_ids and not scene_ids:
            log.LogDebug("Collection '{}': no synced media yet, skipping".format(title))
            continue

        gallery_input = {
            "title": title,
            "code": str(coll["collection_id"]),
            "details": processor.remove_html_tags(coll["description"] or ""),
            "studio_id": studio_id,
            "performer_ids": list(creator_ids),
            "tag_ids": [site_tag_id] if site_tag_id else [],
            "urls": [url],
            "organized": True,
        }
        date = processor.format_date(coll["date"])
        if date:
            gallery_input["date"] = date
        if scene_ids:
            gallery_input["scene_ids"] = scene_ids

        existing = by_url.get(url)
        if existing:
            # A plain sync only fills in missing images; a full sync also
            # refreshes the metadata, matching how post galleries behave.
            update = dict(gallery_input, id=existing["id"]) if full_sync else None
            # This gallery's tag_ids is built from scratch (the site tag only),
            # so a full sync would drop another plugin's tags outright.
            if update is not None and protected_tags:
                update["tag_ids"] = protected_tags.merge_into(
                    list(update.get("tag_ids") or []),
                    [t["id"] for t in existing.get("tags") or []],
                )

            # As in build_post_galleries: Stash rejects addGalleryImages on a
            # folder-based gallery, so only its metadata is ours to set.
            attach = not existing.get("folder")

            def _update(u=update, gid=existing["id"], imgs=list(image_ids), t=title,
                        attach=attach):
                if u is not None:
                    client.update_gallery(u)
                if imgs and attach:
                    client.add_gallery_images(gid, imgs)
                return {"galleries": 1}
            tasks.append(_update)
        else:
            def _create(gi=dict(gallery_input), imgs=list(image_ids), t=title):
                gallery_id = client.create_gallery(gi)
                if not gallery_id:
                    return {}
                if imgs:
                    client.add_gallery_images(gallery_id, imgs)
                log.LogInfo("Created collection gallery '{}' ({} image(s), {} scene(s))".format(
                    t, len(imgs), len(gi.get("scene_ids") or [])))
                return {"galleries": 1}
            tasks.append(_create)

    if tasks:
        run_writes(tasks, workers, totals)


def build_post_galleries(client, db, profile, processor, performers, tags,
                         tag_matcher, studio_id, creator_ids, creator_roles,
                         creator_name, full_sync, keep_manual_edits, workers,
                         all_scenes, all_images, totals, source,
                         creator_sponsor=False, protected_tags=None):
    """Group a creator's media by post and make one gallery per post.

    A gallery is created when a post has 2+ images, or an image alongside a video
    (Stash relates scenes to galleries, not to images, so the gallery carries the
    scene link). Galleries are keyed by the post URL: a plain sync creates missing
    ones and adds images; a full sync also refreshes their metadata.

    ``all_scenes``/``all_images`` are the creator's media already fetched by
    process_profile (organized included), reused here instead of re-querying.
    """
    user_id = profile["user_id"]
    groups = group_media_by_post(db, user_id, all_scenes, all_images)

    # Existing per-post galleries for this creator's studio, keyed by url.
    by_url = {}
    if studio_id:
        for gal in client.find_galleries_for_studio(studio_id):
            for u in gal.get("urls") or []:
                by_url[u] = gal

    # image id -> the post it actually belongs to. Built from this run's own
    # grouping, so it only ever names images the plugin can attribute; anything
    # else stays off-limits to the reconcile pass below.
    owner_of = {}
    for pid, g in groups.items():
        for image_id in g["images"]:
            owner_of.setdefault(image_id, pid)

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
                gallery_input, _title, sponsor_ids = _gallery_meta(
                    db, processor, profile, post_id, group, performers, tags,
                    tag_matcher, studio_id, creator_ids, creator_roles,
                    creator_name, url, scenes, source, creator_sponsor,
                )
                # Non-destructive mode: keep performers and tags already there.
                # Sponsors are the exception -- they are pruned even here, or a
                # brand left on the gallery by an older sync would never leave.
                if keep_manual_edits:
                    merged = list(gallery_input["performer_ids"])
                    for p in existing.get("performers") or []:
                        if p["id"] not in merged:
                            merged.append(p["id"])
                    gallery_input["performer_ids"] = [
                        pid for pid in merged if pid not in sponsor_ids
                    ] or list(creator_ids)
                    merged_tags = list(gallery_input.get("tag_ids") or [])
                    for t in existing.get("tags") or []:
                        if t["id"] not in merged_tags:
                            merged_tags.append(t["id"])
                    gallery_input["tag_ids"] = merged_tags
                # Another plugin's tags survive regardless of keepManualEdits:
                # a gallery comic-reader marked as a Webtoon must not stop being
                # one because this plugin re-derived the post's tags.
                if protected_tags:
                    protected_tags.merge_into(
                        gallery_input.setdefault("tag_ids", []),
                        [t["id"] for t in existing.get("tags") or []],
                    )
                gallery_input["id"] = existing["id"]

            # A folder-based gallery already IS its folder's contents, and Stash
            # rejects addGalleryImages on one ("cannot change contents of
            # folder-based gallery"). Its metadata is still ours to set, so
            # update it and leave the image list alone.
            attach = not existing.get("folder")

            # Images another post owns are only ever detached on a FULL sync:
            # a plain sync is meant to fill in what's missing, not to re-derive
            # what is already there.
            wrong = {}
            if attach and full_sync:
                wrong = {i: p for i, p in owner_of.items() if p != post_id}

            def _update_task(gi=gallery_input, gid=existing["id"], imgs=list(images),
                             attach=attach, wrong=wrong):
                if gi is not None:
                    client.update_gallery(gi)
                if attach:
                    client.add_gallery_images(gid, imgs)
                    reconcile_post_gallery(client, gid, imgs, wrong, totals)
                return {"galleries": 1}
            tasks.append(_update_task)
        else:
            gallery_input, title, _sponsor_ids = _gallery_meta(
                db, processor, profile, post_id, group, performers, tags,
                tag_matcher, studio_id, creator_ids, creator_roles,
                creator_name, url, scenes, source, creator_sponsor,
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
                    skip_multi_file, keep_manual_edits, workers, totals, source,
                    protected_tags=None):
    user_id = profile["user_id"]
    username = profile["username"]
    log.LogInfo("Processing {} {} (user_id {})".format(
        source.label, username, user_id))

    studio_id = None
    performer_ids = []
    creator_roles, creator_name = set(), None
    creator_sponsor = False
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
        creator_sponsor = performers.is_sponsor(username)
        if creator_sponsor:
            log.LogInfo("  '{}' tagged as sponsor".format(username))

        if not crew_only:
            studio_id = studios.resolve(username, source)
            if not studio_id:
                log.LogError("Could not resolve studio for {}; skipping".format(username))
                return

    # Map each Stash media file's path to (kind, stash id, existing tag ids).
    # We route the update by where the media actually lives in Stash so the id
    # always matches the mutation (scenes -> sceneUpdate, images -> imageUpdate).
    # Tag-only and full passes look at organized media too.
    # The skip-multi-file guard protects merged scenes (multiple files from
    # different OF pages) from having their performers/metadata overwritten. It
    # only applies to the destructive sync tasks, not the additive tag pass, and
    # not to sites whose merged media are byte-identical duplicates rather than
    # different files -- see SourceProfile.merged_files_are_duplicates.
    skip_multi = (skip_multi_file and not tag_only
                  and not source.merged_files_are_duplicates)
    if skip_multi_file and not tag_only and source.merged_files_are_duplicates:
        log.LogDebug(
            "  Skip Multi-file does not apply to {}: its merged media are "
            "identical duplicates, so they are synced".format(source.label))

    include_all = full_sync or tag_only or crew_only
    # Fetch the creator's media once (organized included) and reuse it for the
    # gallery pass too. The plain sync only *processes* unorganized media, so
    # organized items are skipped when building the update map (but still count
    # toward galleries).
    all_scenes = media_under_data_path(
        client.find_scenes(username, True), source, username, "scene")
    all_images = media_under_data_path(
        client.find_images(username, True), source, username, "image")
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
            media_map[f["path"]] = entry
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
            path = vf.get("path")
            if path:
                media_map[path] = entry
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
    # A multi-file scene has one entry per file, so without this a scene whose
    # files span two posts would be written twice, the second update silently
    # undoing the first.
    seen_media = set()
    # Sorted so a merged media spanning two posts always takes the same one
    # (the lowest path) instead of whichever the dict happened to yield first --
    # otherwise re-runs could flip such an item between two posts' metadata.
    for path, (kind, stash_id, existing_tags, existing_perf, existing_credit) in sorted(media_map.items()):
        basename = os.path.basename(path)
        # Path first, basename second. The two are equivalent for OF-Scraper and
        # jff-scraper, whose filenames are media ids; on Patreon the same
        # basename really can appear under several posts (the same video posted
        # at two tiers), and only the path says which post this file belongs to.
        media_row = (db.media_by_path(user_id, path)
                     or db.media_by_filename(user_id, basename))
        if not media_row:
            # Logged so an unexplained "Skipped: n" can be traced to the actual
            # files: a path Stash has but the source doesn't means the media sits
            # somewhere the reader isn't looking (an excluded folder, a path
            # outside the data path), which is otherwise invisible.
            log.LogDebug("  No source entry for '{}' -- skipped".format(path))
            totals["skipped"] += 1
            continue
        if (kind, stash_id) in seen_media:
            continue
        seen_media.add((kind, stash_id))

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
                tags, existing_tags, creator_sponsor,
            )
            if update is None:
                totals["skipped"] += 1
                continue
        else:
            update, label = build_update(
                db, processor, profile, media_row, performer_ids, studio_id,
                performers, tags, tag_matcher, kind, creator_roles, creator_name,
                source, existing_perf, existing_tags, keep_manual_edits,
                creator_sponsor, protected_tags,
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
            creator_sponsor, protected_tags,
        )
        # Creator-curated collections (Patreon only; every other source returns
        # none), flattened into one gallery each.
        sync_collection_galleries(
            client, db, profile, processor, studios, performers, tags, studio_id,
            performer_ids, full_sync, workers, all_scenes, all_images, totals,
            source, protected_tags,
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
    # A repair pass, not a sync: it walks media the plugin already stamped and
    # undoes what it wrote OUTSIDE the configured libraries. It needs the parent
    # studios resolved (that is how it finds the plugin's own writes) but no
    # library discovery at all, so it returns before that loop.
    cleanup_only = mode == "cleanup"

    # Dry run comes from either the task ("Preview") or the setting, so a one-off
    # preview never needs the setting toggled and back. The plugin config is read
    # through this same client, so the arg has to be honoured before that read
    # and the setting folded in immediately after.
    client = StashClient(server, dry_run=bool(args.get("dryRun")))
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

    # Optional per-site scoping: the manifest's "Sync <Site>" tasks pass
    # {"site": "<slug>"} so one site can be re-synced without walking the
    # others' libraries. An unknown slug is fatal rather than ignored -- falling
    # back to every site is the opposite of what a per-site task was asked for.
    site = str(args.get("site") or "").strip().lower()
    scoped_source = None
    if site:
        scoped_source = sources.profile_for_slug(site)
        if scoped_source is None:
            msg = "Unknown site '{}'. Known sites: {}.".format(
                site, ", ".join(sources.slugs())
            )
            log.LogError(msg)
            return msg

    # Each site has its own data path and parent studio; everything else is
    # shared. A site with no path configured is simply not scanned, so an
    # OnlyFans-only setup behaves exactly as it did before other sites existed.
    configured_sources = []
    for source in sources.ALL_PROFILES:
        if scoped_source is not None and source is not scoped_source:
            continue
        path = str(get_setting(config, source.path_setting, "") or "").strip()
        if not path:
            continue
        source.parent_default_name = get_setting(
            config, source.parent_setting, source.parent_default
        )
        # Remembered so the media queries can be confined to this site's
        # library -- see media_under_data_path.
        source.data_path = path
        configured_sources.append((source, path))
    try:
        max_title_length = int(get_setting(config, "maxTitleLength", DEFAULT_MAX_TITLE_LENGTH))
    except (TypeError, ValueError):
        max_title_length = DEFAULT_MAX_TITLE_LENGTH
    multiple_ok = bool(get_setting(config, "multiplePerformersOk", False))
    auto_create = bool(get_setting(config, "autoCreatePerformers", False))
    auto_tag_from_text = bool(get_setting(config, "autoTagFromText", False))
    skip_multi_file = bool(get_setting(config, "skipMultiFile", False))
    if not client.dry_run and bool(get_setting(config, "dryRun", False)):
        client.dry_run = True
    if client.dry_run:
        log.LogInfo(
            "DRY RUN: every change is logged and nothing is written to Stash."
        )
    crew_tag_id = get_setting(config, "crewTagId", "")
    sponsor_tag_id = get_setting(config, "sponsorTagId", "")
    keep_manual_edits = bool(get_setting(config, "keepManualEdits", False))
    title_exclusions = parse_title_exclusions(get_setting(config, "titleExclusions", ""))
    try:
        workers = int(get_setting(config, "syncWorkers", DEFAULT_WORKERS))
    except (TypeError, ValueError):
        workers = DEFAULT_WORKERS
    workers = max(1, min(workers, 16))  # clamp: 1 = sequential, cap concurrency

    if not configured_sources:
        if scoped_source is not None:
            msg = (
                "No data path configured for {}. Set '{}' in the plugin settings, "
                "or use a task that isn't scoped to one site.".format(
                    scoped_source.label, scoped_source.path_setting
                )
            )
        else:
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
                 "crew/sponsor credit pass" if crew_only else
                 "scoped performer re-sync" if performer_scope else
                 "{}metadata sync".format("FULL " if full_sync else ""))
    log.LogInfo("Starting {}{} for: {}".format(
        pass_name,
        " scoped to {}".format(scoped_source.label) if scoped_source else "",
        ", ".join("{} ({})".format(src.label, path) for src, path in configured_sources)))

    # The parent studio is only needed by the passes that create studios, and it
    # must already exist -- the plugin never creates it.
    if not tag_only and not crew_only:
        for source, _path in configured_sources:
            name = source.parent_default_name
            # Loaded first so a parent created below gets the site logo too --
            # Stash accepts a studio image on create only.
            source.icon = load_icon(server, source.icon_file)
            source.parent_id = client.find_studio(name)
            if not source.parent_id:
                # Create it rather than failing the run. Making the user go and
                # add a studio by hand before their first sync is friction for
                # no benefit: the plugin already creates every per-creator
                # studio beneath it, so it may as well create the one they all
                # hang off. find_studio is alias-aware, so an existing studio
                # carrying the name as an alias is reused instead.
                try:
                    source.parent_id = client.create_studio(
                        name, None, None,
                        "{} creators, synced by Fan Site Metadata Sync.".format(source.label),
                        source.icon,
                    )
                except RuntimeError as e:
                    # create_studio doesn't swallow its own errors, and an
                    # unhandled one here would end the run as a traceback
                    # instead of the actionable message below.
                    log.LogWarning("Could not create parent studio '{}': {}".format(name, e))
                    source.parent_id = None
                if not source.parent_id:
                    msg = (
                        "Could not find or create the parent studio '{}'. Create "
                        "it in Stash (or fix the {} setting) and retry.".format(
                            name, source.parent_setting)
                    )
                    log.LogError(msg)
                    return msg
                log.LogInfo("Created parent studio '{}'".format(name))

    if cleanup_only:
        totals = {"scenes": 0, "images": 0, "galleries": 0, "skipped": 0,
                  "skipped_multifile": 0}
        cleanup_stray_media(client, configured_sources, None, workers, totals)
        log.LogProgress(1.0)
        summary = "Cleanup complete. Items reverted: {}".format(
            totals["scenes"] + totals["images"])
        if client.dry_run:
            summary += " (dry run -- nothing was written)"
        log.LogInfo(summary)
        return None

    # Discover every library once, remembering the site whose path found it, so
    # one is never scanned twice when two sites share a parent directory.
    #
    # What a "library" is differs per site: for the scraper-backed sites it's a
    # user_data.db file, for Patreon it's a creator's folder (no database
    # exists). Each site's reader knows how to find its own, so this stays one
    # loop rather than branching per site.
    databases = []
    seen_paths = set()
    for source, path in configured_sources:
        found = source.reader.find_databases(path)
        for db_path in found:
            if db_path in seen_paths:
                continue
            seen_paths.add(db_path)
            databases.append((source, db_path))
        if not found:
            log.LogWarning("No {} library found under {}".format(source.label, path))
    log.LogInfo("Found {} librar{}{}".format(
        len(databases), "y" if len(databases) == 1 else "ies",
        "" if workers <= 1 else " ({} parallel writers)".format(workers)))
    if not databases:
        return

    processor = MediaProcessor(max_title_length, title_exclusions)
    if title_exclusions:
        log.LogInfo("Loaded {} title exclusion pattern(s).".format(len(title_exclusions)))
    studios = StudioResolver(client)
    # The crew/sponsor pass is surgical maintenance: it must never create performers as
    # a side effect of resolving @mentions, even if Create Missing Performers is
    # enabled for the sync tasks.
    performers = PerformerResolver(
        client, auto_create and not crew_only, crew_tag_id, sponsor_tag_id
    )
    tags = TagResolver(client)
    # Resolved once per run, not per creator: it is two reads, and the tag tree
    # cannot change underneath a single sync in any way worth tracking. The
    # tag-only and crew passes only ever ADD tags, so they need no protection --
    # but it is built for them anyway rather than made conditional, since the
    # cost is trivial and a mode that starts replacing tags later would
    # otherwise silently lose it.
    protected_tags = ProtectedTags(client)
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

    for index, (found_by, db_path) in enumerate(databases):
        log.LogProgress(index / len(databases))
        try:
            db = found_by.reader(db_path)
        except Exception as e:
            log.LogError("Could not open {}: {}".format(db_path, e))
            continue
        # Which site this library came from is read from the library itself
        # (the scrapers' schema_flags source; "patreon" from the adapter), not
        # from the path it was found under -- so a data path holding more than
        # one kind of library sorts itself out.
        source = sources.profile_for_source(db.source())
        # A per-site task must honour the site the DATABASE says it is, not the
        # path it was found under: the two sites can share a parent directory,
        # and 'user_data.db' is both scrapers' filename. Without this, "Sync
        # OnlyFans" would happily sync a JustFor.Fans library sitting under the
        # OnlyFans path.
        if scoped_source is not None and source is not scoped_source:
            log.LogDebug("Skipping {} ({} database, not {})".format(
                db_path, source.label, scoped_source.label))
            db.close()
            continue
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
                    protected_tags,
                )
        except Exception as e:
            log.LogError("Error processing {}: {}".format(db_path, e))
        finally:
            db.close()

    log.LogProgress(1.0)
    verb = "Tagging" if tag_only else ("Credits update" if crew_only else "Sync")
    summary = "{} complete. Scenes updated: {}, Images updated: {}, Skipped: {}".format(
        verb, totals["scenes"], totals["images"], totals["skipped"]
    )
    if totals["galleries"]:
        summary += ", Galleries: {}".format(totals["galleries"])
    if totals["skipped_multifile"]:
        summary += ", Skipped multi-file: {}".format(totals["skipped_multifile"])
    if totals.get("detached"):
        summary += ", Detached from wrong gallery: {}".format(totals["detached"])
    log.LogInfo(summary)


if __name__ == "__main__":
    # Raw plugins return their result as JSON on stdout. A non-empty error is
    # logged by Stash at the error level and marks the task as failed.
    error = main()
    print(json.dumps({"error": error} if error else {"output": "ok"}))
