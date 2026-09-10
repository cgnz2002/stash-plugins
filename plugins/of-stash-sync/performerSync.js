// Fan Site Metadata Sync -- performer-page button.
//
// Adds a "Sync Fan Sites" button to each performer's page that triggers a full
// re-sync scoped to that one performer. The button only needs the performer id;
// the Python "Sync Performer" task maps it back to the site username via the
// performer's name/aliases, so the display name doesn't have to equal the
// username. It searches every configured site, so one button covers them all.
//
// Patterns follow the Stash UI-plugin docs: navigation is detected with the
// documented `stash:location` event (PluginApi.Event), the button is injected
// into the DOM once the performer header renders (the same approach the official
// CommunityScripts UI library's PathElementListener uses), and the task is
// started with the `runPluginTask` mutation using `args_map`. It is
// self-contained -- no CommunityScriptsUILibrary dependency.
(function () {
  "use strict";

  var PLUGIN_ID = "of-stash-sync";
  var TASK_NAME = "Sync Performer";
  var BTN_ID = "fansite-sync-performer-btn";
  var PluginApi = window.PluginApi;

  function performerIdFromPath() {
    var m = window.location.pathname.match(/\/performers\/(\d+)(?:\/|$)/);
    return m ? m[1] : null;
  }

  function graphql(query, variables) {
    return fetch("/graphql", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ query: query, variables: variables || {} }),
    }).then(function (r) {
      return r.json();
    });
  }

  function runSync(performerId, btn) {
    var original = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Syncing…";
    var mutation =
      "mutation ($args: Map!) {" +
      "  runPluginTask(plugin_id: \"" + PLUGIN_ID + "\"," +
      "               task_name: \"" + TASK_NAME + "\"," +
      "               args_map: $args)" +
      "}";
    graphql(mutation, { args: { mode: "performer", performerId: performerId } })
      .then(function (res) {
        if (res.errors && res.errors.length) {
          throw new Error(res.errors[0].message);
        }
        btn.textContent = "Sync queued ✓";
      })
      .catch(function (err) {
        console.error("[of-stash-sync] performer sync failed:", err);
        btn.textContent = "Sync failed";
        window.alert(
          "Sync failed: " + (err && err.message ? err.message : err)
        );
      })
      .finally(function () {
        setTimeout(function () {
          btn.textContent = original;
          btn.disabled = false;
        }, 4000);
      });
  }

  function makeButton() {
    var btn = document.createElement("button");
    btn.id = BTN_ID;
    btn.type = "button";
    btn.className = "btn btn-secondary";
    btn.style.marginLeft = "0.5rem";
    btn.textContent = "Sync Fan Sites";
    btn.title = "Full metadata re-sync for this performer, across every configured site";
    btn.addEventListener("click", function () {
      var id = performerIdFromPath();
      if (id) runSync(id, btn);
    });
    return btn;
  }

  function inject() {
    if (!performerIdFromPath()) return true; // not a performer page: nothing to do
    if (document.getElementById(BTN_ID)) return true; // already added
    // Best-effort placement across common Stash performer-page layouts. Adjust
    // the first matching selector if the button lands in an odd spot on your
    // Stash version.
    var host =
      document.querySelector(".detail-header .details-edit") ||
      document.querySelector(".detail-header .name-icons") ||
      document.querySelector(".performer-head") ||
      document.querySelector(".detail-header");
    if (!host) return false; // header not rendered yet
    host.appendChild(makeButton());
    return true;
  }

  // Wait (briefly) for the performer header to render after a navigation, then
  // inject. Mirrors PathElementListener without depending on it.
  function injectWhenReady() {
    var tries = 0;
    (function attempt() {
      if (inject()) return;
      if (++tries > 40) return; // ~10s max, then give up until next navigation
      setTimeout(attempt, 250);
    })();
  }

  if (PluginApi && PluginApi.Event && PluginApi.Event.addEventListener) {
    // Documented navigation event.
    PluginApi.Event.addEventListener("stash:location", injectWhenReady);
  } else {
    // Fallback for older builds without the event: observe the DOM.
    var observer = new MutationObserver(function () {
      inject();
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
  }
  injectWhenReady();
})();
