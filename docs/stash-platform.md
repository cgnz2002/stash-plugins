# Stash platform notes

What Stash offers an extension author, and how to check any of it. Everything
here was verified against **Stash v0.31.x** source, not recalled — each claim
names the file it came from so it can be re-checked when Stash moves.

`CLAUDE.md` covers this repository. This file covers the thing it plugs into,
and is only worth reading when doing Stash-API work: adding a plugin of a new
type, using an API this repo hasn't used yet, or deciding whether Stash can do
something at all.

## How to verify a claim about Stash

There is no Stash checkout here and the plugins run against a server we can't
query, so **read the source at the pinned tag**:

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
- **Scan hooks do not exist.** The source carries the comment *"Scan-related
  hooks are currently disabled until post-hook execution is integrated."* So
  there is no way to run code as files are ingested; a plugin has to be
  triggered afterwards.

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
  A scene or image can also be detached from a gallery via its own update:
  both `SceneUpdateInput` and `ImageUpdateInput` take `gallery_ids`, so
  membership is settable from either side.
- **`.nogallery` / `.forcegallery` are per-directory, NOT recursive.** They
  override the global *Create galleries from folders* setting one folder at a
  time, and the file must be lower case. `ScanHandler.getOrCreateGallery`
  (`pkg/image/scan.go`) stats them in `filepath.Dir(f.Base().Path)` — the
  **immediate parent of each image file**, with no walk up the tree. So
  exempting a library means a file in every directory that *directly* contains
  images (`find <root> -type d -exec touch {}/.nogallery \;`), not one at the
  root. They are consulted at scan time only: neither file removes a folder
  gallery that already exists.
- **`galleryDestroy` with `delete_file: false` destroys no images.**
  `gallery.Service.Destroy` (`pkg/gallery/delete.go`) only cascades to images
  for a **zip-based** gallery, or for a folder-based one *when `deleteFile` is
  set*. A created (non-zip, non-folder) gallery — which is what a plugin makes
  — is just a record, so dropping it is metadata-only and the images stay in
  the library. Both flags default to false (`utils.IsTrue(input.DeleteFile)`
  on a nil pointer).

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
