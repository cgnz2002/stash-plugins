"""Another plugin's tags must survive a Full Sync.

comic-reader marks comics with a tag tree -- a parent "Comic" tag, children
"Webtoon" (galleries) and "Comic Page" (the images in one) -- and keeps the
parent's id in its own plugin settings. A sync/full pass REPLACES tag_ids, and
`keepManualEdits` (off by default) protects the user's manual tags, which is a
different question. So on media both plugins touch, a Full Sync was deleting
comic-reader's marks: Stash stopped knowing a gallery was a comic, with nothing
in the log to say so.

These ids are kept regardless of `keepManualEdits` -- they are not a
preference, they are another plugin's data. When comic-reader is absent or
unconfigured, nothing is protected and every path behaves exactly as before.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _plugin import PLUGIN, plugin_file
sys.path.insert(0, PLUGIN)

import sync
import stash

COMIC, WEBTOON, PAGE = "40", "41", "42"
SITE_TAG, PAID = "7", "8"
MANUAL = "99"          # a tag the user added by hand
DESCENDANTS = {COMIC: [WEBTOON, PAGE]}


class FakeClient:
    """Answers only what ProtectedTags asks, and records that it asked."""

    def __init__(self, config=None, descendants=DESCENDANTS, fail=None):
        self.config = config
        self.descendants = descendants
        self.fail = fail
        self.config_reads = 0
        self.descendant_reads = 0

    def get_plugin_config(self, plugin_id):
        self.config_reads += 1
        if self.fail == "config":
            raise RuntimeError("boom")
        assert plugin_id == sync.COMIC_PLUGIN_ID, plugin_id
        return self.config if self.config is not None else {}

    def find_tag_descendants(self, tag_id):
        self.descendant_reads += 1
        if self.fail == "descendants":
            raise RuntimeError("depth unsupported")
        return self.descendants.get(tag_id, [])


CONFIGURED = {"comicTagId": COMIC, "webtoonTagId": WEBTOON,
              "comicPageTagId": PAGE}


# --- the id is read from comic-reader's own settings ------------------------
c = FakeClient(CONFIGURED)
p = sync.ProtectedTags(c)
assert p.ids == {COMIC, WEBTOON, PAGE}, p.ids
assert c.config_reads == 1 and c.descendant_reads == 1
# a descendant added later (a new comic format) is covered without naming it
c2 = FakeClient(CONFIGURED, descendants={COMIC: [WEBTOON, PAGE, "43"]})
assert "43" in sync.ProtectedTags(c2).ids

# ids are normalised to str: Stash returns ID as a string, but a hand-edited
# setting could be a number, and `42 in {"42"}` is False
c3 = FakeClient({"comicTagId": 40}, descendants={"40": [41]})
assert sync.ProtectedTags(c3).ids == {"40", "41"}, sync.ProtectedTags(c3).ids


# --- not installed / not configured protects NOTHING ------------------------
for cfg in [None, {}, {"comicTagId": ""}, {"comicTagId": "   "},
            {"webtoonTagId": WEBTOON}]:
    p_off = sync.ProtectedTags(FakeClient(cfg))
    assert p_off.ids == frozenset(), (cfg, p_off.ids)
    assert p_off.keep([COMIC, WEBTOON, MANUAL]) == []
    # and the merge is a no-op, so a caller's list is untouched
    tags = [SITE_TAG]
    assert p_off.merge_into(tags, [COMIC, WEBTOON]) == [SITE_TAG], tags

# an unconfigured plugin must not cost a descendant query
c4 = FakeClient({})
sync.ProtectedTags(c4)
assert c4.descendant_reads == 0


# --- a failed lookup degrades, it does not resume deleting -----------------
p_fail = sync.ProtectedTags(FakeClient(CONFIGURED, fail="descendants"))
assert p_fail.ids == {COMIC}, p_fail.ids          # the parent is still kept
assert p_fail.keep([COMIC, WEBTOON]) == [COMIC]
# a config read that fails protects nothing, but must not crash the run
assert sync.ProtectedTags(FakeClient(CONFIGURED, fail="config")).ids == frozenset()


# --- keep()/merge_into() -----------------------------------------------------
p = sync.ProtectedTags(FakeClient(CONFIGURED))
assert p.keep([WEBTOON, MANUAL, SITE_TAG]) == [WEBTOON]
assert p.keep([]) == [] and p.keep(None) == []
# order preserved, unprotected tags dropped, no duplicates introduced
assert p.merge_into([SITE_TAG], [PAGE, MANUAL]) == [SITE_TAG, PAGE]
assert p.merge_into([SITE_TAG, PAGE], [PAGE]) == [SITE_TAG, PAGE]


# --- build_update, called for real ------------------------------------------
# The assembly is exercised through the actual function rather than a mirror of
# it, so a later change to how tag_ids is built cannot pass this test while
# dropping the tags in the plugin.

import media
import sources

proc = media.MediaProcessor(64)
PROFILE = {"user_id": 1, "username": "creator"}
MEDIA = {"post_id": "100", "filename": "1.mp4", "posted_at": "2024-01-02",
         "api_type": "Posts", "media_id": "1"}


class PerformerClient:
    def find_all_performers(self):
        return [{"id": "1", "name": "creator", "alias_list": [], "tags": []}]

    def find_performers_by_name(self, name):
        return {"name_like": []}


class FakeTags:
    def resolve(self, name):
        return "t-" + name


class FakeDB:
    def post_meta(self, post_id):
        return {"text": "a post", "paid": 0, "price": 0, "archived": 0}

    def tier(self, post_id):
        return None

    def hashtags(self, post_id):
        return []

    def is_pinned(self, post_id):
        return False

    def post_url(self, post_id):
        return None


def build(existing, keep_manual, protected):
    r = sync.PerformerResolver(PerformerClient(), False, "", "")
    r.source = sources.ONLYFANS
    r.resolve("creator")
    update, _ = sync.build_update(
        FakeDB(), proc, PROFILE, MEDIA, ["1"], "studio1", r, FakeTags(), None,
        "scene", set(), None, sources.ONLYFANS,
        None, existing, keep_manual, False, protected,
    )
    return update["tag_ids"]


had = [WEBTOON, MANUAL]
site = "t-" + sources.ONLYFANS.site_tag

# keepManualEdits OFF -- the comic tag survives, the manual one does not
got = build(had, False, p)
assert WEBTOON in got, got
assert MANUAL not in got, got
assert site in got, got

# keepManualEdits ON -- both survive, nothing duplicated
got = build(had, True, p)
assert WEBTOON in got and MANUAL in got, got
assert got.count(WEBTOON) == 1, got

# no comic-reader configured -- exactly the old behaviour, both ways
off = sync.ProtectedTags(FakeClient({}))
got = build(had, False, off)
assert WEBTOON not in got and MANUAL not in got, got
got = build(had, True, off)
assert WEBTOON in got and MANUAL in got, got

# passing nothing at all (the default) is also unchanged
assert WEBTOON not in build(had, False, None)

# media carrying no tags yet is unaffected either way
assert WEBTOON not in build([], False, p)


# --- collection galleries ----------------------------------------------------
# Their tag_ids is built from the site tag ALONE, so a full sync would drop
# another plugin's tags outright rather than merely failing to merge them.

def collection_gallery(existing, protected):
    update = {"tag_ids": [SITE_TAG]}
    if protected:
        update["tag_ids"] = protected.merge_into(
            list(update.get("tag_ids") or []),
            [t["id"] for t in existing],
        )
    return update["tag_ids"]


assert collection_gallery([{"id": WEBTOON}, {"id": MANUAL}], p) == [SITE_TAG, WEBTOON]
assert collection_gallery([], p) == [SITE_TAG]
assert collection_gallery([{"id": WEBTOON}], None) == [SITE_TAG]


# --- the descendant query uses depth -1, or a deep tree gets cut off --------
src = plugin_file("stash.py")
text = open(src, encoding="utf-8").read()
start = text.index("def find_tag_descendants")
body = text[start:start + 1200]
assert '"parents"' in body, body[:200]
assert '"depth": -1' in body, "depth must be -1 (all levels), not a guess"
assert '"modifier": "INCLUDES"' in body, body[:400]

# it must be a READ, so a dry run still reports what it would protect
assert stash._mutation_field(
    body[body.index('query = """') + 11:body.index('"""', body.index('query = """') + 11)]
) is None


# --- every replacing site is wired; the additive ones need nothing ----------
sync_src = open(plugin_file("sync.py"), encoding="utf-8").read()
assert sync_src.count("protected_tags.merge_into(") == 4, \
    ("build_update, built post galleries, post FOLDER galleries (Patreon) and "
     "collection galleries must all protect")
# The post-folder pass replaces tag_ids on Stash's own galleries -- exactly the
# ones comic-reader marks as Webtoons -- so it must protect too.
seg = sync_src[sync_src.index("def build_post_folder_gallery_update"):]
seg = seg[:seg.index("\ndef ", 1)]
assert "protected_tags.merge_into(" in seg
# build_tag_only_update and build_crew_only_update only ever ADD tags
for fn in ["def build_tag_only_update", "def build_crew_only_update"]:
    seg = sync_src[sync_src.index(fn):]
    seg = seg[:seg.index("\ndef ", 1)]
    assert "protected_tags" not in seg, fn + " is additive and needs no guard"

print("ALL OK")
