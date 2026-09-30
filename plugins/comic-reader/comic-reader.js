// Comic Reader -- backend (Stash embedded JavaScript, `interface: js`).
//
// Why JavaScript and not the repo's usual Python `raw` plugin: this plugin's
// work is mostly hooks, and Stash runs hooks synchronously inside the request
// that triggered them -- including during a library scan, where
// Image.Create.Post fires once per new image. A `raw` hook would start a
// Python process for every one of those; an embedded JS hook runs in-process
// and its gql.Do() calls go straight to Stash's GraphQL handler, no network.
//
// The runtime is goja, so this file sticks to ES5 (var, function) to stay
// safe across the goja versions Stash has shipped.
//
// The model, all plain Stash tags so the user can see and edit it:
//   Comic       - a gallery that is a comic (read as page spreads)
//   Webtoon     - child of Comic; read as one continuous scroll
//   Comic Page  - child of Comic; put on every image inside a comic so the
//                 Images page can hide pages (its filter can't see galleries)
// "Is a comic" means tagged Comic or any descendant, so tagging a gallery
// Webtoon alone is enough, and one hierarchical exclusion hides everything.

var PLUGIN_ID = "comic-reader";

var TAGS = [
  { key: "comicTagId", name: "Comic", parent: null,
    description: "Galleries read in the Comics section. Added by Comic Reader." },
  { key: "webtoonTagId", name: "Webtoon", parent: "comicTagId",
    description: "Comics read as one continuous vertical scroll. Added by Comic Reader." },
  { key: "comicPageTagId", name: "Comic Page", parent: "comicTagId",
    description: "Images inside a comic. Kept up to date by Comic Reader; used to hide comic pages from the Images page." }
];

// The list views whose default filter leaves comics out. The performer and
// studio views have their own default filters (Stash keys them per view), and
// those pages get a Comics tab instead. Tag views are deliberately left alone:
// on the Comic tag's own page, hiding comics would leave it empty.
var HIDE_VIEWS = [
  { view: "galleries", mode: "GALLERIES" },
  { view: "performer_galleries", mode: "GALLERIES" },
  { view: "studio_galleries", mode: "GALLERIES" },
  { view: "images", mode: "IMAGES" },
  { view: "performer_images", mode: "IMAGES" },
  { view: "studio_images", mode: "IMAGES" }
];

var BATCH = 500;

// --------------------------------------------------------------------------
// config

function readConfig() {
  var data = gql.Do('query { configuration { plugins(include: ["' + PLUGIN_ID + '"]) } }');
  var plugins = (data.configuration && data.configuration.plugins) || {};
  return plugins[PLUGIN_ID] || {};
}

// configurePlugin REPLACES the plugin's whole settings map, so always merge
// into what is there rather than writing only the changed keys.
function writeConfig(changes) {
  var cfg = readConfig();
  for (var k in changes) {
    if (changes.hasOwnProperty(k)) cfg[k] = changes[k];
  }
  gql.Do("mutation ($id: ID!, $input: Map!) { configurePlugin(plugin_id: $id, input: $input) }",
    { id: PLUGIN_ID, input: cfg });
}

// --------------------------------------------------------------------------
// tags

function findTagById(id) {
  if (!id) return null;
  var data = gql.Do("query ($id: ID!) { findTag(id: $id) { id name parents { id } } }", { id: String(id) });
  return data.findTag || null;
}

// Stash enforces uniqueness across tag names AND aliases, so an existing tag
// carrying the name as an alias must be reused, or the create would fail.
function findTagByName(name) {
  var data = gql.Do("query ($q: String!) { findTags(filter: {q: $q, per_page: -1}) { tags { id name aliases parents { id } } } }",
    { q: name });
  var want = name.toLowerCase();
  var tags = data.findTags.tags;
  for (var i = 0; i < tags.length; i++) {
    var names = [tags[i].name].concat(tags[i].aliases || []);
    for (var j = 0; j < names.length; j++) {
      if ((names[j] || "").toLowerCase() === want) return tags[i];
    }
  }
  return null;
}

function hasParent(tag, parentId) {
  var parents = tag.parents || [];
  for (var i = 0; i < parents.length; i++) {
    if (String(parents[i].id) === String(parentId)) return true;
  }
  return false;
}

