// Comic Reader -- the Comics section and the cover grid it shares with the
// performer and studio Comics tabs.
(function () {
  "use strict";

  var CR = window.ComicReader;
  if (!CR || !CR.React) return;
  var React = CR.React;
  var h = CR.h;

  // Keep in-app navigation inside Stash's router, but leave real links so
  // middle-click / ctrl-click still open a new tab.
  CR.linkProps = function (history, to) {
    return {
      href: to,
      onClick: function (e) {
        if (!history || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        // The reader's Back returns here only when it knows it was opened
        // from inside the app.
        history.push(to, { cr: true });
      },
    };
  };

  var SORTS = {
    title: { label: "Title", fn: function (a, b) { return CR.naturalCompare(CR.galleryTitle(a), CR.galleryTitle(b)); } },
    oldest: { label: "Oldest first", fn: function (a, b) { return byDate(a, b) || SORTS.title.fn(a, b); } },
    newest: { label: "Newest first", fn: function (a, b) { return byDate(b, a) || SORTS.title.fn(a, b); } },
    added: { label: "Recently added", fn: function (a, b) { return (b.created_at || "").localeCompare(a.created_at || ""); } },
  };
  function byDate(a, b) {
    var da = a.date || "", db = b.date || "";
    return da === db ? 0 : (da < db ? -1 : 1);
  }

  function ComicCard(props) {
    var g = props.gallery;
    var webtoon = CR.hasTag(g.tags, props.tags.webtoon);
    var read = CR.store.get("page:" + g.id, 0);
    var link = CR.linkProps(props.history, CR.readerPath(g.id));
    var creator = CR.creatorOf(g);
    return h("div", { className: "cr-card" },
      h("a", Object.assign({ className: "cr-card-cover", title: "Read " + CR.galleryTitle(g) }, link),
        g.paths && g.paths.cover ? h("img", { src: g.paths.cover, loading: "lazy", alt: "" }) : null,
        webtoon ? h("span", { className: "cr-badge" }, "Webtoon") : null,
        read > 0 && g.image_count
          ? h("div", { className: "cr-card-progress", title: "Read to page " + (read + 1) },
              h("div", { style: { width: Math.min(100, ((read + 1) / g.image_count) * 100) + "%" } }))
          : null),
      h("div", { className: "cr-card-body" },
        h("a", Object.assign({ className: "cr-card-title" }, link), CR.galleryTitle(g)),
        h("div", { className: "cr-card-meta" },
          props.showCreator ? h("span", null, creator.name) : null,
          h("span", null, (g.image_count || 0) + " page" + (g.image_count === 1 ? "" : "s")),
          g.date ? h("span", null, g.date) : null,
          h("a", Object.assign({ className: "cr-card-gallery", title: "Open the gallery in Stash" },
            CR.linkProps(props.history, "/galleries/" + g.id)), h(CR.Icon, { name: "faImages" })))));
  }

  // props: filter (extra GalleryFilterType fields), defaultGroup, storeKey,
  // history, emptyText
  function ComicGrid(props) {
    var key = props.storeKey || "library";
    var q = React.useState("");
    var format = React.useState(CR.store.get(key + ":format", "all"));
    var sort = React.useState(CR.store.get(key + ":sort", "title"));
    var group = React.useState(CR.store.get(key + ":group", props.defaultGroup || "creator"));
    var filterJson = JSON.stringify(props.filter || {});

    var data = CR.useAsync(function () {
      return Promise.all([CR.tags(), CR.findComics(props.filter || {})])
        .then(function (r) { return { tags: r[0], comics: r[1] }; });
    }, [filterJson, props.reloadKey]);

    function persist(state, name) {
      return function (v) { state[1](v); CR.store.set(key + ":" + name, v); };
    }

    if (data.loading) return h(CR.Loading, { text: "Loading comics…" });
    if (data.error) return h(CR.ErrorBox, { error: data.error });

    var tags = data.data.tags;
    var all = data.data.comics;
    var needle = q[0].trim().toLowerCase();
    var list = all.filter(function (g) {
      if (format[0] === "webtoon" && !CR.hasTag(g.tags, tags.webtoon)) return false;
      if (format[0] === "comic" && CR.hasTag(g.tags, tags.webtoon)) return false;
      if (!needle) return true;
      return (CR.galleryTitle(g) + " " + CR.creatorOf(g).name).toLowerCase().indexOf(needle) >= 0;
    });
    list.sort((SORTS[sort[0]] || SORTS.title).fn);

    var controls = h("div", { className: "cr-controls" },
      h("input", {
        className: "clearable-text-field form-control cr-search", type: "search", placeholder: "Search comics…",
        value: q[0], onChange: function (e) { q[1](e.target.value); },
      }),
      h("select", { className: "btn-secondary form-control cr-select", value: format[0],
                    onChange: function (e) { persist(format, "format")(e.target.value); } },
        h("option", { value: "all" }, "All formats"),
        h("option", { value: "comic" }, "Comics"),
        h("option", { value: "webtoon" }, "Webtoons")),
      h("select", { className: "btn-secondary form-control cr-select", value: sort[0],
                    onChange: function (e) { persist(sort, "sort")(e.target.value); } },
        Object.keys(SORTS).map(function (k) { return h("option", { key: k, value: k }, SORTS[k].label); })),
      h("select", { className: "btn-secondary form-control cr-select", value: group[0],
                    onChange: function (e) { persist(group, "group")(e.target.value); } },
        h("option", { value: "creator" }, "Group by creator"),
        h("option", { value: "none" }, "No grouping")),
      h("span", { className: "cr-count text-muted" },
        list.length === all.length ? all.length + " comic" + (all.length === 1 ? "" : "s")
                                   : list.length + " of " + all.length));

    if (!all.length) {
      return h("div", null, h("div", { className: "cr-empty" }, props.emptyText ||
        "No comics yet. Mark a gallery as a comic from its page, or build one from images."));
    }

    var body;
    if (group[0] === "creator") {
      var groups = {};
      var order = [];
      list.forEach(function (g) {
        var c = CR.creatorOf(g);
        if (!groups[c.key]) { groups[c.key] = { name: c.name, items: [] }; order.push(c.key); }
        groups[c.key].items.push(g);
      });
      order.sort(function (a, b) { return CR.naturalCompare(groups[a].name, groups[b].name); });
      body = order.map(function (k) {
        return h("section", { key: k, className: "cr-group" },
          h("h5", { className: "cr-group-title" }, groups[k].name,
            h("span", { className: "text-muted" }, " " + groups[k].items.length)),
          h("div", { className: "cr-grid" }, groups[k].items.map(function (g) {
            return h(ComicCard, { key: g.id, gallery: g, tags: tags, history: props.history, showCreator: false });
          })));
      });
    } else {
      body = h("div", { className: "cr-grid" }, list.map(function (g) {
        return h(ComicCard, { key: g.id, gallery: g, tags: tags, history: props.history, showCreator: true });
      }));
    }
    return h("div", { className: "cr-library-body" }, controls,
      list.length ? body : h("div", { className: "cr-empty" }, "Nothing matches."));
  }

  function Library(props) {
    var setup = React.useState("");
    var reload = React.useState(0);

    function runSetup() {
      setup[1]("Running Set Up Comics… (see Settings > Logs for details)");
      CR.runTask("Set Up Comics")
        .then(function () {
          // The task is queued; give it a moment, then reload the grid.
          setTimeout(function () { setup[1]("Done."); reload[1](reload[0] + 1); }, 2500);
        })
        .catch(function (e) { setup[1]("Could not start set-up: " + e.message); });
    }

    return h("div", { className: "cr-library container-fluid" },
      h("div", { className: "cr-header" },
        h("h2", null, h(CR.Icon, { name: "faBookOpen" }), " Comics"),
        h("div", { className: "cr-header-actions" },
          h("a", Object.assign({ className: "btn btn-primary" }, CR.linkProps(props.history, CR.ROUTE + "/new")),
            h(CR.Icon, { name: "faPlus" }), " New comic from images"),
          h("button", { type: "button", className: "btn btn-secondary", onClick: runSetup,
                        title: "Marks every .cbz as a comic, re-tags comic pages and hides comics from Galleries and Images. Safe to run any time." },
            "Set up / repair"))),
      setup[0] ? h("div", { className: "alert alert-info" }, setup[0]) : null,
      h(ComicGrid, { history: props.history, storeKey: "library", defaultGroup: "creator", reloadKey: reload[0],
        emptyText: "No comics yet. Run \"Set up / repair\" to mark your .cbz files, open a gallery and use \"Mark as comic\", or build one with \"New comic from images\"." }));
  }

  CR.ComicGrid = ComicGrid;
  CR.Library = Library;
})();
