"""One image must not end up in several posts' galleries.

Observed in a real library: a single image sat in four unrelated comics'
galleries. Cause was a basename collision -- several Patreon post folders held
a same-named file, the index was keyed by basename, so every one of those posts
resolved to the SAME Stash image and claimed it. `addGalleryImages` only ever
adds, so each wrong claim stuck permanently and nothing ever re-derived a
gallery's contents.

Two halves here: the grouping must not make the wrong claim again, and a full
sync must be able to undo the claims already made.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _plugin import PLUGIN, plugin_file
sys.path.insert(0, PLUGIN)

import sync
import stash

CREATOR = "/data/patreon/mrxtoon - Mr. X-Toon/posts"


def image(stash_id, path):
    return {"id": stash_id, "organized": False, "tags": [], "performers": [],
            "photographer": None,
            "visual_files": [{"path": path, "basename": os.path.basename(path)}]}


class Row(dict):
    """A source row. Patreon rows carry a path; sqlite ones do not."""


class FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def medias_for_model(self, user_id):
        return self.rows

    def post_for_path(self, path):
        # A scraper database's answer. The real PatreonLibrary's is exercised
        # in test_post_folders.py; here, the name/path matching is under test.
        return None


# --- the collision that caused it -------------------------------------------
# Three posts each hold their own "1.jpg"; Stash has all three as separate
# images. Keyed by basename, all three posts resolved to whichever image won.
PATHS = {
    "priest": CREATOR + "/101 - The Priest's Special Wedding Blessing/images/1.jpg",
    "herc": CREATOR + "/102 - Hercules and Aladdin Comic/images/1.jpg",
    "spider": CREATOR + "/103 - SHIELD ME SPIDER/images/1.jpg",
}
images = [image("img-priest", PATHS["priest"]),
          image("img-herc", PATHS["herc"]),
          image("img-spider", PATHS["spider"])]

rows = [Row(post_id="101", filename="1.jpg", path=PATHS["priest"],
            posted_at="2025-11-05", api_type="image_file"),
        Row(post_id="102", filename="1.jpg", path=PATHS["herc"],
            posted_at="2025-11-06", api_type="image_file"),
        Row(post_id="103", filename="1.jpg", path=PATHS["spider"],
            posted_at="2025-11-07", api_type="image_file")]

groups = sync.group_media_by_post(FakeDB(rows), "mrxtoon", [], images)
assert groups["101"]["images"] == ["img-priest"], groups["101"]
assert groups["102"]["images"] == ["img-herc"], groups["102"]
assert groups["103"]["images"] == ["img-spider"], groups["103"]
# no image is claimed twice -- the actual symptom
claimed = [i for g in groups.values() for i in g["images"]]
assert len(claimed) == len(set(claimed)), claimed


# --- an AMBIGUOUS name must never be guessed at -----------------------------
# Stash not having this exact file does not make a same-named file in another
# post the answer. This is the wrong claim itself.
missing = CREATOR + "/104 - Some Other Comic/images/1.jpg"
rows2 = rows + [Row(post_id="104", filename="1.jpg", path=missing,
                    posted_at="2025-11-08", api_type="image_file")]
groups2 = sync.group_media_by_post(FakeDB(rows2), "mrxtoon", [], images)
assert groups2["104"]["images"] == [], groups2["104"]


# --- a UNIQUE name still falls back, so a path mismatch isn't fatal ---------
# If the Patreon Data Path setting doesn't match the path Stash scanned, every
# path lookup misses. Refusing all of them would turn a recoverable
# misconfiguration into "the sync silently does nothing", so a name that points
# at exactly one post on both sides is still trusted.
elsewhere = [image("img-solo", "/mnt/nas/patreon/mrxtoon/posts/105 - Solo/images/unique.jpg")]
solo_rows = [Row(post_id="105", filename="unique.jpg",
                 path=CREATOR + "/105 - Solo/images/unique.jpg",
                 posted_at="2025-11-09", api_type="image_file")]
solo = sync.group_media_by_post(FakeDB(solo_rows), "mrxtoon", [], elsewhere)
assert solo["105"]["images"] == ["img-solo"], solo

# ...but a name Stash holds twice is ambiguous on ITS side too, and refused
dupe_in_stash = [image("a", "/x/one/same.jpg"), image("b", "/y/two/same.jpg")]
dupe_rows = [Row(post_id="106", filename="same.jpg", path="/nowhere/same.jpg",
                 posted_at="2025-11-10", api_type="image_file")]
dupe = sync.group_media_by_post(FakeDB(dupe_rows), "mrxtoon", [], dupe_in_stash)
assert dupe["106"]["images"] == [], dupe

# paths differing only by separators still match
norm = [image("img-norm", CREATOR + "/107 - Norm/images//a.jpg")]
norm_rows = [Row(post_id="107", filename="a.jpg",
                 path=CREATOR + "/107 - Norm/./images/a.jpg",
                 posted_at="2025-11-11", api_type="image_file")]
assert sync.group_media_by_post(FakeDB(norm_rows), "mrxtoon", [], norm)["107"]["images"] \
    == ["img-norm"]


# --- sources WITHOUT a path still use the basename --------------------------
# OF-Scraper and jff-scraper name files after the media id, so a basename
# identifies a post there and the fallback is the only lookup they have.
class SqliteRow(dict):
    def keys(self):            # no "path" column
        return ["post_id", "filename", "posted_at", "api_type"]


of_images = [image("of-1", "/data/of/user/Posts/Free/9001.jpg")]
of_rows = [SqliteRow(post_id="55", filename="9001.jpg",
                     posted_at="2024-01-02", api_type="Posts")]
of_groups = sync.group_media_by_post(FakeDB(of_rows), 1, [], of_images)
assert of_groups["55"]["images"] == ["of-1"], of_groups


# --- reconcile: undo a wrong claim, leave everything else alone -------------
class FakeClient:
    def __init__(self, contents):
        self.contents = contents
        self.removed = []

    def find_gallery_image_ids(self, gallery_id):
        return list(self.contents[gallery_id])

    def remove_gallery_images(self, gallery_id, image_ids):
        self.removed.append((gallery_id, list(image_ids)))


# The Priest gallery wrongly holds the other two posts' images, plus one the
# plugin has never seen (added by hand).
HAND_ADDED = "img-manual"
owner_of = {"img-priest": "101", "img-herc": "102", "img-spider": "103"}
wrong = {i: p for i, p in owner_of.items() if p != "101"}

c = FakeClient({"g1": ["img-priest", "img-herc", "img-spider", HAND_ADDED]})
totals = {}
sync.reconcile_post_gallery(c, "g1", ["img-priest"], wrong, totals)
assert c.removed == [("g1", ["img-herc", "img-spider"])], c.removed
assert totals["detached"] == 2, totals
# the hand-added image survives: the plugin cannot attribute it, so it is not
# the plugin's to remove
assert HAND_ADDED not in c.removed[0][1]

# nothing to do -> no mutation at all
c = FakeClient({"g1": ["img-priest", HAND_ADDED]})
totals = {}
sync.reconcile_post_gallery(c, "g1", ["img-priest"], wrong, totals)
assert c.removed == [], c.removed
assert totals == {}, totals

# an image this post owns is never detached, even if listed as wanted twice
c = FakeClient({"g1": ["img-priest"]})
sync.reconcile_post_gallery(c, "g1", ["img-priest", "img-priest"], wrong, {})
assert c.removed == []

# with nothing attributable elsewhere the pass does not even read the gallery
class Exploding:
    def find_gallery_image_ids(self, gallery_id):
        raise AssertionError("must not query when there is nothing to detach")

    def remove_gallery_images(self, gallery_id, image_ids):
        raise AssertionError("must not remove")


sync.reconcile_post_gallery(Exploding(), "g1", ["img-priest"], {}, {})

# a failed listing warns rather than removing blindly
class BadList(FakeClient):
    def find_gallery_image_ids(self, gallery_id):
        raise RuntimeError("nope")


c = BadList({"g1": []})
sync.reconcile_post_gallery(c, "g1", ["img-priest"], wrong, {})
assert c.removed == []


# --- the mutation is real, and guarded -------------------------------------
text = open(plugin_file("stash.py"), encoding="utf-8").read()
assert "removeGalleryImages(input: { gallery_id: $id, image_ids: $ids })" in text
assert stash._mutation_field(
    "mutation RemoveGalleryImages($id: ID!, $ids: [ID!]!) "
    "{ removeGalleryImages(input: { gallery_id: $id, image_ids: $ids }) }"
) == "removeGalleryImages", "must be recognised as a mutation, so a dry run skips it"

sync_src = open(plugin_file("sync.py"), encoding="utf-8").read()
# detaching happens on a FULL sync only, and never on a folder gallery
seg = sync_src[sync_src.index("wrong = {}"):]
seg = seg[:seg.index("tasks.append(_update_task)")]
assert "attach and full_sync" in seg, seg
assert "reconcile_post_gallery(client, gid, imgs, wrong, totals)" in seg

print("ALL OK")
