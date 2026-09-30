// Comic Reader -- series.
//
// A series is a Stash tag under "Comic Series"; its comics are the galleries
// carrying it, in release order (date, then title). That covers both ways
// creators publish: a chapter per post (each chapter its own gallery, the
// series ties them together) and a page per post (one growing gallery, which
// can be a series of one). Being tags, series show up in Stash's own filters,
// tag pages and bulk edit too.
//
// This file: the "Add to series" dialog, the Series tab (a grid of series)
// and a series' own page (Continue, then Stash's comic list for it).
(function () {
  "use strict";

  var CR = window.ComicReader;
  if (!CR || !CR.React) return;
  var React = CR.React;
  var h = CR.h;
  var api = CR.api;

  // ------------------------------------------------------------- dialog

  // props: galleryIds, current (series or null), onClose(changed)
  function SeriesDialog(props) {
    var B = CR.Bootstrap;
    var Modal = B.Modal;
    var list = CR.useAsync(CR.seriesList, []);
    var name = React.useState(props.current ? props.current.name : "");
    var busy = React.useState(false);
    var toast = CR.useToast();
    var n = props.galleryIds.length;
    var what = n === 1 ? "this comic" : n + " comics";

    function done(msg) {
      busy[1](false);
      CR.refreshStash();
      toast.success(msg);
      props.onClose(true);
    }
    function fail(e) { busy[1](false); toast.error(e); }
    function save() {
      var want = name[0].trim();
      if (!want || busy[0]) return;
      busy[1](true);
      CR.ensureSeries(want)
        .then(function (id) { return CR.setSeries(props.galleryIds, id); })
        .then(function () { done("Added " + what + " to " + want); })
        .catch(fail);
    }
    function remove() {
      busy[1](true);
      CR.setSeries(props.galleryIds, null)
        .then(function () { done("Took " + what + " out of " + props.current.name); })
        .catch(fail);
    }
    if (!Modal) return null;

    var series = list.data || [];
    return h(Modal, { show: true, onHide: function () { props.onClose(false); }, className: "cr-series-dialog" },
      h(Modal.Header, { closeButton: true },
        h(Modal.Title, null, props.current ? "Change series" : "Add to a series")),
      h(Modal.Body, null,
        h("label", { htmlFor: "cr-series-name" }, "Series"),
        h("input", {
          id: "cr-series-name", className: "text-input form-control", list: "cr-series-names",
          value: name[0], autoFocus: true, placeholder: "Pick one, or type a new name",
          onChange: function (e) { name[1](e.target.value); },
          onKeyDown: function (e) { if (e.key === "Enter") save(); },
        }),
        h("datalist", { id: "cr-series-names" },
          series.map(function (s) { return h("option", { key: s.id, value: s.name }); })),
        h("small", { className: "form-text text-muted" },
          "Comics in a series read in release order: by date, then title. A comic belongs to one series at a time.")),
      h(Modal.Footer, null,
        props.current
          ? h(CR.Button, { variant: "danger", className: "mr-auto", disabled: busy[0], onClick: remove }, "Remove from series")
          : null,
        h(CR.Button, { variant: "secondary", disabled: busy[0], onClick: function () { props.onClose(false); } }, "Cancel"),
        h(CR.Button, { variant: "primary", disabled: busy[0] || !name[0].trim(), onClick: save }, "Save")));
  }
  CR.SeriesDialog = SeriesDialog;

  // ------------------------------------------------------------- data

  // Every series with its comics (release order), in one query.
  function loadAllSeries() {
    return CR.seriesList().then(function (series) {
      if (!series.length) return [];
      return CR.gql("query ($f: GalleryFilterType) { findGalleries(gallery_filter: $f, filter: {per_page: -1}) { galleries { " +
          CR.GALLERY_FIELDS + " } } }",
        { f: { tags: { value: series.map(function (s) { return s.id; }), modifier: "INCLUDES" } } })
        .then(function (d) {
          var by = {};
          d.findGalleries.galleries.forEach(function (g) {
            var s = CR.seriesOf(g.tags, series);
            if (s) (by[s.id] = by[s.id] || []).push(g);
          });
          return series.map(function (s) { return summarise(s, CR.seriesOrder(by[s.id] || [])); });
        });
    });
  }

  function summarise(s, comics) {
    var read = 0, lastRead = "";
    comics.forEach(function (c) {
      var p = CR.progressOf(c);
      if (p.finished && p.page === null) read += 1;
      if (p.readAt && p.readAt > lastRead) lastRead = p.readAt;
    });
    return { series: s, comics: comics, read: read, lastRead: lastRead, next: CR.continueTarget(comics) };
  }

  // ------------------------------------------------------------- cards

  // The same card as a comic's, for a series: its cover (the tag's image, or
  // the first comic's), how many comics and how many read, and GridCard's
  // progress bar for the series as a whole.
  function SeriesCard(props) {
    var x = props.item;
    var first = x.comics[0];
    var cover = x.series.image || (first && first.paths && first.paths.cover);
    var n = x.comics.length;
    return h(api.components.GridCard, {
      className: "gallery-card cr-comic-card cr-series-card zoom-" + (props.zoomIndex || 0),
      url: CR.seriesPath(x.series.id),
      width: props.cardWidth,
      title: x.series.name,
      linkClassName: "gallery-card-header",
      image: h("div", { className: "cr-cover" },
        cover ? h("img", { className: "gallery-card-image cr-cover-img", loading: "lazy", alt: x.series.name, src: cover }) : null,
        h("div", { className: "cr-cover-badges" },
          x.read === n && n > 0
            ? h("span", { className: "badge badge-success" }, h(CR.Icon, { name: "faCheck" }), " Read")
            : null,
          h("span", { className: "badge badge-secondary" }, n + (n === 1 ? " comic" : " comics")))),
      details: h("div", { className: "card-section-details text-muted" },
        x.read ? x.read + " of " + n + " read" : "Not started"),
      resumeTime: x.read > 0 && x.read < n ? x.read : undefined,
      duration: x.read > 0 && x.read < n ? n : undefined,
    });
  }

  // ------------------------------------------------------- Series tab

  // Every series, the ones read most recently first. Series are few enough
  // that a plain grid does; Stash's tag list can't host them, because its
  // "Set as default" is fixed to the Tags page's own view.
  function SeriesIndex(props) {
    var loading = CR.useCardComponents();
    var data = CR.useAsync(loadAllSeries, []);
    var measure = CR.useContainerWidth();
    var width = CR.cardWidth(measure[1], 280);
    var items = (data.data || []).slice().sort(function (a, b) {
      if (a.lastRead !== b.lastRead) return a.lastRead < b.lastRead ? 1 : -1;
      return CR.naturalCompare(a.series.name, b.series.name);
    });

    var body;
    if (loading || data.loading) body = h(CR.Loading, { text: "Loading series…" });
    else if (data.error) body = h(CR.ErrorBox, { error: data.error });
    else if (!items.length) {
      body = h("div", { className: "cr-empty text-muted" },
        h("p", null, "No series yet."),
        h("p", null, "Put comics in a series from a comic's details in the reader, a gallery page, " +
          "or by selecting comics here and choosing … > Add to series."));
    } else {
      body = h("div", { className: "row justify-content-center", ref: measure[0] },
        items.map(function (x) {
          return h(SeriesCard, { key: x.series.id, item: x, cardWidth: width, zoomIndex: 1 });
        }));
    }
    return h("div", { className: "cr-library" },
      h(CR.LibraryTabs, { active: "series", history: props.history }),
      h("div", { ref: items.length ? null : measure[0] }, body));
  }
  CR.SeriesIndex = SeriesIndex;

  // ------------------------------------------------------- series page

  // A series' own view gets its own default filter; the first time, it is
  // seeded to release order (Stash's gallery default is by path).
  var seeded = {};
  function seedDefault(view) {
    if (seeded[view]) return seeded[view];
    seeded[view] = CR.gql("query { configuration { ui } }").then(function (d) {
      var defaults = ((d.configuration || {}).ui || {}).defaultFilters || {};
      if (defaults[view]) return;
      return CR.gql("mutation ($k: String!, $v: Any) { configureUISetting(key: $k, value: $v) }",
        { k: "defaultFilters." + view, v: { mode: "GALLERIES", find_filter: { sort: "date", direction: "ASC" } } })
        // Stash's list reads default filters from its cached settings, so
        // refresh that cache before the list is first drawn.
        .then(CR.refetchStash);
    }).catch(function () { /* the list just starts in Stash's default order */ });
    return seeded[view];
  }

  function SeriesPage(props) {
    var id = String(props.seriesId);
    var history = props.history;
    var reload = React.useState(0);
    var data = CR.useAsync(function () {
      return Promise.all([CR.seriesList(), CR.seriesComics(id), seedDefault("series_comics")])
        .then(function (r) {
          var s = r[0].filter(function (x) { return x.id === id; })[0];
          return s ? summarise(s, r[1]) : null;
        });
    }, [id, reload[0]]);
    var ops = CR.useSeriesOperation(function () { reload[1](function (k) { return k + 1; }); });
    var extra = React.useMemo(function () { return [ops.op]; }, [ops.op]);

    if (data.loading) return h(CR.Loading, { text: "Loading series…" });
    if (data.error) return h(CR.ErrorBox, { error: data.error });
    var x = data.data;
    if (!x) {
      return h("div", { className: "cr-empty text-muted" }, "This series no longer exists. ",
        h("a", CR.linkProps(history, CR.seriesPath()), "All series"));
    }

    var n = x.comics.length;
    var target = x.next;
    var tp = target ? CR.progressOf(target) : null;
    var label = !target ? null
      : tp.page !== null ? "Continue " + CR.galleryTitle(target) + " · p. " + (tp.page + 1)
      : x.read === 0 ? "Start reading"
      : x.read === n ? "Read again"
      : "Read next: " + CR.galleryTitle(target);

    return h("div", { className: "cr-library cr-series-page" },
      h(CR.LibraryTabs, { active: "series", history: history }),
      h("div", { className: "cr-series-head" },
        h("h2", null, x.series.name),
        x.series.description ? h("p", { className: "pre" }, x.series.description) : null,
        h("div", { className: "text-muted" },
          n + (n === 1 ? " comic" : " comics") + " · " + x.read + " read"),
        h("div", { className: "cr-series-actions" },
          target
            ? h("a", Object.assign({ className: "btn btn-primary" }, CR.linkProps(history, CR.readerPath(target.id))),
                h(CR.Icon, { name: "faBookOpen" }), " ", label)
            : null,
          h("a", Object.assign({ className: "btn btn-secondary", title: "Rename it, give it a cover image or a description" },
            CR.linkProps(history, "/tags/" + id)), "Edit series in Stash"))),
      h(CR.ComicsList, {
        view: "series_comics", alterQuery: true, history: history, extraOperations: extra,
        extra: { tags: { value: [id], modifier: "INCLUDES" } },
      }),
      ops.modal);
  }
  CR.SeriesPage = SeriesPage;
})();
