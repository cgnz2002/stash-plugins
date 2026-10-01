"""comic-reader: its manifest, and the pure logic of its JavaScript.

The manifest half is plain Python. It guards the failures Stash reports only
as a line in its log, if at all: Stash parses manifests strictly (one unknown
key and the whole plugin fails to load), a missing ui/ file breaks the UI, and
a misspelled hook trigger is accepted and then simply never fires.

The logic half runs the plugin's own JavaScript under Node with stubbed
globals -- Stash's `gql`/`log`/`input` for the backend, a bare `window` for the
UI files -- and is skipped when Node isn't installed. The load-bearing case is
removablePages(): Stash evaluates NOT over a galleries_filter per joined
gallery, so the candidate query returns pages that ARE still in a comic, and
trusting it stripped Comic Page from real comic pages.
"""

import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _plugin import REPO

PLUGIN = os.path.join(REPO, "plugins", "comic-reader")
MANIFEST = os.path.join(PLUGIN, "comic-reader.yml")

# pkg/plugin/config.go (Stash v0.31)
TOP_LEVEL = {"name", "description", "version", "url", "exec", "interface", "errLog",
             "tasks", "hooks", "settings", "ui"}
UI_KEYS = {"javascript", "css", "requires", "assets", "csp"}
# pkg/plugin/hook/hooks.go (Stash v0.31)
TYPES = ["Scene", "SceneMarker", "Image", "Gallery", "GalleryChapter", "Group", "Movie",
         "Performer", "Studio", "Tag"]
TRIGGERS = {"{}.{}.Post".format(t, op) for t in TYPES for op in ("Create", "Update", "Destroy")}
TRIGGERS.add("Tag.Merge.Post")

with open(MANIFEST, encoding="utf-8") as f:
    text = f.read()
lines = text.splitlines()

# --- manifest -------------------------------------------------------------

top = [re.match(r"^([A-Za-z]\w*):", l).group(1) for l in lines if re.match(r"^[A-Za-z]\w*:", l)]
unknown = set(top) - TOP_LEVEL
assert not unknown, "unknown top-level keys (Stash would refuse the plugin): {}".format(unknown)
assert len(top) == len(set(top)), "duplicate top-level key: {}".format(top)
assert re.search(r"^interface: js$", text, re.M), "the backend is an embedded JS plugin"


def block(key):
    """Lines of a top-level block, up to the next top-level key."""
    out, inside = [], False
    for l in lines:
        if re.match(r"^[A-Za-z]\w*:", l):
            inside = l.startswith(key + ":")
            continue
        if inside:
            out.append(l)
    return out


ui = block("ui")
ui_keys = {re.match(r"^  (\w+):", l).group(1) for l in ui if re.match(r"^  \w+:", l)}
assert ui_keys <= UI_KEYS, "unknown ui keys: {}".format(ui_keys - UI_KEYS)
ui_files = [l.strip()[2:] for l in ui if l.strip().startswith("- ")]
exec_files = [l.strip()[2:] for l in block("exec") if l.strip().startswith("- ")]
assert exec_files == ["comic-reader.js"], exec_files
for rel in exec_files + ui_files:
    assert os.path.isfile(os.path.join(PLUGIN, rel)), "manifest lists a missing file: {}".format(rel)
# common.js defines the namespace the others extend; main.js wires them up.
js = [p for p in ui_files if p.endswith(".js")]
assert js[0] == "ui/common.js" and js[-1] == "ui/main.js", js

# Keys one level into tasks / hooks / settings (pkg/plugin/config.go). Stash
# rejects the whole plugin on an unknown one, a typo included.
TASK_KEYS = {"name", "description", "defaultArgs", "execArgs"}
HOOK_KEYS = TASK_KEYS | {"triggeredBy"}
SETTING_KEYS = {"displayName", "description", "type"}
for section, allowed, indent in [("tasks", TASK_KEYS, r"^  (?:- )?(\w+):"), ("hooks", HOOK_KEYS, r"^  (?:- )?(\w+):"),
                                 ("settings", SETTING_KEYS, r"^    (\w+):")]:
    keys = {m.group(1) for m in (re.match(indent, l) for l in block(section)) if m}
    assert keys and keys <= allowed, "{}: unknown keys {}".format(section, keys - allowed)

# Every list item under triggeredBy must be a trigger Stash knows -- matched
# loosely first, so a mis-cased "gallery.update.post" is flagged, not skipped.
triggers = [l.strip()[2:] for l in block("hooks")
            if re.match(r"^\s+- \w+\.\w+\.\w+\s*$", l)]
