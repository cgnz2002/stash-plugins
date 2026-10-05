"""Patreon writes post metadata onto Stash's own folder/zip galleries.

patreon-dl downloads every post into a folder of its own, so the gallery Stash
makes from that folder already IS the post's gallery. The plugin used to build
a second one beside it -- every multi-image post in Stash twice -- and the
built copy's membership was the plugin's to get wrong (one image ended up in
four unrelated comics' galleries). Now the post's metadata goes onto Stash's
galleries, whose contents Stash owns and the plugin cannot touch.

Also covers images Stash reads out of a post's zip: their path runs THROUGH
the zip, so no disk walk ever indexed them, but structurally they can belong
to nothing but that post.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _plugin import PLUGIN, plugin_file
sys.path.insert(0, PLUGIN)
import media
import patreon_source as ps
import source_database
import sources
import sync

root = tempfile.mkdtemp()
proc = media.MediaProcessor(65, [])


def w(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def info(post_id, title):
    return ("ID: {0}\nType: image_file\nTitle: {1}\nContent: <p>{1} body</p>\n"
            "Published: 2025-11-05T10:00:00.000+00:00\n"
            "URL: https://www.patreon.com/posts/x-{0}\n").format(post_id, title)


creator = os.path.join(root, "mirenac - Mirenac")
A = os.path.join(creator, "posts", "101 - The Priest's Blessing")
B = os.path.join(creator, "posts", "102 - Hercules Comic")
w(os.path.join(A, "post_info", "info.txt"), info(101, "The Priest's Blessing"))
w(os.path.join(A, "post_info", "cover-image.jpg"))
w(os.path.join(A, "images", "1.png"))
w(os.path.join(A, "images", "2.png"))
w(os.path.join(A, "attachments", "extra.png"))
w(os.path.join(B, "post_info", "info.txt"), info(102, "Hercules Comic"))
w(os.path.join(B, "attachments", "pages.zip"))

lib = ps.PatreonLibrary(creator)

# --- post_for_path -----------------------------------------------------------
assert lib.post_for_path(os.path.join(A, "images")) == "101"
assert lib.post_for_path(os.path.join(A, "images", "1.png")) == "101"
assert lib.post_for_path(os.path.join(A, "attachments")) == "101"
# a zip, and an image Stash read out of it -- a path that exists nowhere on disk
ZIP = os.path.join(B, "attachments", "pages.zip")
IN_ZIP = os.path.join(ZIP, "01.png")
assert lib.post_for_path(ZIP) == "102"
assert lib.post_for_path(IN_ZIP) == "102"
# covers and thumbnails are not the post's media, for galleries as for images
assert lib.post_for_path(os.path.join(A, "post_info")) is None
assert lib.post_for_path(os.path.join(A, "post_info", "cover-image.jpg")) is None
assert lib.post_for_path(os.path.join(A, ".thumbnails", "t.jpg")) is None
# outside every post folder
assert lib.post_for_path(creator) is None
assert lib.post_for_path(os.path.join(creator, "collections", "c.jpg")) is None
assert lib.post_for_path("/torrents/whisparr/[Mirenac] x/1.png") is None
assert lib.post_for_path("") is None

# the zip image gets a row for its post -- path-first, never the basename,
# which post A may well share ("01.png" / "1.png" are everywhere)
row = lib.media_for_path("mirenac", IN_ZIP)
assert row["post_id"] == "102" and row["path"] == IN_ZIP, row
assert lib.media_for_path("mirenac", "/elsewhere/01.png") is None

# scraper databases degrade to None, like hashtags()/tier()
assert source_database.SourceDatabase.post_for_path(None, "/x") is None
assert source_database.SourceDatabase.media_for_path(None, "u", "/x") is None

# process_profile asks for the post folder BETWEEN path and basename
src = open(plugin_file("sync.py"), encoding="utf-8").read()
i_path = src.index("db.media_by_path(user_id, path)")
i_folder = src.index("db.media_for_path(user_id, path)")
i_name = src.index("db.media_by_filename(user_id, basename)")
assert i_path < i_folder < i_name, "path, then post folder, then basename"


# --- grouping places a zip's images with their post -------------------------
images = [
    {"id": "i1", "visual_files": [{"path": os.path.join(A, "images", "1.png"),
                                   "basename": "1.png"}]},
    {"id": "iz", "visual_files": [{"path": IN_ZIP, "basename": "01.png"}]},
]
groups = sync.group_media_by_post(lib, "mirenac", [], images)
assert groups["101"]["images"] == ["i1"], groups
assert groups["102"]["images"] == ["iz"], groups


# --- the gallery pass --------------------------------------------------------
class FakeTags:
    def resolve(self, name):
        return "tag-" + name


class FakeResolver:
    def resolve(self, username, from_mention=False, source=None):
        return ["p-other"]

    def is_sponsor(self, username):
        return False

    content_house_tag_id = ""

    def is_content_house(self, username):
        return False


class FakeClient:
    def __init__(self, galleries):
        self.galleries = galleries
        self.updated, self.created, self.attached = [], [], []

    def find_located_galleries(self, path):
        return self.galleries

    def update_gallery(self, gi):
        self.updated.append(gi)

    def create_gallery(self, gi):
        self.created.append(gi)
        return "new"

    def add_gallery_images(self, gid, imgs):
        self.attached.append(gid)


def gal(gid, folder=None, zip_path=None, **kw):
    g = {"id": gid, "title": "", "code": "", "organized": False, "urls": [],
         "folder": {"path": folder} if folder else None,
         "files": [{"path": zip_path}] if zip_path else [],
         "tags": [], "performers": []}
    g.update(kw)
    return g


G_IMAGES = gal("g-images", folder=os.path.join(A, "images"))
G_ATTACH = gal("g-attach", folder=os.path.join(A, "attachments"))
G_ZIP = gal("g-zip", zip_path=ZIP)
G_COVER = gal("g-cover", folder=os.path.join(A, "post_info"))
# substring match from Stash: same creator name, different library
G_STRAY = gal("g-stray", folder="/torrents/whisparr/mirenac - Mirenac/posts/101 - x/images")

P = sources.PATREON
profile = {"username": "mirenac", "user_id": "mirenac"}
scenes = [{"id": "s1", "files": [{"path": os.path.join(A, "attachments", "v.mp4")}]}]
w(os.path.join(A, "attachments", "v.mp4"))
lib = ps.PatreonLibrary(creator)       # re-read: the video now exists


def run(galleries, full_sync=False, keep=False):
    client = FakeClient(galleries)
    totals = {"galleries": 0}
    sync.sync_post_folder_galleries(
        client, lib, profile, proc, FakeResolver(), FakeTags(), None,
        "studio-1", ["p-creator"], set(), None, full_sync, keep, 1,
        scenes, images, totals, P,
    )
    return client


c = run([G_IMAGES, G_ATTACH, G_ZIP, G_COVER, G_STRAY])
by_id = {u["id"]: u for u in c.updated}
# both of post 101's folders get its metadata; post 102's zip gets 102's
assert set(by_id) == {"g-images", "g-attach", "g-zip"}, sorted(by_id)
assert by_id["g-images"]["title"] == "The Priest's Blessing", by_id["g-images"]
assert by_id["g-images"]["code"] == "101"
assert by_id["g-images"]["urls"] == ["https://www.patreon.com/posts/x-101"]
assert by_id["g-images"]["studio_id"] == "studio-1"
assert by_id["g-images"]["organized"] is True
assert by_id["g-zip"]["title"] == "Hercules Comic" and by_id["g-zip"]["code"] == "102"
# the post's video is linked to the post's galleries
assert by_id["g-images"]["scene_ids"] == ["s1"], by_id["g-images"]
# Stash owns the contents: nothing is created and nothing attached, ever
assert c.created == [] and c.attached == [], (c.created, c.attached)
# post_info/ (covers) and the out-of-library stray are left alone
assert "g-cover" not in by_id and "g-stray" not in by_id


# --- plain sync vs full sync -------------------------------------------------
done = dict(G_IMAGES, organized=True, code="101", title="My fixed title")
assert run([done]).updated == [], "organized: a plain sync leaves it alone"
assert [u["id"] for u in run([done], full_sync=True).updated] == ["g-images"]
# Organized by HAND, never given any post's data: still the user's "done".
# These are Stash's galleries, curated before this pass existed, and a routine
# sync must not overwrite them merely because they lack the post id.
curated_by_hand = dict(G_IMAGES, organized=True, code="", title="Hand title",
                       performers=[{"id": "p-mine"}])
assert run([curated_by_hand]).updated == []


# --- keepManualEdits merges what the user added -----------------------------
curated = dict(G_IMAGES, performers=[{"id": "p-mine"}], tags=[{"id": "t-mine"}])
u = run([curated], keep=True).updated[0]
assert "p-mine" in u["performer_ids"] and "p-creator" in u["performer_ids"], u
assert "t-mine" in u["tag_ids"] and "tag-Patreon" in u["tag_ids"], u
# ...and replaces them without it
u = run([curated], keep=False).updated[0]
assert "p-mine" not in u["performer_ids"] and "t-mine" not in u["tag_ids"], u


# --- wiring: Patreon uses this pass, the scraper sites keep theirs ----------
assert P.post_folders is True
assert sources.ONLYFANS.post_folders is False
assert sources.JUSTFORFANS.post_folders is False
seg = src[src.index("elif not crew_only:"):]
seg = seg[:seg.index("\ndef ")]
assert "if source.post_folders:" in seg and "sync_post_folder_galleries(" in seg
assert "build_post_galleries(" in seg
# the generic adoption would retitle what this pass just titled
assert "if not source.post_folders:" in seg

print("ALL OK")