// The design depends on Webtoon and Comic Page sitting under Comic, so a
// reused tag that isn't there yet gets Comic added as a parent (its other
// parents are kept).
function ensureParent(tag, parentId) {
  if (!parentId || hasParent(tag, parentId)) return;
  var ids = [];
  var parents = tag.parents || [];
  for (var i = 0; i < parents.length; i++) ids.push(parents[i].id);
  ids.push(parentId);
  gql.Do("mutation ($input: TagUpdateInput!) { tagUpdate(input: $input) { id } }",
    { input: { id: tag.id, parent_ids: ids } });
  log.Info("[comic-reader] Put tag '" + tag.name + "' under the Comic tag");
}

// Returns {comicTagId, webtoonTagId, comicPageTagId}, creating whatever is
// missing. Ids are stored in the plugin's settings so renaming a tag in Stash
// doesn't disconnect it.
function ensureTags() {
  var cfg = readConfig();
  var ids = {};
  var changed = {};
  for (var i = 0; i < TAGS.length; i++) {
    var spec = TAGS[i];
    var parentId = spec.parent ? ids[spec.parent] : null;
    var tag = findTagById(cfg[spec.key]) || findTagByName(spec.name);
    if (tag) {
      ensureParent(tag, parentId);
    } else {
      // ignore_auto_tag: Stash's Auto Tag task matches tag names against file
      // paths, so a tag called "Comic" would otherwise tag everything under
      // a folder named "comics" -- turning random galleries into comics.
      var created = gql.Do("mutation ($input: TagCreateInput!) { tagCreate(input: $input) { id name } }",
        { input: { name: spec.name, description: spec.description, ignore_auto_tag: true,
                   parent_ids: parentId ? [parentId] : [] } });
      tag = created.tagCreate;
      log.Info("[comic-reader] Created tag '" + spec.name + "'");
    }
    ids[spec.key] = String(tag.id);
    if (String(cfg[spec.key] || "") !== ids[spec.key]) changed[spec.key] = ids[spec.key];
  }
  var any = false;
  for (var k in changed) { if (changed.hasOwnProperty(k)) any = true; }
  if (any) writeConfig(changed);
  return ids;
}

// Tag ids already configured, without creating anything. Hooks use this: they
// fire on every gallery/image change in the library and must stay no-ops
// until the plugin has actually been set up.
function configuredTags() {
  var cfg = readConfig();
  if (!cfg.comicTagId || !cfg.comicPageTagId) return null;
  return { comicTagId: String(cfg.comicTagId), webtoonTagId: String(cfg.webtoonTagId || ""),
           comicPageTagId: String(cfg.comicPageTagId) };
}

// The Comic tag plus every descendant (Webtoon, Comic Page, and anything the
// user nests under Comic later, e.g. a Manga tag).
function comicFamily(tags) {
  var data = gql.Do("query ($id: ID!) { findTags(tag_filter: {parents: {value: [$id], modifier: INCLUDES, depth: -1}}, filter: {per_page: -1}) { tags { id name } } }",
    { id: tags.comicTagId });
  var family = [{ id: tags.comicTagId, name: "Comic" }];
  var list = data.findTags.tags;
  for (var i = 0; i < list.length; i++) family.push({ id: String(list[i].id), name: list[i].name });
  return family;
}

function isComicTags(tagList, familyIds) {
  for (var i = 0; i < (tagList || []).length; i++) {
    if (familyIds[String(tagList[i].id)]) return true;
  }
  return false;
}

function idSet(family) {
  var set = {};
  for (var i = 0; i < family.length; i++) set[family[i].id] = true;
  return set;
}

// --------------------------------------------------------------------------
// page tags

function bulkImageTags(ids, tagId, mode) {
  for (var i = 0; i < ids.length; i += BATCH) {
    gql.Do("mutation ($input: BulkImageUpdateInput!) { bulkImageUpdate(input: $input) { id } }",
      { input: { ids: ids.slice(i, i + BATCH), tag_ids: { ids: [tagId], mode: mode } } });
  }
}

function imageIds(filter) {
  var data = gql.Do("query ($f: ImageFilterType) { findImages(image_filter: $f, filter: {per_page: -1}) { images { id } } }",
    { f: filter });
  var out = [];
  var list = data.findImages.images;
  for (var i = 0; i < list.length; i++) out.push(list[i].id);
  return out;
}

// "In at least one comic gallery", as an image filter.
function inComicGallery(tags) {
  return { galleries_filter: { tags: { value: [tags.comicTagId], modifier: "INCLUDES", depth: -1 } } };
}

