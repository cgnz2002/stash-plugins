// Comic Reader -- pieces injected into Stash's own pages:
//   * a Comics tab on performer and studio pages
//   * Read / Mark as comic buttons on gallery pages
//
// Stash v0.31 offers no plugin hook for either spot (performer/studio tabs
// and the gallery page aren't patchable components), so this finds the
// rendered elements and portals React into them -- the same
// find-the-header approach as of-stash-sync's "Sync Fan Sites" button.
// Portals keep Stash's router and contexts, so links and the shared grid
// behave exactly as they do on the Comics page.
//
// Stash also redirects an unknown tab URL (/performers/1/comics goes to
// .../galleries), so the Comics tab switches in-page instead of by URL.
(function () {
  "use strict";

  var CR = window.ComicReader;
  if (!CR || !CR.React) return;
  var React = CR.React;
  var h = CR.h;
  var ReactDOM = CR.api.ReactDOM;

  function pageContext(pathname) {
    var m = pathname.match(/^\/(performers|studios)\/(\d+)(?:\/|$)/);
    if (m) return { kind: m[1] === "performers" ? "performer" : "studio", id: m[2] };
    var g = pathname.match(/^\/galleries\/(\d+)(?:\/|$)/);
    if (g) return { kind: "gallery", id: g[1] };
    return null;
  }

  // The element matching `selector`, re-found whenever React replaces it
  // (pages re-render after their data loads, or on navigation).
  function useElement(selector, key) {
    var s = React.useState(null);
    React.useEffect(function () {
      var current = null;
      var queued = false;
      function check() {
        queued = false;
        if (current && document.contains(current)) return;
        current = document.querySelector(selector);
        s[1](current);
      }
      check();
      var obs = new MutationObserver(function () {
        if (!queued) { queued = true; requestAnimationFrame(check); }
      });
      obs.observe(document.body, { childList: true, subtree: true });
      return function () { obs.disconnect(); };
    }, [selector, key]);
    return s[0];
  }

  // ------------------------------------------------ performer / studio tab

  function tabFilter(ctx) {
    if (ctx.kind === "performer") return { performers: { value: [ctx.id], modifier: "INCLUDES" } };
    // depth -1: a parent studio (e.g. the Patreon network) shows every
    // creator's comics beneath it.
    return { studios: { value: [ctx.id], modifier: "INCLUDES", depth: -1 } };
  }

  function ComicsTab(props) {
    var ctx = props.ctx;
    var nav = useElement("." + ctx.kind + "-tabs > .nav-tabs", ctx.kind + ctx.id);
    var tabKey = ctx.kind + ":" + ctx.id;
    // Coming back from a comic opened in this tab lands on the tab again.
    var active = React.useState(CR.returnToTab === tabKey);
    var count = React.useState(null);

    var els = React.useMemo(function () {
      if (!nav) return null;
      var link = document.createElement("a");
      link.className = "nav-item nav-link cr-tab";
      link.setAttribute("role", "tab");
      link.setAttribute("href", "#");
      var pane = document.createElement("div");
      pane.className = "cr-tab-pane";
      return { link: link, pane: pane };
    }, [nav]);

    React.useEffect(function () {
      CR.returnToTab = null;
      var live = true;
      CR.tags().then(function (tags) {
        return CR.gql("query ($f: GalleryFilterType) { findGalleries(gallery_filter: $f, filter: {per_page: 0}) { count } }",
          { f: CR.comicFilter(tags, tabFilter(ctx)) });
      }).then(function (d) { if (live) count[1](d.findGalleries.count); })
        .catch(function (e) { console.error("[comic-reader] comics count failed:", e); });
      return function () { live = false; };
    }, [tabKey]);

    React.useEffect(function () {
      if (!nav || !els) return;
      nav.appendChild(els.link);
      var content = nav.parentElement.querySelector(":scope > .tab-content");
      nav.parentElement.insertBefore(els.pane, content ? content.nextSibling : null);
      function onNavClick(e) {
        var a = e.target.closest && e.target.closest(".nav-link");
        if (!a) return;
        if (a === els.link) { e.preventDefault(); active[1](true); }
        else active[1](false);
      }
      // Remember the tab when a comic is opened from it.
      function onPaneClick(e) {
        if (e.target.closest && e.target.closest("a")) CR.returnToTab = tabKey;
      }
      nav.addEventListener("click", onNavClick, true);
      els.pane.addEventListener("click", onPaneClick, true);
      return function () {
        nav.removeEventListener("click", onNavClick, true);
        els.pane.removeEventListener("click", onPaneClick, true);
        nav.classList.remove("cr-comics-active");
        if (content) content.style.display = "";
        els.link.remove();
        els.pane.remove();
      };
    }, [nav, els]);

    var on = active[0] && count[0] > 0;
    // Stash's tab that was selected when Comics took over. Its "active" class
    // is taken off while Comics shows and put back afterwards: overriding its
    // look with CSS lost to themes that style the selected tab more strongly,
    // leaving two tabs lit. React only rewrites a tab's class when its own
    // props change, so the class stays off until Stash selects another tab.
    var stockActive = React.useRef(null);
    React.useEffect(function () {
      if (!nav || !els) return;
      var content = nav.parentElement.querySelector(":scope > .tab-content");
      if (on) {
        var cur = nav.querySelector(".nav-link.active:not(.cr-tab)");
        if (cur) {
          stockActive.current = cur;
          cur.classList.remove("active");
          cur.setAttribute("aria-selected", "false");
        }
      } else if (stockActive.current) {
        var prev = stockActive.current;
        stockActive.current = null;
        // Put it back unless Stash has since selected another tab itself.
        if (document.contains(prev) && !nav.querySelector(".nav-link.active:not(.cr-tab)")) {
          prev.classList.add("active");
          prev.setAttribute("aria-selected", "true");
        }
      }
      nav.classList.toggle("cr-comics-active", on);
      els.link.classList.toggle("active", on);
      els.link.setAttribute("aria-selected", on ? "true" : "false");
      // A creator with no comics gets no tab, rather than "Comics 0" on
      // every performer in the library.
      els.link.style.display = count[0] > 0 ? "" : "none";
      if (content) content.style.display = on ? "none" : "";
      els.pane.style.display = on ? "" : "none";
    }, [on, count[0], nav, els]);

    if (!els) return null;
    return h(React.Fragment, null,
      ReactDOM.createPortal(h(React.Fragment, null, "Comics",
        count[0] ? h("span", { className: "left-spacing badge badge-pill badge-secondary" }, count[0]) : null), els.link),
      on ? ReactDOM.createPortal(h(CR.ComicGrid, {
        history: props.history, filter: tabFilter(ctx), storeKey: "tab", defaultGroup: "none",
      }), els.pane) : null);
  }

  // ------------------------------------------------------ gallery buttons

  function GalleryActions(props) {
    var id = props.ctx.id;
    var nav = useElement(".gallery-tabs .nav-tabs", "g" + id);
    var host = React.useMemo(function () {
      if (!nav) return null;
      var div = document.createElement("div");
      div.className = "cr-gallery-actions";
      return div;
    }, [nav]);
    var state = React.useState(null); // {tags, family, gallery}
    var busy = React.useState(false);
    var toast = CR.useToast();
    var reload = React.useState(0);

    React.useEffect(function () {
      if (!nav || !host) return;
      var anchor = nav.parentElement;
      anchor.parentElement.insertBefore(host, anchor);
      return function () { host.remove(); };
    }, [nav, host]);

    React.useEffect(function () {
      var live = true;
      Promise.all([
        CR.tags(), CR.family(),
        CR.gql("query ($id: ID!) { findGallery(id: $id) { id image_count tags { id } } }", { id: id }),
      ]).then(function (r) {
        if (live) state[1]({ tags: r[0], family: r[1], gallery: r[2].findGallery });
      }).catch(function (e) { console.error("[comic-reader] gallery lookup failed:", e); });
      return function () { live = false; };
    }, [id, reload[0]]);

    // This page is where Stash's own "Add" tab and "Remove from gallery"
    // live, and neither fires a hook -- so a comic's page tags are re-checked
    // whenever its page is opened.
    var isComicNow = !!(state[0] && state[0].gallery && CR.isComic(state[0].gallery.tags, state[0].family));
    React.useEffect(function () {
      if (isComicNow) CR.runOperation({ mode: "pages", galleryId: id }).catch(function () {});
    }, [isComicNow, id]);

    if (!host || !state[0] || !state[0].gallery) return null;
    var s = state[0];
    var comic = CR.isComic(s.gallery.tags, s.family);
    var webtoon = CR.hasTag(s.gallery.tags, s.tags.webtoon);

    function change(add, remove, done) {
      busy[1](true);
      CR.editGalleryTags(id, add, remove)
        .then(function () {
          busy[1](false);
          reload[1](function (n) { return n + 1; });
          CR.refreshStash();
          toast.success(done);
        })
        .catch(function (e) { busy[1](false); toast.error(e); });
    }

    var buttons;
    if (comic) {
      buttons = [
        h("a", Object.assign({ key: "read", className: "btn btn-primary btn-sm" },
          CR.linkProps(props.history, CR.readerPath(id))),
          h(CR.Icon, { name: "faBookOpen" }), webtoon ? " Read webtoon" : " Read comic"),
        h("button", {
          key: "unmark", type: "button", className: "btn btn-secondary btn-sm", disabled: busy[0],
          title: "Stop treating this gallery as a comic (it returns to Galleries)",
          onClick: function () {
            var present = s.gallery.tags.map(function (t) { return String(t.id); })
              .filter(function (t) { return s.family.indexOf(t) >= 0; });
            change([], present, "No longer a comic; it is back in Galleries");
          },
        }, "Not a comic"),
      ];
    } else {
      buttons = [
        h("button", { key: "mark", type: "button", className: "btn btn-primary btn-sm",
                      title: "Read as side-by-side pages; moves it from Galleries to Comics",
                      disabled: busy[0],
                      onClick: function () { change([s.tags.comic], [], "Marked as a comic"); } },
          h(CR.Icon, { name: "faBookOpen" }), " Mark as comic"),
        h("button", { key: "webtoon", type: "button", className: "btn btn-secondary btn-sm",
                      title: "Read as one continuous vertical scroll; moves it from Galleries to Comics",
                      disabled: busy[0],
                      onClick: function () { change([s.tags.webtoon], [], "Marked as a webtoon"); } },
          "Mark as webtoon"),
      ];
    }
    return ReactDOM.createPortal(h(React.Fragment, null, buttons), host);
  }

  // --------------------------------------------------------------- root

  // Only one injector may be live: if Stash ever mounts the host twice, a
  // second copy would add a second tab and a second row of buttons.
  //
  // The claim is made in an effect, i.e. only once React has actually put
  // this instance on the page. Claiming during render (0.1.0) broke on a
  // browser's first load: React can render a component and then throw that
  // render away while lazy chunks load, and the discarded render kept the
  // claim forever, so no injector ever ran and the gallery buttons and
  // Comics tab never appeared.
  var owner = null;
  var waiting = [];

  function Injector() {
    var me = React.useRef({});
    var mine = React.useState(false);
    React.useEffect(function () {
      function claim() {
        if (owner !== null && owner !== me.current) return false;
        owner = me.current;
        mine[1](true);
        return true;
      }
      if (!claim()) waiting.push(claim);
      return function () {
        waiting = waiting.filter(function (c) { return c !== claim; });
        if (owner === me.current) {
          owner = null;
          // Hand over to the next mounted instance, if any.
          for (var i = 0; i < waiting.length; i++) {
            if (waiting[i]()) { waiting.splice(i, 1); break; }
          }
        }
      };
    }, []);

    var loc = CR.Router.useLocation ? CR.Router.useLocation() : window.location;
    var history = CR.Router.useHistory ? CR.Router.useHistory() : null;
    if (!mine[0]) return null;
    var ctx = pageContext(loc.pathname);
    if (!ctx) return null;
    if (ctx.kind === "gallery") return h(GalleryActions, { key: "g" + ctx.id, ctx: ctx, history: history });
    return h(ComicsTab, { key: ctx.kind + ctx.id, ctx: ctx, history: history });
  }

  CR.Injector = Injector;
})();
