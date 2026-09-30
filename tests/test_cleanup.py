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


# --- the tasks exist, and the preview is a dry run --------------------------
manifest = open(plugin_file("of-stash-sync.yml"), encoding="utf-8").read()
assert "mode: cleanup" in manifest
assert manifest.count("mode: cleanup") == 2, "a preview and a real run"
preview = manifest[manifest.index("Preview Cleanup of Stray Media"):]
preview = preview[:preview.index("- name: Clean Up Stray Media")]
assert "dryRun: true" in preview, preview

print("ALL OK")