assert triggers, "no hook triggers found"
bad = [t for t in triggers if t not in TRIGGERS]
assert not bad, "hook triggers Stash doesn't know (they would never fire): {}".format(bad)
for needed in ("Gallery.Create.Post", "Image.Create.Post", "Gallery.Update.Post", "Gallery.Destroy.Post",
               "Image.Update.Post"):
    assert needed in triggers, needed

types = re.findall(r"^\s+type: (\w+)$", "\n".join(block("settings")), re.M)
assert types and set(types) <= {"STRING", "NUMBER", "BOOLEAN"}, types

# Every task passes task: "true" -- the backend only calls log.Progress then,
# because log.Progress blocks forever outside a queued task.
tasks = "\n".join(block("tasks"))
assert tasks.count("- name:") == tasks.count('task: "true"'), "every task must pass task: \"true\""

# The backend must stay ES5: goja versions differ in how much ES6 they take.
with open(os.path.join(PLUGIN, "comic-reader.js"), encoding="utf-8") as f:
    backend = f.read()
code = re.sub(r"//[^\n]*", "", backend)
for pattern, what in [(r"\b(let|const)\s", "let/const"), (r"=>", "arrow function"), (r"`", "template string"),
                      (r"\bclass\s", "class")]:
    assert not re.search(pattern, code), "backend uses {} (keep it ES5 for goja)".format(what)

# No .yml may live under the plugin besides the manifest: build_site.sh
# publishes every *.yml under plugins/ as a plugin.
ymls = [os.path.join(d, n) for d, _, ns in os.walk(PLUGIN) for n in ns if n.endswith(".yml")]
assert ymls == [MANIFEST], ymls

# --- JavaScript logic ------------------------------------------------------

node = shutil.which("node")
if node is None:
    print("node unavailable, skipping the JavaScript logic checks")
    print("ALL OK")
    raise SystemExit(0)

