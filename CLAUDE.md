# CLAUDE.md

Guidance for working in this repository.

## What this repo is

A collection of native [Stash](https://stashapp.cc) plugins, distributed as a
Stash **plugin source**. It is built from the official
[`stashapp/plugins-repo-template`](https://github.com/stashapp/plugins-repo-template):
a GitHub Action packages everything under `plugins/` into an `index.yml` + per-plugin
zips and publishes them to GitHub Pages. Stash points at the published `index.yml`
and installs plugins straight from here.

Published source URL (add this in Stash → Settings → Plugins → Add Source):

```
https://cgnz2002.github.io/stash-plugins/main/index.yml
```

Currently there are three plugins (the Patreon one is being retired — see
*Patreon* below):

- **`plugins/of-stash-sync/`** — **Fan Site Metadata Sync**. Syncs scraped
  fan-site metadata into matching Stash scenes and images: title, details, date,
  post URL, performers, studio, code, tags and the `organized` flag. It serves
  **several sites from one task** — OnlyFans (from
  [OF-Scraper](https://github.com/datawhores/OF-Scraper)), JustFor.Fans (from
  jff-scraper) and Patreon (from
  [patreon-dl](https://github.com/patrickkfkan/patreon-dl)). The first two share
  a `user_data.db` schema shape; Patreon has **no database at all** and is read
  off disk through an adapter (see *Patreon* below).
  **The directory and manifest filename are still `of-stash-sync`, and that is
  deliberate: Stash keys plugin settings by plugin id, so keeping it preserves
  every existing install's configuration.** The per-site differences live in
  `sources.py`; see *Multi-site architecture* below.
- **`plugins/comic-reader/`** — **Comic Reader**. A Comics section, a reader
  (side-by-side page spreads, or a continuous webtoon scroll) and a Comics tab
  on performer and studio pages. A comic is a gallery tagged `Comic` or any
  tag under it; `.cbz` galleries are marked automatically, comics and their
  pages are hidden from the Galleries/Images pages. Unlike the others its
  backend is **embedded JavaScript** (`interface: js`), not Python — see
  *Comic Reader* below for why, and for the Stash quirks it works around.
- **`plugins/patreon-stash-sync/`** — **DEPRECATED and frozen.** Its manifest
  `name` and every task description say so, so Stash shows it in the plugin
  list and Tasks page; it stays published rather than being dropped from the
  index, because removing it would break *Check for Updates* for anyone who
  still has it installed. Don't port fixes to it — every Patreon fix since the
  merge has gone into `of-stash-sync` only. **Its manifest `name` must not
  start with a YAML indicator** such as `[`: `build_site.sh` interpolates it
  into `index.yml`, and an unparseable index breaks the source for every user,
  including the plugins that were fine. `tests/test_build_index.py` guards
  this. Historically: Patreon Metadata Sync, which syncs metadata for
  Patreon content downloaded with
  [patreon-dl](https://github.com/patrickkfkan/patreon-dl). Unlike of-stash-sync,
  there is **no database step**: patreon-dl writes each post's metadata beside the
  media (`post_info/info.txt`, `post_info/post-api.json`) and collection JSON, so
  `patreon_source.py` reads those files **directly** off the data path. Because
  Stash ingests each post folder as a **gallery**, the plugin syncs each post onto
  its Stash **gallery and the images inside it** (matched by the post folder
  basename, which embeds the post id), and creates one gallery per Patreon
  **collection** with the member posts' images attached. Studios (parent +
  per-creator) and the creator performer are created if missing. A Patreon post
  has a single creator, so there is no @mention/crew handling and the title is
  used as-is. See that plugin's README for the pipeline.

## Repository layout

```
build_site.sh                       Template build script: plugins/ -> _site/<branch>/{index.yml, <id>.zip}
.github/workflows/deploy.yml         Builds the source and deploys to GitHub Pages on push to plugins/**
docs/stash-platform.md               What STASH offers an extension author, and how to verify it
tests/                               Stdlib test scripts + run.py (outside plugins/ so they aren't packaged)
plugins/
  of-stash-sync/                     (id kept for settings continuity; serves all sites)
    of-stash-sync.yml                Plugin manifest (settings, tasks, exec entry point)
    sync.py                          Entry point + orchestration (run by Stash)
    sources.py                       Per-site behaviour (SourceProfile) + detection
    stash.py                         Minimal Stash GraphQL client (stdlib only)
    source_database.py               Read-only reader for OF-Scraper / jff-scraper dbs
    media.py                         Post text -> title/details/tags/date processing
    log.py                           Stash log-viewer logging via stderr
    performerSync.js, titleExclusions.js  UI plugins
    README.md                        User-facing docs (settings, tasks, install)
    onlyfans.png, justforfans.png    Studio icons (one per site)
  patreon-stash-sync/
    patreon-stash-sync.yml           Plugin manifest (gallery/collection tasks)
    sync.py                          Orchestration: posts -> galleries + images, collections -> galleries
    patreon_source.py                Reads patreon-dl's on-disk info.txt/post-api.json/collection JSON
    stash.py                         Stash GraphQL client (adds gallery ops)
    media.py, log.py                 Copied from of-stash-sync
    README.md                        User-facing docs + pipeline diagram
    patreon.png                      Studio icon
  comic-reader/
    comic-reader.yml                 Manifest: hooks, tasks, settings, ui files (order matters)
    comic-reader.js                  Backend (Stash's embedded JS, ES5): tags, hooks, tasks
    ui/common.js                     Shared UI helpers; defines window.ComicReader (load first)
    ui/info.js                       The reader's details panel (docked, or a sheet on phones)
    ui/overview.js                   The reader's page overview: jump to a page, Order by, Reorder
    ui/reader.js, ui/library.js      The reader; the Comics list (Stash's own) + comic cards
    ui/series.js                     Series: add-to-series dialog, Series tab, a series' page
    ui/builder.js                    "New comic from images"
    ui/inject.js                     Comics tab on performer/studio pages, gallery-page buttons
    ui/main.js                       Route + nav item + injector wiring (load last)
    ui/comics.css                    All styles, prefixed cr-
    README.md                        User-facing docs
```

### Multi-site architecture

One plugin serves every supported site because the scraper schemas are the same
shape: OF-Scraper's `profiles`/`medias`/`posts`, which jff-scraper deliberately
mirrors (numeric `model_id`, `posted_at`) and then extends.

- **`source_database.py`** — one reader for both. jff-scraper's extra tables
  (`jff_posts`, `schema_flags`) are optional: on an OF-Scraper database
  `source()` returns None, `post_url()` None, `hashtags()` [], `tier()` None and
  `is_pinned()` False, so the identical code path serves both. It still detects
  the *older* OF-Scraper layout too (no `model_id`, date in `created_at`, empty
  `profiles`).
- **`patreon_source.py`** — patreon-dl's on-disk layout: the post/collection
  parsers, plus `PatreonLibrary`, an adapter that answers the same questions
  `SourceDatabase` does so `sync.py` never learns Patreon has no database.
- **`sources.py`** — a `SourceProfile` per site holds everything that genuinely
  differs, including `reader` (what opens one of that site's libraries) and
  `real_titles`. `profile_for_source()` picks one **per library** from its
  reported source. Detection is per-database, not a setting, so a
  data path holding both kinds of library sorts itself out. Adding a site means
  adding a profile, not branching through sync.py.

What differs per site, and why:

| | OnlyFans | JustFor.Fans |
|---|---|---|
| Post URL | rebuilt from the post id (`onlyfans.com/<id>/<user>`); declines for non-numeric ids (profile/avatar assets) | read from `jff_posts.post_url` — a JFF link carries an encoded key and **cannot** be rebuilt; synthetic `#post-<id>` fallback keeps URL-keyed galleries idempotent |
| Scene `code` | filename stem (OF-Scraper names files by media id, so the stem *is* the id) | the post id (jff-scraper names files `<date> - <post id> - <desc>`, so the stem would be junk) |
| Paid | `paid AND price > 0` | `jff_posts.tier == 'Paid'` — JFF exposes no price, so those columns are always 0 and the price rule would **never** fire |
| Studio / tag | `<user> (OnlyFans)` / `OnlyFans` | `<user> (JustForFans)` / `JustFor.Fans` |
| Extra tags | — | the post's hashtags (created if missing — deliberate metadata, unlike fuzzy text matches) and `pinned` |

Other multi-site notes:

- **Settings** — each site has its own data path (`dataPath`, `jffDataPath`) and
  parent studio; everything else is shared. A blank path means that site is not
  scanned, so an OnlyFans-only install behaves exactly as before.
- **Studios are keyed per (site, creator)** in `StudioResolver.cache`: the same
  username can exist on two sites and each gets its own studio under its own
  parent. The resolver also back-fills a site's logo onto a studio Stash reports
  as having no image (`image_path` containing `&default=true`, which Stash's
  `GetStudioImageURL` appends only then) — Stash accepts a studio image on
  *create* only, so it would otherwise stay blank forever.
- **Collaborator links** — `media.py` matches `onlyfans.com/<user>` *and*
  `justfor.fans/<user>` regardless of which site a post came from: a creator on
  one site linking a collaborator's profile on the other is still a real credit.
- **Galleries carry the post id in `code`** for every site, which is how the tag
  pass correlates a gallery back to its post (an OnlyFans URL embeds the id, a
  JustFor.Fans one does not; the URL parse remains a fallback).
- The scrapers' per-post `.json` sidecars are deliberately **not** read: the
  database already holds everything, and per-post file reads are what made an
  early version of the Patreon plugin time out.

The plugin also ships two UI-JS plugins (wired via the manifest `ui:` block):
`performerSync.js` (the per-performer "Sync Fan Sites" button) and
`titleExclusions.js` (a list editor for the `titleExclusions` setting). The
latter is surfaced via `register.route` plus a `patch.before("SettingsToolsSection")`
button (the CommunityScripts/AIOverhaul pattern), and persists the list with the
`configurePlugin` mutation (read back by `sync.py` from `configuration { plugins }`).
Values are stored as a JSON string so the manifest `titleExclusions` STRING field
stays a hand-editable fallback. **It deliberately does NOT reuse Stash's
`StringListSetting`/`StringListInput`**: those (and their `Setting`/`ModalSetting`
wrappers) call `useSettings()`, which throws `useSettings must be used within a
SettingsContext` on a standalone plugin route — so the editor uses its own
context-free rows editor (row-per-pattern + Save) instead.

## How the plugin runs

Stash invokes `sync.py` as a **raw external plugin** (`interface: raw` in the
manifest). At runtime:

- Stash writes a JSON payload to **stdin**: `server_connection` (scheme, port,
  session cookie) and `args` (the task's `defaultArgs`, e.g. `{"mode": "sync"}`).
- The plugin reads settings via the GraphQL `configuration { plugins }` query
  using the **session cookie** from stdin — no API key.
- Progress and messages are written to **stderr** with the SOH/level/STX prefix
  scheme (`log.py`) so they appear in the Stash log viewer and task bar.
- The final result is printed to **stdout** as JSON: `{"output": "ok"}` on
  success, or `{"error": "..."}` on a fatal failure. Stash logs the `error` at
  error level — but the **job still ends as FINISHED, not FAILED** (verified
  against v0.31.1: the task function logs the error and returns nil). So the
  log is the only place a user sees it; make the message actionable.
  `main()` returns the error string; keep that contract when adding new
  fatal-exit paths.

The manifest's tasks (17 of them, over 18 settings) are selected by `args.mode`,
crossed with the optional `args.site` scope and `args.dryRun` — which is why
there are far more tasks than modes. Each
covers **every configured site** — the per-site data paths are all scanned in one
run, so there is no per-site task:

- `mode: sync` — only unorganized scenes/images. Also groups each post's
  media into a gallery (2+ images, or an image + a video), linking the post's
  scene to the gallery (Stash relates scenes to galleries, not images). Galleries
  are keyed by post URL for idempotency; a plain sync only creates missing ones
  and adds images.
- `mode: full` — re-sync everything, ignoring the `organized` flag. Also refreshes
  the per-post galleries' metadata.
- `mode: tag` — additive only; adds tags from post text, never touches other
  fields. Safe over manually edited media.
- `args.site` — an **orthogonal** scope, not a mode: `{mode: sync, site: <slug>}`
  backs the per-site tasks. Applied twice on purpose — once when building the
  configured source list, and again per library against the site the *library*
  reports — because both scrapers name their file `user_data.db` and two sites
  can share a parent directory, so path filtering alone would let "Sync OnlyFans
  Only" sync a JustFor.Fans library. An unknown slug is fatal rather than a
  silent fallback to every site.
- `args.dryRun` — set by "Preview (Dry Run)", and also a setting. Enforced in
  **`StashClient.call()`**, the one chokepoint every request passes through,
  rather than per mutation method: a per-method check is one someone forgets to
  add with the next mutation, and the failure mode is writing to a library the
  user was told wouldn't be touched. Reads still run, so a preview reflects the
  real library; mutations return `{<field>: {"id": DRY_RUN_ID}}` so `create_*`
  callers carry on instead of erroring through the whole run.
- `mode: crew` — surgical credits pass ("Update Crew & Sponsors"). For all synced
  media it moves crew-tagged people out of the performers list and into the scene
  `director` / image `photographer` field, and drops sponsor-tagged accounts in
  favour of the `sponsored` tag. Every other field is left untouched. Skips media
  that already match, and never creates performers (even with auto-create on).
- `mode: performer` — a full re-sync scoped to one Stash performer. Requires a
  `performerId` arg (does nothing without it, so it never falls back to syncing
  everyone). It resolves that performer's name + `alias_list` and only processes
  the `user_data.db` profile whose site username matches, on any site — bridging
  the usual display-name≠username gap. Triggered by the **"Sync Fan Sites" button** that the
  plugin's UI JavaScript (`performerSync.js`, wired via the manifest `ui:` block)
  injects on each performer page; the button calls the `runPluginTask` mutation
  with `{mode: performer, performerId}`.

## Architecture notes

- **`StashClient` (stash.py)** — every GraphQL query/mutation. Fields were
  verified against the **Stash v0.31.1** schema. It connects to
  `localhost:<port>` (the plugin always runs on the same host as Stash).
  **Auth:** Stash hands the plugin a *session cookie* on stdin, but that cookie
  has a limited lifetime — a big Full Sync (thousands of items) can outlive it
  and then get `HTTP 401` on every remaining request, failing whole creators
  near the end. So `main()` calls `use_api_key()` up front: it reads
  `configuration { general { apiKey } }` with the still-valid cookie and, if a
  key exists, sends it as the `ApiKey` header on all later requests (API keys
  don't expire). Falls back to the cookie (with a warning) when no key is set.
- **`SourceDatabase` (source_database.py)** — opens `user_data.db` files
  **read-only** (`mode=ro` URI) so a concurrently running scraper never causes a
  write or "readonly database" error. Schema verified against **OF-Scraper
  3.14.7** and jff-scraper. Post text is searched across the `posts`, `stories`,
  `messages`, `others`, and `products` tables. It **detects the schema at open
  time** (`_detect_schema`) to also support older OF-Scraper databases, which
  lack `medias.model_id`, store the date in `created_at` instead of `posted_at`,
  and leave `profiles` empty (the creator name is then recovered from
  `medias.directory`). jff-scraper's extra tables are read through optional
  accessors that degrade to empty on a database without them — see *Multi-site
  architecture* above.
- **`MediaProcessor` (media.py)** — turns post text into title/details, parses
  collaborator credits, derives studio code from filename, formats dates.
  `parse_mentions` picks up both `@mentions` **and** bare profile links
  (`onlyfans.com/<username>`, `justfor.fans/<username>`), because creators
  sometimes credit a collaborator by URL instead of an @mention; the post-id URL
  form `onlyfans.com/<postid>/<username>` is excluded by skipping purely-numeric
  first segments. `_MENTION_RE`'s boundaries are **deny-lists, and must stay
  that way**: the character before `@` may be anything that can't end an email
  local part (not `[\w.\-]`, which is the sole reason `fan@example.com` isn't a
  credit), and the name just can't be cut off mid-word. They were allow-lists
  (whitespace/`>` before, a short punctuation set after) and silently dropped
  every credit written in an unanticipated way — `🚨@name`, `(@name)`, `@name😈`
  — which is a *silent* miss, not an error, so don't reintroduce one. It returns `(username, domain)` pairs rather than bare names:
  a post can link a collaborator on **another** site (a JFF creator plugging
  their OnlyFans), and a performer created from that credit must get the URL of
  the site that was linked, not the site being synced. `sources.profile_for_domain`
  maps the domain to a profile and returns `None` for a bare @mention, which
  makes `PerformerResolver.resolve(..., source=None)` fall back to the database's
  own site. The username is returned **as written**, because a performer created
  from that credit is *named* with it — lowercasing would leave `@BrandCo` in
  Stash as `brandco` forever. Dedup stays case-insensitive, and when one credit
  is spelled several ways a spelling carrying capitals beats an all-lowercase
  one (either form can be the careful one); capitals are kept, never invented.
  Matching in `PerformerResolver` is case-insensitive throughout, so casing only
  decides the name of a *new* performer and never splits a credit off an
  existing one. `process_text` also applies the **Title Exclusions** list
  (`titleExclusions` setting, parsed by `parse_title_exclusions`): each entry is
  a case-insensitive regex removed from the **title only** — details/description
  keeps the original post text verbatim. Stripping happens after the details
  decision so the description is untouched, leftover edge separators are tidied,
  and a title is never left empty (falls back to the original). Applies to
  scenes, images and galleries via this one chokepoint. Tag matching
  (`compile_name_pattern`) mirrors Stash's own auto-tagger
  (separator-insensitive, word-bounded, case-insensitive).
- **sync.py resolvers** — `PerformerResolver`, `StudioResolver`, `TagResolver`,
  `TagTextMatcher` each cache lookups and create-if-missing where appropriate.
  `PerformerResolver`, `TagResolver` and `StudioResolver` all build an in-memory
  index keyed by lowercase **name *and* alias**, so a plugin tag such as
  `archived`/`paid`, or a per-creator studio `<username> (OnlyFans)`, resolves to
  an existing tag/studio that carries the name as an alias instead of hitting
  Stash's "name already exists" error on create (Stash enforces uniqueness across
  names and aliases for tags, performers and studios). `find_studio` (used for
  the parent studio) is alias-aware for the same reason. Updates are routed by where the media
  actually lives in Stash (scene -> `sceneUpdate`, image -> `imageUpdate`).
- **Performance** — resolvers bulk-fetch **all** performers/studios once into
  in-memory maps (`find_all_performers`/`find_all_studios`), so username->performer
  resolution is a dict hit, not two queries per name (a live query only for the
  rare auto-create near-match). Each creator's scenes/images are fetched **once**
  (organized included; plain sync filters organized in code) and reused for the
  gallery pass. Writes (scene/image/gallery mutations) run through a thread pool
  (`run_writes`, `Sync Workers` setting, default 2, 1 = sequential) with
  retry-on-transient; **all resolution happens sequentially first**, so only
  stateless mutations run concurrently (no shared-cache races). Note SQLite has
  a **single writer**: parallel writes serialise on the DB write lock rather than
  truly committing at once, so high worker counts don't speed writes up — they
  pile up transactions until requests time out and starve the rest of Stash.
  Hence the low default and the strong "lower it if you see errors" guidance.
  `_run_write_task` retries transient contention (`lock` / `foreign key` /
  `constraint` / `busy` / `timed out` / `cancelled`) up to 5× with capped
  exponential back-off; a socket read timeout is normalised to a `timed out`
  RuntimeError in `stash.py` (`REQUEST_TIMEOUT`, 300s) so it's caught by that
  retry instead of slipping past as a bare `TimeoutError`.
- **Folder galleries** (`sync_folder_galleries`) — Stash creates a gallery for every scanned folder of images.
  Those are distinct from the per-post galleries the plugins build, and the
  schema makes them safe to tell apart: **a folder gallery has a `folder`, a
  plugin-made one does not** (`find_folder_galleries` filters on exactly that).
  Each of a creator's folder galleries gets the creator's studio, the creator
  performer, the site tag, and a title of
  `<username> <Site> <MediaDir> (<category>)` (the site label comes from the
  source profile) — the category being the path
  segments between the creator's directory and the media folder, without which
  every image folder of one creator (Posts/Free, Posts/Paid, Messages/Free,
  Archived/...) would collide on the same title. Deliberately **additive** for
  performers and tags: a folder is not a post, so there is no authoritative cast
  to replace with, and manual curation survives. Only title/studio are asserted,
  under the usual organized rule (plain sync skips organized, full refreshes
  all), and `build_folder_gallery_update` returns None when nothing would change
  so re-runs write nothing. The creator is matched by whole **path segment**, not
  substring, because Stash's `path` filter is a substring match and would
  otherwise let `jake` claim `/data/jakeson/...`. **A whole segment is still
  not confinement**: `/torrents/onlyfans/onlydurden/` passes it, and was
  adopted like the user's own folder. So `gallery_outside_data_path` also
  confines it to the site's data path — and the per-post and collection
  `by_url` lookups too, since a folder gallery anywhere can carry a stamped
  post URL. A plugin-made gallery has no path, so it is never "outside".
  `mode: cleanup` reverts the ones already adopted: a folder/zip gallery
  outside every data path counts as ours only if it carries the exact
  `folder_gallery_title` (built from its own path and studio, not something a
  person types) or a post URL on the site's domain (`plugin_adopted_gallery`).
  The revert follows the evidence (`build_gallery_cleanup_update`): a title
  fingerprint undoes only the five fields `sync_folder_galleries` sets, so the
  gallery's own details/date survive; a URL fingerprint reverts like media.
  A gallery photographer is never cleared — the plugin never writes one.
- **Media queries are confined to the site's data path**
  (`media_under_data_path`). Stash's `path` filter is a plain **substring**
  match over the **whole library**, and `find_scenes`/`find_images` pass only
  the creator's username -- so a Patreon creator with the vanity `Mirenac`
  matched `/torrents/downloads/whisparr/[Mirenac] <title>/1.png`, a file with no
  connection to the library. A post whose own image was also named `1.png` then
  claimed it, and the plugin wrote that post's title, URL, date, studio and
  `organized: True` onto somebody's torrent download. `source.data_path` is
  filled in per run from the same setting used to find libraries, and the
  substring result is filtered to paths under it (normalised, `+ os.sep` so
  `/data/patreon` can't swallow `/data/patreon-backup`; a multi-file item is
  kept if ANY file is inside). Note the whole-path-SEGMENT rule
  `sync_folder_galleries` uses does **not** work here: it would reject
  Patreon's own `<vanity> - <Name>` folders, and the torrent path has `Mirenac`
  inside a segment rather than as one.
- **Gallery membership: match on path, and only guess at an unambiguous name.**
  `group_media_by_post` indexes Stash media by **normalised path**, with a
  basename index as fallback. The fallback is used **only when the name is
  unambiguous on both sides** — one post in the source, one media in Stash —
  because a shared basename produces a confident *wrong* answer: several
  Patreon post folders holding a same-named file each claimed the same Stash
  image, and since `addGalleryImages` only ever adds and nothing re-derives a
  gallery's contents, every wrong claim stuck. One image was observed sitting in
  four unrelated comics' galleries. The fallback is kept rather than deleted
  because dropping it turns a *data path that doesn't match Stash's library
  path* into a silent total no-match; that case now logs an explicit warning
  naming the likely misconfiguration. `remember()` marks a duplicate basename by
  storing `None`, so ambiguity is recorded rather than resolved first-wins.
- **`reconcile_post_gallery` is the only thing that removes images from a
  gallery**, and it runs on a **full sync only**. It detaches an image from a
  post gallery solely when the same run can name the *other* post that owns it
  (`owner_of`, built from this run's grouping). Media the plugin cannot
  attribute — added by hand, or by another plugin — is never touched, which is
  what makes an otherwise destructive pass safe to run unattended: it can only
  undo a claim the plugin itself made. Folder galleries are excluded (Stash
  rejects the mutation, same as `addGalleryImages`).
  **The gap that leaves, and what fills it:** `reconcile_post_gallery` cannot
  detach a file that belongs to *no* post — there is no other owner to name —
  so a stray the substring-path bug pulled in stayed a member of the gallery
  that wrongly claimed it even after `mode: cleanup` stripped its metadata (one
  torrented image was observed in four unrelated comics' galleries). So
  `build_cleanup_update` also sends `gallery_ids`, dropping the plugin's own
  galleries from a reverted stray. `plugin_made_gallery` gates it on **two**
  tests: no `folder` (Stash owns a folder gallery's contents, and an earlier
  sync may well have stamped the post URL onto one) **and** a URL on the site's
  own domain (a hand-made gallery has no folder either). The field is omitted
  entirely when nothing of ours is among them, so the write cannot disturb a
  membership it isn't responsible for. Both `ImageUpdateInput` and
  `SceneUpdateInput` take `gallery_ids`, so it rides along on the same mutation
  — no second request, no extra chance to half-apply.
- **`ProtectedTags` — another plugin's tags are not ours to delete.** A
  sync/full pass replaces `tag_ids`, and `keepManualEdits` (off by default)
  protects the *user's* manual tags, which is a different question from another
  plugin's bookkeeping. `comic-reader` marks comics with a tag tree (parent
  `Comic`, children `Webtoon` on galleries and `Comic Page` on their images) and
  keeps the parent's id in its own settings, so a Full Sync over shared media
  was silently deleting the record of what a gallery *is*. `ProtectedTags` reads
  `comicTagId` from `configuration { plugins }` -> `comic-reader` once per run
  and expands it with `findTags(parents: {value: [id], modifier: INCLUDES,
  depth: -1})` — **`depth: -1` means all levels**; any fixed number silently
  truncates a deeper tree. Those ids are then re-added at the three replacing
  sites (`build_update`, the post-gallery merge, `sync_collection_galleries` —
  the last builds `tag_ids` from the site tag alone, so it would drop them
  outright) **regardless of `keepManualEdits`**. Discovery is by id, never by
  name, so renaming the tags can't break it; an absent or unconfigured
  comic-reader yields an empty set and every path behaves exactly as before. A
  failed descendant lookup keeps the parent rather than protecting nothing. The
  `tag` and `crew` passes only ever add tags, so they are deliberately not
  wired — `tests/test_protected_tags.py` asserts that too, so "wire it
  everywhere" doesn't get done reflexively.
- **Non-destructive sync** — by default a sync/full pass *replaces* a media's
  `performer_ids` and `tag_ids` with the post's derived values, so a Full Sync
  drops manually-added performers/tags. The **Keep Manual Performers & Tags**
  setting (`keepManualEdits`) makes `build_update`/`build_post_galleries` *merge*
  both instead: existing performers and tags are kept and the post's ones added
  alongside; crew ids are still removed on scenes/images so the crew feature keeps
  working. Everything else (title, details, date, studio, url) is still updated.
- **Crew credit** — a performer carrying the configured *Crew Tag* (matched by
  **tag id**, not name, so renames don't break it) is treated as crew:
  `collect_crew` credits them in the scene
  `director` / image `photographer` field instead of the performers list, for
  both the creator and any collaborator credited by @mention or profile URL. The creator is still added
  as a performer when a post credits only crew (so media is never
  performer-less), and the **studio always follows the source db**, never the
  credited director. `build_update` applies this during sync; `build_crew_only_update`
  applies just this part for the `crew` task. **Galleries are the exception**:
  `_gallery_meta` keeps crew (and every @mention) as **linked performers** on the
  post's gallery and leaves the gallery photographer empty, because Stash's
  director/photographer fields don't link back to a performer — so a crew
  member's work is only browsable via the gallery's performer link.
- **Sponsors** — the same shape as crew, driven by a second tag id
  (`sponsorTagId`), but with nowhere to credit them: a Sponsor-tagged account is
  **removed from `performer_ids`** and the media gets the `SPONSORED_TAG`
  (`sponsored`, resolved through `TagResolver` so an existing tag carrying it as
  a *name or alias* is reused). `collect_crew` returns `sponsor_ids` alongside
  `crew_ids` and keeps sponsors out of `mention_performer_ids`; `build_update`
  and `build_crew_only_update` prune them and add the tag. **Galleries drop
  sponsors too** — the exception to the crew exception above, because the reason
  crew stay (their credit field loses the performer link) doesn't apply when
  there is no credit field at all. `_gallery_meta` therefore returns
  `sponsor_ids` as a third value so the `keepManualEdits` merge can prune a
  sponsor an older sync left behind; `keepManualEdits` deliberately does **not**
  protect sponsors, or a brand added before the feature existed could never be
  cleaned out. A performer tagged both crew and sponsor gets both treatments,
  which is why `PerformerResolver.resolve` inspects every match instead of
  stopping at the first crew hit. The tag is only ever **added**, never removed.
  Note `is_sponsor()` reads the cache `resolve()` fills, so it must be called
  *after* `resolve()` for that username.
- **Content houses** — a third tag id (`contentHouseTagId`, setting *Content
  House Tag ID*) for accounts that are credited like people but are a
  production house, filming location or site. Same drop as sponsors (scenes,
  images **and** galleries; `keepManualEdits` doesn't protect them), but the
  media gets a tag **named after the house** rather than one shared tag, so
  its content stays filterable and the user can nest those tags under their
  own parent. `collect_content_houses` is separate from `collect_crew` so the
  latter's 5-tuple (used across the tests) is unchanged; `collect_crew` only
  keeps houses out of `mention_performer_ids`. `TagResolver.resolve_any`
  reuses an existing tag matching the @name **or** the performer's display
  name, by name or alias, before creating one under the @name as written.
  Credited accounts only — the creator is already the studio. Wired into the
  `crew` pass too, still surgical: performers pruned, tags only added.
  The setting deliberately avoids the word *location*: the user already has a
  Location tag group, and the marker tag is matched by id, so it can be
  named anything.

### Patreon

Patreon is the one source with **no database**: patreon-dl writes each post's
metadata beside its media. `PatreonLibrary` (patreon_source.py) parses the tree
on open and answers `SourceDatabase`'s questions, so the whole of sync.py stays
site-agnostic rather than growing a second pipeline for a source that differs
only in where metadata lives. A "library" is therefore a *creator folder* here,
not a file — which is why `SourceProfile.reader` exists and the discovery loop
asks the profile what to open.

- **Creators are identified by content** (a folder containing `posts/` or
  `collections/`), because patreon-dl leaves `logs/`, `.patreon-dl/` and a
  `patreon-dl.conf.bak` beside the real ones and a name-shaped heuristic would
  make a performer out of `logs`. It also handles a bare-vanity folder with no
  ` - Name` suffix.
- **`EXCLUDED_DIRS` is load-bearing, not cosmetic.** `post_info/`,
  `.thumbnails/` and `image_previews/` hold real image files
  (`cover-image.jpg`, `thumbnail.jpg`, a byte-identical duplicate of the cover).
  A real library's download log counts ~1130 `post_info` and ~1080
  `.thumbnails` jpgs against ~1072 genuine ones — and since the post id is
  parsed from the *path* and the post folder is their ancestor, every one would
  otherwise match its post and take its metadata and `organized` flag. Don't
  "simplify" this away.
- **`embed/` is NOT in that list, and must not be added back.** It looks
  auxiliary and was excluded on the assumption that it held only `.txt`
  descriptors of embedded media; it does not. patreon-dl downloads embedded
  video into it, so excluding it silently dropped whole scenes — a post whose
  only video is `<post>/embed/<title>.mp4` synced nothing at all and merely
  incremented `Skipped`. The three folders above hold duplicates of media that
  exists elsewhere; `embed/` holds media that exists nowhere else, which is the
  whole distinction. Its `.txt` descriptors get indexed too, harmlessly: Stash
  never ingests one, so it can never match a scene or image.
- **Patreon basenames are not unique; match on the path.** A creator who posts
  the same video at two tiers gets the identical filename in both post folders
  (`embed/TARZAN & MILO.mp4.mp4` under both `- 4k Diamond` and
  `- 1080p - Gold`). Keyed by basename that is two silent bugs: one file evicts
  the other from the index, so a Stash scene is never processed at all; and
  whichever post `os.walk` reached first supplies the metadata for both. So
  `PatreonLibrary` keys `_by_path` on the absolute path and exposes
  `media_by_path()`, `sync.py`'s `media_map` and `group_media_by_post`'s index
  are both keyed by path, and lookups go path-first with the basename as
  fallback. `SourceDatabase.media_by_path()` returns None by design — OF-Scraper
  and jff-scraper name files after the media id, so a basename already
  identifies a post there — and the fallback covers it, the same degrading
  -capability pattern as `hashtags()`/`tier()`. A multi-file scene yields one
  `media_map` entry per file, so `seen_media` keeps it from being written twice.
- **Skip Multi-file does not apply to Patreon**
  (`SourceProfile.merged_files_are_duplicates`, True only there). The setting
  exists for OnlyFans, where a merged scene is several *different* files from
  different pages: no single post is the right source and overwriting is the
  greater harm. patreon-dl gives the opposite case — the same image posted at
  several tiers downloads byte-identical into each post folder and Stash merges
  them on hash — so skipping would skip ordinary images and the merge protects
  nothing. The flag is per-site rather than a setting because it states a fact
  about the site, not a preference; it defaults to False so a new profile opts
  in deliberately. `media_map` is iterated **sorted**, so such an item always
  takes the lowest-sorting post's metadata instead of flipping between posts
  across runs.
- **Video has no folder of its own** — `.mp4` sits in `images/`, `attachments/`
  and `embed/` next to the pictures (85 and 65 in that same log), alongside
  `.psd`/`.zip`/`.pdf` Stash won't ingest. The media index is therefore
  deliberately **not** an extension allow-list: indexing a file Stash never
  matches costs nothing, missing one it has loses the credit.
- **A folder gallery can turn up as a post's existing gallery.** Per-post and
  collection galleries are looked up through `find_galleries_for_studio`, keyed
  by URL — and a gallery Stash made from a scanned folder can carry both the
  creator's studio and the post URL, because an earlier sync stamped them on.
  Stash owns such a gallery's contents and rejects `addGalleryImages` on it
  outright, so both call sites check `existing.get("folder")` and update only
  its metadata. This is the opposite direction to `find_folder_galleries`,
  which filters *for* `folder`; the field is the discriminator either way, so
  the studio query fetches it.
- **Titles are authored**, hence `real_titles`: used as written, with the body
  kept whole as details. Title Exclusions still apply — `SourceProfile.title()`
  strips on **both** branches, so Patreon's authored titles and the other
  sites' derived ones behave alike. Note `real_titles` deliberately skips
  `maxTitleLength`: an authored title is a deliberate length, unlike one cut
  out of a wall of post text. Collection gallery titles are stripped too, at
  their own call site in `sync_collection_galleries` — they don't route through
  `SourceProfile.title()`, so adding a title path means checking it reaches
  there as well.
- **Post galleries are Stash's folder/zip galleries, not built ones**
  (`SourceProfile.post_folders`, True only for Patreon). patreon-dl gives every
  post its own folder, so the gallery Stash makes from it already IS the post's
  gallery; building another beside it (what `build_post_galleries` does for
  the scraper sites) put every multi-image post in Stash twice, and the built
  copy's membership was the plugin's to get wrong — which is how one image sat
  in four unrelated comics' galleries. `sync_post_folder_galleries` instead
  finds the creator's folder and zip galleries (`find_located_galleries`,
  confined to the creator folder because the path filter is a substring), maps
  each to its post with `PatreonLibrary.post_for_path` (walks up to the nearest
  post folder; None inside `EXCLUDED_DIRS`), and writes `_gallery_meta` onto it,
  including `scene_ids` — Stash validates only *image* membership on folder/zip
  galleries (`pkg/gallery/validation.go`), so scene links are allowed. It never
  creates a gallery or attaches an image. `sync_folder_galleries` is skipped
  for such sources, or its generic title would overwrite the post's. The plain
  sync uses the strict organized rule (skip any organized gallery) because
  these are the *user's* galleries and some were hand-curated before this
  pass existed. OF-Scraper/jff-scraper file media by type, not post, so they
  keep built galleries.
- **Images inside a zip are matched by post folder.** Stash gives a zip
  member the path `<zip>/<inner>` (`pkg/file/zip.go`, `filepath.Rel(zipPath,
  name)`), which no disk walk indexed. `media_for_path` / `post_for_path`
  place it in the post whose folder holds the zip — used **between** the exact
  path and the basename fallback in both `process_profile` and
  `group_media_by_post`, because a zip's `01.png` is exactly the kind of
  basename another post shares.
- **Collections → one flat gallery each** (`sync_collection_galleries`), holding
  member posts' images and linking their scenes. Stash has no nested galleries
  and no gallery→group link, so a gallery *of* galleries is impossible; Groups
  were rejected because they take scenes only and the library is ~1342 images to
  ~51 videos. Keyed by collection **URL**, not title, so a rename updates rather
  than duplicating. `collections()` joins `hashtags`/`tier`/`is_pinned` as a
  capability that degrades to empty on the scraper-backed sources.

### Comic Reader

`plugins/comic-reader/` shares nothing with the sync plugins; its user docs are
its README. What matters when changing it:

- **Why `interface: js`.** Its work is hooks, and Stash runs hooks
  synchronously inside the triggering request — including during a scan, where
  `Image.Create.Post` fires once per new image. A `raw` hook would spawn Python
  per image; an embedded JS hook runs in-process, and `gql.Do()` calls Stash's
  GraphQL handler directly (no network, session cookie attached). The backend
  is **ES5** (`var`, `function`): goja's ES6 support varies by Stash version,
  and `tests/test_comic_reader.py` rejects `let`/`const`/arrows/template
  strings/classes in it.
- **`log.Progress` blocks forever outside a queued task** (it sends on a
  channel nothing reads for hooks and `runPluginOperation`). Only call it via
  `progress(args, …)`, which requires the `task: "true"` every task passes in
  its `defaultArgs`. The UI calls the backend through `runPluginOperation`
  (modes `tags`, `pages`), which never passes it.
- **The model is tags**, ids stored in the plugin's own settings
  (`comicTagId`, `webtoonTagId`, `comicPageTagId`; `ensureTags()` creates or
  reuses by name/alias and saves them). `Webtoon` and `Comic Page` are
  children of `Comic`, so "is a comic" = tagged Comic at `depth: -1`, and one
  hierarchical exclusion hides everything. `Comic Page` exists because the
  Images page's filter can't see an image's galleries.
- **Hooks must stay cheap** — `Gallery.Update.Post` fires for every gallery
  the sync plugin rewrites, `Image.Update.Post` for every image anything
  rewrites (this plugin's own Comic Page writes included). `Image.Update.Post`
  returns before any query unless `inputFields` has `gallery_ids`;
  `configuredTags()` makes every hook a no-op until set-up has run; a non-comic
  gallery update without `tag_ids` returns before any image query.
- **`addGalleryImages` / `removeGalleryImages` fire no hooks** (Stash's own
  gallery Add tab and "Remove from gallery" use them), so opening a comic in
  the reader or its gallery page runs the per-gallery `pages` operation, and
  *Set Up Comics* repairs everything.
- **User choices must survive a repair.** A `.cbz` that loses its comic tags
  through an edit that lists `tag_ids` gets a `not_comic` custom field (the
  scan hook and `tagCbzGalleries` skip it; re-marking clears it) -- only on an
  explicit tag edit, never a scan-time update with no field list. Hide/Show
  write the `showComicsInLists` setting, which *Set Up* follows.
- **Never trust `NOT` over a `galleries_filter`.** Stash evaluates it per
  joined gallery row, not per image, so an image in one comic and one
  non-comic gallery matches both `galleries_filter: comic` and
  `NOT: {galleries_filter: comic}`. Trusting it stripped `Comic Page` off real
  comic pages. `removablePages()` uses it only as a candidate list (plus
  `galleries: IS_NULL` for pages in no gallery) and checks each candidate's
  galleries in code. The test pins this.
- **Hiding = default filters, per view.** Stash keys default filters by view
  (`configuration.ui.defaultFilters.<view>`), and performer/studio tabs are
  their own views (`performer_galleries`, `studio_images`, …), so hiding sets
  six of them. Tag views are left alone on purpose (the Comic tag's own page
  would be empty). The saved shape is what the UI writes:
  `tags: {modifier: INCLUDES, value: {items, excluded: [{id,label}], depth}}` —
  there is no EXCLUDES modifier for tags. `hideInFilter`/`showInFilter` merge
  into a user's existing default filter and must keep everything else --
  including `depth`, which applies to exclusions too: only a criterion the
  plugin creates gets `depth: -1`; the family is listed id by id instead.
- **Reading order defaults to the natural file path** (`CR.sortPages`), not
  the date: 0.5 and earlier sorted by date first, and every page of a comic
  shared in one Patreon post has the same date (and the same synced title),
  so the date decided nothing and undated pages jumped to the front.
  patreon-dl numbers a post's files in post order and post folders start
  with the post id, so path order is release order across posts too. Per
  comic, `comic_order` (`date` / `custom`) and `comic_page_order` (image ids
  comma-separated -- Stash custom fields reject lists) override it; the
  custom list survives switching modes, and pages missing from it follow in
  path order. In `date` mode an undated page goes last. The reader sorts
  `CR.fetchPages`' path-ordered pages itself, and keeps the page on screen
  when the order changes. The overview (`ui/overview.js`) labels pages by
  file name plus `CR.pageFolder` (skipping generic `images/` etc.) when a
  comic spans folders, since every post has a `1.jpg`.
- **Share patched components politely -- 0.1.0 blanked Stash for Stash TV
  users.** Stash's patch chain (`RB` in the bundle) *replaces* the argument
  list with whatever a `before` returns, then calls each `instead` with those
  arguments plus `next`. React calls a component with two arguments, and
  Stash TV's `MainNavBar.MenuItems` patch takes `next` positionally as the
  third -- so a `before` returning just `[props]` handed it `undefined`
  (React #130, blank app). Every patch here goes through `CR.keepArgs`, which
  returns all arguments and leaves them untouched if the transform throws;
  children stay an array (`React.Children.toArray`); and everything rendered
  inside Stash's tree sits in `CR.Boundary`, an error boundary, so a failure
  hides only our piece. `tests/test_comic_reader.py` runs the chain against a
  Stash TV-shaped patch.
- **The nav item follows the Stash TV / Binge pattern:** a `before` on
  `CheckboxGroup` (group `menu-items`) adds a "Comics" row to Settings >
  Interface > Menu items, the item renders only when `interface.menuItems`
  holds `comics` (or has no list at all), and `seedMenuItem()` adds it to the
  list once, recording `navSeeded` in the plugin's settings so a user who
  unticks it stays unticked.
- **Claim singletons in an effect, never during render.** React can render a
  component and discard that render while lazy chunks load (a browser's
  first load); the injector used to claim "I am the one" during render, the
  discarded render kept the claim, and the gallery buttons / Comics tab never
  appeared. The claim now happens in `useEffect`, which only committed
  instances run.
- **Don't restyle another component's state, change it.** The stock tab that
  was active when Comics opens has its `active` class removed and restored
  afterwards; a CSS override lost to themes (refract) and left two tabs lit.
- **UI injection.** Performer/studio tabs and the gallery page are not
  patchable components in v0.31, so `ui/inject.js` finds the rendered
  elements and uses `ReactDOM.createPortal` (keeping Stash's router/context).
  The injector lives in the `MainNavBar.MenuItems` patch — **not**
  `MainNavBar.UtilityItems`, which Stash renders twice (desktop + mobile) and
  duplicated every injected tab. Stash redirects unknown tab URLs
  (`/performers/1/comics` → `.../galleries`), so the Comics tab switches
  in-page. `register.route` is react-router v5 (`<Route path component>`,
  prefix match), so one route dispatches `/plugins/comics`, `/read/:id` and
  `/new`.
- **Interplay with of-stash-sync:** its Full Sync keeps every tag under
  comic-reader's `comicTagId` (see `ProtectedTags` above), so the settings
  key names `comicTagId` / `webtoonTagId` / `comicPageTagId` are a contract
  between the two plugins -- don't rename them. `seriesTagId` (Comic
  Series) sits under Comic, so series ride along without of-stash-sync
  knowing about them.
- **The Comics list is Stash's `FilteredGalleryList`, not a copy of it.**
  The user asked for exactly Stash's listing (search, saved filters and *Set
  as default*, filter dialog and chips, sort, per page, operations, zoom,
  pagination), so `CR.ComicsList` renders the real one and changes three
  things: `filterHook` wraps the list's copy of the filter so `makeFilter()`
  returns `{...user's filter, AND: comic restriction}` (the restriction never
  appears in the UI or gets saved into a filter; an existing `AND` is nested,
  not replaced); `view` is `comics` / `performer_comics` / `studio_comics`, so
  the Comics page gets its own default filter instead of the Galleries one
  (which hides comics); and `extraOperations` adds our items to the `…` menu.
  Tabs pass `alterQuery: false` so they don't rewrite the performer page's URL.
- **Comic cards replace the grid only inside a Comics list.** The
  `patch.instead("GalleryCardGrid")` in main.js reads `CR.ComicsContext`
  (provided by `ComicsList`) and otherwise returns `next` with exactly the
  arguments it got. `useContext` runs on every render so the hook order never
  changes, and the error fallback renders Stash's grid lazily as a component --
  calling `next` up front would run its hooks inside ours. The test pins both.
- **The reader's details panel (`ui/info.js`)** is the gallery page's
  details panel reshaped for comics: links lead to more comics (a performer
  or studio opens its Comics tab via `CR.returnToTab`, a tag opens
  `/plugins/comics?c=<criterion>`), and editing stays on the gallery page.
  `CR.criterionParam` must encode exactly as Stash's `getEncodedParams` does
  (braces outside strings become parentheses); the test decodes it with a
  copy of Stash's own `translateJSON`. `RatingSystem` arrives with the
  `Galleries` chunk, so the panel loads that, along with Stash's `Date`.
  Performers use the exact `performer-tag-container` markup of Stash's
  scene list; links are plain buttons because the gallery page shows none
  and `ExternalLinksButton` portals its menu behind the reader. Two theme lessons: the panel is
  an `aside` that owns position, with a `.card` inside for the surface only
  (refract's `.card` sets `position: relative` and full-width images, which
  wrecked a positioned card), and a dark scrim sits behind that card because
  a theme's card can be translucent. `CR.returnToTab` is consumed when the
  Comics tab has actually *shown*, not on mount: Stash redirects
  `/performers/1` to its default tab and the tab can mount twice on the way.
- **Reading progress is synced on the gallery** (custom fields
  `comic_page` 1-based, `comic_finished`, `comic_read_at`, `comic_seen`),
  written by `CR.saveProgress` 1.5 s after the page settles, at once on
  reaching the end, and flushed when the reader closes or the tab hides;
  just opening a comic writes nothing. Stash stores a boolean custom field
  as `1`, hence `progressOf` accepting 1/"true". Stash's list data
  (`SlimGalleryData`) has no custom fields, so card grids fetch them once per
  page (`CR.useProgress`). Every save fires `Gallery.Update.Post`, so the
  hook returns before any query for a gallery edit that lists fields without
  `tag_ids` -- a gallery update can't change which images it holds. The old
  per-browser `page:<id>` localStorage note is read only for a comic with no
  synced progress at all, and zeroed on the first sync.
- **Series are tags under `Comic Series`** (`seriesTagId`, a child of Comic,
  so the whole tree stays inside of-stash-sync's `ProtectedTags`). Order is
  date, then natural title (`CR.seriesOrder`), undated last. Joining a series
  also adds `Comic`: a hide filter merged into a user's existing criterion
  lists the family id by id, so a series tag created later isn't in it.
  `CR.ensureSeries` reuses a same-named tag (name or alias, any case) and
  puts it under Comic Series, since Stash won't allow a second tag of that
  name. The **Series tab is a plain grid, not Stash's `FilteredTagList`**:
  that component hard-codes its view to `tags`, so its *Set as default* would
  rewrite the Tags page's default. A series' page is `CR.ComicsList` with
  view `series_comics`, whose default filter is seeded once to date ASC --
  followed by a refetch of Stash's cached settings, because the list reads
  default filters from that cache and would otherwise start by path. The
  reader's window key handler ignores keys while a Bootstrap modal is open
  (`body.modal-open`): react-bootstrap's Esc closes the dialog, and the same
  event used to close the reader behind it too.
- **O counts go on the page, not the comic**: Stash has O counters on
  images and scenes only (`imageIncrementO`/`DecrementO`/`ResetO`), none on
  galleries. The reader's button copies Stash's `OCounterButton` markup (the
  image viewer's `.o-counter` button group; the component itself isn't
  exposed) with the exposed `SweatDrops` icon, and follows
  `interface.sfwContentMode`. A two-page spread gets a menu instead of a
  guess. The end card's rating and the details panel's share one state in
  the Reader, so rating in one shows in the other.
- **Continue reading's x** sets `comic_hide_continue`; `saveProgress` always
  removes it (removing an absent custom field is a no-op in Stash), so
  reading a hidden comic again brings it back, and the page is never lost.
- **Look and theming: build from Stash's own parts.** Cards are
  `PluginApi.components.GridCard` with the `gallery-card` class names and
  Stash's own `GalleryCard.Overlays/Details/Popovers`, sized like Stash's
  (`CR.cardWidth` is its `(w-30)/ceil((w-30)/preferred)-10`). Themes size a
  gallery card's image for landscape (refract forces the header to 4:3 and
  clips it), so the portrait-cover rules are one class more specific than a
  theme's `body.x .gallery-card .gallery-card-header` -- the one place
  comics.css deliberately out-ranks a theme. The builder's toolbar reuses
  `.filtered-list-toolbar` markup and the `zoom-slider`;
  inputs use `clearable-text-field form-control` / `btn-secondary form-control`;
  messages go through `PluginApi.hooks.useToast`; the spinner is
  `LoadingIndicator`; the reader's root carries `.Lightbox`. comics.css is
  layout only -- any colour it needs is `var(--primary)`. GridCard lives in a
  lazily loaded chunk, so `CR.useCardComponents()` loads
  `loadableComponents.TagLink` before a grid renders; a plugin route doesn't
  load it on its own.

## Hard constraints — keep these intact

- **Standard library only.** No third-party Python deps; the plugin runs inside
  the Stash container with nothing installed. The same goes for comic-reader's
  JavaScript: no bundler, no npm packages — UI code uses only what
  `window.PluginApi` provides. (This is why `media.py` has its own
  emoji regex instead of the `emojis` package.) Don't add `requirements.txt` or
  imports outside the stdlib.
- **Databases are opened read-only.** Never change `SourceDatabase` to open for
  write.
- **GraphQL fields must match the Stash schema** (currently v0.31.x). Verify any
  new field/query against the running Stash version before relying on it.
  Note the asymmetry: `director` exists only on scenes, `photographer` only on
  images — credit each on the media type that has it.
- **The tag-only and crew/sponsor paths are surgical.** `build_tag_only_update`
  only ever adds tags; `build_crew_only_update` only ever touches
  `performer_ids`, the `director`/`photographer` field, and `tag_ids` — the last
  one **additively and only to add `sponsored`** — and returns `(None, None)`
  when nothing changes. Both leave all other fields untouched (Stash only
  mutates fields you send), so manual edits survive. Keep them that way.
- Updates set `organized: True` so the normal `sync` pass skips them next time —
  don't drop this from the regular sync update. (The `tag` and `crew` passes do
  not set it, so they don't disturb the sync/organized workflow.)

## Building / distributing

Publishing is **automatic via GitHub Actions** — there is no manual step:

- `.github/workflows/deploy.yml` triggers on a push to `main` that touches
  `plugins/**` (or via the Actions tab → *Run workflow*). It runs
  `build_site.sh _site/main`, uploads `_site` as a Pages artifact, and deploys it.
- GitHub Pages is configured with **Source: GitHub Actions** (Settings → Pages).
  Changing it to "Deploy from a branch" breaks this flow — leave it on Actions.
- `build_site.sh [outdir]` finds every `plugins/**/*.yml`, zips that plugin's
  directory into `<plugin_id>.zip`, and writes `<outdir>/index.yml` with the
  plugin's `id`, `name`, `metadata.description`, `version` (`<yml version>-<git
  short hash>`), `date`, `path`, and `sha256`. A `# requires: a, b` line in a
  manifest becomes the index `requires` list. `plugin_id` is the manifest's
  filename without `.yml`.

To ship a change: edit files under `plugins/<id>/`, commit, and push to `main`.
The Action republishes within a minute or two; then **Check for Updates** on the
source in Stash. `_site/`, `__pycache__/`, and `*.pyc` are gitignored.

## Adding a new plugin

Create `plugins/<id>/<id>.yml` (manifest named after its directory) plus the
plugin's code in the same directory, then push. `build_site.sh` picks it up
automatically and the next deploy publishes it. Follow the existing
`interface: raw` + stdin-JSON / stderr-logging pattern unless the plugin type
calls for something else.

**Read `docs/stash-platform.md` first if the new plugin isn't another
`interface: raw` task plugin.** It covers what Stash actually offers — the three
plugin interfaces (`raw` / `rpc` / `js`), hooks (all `.Post`, and no scan hook
exists), what a scraper can and cannot do (and that a scraper isn't a plugin and
isn't packaged by `build_site.sh`), gallery chapters, and how to verify any of
it against the Stash source rather than from memory. Several of its entries are
UI-only limits that reading the GraphQL schema will not reveal.

Note `build_site.sh` globs `plugins/**/*.yml`, so **every** `.yml` under
`plugins/` becomes a published plugin — don't put fixtures or config there.

## Conventions

- Python: stdlib only, classes for clients/resolvers, `.format()` string
  formatting (as in existing code), docstrings explaining *why* (schema versions,
  edge cases) rather than restating the code.
- No linter is configured. There **are** tests: `python3 tests/run.py` (add a
  substring to filter, e.g. `python3 tests/run.py patreon`).
  `test_comic_reader.py` runs the plugin's JavaScript under Node when `node`
  is on PATH and skips that half otherwise. Stdlib only, one
  process per file, each asserting its way to `ALL OK`; nothing talks to a
  running Stash. They live at the repo root rather than beside the plugin
  because `build_site.sh` runs `zip -r` over a plugin's whole directory, so
  anything kept there ships to every user's install.
- Most of them encode a bug that actually shipped — `embed/` holding real
  media, the folder-gallery guard, path-not-basename matching, the dry-run
  sentinel needing to look like an id. Adding a case with a fix is how that
  reasoning survives; note that `test_patreon.py` once asserted the *wrong*
  behaviour and so confirmed a bug instead of catching it, because it was
  written from the same bad premise as the code.
- `tests/test_no_undefined_names.py` uses stdlib `symtable` to flag a name a
  function resolves globally that the module never defines. Nothing else can
  catch that: the plugin needs a live Stash to run, and `ast.parse` accepts an
  undefined name happily -- so a `NameError` on a rarely-taken line reaches a
  user. One did, on the last line of a cleanup run, after all its writes.
- Commit messages: short imperative subject lines (see `git log`).
