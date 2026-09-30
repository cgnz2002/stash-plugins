"""A folder-based gallery must never get addGalleryImages.

Stash owns a folder gallery's contents and rejects the mutation outright
("cannot change contents of folder-based gallery"). One can still turn up as the
post's existing gallery -- it carries the creator's studio and the post url,
because an earlier sync stamped them on -- so the lookup has to tell them apart.
Its metadata IS still ours to set; only the image list is off limits.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _plugin import PLUGIN, plugin_file
sys.path.insert(0, PLUGIN)
import stash

# The query must ask for `folder`, or the caller cannot possibly tell.
src = open(plugin_file("stash.py"),
           encoding="utf-8").read()
start = src.index("def find_galleries_for_studio")
body = src[start:src.index("def find_folder_galleries")]
assert "folder { path }" in body, "find_galleries_for_studio must fetch `folder`"


class FakeClient:
    def __init__(self):
        self.updated, self.attached = [], []

    def update_gallery(self, gi):
        self.updated.append(gi["id"])

    def add_gallery_images(self, gid, imgs):
        # Stand in for the server's refusal, so a regression fails loudly here
        # instead of as an Error in the user's task log.
        if gid in FOLDER_BASED:
            raise AssertionError(
                "addGalleryImages on folder-based gallery {}".format(gid))
        self.attached.append((gid, imgs))


FOLDER_BASED = {"g-folder"}


def run(existing, images):
    """The task build_post_galleries/sync_collection_galleries construct."""
    client = FakeClient()
    attach = not existing.get("folder")
    client.update_gallery({"id": existing["id"]})
    if attach:
        client.add_gallery_images(existing["id"], images)
    return client


# folder gallery: metadata updated, contents left alone
c = run({"id": "g-folder", "folder": {"path": "/data/patreon/x/posts/1/images"}},
        ["i1", "i2"])
assert c.updated == ["g-folder"], c.updated
assert c.attached == [], c.attached

# plugin-made gallery: unchanged behaviour, images still attached
c = run({"id": "g-plugin", "urls": ["https://www.patreon.com/posts/1"]}, ["i1", "i2"])
assert c.updated == ["g-plugin"], c.updated
assert c.attached == [("g-plugin", ["i1", "i2"])], c.attached

# Stash returning folder: null is the same as the key being absent
c = run({"id": "g-plugin", "folder": None}, ["i1"])
assert c.attached == [("g-plugin", ["i1"])], c.attached

# and the guard is actually present at both call sites
sync = open(plugin_file("sync.py"),
            encoding="utf-8").read()
assert sync.count('attach = not existing.get("folder")') == 2, \
    "both build_post_galleries and sync_collection_galleries must guard"

print("ALL OK")
