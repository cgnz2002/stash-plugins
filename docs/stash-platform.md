# Stash platform notes

What Stash offers an extension author, and how to check any of it. Everything
here was verified against **Stash v0.31.x** source, not recalled — each claim
names the file it came from so it can be re-checked when Stash moves.

`CLAUDE.md` covers this repository. This file covers the thing it plugs into,
and is only worth reading when doing Stash-API work: adding a plugin of a new
type, using an API this repo hasn't used yet, or deciding whether Stash can do
something at all.

## How to verify a claim about Stash

Two ways: read the source at the pinned tag, or run the real thing (see
*Running a real Stash* below) -- which is how several entries here were
corrected. The source first:

```
https://raw.githubusercontent.com/stashapp/stash/v0.31.0/<path>
```

Useful paths:

| What | Path |
|---|---|
| Top-level queries/mutations | `graphql/schema/schema.graphql` |
| A type's fields and inputs | `graphql/schema/types/<thing>.graphql` |
| Scraper types, enums, inputs | `graphql/schema/types/scraper.graphql` |
| Task inputs (Identify, Scan…) | `graphql/schema/types/metadata.graphql` |
| Plugin manifest parsing | `pkg/plugin/config.go` |
| Hook trigger names | `pkg/plugin/hook/hooks.go` |
| What a script scraper receives | `pkg/scraper/script.go` |
| Which scrape paths exist per type | `pkg/scraper/cache.go` |

The schema answers *what a field is*. It does **not** answer *what the UI does
with it* — for that, read `ui/v2.5/src/...`. Several limits below are in the UI
only and are invisible from the schema. A few UI files have moved since v0.31.0;
if a path 404s, try the same path under `develop` and treat the result as
current-ish rather than as v0.31.

## Plugin interfaces

`pkg/plugin/config.go` accepts exactly three, and defaults to `raw` when the
manifest omits `interface:`:

- **`raw`** — an executable. Stash writes `common.PluginInput` as JSON to
  **stdin** (`server_connection` + `args`), reads a JSON result from **stdout**,
  and shows **stderr** in the log viewer when lines carry the SOH/level/STX
  prefix. This is what `of-stash-sync` uses; `CLAUDE.md` documents the contract.
- **`rpc`** — the executable speaks Stash's RPC protocol instead of one-shot
  JSON. Buys progress/stop handling beyond what stderr logging gives; costs a
  protocol implementation.
- **`js`** — the plugin *is* JavaScript, run by Stash's embedded interpreter,
  with no separate process. Not the same thing as the `ui.javascript` block,
  which ships browser-side JS (what `performerSync.js` and
  `titleExclusions.js` do).

## Hooks

A manifest can declare a `hooks:` block whose `triggeredBy:` lists trigger
names, so a plugin runs on a data change rather than only from the Tasks page.
The full set (`pkg/plugin/hook/hooks.go`):

`Scene`, `Image`, `Gallery`, `GalleryChapter`, `SceneMarker`, `Performer`,
`Studio`, `Tag`, `Group` (and deprecated `Movie`), each as
`<Type>.Create.Post`, `<Type>.Update.Post`, `<Type>.Destroy.Post`, plus
`Tag.Merge.Post`.

Two limits worth knowing before designing around hooks:

- **Every hook is `.Post`.** There is no `.Pre` trigger, so a hook cannot veto
  or rewrite a change on its way in — only react after it has happened.
- **Scans DO fire the create hooks** -- despite `hooks.go` carrying the
  comment *"Scan-related hooks are currently disabled until post-hook execution
  is integrated."* That comment is stale: `pkg/gallery/scan.go` and
  `pkg/image/scan.go` call `RegisterPostHooks(..., GalleryCreatePost / ImageCreatePost, nil, nil)`,
  and scanning a `.cbz` on v0.31.1 fired `Gallery.Create.Post` once and
  `Image.Create.Post` per page, each with `"input": null`. (Verified with a
  throwaway hook plugin logging its `hookContext`.) They are create hooks, so
  files Stash already knows don't fire them again. comic-reader relies on this.
- **Hooks run inside the triggering request**, one after another, so a slow
  hook slows the scan or mutation that fired it. That is why comic-reader's
  hooks are embedded JS rather than a `raw` process per event.

## Embedded JavaScript plugins (`interface: js`)

Verified on v0.31.1 (`pkg/plugin/js.go`, `pkg/javascript/*.go`) and by running
comic-reader:

