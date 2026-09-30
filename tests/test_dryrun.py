"""Dry run: mutations logged and skipped, reads still real."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _plugin import PLUGIN, plugin_file
sys.path.insert(0, PLUGIN)
import stash

SERVER = {"Scheme": "http", "Port": 9999, "SessionCookie": {"Value": "c"}}


def client(dry):
    c = stash.StashClient(SERVER, dry_run=dry)
    c._sent = []
    # Any real network attempt fails loudly, so anything that "succeeds" in dry
    # run must have been short-circuited rather than sent.
    def boom(*a, **k):
        raise AssertionError("a request left the process during a dry run")
    c.__dict__["_transport"] = boom
    return c


# --- mutation field extraction ---------------------------------------------
assert stash._mutation_field(
    "mutation SceneUpdate($input: SceneUpdateInput!) { sceneUpdate(input: $input) { id } }"
) == "sceneUpdate"
assert stash._mutation_field(
    "\n        mutation GalleryCreate($input: GalleryCreateInput!) {\n"
    "            galleryCreate(input: $input) { id }\n        }\n"
) == "galleryCreate"
assert stash._mutation_field("mutation AddGalleryImages($id: ID!, $ids: [ID!]!) "
                             "{ addGalleryImages(gallery_id: $id, image_ids: $ids) }"
                             ) == "addGalleryImages"
# reads must NOT be treated as mutations -- a preview has to see the real library
assert stash._mutation_field("query { configuration { plugins } }") is None
assert stash._mutation_field("{ findScenes { count } }") is None
# a read that merely mentions the word
assert stash._mutation_field("query FindTags { findTags(filter: $f) { tags { id } } }") is None


# --- dry run short-circuits writes and returns a usable shape ---------------
c = client(True)
out = c.call("mutation PerformerCreate($input: PerformerCreateInput!) "
             "{ performerCreate(input: $input) { id name } }",
             {"input": {"name": "BrandCo", "urls": ["https://x/BrandCo"]}})
assert out == {"performerCreate": {"id": stash.DRY_RUN_ID}}, out
# so create_* callers keep going instead of erroring through the whole run
assert out["performerCreate"]["id"] == stash.DRY_RUN_ID

out = c.call("mutation GalleryCreate($input: GalleryCreateInput!) "
             "{ galleryCreate(input: $input) { id } }",
             {"input": {"title": "Moano & Ariel", "scene_ids": ["s1", "s2"],
                        "performer_ids": ["p1"], "tag_ids": ["t1"],
                        "organized": True}})
assert out["galleryCreate"]["id"] == stash.DRY_RUN_ID

# variables without an "input" wrapper still summarise (addGalleryImages)
out = c.call("mutation AddGalleryImages($id: ID!, $ids: [ID!]!) "
             "{ addGalleryImages(gallery_id: $id, image_ids: $ids) }",
             {"id": "g1", "ids": ["i1", "i2", "i3"]})
assert out == {"addGalleryImages": {"id": stash.DRY_RUN_ID}}, out


# --- the sentinel id must be usable where a REAL id is -----------------------
# A simulated create hands this id back, and the caller can pass it to a *read*
# (a new studio's id goes straight into find_galleries_for_studio). Stash
# splices ids into SQL, so a non-numeric sentinel produced
# "studio_id IN (VALUES(dry-run))" -> "no such column: dry" and killed the
# whole creator's preview. It has to look like an id and match nothing.
assert stash.DRY_RUN_ID.isdigit(), stash.DRY_RUN_ID
assert int(stash.DRY_RUN_ID) == 0, stash.DRY_RUN_ID
# ...and it reads as "(new)" rather than a confusing bare 0
assert "studio=(new)" in stash._summarize({"studio_id": stash.DRY_RUN_ID})
assert "studio=382" in stash._summarize({"studio_id": "382"})


# --- the summary names what would change -----------------------------------
s = stash._summarize({"id": "42", "title": "Beach day", "date": "2024-01-02",
                      "urls": ["https://onlyfans.com/9/u"], "studio_id": "s1",
                      "performer_ids": ["a", "b"], "tag_ids": ["t"],
                      "details": "x" * 30, "organized": True})
for expect in ["id=42", "title='Beach day'", "date='2024-01-02'",
               "url=https://onlyfans.com/9/u", "studio=s1", "performers=2",
               "tags=1", "details[30]", "organized"]:
    assert expect in s, (expect, s)
assert stash._summarize({}) == "(no fields)"
assert stash._summarize(None) == "(no fields)"
assert "images=3" in stash._summarize({"id": "g1", "ids": ["a", "b", "c"]})


# --- with dry run OFF a mutation is NOT short-circuited --------------------
c = client(False)
try:
    c.call("mutation SceneUpdate($input: SceneUpdateInput!) "
           "{ sceneUpdate(input: $input) { id } }", {"input": {"id": "1"}})
except Exception as e:
    # it tried to reach Stash, which is the point
    assert "connection" in str(e).lower() or "timed out" in str(e).lower(), e
else:
    raise AssertionError("expected a real request attempt with dry_run off")

print("ALL OK")