// Tag every page of every comic, and untag images that are no longer in any
// comic. Two queries for the whole library, whatever its size.
function refreshAllPages(tags) {
  var add = inComicGallery(tags);
  add.tags = { value: [tags.comicPageTagId], modifier: "EXCLUDES" };
  var toAdd = imageIds(add);
  if (toAdd.length) bulkImageTags(toAdd, tags.comicPageTagId, "ADD");

  var toRemove = removablePages(tags, null);
  if (toRemove.length) bulkImageTags(toRemove, tags.comicPageTagId, "REMOVE");
  return { tagged: toAdd.length, untagged: toRemove.length };
}

function imagesWithGalleries(filter) {
  var data = gql.Do("query ($f: ImageFilterType) { findImages(image_filter: $f, filter: {per_page: -1}) { images { id galleries { tags { id } } } } }",
    { f: filter });
  return data.findImages.images;
}

// Images carrying Comic Page that sit in no comic gallery (optionally only
// those in one gallery). An image can be in several galleries -- a page
// posted on its own is also in its post's gallery -- so it only loses the tag
// once NO comic holds it.
//
// That "no comic holds it" check is done here, not in the query, because
// Stash evaluates NOT over a galleries_filter per joined gallery row, not per
// image: an image in one comic and one non-comic gallery matches both
// `galleries_filter: comic` AND `NOT: {galleries_filter: comic}`. Trusting
// the NOT stripped real comic pages. It is still used, as a cheap candidate
// list -- whichever way Stash evaluates it, it returns at least every image
// with a non-comic gallery -- plus the pages left in no gallery at all.
function removablePages(tags, galleryId) {
  var base = function () { return { tags: { value: [tags.comicPageTagId], modifier: "INCLUDES" } }; };
  var candidates;
  if (galleryId) {
    var one = base();
    one.galleries = { value: [String(galleryId)], modifier: "INCLUDES" };
    candidates = imagesWithGalleries(one);
  } else {
    var some = base();
    some.NOT = inComicGallery(tags);
    var none = base();
    none.galleries = { value: [], modifier: "IS_NULL" };
    candidates = imagesWithGalleries(some).concat(imagesWithGalleries(none));
  }
  var family = idSet(comicFamily(tags));
  var out = [];
  var seen = {};
  for (var i = 0; i < candidates.length; i++) {
    var img = candidates[i];
    if (seen[img.id]) continue;
    seen[img.id] = true;
    var held = false;
    for (var g = 0; g < (img.galleries || []).length; g++) {
      if (isComicTags(img.galleries[g].tags, family)) { held = true; break; }
    }
    if (!held) out.push(img.id);
  }
  return out;
}

function syncGalleryPages(tags, galleryId, isComic) {
  if (isComic) {
    var missing = imageIds({ galleries: { value: [String(galleryId)], modifier: "INCLUDES" },
                             tags: { value: [tags.comicPageTagId], modifier: "EXCLUDES" } });
    if (missing.length) bulkImageTags(missing, tags.comicPageTagId, "ADD");
    return missing.length;
  }
  var stale = removablePages(tags, galleryId);
  if (stale.length) bulkImageTags(stale, tags.comicPageTagId, "REMOVE");
  return -stale.length;
}

// --------------------------------------------------------------------------
// .cbz

function isCbz(files) {
  for (var i = 0; i < (files || []).length; i++) {
    if (/\.cbz$/i.test(files[i].path || "")) return true;
  }
  return false;
}

// Every .cbz gallery that isn't a comic yet. Stash's path filter is a
// case-insensitive substring match, so the extension is re-checked here.
function tagCbzGalleries(tags, familyIds) {
  var data = gql.Do('query { findGalleries(gallery_filter: {path: {value: ".cbz", modifier: INCLUDES}}, filter: {per_page: -1}) { galleries { id files { path } tags { id } } } }');
  var ids = [];
  var list = data.findGalleries.galleries;
  for (var i = 0; i < list.length; i++) {
    if (isCbz(list[i].files) && !isComicTags(list[i].tags, familyIds)) ids.push(list[i].id);
  }
  if (ids.length) {
    gql.Do("mutation ($input: BulkGalleryUpdateInput!) { bulkGalleryUpdate(input: $input) { id } }",
      { input: { ids: ids, tag_ids: { ids: [tags.comicTagId], mode: "ADD" } } });
  }
  return ids.length;
}

// --------------------------------------------------------------------------
// default filters (hide comics from the Galleries / Images pages)

