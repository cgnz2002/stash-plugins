"""Sponsor feature regression test.

A Sponsor-tagged account is dropped from the performers list and the media gets
the 'sponsored' tag instead. Covers scenes/images (sync + the surgical pass),
galleries, keep-manual mode, crew interaction, and the "unchanged when no
sponsor tag is configured" guarantee.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _plugin import PLUGIN, plugin_file
sys.path.insert(0, PLUGIN)

import media, sources, sync

CREW_TAG = "7"
SPONSOR_TAG = "9"
SPONSORED_TAG_ID = "500"

proc = media.MediaProcessor(64)


class FakeClient:
    """Performers: creator=1, brandco=2 (sponsor), pal=3, shooter=4 (crew)."""

    def __init__(self):
        self.performers = [
            {"id": "1", "name": "creator", "alias_list": [], "tags": []},
            {"id": "2", "name": "brandco", "alias_list": [],
             "tags": [{"id": SPONSOR_TAG}]},
            {"id": "3", "name": "pal", "alias_list": [], "tags": []},
            {"id": "4", "name": "shooter", "alias_list": [],
             "tags": [{"id": CREW_TAG}]},
        ]

    def find_all_performers(self):
        return self.performers

    def find_performers_by_name(self, name):
        return {"name_like": []}

    def create_performer(self, name, url):
        raise AssertionError("should not create in these cases")


class FakeTags:
    def __init__(self):
        self.resolved = []

    def resolve(self, name):
        self.resolved.append(name)
        return SPONSORED_TAG_ID if name == sync.SPONSORED_TAG else "t-" + name


class FakeDB:
    def __init__(self, text):
        self.text = text

    def post_meta(self, post_id):
        return {"text": self.text, "paid": 0, "price": 0, "archived": 0}

    def tier(self, post_id):
        return None

    def hashtags(self, post_id):
        return []

    def is_pinned(self, post_id):
        return False

    def post_url(self, post_id):
        return None


def resolver(sponsor_tag=SPONSOR_TAG, crew_tag=CREW_TAG):
    r = sync.PerformerResolver(FakeClient(), False, crew_tag, sponsor_tag)
    r.source = sources.ONLYFANS
    return r


MEDIA = {"post_id": "111", "filename": "222.mp4", "posted_at": "2024-01-02",
         "api_type": "Posts"}
PROFILE = {"username": "creator", "user_id": 1}


# --- collect_crew splits sponsors out ---------------------------------------
r = resolver()
r.resolve("creator")
text = "ad for @brandco with @pal and @shooter"
directors, photographers, crew_ids, mention_ids, sponsor_ids = sync.collect_crew(
    proc, r, text, set(), None, ["1"]
)
assert sponsor_ids == {"2"}, sponsor_ids          # brandco dropped
assert crew_ids == {"4"}, crew_ids                # shooter still crew
assert mention_ids == ["3"], mention_ids          # only pal survives
assert directors == ["shooter"], directors

# no Sponsor Tag configured -> nobody is a sponsor, old behaviour exactly
r2 = resolver(sponsor_tag="")
r2.resolve("creator")
_, _, crew2, mentions2, sponsors2 = sync.collect_crew(
    proc, r2, text, set(), None, ["1"]
)
assert sponsors2 == set(), sponsors2
assert mentions2 == ["2", "3"], mentions2         # brandco back in the cast
assert crew2 == {"4"}, crew2


# --- build_update: sponsor out of performers, 'sponsored' tag in -------------
r = resolver()
r.resolve("creator")
tags = FakeTags()
db = FakeDB("ad for @brandco with @pal")
update, _title = sync.build_update(
    db, proc, PROFILE, MEDIA, ["1"], "studio1", r, tags, None, "scene",
    set(), None, sources.ONLYFANS,
)
assert "2" not in update["performer_ids"], update["performer_ids"]
assert update["performer_ids"] == ["1", "3"], update["performer_ids"]
assert SPONSORED_TAG_ID in update["tag_ids"], update["tag_ids"]

# no sponsor credited -> no 'sponsored' tag
r = resolver()
r.resolve("creator")
tags = FakeTags()
update, _ = sync.build_update(
    FakeDB("just @pal"), proc, PROFILE, MEDIA, ["1"], "studio1", r, tags, None,
    "scene", set(), None, sources.ONLYFANS,
)
assert update["performer_ids"] == ["1", "3"], update["performer_ids"]
assert SPONSORED_TAG_ID not in update["tag_ids"], update["tag_ids"]
assert sync.SPONSORED_TAG not in tags.resolved, tags.resolved


# --- keep_manual_edits still prunes the sponsor -----------------------------
r = resolver()
r.resolve("creator")
update, _ = sync.build_update(
    FakeDB("ad for @brandco"), proc, PROFILE, MEDIA, ["1"], "studio1", r,
    FakeTags(), None, "image", set(), None, sources.ONLYFANS,
    existing_performer_ids=["1", "2", "99"], existing_tag_ids=["manual"],
    keep_manual_edits=True,
)
assert "2" not in update["performer_ids"], update["performer_ids"]
assert "99" in update["performer_ids"], update["performer_ids"]   # manual kept
assert "manual" in update["tag_ids"], update["tag_ids"]
assert SPONSORED_TAG_ID in update["tag_ids"], update["tag_ids"]


# --- the creator themself tagged sponsor: tagged, but never left empty -------
r = resolver()
r.resolve("creator")
update, _ = sync.build_update(
    FakeDB("solo ad"), proc, PROFILE, MEDIA, ["1"], "studio1", r, FakeTags(),
    None, "scene", set(), None, sources.ONLYFANS, creator_sponsor=True,
)
assert update["performer_ids"] == ["1"], update["performer_ids"]
assert SPONSORED_TAG_ID in update["tag_ids"], update["tag_ids"]


# --- the surgical pass ------------------------------------------------------
r = resolver()
r.resolve("creator")
tags = FakeTags()
update, label = sync.build_crew_only_update(
    FakeDB("ad for @brandco"), proc, MEDIA, ["1"], set(), None, r, "scene",
    ["1", "2"], None, tags, ["old"],
)
assert update["performer_ids"] == ["1"], update["performer_ids"]
assert update["tag_ids"] == ["old", SPONSORED_TAG_ID], update["tag_ids"]
assert "title" not in update and "details" not in update, update  # still surgical
assert "+sponsored" in label, label

# idempotent: already correct -> no write
update, label = sync.build_crew_only_update(
    FakeDB("ad for @brandco"), proc, MEDIA, ["1"], set(), None, r, "scene",
    ["1"], None, FakeTags(), ["old", SPONSORED_TAG_ID],
)
assert update is None and label is None, (update, label)

# no sponsor tag configured -> the crew pass behaves exactly as before
r = resolver(sponsor_tag="")
r.resolve("creator")
update, label = sync.build_crew_only_update(
    FakeDB("ad for @brandco"), proc, MEDIA, ["1"], set(), None, r, "scene",
    ["1", "2"], None, FakeTags(), ["old"],
)
assert update is None, update       # brandco is just a performer now


# --- galleries: sponsors dropped (crew are not) -----------------------------
r = resolver()
r.resolve("creator")
group = {"posted_at": "2024-01-02", "api_type": "Posts"}
gi, _t, sponsor_ids = sync._gallery_meta(
    FakeDB("ad for @brandco with @shooter"), proc, PROFILE, "111", group, r,
    FakeTags(), None, "studio1", ["1"], set(), None, "https://x/1", [],
    sources.ONLYFANS,
)
assert sponsor_ids == {"2"}, sponsor_ids
assert "2" not in gi["performer_ids"], gi["performer_ids"]
assert "4" in gi["performer_ids"], gi["performer_ids"]   # crew still linked
assert SPONSORED_TAG_ID in gi["tag_ids"], gi["tag_ids"]

print("ALL OK")
