"""Title Exclusions must reach Patreon titles the same way they reach OF/JFF.

Patreon is the odd one out: its titles are AUTHORED (real_titles), so they take
a different branch of SourceProfile.title() than the derived-from-post-text
titles the other sites get. Both branches have to strip, and so does the
collection gallery, which builds its title without going through either.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _plugin import PLUGIN, plugin_file
sys.path.insert(0, PLUGIN)
from media import MediaProcessor
import sources

EXCLUSIONS = [r"new collab:", r"\[patreon exclusive\]", r"^\U0001F947"]
proc = MediaProcessor(65, EXCLUSIONS)

PATREON = sources.PATREON
ONLYFANS = sources.ONLYFANS

# --- authored titles (Patreon) ---------------------------------------------
meta = {"title": "[Patreon Exclusive] Moano & Ariel", "text": "body text"}
title, details = PATREON.title(proc, meta, "body text", "fallback")
assert title == "Moano & Ariel", title
# the body is kept WHOLE as details -- stripping is title-only
assert details == "body text", details

# case-insensitive, like every other exclusion
meta = {"title": "NEW COLLAB: Beach day", "text": ""}
assert PATREON.title(proc, meta, "", "fb")[0] == "Beach day"

# an emoji-prefixed title, matching the real library's convention
meta = {"title": "\U0001F947Moano & Ariel  Ass Training  1080p + SFX", "text": ""}
assert PATREON.title(proc, meta, "", "fb")[0] == "Moano & Ariel Ass Training 1080p + SFX", \
    PATREON.title(proc, meta, "", "fb")[0]

# stripping to nothing keeps the original rather than leaving a blank title
meta = {"title": "[Patreon Exclusive]", "text": ""}
assert PATREON.title(proc, meta, "", "fb")[0] == "[Patreon Exclusive]"

# a Patreon post with NO authored title falls back to the derived path, which
# strips too
meta = {"title": "", "text": "New collab: Beach day\nand the rest"}
title, details = PATREON.title(proc, meta, meta["text"], "fb")
assert title == "Beach day", title
assert details == "New collab: Beach day\nand the rest", details

# --- derived titles (OnlyFans/JFF) are unchanged ---------------------------
title, details = ONLYFANS.title(proc, {"title": "ignored"},
                                "New collab: Beach day\nbody", "fb")
assert title == "Beach day", title
assert details == "New collab: Beach day\nbody", details

# --- collection galleries (Patreon-only) -----------------------------------
# These build a title directly instead of going through SourceProfile.title(),
# so they were missing the pass entirely.
src = open(plugin_file("sync.py"),
           encoding="utf-8").read()
assert 'processor.apply_title_exclusions(coll["title"])' in src, \
    "collection gallery title must be stripped too"
assert proc.apply_title_exclusions("[Patreon Exclusive] Tarzan & Milo") == "Tarzan & Milo"
# an untitled collection keeps its synthetic fallback
assert (proc.apply_title_exclusions("") or "Collection 42") == "Collection 42"

# --- no exclusions configured: everything passes through untouched ---------
plain = MediaProcessor(65, [])
meta = {"title": "[Patreon Exclusive] Moano & Ariel", "text": ""}
assert plain.title_exclusions == []
assert PATREON.title(plain, meta, "", "fb")[0] == "[Patreon Exclusive] Moano & Ariel"

print("ALL OK")
