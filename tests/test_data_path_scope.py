"""A creator's name must not drag in media from outside their library.

Stash's `path` filter is a plain SUBSTRING match over the WHOLE library, and the
media queries pass only the creator's username. So a Patreon creator with the
vanity "Mirenac" matched

    /torrents/downloads/whisparr/[Mirenac] The Priest's ... Mini Comic/1.png

a file with no connection to the Patreon library. A post whose own image was
also named `1.png` then claimed it, and the plugin wrote that post's title,
URL, date, studio and `organized: True` onto somebody's torrent download.

The fix confines the substring result to the data path configured for the site.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _plugin import PLUGIN, plugin_file
sys.path.insert(0, PLUGIN)

import sync
import sources


def image(stash_id, path):
    return {"id": stash_id, "visual_files": [{"path": path,
                                              "basename": os.path.basename(path)}]}


def scene(stash_id, *paths):
    return {"id": stash_id, "files": [{"path": p, "basename": os.path.basename(p)}
                                      for p in paths]}


class Profile:
    """Stand-in for a SourceProfile carrying a configured data path."""

    def __init__(self, data_path):
        self.data_path = data_path


P = Profile("/data/patreon")

TORRENT = ("/torrents/downloads/whisparr/"
           "[Mirenac] The Priest's Special Wedding Blessing Mini Comic/1.png")
REAL = "/data/patreon/Mirenac - Mirenac/posts/101 - The Priest's/images/1.png"

# --- the reported case ------------------------------------------------------
kept = sync.media_under_data_path(
    [image("torrent", TORRENT), image("real", REAL)], P, "Mirenac", "image")
assert [i["id"] for i in kept] == ["real"], [i["id"] for i in kept]

# scenes go through the same gate, via their own file shape
kept = sync.media_under_data_path(
    [scene("t", "/torrents/downloads/Mirenac/x.mp4"),
     scene("r", "/data/patreon/Mirenac - Mirenac/posts/9/embed/x.mp4")],
    P, "Mirenac", "scene")
assert [s["id"] for s in kept] == ["r"], [s["id"] for s in kept]


# --- a prefix must not match a SIBLING directory ----------------------------
# "/data/patreon" must not swallow "/data/patreon-backup".
assert sync.media_under_data_path(
    [image("x", "/data/patreon-backup/Mirenac/1.png")], P, "Mirenac", "image") == []
# ...while the real path, and deeper nesting under it, are kept
assert len(sync.media_under_data_path(
    [image("a", "/data/patreon/a/b/c/d/1.png")], P, "Mirenac", "image")) == 1

# a trailing slash or redundant separators in the setting change nothing
for spelling in ["/data/patreon/", "/data/patreon", "/data//patreon", "/data/./patreon"]:
    got = sync.media_under_data_path(
        [image("real", REAL), image("t", TORRENT)],
        Profile(spelling), "Mirenac", "image")
    assert [i["id"] for i in got] == ["real"], (spelling, got)


# --- a multi-file item is kept if ANY file is inside -------------------------
# Stash merges identical files; one copy living elsewhere shouldn't disown the
# media, and build_update writes to the media, not the file.
both = scene("m", TORRENT, "/data/patreon/Mirenac - Mirenac/posts/9/embed/x.mp4")
assert len(sync.media_under_data_path([both], P, "Mirenac", "scene")) == 1


# --- degrade safely ---------------------------------------------------------
# No configured path -> nothing to confine to, so nothing is dropped.
for blank in ["", "   ", None]:
    items = [image("torrent", TORRENT)]
    assert sync.media_under_data_path(items, Profile(blank), "x", "image") == items
# a profile that never had the attribute at all (older object) is left alone
class Bare:
    pass


items = [image("torrent", TORRENT)]
assert sync.media_under_data_path(items, Bare(), "x", "image") == items
# media with no file paths is kept rather than silently dropped
pathless = {"id": "p", "visual_files": [{"basename": "1.png"}]}
assert sync.media_under_data_path([pathless], P, "x", "image") == [pathless]


# --- the real SourceProfile carries the attribute ---------------------------
for prof in sources.ALL_PROFILES:
    assert hasattr(prof, "data_path"), prof.key

# and main() fills it in from the same setting it uses to find libraries
src = open(plugin_file("sync.py"), encoding="utf-8").read()
assert "source.data_path = path" in src
# both media queries go through the gate
assert src.count("media_under_data_path(") == 3, \
    "find_scenes and find_images must both be confined"

print("ALL OK")
