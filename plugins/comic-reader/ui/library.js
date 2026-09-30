// Comic Reader -- the Comics section and the cover grid it shares with the
// performer and studio Comics tabs.
//
// Built from Stash's own pieces so it looks and themes like the Galleries
// page: GridCard (the card every list page uses) with the gallery-card class
// names, the list toolbar's markup and zoom slider, the studio logo overlay,
// and GridCard's progress bar for how far a comic has been read.
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

  CR.Link = function (props) {
    var L = CR.Router.Link;
    var p = Object.assign({}, props);
    delete p.children;
    if (L) return h(L, p, props.children);
    return h("a", Object.assign({ href: props.to }, p), props.children);
  };

  // Portrait covers want narrower cards than Stash's landscape gallery cards
  // (280/340/480/640); the zoom slider works the same way.
  var ZOOM_WIDTHS = [160, 210, 280, 380];

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

  // Stash's studio overlay: the logo, or the name when the user turned on
  // Interface > "Show studio as text".
  function StudioOverlay(props) {
    var s = props.studio;
    if (!s) return null;
    return h("div", { className: "studio-overlay" },
      h(CR.Link, { to: "/studios/" + s.id },
        props.asText || !s.image_path
          ? s.name
          : h("img", { className: "image-thumbnail", loading: "lazy", alt: s.name, src: s.image_path })));
  }

  function ComicCard(props) {
    var g = props.gallery;
    var GridCard = CR.api.components.GridCard;
    var TruncatedText = CR.api.components.TruncatedText;
    var Button = CR.Bootstrap.Button;
    var ButtonGroup = CR.Bootstrap.ButtonGroup;
    var webtoon = CR.hasTag(g.tags, props.tags.webtoon);
    var read = CR.store.get("page:" + g.id, 0);
    var creator = CR.creatorOf(g);

    var details = h("div", { className: "gallery-card__details" },
      g.date ? h("span", { className: "gallery-card__date" }, g.date) : null,
      props.showCreator && TruncatedText
        ? h(TruncatedText, { className: "gallery-card__description", text: creator.name, lineCount: 1 })
        : null);

    var popovers = h(React.Fragment, null,
      h("hr"),
      h(ButtonGroup || "div", { className: "card-popovers" },
        h(CR.Link, { to: "/galleries/" + g.id, title: "Open the gallery in Stash" },
          h(Button || "button", { className: "minimal" },
            h(CR.Icon, { name: "faImages" }), h("span", null, g.image_count || 0)))));

    return h(GridCard, {
      className: "gallery-card cr-comic-card zoom-" + props.zoom,
      url: CR.readerPath(g.id),
      width: props.width,
      linkClassName: "gallery-card-header",
      title: CR.galleryTitle(g),
      image: h("img", { className: "gallery-card-image", loading: "lazy", alt: CR.galleryTitle(g),
                        src: (g.paths && g.paths.cover) || "" }),
      overlays: h(React.Fragment, null,
        h(StudioOverlay, { studio: g.studio, asText: props.studioAsText }),
        webtoon ? h("span", { className: "badge badge-info cr-format-badge" }, "Webtoon") : null),
      details: details,
      popovers: popovers,
      // GridCard draws Stash's own resume bar when given these.
      resumeTime: read > 0 ? read + 1 : undefined,
      duration: read > 0 ? g.image_count : undefined,
    });
  }

  function CardRow(props) {
    var measure = CR.useContainerWidth();
    var width = CR.cardWidth(measure[1], ZOOM_WIDTHS[props.zoom]);
    return h("div", { className: "row justify-content-center", ref: measure[0] },
      props.items.map(function (g) {
        return h(ComicCard, { key: g.id, gallery: g, tags: props.tags, zoom: props.zoom, width: width,
                              showCreator: props.showCreator, studioAsText: props.studioAsText });
      }));
  }

  // props: filter (extra GalleryFilterType fields), defaultGroup, storeKey,
  // reloadKey, emptyText, actions (extra toolbar buttons)
  function ComicGrid(props) {
    var key = props.storeKey || "library";
    var cardsLoading = CR.useCardComponents();
    var q = React.useState("");
    var format = React.useState(CR.store.get(key + ":format", "all"));
    var sort = React.useState(CR.store.get(key + ":sort", "title"));
    var group = React.useState(CR.store.get(key + ":group", props.defaultGroup || "creator"));
    var zoom = React.useState(CR.store.get("zoom", 1));
    var filterJson = JSON.stringify(props.filter || {});

    var data = CR.useAsync(function () {
      return Promise.all([CR.tags(), CR.findComics(props.filter || {}), CR.uiConfig()])
        .then(function (r) { return { tags: r[0], comics: r[1], studioAsText: !!r[2].showStudioAsText }; });
    }, [filterJson, props.reloadKey]);

    function persist(state, name) {
      return function (v) { state[1](v); CR.store.set(key + ":" + name, v); };
    }

    if (data.loading || cardsLoading) return h(CR.Loading, { text: "Loading comics…" });
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

    function select(state, name, options) {
      return h("div", { className: "mr-2 mb-2 btn-group" },
        h("select", { className: "btn-secondary form-control", value: state[0],
                      onChange: function (e) { persist(state, name)(e.target.value); } },
          options.map(function (o) { return h("option", { key: o[0], value: o[0] }, o[1]); })));
    }

    // Same markup as Stash's list toolbar, so it lines up and themes alike.
    var toolbar = h("div", { className: "filtered-list-toolbar btn-toolbar" },
      h("div", { className: "mr-2 mb-2 btn-group" },
        h("input", {
          className: "clearable-text-field form-control", type: "search", placeholder: "Search…",
          value: q[0], onChange: function (e) { q[1](e.target.value); },
        })),
      select(format, "format", [["all", "All formats"], ["comic", "Comics"], ["webtoon", "Webtoons"]]),
      select(sort, "sort", Object.keys(SORTS).map(function (k) { return [k, SORTS[k].label]; })),
      select(group, "group", [["creator", "Group by creator"], ["none", "No grouping"]]),
      props.actions || null,
      h("div", { className: "zoom-slider-container" },
        h("input", {
          className: "zoom-slider form-control", type: "range", min: 0, max: 3, value: zoom[0],
          onChange: function (e) { var z = Number(e.target.value); zoom[1](z); CR.store.set("zoom", z); },
        })));

    var count = h("div", { className: "cr-count text-muted text-center mb-2" },
      list.length === all.length ? all.length + " comic" + (all.length === 1 ? "" : "s")
                                 : list.length + " of " + all.length + " comics");

    if (!all.length) {
      return h("div", null, toolbar, h("div", { className: "cr-empty text-muted" }, props.emptyText ||
        "No comics yet. Mark a gallery as a comic from its page, or build one from images."));
    }

    var body;
    if (group[0] === "creator") {
      var groups = {};
      var order = [];
      list.forEach(function (g) {
        var c = CR.creatorOf(g);
        if (!groups[c.key]) { groups[c.key] = { name: c.name, studio: g.studio, items: [] }; order.push(c.key); }
        groups[c.key].items.push(g);
      });
      order.sort(function (a, b) { return CR.naturalCompare(groups[a].name, groups[b].name); });
      body = order.map(function (k) {
        var gr = groups[k];
        return h("section", { key: k, className: "cr-group" },
          h("h5", { className: "cr-group-title" },
            gr.studio ? h(CR.Link, { to: "/studios/" + gr.studio.id }, gr.name) : gr.name,
            h("span", { className: "badge badge-pill badge-secondary left-spacing" }, gr.items.length)),
          h(CardRow, { items: gr.items, tags: tags, zoom: zoom[0], showCreator: false, studioAsText: data.data.studioAsText }));
      });
    } else {
      body = h(CardRow, { items: list, tags: tags, zoom: zoom[0], showCreator: true, studioAsText: data.data.studioAsText });
    }
    return h("div", { className: "cr-library-body" }, toolbar, count,
      list.length ? body : h("div", { className: "cr-empty text-muted" }, "No comics match."));
  }

  function Library(props) {
    var toast = CR.useToast();
    var reload = React.useState(0);
    var Dropdown = CR.Bootstrap.Dropdown;

    function runSetup() {
      CR.runTask("Set Up Comics")
        .then(function () {
          toast.success("Set Up Comics started. See Settings > Logs for what it changed.");
          // The task is queued; give it a moment, then reload the grid.
          setTimeout(function () { reload[1](function (n) { return n + 1; }); }, 2500);
        })
        .catch(function (e) { toast.error(e); });
    }

    // "New" as a primary button and maintenance tucked in the "…" menu, like
    // the operations menu on Stash's own list pages.
    var actions = h(React.Fragment, null,
      h("div", { className: "mr-2 mb-2 btn-group" },
        h("a", Object.assign({ className: "btn btn-primary" }, CR.linkProps(props.history, CR.ROUTE + "/new")),
          h(CR.Icon, { name: "faPlus" }), " New comic")),
      Dropdown
        ? h("div", { className: "mr-2 mb-2 btn-group" },
            h(Dropdown, null,
              h(Dropdown.Toggle, { variant: "secondary", title: "More" }, h(CR.Icon, { name: "faEllipsisH" })),
              h(Dropdown.Menu, { className: "bg-secondary text-white" },
                h(Dropdown.Item, { className: "bg-secondary text-white", onClick: runSetup,
                                   title: "Marks every .cbz as a comic, re-tags comic pages and hides comics from Galleries and Images. Safe to run any time." },
                  "Set up / repair comics"))))
        : null);

    return h("div", { className: "cr-library" },
      h(ComicGrid, { history: props.history, storeKey: "library", defaultGroup: "creator", reloadKey: reload[0],
        actions: actions,
        emptyText: "No comics yet. Use … > \"Set up / repair comics\" to mark your .cbz files, open a gallery and use \"Mark as comic\", or build one with \"New comic\"." }));
  }

  CR.ComicGrid = ComicGrid;
  CR.Library = Library;
})();
