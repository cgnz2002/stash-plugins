"""Collection galleries: flattened member media, keyed by URL."""
import json
import os
import shutil
import sys
import tempfile

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _plugin import PLUGIN, plugin_file
sys.path.insert(0, PLUGIN)
import media
import patreon_source as ps
import sources
import sync

root = tempfile.mkdtemp()
proc = media.MediaProcessor(65)


def w(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


INFO = "ID: {pid}\nType: image_file\nTitle: {t}\nContent: <p>body</p>\nPublished: 2026-09-29T07:05:00.000+00:00\nURL: https://www.patreon.com/posts/{pid}\n"

creator = os.path.join(root, "mrxtoon - Mr. X-Toon")
# two member posts: one with an image, one with a video
for pid, title, folder, fname in [
    ("169450662", "Ball Worship", "images", "ball.jpg"),
    ("38354242", "Preview", "attachments", "preview.mp4"),
]:
    p = os.path.join(creator, "posts", "{} - {}".format(pid, title))
    w(os.path.join(p, "post_info", "info.txt"), INFO.format(pid=pid, t=title))
    w(os.path.join(p, "post_info", "cover-image.jpg"))
    w(os.path.join(p, folder, fname))

# the collection, as patreon-dl writes it
coll_doc = {"data": {"type": "collection", "id": "2379775", "attributes": {
    "title": "Moano & Ariel \U0001F30A",
    "description": "<p>The <b>series</b></p>",
    "created_at": "2026-01-05T00:00:00.000+00:00",
    "post_ids": ["169450662", "38354242", "999999"],   # last one not in Stash
}}}
w(os.path.join(creator, "collections", "2379775 - Moano & Ariel", "c.json"),
  json.dumps(coll_doc))

lib = ps.PatreonLibrary(creator)
colls = lib.collections()
assert len(colls) == 1, colls
assert colls[0]["title"] == "Moano & Ariel \U0001F30A", colls[0]
assert colls[0]["vanity"] == "mrxtoon", colls[0]

# OF/JFF have no such concept and must degrade quietly to empty (the method
# ignores self, so an unbound call is a fair check without standing up sqlite)
assert sources.ONLYFANS.reader.collections(None) == []
assert sources.JUSTFORFANS.reader.collections(None) == []


class FakeClient:
    def __init__(self, existing=()):
        self.existing = list(existing)
        self.created = []
        self.updated = []
        self.attached = []

    def find_galleries_for_studio(self, studio_id):
        return self.existing

    def create_gallery(self, gi):
        self.created.append(gi)
        return "new-gal"

    def update_gallery(self, gi):
        self.updated.append(gi)

    def add_gallery_images(self, gid, ids):
        self.attached.append((gid, list(ids)))


class FakeTags:
    def resolve(self, name):
        return "tag-" + name


# Stash's view: the image and the video of the two member posts
all_images = [{"id": "img1", "visual_files": [{"basename": "ball.jpg"}]}]
all_scenes = [{"id": "sc1", "files": [{"path": "/data/patreon/x/preview.mp4"}]}]
profile = {"username": "mrxtoon", "user_id": "mrxtoon"}
totals = {"galleries": 0, "scenes": 0, "images": 0, "skipped": 0, "skipped_multifile": 0}

client = FakeClient()
sync.sync_collection_galleries(
    client, lib, profile, proc, None, None, FakeTags(), "studio1", ["p1"],
    False, 1, all_scenes, all_images, totals, sources.PATREON,
)

assert len(client.created) == 1, client.created
gi = client.created[0]
assert gi["title"] == "Moano & Ariel \U0001F30A", gi["title"]
assert gi["code"] == "2379775"
assert gi["urls"] == ["https://www.patreon.com/collection/2379775"], gi["urls"]
assert gi["details"] == "The series", gi["details"]
assert gi["date"] == "2026-01-05", gi["date"]
assert gi["studio_id"] == "studio1"
assert gi["performer_ids"] == ["p1"]
assert gi["tag_ids"] == ["tag-Patreon"], gi["tag_ids"]
# BOTH kinds of member media: images attached, scenes linked
assert gi["scene_ids"] == ["sc1"], gi["scene_ids"]
assert client.attached == [("new-gal", ["img1"])], client.attached
assert totals["galleries"] == 1

# --- keyed by URL: a renamed collection updates, never duplicates -----------
existing = [{"id": "gal9", "urls": ["https://www.patreon.com/collection/2379775"],
             "title": "Old Name", "performers": [], "tags": []}]
client = FakeClient(existing)
sync.sync_collection_galleries(
    client, lib, profile, proc, None, None, FakeTags(), "studio1", ["p1"],
    True, 1, all_scenes, all_images, totals, sources.PATREON,
)
assert client.created == [], client.created          # no duplicate
assert len(client.updated) == 1, client.updated
assert client.updated[0]["id"] == "gal9"
assert client.updated[0]["title"] == "Moano & Ariel \U0001F30A"

# --- a plain sync fills images but doesn't rewrite metadata -----------------
client = FakeClient(existing)
sync.sync_collection_galleries(
    client, lib, profile, proc, None, None, FakeTags(), "studio1", ["p1"],
    False, 1, all_scenes, all_images, totals, sources.PATREON,
)
assert client.updated == [], client.updated
assert client.attached == [("gal9", ["img1"])], client.attached

# --- a collection whose posts aren't synced yet creates nothing ------------
client = FakeClient()
sync.sync_collection_galleries(
    client, lib, profile, proc, None, None, FakeTags(), "studio1", ["p1"],
    False, 1, [], [], totals, sources.PATREON,
)
assert client.created == [] and client.attached == [], (client.created, client.attached)

shutil.rmtree(root)
print("ALL OK")
