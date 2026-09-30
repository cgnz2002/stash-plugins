// Comic Reader -- shared UI helpers. Loaded first; the other ui/*.js files
// hang their components off window.ComicReader.
//
// Stash concatenates every listed plugin script into one file, so each file is
// its own IIFE and shares state only through this namespace.
(function () {
  "use strict";

  var api = window.PluginApi;
  if (!api || !api.React) return;

  var CR = (window.ComicReader = window.ComicReader || {});
  var React = api.React;

  CR.api = api;
  CR.React = React;
  CR.h = React.createElement;
  CR.PLUGIN_ID = "comic-reader";
  CR.ROUTE = "/plugins/comics";
  CR.libs = api.libraries || {};
  CR.Router = CR.libs.ReactRouterDOM || {};
  CR.Bootstrap = CR.libs.Bootstrap || {};
  CR.FA = CR.libs.FontAwesomeSolid || {};

  // ------------------------------------------------------------------ gql

  CR.gql = function (query, variables) {
    return fetch("/graphql", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ query: query, variables: variables || {} }),
    })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (res.errors && res.errors.length) throw new Error(res.errors[0].message);
        return res.data;
      });
  };

  CR.runOperation = function (args) {
    return CR.gql(
      "mutation ($id: ID!, $args: Map) { runPluginOperation(plugin_id: $id, args: $args) }",
      { id: CR.PLUGIN_ID, args: args }
    ).then(function (d) { return d.runPluginOperation; });
  };

  CR.runTask = function (taskName) {
    return CR.gql(
      "mutation ($id: ID!, $t: String) { runPluginTask(plugin_id: $id, task_name: $t) }",
      { id: CR.PLUGIN_ID, t: taskName }
    );
  };

  // ----------------------------------------------------------------- tags

  var tagsPromise = null;

  // The plugin's tag ids. Read from its settings; the first time (nothing set
  // up yet) the backend creates the tags and stores their ids.
  CR.tags = function () {
    if (tagsPromise) return tagsPromise;
    tagsPromise = CR.gql('query { configuration { plugins(include: ["' + CR.PLUGIN_ID + '"]) } }')
      .then(function (d) {
        var cfg = ((d.configuration || {}).plugins || {})[CR.PLUGIN_ID] || {};
        if (cfg.comicTagId && cfg.webtoonTagId && cfg.comicPageTagId) return cfg;
        return CR.runOperation({ mode: "tags" });
      })
      .then(function (cfg) {
        return {
          comic: String(cfg.comicTagId),
          webtoon: String(cfg.webtoonTagId),
          page: String(cfg.comicPageTagId),
          ratio: Number(cfg.webtoonRatio) > 0 ? Number(cfg.webtoonRatio) : 2,
        };
      })
      .catch(function (e) { tagsPromise = null; throw e; });
    return tagsPromise;
  };

  // Ids of the Comic tag and everything under it. Unmarking a comic removes
  // all of them, including child tags the user added (a "Manga" tag, say).
  CR.family = function () {
    return CR.tags().then(function (tags) {
      return CR.gql("query ($id: ID!) { findTags(tag_filter: {parents: {value: [$id], modifier: INCLUDES, depth: -1}}, filter: {per_page: -1}) { tags { id } } }",
        { id: tags.comic })
        .then(function (d) {
          return [tags.comic].concat(d.findTags.tags.map(function (t) { return String(t.id); }));
        });
    });
  };

  CR.isComic = function (tagList, familyIds) {
    return (tagList || []).some(function (t) { return familyIds.indexOf(String(t.id)) >= 0; });
  };

  // Stash's own pages read through Apollo's cache, so after changing a
  // gallery behind its back, ask it to refetch what is on screen.
  CR.refreshStash = function () {
    try {
      var svc = api.utils && api.utils.StashService;
      var client = svc && svc.getClient && svc.getClient();
      if (client && client.refetchQueries) client.refetchQueries({ include: "active" });
    } catch (e) { /* cosmetic only */ }
  };

  // "Is a comic": tagged Comic or anything under it, so Webtoon alone counts.
  CR.comicFilter = function (tags, extra) {
    var f = { tags: { value: [tags.comic], modifier: "INCLUDES", depth: -1 } };
    for (var k in extra || {}) if (extra.hasOwnProperty(k)) f[k] = extra[k];
    return f;
  };

  CR.hasTag = function (tagList, id) {
    return (tagList || []).some(function (t) { return String(t.id) === String(id); });
  };

  // Add/remove tags on one gallery without touching its other tags.
  CR.editGalleryTags = function (galleryId, add, remove) {
    var q = "mutation ($i: BulkGalleryUpdateInput!) { bulkGalleryUpdate(input: $i) { id } }";
    var steps = Promise.resolve();
    if (add && add.length) {
      steps = steps.then(function () {
        return CR.gql(q, { i: { ids: [galleryId], tag_ids: { ids: add, mode: "ADD" } } });
      });
    }
    if (remove && remove.length) {
      steps = steps.then(function () {
        return CR.gql(q, { i: { ids: [galleryId], tag_ids: { ids: remove, mode: "REMOVE" } } });
      });
    }
    return steps;
  };

  CR.setCustomField = function (galleryId, key, value) {
    var cf = value === null ? { remove: [key] } : { partial: {} };
    if (value !== null) cf.partial[key] = value;
    return CR.gql("mutation ($i: GalleryUpdateInput!) { galleryUpdate(input: $i) { id } }",
      { i: { id: galleryId, custom_fields: cf } });
  };

  // ------------------------------------------------------------ galleries

  CR.GALLERY_FIELDS =
    "id title date created_at image_count custom_fields " +
    "files { path } folder { path } paths { cover } " +
    "studio { id name image_path } performers { id name } tags { id name }";

  function basename(p) {
    var parts = String(p || "").split(/[\\/]/);
    return parts[parts.length - 1] || "";
  }

  // A .cbz gallery has no title of its own (Stash shows the file name), so
  // fall back the same way.
  CR.galleryTitle = function (g) {
    if (g.title) return g.title;
    if (g.files && g.files.length) return basename(g.files[0].path).replace(/\.[^.]+$/, "");
    if (g.folder && g.folder.path) return basename(g.folder.path);
    return "Gallery " + g.id;
  };

  CR.creatorOf = function (g) {
    if (g.studio) return { key: "s" + g.studio.id, name: g.studio.name };
    if (g.performers && g.performers.length) {
      return { key: "p" + g.performers[0].id, name: g.performers[0].name };
    }
    return { key: "none", name: "Unknown creator" };
  };

  // Stash's interface settings that change how cards look (cached).
  var uiConfig = null;
  CR.uiConfig = function () {
    if (!uiConfig) {
      uiConfig = CR.gql("query { configuration { interface { showStudioAsText } } }")
        .then(function (d) { return d.configuration.interface || {}; })
        .catch(function () { uiConfig = null; return {}; });
    }
    return uiConfig;
  };

  CR.findComics = function (extraFilter) {
    return CR.tags().then(function (tags) {
      return CR.gql(
        "query ($f: GalleryFilterType) { findGalleries(gallery_filter: $f, filter: {per_page: -1, sort: \"title\"}) { galleries { " +
          CR.GALLERY_FIELDS + " } } }",
        { f: CR.comicFilter(tags, extraFilter) }
      ).then(function (d) { return d.findGalleries.galleries; });
    });
  };

  // ---------------------------------------------------------------- pages

  // "page2" before "page10".
  var collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
  CR.naturalCompare = function (a, b) { return collator.compare(a || "", b || ""); };

  function pagePath(img) {
    var f = (img.visual_files || [])[0];
    return (f && f.path) || img.title || "";
  }

  // Reading order: by date, then by path. A .cbz has no dates, so its file
  // names decide; a comic assembled from one-page-per-post images gets each
  // post's date, which is the order the artist released them in -- whatever
  // order the images were added to the gallery.
  CR.sortPages = function (images) {
    return images.slice().sort(function (a, b) {
      var da = a.date || "", db = b.date || "";
      if (da !== db) return da < db ? -1 : 1;
      return CR.naturalCompare(pagePath(a), pagePath(b));
    });
  };

  CR.fetchPages = function (galleryId) {
    return CR.gql(
      "query ($f: ImageFilterType) { findImages(image_filter: $f, filter: {per_page: -1, sort: \"path\"}) { images { " +
        "id title date paths { image thumbnail } visual_files { ... on ImageFile { path width height } } } } }",
      { f: { galleries: { value: [String(galleryId)], modifier: "INCLUDES" } } }
    ).then(function (d) {
      return CR.sortPages(d.findImages.images).map(function (img) {
        var f = (img.visual_files || [])[0] || {};
        return { id: img.id, src: img.paths.image, thumb: img.paths.thumbnail,
                 width: f.width || 0, height: f.height || 0, title: img.title };
      });
    });
  };

  // Tall strips mean a webtoon. The median ignores a stray cover or credits
  // page that would throw an average off.
  CR.looksLikeWebtoon = function (pages, ratio) {
    var r = pages.filter(function (p) { return p.width > 0; })
      .map(function (p) { return p.height / p.width; })
      .sort(function (a, b) { return a - b; });
    if (!r.length) return false;
    return r[Math.floor(r.length / 2)] >= (ratio || 2);
  };

  // ------------------------------------------------------------ react bits

  CR.useAsync = function (fn, deps) {
    var s = React.useState({ loading: true, data: null, error: null });
    React.useEffect(function () {
      var live = true;
      setStateSafe({ loading: true, data: null, error: null });
      Promise.resolve()
        .then(fn)
        .then(function (data) { setStateSafe({ loading: false, data: data, error: null }); })
        .catch(function (e) { setStateSafe({ loading: false, data: null, error: e }); });
      function setStateSafe(v) { if (live) s[1](v); }
      return function () { live = false; };
    }, deps);
    return s[0];
  };

  CR.Icon = function (props) {
    var Icon = api.components && api.components.Icon;
    var icon = CR.FA[props.name];
    if (!Icon || !icon) return null;
    return CR.h(Icon, { icon: icon, className: props.className });
  };

  CR.Button = function (props) {
    var B = CR.Bootstrap.Button;
    if (B) return CR.h(B, props, props.children);
    return CR.h("button", Object.assign({ className: "btn btn-" + (props.variant || "secondary") }, props), props.children);
  };

  // Stash's own spinner, so themes that restyle it restyle ours too.
  CR.Loading = function (props) {
    var LI = api.components && api.components.LoadingIndicator;
    if (LI) return CR.h(LI, { message: props.text });
    return CR.h("div", { className: "cr-loading" }, props.text || "Loading…");
  };

  // Stash's toasts (the same ones its own pages use for "Updated gallery").
  var noToast = {
    success: function (m) { console.info("[comic-reader]", m); },
    error: function (e) { console.error("[comic-reader]", e); },
  };
  CR.useToast = function () {
    var use = api.hooks && api.hooks.useToast;
    return use ? use() : noToast;
  };

  // GridCard (Stash's card, used by every list page) lives in a lazily loaded
  // chunk that a plugin route doesn't trigger by itself. Load it, and render
  // nothing until it is there. Returns true while loading.
  CR.useCardComponents = function () {
    var use = api.hooks && api.hooks.useLoadComponents;
    var chunk = api.loadableComponents && api.loadableComponents.TagLink;
    var loading = use && chunk ? use([chunk]) : false;
    return loading && !(api.components && api.components.GridCard);
  };

  // Stash's card sizing (TagLink chunk, v0.31): fill the row with cards as
  // close to the zoom level's preferred width as fits. GridCard ignores the
  // width on phones, where CSS makes cards full width.
  CR.cardWidth = function (containerWidth, preferred) {
    if (!containerWidth) return preferred;
    var usable = containerWidth - 30;
    return usable / Math.ceil(usable / preferred) - 10;
  };

  // [callback ref, width] -- a callback ref, so the observer attaches whenever
  // the element actually mounts (it may render after a loading state).
  CR.useContainerWidth = function () {
    var node = React.useState(null);
    var width = React.useState(0);
    var ref = React.useCallback(function (el) { node[1](el); }, []);
    React.useEffect(function () {
      var el = node[0];
      if (!el) return;
      var last = el.getBoundingClientRect().width;
      width[1](last);
      if (typeof ResizeObserver === "undefined") return;
      var ro = new ResizeObserver(function (entries) {
        var w = entries[0].contentRect.width;
        if (Math.abs(w - last) > 20) { last = w; width[1](w); }
      });
      ro.observe(el);
      return function () { ro.disconnect(); };
    }, [node[0]]);
    return [ref, width[0]];
  };

  CR.ErrorBox = function (props) {
    var e = props.error;
    return CR.h("div", { className: "cr-error alert alert-danger" },
      "Something went wrong: " + (e && e.message ? e.message : String(e)));
  };

  CR.readerPath = function (galleryId) { return CR.ROUTE + "/read/" + galleryId; };

  // Per-viewer conveniences only (last page, spread preference). Storage can
  // be unavailable -- private windows, blocked site data -- so never rely on it.
  CR.store = {
    get: function (k, dflt) {
      try {
        var v = window.localStorage.getItem("comic-reader:" + k);
        return v === null ? dflt : JSON.parse(v);
      } catch (e) { return dflt; }
    },
    set: function (k, v) {
      try { window.localStorage.setItem("comic-reader:" + k, JSON.stringify(v)); } catch (e) { /* ignore */ }
    },
  };
})();
