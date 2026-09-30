"""Skip Multi-file is an OnlyFans guard; Patreon must opt out of it.

On OnlyFans a merged scene is several DIFFERENT files gathered from different
pages, so there is no single right post to take metadata from and the setting
protects them. patreon-dl produces the opposite: a creator posting the same
image at several tiers downloads byte-identical copies into each post folder,
and Stash merges them on hash. Skipping those skips ordinary images.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _plugin import PLUGIN, plugin_file
sys.path.insert(0, PLUGIN)
import sources

# --- the profiles declare it ------------------------------------------------
assert sources.PATREON.merged_files_are_duplicates is True
assert sources.ONLYFANS.merged_files_are_duplicates is False
assert sources.JUSTFORFANS.merged_files_are_duplicates is False
# A new profile must opt IN deliberately: the exemption is unsafe by default,
# since skipping is the protective behaviour.
import inspect
sig = inspect.signature(sources.SourceProfile.__init__)
assert sig.parameters["merged_files_are_duplicates"].default is False


def skip_multi(setting_on, tag_only, source):
    """Mirror of the expression in process_profile."""
    return setting_on and not tag_only and not source.merged_files_are_duplicates


# --- setting ON -------------------------------------------------------------
# OnlyFans/JFF still protected...
assert skip_multi(True, False, sources.ONLYFANS) is True
assert skip_multi(True, False, sources.JUSTFORFANS) is True
# ...Patreon synced anyway, which is the whole ask
assert skip_multi(True, False, sources.PATREON) is False

# --- setting OFF: nothing is skipped anywhere ------------------------------
for p in sources.ALL_PROFILES:
    assert skip_multi(False, False, p) is False

# --- the tag pass is additive and never skips, on any site -----------------
for p in sources.ALL_PROFILES:
    assert skip_multi(True, True, p) is False

# --- a merged item must pick the SAME post every run -----------------------
# media_map is keyed by path; iterating a dict raw would let a merged scene
# flip between two posts' metadata across runs.
src = open(plugin_file("sync.py"),
           encoding="utf-8").read()
assert "sorted(media_map.items())" in src, "iteration must be deterministic"

media_map = {
    "/d/posts/48090299 - Gold/images/a.jpg": ("image", "7"),
    "/d/posts/47982224 - Diamond/images/a.jpg": ("image", "7"),
}
seen, chosen = set(), []
for path, (kind, sid) in sorted(media_map.items()):
    if (kind, sid) in seen:
        continue
    seen.add((kind, sid))
    chosen.append(path)
assert chosen == ["/d/posts/47982224 - Diamond/images/a.jpg"], chosen
# and the merged item is written exactly once
assert len(chosen) == 1

# --- the setting's description tells the user about the exemption ----------
manifest = open(plugin_file("of-stash-sync.yml"),
                encoding="utf-8").read()
assert "DOES NOT APPLY TO PATREON" in manifest

print("ALL OK")
