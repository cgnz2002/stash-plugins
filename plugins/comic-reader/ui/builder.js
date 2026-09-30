// Comic Reader -- "New comic from images".
//
// For comics released one page per post with no Patreon collection tying
// them together: those posts have no gallery of their own (a single-image
// post doesn't get one), so the pages are loose images. Pick a creator, pick
// the pages, and this makes a gallery tagged Comic or Webtoon. Reading order
// is the reader's usual one (post date, then file name), so picking order
// doesn't matter.
//
// Picking uses Stash's own image cards and selection checkboxes (GridCard in
// selecting mode), so it behaves like selecting on the Images page,
// shift-click for a run included.
(function () {
  "use strict";

  var CR = window.ComicReader;
  if (!CR || !CR.React) return;
  var React = CR.React;
  var h = CR.h;
  var PAGE_SIZE = 120;
  var PICK_WIDTH = 150;

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

  function PickCard(props) {
    var GridCard = CR.api.components.GridCard;
    var img = props.image;
    var f = (img.visual_files || [])[0] || {};
    return h(GridCard, {
      className: "image-card cr-pick" + (props.selected ? " cr-picked" : ""),
      url: "/images/" + img.id,
      width: props.width,
      title: img.title || "",
      image: h("div", { className: "image-card-preview" + (f.height > f.width ? " portrait" : "") },
        h("img", { className: "image-card-preview-image", loading: "lazy", alt: img.title || "", src: img.paths.thumbnail })),
      details: img.date ? h("div", { className: "gallery-card__details" },
        h("span", { className: "gallery-card__date" }, img.date)) : null,
      // Always selecting: a click picks the page instead of opening it.
      selecting: true,
      selected: props.selected,
      onSelectedChanged: props.onSelectedChanged,
    });
  }

  function Builder(props) {
    var toast = CR.useToast();
    var cardsLoading = CR.useCardComponents();
    var measure = CR.useContainerWidth();
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
    var creating = React.useState(false);
    // A gallery made by an attempt that then failed part-way; a retry fills
    // it instead of leaving it empty and making another.
    var createdId = React.useRef(null);

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

    function pick(img, on, shift) {
      var next = Object.assign({}, selected[0]);
      var list = images[0];
      if (shift && lastClicked.current !== null) {
        // Shift-click: the whole run between this and the last click.
        var a = list.findIndex(function (i) { return i.id === lastClicked.current; });
        var b = list.findIndex(function (i) { return i.id === img.id; });
        if (a >= 0 && b >= 0) {
          for (var i = Math.min(a, b); i <= Math.max(a, b); i++) next[list[i].id] = list[i];
          selected[1](next);
          lastClicked.current = img.id;
          return;
        }
      }
      if (on) next[img.id] = img; else delete next[img.id];
      selected[1](next);
      lastClicked.current = img.id;
    }

    function selectAllShown() {
      var next = Object.assign({}, selected[0]);
      images[0].forEach(function (i) { next[i.id] = i; });
      selected[1](next);
    }

    function create() {
      if (!chosen.length || !title[0].trim() || creating[0]) return;
      creating[1](true);
      var performerIds = {};
      chosen.forEach(function (i) { (i.performers || []).forEach(function (p) { performerIds[p.id] = true; }); });
      var dates = chosen.map(function (i) { return i.date; }).filter(Boolean).sort();
      CR.tags().then(function (tags) {
        if (createdId.current) return { galleryCreate: { id: createdId.current } };
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
        createdId.current = id;
        return CR.gql("mutation ($g: ID!, $ids: [ID!]!) { addGalleryImages(input: {gallery_id: $g, image_ids: $ids}) }",
          { g: id, ids: chosen.map(function (i) { return i.id; }) })
          // Make sure the new pages carry Comic Page (hides them from Images).
          .then(function () { return CR.runOperation({ mode: "pages", galleryId: id }); })
          .then(function () { return id; });
      }).then(function (id) {
        createdId.current = null;
        toast.success("Created comic “" + title[0].trim() + "”");
        props.history.push(CR.readerPath(id), { cr: true });
      }).catch(function (e) {
        creating[1](false);
        toast.error(e);
      });
    }

    if (cardsLoading) return h(CR.Loading, null);

    var studioOptions = [h("option", { key: "", value: "" }, "Any creator (search by title)")];
    (studios.data || []).forEach(function (s) {
      studioOptions.push(h("option", { key: s.id, value: s.id }, s.name + " (" + s.image_count + ")"));
    });
    var width = CR.cardWidth(measure[1], PICK_WIDTH);

    var toolbar = h("div", { className: "filtered-list-toolbar btn-toolbar" },
      h("div", { className: "mr-2 mb-2 btn-group" },
        h("select", {
          className: "btn-secondary form-control", value: studio[0],
          onChange: function (e) { studio[1](e.target.value); CR.store.set("builder:studio", e.target.value); },
        }, studioOptions)),
      h("div", { className: "mr-2 mb-2 btn-group" },
        h("input", {
          className: "clearable-text-field form-control", type: "search",
          placeholder: "Title contains…", value: q[0],
          onChange: function (e) { q[1](e.target.value); },
        })),
      h("div", { className: "mr-2 mb-2 btn-group cr-check" },
        h("div", { className: "custom-control custom-switch" },
          h("input", { type: "checkbox", className: "custom-control-input", id: "cr-hide-used",
                       checked: hideUsed[0], onChange: function (e) { hideUsed[1](e.target.checked); } }),
          h("label", { className: "custom-control-label", htmlFor: "cr-hide-used" }, "Hide pages already in a comic"))));

    var picker = h("div", { className: "cr-builder-picker" },
      toolbar,
      !studio[0] && !q[0].trim()
        ? h("div", { className: "cr-empty text-muted" }, "Pick a creator, or search by title, to see their images.")
        : null,
      error[0] ? h(CR.ErrorBox, { error: error[0] }) : null,
      images[0].length
        ? h("div", { className: "cr-count text-muted text-center mb-2" },
            images[0].length + " of " + total[0] + " · click to pick, shift-click to pick a run · ",
            h("button", { type: "button", className: "btn btn-link btn-sm p-0 align-baseline", onClick: selectAllShown }, "pick all shown"))
        : null,
      h("div", { className: "row justify-content-center", ref: measure[0] }, images[0].map(function (img) {
        return h(PickCard, {
          key: img.id, image: img, width: width, selected: !!selected[0][img.id],
          onSelectedChanged: function (on, shift) { pick(img, on, shift); },
        });
      })),
      loading[0] ? h(CR.Loading, null) : null,
      !loading[0] && images[0].length < total[0]
        ? h("div", { className: "text-center mb-3" },
            h("button", { type: "button", className: "btn btn-secondary", onClick: function () { page[1](page[0] + 1); } },
              "Load more"))
        : null);

    var panel = h("div", { className: "card cr-builder-panel" },
      h("div", { className: "card-body" },
        h("h5", { className: "card-title" },
          chosen.length ? chosen.length + " page" + (chosen.length === 1 ? "" : "s") + " picked" : "No pages picked"),
        h("div", { className: "form-group" },
          h("label", { htmlFor: "cr-title" }, "Title"),
          h("input", {
            id: "cr-title", className: "text-input form-control", type: "text", value: title[0],
            placeholder: "Comic title",
            onChange: function (e) { titleTouched.current = true; title[1](e.target.value); },
          })),
        h("div", { className: "form-group" },
          h("label", null, "Read as"),
          h("div", { className: "btn-group d-flex" },
            [["comic", "Pages"], ["webtoon", "Webtoon scroll"]].map(function (o) {
              return h("button", {
                key: o[0], type: "button", className: "btn " + (format[0] === o[0] ? "btn-primary" : "btn-secondary"),
                onClick: function () { format[1](o[0]); },
              }, o[1]);
            }))),
        h("button", {
          type: "button", className: "btn btn-primary btn-block",
          disabled: !chosen.length || !title[0].trim() || creating[0],
          onClick: create,
        }, creating[0] ? "Creating…" : "Create comic"),
        chosen.length
          ? h("div", { className: "cr-order-wrap" },
              h("div", { className: "cr-order-head text-muted" },
                h("span", null, "Reading order (by post date)"),
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
          : null));

    return h("div", { className: "cr-library" },
      h("div", { className: "cr-builder-head" },
        h("a", Object.assign({ className: "btn btn-secondary" }, CR.linkProps(props.history, CR.ROUTE)),
          h(CR.Icon, { name: "faChevronLeft" }), " Comics"),
        h("h4", { className: "m-0" }, "New comic from images")),
      h("p", { className: "text-muted" },
        "For comics posted one page per post. Pick the pages; they're put in post-date order, " +
        "and the new comic is hidden from Galleries and its pages from Images."),
      h("div", { className: "cr-builder" }, picker, panel));
  }

  CR.Builder = Builder;
  CR.suggestTitle = suggestTitle;
})();
