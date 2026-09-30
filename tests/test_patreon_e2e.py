"""Patreon driven through sync.py's real build_update / _gallery_meta.

Checks the site-specific decisions: authored titles kept as-is (with the body
left whole as details), the Patreon site tag and studio, the post URL, the
post-id code, and that OnlyFans behaviour is untouched by the same code path.
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
import media
import patreon_source as ps
import sources
import sync

root = tempfile.mkdtemp()
proc = media.MediaProcessor(65, ["new collab:"])


def w(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


INFO = """ID: 38354242
Type: video_external_file
Title: New collab: Preview for Gold Tiers
Content: <p>Body text that is <b>not</b> the title.</p>
Published: 2026-09-29T07:05:00.000+00:00
URL: https://www.patreon.com/posts/preview-gold-38354242
"""

creator = os.path.join(root, "mrxtoon - Mr. X-Toon")
post = os.path.join(creator, "posts", "38354242 - Prieview for Gold Tiers")
w(os.path.join(post, "post_info", "info.txt"), INFO)
w(os.path.join(post, "post_info", "cover-image.jpg"))
w(os.path.join(post, "attachments", "38354242-preview.mp4"))
w(os.path.join(post, "images", "still.jpg"))

lib = ps.PatreonLibrary(creator)
assert sources.profile_for_source(lib.source()) is sources.PATREON

PATREON = sources.PATREON


class FakeTags:
    def __init__(self):
        self.names = []

    def resolve(self, name):
        self.names.append(name)
        return "tag-" + name


class FakeResolver:
    source = PATREON

    def resolve(self, username, from_mention=False, source=None):
        return ["p1"]

    def creator_credit(self, username):
        return set(), None

    def is_sponsor(self, username):
        return False


profile = {"username": "mrxtoon", "user_id": "mrxtoon"}
row = lib.media_by_filename("mrxtoon", "38354242-preview.mp4")
assert row is not None
tags = FakeTags()

update, title = sync.build_update(
    lib, proc, profile, row, ["p1"], "studio-patreon", FakeResolver(), tags, None,
    "scene", set(), None, PATREON,
)

# The authored title is used as written -- NOT derived from the body ...
assert title == "Preview for Gold Tiers", title          # exclusion still applied
assert update["title"] == "Preview for Gold Tiers"
# ... and the body stays whole as details rather than being split out of
assert "Body text that is not the title." in update["details"], update["details"]
# post id as the code (filenames are named after the media, not the post)
assert update["code"] == "38354242", update["code"]
# the real captured URL, slug and all
assert update["urls"] == ["https://www.patreon.com/posts/preview-gold-38354242"], update["urls"]
assert update["date"] == "2026-09-29", update["date"]
assert update["studio_id"] == "studio-patreon"
assert update["organized"] is True
# Patreon site tag, and none of the JustFor.Fans-only extras
assert "Patreon" in tags.names, tags.names
assert "paid" not in tags.names and "pinned" not in tags.names, tags.names

# per-site identity
assert PATREON.profile_url("mrxtoon") == "https://www.patreon.com/mrxtoon"
assert PATREON.studio_name("mrxtoon") == "mrxtoon (Patreon)"
assert PATREON.is_paid(lib, "38354242", lib.post_meta("38354242")) is False

# --- the gallery for the same post -----------------------------------------
group = {"posted_at": "2026-09-29T07:05:00.000+00:00", "api_type": "video_external_file"}
gi, gtitle, sponsor_ids = sync._gallery_meta(
    lib, proc, profile, "38354242", group, FakeResolver(), FakeTags(), None,
    "studio-patreon", ["p1"], set(), None,
    "https://www.patreon.com/posts/preview-gold-38354242", ["scene1"], PATREON,
)
assert gtitle == "Preview for Gold Tiers", gtitle
assert gi["code"] == "38354242"
assert gi["scene_ids"] == ["scene1"]
assert sponsor_ids == set()

# --- OnlyFans is unchanged by the shared title path ------------------------
class OFDB:
    def post_meta(self, post_id):
        return {"text": "New collab: beach day with the crew", "price": 0,
                "paid": 0, "archived": 0}
    def tier(self, post_id): return None
    def hashtags(self, post_id): return []
    def is_pinned(self, post_id): return False
    def post_url(self, post_id): return None


of_row = {"post_id": "998", "filename": "12345.mp4", "posted_at": "2024-01-02",
          "api_type": "Posts"}
of_tags = FakeTags()
of_update, of_title = sync.build_update(
    OFDB(), proc, {"username": "someone"}, of_row, ["p1"], "s", FakeResolver(),
    of_tags, None, "scene", set(), None, sources.ONLYFANS,
)
# derived from the post text, exclusion stripped, description kept verbatim
assert of_title == "beach day with the crew", of_title
assert of_update["code"] == "12345", of_update["code"]   # filename stem, as always
assert of_update["urls"] == ["https://www.onlyfans.com/998/someone"], of_update["urls"]
assert "OnlyFans" in of_tags.names, of_tags.names

shutil.rmtree(root)
print("ALL OK")
