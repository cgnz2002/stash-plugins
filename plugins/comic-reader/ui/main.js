// Comic Reader -- wiring: the /plugins/comics route, the Comics nav item and
// its Settings > Interface > Menu items toggle. Loaded last, once every
// component exists.
//
// Other plugins patch the same components, so every patch here follows two
// rules (0.1.0 broke both and blanked Stash for anyone running Stash TV):
//
//  1. A `before` patch returns ALL the arguments it was given. Stash's patch
//     chain (RB in the v0.31 bundle) replaces the whole argument list with
//     whatever a `before` returns, then calls each `instead` patch with those
//     arguments plus `next`. React calls a component with two arguments, and
//     Stash TV's MenuItems patch takes `next` as the THIRD -- so returning
//     just [props] handed it `undefined` as the component to render: React
//     error #130, and a blank app.
//  2. Children stay an array (React.Children.toArray), because other patches
//     inspect and extend them, and every patch degrades to its untouched
//     arguments if it throws.
//
// The nav item follows the same pattern as Stash TV and Binge: a "Comics" row
// in Settings > Interface > Menu items, switched on once for a fresh install.
(function () {
  "use strict";

  var CR = window.ComicReader;
  if (!CR || !CR.React) return;
  var React = CR.React;
  var h = CR.h;
  var api = CR.api;
  var MENU_ID = "comics";

  // Rule 1: wrap a props -> props transform so the other arguments always
  // survive, and so an exception leaves the arguments exactly as they came.
  function keepArgs(name, transform) {
    return function () {
      var args = Array.prototype.slice.call(arguments);
      try {
        var props = transform(args[0]);
        if (props) args[0] = props;
      } catch (e) {
        console.error("[comic-reader] " + name + " patch failed; left untouched:", e);
      }
      return args;
    };
  }
  CR.keepArgs = keepArgs;

  // One route serves the library, the reader and the builder. Stash's
  // register.route is a react-router v5 <Route path component>, which matches
  // by prefix, so the sub-paths are dispatched here.
  function ComicsPage() {
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
    var series = path.match(/^\/plugins\/comics\/series(?:\/(\d+))?$/);
    if (series && CR.SeriesPage) {
      return series[1]
        ? h(CR.SeriesPage, { key: series[1], seriesId: series[1], history: history })
        : h(CR.SeriesIndex, { history: history });
    }
    return h(CR.Library, { history: history });
  }

  function ComicsRoute() {
    return h(CR.Boundary, {
      name: "Comics page",
      fallback: h("div", { className: "cr-empty text-muted" },
        "Comic Reader hit an error on this page. The rest of Stash is unaffected; details are in the browser console."),
    }, h(ComicsPage));
  }

  try {
    api.register.route(CR.ROUTE, ComicsRoute);
  } catch (e) {
    console.error("[comic-reader] could not register the Comics route:", e);
  }

  // ------------------------------------------------ menu items setting

  // Show the item on a fresh install, once -- Binge's approach. Stash only
  // lists what is in interface.menuItems, and "not in the list" can't tell a
  // new install from someone who unticked us, so this seeds the list a single
  // time and records that in the plugin's settings; after that the user's
  // choice stands. Plain fetch at load, never from inside a render.
  var seeded = null;
  function seedMenuItem() {
    if (seeded) return seeded;
    seeded = CR.gql('query { configuration { interface { menuItems } plugins(include: ["' + CR.PLUGIN_ID + '"]) } }')
      .then(function (d) {
        var items = d.configuration.interface.menuItems;
        var mine = (d.configuration.plugins || {})[CR.PLUGIN_ID] || {};
        // No list at all means Stash's defaults, where we show anyway.
        if (!items || items.indexOf(MENU_ID) >= 0 || mine.navSeeded) return false;
        return CR.gql("mutation ($items: [String!]) { configureInterface(input: {menuItems: $items}) { menuItems } }",
          { items: items.concat([MENU_ID]) })
          .then(function () {
            // configurePlugin replaces the whole map: merge, never overwrite.
            return CR.gql("mutation ($id: ID!, $input: Map!) { configurePlugin(plugin_id: $id, input: $input) }",
              { id: CR.PLUGIN_ID, input: Object.assign({}, mine, { navSeeded: true }) });
          })
          .then(function () { return true; });
      })
      .catch(function (e) {
        console.warn("[comic-reader] could not add Comics to the menu items:", e);
        return false;
      });
    return seeded;
  }
  seedMenuItem();

  // The toggle itself: one more row in the "menu-items" checkbox group.
  try {
    api.patch.before("CheckboxGroup", keepArgs("menu items", function (props) {
      if (!props || props.groupId !== "menu-items" || !Array.isArray(props.items)) return null;
      if (props.items.some(function (i) { return i.id === MENU_ID; })) return null;
      return Object.assign({}, props, { items: props.items.concat([{ id: MENU_ID, headingID: "Comics" }]) });
    }));
  } catch (e) {
    console.error("[comic-reader] could not add the Comics menu-items toggle:", e);
  }

  // Same markup as Stash's own menu items (MainNavBar), so it lines up on
  // desktop and in the collapsed mobile menu alike. Shown when "Comics" is
  // ticked under Settings > Interface > Menu items -- Stash's own query, so
  // it updates the moment that setting is saved.
  function ComicsNavItem() {
    var q = api.GQL && api.GQL.useConfigurationQuery ? api.GQL.useConfigurationQuery() : null;
    var justSeeded = React.useState(false);
    React.useEffect(function () {
      var live = true;
      seedMenuItem().then(function (did) { if (live && did) justSeeded[1](true); });
      return function () { live = false; };
    }, []);

    var cfg = q && q.data && q.data.configuration;
    var items = cfg && cfg.interface ? cfg.interface.menuItems : null;
    if (q && q.loading && !cfg) return null;
    // No list = Stash's defaults (everything on). A list without us = the
    // user switched Comics off, unless we only just seeded it and Stash's
    // cached settings haven't caught up.
    if (items && items.indexOf(MENU_ID) < 0 && !justSeeded[0]) return null;

    var Nav = CR.Bootstrap.Nav;
    var NavLink = CR.Router.NavLink;
    var link = h(NavLink, { activeClassName: "active", to: CR.ROUTE },
      h(CR.Button, { className: "minimal p-4 p-xl-2 d-flex d-xl-inline-block flex-column justify-content-between align-items-center" },
        h(CR.Icon, { name: "faBookOpen", className: "nav-menu-icon d-block d-xl-inline mb-2 mb-xl-0" }),
        h("span", null, "Comics")));
    if (!Nav || !Nav.Link) return h("div", { className: "col-4 col-sm-3 col-md-2 col-lg-auto nav-link" }, link);
    return h(Nav.Link, { eventKey: CR.ROUTE, as: "div", className: "col-4 col-sm-3 col-md-2 col-lg-auto" }, link);
  }

  // Inside a Comics list (CR.ComicsContext) the gallery card grid renders
  // comic cards; everywhere else it is untouched -- `next` gets exactly the
  // arguments Stash passed. useContext is called on every render, so the
  // patched component's hook order never changes.
  // Stash's own grid as a component, for the error fallback -- built lazily,
  // never by calling `next` up front (that would run its hooks in ours).
  function StockGrid(p) { return p.next.apply(null, p.args); }
  try {
    api.patch.instead("GalleryCardGrid", function () {
      var args = Array.prototype.slice.call(arguments);
      var next = args.pop();
      var inComics = React.useContext(CR.ComicsContext);
      if (!inComics || !CR.ComicCardGrid) return next.apply(this, args);
      return h(CR.Boundary, { name: "comic cards", fallback: h(StockGrid, { next: next, args: args }) },
        h(CR.ComicCardGrid, args[0]));
    });
  } catch (e) {
    console.error("[comic-reader] could not add comic cards:", e);
  }

  // The menu is mounted on every page and sits inside Stash's router, so it
  // is also where the page injector lives: it watches navigation and portals
  // the performer/studio Comics tab and the gallery-page buttons in. (Not
  // MainNavBar.UtilityItems: Stash renders that twice, desktop and mobile,
  // which injected everything twice.) The injector renders nothing itself,
  // so it is there whether or not the nav item is switched on.
  try {
    api.patch.before("MainNavBar.MenuItems", keepArgs("nav bar", function (props) {
      var children = React.Children.toArray(props && props.children);
      children.push(h(CR.Boundary, { key: "cr-comics", name: "Comics nav item" }, h(ComicsNavItem)));
      if (CR.Injector) {
        children.push(h(CR.Boundary, { key: "cr-injector", name: "page injector" }, h(CR.Injector)));
      }
      return Object.assign({}, props, { children: children });
    }));
  } catch (e) {
    console.error("[comic-reader] could not add the Comics menu item:", e);
  }
})();