HARNESS = r"""
const fs = require("fs"), vm = require("vm"), path = require("path"), assert = require("assert");
const dir = process.argv[2];
const read = (p) => fs.readFileSync(path.join(dir, p), "utf8");
// Values built inside a vm context carry that context's Array/Object
// prototypes, which deepStrictEqual rejects; compare plain copies.
const plain = (x) => (x === undefined ? x : JSON.parse(JSON.stringify(x)));
const eq = (a, b, msg) => assert.deepStrictEqual(plain(a), plain(b), msg);

// ---- backend, with Stash's globals stubbed --------------------------------
const calls = [];
let responder = () => ({});
const be = {
  input: { Args: { mode: "noop" } },
  log: { Info() {}, Warn() {}, Error() {}, Debug() {}, Trace() {}, Progress() {} },
  gql: { Do(q, v) { calls.push(q); return responder(q, v || {}); } },
};
vm.createContext(be);
vm.runInContext(read("comic-reader.js"), be);
const family = [{ id: "1", name: "Comic" }, { id: "2", name: "Webtoon" }, { id: "3", name: "Comic Page" }];
const exIds = (f) => f.object_filter.tags.value.excluded.map((e) => e.id);

// no default filter yet: one is created, excluding the whole family
let f = be.hideInFilter(null, "GALLERIES", family);
eq(exIds(f), ["1", "2", "3"]);
assert.strictEqual(f.object_filter.tags.modifier, "INCLUDES");
assert.strictEqual(f.object_filter.tags.value.depth, -1);
assert.strictEqual(f.mode, "GALLERIES");
// already hidden: no change, so no write
assert.strictEqual(be.hideInFilter(JSON.parse(JSON.stringify(f)), "GALLERIES", family), null);

// the user's own filter is kept: sort, other criteria, included tags and their depth
const user = { mode: "GALLERIES", find_filter: { sort: "date", direction: "DESC" },
  object_filter: { organized: { modifier: "EQUALS", value: "true" },
    tags: { modifier: "INCLUDES", value: { items: [{ id: "9", label: "Fav" }], excluded: [{ id: "8", label: "Meh" }], depth: 0 } } } };
f = be.hideInFilter(JSON.parse(JSON.stringify(user)), "GALLERIES", family);
eq(exIds(f), ["8", "1", "2", "3"]);
assert.strictEqual(f.object_filter.tags.value.depth, 0, "a user's depth-0 include must keep its meaning");
eq(f.object_filter.organized, user.object_filter.organized);
eq(f.find_filter, user.find_filter);
// a user's exclude-only criterion keeps its depth: depth applies to the
// exclusions too, so forcing -1 would widen "exclude X" to X's children
const exOnly = { object_filter: { tags: { modifier: "INCLUDES", value: { items: [], excluded: [{ id: "8", label: "Meh" }], depth: 0 } } } };
const fx = be.hideInFilter(JSON.parse(JSON.stringify(exOnly)), "GALLERIES", family);
assert.strictEqual(fx.object_filter.tags.value.depth, 0);
eq(exIds(fx), ["8", "1", "2", "3"]);
// "has any tag" can't carry exclusions in the UI: leave it alone
assert.strictEqual(be.hideInFilter({ object_filter: { tags: { modifier: "NOT_NULL" } } }, "GALLERIES", family), null);

// show removes only the family, and drops a criterion left empty
let s = be.showInFilter(JSON.parse(JSON.stringify(f)), family);
eq(exIds(s), ["8"]);
eq(s.object_filter.tags.value.items, [{ id: "9", label: "Fav" }]);
s = be.showInFilter(be.hideInFilter(null, "IMAGES", family), family);
assert.strictEqual(s.object_filter.tags, undefined);
assert.strictEqual(be.showInFilter(JSON.parse(JSON.stringify(user)), family), null);

assert.ok(be.isCbz([{ path: "/a/Issue 1.CBZ" }]));
assert.ok(be.isCbz([{ path: "/a/x.zip" }, { path: "/a/y.cbz" }]));
assert.ok(!be.isCbz([{ path: "/a/x.zip" }]));
assert.ok(!be.isCbz([{ path: "/a/x.cbz.txt" }]));
assert.ok(!be.isCbz(null));

// canned GraphQL for the hook / removal paths
const cfg = { comicTagId: "1", webtoonTagId: "2", comicPageTagId: "3" };
function respond(extra) {
  return (q, v) => {
    if (q.indexOf("configuration") >= 0) return { configuration: { plugins: { "comic-reader": cfg } } };
    if (q.indexOf("findTags") >= 0) return { findTags: { tags: [{ id: "2", name: "Webtoon" }, { id: "3", name: "Comic Page" }] } };
    return extra(q, v);
  };
}
const tags = { comicTagId: "1", webtoonTagId: "2", comicPageTagId: "3" };

// THE quirk: the NOT candidate query hands back image 20, which is in a
// non-comic gallery AND a comic one. It must not be untagged; 21 (in no
// comic) and 22 (in no gallery at all) must be.
responder = respond((q, v) => {
  if (q.indexOf("findImages") >= 0) {
    if (v.f.NOT) return { findImages: { images: [
      { id: "20", galleries: [{ tags: [] }, { tags: [{ id: "1" }] }] },
      { id: "21", galleries: [{ tags: [{ id: "7" }] }] } ] } };
    if (v.f.galleries && v.f.galleries.modifier === "IS_NULL") return { findImages: { images: [{ id: "22", galleries: [] }] } };
  }
  throw new Error("unexpected query " + q);
});
eq(be.removablePages(tags, null), ["21", "22"]);
// a Webtoon-tagged gallery (a child of Comic) also holds its pages
responder = respond((q, v) => ({ findImages: { images: [{ id: "30", galleries: [{ tags: [{ id: "2" }] }] }] } }));
eq(be.removablePages(tags, "5"), []);

// a sync rewriting a non-comic gallery without touching tags costs no image query
calls.length = 0;
responder = respond((q) => {
  if (q.indexOf("findGallery") >= 0) return { findGallery: { id: "4", files: [], tags: [{ id: "7" }] } };
  throw new Error("unexpected query " + q);
});
assert.strictEqual(be.onHook({ type: "Gallery.Update.Post", id: 4, inputFields: ["title", "date"] }), "skipped");
assert.ok(!calls.some((q) => q.indexOf("findImages") >= 0));

// ...and neither costs a single query -- nor does the reader saving progress
// on a COMIC (custom_fields only), which it does every few page turns. That
// update can't make or unmake a comic, and gallery updates never move images.
calls.length = 0;
responder = () => { throw new Error("no query expected"); };
assert.strictEqual(be.onHook({ type: "Gallery.Update.Post", id: 12, inputFields: ["id", "custom_fields"] }), "skipped");
assert.strictEqual(be.onHook({ type: "Gallery.Update.Post", id: 4, inputFields: ["title", "date"] }), "skipped");
assert.strictEqual(calls.length, 0);

// an image edit that didn't move it between galleries costs nothing -- not
// even the config read -- because every image the sync plugin rewrites fires it
calls.length = 0;
responder = () => { throw new Error("no query expected"); };
assert.strictEqual(be.onHook({ type: "Image.Update.Post", id: 1, inputFields: ["tag_ids", "ids"] }), "skipped");
assert.strictEqual(be.onHook({ type: "Image.Update.Post", id: 1 }), "skipped");
assert.strictEqual(calls.length, 0);

// .cbz opt-out: unmarking a .cbz by editing its tags records "not_comic";
// a scan-time update (no field list) never does; Set Up skips opted-out ones
const writes = [];
function cbzGallery(tagList, cf) {
  return respond((q, v) => {
    if (q.indexOf("findGallery(") >= 0) return { findGallery: { id: "9", files: [{ path: "/c/x.cbz" }], tags: tagList, custom_fields: cf } };
    if (q.indexOf("mutation") >= 0) { writes.push(v.input); return {}; }
    if (q.indexOf("findImages") >= 0) return { findImages: { images: [] } };
    throw new Error("unexpected query " + q);
  });
}
writes.length = 0;
responder = cbzGallery([], {});
be.onHook({ type: "Gallery.Update.Post", id: 9, inputFields: ["id", "tag_ids"] });
eq(writes.filter((w) => w.custom_fields).map((w) => w.custom_fields), [{ partial: { not_comic: "true" } }]);
writes.length = 0;
be.onHook({ type: "Gallery.Update.Post", id: 9 });
assert.strictEqual(writes.filter((w) => w.custom_fields).length, 0, "no field list: not a user's tag edit");
writes.length = 0;
responder = cbzGallery([{ id: "1" }], { not_comic: "true" });
be.onHook({ type: "Gallery.Update.Post", id: 9, inputFields: ["tag_ids"] });
eq(writes.filter((w) => w.custom_fields).map((w) => w.custom_fields), [{ remove: ["not_comic"] }]);
// a newly scanned .cbz the user already said no to stays unmarked
writes.length = 0;
responder = cbzGallery([], { not_comic: "true" });
be.onHook({ type: "Gallery.Create.Post", id: 9 });
assert.ok(!writes.some((w) => w.tag_ids), "opted-out .cbz must not be re-marked on scan");
writes.length = 0;
responder = respond((q, v) => {
  if (q.indexOf("findGalleries") >= 0) return { findGalleries: { galleries: [
    { id: "1", files: [{ path: "/a.cbz" }], tags: [], custom_fields: {} },
    { id: "2", files: [{ path: "/b.cbz" }], tags: [], custom_fields: { not_comic: "true" } },
    { id: "3", files: [{ path: "/c.cbz" }], tags: [{ id: "2" }], custom_fields: {} } ] } };
  if (q.indexOf("mutation") >= 0) { writes.push(v.input); return {}; }
  throw new Error("unexpected query " + q);
});
assert.strictEqual(be.tagCbzGalleries(tags, { "1": true, "2": true, "3": true }), 1);
eq(writes[0].ids, ["1"]);

// hooks stay inert until the plugin has been set up
responder = (q) => ({ configuration: { plugins: {} } });
assert.strictEqual(be.onHook({ type: "Image.Create.Post", id: 1 }), "not set up");

// a scanned page already carrying the tag needs no write
calls.length = 0;
responder = respond((q) => ({ findImage: { id: "5", tags: [{ id: "3" }], galleries: [{ tags: [{ id: "1" }] }] } }));
assert.strictEqual(be.onHook({ type: "Image.Create.Post", id: 5 }), "already tagged");
assert.ok(!calls.some((q) => q.indexOf("mutation") >= 0));

// ---- UI, with a bare window ------------------------------------------------
const win = { PluginApi: { React: { createElement() {}, useState() {}, Component: function Component() {} }, libraries: {}, components: {} } };
win.window = win;
win.Intl = Intl;
vm.createContext(win);
for (const p of ["ui/common.js", "ui/info.js", "ui/overview.js", "ui/reader.js", "ui/series.js", "ui/builder.js"]) vm.runInContext(read(p), win);
const CR = win.ComicReader;

const P = (w, h) => ({ width: w, height: h });
const portrait = Array.from({ length: 13 }, () => P(800, 1200));
let sp = CR.buildSpreads(portrait, true);
eq(sp.slice(0, 3), [[0], [1, 2], [3, 4]]);
eq(sp[sp.length - 1], [11, 12]);
assert.strictEqual(CR.buildSpreads(portrait, false).length, 13);
// a double-page image stands alone and pairing resumes after it
sp = CR.buildSpreads([P(8, 12), P(8, 12), P(24, 12), P(8, 12), P(8, 12)], true);
eq(sp, [[0], [1], [2], [3, 4]]);
eq(CR.buildSpreads([], true), []);

const img = (id, date, path) => ({ id, date, visual_files: [{ path }] });
const order = CR.sortPages([img("a", null, "/c.cbz/page10.png"), img("b", null, "/c.cbz/page2.png"), img("c", null, "/c.cbz/page1.png")]);
eq(order.map((i) => i.id), ["c", "b", "a"], "natural order: page2 before page10");
// The default is the file path, not the date: a comic shared in one post
// gives every page the same date, and file order is the creator's order.
// Patreon post folders start with the post id, so several posts still read
// in release order -- naturally (post 99 before post 100).
const pat = (id, date, post, file) => img(id, date, "/patreon/Artist/posts/" + post + "/images/" + file);
eq(CR.sortPages([pat("b2", "2026-01-02", "100 - Ch 2", "1.png"), pat("a10", "2026-01-01", "99 - Ch 1", "10.png"),
                 pat("a2", "2026-01-01", "99 - Ch 1", "2.png"), pat("a1", "2026-01-01", "99 - Ch 1", "1.png")])
   .map((i) => i.id), ["a1", "a2", "a10", "b2"]);
// path is the default even when dates disagree with it
const posts = [img("p3", "2026-01-03", "/x/1"), img("p1", "2026-01-01", "/x/9"), img("p2", "2026-01-02", "/x/5")];
eq(CR.sortPages(posts).map((i) => i.id), ["p3", "p2", "p1"]);
// "date": post date, then path; an undated page goes last, not first
eq(CR.sortPages(posts, { mode: "date", ids: [] }).map((i) => i.id), ["p1", "p2", "p3"]);
eq(CR.sortPages(posts.concat([img("u", null, "/x/0")]), { mode: "date", ids: [] }).map((i) => i.id), ["p1", "p2", "p3", "u"]);
// "custom": the saved ids first, in that order; pages added since follow in path order
eq(CR.sortPages(posts.concat([img("new", null, "/x/0")]), { mode: "custom", ids: ["p2", "gone", "p3", "p1"] })
   .map((i) => i.id), ["p2", "p3", "p1", "new"]);
// reader page objects carry the path directly
eq(CR.sortPages([{ id: "b", path: "/c/2.png" }, { id: "a", path: "/c/10.png" }]).map((i) => i.id), ["b", "a"]);

// The order saved on a gallery: custom fields can't hold lists, so ids are
// comma-separated; "custom" without ids falls back to path.
eq(CR.orderOf({ custom_fields: {} }), { mode: "path", ids: [] });
eq(CR.orderOf({ custom_fields: { comic_order: "date" } }).mode, "date");
eq(CR.orderOf({ custom_fields: { comic_order: "custom", comic_page_order: "3,1,2" } }), { mode: "custom", ids: ["3", "1", "2"] });
eq(CR.orderOf({ custom_fields: { comic_order: "custom" } }).mode, "path");
eq(CR.orderOf({ custom_fields: { comic_order: "date", comic_page_order: "3,1" } }), { mode: "date", ids: ["3", "1"] }, "kept for switching back");
// The overview labels pages by file name, plus the folder when a comic spans
// several: the sync titles every image of a post alike and every post has a
// 1.jpg, so the post folder is what tells pages apart.
eq(CR.pageFolder("/p/Artist/posts/100002 - Ch 1 Page 2/images/1.png"), "100002 - Ch 1 Page 2", "skips images/");
eq(CR.pageFolder("C:\\lib\\comics\\Space Pals.cbz\\page01.png"), "Space Pals.cbz");
eq(CR.pageFolder("page.png"), "");
assert.ok(CR.manyFolders([{ path: "/p/posts/1 - A/images/1.png" }, { path: "/p/posts/2 - B/images/1.png" }]));
assert.ok(!CR.manyFolders([{ path: "/c/x.cbz/1.png" }, { path: "/c/x.cbz/2.png" }]));
assert.ok(!CR.manyFolders([]));
eq(CR.movePage(["a", "b", "c", "d"], 3, 0), ["d", "a", "b", "c"]);
eq(CR.movePage(["a", "b", "c", "d"], 0, 2), ["b", "c", "a", "d"]);
eq(CR.movePage(["a", "b"], 1, 9), ["a", "b"]);

const strip = { width: 720, height: 3200 };
assert.ok(CR.looksLikeWebtoon([strip, strip, strip], 2));
assert.ok(CR.looksLikeWebtoon([P(800, 1200), strip, strip], 2), "a normal cover doesn't outvote the strips");
assert.ok(!CR.looksLikeWebtoon(portrait, 2));
assert.ok(!CR.looksLikeWebtoon([P(0, 0)], 2));

assert.strictEqual(CR.suggestTitle([{ title: "Moon Quest Chapter 1 Page 1" }, { title: "Moon Quest Chapter 1 Page 12" }]), "Moon Quest Chapter 1");
assert.strictEqual(CR.suggestTitle([{ title: "Ep. 4 - pg 1" }, { title: "Ep. 4 - pg 2" }]), "Ep. 4");
assert.strictEqual(CR.suggestTitle([{ title: "Sketch dump" }]), "Sketch dump");
assert.strictEqual(CR.suggestTitle([{ title: "" }]), "");

assert.strictEqual(CR.galleryTitle({ id: 1, title: "", files: [{ path: "/lib/Space Pals - Issue 1.cbz" }] }), "Space Pals - Issue 1");
assert.strictEqual(CR.galleryTitle({ id: 2, title: "Named" }), "Named");
assert.strictEqual(CR.galleryTitle({ id: 3, title: "", files: [], folder: { path: "/lib/Folder Comic" } }), "Folder Comic");

// Synced progress: stored 1-based on the gallery (readable in Stash), used
// 0-based; Stash hands a true custom field back as 1.
eq(CR.progressOf({ custom_fields: { comic_page: 4, comic_read_at: "2026-09-30T14:00:00Z", comic_seen: 5 } }),
   { page: 3, finished: false, readAt: "2026-09-30T14:00:00Z", seen: 5, hidden: false });
eq(CR.progressOf({ custom_fields: { comic_finished: 1 } }).finished, true);
eq(CR.progressOf({ custom_fields: { comic_finished: "true" } }).finished, true);
eq(CR.progressOf({ custom_fields: { comic_finished: 0 } }).finished, false);
eq(CR.progressOf({}), { page: null, finished: false, readAt: null, seen: 0, hidden: false });
eq(CR.progressOf({ custom_fields: { comic_page: 4, comic_hide_continue: 1 } }).hidden, true);
eq(CR.progressOf({ custom_fields: { comic_page: 4, comic_hide_continue: 1 } }).page, 3, "hiding keeps the place");
eq(CR.progressOf({ custom_fields: { comic_page: 0 } }).page, null);

// Saving progress: a partial update (other custom fields untouched) that
// brings a comic hidden from Continue reading back; finishing clears the page.
const sent = [];
win.fetch = (url, opts) => { sent.push(JSON.parse(opts.body)); return Promise.resolve({ json: () => ({ data: {} }) }); };
CR.saveProgress("12", 3, 5);
CR.saveProgress("12", "end", 5);
CR.hideFromContinue("12");
const cfOf = (i) => sent[i].variables.i.custom_fields;
eq(cfOf(0).partial.comic_page, 4);
eq(cfOf(0).partial.comic_seen, 5);
eq(cfOf(0).remove, ["comic_hide_continue"]);
assert.ok(!("full" in cfOf(0)), "never a full replace");
eq(cfOf(1).partial.comic_finished, true);
eq(cfOf(1).remove, ["comic_hide_continue", "comic_page"]);
assert.ok(!("comic_page" in cfOf(1).partial));
eq(cfOf(2), { partial: { comic_hide_continue: true } });
// Saving an order: custom writes both fields; date/path leave the custom list
// in place so switching back to Custom loses nothing.
sent.length = 0;
CR.saveOrder("12", "custom", ["3", "1"]);
CR.saveOrder("12", "date", ["3", "1"]);
CR.saveOrder("12", "path", ["3", "1"]);
eq(cfOf(0), { partial: { comic_order: "custom", comic_page_order: "3,1" } });
eq(cfOf(1), { partial: { comic_order: "date" } });
eq(cfOf(2), { remove: ["comic_order"] });

// Series: release order is date, then title (naturally), undated last.
const ep = (id, date, title) => ({ id, date, title, custom_fields: {} });
eq(CR.seriesOrder([ep("c", "2026-02-01", "Ch 3"), ep("x", null, "Extra"), ep("b", "2026-01-15", "Ch 10"), ep("a", "2026-01-15", "Ch 2")])
   .map((g) => g.id), ["a", "b", "c", "x"]);
// Continue goes to the one part-way through, else the first unfinished.
const done = (g) => Object.assign(g, { custom_fields: { comic_finished: 1 } });
const mid = (g) => Object.assign(g, { custom_fields: { comic_page: 2 } });
eq(CR.continueTarget([done(ep("a")), ep("b"), mid(ep("c"))]).id, "c");
eq(CR.continueTarget([done(ep("a")), ep("b"), ep("c")]).id, "b");
eq(CR.continueTarget([done(ep("a")), done(ep("b"))]).id, "a", "all read: start again");
assert.strictEqual(CR.continueTarget([]), null);
eq(CR.seriesOf([{ id: 5 }, { id: "8" }], [{ id: "8", name: "Moon Quest" }]).name, "Moon Quest");
assert.strictEqual(CR.seriesOf([{ id: 5 }], [{ id: "8", name: "Moon Quest" }]), null);

// Details panel links: "comics with this tag" must decode with Stash's own
// list-URL decoder (translateJSON in the v0.31 bundle, copied as shipped),
// including a label with parentheses and quotes in it.
function stashTranslate(t, r) {
  let n = false, a = false;
  return [...t].map((i) => {
    if (a) { a = false; return i; }
    switch (i) {
      case "\\": n && (a = true); break;
      case '"': n = !n; break;
      case "(": if (r && !n) return "{"; break;
      case ")": if (r && !n) return "}"; break;
      case "{": if (!r && !n) return "("; break;
      case "}": if (!r && !n) return ")"; break;
    }
    return i;
  }).join("");
}
const crit = { type: "tags", modifier: "INCLUDES", value: { items: [{ id: "6", label: 'Space (Sci-Fi) {"x"}' }], excluded: [], depth: 0 } };
const param = CR.criterionParam(crit);
assert.ok(!/[{}]/.test(decodeURIComponent(param).replace(/"(?:[^"\\]|\\.)*"/g, "")), "no braces outside strings");
eq(JSON.parse(stashTranslate(new URLSearchParams("c=" + param).get("c"), true)), crit);
const tagPath = CR.comicsWithTagPath({ id: 6, name: "Space (Sci-Fi)" });
assert.ok(tagPath.indexOf(CR.ROUTE + "?c=") === 0, tagPath);
eq(JSON.parse(stashTranslate(new URLSearchParams(tagPath.split("?")[1]).get("c"), true)).value.items, [{ id: "6", label: "Space (Sci-Fi)" }]);

// A date-only value is a local date: never shown as the day before.
const shown = CR.formatDate("2026-01-01");
assert.ok(/2026/.test(shown) && /\b1\b/.test(shown) && !/31/.test(shown), shown);
assert.strictEqual(CR.formatDate(""), "");
assert.strictEqual(CR.formatDate("not a date"), "not a date");

// ---- nav patches vs Stash's patch chain -----------------------------------
// Stash (RB / nMt in the v0.31 bundle): each `before` REPLACES the argument
// list with what it returns; each `instead` gets those arguments plus `next`.
// React calls a component with two arguments, and Stash TV's MenuItems patch
// reads `next` as its THIRD. 0.1.0's before returned [props] alone, so Stash
// TV got undefined as a component: React error #130, a blank Stash.
const patches = { before: {}, instead: {}, after: {} };
const reg = (kind) => (name, fn) => { (patches[kind][name] = patches[kind][name] || []).push(fn); };
const R = {
  createElement(type, props, ...kids) {
    const p = Object.assign({}, props || {});
    if (kids.length) p.children = kids.length === 1 ? kids[0] : kids;
    return { type, props: p, key: p.key };
  },
  Fragment: "Fragment",
  Children: { toArray(c) { const out = []; (function walk(x) { if (x == null || x === false) return; if (Array.isArray(x)) x.forEach(walk); else out.push(x); })(c); return out; } },
  Component: function Component() {},
  useState() { return [null, () => {}]; }, useEffect() {}, useRef() { return { current: null }; },
  useMemo(f) { return f(); }, useCallback(f) { return f; },
  // A context's current value is whatever the test sets on it.
  createContext(d) { return { value: d, Provider: "Provider" }; },
  useContext(c) { return c.value; },
};
const ui = {
  PluginApi: { React: R, ReactDOM: {}, libraries: {}, components: {}, hooks: {}, GQL: {},
               register: { route() {} },
               patch: { before: reg("before"), instead: reg("instead"), after: reg("after") } },
  // seedMenuItem() runs at load; answer "no custom menu list" so it does nothing
  fetch: () => Promise.resolve({ json: () => ({ data: { configuration: { interface: { menuItems: null }, plugins: {} } } }) }),
  Promise, Intl, console,
};
ui.window = ui;
vm.createContext(ui);
for (const p of ["ui/common.js", "ui/info.js", "ui/overview.js", "ui/reader.js", "ui/library.js", "ui/series.js", "ui/builder.js", "ui/inject.js", "ui/main.js"]) vm.runInContext(read(p), ui);

function stashCall(name, target, args) {           // RB, as shipped
  for (const b of patches.before[name] || []) args = b.apply(null, args);
  const ins = patches.instead[name] || [];
  return { args, out: ins.length ? ins[0].apply(null, args.concat([target])) : target.apply(null, args) };
}
const CTX = { legacyContext: true };
const itemA = R.createElement("div", { key: "a" }), itemB = R.createElement("div", { key: "b" });
// Stash TV's patch, as shipped: instead("MainNavBar.MenuItems", function({children, ...t}, n, r) {...})
patches.instead["MainNavBar.MenuItems"] = [function (props, n, r) {
  assert.strictEqual(typeof r, "function", "Stash TV's `next` (3rd argument) must still be the component");
  return r(props, n);
}];
const menu = (props) => props.children;
const res = stashCall("MainNavBar.MenuItems", menu, [{ children: [itemA, itemB], other: 1 }, CTX]);
assert.strictEqual(res.args.length, 2, "a before patch must return every argument");
assert.strictEqual(res.args[1], CTX);
assert.strictEqual(res.args[0].other, 1, "other props survive");
assert.ok(Array.isArray(res.args[0].children), "children stay an array for the next patch");
assert.strictEqual(res.args[0].children.length, 4, "Stash's items plus the Comics item and the injector");
assert.ok(res.args[0].children.slice(2).every((c) => c.type === ui.ComicReader.Boundary), "ours sit in error boundaries");

// a transform that throws leaves the arguments exactly as they came
const orig = [{ a: 1 }, CTX];
const kept = ui.ComicReader.keepArgs("t", () => { throw new Error("boom"); }).apply(null, orig);
assert.strictEqual(kept.length, 2); assert.strictEqual(kept[0], orig[0]); assert.strictEqual(kept[1], CTX);

// Settings > Interface > Menu items gets one Comics row; other groups untouched
const cg = patches.before["CheckboxGroup"];
assert.ok(cg && cg.length === 1, "CheckboxGroup patch registered");
let g = cg[0]({ groupId: "menu-items", items: [{ id: "scenes" }] }, CTX);
assert.strictEqual(g.length, 2); assert.strictEqual(g[1], CTX);
eq(g[0].items.map((i) => i.id), ["scenes", "comics"]);
g = cg[0](g[0], CTX);
eq(g[0].items.map((i) => i.id), ["scenes", "comics"], "never added twice");
const other = { groupId: "something-else", items: [{ id: "x" }] };
assert.strictEqual(cg[0](other, CTX)[0], other);

// ---- the Comics list: Stash's gallery list, restricted to comics -----------
// The restriction is ANDed onto what the user's filter builds, never merged
// into it, so a user filtering comics by their own tags can't collide with it.
const hook = ui.ComicReader.comicsHook({ comic: "1" }, { performers: { value: ["7"], modifier: "INCLUDES" } });
const filt = { makeFilter() { return { tags: { value: ["9"], modifier: "INCLUDES" } }; } };
assert.strictEqual(hook(filt), filt, "the hook works on Stash's copy in place");
eq(filt.makeFilter(), {
  tags: { value: ["9"], modifier: "INCLUDES" },
  AND: { tags: { value: ["1"], modifier: "INCLUDES", depth: -1 }, performers: { value: ["7"], modifier: "INCLUDES" } },
}, "user's tags kept; comics + performer ANDed on");
// an AND already in the user's filter is kept, the restriction nests under it
const nested = hook({ makeFilter() { return { AND: { rating100: { value: 80, modifier: "GREATER_THAN" } } }; } }).makeFilter();
eq(nested.AND.rating100, { value: 80, modifier: "GREATER_THAN" });
eq(nested.AND.AND.tags, { value: ["1"], modifier: "INCLUDES", depth: -1 });
const bare = { find_filter: {} };
assert.strictEqual(hook(bare), bare, "not a ListFilterModel: left alone");

// GalleryCardGrid: comic cards inside a Comics list, Stash's grid everywhere
// else -- with exactly the arguments Stash passed, and never both.
const gridPatch = patches.instead["GalleryCardGrid"];
assert.ok(gridPatch && gridPatch.length === 1, "GalleryCardGrid patch registered");
const gridProps = { galleries: [], zoomIndex: 1 };
let nextCalls = [];
const stockGrid = function () { nextCalls.push([this, ...arguments]); return "stock"; };
const self = {};
ui.ComicReader.ComicsContext.value = null;
assert.strictEqual(gridPatch[0].call(self, gridProps, CTX, stockGrid), "stock", "outside Comics: Stash's own grid");
eq(nextCalls.length, 1);
assert.strictEqual(nextCalls[0][0], self); assert.strictEqual(nextCalls[0][1], gridProps); assert.strictEqual(nextCalls[0][2], CTX);
nextCalls = [];
ui.ComicReader.ComicsContext.value = { tags: { comic: "1" } };
const inside = gridPatch[0].call(self, gridProps, CTX, stockGrid);
ui.ComicReader.ComicsContext.value = null;
assert.strictEqual(nextCalls.length, 0, "inside Comics, Stash's grid isn't also rendered (its hooks would run in ours)");
assert.strictEqual(inside.type, ui.ComicReader.Boundary, "comic cards sit in an error boundary");
assert.strictEqual(inside.props.children.type, ui.ComicReader.ComicCardGrid);
eq(inside.props.children.props, gridProps, "with the props Stash gave the grid");
assert.strictEqual(inside.props.fallback.props.next, stockGrid, "and fall back to Stash's grid");

console.log("JS OK");
"""

proc = subprocess.run([node, "-", PLUGIN], input=HARNESS, capture_output=True, text=True)
assert proc.returncode == 0 and proc.stdout.strip().endswith("JS OK"), proc.stdout + proc.stderr

print("ALL OK")
