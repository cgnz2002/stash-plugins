// OnlyFans Metadata Sync -- Title Exclusions editor.
//
// Adds a list editor for the plugin's `titleExclusions` setting -- the phrases /
// regexes stripped from generated scene/image/gallery titles. It reuses Stash's
// OWN list component (`PluginApi.components.StringListSetting`, the same widget
// behind Settings > Library > Exclusions: a "Change" button opening a modal with
// add / remove / reorder rows and Confirm / Cancel), so it looks and behaves
// exactly like the native Exclusions UI rather than a hand-rolled modal.
//
// Surfaced the way real plugins do it (cf. CommunityScripts/AIOverhaul): a page
// is registered at /plugins/of-stash-sync-titles and a button is injected into
// Settings > Tools via patch.before("SettingsToolsSection"). The list is read
// from and written to the plugin's own config with the standard
// configuration{plugins} query and configurePlugin mutation. The value is stored
// as a JSON string so it is also valid in the manifest "Title Exclusions" STRING
// field (a hand-editable fallback if this script ever fails to load).
(function () {
  "use strict";

  var PluginApi = window.PluginApi;
  if (!PluginApi || !PluginApi.React) return;

  var React = PluginApi.React;
  var PLUGIN_ID = "of-stash-sync";
  var KEY = "titleExclusions";
  var ROUTE = "/plugins/of-stash-sync-titles";

  var libs = PluginApi.libraries || {};
  var Button =
    (libs.Bootstrap && libs.Bootstrap.Button) ||
    function (p) { return React.createElement("button", p, p.children); };
  var RR = libs.ReactRouterDOM || {};
  var Link = RR.Link;

  function gql(query, variables) {
    return fetch("/graphql", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ query: query, variables: variables || {} }),
    }).then(function (r) { return r.json(); });
  }

  // Normalise whatever is stored (JSON array, JSON string, or newline text) into
  // a string[] for the editor.
  function toList(raw) {
    if (Array.isArray(raw)) return raw.map(String);
    if (typeof raw === "string" && raw.trim()) {
      try {
        var arr = JSON.parse(raw);
        if (Array.isArray(arr)) return arr.map(String);
      } catch (e) { /* not JSON: fall through to newline split */ }
      return raw.split(/\r?\n/).map(function (s) { return s.trim(); })
                .filter(function (s) { return s.length > 0; });
    }
    return [];
  }

  function loadPatterns() {
    return gql("query { configuration { plugins } }").then(function (res) {
      var conf = res && res.data && res.data.configuration;
      var plugins = (conf && conf.plugins) || {};
      var cfg = plugins[PLUGIN_ID] || {};
      return toList(cfg[KEY]);
    });
  }

  function savePatterns(list) {
    var input = {};
    // Stored as a JSON string so it stays compatible with the manifest STRING
    // field; the Python side parses JSON arrays, JSON strings and plain text.
    input[KEY] = JSON.stringify(list);
    return gql(
      "mutation ($id: ID!, $input: Map!) {" +
      "  configurePlugin(plugin_id: $id, input: $input)" +
      "}",
      { id: PLUGIN_ID, input: input }
    ).then(function (res) {
      if (res.errors && res.errors.length) throw new Error(res.errors[0].message);
    });
  }

  function TitleExclusionsPage() {
    var st = React.useState([]);
    var value = st[0], setValue = st[1];
    var ld = React.useState(true);
    var loading = ld[0], setLoading = ld[1];
    var er = React.useState(null);
    var error = er[0], setError = er[1];

    React.useEffect(function () {
      var live = true;
      loadPatterns()
        .then(function (list) { if (live) { setValue(list); setLoading(false); } })
        .catch(function (e) { if (live) { setError(String(e)); setLoading(false); } });
      return function () { live = false; };
    }, []);

    function onChange(next) {
      setValue(next);
      savePatterns(next).catch(function (e) {
        setError("Could not save: " + (e && e.message ? e.message : e));
      });
    }

    var StringListSetting = PluginApi.components && PluginApi.components.StringListSetting;

    var children = [
      React.createElement("h4", { key: "h" }, "OnlyFans Sync — Title Exclusions"),
      React.createElement(
        "p", { key: "d", className: "text-muted" },
        "Phrases or regular expressions removed from generated scene, image and " +
        "gallery titles. The description/details field keeps the original text. " +
        "Matched case-insensitively; e.g. an entry \"new collab:\" turns " +
        "\"New collab: Beach day\" into \"Beach day\"."
      ),
    ];

    if (error) {
      children.push(React.createElement(
        "div", { key: "e", className: "text-danger" }, String(error)));
    }

    if (StringListSetting) {
      children.push(React.createElement(StringListSetting, {
        key: "list",
        id: "of-stash-sync-title-exclusions",
        heading: "Title exclusions",
        subHeading: "One phrase or regex per row. Removed from titles only.",
        value: value,
        onChange: onChange,
        defaultNewValue: "new collab:",
        disabled: loading,
      }));
    } else {
      // Very old Stash without the component: fall back to a plain textarea.
      children.push(React.createElement("textarea", {
        key: "ta",
        className: "form-control",
        rows: 8,
        defaultValue: value.join("\n"),
        disabled: loading,
        onBlur: function (ev) {
          onChange(ev.target.value.split(/\r?\n/)
            .map(function (s) { return s.trim(); })
            .filter(function (s) { return s.length > 0; }));
        },
      }));
      children.push(React.createElement(
        "p", { key: "fallnote", className: "text-muted" },
        "One pattern per line; saved when you click away."));
    }

    if (Link) {
      children.push(React.createElement(
        "div", { key: "back", style: { marginTop: "1rem" } },
        React.createElement(Link, { to: "/settings?tab=plugins" }, "← Back to plugin settings")));
    }

    return React.createElement("div", { className: "container-fluid", style: { padding: "1rem" } },
      children);
  }

  try {
    PluginApi.register.route(ROUTE, function () {
      return React.createElement(TitleExclusionsPage);
    });
  } catch (e) {
    console.error("[of-stash-sync] could not register title-exclusions route:", e);
  }

  // Add a button under Settings > Tools that opens the editor page.
  try {
    PluginApi.patch.before("SettingsToolsSection", function (props) {
      var Setting = PluginApi.components && PluginApi.components.Setting;
      if (!Setting) return props;
      var heading = Link
        ? React.createElement(Link, { to: ROUTE },
            React.createElement(Button, null, "OnlyFans Sync: Title Exclusions"))
        : React.createElement(Button, {
            onClick: function () { window.location.href = ROUTE; },
          }, "OnlyFans Sync: Title Exclusions");
      return [{
        children: React.createElement(
          React.Fragment, null,
          props.children,
          React.createElement(Setting, {
            heading: heading,
            subHeading: "Edit the phrases/regexes stripped from synced titles.",
          })
        ),
      }];
    });
  } catch (e) {
    console.error("[of-stash-sync] could not patch SettingsToolsSection:", e);
  }
})();
