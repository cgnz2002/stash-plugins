"""Content houses: credited like a person, synced as a tag.

A production house, filming location or fan site gets @mentioned like a
collaborator, but it is no performer, no crew and no sponsor -- and Stash has
no location field. A performer carrying the Content House Tag is therefore
dropped from scenes, images and galleries, and the media gets a tag named
after it, so everything from that house stays filterable. One tag per house,
not a generic one; the user files those under their own parent tag by hand.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _plugin import PLUGIN, plugin_file
sys.path.insert(0, PLUGIN)

import media, sources, sync

HOUSE_TAG = "11"
SPONSOR_TAG = "9"
proc = media.MediaProcessor(64)


class FakeClient:
    """creator=1, pal=3, raunchhouse=5 (house, display name 'Raunch House'),
    brandco=2 (sponsor)."""

    def __init__(self):
        self.performers = [
            {"id": "1", "name": "creator", "alias_list": [], "tags": []},
            {"id": "2", "name": "brandco", "alias_list": [],
             "tags": [{"id": SPONSOR_TAG}]},
            {"id": "3", "name": "pal", "alias_list": [], "tags": []},
            {"id": "5", "name": "Raunch House", "alias_list": ["raunchhouse"],
             "tags": [{"id": HOUSE_TAG}]},
        ]
        self.tags = []
        self.created = []

    def find_all_performers(self):
        return self.performers

    def find_performers_by_name(self, name):
        return {"name_like": []}

    def find_all_tags(self):
        return self.tags

    def create_tag(self, name):
        self.created.append(name)
        return "new-" + name


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


def setup(house_tag=HOUSE_TAG, tags_in_stash=()):
    client = FakeClient()
    # the site tag already exists, so `created` shows only what this feature makes
    client.tags = [{"id": "t-of", "name": "OnlyFans", "aliases": []}]
    client.tags += list(tags_in_stash)
    r = sync.PerformerResolver(client, False, "", SPONSOR_TAG, house_tag)
    r.source = sources.ONLYFANS
    r.resolve("creator")
    return client, r, sync.TagResolver(client)


MEDIA = {"post_id": "111", "filename": "222.mp4", "posted_at": "2024-01-02",
         "api_type": "Posts"}
PROFILE = {"username": "creator", "user_id": 1}
TEXT = "shot at @raunchhouse with @pal"


# --- the resolver flags it; collect_crew keeps it out of the cast -----------
client, r, tags = setup()
r.resolve("raunchhouse")
assert r.is_content_house("raunchhouse") is True
assert r.is_content_house("pal") is False
_, _, crew, mentions, sponsors = sync.collect_crew(
    proc, r, TEXT, set(), None, ["1"])
assert mentions == ["3"], mentions
assert sponsors == set(), "a content house is not a sponsor"


# --- sync: performer out, its own tag in, created under the @name ------------
client, r, tags = setup()
update, _ = sync.build_update(
    FakeDB(TEXT), proc, PROFILE, MEDIA, ["1"], "studio1", r, tags, None,
    "scene", set(), None, sources.ONLYFANS,
)
assert "5" not in update["performer_ids"], update["performer_ids"]
assert update["performer_ids"] == ["1", "3"], update["performer_ids"]
assert "new-raunchhouse" in update["tag_ids"], update["tag_ids"]
assert client.created == ["raunchhouse"], client.created   # the @name, as written
# no 'sponsored' tag -- that is the sponsor feature, not this one
assert "sponsored" not in client.created


# --- an existing tag is reused, by name OR alias, by @name OR display name ---
for existing in (
    {"id": "t-house", "name": "raunchhouse", "aliases": []},       # the @name
    {"id": "t-house", "name": "Raunch House", "aliases": []},      # display name
    {"id": "t-house", "name": "RH Studio", "aliases": ["raunchhouse"]},  # alias
):
    client, r, tags = setup(tags_in_stash=[existing])
    update, _ = sync.build_update(
        FakeDB(TEXT), proc, PROFILE, MEDIA, ["1"], "studio1", r, tags, None,
        "image", set(), None, sources.ONLYFANS,
    )
    assert "t-house" in update["tag_ids"], (existing, update["tag_ids"])
    assert client.created == [], (existing, client.created)


# --- keepManualEdits still prunes a house an older sync left behind ----------
client, r, tags = setup()
update, _ = sync.build_update(
    FakeDB(TEXT), proc, PROFILE, MEDIA, ["1"], "studio1", r, tags, None,
    "scene", set(), None, sources.ONLYFANS,
    existing_performer_ids=["1", "5", "9"], keep_manual_edits=True,
)
assert "5" not in update["performer_ids"], update["performer_ids"]
assert "9" in update["performer_ids"], "the user's own additions survive"


# --- the surgical crew pass fixes existing media ----------------------------
client, r, tags = setup()
upd, label = sync.build_crew_only_update(
    FakeDB(TEXT), proc, MEDIA, ["1"], set(), None, r, "scene",
    ["1", "3", "5"], "", tags, ["t-mine"],
)
assert upd["performer_ids"] == ["1", "3"], upd
assert upd["tag_ids"] == ["t-mine", "new-raunchhouse"], upd   # added only
assert "content-house" in label and "sponsored" not in label, label
assert set(upd) == {"performer_ids", "tag_ids"}, "surgical: nothing else"
# ...and is idempotent once applied
again, _ = sync.build_crew_only_update(
    FakeDB(TEXT), proc, MEDIA, ["1"], set(), None, r, "scene",
    ["1", "3"], "", tags, ["t-mine", "new-raunchhouse"],
)
assert again is None

# a house AND a sponsor in one post: each gets its own treatment
client, r, tags = setup()
upd, label = sync.build_crew_only_update(
    FakeDB("@raunchhouse x @brandco"), proc, MEDIA, ["1"], set(), None, r,
    "image", ["1", "2", "5"], "", tags, [],
)
assert upd["performer_ids"] == ["1"], upd
assert "new-raunchhouse" in upd["tag_ids"], upd
assert "sponsored" in label and "content-house" in label, label


# --- galleries drop it too, and get the tag ----------------------------------
client, r, tags = setup()
gi, _title, prune = sync._gallery_meta(
    FakeDB(TEXT), proc, PROFILE, "111", {"posted_at": "2024-01-02",
                                         "api_type": "Posts"},
    r, tags, None, "studio1", ["1"], set(), None, "https://x/111", [],
    sources.ONLYFANS,
)
assert "5" not in gi["performer_ids"], gi["performer_ids"]
assert "new-raunchhouse" in gi["tag_ids"], gi["tag_ids"]
assert "5" in prune, "returned so the keep-manual merge can prune it"


# --- no tag configured: old behaviour exactly --------------------------------
client, r, tags = setup(house_tag="")
update, _ = sync.build_update(
    FakeDB(TEXT), proc, PROFILE, MEDIA, ["1"], "studio1", r, tags, None,
    "scene", set(), None, sources.ONLYFANS,
)
assert update["performer_ids"] == ["1", "5", "3"], update["performer_ids"]
assert client.created == [], client.created


# --- wiring -----------------------------------------------------------------
src = open(plugin_file("sync.py"), encoding="utf-8").read()
assert 'get_setting(config, "contentHouseTagId", "")' in src
manifest = open(plugin_file("of-stash-sync.yml"), encoding="utf-8").read()
assert "contentHouseTagId:" in manifest

print("ALL OK")
