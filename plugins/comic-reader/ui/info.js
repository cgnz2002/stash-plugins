// Comic Reader -- the comic's details panel, beside the pages in the reader.
//
// The counterpart of the details panel on Stash's gallery and scene pages,
// shaped for reading: who and what it is, how long it is, its rating, and
// where to go next. Links lead to more comics rather than to Stash's
// gallery lists: a performer or studio opens its Comics tab, a tag opens the
// Comics page filtered by it. Editing and file details stay on the gallery
// page, one click away.
//
// Built from Stash's own parts: RatingSystem (follows the user's stars or
// decimal setting) and Date, and where a part isn't exposed to plugins, its
// exact markup -- performers as in Stash's scene list details
// (performer-tag-container), tags as TagLink renders them -- so a theme
// styles all of it. Links are plain Stash buttons: the gallery page shows no
// links, and Stash's ExternalLinksButton opens its menu on document.body,
// behind the full-screen reader.
(function () {
  "use strict";

  var CR = window.ComicReader;
  if (!CR || !CR.React) return;
  var React = CR.React;
  var h = CR.h;
  var api = CR.api;

  // RatingSystem and Date arrive with Stash's Galleries chunk (the Comics page
  // loads it too); a reader opened directly from a link has loaded nothing.
  CR.useInfoComponents = function () {
    var use = api.hooks && api.hooks.useLoadComponents;
    var lc = api.loadableComponents || {};
    var chunks = [lc.Galleries].filter(Boolean);
    return use && chunks.length ? use(chunks) : false;
  };

  // A list-page URL criterion, encoded the way Stash's own list URLs are:
  // JSON with the braces outside strings turned into parentheses.
  function criterionParam(obj) {
    var inString = false;
    var escaped = false;
    var out = Array.prototype.map.call(JSON.stringify(obj), function (ch) {
      if (escaped) { escaped = false; return ch; }
      if (ch === "\\" && inString) { escaped = true; return ch; }
      if (ch === '"') inString = !inString;
      else if (!inString && ch === "{") return "(";
      else if (!inString && ch === "}") return ")";
      return ch;
    }).join("");
    return encodeURIComponent(out);
  }
  CR.criterionParam = criterionParam;

  CR.comicsWithTagPath = function (tag) {
    return CR.ROUTE + "?c=" + criterionParam({
      type: "tags", modifier: "INCLUDES",
      value: { items: [{ id: String(tag.id), label: tag.name }], excluded: [], depth: 0 },
    });
  };

  // "2026-01-31" as a local date: new Date("2026-01-31") is UTC midnight,
  // which is the day before anywhere west of Greenwich.
  CR.formatDate = function (s) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s || "");
    if (!m) return s || "";
    var d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
    try {
      return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
    } catch (e) {
      return s;
    }
  };

  function hostOf(url) {
    var m = /^[a-z]+:\/\/(?:www\.)?([^\/?#]+)/i.exec(url || "");
    return m ? m[1] : url;
  }

  // Stash reports a studio without an image by appending default=true.
  function studioLogo(studio) {
    var p = studio && studio.image_path;
    return p && p.indexOf("default=true") < 0 ? p : null;
  }

  // In-app link that opens a performer's or studio's Comics tab on arrival.
  function tabLinkProps(history, kind, id) {
    var to = "/" + kind + "s/" + id;
    var base = CR.linkProps(history, to);
    return {
      href: base.href,
      onClick: function (e) {
        CR.returnToTab = kind + ":" + id;
        base.onClick(e);
      },
    };
  }

  function Section(props) {
    if (!props.show) return null;
    return h("section", { className: "cr-info-section" },
      h("h6", null, props.title),
      props.children);
  }

  // props: gallery, family (comic tag ids, hidden from the tag list), pages,
  // layout, history, docked, onClose, onRated
  function ComicInfo(props) {
    var g = props.gallery;
    var history = props.history;
    var toast = CR.useToast();
    var loading = CR.useInfoComponents();
    var rating = React.useState(g.rating100 == null ? null : g.rating100);
    var family = props.family || [];
    var Rating = !loading && api.components.RatingSystem;
    var StashDate = !loading && api.components.Date;

    function setRating(v) {
      var prev = rating[0];
      rating[1](v);
      CR.gql("mutation ($id: ID!, $r: Int) { galleryUpdate(input: {id: $id, rating100: $r}) { id rating100 } }",
        { id: g.id, r: v })
        .then(function () { if (props.onRated) props.onRated(v); })
        .catch(function (e) { rating[1](prev); toast.error(e); });
    }

    var tags = (g.tags || []).filter(function (t) { return family.indexOf(String(t.id)) < 0; });
    var performers = g.performers || [];
    var urls = g.urls || [];
    var logo = studioLogo(g.studio);
    var meta = [
      props.pages + (props.pages === 1 ? " page" : " pages"),
      props.layout === "scroll" ? "Webtoon" : null,
    ].filter(Boolean).join(" · ");
    var date = g.date ? (StashDate ? h(StashDate, { value: g.date }) : CR.formatDate(g.date)) : null;

    // The aside places the panel; the .card inside is only its surface, so a
    // theme's card styling (colour, blur, border) applies without its card
    // layout rules (position, margins) moving the panel.
    return h("aside", {
      className: "cr-info " + (props.docked ? "cr-info-docked" : "cr-info-sheet"),
      role: "complementary", "aria-label": "Comic details",
    }, h("div", { className: "card cr-info-card" },
      h("div", { className: "cr-info-head" },
        g.studio
          ? h("a", Object.assign({ className: "cr-info-studio", title: "Comics from " + g.studio.name },
              tabLinkProps(history, "studio", g.studio.id)),
              logo ? h("img", { src: logo, alt: g.studio.name }) : g.studio.name)
          : h("span", null),
        h(CR.Button, { variant: "secondary", size: "sm", className: "cr-info-close",
                       title: "Hide details (I)", onClick: props.onClose },
          h(CR.Icon, { name: "faXmark" }))),
      h("h3", { className: "cr-info-title" }, CR.galleryTitle(g)),
      h("div", { className: "cr-info-meta text-muted" }, date, date ? " · " : null, meta),
      Rating ? h("div", { className: "cr-info-rating" },
        h(CR.Boundary, { name: "rating" },
          h(Rating, { value: rating[0], onSetRating: setRating, clickToRate: true, withoutContext: true })))
        : null,
      g.details ? h("p", { className: "pre cr-info-details" }, g.details) : null,
      h(Section, { show: performers.length > 0, title: performers.length === 1 ? "Performer" : "Performers" },
        h("div", { className: "cr-info-people" },
          performers.map(function (p) {
            var link = tabLinkProps(history, "performer", p.id);
            var name = p.name + (p.disambiguation ? " (" + p.disambiguation + ")" : "");
            return h("div", { key: p.id, className: "performer-tag-container row" },
              h("a", Object.assign({ className: "performer-tag col m-auto zoom-2", title: "Comics with " + p.name }, link),
                h("img", { loading: "lazy", className: "image-thumbnail", alt: p.name, src: p.image_path || "" })),
              h("span", { className: "tag-item badge badge-secondary d-block" },
                h("a", Object.assign({ title: "Comics with " + p.name }, link), name)));
          }))),
      h(Section, { show: tags.length > 0, title: "Tags" },
        h("div", { className: "cr-info-tags" },
          tags.map(function (t) {
            // TagLink's markup, pointing at comics with this tag.
            return h("span", { key: t.id, className: "tag-item badge badge-secondary" },
              h("a", Object.assign({ title: "Comics tagged " + t.name },
                CR.linkProps(history, CR.comicsWithTagPath(t))), t.name));
          }))),
      h(Section, { show: urls.length > 0, title: urls.length === 1 ? "Link" : "Links" },
        h("div", { className: "cr-info-links" },
          urls.map(function (u) {
            return h("a", { key: u, className: "btn btn-secondary btn-sm", href: u, target: "_blank",
                            rel: "noopener noreferrer", title: u },
              h(CR.Icon, { name: "faArrowUpRightFromSquare" }), " ", hostOf(u));
          }))),
      h("div", { className: "cr-info-foot" },
        h("a", Object.assign({ className: "btn btn-secondary btn-sm", title: "Edit it, see its files and images" },
          CR.linkProps(history, "/galleries/" + g.id)),
          h(CR.Icon, { name: "faImages" }), " Open gallery in Stash"))));
  }

  CR.ComicInfo = ComicInfo;
})();
