// Fan Site Metadata Sync -- Title Exclusions editor.
//
// A list editor for the plugin's `titleExclusions` setting -- the phrases /
// regexes stripped from generated scene/image/gallery titles.
//
// IMPORTANT: this renders on a standalone plugin route, which is OUTSIDE Stash's
// SettingsContext. Stash's `StringListSetting` (the modal Exclusions widget) and
// its `Setting`/`ModalSetting` wrappers call `useSettings()` internally and throw
// "useSettings must be used within a SettingsContext" when rendered here. So this
// uses its OWN context-free rows editor (add / remove rows + Save) instead of
// Stash's list component -- functionally the same list-of-patterns UX, without the
// settings-context dependency that crashed the page.
//
// The list is read from and written to the plugin's own config with the standard
// configuration{plugins} query and configurePlugin mutation, stored as a JSON
// string so the manifest "Title Exclusions" STRING field stays a hand-editable
// fallback. Surfaced via a button in Settings > Tools
// (patch.before("SettingsToolsSection")), the CommunityScripts/AIOverhaul pattern.
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

  function toList(raw) {
    if (Array.isArray(raw)) return raw.map(String);
    if (typeof raw === "string" && raw.trim()) {
      try {
        var arr = JSON.parse(raw);
        if (Array.isArray(arr)) return arr.map(String);
      } catch (e) { /* not JSON: newline split */ }
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

  // Context-free rows editor: a row per pattern (text input + remove) plus an
  // "Add pattern" button. No Stash app context required, so it renders safely on
  // a standalone plugin route.
  function RowsEditor(props) {
    var value = props.value || [];
    function setAt(i, v) {
      var next = value.slice();
      next[i] = v;
      props.setValue(next);
    }
    function removeAt(i) {
      var next = value.slice();
      next.splice(i, 1);
      props.setValue(next);
    }
    var rows = value.map(function (v, i) {
      return React.createElement("div", { className: "input-group mb-2", key: i },
        React.createElement("input", {
          className: "form-control",
          type: "text",
          value: v,
          placeholder: "new collab:",
          onChange: function (e) { setAt(i, e.target.value); },
        }),
        React.createElement("div", { className: "input-group-append" },
          React.createElement(Button, {
            variant: "danger",
            onClick: function () { removeAt(i); },
          }, "−"))
      );
    });
    rows.push(React.createElement("div", { key: "add" },
      React.createElement(Button, {
        variant: "secondary",
        onClick: function () { props.setValue(value.concat([""])); },
      }, "+ Add pattern")));
    return React.createElement("div", null, rows);
  }

  function TitleExclusionsPage() {
    var st = React.useState([]);
    var value = st[0], setValue = st[1];
    var ld = React.useState(true);
    var loading = ld[0], setLoading = ld[1];
    var sv = React.useState("");
    var status = sv[0], setStatus = sv[1];

    React.useEffect(function () {
      var live = true;
      loadPatterns()
        .then(function (list) { if (live) { setValue(list); setLoading(false); } })
        .catch(function (e) {
          if (live) { setStatus("Load failed: " + e); setLoading(false); }
        });
      return function () { live = false; };
    }, []);

    function onSave() {
      var cleaned = value.map(function (s) { return (s || "").trim(); })
                         .filter(function (s) { return s.length > 0; });
      setStatus("Saving…");
      savePatterns(cleaned)
        .then(function () { setValue(cleaned); setStatus("Saved ✓"); })
        .catch(function (e) { setStatus("Save failed: " + (e && e.message ? e.message : e)); });
    }

    // Our own context-free rows editor. We deliberately do NOT use Stash's
    // StringListSetting/StringListInput here: those (or their Setting/ModalSetting
    // wrappers) call useSettings(), which throws outside the Settings page's
    // SettingsContext -- which is exactly where this standalone plugin route
    // renders. RowsEditor needs no app context, so it can't hit that crash.
    var editor = React.createElement(RowsEditor, { value: value, setValue: setValue });

    return React.createElement("div",
      { className: "container-fluid", style: { padding: "1.5rem", maxWidth: "760px" } },
      React.createElement("h4", null, "Fan Site Sync — Title Exclusions"),
      React.createElement("p", { className: "text-muted" },
        "Phrases or regular expressions removed from generated scene, image and " +
        "gallery titles. The description/details keeps the original post text. " +
        "Matched case-insensitively; e.g. \"new collab:\" turns " +
        "\"New collab: Beach day\" into \"Beach day\"."),
      loading ? React.createElement("div", null, "Loading…") : editor,
      React.createElement("div", { style: { marginTop: "1rem" } },
        React.createElement(Button, { variant: "primary", disabled: loading, onClick: onSave }, "Save"),
        React.createElement("span", { style: { marginLeft: "0.75rem" } }, status)),
      Link
        ? React.createElement("div", { style: { marginTop: "1.5rem" } },
            React.createElement(Link, { to: "/settings?tab=plugins" }, "← Back to plugin settings"))
        : null
    );
  }

  try {
    PluginApi.register.route(ROUTE, function () {
      return React.createElement(TitleExclusionsPage);
    });
  } catch (e) {
    console.error("[of-stash-sync] could not register title-exclusions route:", e);
  }

  try {
    PluginApi.patch.before("SettingsToolsSection", function (props) {
      var Setting = PluginApi.components && PluginApi.components.Setting;
      if (!Setting) return props;
      var heading = Link
        ? React.createElement(Link, { to: ROUTE },
            React.createElement(Button, null, "Fan Site Sync: Title Exclusions"))
        : React.createElement(Button, {
            onClick: function () { window.location.href = ROUTE; },
          }, "Fan Site Sync: Title Exclusions");
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
