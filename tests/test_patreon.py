"""PatreonLibrary adapter, against a tree matching the real library.

Structure and junk folders taken from screenshots of /media/patreon and from a
real patreon-dl run log (post_info/.thumbnails/images/attachments/embed/
image_previews, with .mp4 living inside images/ and attachments/).
"""
import os
import shutil
import sys
import tempfile

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _plugin import PLUGIN, plugin_file
sys.path.insert(0, PLUGIN)
import patreon_source as ps

root = tempfile.mkdtemp()


def w(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


INFO = """ID: {pid}
Type: {ptype}
Title: {title}
Content: <p>Some <b>body</b> text with @BrandCo</p>
Published: 2026-09-29T07:05:00.000+00:00
URL: https://www.patreon.com/posts/{pid}
"""

creator = os.path.join(root, "mrxtoon - Mr. X-Toon")

# Post 1: an image post, with every junk folder present
p1 = os.path.join(creator, "posts", "170800135 - Moano & Ariel Fanart by Creedo")
w(os.path.join(p1, "post_info", "info.txt"),
  INFO.format(pid="170800135", ptype="image_file", title="Moano & Ariel Fanart"))
w(os.path.join(p1, "post_info", "cover-image.jpg"))
w(os.path.join(p1, "post_info", "thumbnail.jpg"))
w(os.path.join(p1, "post_info", "post-api.json"), "{}")
w(os.path.join(p1, ".thumbnails", "753752627.jpg"))
w(os.path.join(p1, "image_previews", "prev.jpg"))
w(os.path.join(p1, "embed", "embed.txt"))
# embed/ is NOT auxiliary: patreon-dl downloads embedded video into it, and
# excluding it silently dropped whole scenes.
w(os.path.join(p1, "embed", "Moano & Ariel - Ass Training  LOOP 1080.mp4.mp4"))
w(os.path.join(p1, "images", "Moano_x_Ariel_2.jpg"))

# Post 2: video, which really does land in attachments/ and images/
p2 = os.path.join(creator, "posts", "63317450 - Hercules vs Aladdin")
w(os.path.join(p2, "post_info", "info.txt"),
  INFO.format(pid="63317450", ptype="video_external_file", title="Hercules vs Aladdin"))
w(os.path.join(p2, "post_info", "cover-image.jpg"))
w(os.path.join(p2, "attachments", "63317450-hercules vs aladdin.mp4"))
w(os.path.join(p2, "attachments", "notes.pdf"))
w(os.path.join(p2, "images", "still.jpg"))
w(os.path.join(p2, "images", "clip.mp4"))
w(os.path.join(p2, "images", "half.jpg.part"))     # in-progress download

# Decoys patreon-dl leaves beside the creators
os.makedirs(os.path.join(root, "logs"), exist_ok=True)
os.makedirs(os.path.join(root, ".patreon-dl"), exist_ok=True)
w(os.path.join(root, "patreon-dl.conf.bak"))
# A creator whose folder is a bare vanity with no " - Name"
bare = os.path.join(root, "Chols")
w(os.path.join(bare, "posts", "111 - Hi", "post_info", "info.txt"),
  INFO.format(pid="111", ptype="text_only", title="Hi"))
w(os.path.join(bare, "posts", "111 - Hi", "images", "a.jpg"))

# --- discovery ignores logs/, .patreon-dl/ and the loose .bak ---------------
found = sorted(os.path.basename(p) for p in ps.PatreonLibrary.find_databases(root))
assert found == ["Chols", "mrxtoon - Mr. X-Toon"], found

lib = ps.PatreonLibrary(creator)

# --- the creator ------------------------------------------------------------
assert lib.source() == "patreon"
assert lib.profiles() == [{"user_id": "mrxtoon", "username": "mrxtoon"}], lib.profiles()

# --- junk folders are NOT indexed ------------------------------------------
for junk in ["cover-image.jpg", "thumbnail.jpg", "753752627.jpg", "prev.jpg",
             "post-api.json", "info.txt"]:
    assert lib.media_by_filename("mrxtoon", junk) is None, junk
# ...and neither is a half-finished download
assert lib.media_by_filename("mrxtoon", "half.jpg.part") is None

# --- real media IS indexed, video included ----------------------------------
img = lib.media_by_filename("mrxtoon", "Moano_x_Ariel_2.jpg")
assert img and img["post_id"] == "170800135", img
assert img["posted_at"].startswith("2026-09-29"), img
assert img["api_type"] == "image_file", img

# embed/ video: the case that made "Skipped" hide real scenes
emb = lib.media_by_filename(
    "mrxtoon", "Moano & Ariel - Ass Training  LOOP 1080.mp4.mp4")
assert emb and emb["post_id"] == "170800135", emb
# a .txt descriptor in there is indexed too, which is harmless -- Stash never
# ingests one, so it can never match a scene or image
assert lib.media_by_filename("mrxtoon", "embed.txt") is not None

vid = lib.media_by_filename("mrxtoon", "63317450-hercules vs aladdin.mp4")
assert vid and vid["post_id"] == "63317450", vid
# video mixed into images/ is found too
assert lib.media_by_filename("mrxtoon", "clip.mp4")["post_id"] == "63317450"
# a non-media attachment is harmless to index, and must not break anything
assert lib.media_by_filename("mrxtoon", "notes.pdf")["post_id"] == "63317450"

# --- grouping a post's media (drives the gallery pass) ----------------------
rows = lib.medias_for_model("mrxtoon")
by_post = {}
for r in rows:
    by_post.setdefault(r["post_id"], []).append(r["filename"])
assert sorted(by_post["170800135"]) == [
    "Moano & Ariel - Ass Training  LOOP 1080.mp4.mp4",
    "Moano_x_Ariel_2.jpg", "embed.txt"], by_post["170800135"]
assert sorted(by_post["63317450"]) == [
    "63317450-hercules vs aladdin.mp4", "clip.mp4", "notes.pdf", "still.jpg",
], by_post["63317450"]

# --- post metadata ----------------------------------------------------------
meta = lib.post_meta("170800135")
assert "Some body text" in meta["text"], meta
assert "@BrandCo" in meta["text"], meta      # mentions survive for the credit pass
assert meta["paid"] == 0 and meta["price"] == 0 and meta["archived"] == 0
assert lib.post_url("170800135") == "https://www.patreon.com/posts/170800135"
# Patreon-only degradations, same shape OF-Scraper gives for JFF fields
assert lib.hashtags("170800135") == []
assert lib.tier("170800135") is None
assert lib.is_pinned("170800135") is False
assert lib.post_meta("nope") is None

# --- a bare-vanity creator still resolves ----------------------------------
lib2 = ps.PatreonLibrary(bare)
assert lib2.profiles() == [{"user_id": "Chols", "username": "Chols"}], lib2.profiles()
assert lib2.media_by_filename("Chols", "a.jpg")["post_id"] == "111"

lib.close()
shutil.rmtree(root)
print("ALL OK")