function readUI() {
  var data = gql.Do("query { configuration { ui } }");
  return (data.configuration && data.configuration.ui) || {};
}

// The saved-filter shape the Stash UI itself writes for a tags criterion:
// {value: {items, excluded, depth}, modifier}. Exclusions live in `excluded`
// with modifier INCLUDES -- the UI has no EXCLUDES modifier for tags any more.
function hideInFilter(saved, mode, family) {
  var filter = saved || { mode: mode, find_filter: {}, object_filter: {}, ui_options: {} };
  filter.object_filter = filter.object_filter || {};
  var crit = filter.object_filter.tags;
  if (!crit) {
    crit = { modifier: "INCLUDES", value: { items: [], excluded: [], depth: -1 } };
  }
  crit.value = crit.value || { items: [], excluded: [], depth: -1 };
  crit.value.items = crit.value.items || [];
  crit.value.excluded = crit.value.excluded || [];
  if (crit.modifier !== "INCLUDES" && crit.modifier !== "INCLUDES_ALL") {
    // "has no tags" already leaves comics out; "has any tag" can't carry
    // exclusions (the UI clears them), so there is nothing safe to add.
    return null;
  }
  var have = {};
  for (var i = 0; i < crit.value.excluded.length; i++) have[String(crit.value.excluded[i].id)] = true;
  var added = false;
  // Every family member is listed explicitly rather than relying on depth, so
  // a user filter that includes tags at depth 0 keeps its meaning.
  for (var j = 0; j < family.length; j++) {
    if (!have[family[j].id]) {
      crit.value.excluded.push({ id: family[j].id, label: family[j].name });
      added = true;
    }
  }
  if (!crit.value.items.length) crit.value.depth = -1;
  filter.object_filter.tags = crit;
  return added ? filter : null;
}

function showInFilter(saved, family) {
  if (!saved || !saved.object_filter || !saved.object_filter.tags) return null;
  var crit = saved.object_filter.tags;
  var excluded = (crit.value && crit.value.excluded) || [];
  var drop = idSet(family);
  var kept = [];
  for (var i = 0; i < excluded.length; i++) {
    if (!drop[String(excluded[i].id)]) kept.push(excluded[i]);
  }
  if (kept.length === excluded.length) return null;
  crit.value.excluded = kept;
  if (!kept.length && !(crit.value.items || []).length) delete saved.object_filter.tags;
  return saved;
}

function setDefaultFilters(tags, hide) {
  var family = comicFamily(tags);
  var defaults = readUI().defaultFilters || {};
  var changed = [];
  for (var i = 0; i < HIDE_VIEWS.length; i++) {
    var v = HIDE_VIEWS[i];
    var saved = defaults[v.view] ? JSON.parse(JSON.stringify(defaults[v.view])) : null;
    var next = hide ? hideInFilter(saved, v.mode, family) : showInFilter(saved, family);
    if (!next) continue;
    gql.Do("mutation ($key: String!, $value: Any) { configureUISetting(key: $key, value: $value) }",
      { key: "defaultFilters." + v.view, value: next });
    changed.push(v.view);
  }
  return changed;
}

// --------------------------------------------------------------------------
// hooks

function ctxField(ctx, name) {
  if (!ctx) return undefined;
  if (ctx[name] !== undefined) return ctx[name];
  var upper = name.charAt(0).toUpperCase() + name.slice(1);
  return ctx[upper];
}

