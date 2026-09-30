// Comic Reader -- "New comic from images".
//
// For comics released one page per post with no Patreon collection tying
// them together: those posts have no gallery of their own (a single-image
// post doesn't get one), so the pages are loose images. Pick a creator, pick
// the pages, and this makes a gallery tagged Comic or Webtoon. Reading order
// is the reader's usual one (post date, then file name), so picking order
// doesn't matter.
(function () {
  "use strict";

  var CR = window.ComicReader;
  if (!CR || !CR.React) return;
  var React = CR.React;
  var h = CR.h;
  var PAGE_SIZE = 120;

  var IMAGE_FIELDS = "id title date paths { thumbnail } performers { id } " +
    "visual_files { ... on ImageFile { path width height } }";

  // "Moon Quest Chapter 1 Page 1".."Page 5" -> "Moon Quest Chapter 1".
  function suggestTitle(images) {
    var titles = images.map(function (i) { return (i.title || "").trim(); }).filter(Boolean);
    if (!titles.length) return "";
    var prefix = titles[0];
    titles.forEach(function (t) {
      var n = 0;
      while (n < prefix.length && n < t.length && prefix[n].toLowerCase() === t[n].toLowerCase()) n++;
      prefix = prefix.slice(0, n);
    });
    if (titles.length === 1) prefix = titles[0];
    return prefix
      .replace(/[\s\-–—:_#,.(]*\b(page|pg|p|part|pt)\.?\s*\d*\s*$/i, "")
      .replace(/[\s\-–—:_#,.(]+$/, "")
      .trim();
  }

  function Builder(props) {
    var studios = CR.useAsync(function () {
      return CR.gql('query { findStudios(filter: {per_page: -1, sort: "name"}) { studios { id name image_count } } }')
        .then(function (d) { return d.findStudios.studios.filter(function (s) { return s.image_count > 0; }); });
    }, []);

    var studio = React.useState(CR.store.get("builder:studio", ""));
    var q = React.useState("");
    var hideUsed = React.useState(true);
    var page = React.useState(1);
    var images = React.useState([]);
    var total = React.useState(0);
    var loading = React.useState(false);
    var error = React.useState(null);
    var selected = React.useState({}); // id -> image
    var lastClicked = React.useRef(null);
    var title = React.useState("");
    var titleTouched = React.useRef(false);
    var format = React.useState("comic");
    var creating = React.useState("");

    // Reload from page 1 whenever the filters change.
    React.useEffect(function () { page[1](1); images[1]([]); }, [studio[0], q[0], hideUsed[0]]);

    React.useEffect(function () {
      if (!studio[0] && !q[0].trim()) { images[1]([]); total[1](0); return; }
      var live = true;
      loading[1](true);
      error[1](null);
      CR.tags().then(function (tags) {
        var f = {};
        if (studio[0]) f.studios = { value: [studio[0]], modifier: "INCLUDES", depth: -1 };
        if (q[0].trim()) f.title = { value: q[0].trim(), modifier: "INCLUDES" };
        if (hideUsed[0]) f.tags = { value: [tags.page], modifier: "EXCLUDES" };
        return CR.gql("query ($f: ImageFilterType, $p: Int) { findImages(image_filter: $f, filter: {per_page: " + PAGE_SIZE +
          ", page: $p, sort: \"date\", direction: ASC}) { count images { " + IMAGE_FIELDS + " } } }", { f: f, p: page[0] });
      }).then(function (d) {
        if (!live) return;
        total[1](d.findImages.count);
        images[1](function (prev) { return page[0] === 1 ? d.findImages.images : prev.concat(d.findImages.images); });
        loading[1](false);
      }).catch(function (e) { if (live) { error[1](e); loading[1](false); } });
      return function () { live = false; };
    }, [studio[0], q[0], hideUsed[0], page[0]]);

    var chosen = CR.sortPages(Object.keys(selected[0]).map(function (k) { return selected[0][k]; }));

    React.useEffect(function () {
      if (!titleTouched.current) title[1](suggestTitle(chosen));
    }, [chosen.length]);

    function toggle(img, e) {
      var next = Object.assign({}, selected[0]);
      var list = images[0];
      if (e.shiftKey && lastClicked.current !== null) {
        // Shift-click: select the whole run between this and the last click.
        var a = list.findIndex(function (i) { return i.id === lastClicked.current; });
        var b = list.findIndex(function (i) { return i.id === img.id; });
        if (a >= 0 && b >= 0) {
          for (var i = Math.min(a, b); i <= Math.max(a, b); i++) next[list[i].id] = list[i];
          selected[1](next);
          lastClicked.current = img.id;
          return;
        }
      }
      if (next[img.id]) delete next[img.id]; else next[img.id] = img;
      selected[1](next);
      lastClicked.current = img.id;
    }

    function selectAllShown() {
      var next = Object.assign({}, selected[0]);
      images[0].forEach(function (i) { next[i.id] = i; });
      selected[1](next);
    }

    function create() {
      if (!chosen.length || !title[0].trim()) return;
      creating[1]("Creating…");
      var performerIds = {};
      chosen.forEach(function (i) { (i.performers || []).forEach(function (p) { performerIds[p.id] = true; }); });
      var dates = chosen.map(function (i) { return i.date; }).filter(Boolean).sort();
      CR.tags().then(function (tags) {
        var input = {
          title: title[0].trim(),
          tag_ids: [format[0] === "webtoon" ? tags.webtoon : tags.comic],
          performer_ids: Object.keys(performerIds),
        };
        if (studio[0]) input.studio_id = studio[0];
        if (dates.length) input.date = dates[0];
        return CR.gql("mutation ($i: GalleryCreateInput!) { galleryCreate(input: $i) { id } }", { i: input });
      }).then(function (d) {
        var id = d.galleryCreate.id;
        return CR.gql("mutation ($g: ID!, $ids: [ID!]!) { addGalleryImages(input: {gallery_id: $g, image_ids: $ids}) }",
          { g: id, ids: chosen.map(function (i) { return i.id; }) })
          // Make sure the new pages carry Comic Page (hides them from Images).
          .then(function () { return CR.runOperation({ mode: "pages", galleryId: id }); })
          .then(function () { return id; });
      }).then(function (id) {
        creating[1]("");
        props.history.push(CR.readerPath(id), { cr: true });
      }).catch(function (e) { creating[1]("Could not create the comic: " + e.message); });
    }

    var studioOptions = [h("option", { key: "", value: "" }, "Any creator (search by title)")];
    (studios.data || []).forEach(function (s) {
      studioOptions.push(h("option", { key: s.id, value: s.id }, s.name + " (" + s.image_count + ")"));
    });

    var picker = h("div", { className: "cr-builder-picker" },
      h("div", { className: "cr-controls" },
        h("select", {
          className: "btn-secondary form-control cr-select", value: studio[0],
          onChange: function (e) { studio[1](e.target.value); CR.store.set("builder:studio", e.target.value); },
        }, studioOptions),
        h("input", {
          className: "clearable-text-field form-control cr-search", type: "search",
          placeholder: "Title contains…  e.g. Moon Quest", value: q[0],
          onChange: function (e) { q[1](e.target.value); },
        }),
        h("label", { className: "cr-check" },
          h("input", { type: "checkbox", checked: hideUsed[0], onChange: function (e) { hideUsed[1](e.target.checked); } }),
          " Hide pages already in a comic")),
      !studio[0] && !q[0].trim()
        ? h("div", { className: "cr-empty" }, "Pick a creator, or search by title, to see their images.")
        : null,
      error[0] ? h(CR.ErrorBox, { error: error[0] }) : null,
      images[0].length
        ? h("div", { className: "cr-builder-bar" },
            h("span", { className: "text-muted" }, "Showing " + images[0].length + " of " + total[0] +
              " · click to pick, shift-click to pick a run"),
            h("button", { type: "button", className: "btn btn-link btn-sm", onClick: selectAllShown }, "Pick all shown"))
        : null,
      h("div", { className: "cr-pick-grid" }, images[0].map(function (img) {
        var on = !!selected[0][img.id];
        return h("button", {
          key: img.id, type: "button", className: "cr-pick" + (on ? " cr-picked" : ""),
          title: (img.title || "") + (img.date ? " · " + img.date : ""),
          onClick: function (e) { toggle(img, e); },
        },
          h("img", { src: img.paths.thumbnail, loading: "lazy", alt: "" }),
          on ? h("span", { className: "cr-pick-check" }, h(CR.Icon, { name: "faCheck" })) : null,
          h("span", { className: "cr-pick-label" }, img.title || img.date || ""));
      })),
      loading[0] ? h(CR.Loading, null) : null,
      !loading[0] && images[0].length < total[0]
        ? h("div", { className: "text-center" },
            h("button", { type: "button", className: "btn btn-secondary", onClick: function () { page[1](page[0] + 1); } },
              "Load more"))
        : null);

    var panel = h("div", { className: "cr-builder-panel" },
      h("h5", null, chosen.length ? chosen.length + " page" + (chosen.length === 1 ? "" : "s") + " picked" : "No pages picked"),
      h("label", { className: "cr-field" }, "Title",
        h("input", {
          className: "clearable-text-field form-control", type: "text", value: title[0],
          placeholder: "Comic title",
          onChange: function (e) { titleTouched.current = true; title[1](e.target.value); },
        })),
      h("div", { className: "cr-field" }, "Read as",
        h("div", { className: "btn-group btn-group-sm d-flex" },
          [["comic", "Pages"], ["webtoon", "Webtoon scroll"]].map(function (o) {
            return h("button", {
              key: o[0], type: "button", className: "btn " + (format[0] === o[0] ? "btn-primary" : "btn-secondary"),
              onClick: function () { format[1](o[0]); },
            }, o[1]);
          }))),
      h("button", {
        type: "button", className: "btn btn-primary btn-block",
        disabled: !chosen.length || !title[0].trim() || !!creating[0],
        onClick: create,
      }, "Create comic"),
      creating[0] ? h("div", { className: "cr-status mt-2" }, creating[0]) : null,
      chosen.length
        ? h("div", null,
            h("div", { className: "cr-order-head" },
              h("span", { className: "text-muted" }, "Reading order (by post date)"),
              h("button", { type: "button", className: "btn btn-link btn-sm", onClick: function () { selected[1]({}); } }, "Clear")),
            h("ol", { className: "cr-order" }, chosen.map(function (img) {
              return h("li", { key: img.id },
                h("img", { src: img.paths.thumbnail, alt: "" }),
                h("span", null, img.title || img.date || "Image " + img.id),
                h("button", {
                  type: "button", className: "btn btn-link btn-sm", title: "Remove",
                  onClick: function () { var n = Object.assign({}, selected[0]); delete n[img.id]; selected[1](n); },
                }, "×"));
            })))
        : null);

    return h("div", { className: "cr-library container-fluid" },
      h("div", { className: "cr-header" },
        h("h2", null, "New comic from images"),
        h("a", Object.assign({ className: "btn btn-secondary" }, CR.linkProps(props.history, CR.ROUTE)), "Back to comics")),
      h("p", { className: "text-muted" },
        "For comics posted one page per post. Pick the pages; they're put in post-date order, " +
        "and the new comic is hidden from Galleries and its pages from Images."),
      h("div", { className: "cr-builder" }, picker, panel));
  }

  CR.Builder = Builder;
  CR.suggestTitle = suggestTitle;
})();
