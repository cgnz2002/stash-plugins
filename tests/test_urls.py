"""Per-site performer URL regression test."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _plugin import PLUGIN, plugin_file
sys.path.insert(0, PLUGIN)

import media, sources, sync

proc = media.MediaProcessor(64)

# --- parse_mentions carries the crediting site -------------------------------
got = proc.parse_mentions(
    "shot with @plain and https://onlyfans.com/ofguy plus justfor.fans/jffguy"
)
assert got == [("plain", None), ("ofguy", "onlyfans.com"), ("jffguy", "justfor.fans")], got

# post-id form is not a username
assert proc.parse_mentions("onlyfans.com/12345/creator") == [], \
    proc.parse_mentions("onlyfans.com/12345/creator")

# a URL credit wins over a bare @mention of the same name
assert proc.parse_mentions("@dupe justfor.fans/dupe") == [("dupe", "justfor.fans")]

# --- domain -> profile -------------------------------------------------------
assert sources.profile_for_domain("onlyfans.com") is sources.ONLYFANS
assert sources.profile_for_domain("JustFor.Fans") is sources.JUSTFORFANS
assert sources.profile_for_domain(None) is None
assert sources.profile_for_domain("example.com") is None


# --- resolve() puts the credited site's URL on a created performer -----------
class FakeClient:
    def __init__(self):
        self.created = []
        self._next = 100

    def find_all_performers(self):
        return []

    def find_performers_by_name(self, name):
        return {"name_like": []}

    def create_performer(self, name, url):
        self._next += 1
        self.created.append((name, url))
        return str(self._next)


def new_resolver(site):
    client = FakeClient()
    res = sync.PerformerResolver(client, auto_create=True)
    res.source = site
    return client, res


# syncing a JustFor.Fans library...
client, res = new_resolver(sources.JUSTFORFANS)
res.resolve("creator")                                     # the creator itself
res.resolve("ofcollab", from_mention=True,
            source=sources.profile_for_domain("onlyfans.com"))   # OF link in a JFF post
res.resolve("jffcollab", from_mention=True,
            source=sources.profile_for_domain("justfor.fans"))
res.resolve("bare", from_mention=True, source=None)        # bare @mention
assert client.created == [
    ("creator", "https://justfor.fans/creator"),
    ("ofcollab", "https://www.onlyfans.com/ofcollab"),
    ("jffcollab", "https://justfor.fans/jffcollab"),
    ("bare", "https://justfor.fans/bare"),
], client.created

# ...and the mirror image on an OnlyFans library (unchanged legacy behaviour
# for everything that isn't an explicit cross-site link)
client, res = new_resolver(sources.ONLYFANS)
res.resolve("creator")
res.resolve("bare", from_mention=True)
res.resolve("jffcollab", from_mention=True,
            source=sources.profile_for_domain("justfor.fans"))
assert client.created == [
    ("creator", "https://www.onlyfans.com/creator"),
    ("bare", "https://www.onlyfans.com/bare"),
    ("jffcollab", "https://justfor.fans/jffcollab"),
], client.created


# --- collect_crew consumes the new tuples end to end -------------------------
client, res = new_resolver(sources.JUSTFORFANS)
directors, photographers, crew_ids, mention_ids, _sponsors = sync.collect_crew(
    proc, res, "collab with onlyfans.com/ofguy and @bare", set(), None, ["1"]
)
assert (directors, photographers) == ([], []), (directors, photographers)
assert len(mention_ids) == 2, mention_ids
assert client.created == [
    ("bare", "https://justfor.fans/bare"),
    ("ofguy", "https://www.onlyfans.com/ofguy"),
], client.created

print("ALL OK")
