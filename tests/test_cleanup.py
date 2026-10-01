"""Undo metadata written onto media outside the configured libraries.

Stash's `path` filter is a substring match over the WHOLE library, so a creator
name occurring anywhere else pulled unrelated files into that creator's sync --
a torrent download got a Patreon post's title, URL, date, studio, creator
performer, tags and `organized: True`. media_under_data_path stops it happening
again, but nothing re-derives media the plugin can no longer see, so the writes
already made have to be undone deliberately.

The safety property under test: the cleanup acts ONLY on media it can
positively place outside every configured data path.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _plugin import PLUGIN, plugin_file
sys.path.insert(0, PLUGIN)

import sync
import sources

ROOTS = ["/data/media/Patreon", "/data/media/OnlyFans"]
TORRENT = ("/data/torrents/downloads/whisparr/"
           "[Mirenac] The Priest's Special Wedding Blessing Mini Comic/1.png")
INSIDE = "/data/media/Patreon/Mirenac - Mirenac/posts/101 - The Priest's/images/1.png"


# --- is_outside --------------------------------------------------------------
assert sync.is_outside([TORRENT], ROOTS) is True
assert sync.is_outside([INSIDE], ROOTS) is False
# a sibling directory sharing a prefix is still outside
assert sync.is_outside(["/data/media/Patreon-backup/x/1.png"], ROOTS) is True
# any file inside is enough to spare a merged item
assert sync.is_outside([TORRENT, INSIDE], ROOTS) is False
# nothing to place, or nothing to place it against -> never acted on
assert sync.is_outside([], ROOTS) is False
assert sync.is_outside([TORRENT], []) is False
assert sync.is_outside([TORRENT], ["", "  "]) is False
# separator noise in the configured root doesn't matter
assert sync.is_outside([INSIDE], ["/data/media/Patreon/"]) is False
assert sync.is_outside([INSIDE], ["/data//media/./Patreon"]) is False


# --- build_cleanup_update ----------------------------------------------------
P = sources.PATREON
STRAY = {
    "id": "img1",
    "title": "The Priest's Special Wedding Blessing Comic",
    "organized": True,
    "urls": ["https://www.patreon.com/Mirenac/posts/priest-123",
             "https://example.com/mine"],
    "photographer": "",
    "details": "A post body the plugin wrote",
    "date": "2025-11-05",
    "code": "123456",
    "studio": {"id": "s1", "name": "Mirenac (Patreon)"},
    "performers": [{"id": "p1", "name": "Mirenac"},
                   {"id": "p2", "name": "Someone I Added"}],
    "tags": [{"id": "t1", "name": "Patreon"}, {"id": "t2", "name": "paid"},
             {"id": "t3", "name": "my own tag"}],
    "visual_files": [{"path": TORRENT}],
}

u = sync.build_cleanup_update(STRAY, "image", P, "Mirenac", None)
assert u["id"] == "img1"
assert u["studio_id"] is None, u
assert u["title"] == "", u
assert u["organized"] is False, u
assert u["details"] == "" and u["code"] == "" and u["date"] is None, u
# only the site's URL is dropped; the user's own survives
assert u["urls"] == ["https://example.com/mine"], u["urls"]
# only the creator performer is dropped
assert u["performer_ids"] == ["p2"], u["performer_ids"]
# only the plugin's tags are dropped
assert u["tag_ids"] == ["t3"], u["tag_ids"]
# an image gets `photographer`, never `director` -- the field doesn't exist there
assert "director" not in u, u

scene_stray = dict(STRAY, director="Mirenac", photographer=None,
                   files=[{"path": TORRENT}])
del scene_stray["visual_files"]
su = sync.build_cleanup_update(scene_stray, "scene", P, "Mirenac", None)
assert su["director"] == "", su
assert "photographer" not in su, su

# media carrying nothing of ours is skipped outright (see the no-op case below)
clean = {"id": "x", "title": "", "organized": False, "urls": [],
         "details": "", "date": None, "code": "",
         "studio": None, "performers": [], "tags": [], "visual_files": []}
assert sync.build_cleanup_update(clean, "image", P, "Mirenac", None) is None


# --- fields are cleared only when they hold something ------------------------
# Every field sent is a field overwritten. Some of this media was re-curated by
# hand AFTER the bad sync, and a blind clear would wipe that repair.
partial = dict(STRAY, id="img3", details="", date=None, code="",
               title="", organized=False, photographer="")
u3 = sync.build_cleanup_update(partial, "image", P, "Mirenac", None)
for absent in ("details", "date", "code", "title", "organized", "photographer"):
    assert absent not in u3, (absent, u3)
# ...but what IS ours still goes
assert u3["studio_id"] is None and u3["tag_ids"] == ["t3"], u3


# --- media carrying nothing of ours is skipped entirely ----------------------
# Returning an update with only an id would send a write that changes nothing.
nothing = {"id": "img4", "title": "", "organized": False, "urls": [],
           "details": "", "date": None, "code": "",
           "studio": None, "performers": [], "tags": [], "visual_files": []}
assert sync.build_cleanup_update(nothing, "image", P, "Mirenac", None) is None

# a media whose only trace is the studio still gets cleaned
studio_only = dict(nothing, id="img5", studio={"id": "s1", "name": "Mirenac (Patreon)"})
u5 = sync.build_cleanup_update(studio_only, "image", P, "Mirenac", None)
assert u5 == {"id": "img5", "studio_id": None}, u5


# --- a MERGED item is spared when any file is inside a library ---------------
# Stash merges byte-identical files into one scene, so a Patreon video and a
# torrent copy of it become a single scene with two paths. That scene is
# library media and must not be cleaned.
assert sync.is_outside([TORRENT, INSIDE], ROOTS) is False
assert sync.is_outside([INSIDE, TORRENT], ROOTS) is False
assert sync.is_outside([TORRENT, TORRENT + ".dup"], ROOTS) is True


# --- the pass only touches what is outside ----------------------------------
class FakeClient:
    def __init__(self, media):
        self.media = media
        self.updated = []

    def find_media_under_studio(self, studio_id, kind):
        return self.media.get(kind, [])

    def update_scene(self, update):
        self.updated.append(("scene", update))

    def update_image(self, update):
        self.updated.append(("image", update))


inside_item = dict(STRAY, id="img-inside", visual_files=[{"path": INSIDE}])
client = FakeClient({"image": [STRAY, inside_item], "scene": []})

P.parent_id = "parent1"
configured = [(P, "/data/media/Patreon")]
totals = {"scenes": 0, "images": 0, "galleries": 0, "skipped": 0,
          "skipped_multifile": 0}
sync.cleanup_stray_media(client, configured, None, 1, totals)

assert len(client.updated) == 1, client.updated
kind, update = client.updated[0]
assert kind == "image" and update["id"] == "img1", client.updated
assert totals["images"] == 1, totals

# The studio must reach the log and the tally: it names the creator whose
# handle matched a path the user doesn't recognise, which is the only thing
# that explains why the file was touched at all.
src = open(plugin_file("sync.py"), encoding="utf-8").read()
assert "def plugin_wrote_this" in src
assert "plugin_wrote_this(item, source)" in src

# no parent studio resolved -> the plugin can't identify its own writes, so it
# does nothing rather than guessing
P.parent_id = None
c2 = FakeClient({"image": [STRAY], "scene": []})
sync.cleanup_stray_media(c2, configured, None, 1,
                         dict(totals, images=0))
assert c2.updated == [], c2.updated
P.parent_id = None

# no configured paths -> refuses outright, since everything would look outside
c3 = FakeClient({"image": [STRAY], "scene": []})
sync.cleanup_stray_media(c3, [(P, "")], None, 1, dict(totals))
assert c3.updated == [], c3.updated


# --- a studio under the parent is NOT evidence the plugin wrote it ----------
# Observed in a real library: the user had filed their OWN studios under
# "OnlyFans (network)" -- ZacknRhys, Gang Bang Guys, Juice Anime and more,
# holding hand-curated torrented media this plugin never touched. Selecting on
# the studio tree alone swept 250+ of those into the cleanup, which would have
# stripped titles, dates, tags and the organized flag from media whose only
# sin was being filed sensibly.
OF = sources.ONLYFANS

# The user's own: a studio under the parent, and nothing else of ours.
theirs = {"id": "91394", "title": "SEND ME STARS 7", "organized": True,
          "urls": [], "tags": [{"id": "t9", "name": "anime"}],
          "performers": [], "studio": {"id": "s9", "name": "Juice Anime"},
          "files": [{"path": "/torrents/downloads/prowlarr/Juice Anime "
                             "Patreon Collection/Juice Anime 3/x.mp4"}]}
assert sync.plugin_wrote_this(theirs, OF) is False
assert sync.plugin_wrote_this(theirs, P) is False

# even media filed directly under the parent studio itself
parent_filed = dict(theirs, id="p1", studio={"id": "s0", "name": "OnlyFans (network)"})
assert sync.plugin_wrote_this(parent_filed, OF) is False

# --- the plugin's own fingerprint DOES qualify ------------------------------
# build_update always writes the site tag, and a post URL when one exists.
by_url = dict(theirs, id="u1",
              urls=["https://www.patreon.com/Mirenac/posts/priest-123"])
assert sync.plugin_wrote_this(by_url, P) is True
# ...but that URL is Patreon's, so it is not evidence for OnlyFans
assert sync.plugin_wrote_this(by_url, OF) is False

# The site TAG is deliberately not enough. Torrented OnlyFans content filed
# under "<creator> (OnlyFans)" may reasonably be tagged "OnlyFans" too -- and
# those studios really are OnlyFans creators, so neither the studio name nor
# the tag separates the user's work from the plugin's.
by_tag = dict(theirs, id="t1", tags=[{"id": "t1", "name": "Patreon"},
                                     {"id": "t9", "name": "anime"}])
assert sync.plugin_wrote_this(by_tag, P) is False
assert sync.plugin_wrote_this(by_tag, OF) is False

# --- and the pass acts on that, not on the studio ---------------------------
mine = dict(STRAY, id="img-ours")          # has a patreon.com url and site tag
yours = {"id": "scene-yours", "title": "Keep me", "organized": True,
         "urls": [], "tags": [{"id": "t9", "name": "anime"}], "performers": [],
         "details": "mine", "date": "2020-01-01", "code": "",
         "studio": {"id": "s9", "name": "Juice Anime"},
         "visual_files": [{"path": TORRENT}]}

P.parent_id = "parent1"
client = FakeClient({"image": [mine, yours], "scene": []})
totals2 = {"scenes": 0, "images": 0, "galleries": 0, "skipped": 0,
           "skipped_multifile": 0}
sync.cleanup_stray_media(client, [(P, "/data/media/Patreon")], None, 1, totals2)
touched = [u["id"] for _k, u in client.updated]
assert touched == ["img-ours"], touched
assert "scene-yours" not in touched
P.parent_id = None


# --- a stash-box id makes it somebody else's work ---------------------------
# Scene 111530 in a real library: a torrented OnlyFans scene the user matched
# to FansDB. The scrape gave it onlyfans.com/1026372101/onlydurden and a studio
# named "onlydurden (OnlyFans)" -- the same shape this plugin writes, so the
# URL cannot tell them apart. The stash-box id can: this plugin never sets one.
scraped = {"id": "111530",
           "title": "Have you guys seen this episode of Spider-Man",
           "organized": True,
           "urls": ["https://onlyfans.com/1026372101/onlydurden"],
           "tags": [{"id": "t1", "name": "Anal"}],
           "performers": [{"id": "p1", "name": "Rusty Taylor"}],
           "studio": {"id": "s1", "name": "onlydurden (OnlyFans)"},
           "stash_ids": [{"stash_id": "da767a76-7836-4cbf-a2ba-1f97331db33e",
                          "endpoint": "https://fansdb.cc/graphql"}],
           "files": [{"path": TORRENT}]}
assert sync.plugin_wrote_this(scraped, OF) is False

# the identical scene WITHOUT a stash-box id is the plugin's to revert
assert sync.plugin_wrote_this(dict(scraped, stash_ids=[]), OF) is True
assert sync.plugin_wrote_this(
    {k: v for k, v in scraped.items() if k != "stash_ids"}, OF) is True

# and the pass acts on that. Run under the OnlyFans profile, since that is the
# domain these URLs carry -- a Patreon profile would reject both on the URL.
OF.parent_id = "parentOF"
c = FakeClient({"scene": [scraped, dict(scraped, id="no-id", stash_ids=[])],
                "image": []})
sync.cleanup_stray_media(c, [(OF, "/data/media/OnlyFans")], None, 1,
                         {"scenes": 0, "images": 0, "galleries": 0,
                          "skipped": 0, "skipped_multifile": 0})
assert [u["id"] for _k, u in c.updated] == ["no-id"], c.updated
OF.parent_id = None

# the query has to ask for the field, or every scene looks unidentified
assert "stash_ids { stash_id endpoint }" in open(
    plugin_file("stash.py"), encoding="utf-8").read()


# --- a reverted stray is also taken out of the plugin's own galleries -------
# Clearing the metadata does not undo MEMBERSHIP. The gallery passes only ever
# add, and reconcile_post_gallery can only detach an image when the same run
# can name the post that really owns it -- which it never can for a file that
# belongs to no post at all. So a cleaned torrent image stayed a member of the
# post gallery that wrongly claimed it; one was observed in four unrelated
# comics' galleries.
POST_GAL = {"id": "g1", "title": "The Priest's",
            "urls": ["https://www.patreon.com/Mirenac/posts/priest-123"],
            "folder": None}
OTHER_POST_GAL = {"id": "g2", "title": "Some other post",
                  "urls": ["https://www.patreon.com/Mirenac/posts/other-9"],
                  "folder": None}
FOLDER_GAL = {"id": "g3", "title": "Mirenac Patreon images",
              "urls": ["https://www.patreon.com/Mirenac/posts/priest-123"],
              "folder": {"path": "/data/media/Patreon/Mirenac - Mirenac"}}
HAND_GAL = {"id": "g4", "title": "Comics I like", "urls": [], "folder": None}

assert sync.plugin_made_gallery(POST_GAL, P) is True
# a folder gallery is Stash's -- it owns the contents and rejects membership
# changes, and an earlier sync may have stamped the post URL onto it, so the
# URL alone would wrongly select it
assert sync.plugin_made_gallery(FOLDER_GAL, P) is False
# absence of a folder is not enough on its own: a hand-made gallery has none
assert sync.plugin_made_gallery(HAND_GAL, P) is False
# the domain has to be this site's
assert sync.plugin_made_gallery(POST_GAL, OF) is False

in_galleries = dict(STRAY, id="img-gal",
                    galleries=[POST_GAL, OTHER_POST_GAL, FOLDER_GAL, HAND_GAL])
ug = sync.build_cleanup_update(in_galleries, "image", P, "Mirenac", None)
assert ug["gallery_ids"] == ["g3", "g4"], ug["gallery_ids"]

# nothing of ours among them -> the field is not sent at all, so the write
# can't disturb memberships it isn't responsible for
untouched = dict(STRAY, id="img-gal2", galleries=[FOLDER_GAL, HAND_GAL])
assert "gallery_ids" not in sync.build_cleanup_update(
    untouched, "image", P, "Mirenac", None)
# ...and membership alone is not a reason to write: a media carrying only a
# plugin gallery still gets cleaned, but one carrying nothing is still skipped
only_gal = dict(nothing, id="img-gal3", galleries=[POST_GAL])
assert sync.build_cleanup_update(only_gal, "image", P, "Mirenac", None) == {
    "id": "img-gal3", "gallery_ids": []}

# scenes relate to galleries too (the plugin links a post's scene to the post
# gallery), and SceneUpdateInput takes gallery_ids just as ImageUpdateInput does
scene_gal = dict(scene_stray, id="scene-gal", galleries=[POST_GAL, HAND_GAL])
sg = sync.build_cleanup_update(scene_gal, "scene", P, "Mirenac", None)
assert sg["gallery_ids"] == ["g4"], sg["gallery_ids"]

# the queries have to ask for the galleries, and for `folder` -- without the
# latter every folder gallery carrying a stamped post URL looks like ours
stash_src = open(plugin_file("stash.py"), encoding="utf-8").read()
assert stash_src.count("galleries { id title urls folder { path } }") == 2, \
    "both the scene and the image cleanup query"


# --- the tasks exist, and the preview is a dry run --------------------------
manifest = open(plugin_file("of-stash-sync.yml"), encoding="utf-8").read()
assert "mode: cleanup" in manifest
assert manifest.count("mode: cleanup") == 2, "a preview and a real run"
preview = manifest[manifest.index("Preview Cleanup of Stray Media"):]
preview = preview[:preview.index("- name: Clean Up Stray Media")]
assert "dryRun: true" in preview, preview

print("ALL OK")