function onHook(ctx) {
  var type = ctxField(ctx, "type");
  var id = ctxField(ctx, "id");
  var tags = configuredTags();
  if (!tags || !type) return "not set up";

  if (type === "Gallery.Destroy.Post") {
    // The gallery is gone, so there is no way to ask which images it held;
    // tidy the whole library instead (one query unless something changed).
    var stale = removablePages(tags, null);
    if (stale.length) bulkImageTags(stale, tags.comicPageTagId, "REMOVE");
    return "untagged " + stale.length;
  }

  if (type === "Image.Create.Post") {
    var img = gql.Do("query ($id: ID!) { findImage(id: $id) { id tags { id } galleries { tags { id } } } }",
      { id: String(id) }).findImage;
    if (!img) return "no image";
    var pageTag = {};
    pageTag[tags.comicPageTagId] = true;
    if (isComicTags(img.tags, pageTag)) return "already tagged";
    // Most new images belong to no gallery at all; only then pay for the
    // family lookup.
    if (!(img.galleries || []).length) return "not a comic page";
    var family = idSet(comicFamily(tags));
    for (var g = 0; g < img.galleries.length; g++) {
      if (isComicTags(img.galleries[g].tags, family)) {
        bulkImageTags([img.id], tags.comicPageTagId, "ADD");
        return "tagged page";
      }
    }
    return "not a comic page";
  }

  if (type === "Gallery.Create.Post" || type === "Gallery.Update.Post") {
    var gal = gql.Do("query ($id: ID!) { findGallery(id: $id) { id files { path } tags { id } } }",
      { id: String(id) }).findGallery;
    if (!gal) return "no gallery";
    var fam = idSet(comicFamily(tags));
    var comic = isComicTags(gal.tags, fam);
    if (type === "Gallery.Create.Post" && !comic && isCbz(gal.files)) {
      // This update fires Gallery.Update.Post, which tags whatever pages
      // exist so far; later pages are caught by Image.Create.Post.
      gql.Do("mutation ($input: BulkGalleryUpdateInput!) { bulkGalleryUpdate(input: $input) { id } }",
        { input: { ids: [gal.id], tag_ids: { ids: [tags.comicTagId], mode: "ADD" } } });
      log.Info("[comic-reader] Marked new .cbz gallery " + gal.id + " as a comic");
      return "marked cbz";
    }
    // A non-comic update that didn't touch tags can't have unmarked a comic,
    // so there is nothing to untag -- skip the query. This keeps a big sync
    // that rewrites thousands of galleries cheap.
    var fields = ctxField(ctx, "inputFields");
    if (!comic && fields && fields.length && fields.indexOf("tag_ids") < 0) return "skipped";
    var n = syncGalleryPages(tags, gal.id, comic);
    return (n >= 0 ? "tagged " : "untagged ") + Math.abs(n);
  }
  return "ignored " + type;
}

// --------------------------------------------------------------------------
// entry point

function progress(args, p) {
  // log.Progress blocks forever when nothing is listening (hooks and
  // runPluginOperation), so only queued tasks -- which pass task: "true" via
  // their defaultArgs -- report progress.
  if (args.task === "true" || args.task === true) log.Progress(p);
}

function main() {
  var args = (input && (input.Args || input.args)) || {};
  var mode = args.mode || "";
  try {
    if (mode === "hook") {
      return { Output: onHook(args.hookContext) };
    }
    if (mode === "tags") {
      return { Output: ensureTags() };
    }
    if (mode === "pages") {
      var t = ensureTags();
      if (args.galleryId) {
        var g = gql.Do("query ($id: ID!) { findGallery(id: $id) { id tags { id } } }", { id: String(args.galleryId) }).findGallery;
        if (!g) return { Error: "Gallery " + args.galleryId + " not found" };
        return { Output: syncGalleryPages(t, g.id, isComicTags(g.tags, idSet(comicFamily(t)))) };
      }
      return { Output: refreshAllPages(t) };
    }
    if (mode === "setup") {
      progress(args, 0);
      var tags = ensureTags();
      var family = comicFamily(tags);
      progress(args, 0.2);
      var cbz = tagCbzGalleries(tags, idSet(family));
      log.Info("[comic-reader] Marked " + cbz + " .cbz galler" + (cbz === 1 ? "y" : "ies") + " as comics");
      progress(args, 0.5);
      var pages = refreshAllPages(tags);
      log.Info("[comic-reader] Comic pages: tagged " + pages.tagged + ", untagged " + pages.untagged);
      progress(args, 0.8);
      var views = setDefaultFilters(tags, true);
      log.Info("[comic-reader] Hid comics from: " + (views.length ? views.join(", ") : "(already hidden everywhere)"));
      progress(args, 1);
      return { Output: { tags: tags, cbz: cbz, pages: pages, hidden: views } };
    }
    if (mode === "hide" || mode === "show") {
      var tg = ensureTags();
      var changed = setDefaultFilters(tg, mode === "hide");
      log.Info("[comic-reader] " + (mode === "hide" ? "Hid comics from: " : "Showed comics again in: ") +
        (changed.length ? changed.join(", ") : "(nothing to change)"));
      return { Output: changed };
    }
    return { Error: "Unknown mode '" + mode + "'" };
  } catch (e) {
    var msg = "[comic-reader] " + mode + " failed: " + (e && e.message ? e.message : e);
    log.Error(msg);
    return { Error: msg };
  }
}

main();
