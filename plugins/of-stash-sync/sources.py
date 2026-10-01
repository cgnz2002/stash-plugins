"""Per-site behaviour, so one sync can serve several fan sites.

The scraper databases share a schema (see source_database.py), so nearly all of
the sync is site-agnostic. What genuinely differs is collected here, one
``SourceProfile`` per site, and the right profile is picked per *database* from
its ``schema_flags`` source value -- not from a setting, so a data path holding
both kinds of library sorts itself out.

Adding another site means adding a profile here, not branching through sync.py.

The OnlyFans profile reproduces the behaviour this plugin had before
JustFor.Fans support was folded in, field for field -- that is deliberate, so
merging the two plugins cannot change what an existing OnlyFans library syncs to.
"""

import patreon_source
import source_database


class SourceProfile:
    def __init__(self, key, slug, label, site_tag, studio_suffix, parent_setting,
                 parent_default, path_setting, icon_file, profile_url,
                 post_url, media_code, link_domain, reader=None,
                 real_titles=False, merged_files_are_duplicates=False,
                 post_folders=False):
        # Value of schema_flags.source that selects this profile (None = the
        # OnlyFans default, since OF-Scraper databases carry no such flag).
        self.key = key
        # Stable identifier for the manifest's per-site tasks ({"site": slug}).
        # Separate from `key` because that one is None for OnlyFans, which is
        # fine as a database flag but useless as a task argument.
        self.slug = slug
        self.label = label                  # human name, used in log lines
        self.site_tag = site_tag            # tag put on every synced item
        self.studio_suffix = studio_suffix  # per-creator studio "<user> (<suffix>)"
        self.parent_setting = parent_setting    # settings key for the parent studio
        self.parent_default = parent_default
        self.path_setting = path_setting    # settings key for this site's data path
        self.icon_file = icon_file          # studio image shipped with the plugin
        self.link_domain = link_domain      # profile links that credit a collaborator
        # What opens one library for this site. The scraper-backed sites share
        # SourceDatabase (sqlite); Patreon has no database and uses an adapter
        # over its on-disk tree. Both answer the same questions, so sync.py
        # never needs to know which it got. Set after the classes are imported.
        self.reader = reader
        # Whether the site's posts carry an authored title (Patreon) rather than
        # one that has to be derived from the post text (OnlyFans/JustFor.Fans).
        self.real_titles = real_titles
        # Whether this site's multi-file media are byte-identical DUPLICATES
        # rather than genuinely different files, which decides whether the
        # Skip Multi-file setting applies to it.
        #
        # That setting exists for OnlyFans, where a merged scene is several
        # DIFFERENT files gathered from different pages -- so there is no single
        # right post to take metadata from, and overwriting is the greater harm.
        # patreon-dl produces the opposite case: a creator who posts the same
        # image at several tiers downloads byte-identical copies into each post
        # folder, and Stash merges them automatically on hash. Skipping those
        # means skipping ordinary images whose only sin is being posted twice,
        # and the "merge" carries no information to protect. So Patreon opts out
        # and is synced regardless of the setting.
        self.merged_files_are_duplicates = merged_files_are_duplicates
        # Whether every post is downloaded into a folder of its own (Patreon),
        # so the gallery Stash already makes from that folder IS the post's
        # gallery. Then the post's metadata is written onto Stash's folder (and
        # zip) galleries instead of the plugin building a second, duplicate
        # gallery of the same images. Stash owns a folder gallery's contents,
        # so the plugin can never put the wrong images in one -- the failure
        # that put one image into four unrelated comics' galleries cannot
        # happen. OF-Scraper and jff-scraper file media by type, not by post,
        # so their folders say nothing about posts and they keep the built
        # per-post galleries.
        self.post_folders = post_folders
        self._profile_url = profile_url
        self._post_url = post_url
        self._media_code = media_code
        # Filled in per run from the settings / Stash lookups.
        self.parent_default_name = parent_default  # possibly overridden by settings
        self.parent_id = None
        # The configured data path for this site, filled in per run. Media
        # queries are confined to it: Stash's `path` filter is a substring match
        # over the whole library, so a creator name can otherwise match files
        # with no connection to the library.
        self.data_path = ""
        self.icon = None

    def profile_url(self, username):
        return self._profile_url(username)

    def post_url(self, db, post_id, username):
        return self._post_url(db, post_id, username)

    def media_code(self, processor, media_row, post_id):
        return self._media_code(processor, media_row, post_id)

    def studio_name(self, username):
        return "{} ({})".format(username, self.studio_suffix)

    def title(self, processor, meta, text, fallback):
        """Title and details for a post.

        OnlyFans and JustFor.Fans have no title field, so one is derived from
        the post text (and Title Exclusions strip the boilerplate). Patreon
        posts carry a real, authored title, so sites that have one say so with
        `real_titles` and it is used as written, with the body kept whole as
        the details instead of being split out of it.
        """
        if self.real_titles and meta:
            title = (meta["title"] or "").strip()
            if title:
                # Exclusions still apply: a creator who prefixes every Patreon
                # title with boilerplate has the same problem either way.
                return processor.apply_title_exclusions(title), text
        if text:
            return processor.process_text(text)
        return fallback, ""

    def is_paid(self, db, post_id, meta):
        """Whether the post counts as paid.

        OnlyFans records a real per-post price, so paid content is 'flagged paid
        AND price > 0'. JustFor.Fans exposes no price at all -- those columns are
        always 0 -- so its scraper's Free/Paid tier is the only usable signal.
        Tier wins when present, and the price rule is the fallback.
        """
        tier = db.tier(post_id)
        if tier:
            return str(tier).strip().lower() == "paid"
        if not meta:
            return False
        price = meta["price"] or 0
        return bool(meta["paid"] and price and int(price) > 0)


