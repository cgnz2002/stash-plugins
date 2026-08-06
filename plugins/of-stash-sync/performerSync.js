// OnlyFans Metadata Sync -- performer-page button.
//
// Adds a "Sync OnlyFans" button to each performer's page that triggers a full
// re-sync scoped to that one performer. The button only needs the performer id;
// the Python "Sync Performer" task maps it back to the OF username via the
// performer's name/aliases, so the display name doesn't have to equal the
// username.
//
// This is a plain DOM injector (no dependency on Stash's internal React
// component names, which vary by version). If the button lands in an awkward
// spot on your Stash version, tweak the selectors in `tryInject` below.
(function () {
  "use strict";

  var PLUGIN_ID = "of-stash-sync";
  var TASK_NAME = "Sync Performer";
  var BTN_ID = "of-sync-performer-btn";

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
          "OnlyFans sync failed: " + (err && err.message ? err.message : err)
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
    btn.textContent = "Sync OnlyFans";
    btn.title = "Full metadata re-sync for this performer";
    btn.addEventListener("click", function () {
      var id = performerIdFromPath();
      if (id) runSync(id, btn);
    });
    return btn;
  }

  function tryInject() {
    var performerId = performerIdFromPath();
    if (!performerId) return; // only on a performer's page
    if (document.getElementById(BTN_ID)) return; // already added

    // Best-effort placement across common Stash performer-page layouts.
    var host =
      document.querySelector(".detail-header .details-edit") ||
      document.querySelector(".detail-header .name-icons") ||
      document.querySelector(".performer-head") ||
      document.querySelector(".detail-header");
    if (!host) return;
    host.appendChild(makeButton());
  }

  // Stash is a single-page app, so watch for DOM/route changes and (re)inject.
  var observer = new MutationObserver(function () {
    tryInject();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  tryInject();
})();
