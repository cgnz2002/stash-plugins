"""A Patreon basename can belong to more than one post.

A creator who posts the same video at two tiers gets the SAME filename in two
post folders:

  posts/47982224 - Tarzan and Milo - 4k Diamond/embed/TARZAN & MILO.mp4.mp4
  posts/48090299 - Tarzan and Milo - 1080p - Gold/embed/TARZAN & MILO.mp4.mp4

Keyed by basename that is two bugs: one file evicts the other from the index
(so a Stash scene is silently never processed), and whichever post os.walk
reached first supplies the metadata for both. The path is unique, so it wins.
"""
import shutil, tempfile
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _plugin import PLUGIN, plugin_file
sys.path.insert(0, PLUGIN)
import patreon_source

root = tempfile.mkdtemp()
creator = os.path.join(root, "mrxtoon - Mr. X-Toon")
DUP = "TARZAN & MILO.mp4.mp4"


def post(pid, title, files):
    d = os.path.join(creator, "posts", "{} - {}".format(pid, title))
    info = os.path.join(d, "post_info")
    os.makedirs(info)
    with open(os.path.join(info, "info.txt"), "w", encoding="utf-8") as f:
        f.write("ID: {}\nTitle: {}\nContent: <p>body</p>\n"
                "URL: https://www.patreon.com/posts/{}\n"
                "Published: 2024-01-02T00:00:00.000+00:00\n".format(pid, title, pid))
    for sub, name in files:
        p = os.path.join(d, sub)
        os.makedirs(p, exist_ok=True)
        open(os.path.join(p, name), "w").close()
    return d


d4k = post("47982224", "Tarzan and Milo - 4k Diamond", [("embed", DUP)])
dhd = post("48090299", "Tarzan and Milo - 1080p - Gold", [("embed", DUP)])
p4k = os.path.join(d4k, "embed", DUP)
phd = os.path.join(dhd, "embed", DUP)

lib = patreon_source.PatreonLibrary(creator)

# --- the path resolves to the RIGHT post, both ways round -------------------
assert lib.media_by_path("mrxtoon", p4k)["post_id"] == "47982224"
assert lib.media_by_path("mrxtoon", phd)["post_id"] == "48090299"
# ...so the two titles never get crossed
assert lib.post_meta(lib.media_by_path("mrxtoon", p4k)["post_id"])["title"] \
    == "Tarzan and Milo - 4k Diamond"
assert lib.post_meta(lib.media_by_path("mrxtoon", phd)["post_id"])["title"] \
    == "Tarzan and Milo - 1080p - Gold"

# an unknown path is a miss, not a wrong answer
assert lib.media_by_path("mrxtoon", os.path.join(creator, "nope.mp4")) is None

# --- the basename fallback still answers, deterministically ----------------
# It cannot say WHICH post, so it must at least not crash or return junk.
row = lib.media_by_filename("mrxtoon", DUP)
assert row and row["post_id"] in ("47982224", "48090299"), row
# entries carry their own path, so a caller can tell what it actually got
assert row["path"] in (p4k, phd), row

# --- BOTH files stay reachable for the gallery pass ------------------------
# Previously one evicted the other from the index entirely.
rows = lib.medias_for_model("mrxtoon")
paths = sorted(r["path"] for r in rows)
assert paths == sorted([p4k, phd]), paths
assert sorted(r["post_id"] for r in rows) == ["47982224", "48090299"]

# --- the sqlite-backed sources degrade, they don't break -------------------
import source_database
assert hasattr(source_database.SourceDatabase, "media_by_path")
assert source_database.SourceDatabase.media_by_path(None, "u", "/any/path") is None

# --- sync.py prefers the path and dedupes a multi-file scene ---------------
src = open(plugin_file("sync.py"),
           encoding="utf-8").read()
assert "db.media_by_path(user_id, path)" in src
assert "or db.media_by_filename(user_id, basename)" in src
assert 'media_map[f["path"]] = entry' in src, "scene files must key on path"
assert "seen_media" in src, "a multi-file scene must not be written twice"

shutil.rmtree(root)
print("ALL OK")
