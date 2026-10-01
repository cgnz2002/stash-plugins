// Comic Reader -- the Comics listing, and the same listing on performer and
// studio Comics tabs.
//
// The listing IS Stash's own gallery list (FilteredGalleryList): search,
// saved filters and "Set as default", the filter dialog and chips, sort, per
// page, operations menu, display modes, zoom, pagination and selection are
// all Stash's, and so is anything other plugins add to them. This file only
//   * restricts it to comics, through the list's filterHook -- the same
//     mechanism a performer page uses to scope its Galleries tab, so the
//     restriction never shows up in (or gets saved into) the user's filter;
//   * gives it its own view key, so the Comics page keeps its own default
//     filter instead of the Galleries page's (which hides comics);
//   * swaps the card grid for comic cards, via the GalleryCardGrid patch in
//     main.js, only inside a Comics list (ComicsContext).
(function () {
  "use strict";

  var CR = window.ComicReader;
  if (!CR || !CR.React) return;
  var React = CR.React;
  var h = CR.h;
  var api = CR.api;

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

  // Set inside a Comics list; the GalleryCardGrid patch reads it.
  CR.ComicsContext = React.createContext(null);

  // own AND restrict, nested so an existing AND in the user's filter is kept.
  function andFilter(own, restrict) {
    var out = Object.assign({}, own || {});
    out.AND = own && own.AND ? andFilter(own.AND, restrict) : restrict;
    return out;
  }
  CR.andFilter = andFilter;

  // Stash runs a list's filterHook on a COPY of the user's filter before
  // querying (the list's own search, counts and sidebar suggestions all use
  // that copy). The restriction is ANDed onto whatever makeFilter() builds,
  // rather than pushed in as a tags criterion, so a user filtering comics by
  // their own tags doesn't collide with it.
  CR.comicsHook = function (tags, extra) {
    var restrict = Object.assign({ tags: { value: [tags.comic], modifier: "INCLUDES", depth: -1 } }, extra || {});
    return function (filter) {
      var make = filter && filter.makeFilter;
      if (typeof make !== "function") return filter;
      filter.makeFilter = function () { return andFilter(make.apply(filter, arguments), restrict); };
      return filter;
    };
  };

  // FilteredGalleryList and GridCard live in lazily loaded chunks that a
  // plugin route doesn't load by itself. Returns true while loading.
  CR.useListComponents = function () {
    var use = api.hooks && api.hooks.useLoadComponents;
    var lc = api.loadableComponents || {};
    var chunks = [lc.Galleries, lc.TagLink].filter(Boolean);
    var loading = use && chunks.length ? use(chunks) : false;
    var comps = api.components || {};
    return loading && !(comps.FilteredGalleryList && comps.GridCard);
  };

  // props: view, alterQuery, extra (GalleryFilterType fields ANDed on),
  // extraOperations, history
  function ComicsList(props) {
    var loading = CR.useListComponents();
    var tags = CR.useAsync(CR.tags, []);
    var extraJson = JSON.stringify(props.extra || {});
    // Stable: the list re-queries whenever the hook's identity changes.
    var hook = React.useMemo(function () {
      return tags.data ? CR.comicsHook(tags.data, props.extra) : null;
    }, [tags.data, extraJson]);
    var ctx = React.useMemo(function () {
      return { tags: tags.data, history: props.history };
    }, [tags.data, props.history]);

    if (loading || tags.loading) return h(CR.Loading, { text: "Loading comics…" });
    if (tags.error) return h(CR.ErrorBox, { error: tags.error });
    var List = api.components.FilteredGalleryList;
    if (!List) return h(CR.ErrorBox, { error: new Error("Stash's gallery list isn't available in this version") });
    return h(CR.ComicsContext.Provider, { value: ctx },
      h(List, {
        view: props.view,
        alterQuery: props.alterQuery,
        filterHook: hook,
        extraOperations: props.extraOperations || [],
      }));
  }

  // ------------------------------------------------------------ comic card

  // Portrait covers want somewhat narrower cards than Stash's landscape
  // gallery cards (280/340/480/640), but not so narrow that the title and the
  // details row under the cover crowd (0.2.0's 210 default did); the zoom
  // slider works the same way.
  var ZOOM_WIDTHS = [200, 280, 360, 460];

  // Built on GridCard with the gallery-card class names, and Stash's own
  // GalleryCard.Overlays / .Details / .Popovers for the studio logo, date and
  // the tag / performer / image-count buttons -- so a theme, or another
  // plugin that patches those parts, applies here too. What's comic-specific
  // is the portrait cover, the badges, the Read / Continue button and
  // GridCard's own progress bar for how far it's read.
  //
  // props.progress is CR.progressOf() for the gallery (synced on the
  // gallery); without it, this browser's old local note is used. `plain`
  // leaves out Stash's card parts, for shelves whose gallery data is ours
  // rather than Stash's list data.
  function ComicCard(props) {
    var ctx = React.useContext(CR.ComicsContext) || {};
    var comps = api.components;
    var g = props.gallery;
    var title = CR.galleryTitle(g);
    var prog = props.progress || { page: CR.store.get("page:" + g.id, 0) || null, finished: false, seen: 0 };
    var count = g.image_count || 0;
    var reading = prog.page !== null && prog.page > 0 && prog.page + 1 < count;
    var fresh = prog.seen > 0 && count > prog.seen ? count - prog.seen : 0;
    var webtoon = ctx.tags && CR.hasTag(g.tags, ctx.tags.webtoon);
    var sub = function (name) {
      var C = comps["GalleryCard." + name];
      return C && !props.plain ? h(CR.Boundary, { name: "card " + name }, h(C, props)) : null;
    };

    var cover = h("div", { className: "cr-cover" },
      g.paths && g.paths.cover
        ? h("img", { className: "gallery-card-image cr-cover-img", loading: "lazy", alt: title, src: g.paths.cover })
        : null,
      h("div", { className: "cr-cover-badges" },
        fresh ? h("span", { className: "badge badge-primary" }, fresh + " new") : null,
        prog.finished && !reading && !fresh
          ? h("span", { className: "badge badge-success", title: "Read to the end" }, h(CR.Icon, { name: "faCheck" }), " Read")
          : null,
        webtoon ? h("span", { className: "badge badge-info" }, "Webtoon") : null,
        h("span", { className: "badge badge-secondary" }, count + (count === 1 ? " page" : " pages"))),
      h("span", { className: "cr-cover-read btn btn-primary btn-sm" },
        h(CR.Icon, { name: "faBookOpen" }),
        reading ? " Continue · p. " + (prog.page + 1) : prog.finished ? " Read again" : " Read"));

    return h(comps.GridCard, {
      className: "gallery-card cr-comic-card zoom-" + (props.zoomIndex || 0),
      // An object `to`, so the reader knows it was opened from inside the
      // app and its Back returns here.
      url: { pathname: CR.readerPath(g.id), state: { cr: true } },
      width: props.cardWidth,
      title: title,
      linkClassName: "gallery-card-header",
      image: cover,
      overlays: props.onHide
        ? h("button", {
            type: "button", className: "btn btn-secondary btn-sm cr-shelf-hide",
            title: "Remove from Continue reading (your place is kept)",
            onClick: function (e) { e.preventDefault(); e.stopPropagation(); props.onHide(); },
          }, h(CR.Icon, { name: "faXmark" }))
        : sub("Overlays"),
      details: sub("Details"),
      popovers: sub("Popovers"),
      selected: props.selected,
      selecting: props.selecting,
      onSelectedChanged: props.onSelectedChanged,
      resumeTime: reading ? prog.page + 1 : undefined,
      duration: reading ? count : undefined,
    });
  }
  CR.ComicCard = ComicCard;

  // Progress for the galleries on screen, re-read whenever the list changes.
  CR.useProgress = function (galleries) {
    var ids = (galleries || []).map(function (g) { return String(g.id); });
    var key = ids.join(",");
    var s = React.useState({});
    React.useEffect(function () {
      var live = true;
      CR.fetchProgress(ids).then(function (m) { if (live) s[1](m); });
      return function () { live = false; };
    }, [key]);
    return s[0];
  };

  // Stands in for GalleryCardGrid inside a Comics list: same props, same
  // row markup, portrait card widths.
  function ComicCardGrid(props) {
    var measure = CR.useContainerWidth();
    var progress = CR.useProgress(props.galleries);
    var zoom = props.zoomIndex || 0;
    var width = CR.cardWidth(measure[1], ZOOM_WIDTHS[Math.max(0, Math.min(3, zoom))]);
    var selected = props.selectedIds || { size: 0, has: function () { return false; } };
    return h("div", { className: "row justify-content-center", ref: measure[0] },
      (props.galleries || []).map(function (g) {
        return h(ComicCard, {
          key: g.id, gallery: g, cardWidth: width, zoomIndex: zoom, progress: progress[g.id],
          selecting: selected.size > 0, selected: selected.has(g.id),
          onSelectedChanged: function (on, shift) { if (props.onSelectChange) props.onSelectChange(g.id, on, shift); },
        });
      }));
  }
  CR.ComicCardGrid = ComicCardGrid;

  // ---------------------------------------------------------------- shelf

  // A titled, sideways-scrolling row of cards, headed like Stash's own front
  // page rows.
  CR.Shelf = function (props) {
    return h("div", { className: "recommendation-row cr-shelf" },
      h("div", { className: "recommendation-row-head" },
        h("div", null, h("h2", null, props.title)),
        props.more || null),
      h("div", { className: "cr-shelf-cards" }, props.children));
  };

  // Comics part-way through, most recently read first. The x on a card takes
  // it out of this row without touching its progress; reading it again
  // brings it back (CR.saveProgress clears the flag).
  function ContinueReading() {
    var toast = CR.useToast();
    var gone = React.useState({}); // hidden just now, before a refetch
    var data = CR.useAsync(function () {
      return CR.tags().then(function (tags) {
        return CR.gql("query ($f: GalleryFilterType) { findGalleries(gallery_filter: $f, filter: {per_page: -1}) { galleries { " +
            CR.GALLERY_FIELDS + " } } }",
          { f: CR.comicFilter(tags, { custom_fields: [{ field: "comic_page", modifier: "NOT_NULL" }] }) });
      }).then(function (d) {
        return d.findGalleries.galleries.filter(function (g) { return !CR.progressOf(g).hidden; }).sort(function (a, b) {
          var ra = CR.progressOf(a).readAt || "", rb = CR.progressOf(b).readAt || "";
          return ra < rb ? 1 : ra > rb ? -1 : 0;
        }).slice(0, 20);
      });
    }, []);
    var shown = (data.data || []).filter(function (g) { return !gone[0][g.id]; });
    if (!shown.length) return null;
    function hide(g) {
      gone[1](function (cur) { var next = Object.assign({}, cur); next[g.id] = true; return next; });
      CR.hideFromContinue(g.id)
        .then(function () { toast.success("Removed " + CR.galleryTitle(g) + " from Continue reading. Your place is kept."); })
        .catch(function (e) {
          gone[1](function (cur) { var next = Object.assign({}, cur); delete next[g.id]; return next; });
          toast.error(e);
        });
    }
    return h(CR.Shelf, { title: "Continue reading" },
      shown.map(function (g) {
        return h(ComicCard, { key: g.id, gallery: g, plain: true, cardWidth: 170, progress: CR.progressOf(g),
                              onHide: function () { hide(g); } });
      }));
  }
  CR.ContinueReading = ContinueReading;

  // ------------------------------------------------------------- the page

  // Comics | Series, as Stash's own tabs.
  CR.LibraryTabs = function (props) {
    var Nav = CR.Bootstrap.Nav;
    var tabs = [{ key: "comics", label: "Comics", to: CR.ROUTE }, { key: "series", label: "Series", to: CR.seriesPath() }];
    if (!Nav) return null;
    return h(Nav, { variant: "tabs", className: "cr-tabs", activeKey: props.active },
      tabs.map(function (t) {
        return h(Nav.Item, { key: t.key },
          h(Nav.Link, Object.assign({ eventKey: t.key }, CR.linkProps(props.history, t.to)), t.label));
      }));
  };

  // "Add to series…" for the comics selected in a list.
  CR.useSeriesOperation = function (onChanged) {
    var dialog = React.useState(null); // gallery ids
    var op = React.useMemo(function () {
      return {
        text: "Add to series…",
        isDisplayed: function (result, filter, selectedIds) { return !!selectedIds && selectedIds.size > 0; },
        onClick: function (result, filter, selectedIds) { dialog[1](Array.from(selectedIds || [])); },
      };
    }, []);
    var modal = dialog[0] && CR.SeriesDialog
      ? h(CR.SeriesDialog, {
          galleryIds: dialog[0], current: null,
          onClose: function (changed) { dialog[1](null); if (changed && onChanged) onChanged(); },
        })
      : null;
    return { op: op, modal: modal };
  };

  function Library(props) {
    var toast = CR.useToast();
    var history = props.history;
    var series = CR.useSeriesOperation();
    // Added to the list's own "…" operations menu.
    var ops = React.useMemo(function () {
      return [
        series.op,
        { text: "New comic from images…", onClick: function () { history.push(CR.ROUTE + "/new", { cr: true }); } },
        {
          text: "Set up / repair comics",
          onClick: function () {
            CR.runTask("Set Up Comics")
              .then(function () { toast.success("Set Up Comics started. See Settings > Logs for what it changed."); })
              .catch(function (e) { toast.error(e); });
          },
        },
      ];
    }, [history, series.op]);
    return h("div", { className: "cr-library" },
      h(CR.LibraryTabs, { active: "comics", history: history }),
      h(CR.Boundary, { name: "continue reading" }, h(ContinueReading)),
      h(ComicsList, { view: "comics", alterQuery: true, history: history, extraOperations: ops }),
      series.modal);
  }

  CR.ComicsList = ComicsList;
  CR.Library = Library;
})();
