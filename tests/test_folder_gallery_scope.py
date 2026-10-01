"""Folder galleries are confined to the data path, and cleanup reverts strays.

sync_folder_galleries looked folder galleries up with Stash's SUBSTRING path
filter over the whole library, and narrowed the result only by requiring the
creator's name as a whole path SEGMENT. That stops `jake` claiming `jakeson`,
but it is not confinement: a torrent folder named after the creator passes it,
and was adopted like the user's own -- creator studio, creator performer, site
tag, a "<creator> OnlyFans <dir>" title, and organized. The per-post and
collection lookups had the same hole for a folder gallery carrying a stamped
post URL. Images already had this guard (media_under_data_path); galleries did
not.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _plugin import PLUGIN, plugin_file
sys.path.insert(0, PLUGIN)

import sync
import sources

OF = sources.ONLYFANS
P = sources.PATREON
OF.data_path = "/data/media/OnlyFans"

MINE = {"id": "g-mine", "title": "", "organized": False,
        "folder": {"path": "/data/media/OnlyFans/onlydurden/Posts/Free/Images"}}
TORRENT = {"id": "g-torrent", "title": "", "organized": False,
           "folder": {"path": "/torrents/onlyfans/onlydurden/Pack 3"}}
PLUGIN_MADE = {"id": "g-plugin", "folder": None, "files": []}


# --- the predicate -----------------------------------------------------------
assert sync.gallery_outside_data_path(MINE, OF) is False
assert sync.gallery_outside_data_path(TORRENT, OF) is True
# a plugin-made gallery has no location, so it is never "outside"
assert sync.gallery_outside_data_path(PLUGIN_MADE, OF) is False
# a zip gallery is judged by its file
zip_out = {"id": "z", "folder": None,
           "files": [{"path": "/torrents/onlyfans/onlydurden.zip"}]}
assert sync.gallery_outside_data_path(zip_out, OF) is True
# no configured path -> nothing to confine to, leave alone
P.data_path = ""
assert sync.gallery_outside_data_path(TORRENT, P) is False


# --- sync_folder_galleries only adopts the library's own folders ------------
class FakeClient:
    def __init__(self, galleries):
        self.galleries = galleries
        self.updated = []

    def find_folder_galleries(self, username):
        return self.galleries

    def update_gallery(self, update):
        self.updated.append(update)


client = FakeClient([MINE, TORRENT])
totals = {"galleries": 0}
sync.sync_folder_galleries(client, {"username": "onlydurden"}, "s1", ["p1"],
                           None, True, 1, totals, OF)
touched = [u["id"] for u in client.updated]
assert touched == ["g-mine"], touched

# both by-url lookups skip an outside folder gallery too
src = open(plugin_file("sync.py"), encoding="utf-8").read()
for fn in ("def build_post_galleries", "def sync_collection_galleries"):
    body = src[src.index(fn):]
    body = body[:body.index("\ndef ")]
    assert "gallery_outside_data_path(gal, source)" in body, fn
# ...and the lookup fetches what that needs
stash_src = open(plugin_file("stash.py"), encoding="utf-8").read()
q = stash_src[stash_src.index("def find_galleries_for_studio"):]
q = q[:q.index("def find_galleries_under_studio")]
assert "folder { path } files { path }" in q, q


# --- what counts as the plugin's work on a folder gallery -------------------
adopted_title = sync.folder_gallery_title(
    "onlydurden", TORRENT["folder"]["path"], OF)
adopted = dict(TORRENT, title=adopted_title, organized=True,
               studio={"id": "s1", "name": "onlydurden (OnlyFans)"},
               performers=[{"id": "p1", "name": "onlydurden"},
                           {"id": "p2", "name": "Someone I added"}],
               tags=[{"id": "t1", "name": "OnlyFans"},
                     {"id": "t2", "name": "my tag"}],
               details="my own notes", date="2021-01-01", code="",
               urls=[])
assert sync.plugin_adopted_gallery(adopted, OF) is True

# the user retitled it -> not ours any more, even with our studio on it
retitled = dict(adopted, title="Durden pack 3")
assert sync.plugin_adopted_gallery(retitled, OF) is False

# a post URL on the site's domain is the per-post pass's fingerprint
stamped = dict(retitled, urls=["https://onlyfans.com/123/onlydurden"])
assert sync.plugin_adopted_gallery(stamped, OF) is True


# --- the revert follows the evidence ----------------------------------------
# Adopted by title: sync_folder_galleries only ever sets title, studio, the
# creator performer, the site tag and organized, so the details and date are
# the user's and must survive.
u = sync.build_gallery_cleanup_update(adopted, OF, "onlydurden")
assert u == {"id": "g-torrent", "title": "", "studio_id": None,
             "organized": False, "performer_ids": ["p2"],
             "tag_ids": ["t2"]}, u

# Stamped with a post URL: the per-post pass wrote the post's metadata
# wholesale, so it is reverted like media -- but a gallery photographer is
# never the plugin's, so it stays.
stamped_full = dict(stamped, details="post body", photographer="Kept")
su = sync.build_gallery_cleanup_update(stamped_full, OF, "onlydurden")
assert su["urls"] == [] and su["details"] == "", su
assert "photographer" not in su, su
assert "gallery_ids" not in su, su


# --- the cleanup pass acts on exactly that -----------------------------------
class CleanupClient:
    def __init__(self, galleries):
        self.galleries = galleries
        self.updated = []

    def find_media_under_studio(self, studio_id, kind):
        return []

    def find_galleries_under_studio(self, studio_id):
        return self.galleries

    def update_gallery(self, update):
        self.updated.append(update)


inside_adopted = dict(adopted, id="g-inside",
                      folder={"path": MINE["folder"]["path"]})
inside_adopted["title"] = sync.folder_gallery_title(
    "onlydurden", MINE["folder"]["path"], OF)
plugin_made = {"id": "g-made", "title": "A post", "organized": True,
               "urls": ["https://onlyfans.com/9/onlydurden"],
               "studio": {"id": "s1", "name": "onlydurden (OnlyFans)"},
               "folder": None, "files": []}

OF.parent_id = "parentOF"
cc = CleanupClient([adopted, retitled, inside_adopted, plugin_made])
t = {"scenes": 0, "images": 0, "galleries": 0, "skipped": 0,
     "skipped_multifile": 0}
sync.cleanup_stray_media(cc, [(OF, "/data/media/OnlyFans")], None, 1, t)
# only the adopted torrent folder: the retitled one is the user's, the inside
# one is legitimately adopted, the plugin-made one has no location at all
assert [u["id"] for u in cc.updated] == ["g-torrent"], cc.updated
assert t["galleries"] == 1, t
OF.parent_id = None

print("ALL OK")
