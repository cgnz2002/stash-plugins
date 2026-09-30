// Comic Reader -- wiring: the /plugins/comics route and the navigation item.
// Loaded last, once every component exists.
(function () {
  "use strict";

  var CR = window.ComicReader;
  if (!CR || !CR.React) return;
  var React = CR.React;
  var h = CR.h;
  var api = CR.api;

  // One route serves the library, the reader and the builder. Stash's
  // register.route is a react-router v5 <Route path component>, which matches
  // by prefix, so the sub-paths are dispatched here.
  function ComicsRoute() {
    var loc = CR.Router.useLocation();
    var history = CR.Router.useHistory();
    var path = loc.pathname.replace(/\/+$/, "");
    var read = path.match(/^\/plugins\/comics\/read\/(\d+)$/);

    if (read) {
      // Keyed by gallery: going straight from one comic to another must not
      // carry the first one's position (and save it over the second's).
      return h(CR.Reader, {
        key: read[1], galleryId: read[1], history: history,
        // Back to wherever the comic was opened from; a reader opened
        // directly (bookmark, new tab) has nowhere in the app to go back to.
        onExit: function () {
          if (loc.state && loc.state.cr) history.goBack();
          else history.push(CR.ROUTE);
        },
      });
    }
    if (path === CR.ROUTE + "/new" && CR.Builder) return h(CR.Builder, { history: history });
    return h(CR.Library, { history: history });
  }

  try {
    api.register.route(CR.ROUTE, ComicsRoute);
  } catch (e) {
    console.error("[comic-reader] could not register the Comics route:", e);
  }

  // Same markup as Stash's own menu items (MainNavBar), so it lines up on
  // desktop and in the collapsed mobile menu alike.
  function ComicsNavItem() {
    var Nav = CR.Bootstrap.Nav;
    var NavLink = CR.Router.NavLink;
    var link = h(NavLink, { activeClassName: "active", to: CR.ROUTE },
      h(CR.Button, { className: "minimal p-4 p-xl-2 d-flex d-xl-inline-block flex-column justify-content-between align-items-center" },
        h(CR.Icon, { name: "faBookOpen", className: "nav-menu-icon d-block d-xl-inline mb-2 mb-xl-0" }),
        h("span", null, "Comics")));
    if (!Nav || !Nav.Link) return h("div", { className: "col-4 col-sm-3 col-md-2 col-lg-auto nav-link" }, link);
    return h(Nav.Link, { eventKey: CR.ROUTE, as: "div", className: "col-4 col-sm-3 col-md-2 col-lg-auto" }, link);
  }

  // The menu is mounted on every page and sits inside Stash's router, so it
  // is also where the page injector lives: it watches navigation and portals
  // the performer/studio Comics tab and the gallery-page buttons in. (Not
  // MainNavBar.UtilityItems: Stash renders that twice, desktop and mobile,
  // which injected everything twice.)
  try {
    api.patch.before("MainNavBar.MenuItems", function (props) {
      return [{ children: h(React.Fragment, null, props.children,
        h(ComicsNavItem, { key: "cr-comics" }),
        CR.Injector ? h(CR.Injector, { key: "cr-injector" }) : null) }];
    });
  } catch (e) {
    console.error("[comic-reader] could not add the Comics menu item:", e);
  }
})();