- `exec: [script.js]` is relative to the manifest. Globals: `input`
  (`input.Args` holds the args; a hook's `hookContext` is in there), `log`
  (`Trace/Debug/Info/Warn/Error/Progress`), `gql.Do(query, vars)` (returns
  `data`, throws on GraphQL errors) and `util.Sleep`. The script's final value
  is the result: return `{Output: ..., Error: ...}` (capitalised).
- `gql.Do` goes straight to Stash's GraphQL handler in-process with the
  session cookie -- no network, and cheap enough for per-image hooks.
- **`log.Progress` blocks forever unless the plugin runs as a queued task.**
  It sends on a channel that is nil for hooks and `runPluginOperation`. Gate
  it behind an arg only the manifest's tasks pass.
- The runtime is goja; keep backend code to ES5 to stay safe across the goja
  versions Stash has shipped.

## Running plugins: calls and outcomes

- `runPluginOperation(plugin_id, args)` runs a plugin **synchronously** and
  returns its output (an `error` becomes a GraphQL error). It ignores the
  manifest's `defaultArgs`. Handy as a test harness and for UI → backend calls.
- `runPluginTask(plugin_id, task_name, args_map)` queues a job. A task that
  returns `{"error": ...}` is logged at error level, but **the job ends
  FINISHED, not FAILED** -- only an unknown task name etc. fails the job.

## Filtering quirks

- **`NOT` over a sub-filter is evaluated per joined row, not per object.** An
  image in one comic gallery and one other gallery matches BOTH
  `galleries_filter: {tags: comic}` and `NOT: {galleries_filter: {tags: comic}}`
  (v0.31.1, reproduced against a live server). Use such a `NOT` only as a
  candidate list and check membership in code.
- **Default filters are per view**, stored at
  `configuration.ui.defaultFilters.<view>` and written with
  `configureUISetting(key: "defaultFilters.<view>", value: {mode, find_filter, object_filter, ui_options})`.
  Performer, studio and tag pages have their own views (`performer_galleries`,
  `studio_images`, `tag_galleries`, ...), so the main Galleries page's default
  doesn't reach a performer's Galleries tab.
- A saved **tags** criterion is `{modifier: "INCLUDES", value: {items: [{id,label}], excluded: [{id,label}], depth}}`.
  The UI has no EXCLUDES modifier for tags any more; exclusions live in
  `excluded`. `depth: -1` includes child tags.
- The `path` filter is a case-insensitive substring match, on images it can
  match more loosely than expected -- re-check paths in code.

## UI plugins: what is and isn't patchable (v0.31.1)

Found by reading the shipped bundle (`/assets/*.js`), not the docs:

- **How patches chain** (`RB` / `nMt` in the v0.31 bundle): every `before`
  runs first, and its return value *replaces* the argument list; then the
  `instead` handlers run as a chain, each called with those arguments plus
  `next` appended; then each `after` gets the arguments plus the result.
  React calls a function component with **two** arguments, and some plugins
  (Stash TV) read `next` positionally as the third. So a `before` must return
  every argument it was given, `[props, ...rest]`, never `[props]` -- the
  latter blanks the app for anyone running such a plugin (React #130).
  Keep `children` an array for the next patch, and never throw out of a
  patch.
- **Nav items the way Stash TV and Binge do it:** patch `CheckboxGroup` for
  `groupId === "menu-items"` to add `{id, headingID}` to its `items` (that
  is the Settings > Interface > Menu items list), and show the nav item only
  when `configuration.interface.menuItems` contains that id. The list is null
  until the user first edits it, which means "Stash's defaults".
- A component can be rendered and then **discarded** by React (e.g. while a
  lazy chunk loads on first page load), so never record anything global
  during render; do it in an effect.
- `MainNavBar.MenuItems` is rendered **once** and sits inside the router --
  a good home for a nav item and for any always-mounted helper component.
  `MainNavBar.UtilityItems` is rendered **twice** (desktop and mobile), so
  anything mounted there runs twice.
- `PluginRoutes` / `register.route` is react-router **v5**:
  `<Route path={p} component={C}/>`, prefix-matching.
- Patchable around galleries/performers: `GalleryCard*`, `GalleryList`,
  `FilteredGalleryList`, `PerformerPage`, `PerformerGalleriesPanel`,
  `PerformerImagesPanel`, `StudioDetailsPanel`, `FrontPage`, card grids and
  recommendation rows. **Not** patchable: the performer/studio tab list, the
  studio page as a whole, and the gallery page -- add to those by finding the
  rendered element and portalling into it (`ReactDOM.createPortal` keeps the
  router and contexts).
- Performer/studio pages redirect an unknown tab segment
  (`/performers/1/comics` → `/performers/1/galleries`), so an injected tab
  can't have its own URL.
- Stash's own inputs use `clearable-text-field form-control` (text) and
  `btn-secondary form-control` (select); plain `form-control` renders white.

## Running a real Stash

A session can run the actual server; the release binary is self-contained:

```
curl -sSL -o stash-linux https://github.com/stashapp/stash/releases/download/v0.31.1/stash-linux
chmod +x stash-linux && ./stash-linux --config config.yml --nobrowser
```

With a `config.yml` that sets `stash: [{path: <library>}]`, `database`,
`generated`, `cache`, `blobs_path`, `plugins_path: <repo>/plugins` (plugins
load live from the working tree; `mutation { reloadPlugins }` after an edit)
and **`port: 9998`** -- `tests/test_dryrun.py` expects nothing to be listening
on 9999. `apt-get install ffmpeg` gives Stash ffprobe for video; `.cbz` and
images scan without it. Node's Playwright (with the preinstalled Chromium)
drives the UI; a fresh install shows a Release Notes modal to close first.

## Scrapers

A scraper is **not** a plugin: separate YAML, its own directory, and
`build_site.sh` does not package it (it only globs `plugins/**/*.yml`). A
scraper can be a local script (`action: script`) with no network access, which
makes "scrape from files already on disk" viable in principle.

Content types are `SCENE`, `GALLERY`, `IMAGE`, `PERFORMER`, `GROUP`, `MOVIE`;
scrape types are `NAME`, `FRAGMENT`, `URL`
(`graphql/schema/types/scraper.graphql`).

A scraped result is rich — `ScrapedScene`/`ScrapedImage`/`ScrapedGallery` all
carry `studio`, `tags` and `performers`, and `ScrapedStudio` has `stored_id`,
so the scrape dialog matches existing objects or creates new ones. Do not
assume a scraper can only return flat text.

**The limits are per content type, and they are sharp:**

| | Scene | Gallery | Image |
|---|---|---|---|
| Fragment includes each file's `path` | yes | yes | **no** |
| Multi-result "pick one" (`scrapeByName`) | yes | no | no |
| Covered by the bulk Identify task | yes | no | no |

- **An image fragment is only `{id, title, details, urls, date}`.**
  `scrapeImageByImage` calls `imageToUpdateInput` (`pkg/scraper/stash.go`),
  which builds an `ImageUpdateInput` — no files, no paths. Scenes and galleries
  instead use `sceneInputFromScene` / `galleryInputFromGallery`
  (`pkg/scraper/script.go`), which both attach a `Files` array with a `Path` per
  file. So an image scraper cannot see which files an image was merged from.
  The `id` is present, so a script could query Stash's GraphQL back for
  `visual_files { path }` — but that needs an API key of its own, since a
  scraper is not handed the session cookie a plugin gets.
- **`scrapeByName` supports only performers and scenes** — `pkg/scraper/script.go`
  ends its switch with `default: return nil, ErrNotSupported`. That is the only
  path returning a *list* of candidates, so images and galleries can never offer
  a choice.
- **A fragment scrape returns exactly one result** for every type: the script's
  output is unmarshalled into a single object, not a slice.
- **The image UI hardcodes the first result** —
  `ImageEditPanel.tsx` does `setScrapedImage(result.data.scrapeSingleImage[0])` —
  so even a list would not surface.
- **Identify is scene-only.** `IdentifyMetadataInput`
  (`graphql/schema/types/metadata.graphql`) takes `sceneIDs` and `paths` and
  nothing else. Its `setOrganized` and per-field `createMissing` (performers,
  tags, studios only) therefore never apply to images or galleries, which stay
  one-at-a-time in the UI.

The practical reading: scrapers are for **interactive, per-item** work, and are
strongest on scenes. For anything bulk, or anything touching galleries and
images at library scale, a plugin task is the right tool — which is why this
repo is plugins.

## Galleries

- **Folder galleries vs plugin galleries.** Stash makes a gallery per scanned
  folder of images. A folder gallery has a `folder`; a created one does not, and
  that field is the only reliable discriminator. Stash **owns** a folder
  gallery's contents and rejects `addGalleryImages` on it with *"cannot change
  contents of folder-based gallery"*. Its metadata is still writable.
- **No nesting, no group link.** Galleries cannot contain galleries, and there
  is no gallery→group relation, so "a gallery of galleries" is impossible.
  Groups take scenes only.
- **Galleries carry `scene_ids`**, which is how a gallery covers video at all.

### Chapters

`GalleryChapter { id, gallery, title, image_index, created_at, updated_at }`,
written with `galleryChapterCreate` / `galleryChapterUpdate` /
`galleryChapterDestroy`, and readable as `Gallery.chapters`.

Two facts decide whether chapters are usable for a given idea:

- **`image_index` is 1-based**, not 0-based. `GalleryChapterForm.tsx` validates
  `.moreThan(0)` and defaults to `1`, and the detail page converts on click:
  `onClickChapter(imageindex) => showLightbox(imageindex - 1)`.
- **It indexes a `path`-sorted list, and that sort is hardcoded.** Both
  `GalleryViewer.tsx` and the lightbox hook query with `sort: "path"`; it is not
  a user-selectable order. So chapter positions are stable — but they are
  positions, so they shift whenever the gallery's image set changes and must be
  recomputed, and the ordering is whatever path-ascending gives, which cannot be
  overridden.

Because the sort is server-side, the safe way to compute an index is to read the
gallery's images back with the same `sort: "path"` filter and use the returned
order, rather than predicting how SQLite collates a path containing emoji or
mixed case.

## Auth

A plugin is handed a **session cookie** on stdin, which expires — long runs get
`HTTP 401` partway through. Read `configuration { general { apiKey } }` with the
cookie while it is still valid and send that as the `ApiKey` header afterwards;
API keys don't expire. `of-stash-sync` does this in `use_api_key()`.

A scraper gets no such credential, which is why the id-plus-callback trick above
needs its own configured key.
