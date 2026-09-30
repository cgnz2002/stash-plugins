"""Username capitalisation is preserved when creating performers."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _plugin import PLUGIN, plugin_file
sys.path.insert(0, PLUGIN)

import media, sources, sync

proc = media.MediaProcessor(64)

# --- parse_mentions returns the username as written -------------------------
got = proc.parse_mentions("shot with @BrandCo and https://onlyfans.com/ChicagoNerd")
assert got == [("BrandCo", None), ("ChicagoNerd", "onlyfans.com")], got

# dedup is still case-insensitive: one credit, not two, the URL still wins for
# the domain, and the capitalised spelling survives whichever form carried it
got = proc.parse_mentions("@BrandCo ... justfor.fans/brandco")
assert got == [("BrandCo", "justfor.fans")], got

got = proc.parse_mentions("onlyfans.com/BrandCo then @brandco")
assert got == [("BrandCo", "onlyfans.com")], got

# all-lowercase everywhere stays lowercase -- capitals are kept, never invented
assert proc.parse_mentions("@brandco onlyfans.com/brandco") == [
    ("brandco", "onlyfans.com")]

# first capitalised spelling wins over a later one
assert proc.parse_mentions("@BrandCo and @BRANDCO") == [("BrandCo", None)]

# trailing sentence punctuation still trimmed, case still intact
assert proc.parse_mentions("see onlyfans.com/BigName.") == [("BigName", "onlyfans.com")]

# post-id form still skipped
assert proc.parse_mentions("onlyfans.com/12345/Creator") == []


# --- @mention boundaries: emoji/brackets/quotes around a credit -------------
# The real-world miss that prompted this: an emoji straight before the '@'.
for text in [
    "Brand new video \U0001F6A8@Aidentylerxxx  on another adventure",
    "shot with @Aidentylerxxx today",
    "(@Aidentylerxxx) joined us",
    "with @Aidentylerxxx\U0001F608 hot",
    "collab:@Aidentylerxxx",
    "see @Aidentylerxxx.",
    '"@Aidentylerxxx" was there',
    "@Aidentylerxxx!! wow",
    "<a href='x'>@Aidentylerxxx</a> tagged",
    "@Aidentylerxxx",
]:
    assert proc.parse_mentions(text) == [("Aidentylerxxx", None)], (text, proc.parse_mentions(text))

# a trailing separator isn't part of the username
assert proc.parse_mentions("feat. @name-") == [("name", None)]
# dots inside a username still survive
assert proc.parse_mentions("with @first.last today") == [("first.last", None)]

# ...and an email is still NOT a mention (the whole point of the guard)
for text in ["email me at fan@example.com please",
             "contact fan.name@example.com ok",
             "reach me-here@example.com",
             "20 @ 30 dollars"]:
    assert proc.parse_mentions(text) == [], (text, proc.parse_mentions(text))


# --- a created performer keeps the capitals ---------------------------------
class FakeClient:
    def __init__(self, existing=()):
        self.existing = list(existing)
        self.created = []
        self._next = 100

    def find_all_performers(self):
        return self.existing

    def find_performers_by_name(self, name):
        return {"name_like": []}

    def create_performer(self, name, url):
        self._next += 1
        self.created.append((name, url))
        return str(self._next)


client = FakeClient()
res = sync.PerformerResolver(client, auto_create=True)
res.source = sources.ONLYFANS
for mention, domain in proc.parse_mentions("@BrandCo with justfor.fans/ChicagoNerd"):
    res.resolve(mention, from_mention=True,
                source=sources.profile_for_domain(domain))
assert client.created == [
    ("BrandCo", "https://www.onlyfans.com/BrandCo"),
    ("ChicagoNerd", "https://justfor.fans/ChicagoNerd"),
], client.created


# --- matching an EXISTING performer stays case-insensitive -------------------
# '@BrandCo' must attach to the lowercase 'brandco' Stash already has (however
# it got that name) rather than creating a second, differently-cased duplicate.
client = FakeClient([
    {"id": "1", "name": "brandco", "alias_list": [], "tags": []},
    {"id": "2", "name": "Someone", "alias_list": ["chicagonerd"], "tags": []},
])
res = sync.PerformerResolver(client, auto_create=True)
res.source = sources.ONLYFANS
assert res.resolve("BrandCo", from_mention=True) == ["1"]
assert res.resolve("ChicagoNERD", from_mention=True) == ["2"]   # alias match
assert client.created == [], client.created


# --- crew/sponsor lookups are case-insensitive over the new casing ----------
CREW, SPONSOR = "7", "9"
client = FakeClient([
    {"id": "3", "name": "Shooter", "alias_list": [], "tags": [{"id": CREW}]},
    {"id": "4", "name": "BrandCo", "alias_list": [], "tags": [{"id": SPONSOR}]},
])
res = sync.PerformerResolver(client, False, CREW, SPONSOR)
res.source = sources.ONLYFANS
directors, _photog, crew_ids, mention_ids, sponsor_ids = sync.collect_crew(
    proc, res, "@SHOOTER shot it, ad for onlyfans.com/brandco", set(), None, ["1"]
)
assert directors == ["Shooter"], directors     # the performer's display name
assert crew_ids == {"3"}, crew_ids
assert sponsor_ids == {"4"}, sponsor_ids
assert mention_ids == [], mention_ids

print("ALL OK")
