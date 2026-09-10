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


class SourceProfile:
    def __init__(self, key, label, site_tag, studio_suffix, parent_setting,
                 parent_default, path_setting, icon_file, profile_url,
                 post_url, media_code, link_domain):
        # Value of schema_flags.source that selects this profile (None = the
        # OnlyFans default, since OF-Scraper databases carry no such flag).
        self.key = key
        self.label = label                  # human name, used in log lines
        self.site_tag = site_tag            # tag put on every synced item
        self.studio_suffix = studio_suffix  # per-creator studio "<user> (<suffix>)"
        self.parent_setting = parent_setting    # settings key for the parent studio
        self.parent_default = parent_default
        self.path_setting = path_setting    # settings key for this site's data path
        self.icon_file = icon_file          # studio image shipped with the plugin
        self.link_domain = link_domain      # profile links that credit a collaborator
        self._profile_url = profile_url
        self._post_url = post_url
        self._media_code = media_code
        # Filled in per run from the settings / Stash lookups.
        self.parent_default_name = parent_default  # possibly overridden by settings
        self.parent_id = None
        self.icon = None

    def profile_url(self, username):
        return self._profile_url(username)

    def post_url(self, db, post_id, username):
        return self._post_url(db, post_id, username)

    def media_code(self, processor, media_row, post_id):
        return self._media_code(processor, media_row, post_id)

    def studio_name(self, username):
        return "{} ({})".format(username, self.studio_suffix)

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

ALL_PROFILES = [ONLYFANS, JUSTFORFANS]
_BY_KEY = {p.key: p for p in ALL_PROFILES}


def profile_for_source(source):
    """Profile for a database's schema_flags source value. An OF-Scraper
    database has no such flag (source is None), which selects OnlyFans."""
    key = (str(source).strip().lower() or None) if source else None
    return _BY_KEY.get(key, ONLYFANS)
