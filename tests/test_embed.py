"""embed/ holds real video: it must be indexed, the other aux folders must not."""
import json, shutil, tempfile
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _plugin import PLUGIN, plugin_file
sys.path.insert(0, PLUGIN)
import patreon_source

root = tempfile.mkdtemp()
creator = os.path.join(root, "mrxtoon - Mr. X-Toon")

def post(pid, title, files):
    d = os.path.join(creator, "posts", "{} - {}".format(pid, title))
    info = os.path.join(d, "post_info")
    os.makedirs(info)
    with open(os.path.join(info, "info.txt"), "w", encoding="utf-8") as f:
        f.write("ID: {}\nTitle: {}\nContent: <p>body</p>\n"
                "URL: https://www.patreon.com/posts/{}\n"
                "Published: 2024-01-02T00:00:00.000+00:00\n".format(pid, title, pid))
    # patreon-dl leaves these beside the media, and they are real image files
    for aux, name in [("post_info", "cover-image.jpg"), (".thumbnails", "thumbnail.jpg"),
                      ("image_previews", "preview.jpg")]:
        p = os.path.join(d, aux)
        os.makedirs(p, exist_ok=True)
        open(os.path.join(p, name), "w").close()
    for sub, name in files:
        p = os.path.join(d, sub)
        os.makedirs(p, exist_ok=True)
        open(os.path.join(p, name), "w").close()
    return d

# The two posts from the report, by their real paths.
post("169449288", "\U0001F947Moano & Ariel  Ass Training  1080p + SFX",
     [("embed", "Moano & Ariel - Ass Training  LOOP 1080.mp4.mp4")])
post("38354242", "Prieview for Gold Tiers",
     [("attachments", "ScreenCapture_2020-6-18 00.00.24.mp4")])

lib = patreon_source.PatreonLibrary(creator)
assert lib.vanity == "mrxtoon", lib.vanity

# the file that silently vanished before
row = lib.media_by_filename("mrxtoon", "Moano & Ariel - Ass Training  LOOP 1080.mp4.mp4")
assert row, "embed/ video still not indexed"
assert row["post_id"] == "169449288", row
assert lib.post_url("169449288") == "https://www.patreon.com/posts/169449288"

# the one that always worked must keep working
row = lib.media_by_filename("mrxtoon", "ScreenCapture_2020-6-18 00.00.24.mp4")
assert row and row["post_id"] == "38354242", row

# ...and the genuinely-auxiliary folders must STILL be excluded, or two thirds
# of the library's "images" become junk carrying a post's metadata
for junk in ["cover-image.jpg", "thumbnail.jpg", "preview.jpg"]:
    assert lib.media_by_filename("mrxtoon", junk) is None, junk
assert "embed" not in patreon_source.EXCLUDED_DIRS
assert patreon_source.EXCLUDED_DIRS == {"post_info", ".thumbnails", "image_previews"}

# both posts' media reachable for the gallery pass
names = sorted(r["filename"] for r in lib.medias_for_model("mrxtoon"))
assert names == ["Moano & Ariel - Ass Training  LOOP 1080.mp4.mp4",
                 "ScreenCapture_2020-6-18 00.00.24.mp4"], names

shutil.rmtree(root)
print("ALL OK")