def _of_profile_url(username):
    return "https://www.onlyfans.com/{}".format(username)


def _of_post_url(db, post_id, username):
    """An OnlyFans post link is onlyfans.com/<post id>/<user>, so it rebuilds
    from the id. Real posts have a numeric id; profile/avatar/header assets use a
    hash and would produce a junk URL, so those get none."""
    if not str(post_id).isdigit():
        return None
    return "https://www.onlyfans.com/{}/{}".format(post_id, username)


def _of_media_code(processor, media_row, post_id):
    """OF-Scraper names each file after its media id, so the filename stem IS the
    id -- which is what this plugin has always used as the studio code."""
    return processor.studio_code(media_row["filename"])


def _jff_profile_url(username):
    return "https://justfor.fans/{}".format(username)


def _jff_post_url(db, post_id, username):
    """A JustFor.Fans link carries an encoded key
    (justfor.fans/<user>?Post=<key>), so unlike OnlyFans it cannot be rebuilt
    from the post id -- it has to come from what the scraper captured. When it
    wasn't captured, fall back to a stable synthetic link so the per-post
    galleries (which are keyed by URL) stay idempotent across runs."""
    url = db.post_url(post_id)
    if url:
        return url
    return "{}#post-{}".format(_jff_profile_url(username), post_id)


def _jff_media_code(processor, media_row, post_id):
    """jff-scraper names files '<date> - <post id> - <description>', so the stem
    would be a long useless string; the post id is the identifier that ties a
    scene to its filename, its JSON sidecar and its gallery."""
    return str(post_id)


ONLYFANS = SourceProfile(
    key=None,
    slug="onlyfans",
    label="OnlyFans",
    site_tag="OnlyFans",
    studio_suffix="OnlyFans",
    parent_setting="parentStudioName",
    parent_default="OnlyFans (network)",
    path_setting="dataPath",
    icon_file="onlyfans.png",
    profile_url=_of_profile_url,
    post_url=_of_post_url,
    media_code=_of_media_code,
    link_domain="onlyfans.com",
)

JUSTFORFANS = SourceProfile(
    key="jff",
    slug="justforfans",
    label="JustFor.Fans",
    site_tag="JustFor.Fans",
    studio_suffix="JustForFans",
    parent_setting="jffParentStudioName",
    parent_default="JustForFans (network)",
    path_setting="jffDataPath",
    icon_file="justforfans.png",
    profile_url=_jff_profile_url,
    post_url=_jff_post_url,
    media_code=_jff_media_code,
    link_domain="justfor.fans",
)

def _patreon_profile_url(username):
    return "https://www.patreon.com/{}".format(username)


def _patreon_post_url(db, post_id, username):
    """patreon.com/posts/<id> is rebuildable, but the adapter already holds the
    real URL captured from the post (which carries the slug), so prefer that."""
    return db.post_url(post_id) or "https://www.patreon.com/posts/{}".format(post_id)


def _patreon_media_code(processor, media_row, post_id):
    """patreon-dl names files after the media, not the post, and video carries a
    '<mediaId>-<title>' prefix -- so as with JustFor.Fans the post id is the
    identifier that ties a scene back to its post folder and its gallery."""
    return str(post_id)


PATREON = SourceProfile(
    key="patreon",
    slug="patreon",
    label="Patreon",
    site_tag="Patreon",
    studio_suffix="Patreon",
    parent_setting="patreonParentStudioName",
    parent_default="Patreon (network)",
    path_setting="patreonDataPath",
    icon_file="patreon.png",
    profile_url=_patreon_profile_url,
    post_url=_patreon_post_url,
    media_code=_patreon_media_code,
    link_domain="patreon.com",
    real_titles=True,
    merged_files_are_duplicates=True,
    post_folders=True,
)

ALL_PROFILES = [ONLYFANS, JUSTFORFANS, PATREON]

# Wired here rather than at construction so the profiles above stay pure data.
# The scraper-backed sites share one sqlite reader; Patreon has no database at
# all, so its adapter reads the creator's on-disk tree and answers the same
# questions. sync.py opens `source.reader(path)` without caring which it is.
ONLYFANS.reader = source_database.SourceDatabase
JUSTFORFANS.reader = source_database.SourceDatabase
PATREON.reader = patreon_source.PatreonLibrary
_BY_KEY = {p.key: p for p in ALL_PROFILES}
_BY_DOMAIN = {p.link_domain: p for p in ALL_PROFILES}
_BY_SLUG = {p.slug: p for p in ALL_PROFILES}


def profile_for_slug(slug):
    """Profile named by a per-site task's ``site`` argument, or None if the slug
    isn't one we know (the caller reports that rather than silently syncing
    everything, which is the opposite of what a per-site task was asked to do)."""
    return _BY_SLUG.get(str(slug).strip().lower()) if slug else None


def slugs():
    return [p.slug for p in ALL_PROFILES]


def profile_for_source(source):
    """Profile for a database's schema_flags source value. An OF-Scraper
    database has no such flag (source is None), which selects OnlyFans."""
    key = (str(source).strip().lower() or None) if source else None
    return _BY_KEY.get(key, ONLYFANS)


def profile_for_domain(domain):
    """Profile for the site a collaborator's profile link points at, or None
    when there is no link to go on (a bare @mention).

    A post can credit a collaborator with a link to *another* site -- a
    JustFor.Fans creator plugging their OnlyFans, say -- so a performer created
    from such a credit must get the URL of the site that was actually linked,
    not the site the post came from. None means 'unknown', and the caller falls
    back to the post's own source.
    """
    if not domain:
        return None
    return _BY_DOMAIN.get(str(domain).strip().lower())
