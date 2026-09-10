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

Currently there are three plugins:

- **`plugins/of-stash-sync/`** — OnlyFans Metadata Sync. Syncs metadata scraped by
  [OF-Scraper](https://github.com/datawhores/OF-Scraper) (read from its
  `user_data.db` sqlite files) into matching Stash scenes and images: title,
  details, date, URL, performers, studio, code, tags, and the `organized` flag.
  It is a native, dependency-free re-implementation of
  [`timekillerj/ofscraper-stash-sync`](https://github.com/timekillerj/ofscraper-stash-sync).
- **`plugins/patreon-stash-sync/`** — Patreon Metadata Sync. Syncs metadata for
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
- **`plugins/jff-stash-sync/`** — JustFor.Fans Metadata Sync. The JFF sibling of
  of-stash-sync, reading the `user_data.db` files written by **jff-scraper**.
  That schema's core is deliberately OF-Scraper-compatible (`profiles`/`medias`/
  `posts`, numeric `model_id`, `posted_at`), so the structure is shared; the
  platform differences all come from the scraper's extra `jff_posts` table and
  are listed under *jff-stash-sync specifics* below.

## Repository layout

```
build_site.sh                       Template build script: plugins/ -> _site/<branch>/{index.yml, <id>.zip}
.github/workflows/deploy.yml         Builds the source and deploys to GitHub Pages on push to plugins/**
plugins/
  of-stash-sync/
    of-stash-sync.yml                Plugin manifest (settings, tasks, exec entry point)
    sync.py                          Entry point + orchestration (run by Stash)
    stash.py                         Minimal Stash GraphQL client (stdlib only)
    of_database.py                   Read-only reader for OF-Scraper user_data.db
    media.py                         Post text -> title/details/tags/date processing
    log.py                           Stash log-viewer logging via stderr
    README.md                        User-facing docs (settings, tasks, install)
    onlyfans.png                     Studio icon
  patreon-stash-sync/
    patreon-stash-sync.yml           Plugin manifest (gallery/collection tasks)
    sync.py                          Orchestration: posts -> galleries + images, collections -> galleries
    patreon_source.py                Reads patreon-dl's on-disk info.txt/post-api.json/collection JSON
    stash.py                         Stash GraphQL client (adds gallery ops)
    media.py, log.py                 Copied from of-stash-sync
    README.md                        User-facing docs + pipeline diagram
    patreon.png                      Studio icon
  jff-stash-sync/
    jff-stash-sync.yml               Plugin manifest (same task modes as of-stash-sync)
    sync.py                          Orchestration (of-stash-sync's, with the JFF differences)
    jff_database.py                  Reader for jff-scraper user_data.db (+ jff_posts)
    stash.py, media.py, log.py       Copied from of-stash-sync (media.py: justfor.fans links)
    performerSync.js, titleExclusions.js  UI plugins (same pattern, JFF ids/labels)
    justforfans.png                  Studio icon (also back-filled onto existing studios)
    README.md                        User-facing docs
```

### jff-stash-sync specifics

Everything else matches of-stash-sync; these are the real differences, and each
exists because of something the platform or the scraper does:

- **Post URL** — a JFF link is `justfor.fans/<user>?Post=<encoded key>`, so unlike
  OnlyFans it **cannot be rebuilt from the post id**. `jff_post_url()` reads the
  real URL from `jff_posts.post_url`, falling back to a stable synthetic
  `justfor.fans/<user>#post-<id>` so per-post galleries (keyed by URL) stay
  idempotent.
- **Post id on galleries and media** — since the post id isn't in the URL, it is
  stamped into the **`code`** field of galleries *and* scenes/images, and the tag
  pass reads `gallery.code` to correlate a gallery back to its post (falling back
  to a lazily built url→post map). Note of-stash-sync instead derives a scene's
  `code` from the filename stem, which works there only because OF-Scraper names
  files by media id; jff-scraper names them `<date> - <post id> - <description>`.
  `code` is a valid field on `Gallery`/`GalleryCreateInput`/`GalleryUpdateInput`
  (verified against the schema).
- **Paid** — JFF has no per-post price, so `posts.paid`/`price` are always `0` and
  of-stash-sync's `paid AND price > 0` rule would never fire. The `jff_posts.tier`
  column (Free/Paid) drives the `paid` tag instead; the price rule remains only as
  a fallback for a database with no tier.
- **Hashtags / pinned** — `jff_posts.tags` (a JSON array) are synced as tags and
  **created if missing** (they are deliberate creator metadata, unlike the fuzzy
  text matches which only attach existing tags); `jff_posts.pinned` adds a
  `pinned` tag.
- **Collaborators** — `media.py`'s profile-link regex matches `justfor.fans/<user>`
  rather than `onlyfans.com/<user>`.
- **Which databases** — `user_data.db` is also OF-Scraper's filename, so the sync
  only processes databases whose `schema_flags` row says `source = jff` (checked
  via `JFFDatabase.source()`) and skips others with a warning.
- **Performance** — `jff_posts` is bulk-loaded once per creator into a dict
  (`_ensure_jff_index`), because each post needs several of its fields and a query
  per field would be four round-trips per post.
- **Studio logo back-fill** — Stash only accepts a studio image on *create*, so
  studios made before `justforfans.png` shipped would stay blank forever.
  `StudioResolver` therefore also sets the logo on an existing per-creator studio
  when Stash reports it as having none. The signal is `image_path` containing
  `&default=true`, which Stash's `GetStudioImageURL(hasImage)` appends only when
  the studio has no image of its own — so a studio with any image (including a
  hand-picked one) is never overwritten, and the update runs at most once per
  studio per run. of-stash-sync has the same create-only limitation and has not
  been given this treatment.
- The scraper's per-post `.json` sidecars are deliberately **not** read: the DB
  already holds everything, and per-post file reads are what made an early version
  of the Patreon plugin time out.

of-stash-sync also ships two UI-JS plugins (wired via the manifest `ui:` block):
`performerSync.js` (the per-performer "Sync OnlyFans" button) and
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
  success, or `{"error": "..."}` on a fatal failure (Stash logs the `error` at
  error level and marks the task failed). `main()` returns the error string;
  keep that contract when adding new fatal-exit paths.

The four tasks are defined in the manifest and selected by `args.mode`:

- `mode: sync` — only unorganized OnlyFans scenes/images. Also groups each post's
  media into a gallery (2+ images, or an image + a video), linking the post's
  scene to the gallery (Stash relates scenes to galleries, not images). Galleries
  are keyed by post URL for idempotency; a plain sync only creates missing ones
  and adds images.
- `mode: full` — re-sync everything, ignoring the `organized` flag. Also refreshes
  the per-post galleries' metadata.
- `mode: tag` — additive only; adds tags from post text, never touches other
  fields. Safe over manually edited media.
- `mode: crew` — surgical crew-credit pass. For all OnlyFans media it moves
  crew-tagged people out of the performers list and into the scene `director` /
  image `photographer` field, leaving every other field untouched. Skips media
  that already match, and never creates performers (even with auto-create on).
- `mode: performer` — a full re-sync scoped to one Stash performer. Requires a
  `performerId` arg (does nothing without it, so it never falls back to syncing
  everyone). It resolves that performer's name + `alias_list` and only processes
  the `user_data.db` profile whose OF username matches — bridging the usual
  display-name≠username gap. Triggered by the **"Sync OnlyFans" button** that the
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
- **`OFDatabase` (of_database.py)** — opens `user_data.db` files **read-only**
  (`mode=ro` URI) so a concurrently running OF-Scraper never causes a write or
  "readonly database" error. Schema verified against **OF-Scraper 3.14.7**. Post
  text is searched across the `posts`, `stories`, `messages`, `others`, and
  `products` tables. It **detects the schema at open time** (`_detect_schema`) to
  also support older OF-Scraper databases, which lack `medias.model_id`, store
  the date in `created_at` instead of `posted_at`, and leave `profiles` empty
  (the creator name is then recovered from `medias.directory`).
- **`MediaProcessor` (media.py)** — turns post text into title/details, parses
  collaborator credits, derives studio code from filename, formats dates.
  `parse_mentions` picks up both `@mentions` **and** bare profile links
  (`onlyfans.com/<username>`), because creators sometimes credit a collaborator
  by URL instead of an @mention; the post-id URL form
  `onlyfans.com/<postid>/<username>` is excluded by skipping purely-numeric
  first segments. `process_text` also applies the **Title Exclusions** list
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
- **Folder galleries** (`sync_folder_galleries`, in of-stash-sync *and*
  jff-stash-sync) — Stash creates a gallery for every scanned folder of images.
  Those are distinct from the per-post galleries the plugins build, and the
  schema makes them safe to tell apart: **a folder gallery has a `folder`, a
  plugin-made one does not** (`find_folder_galleries` filters on exactly that).
  Each of a creator's folder galleries gets the creator's studio, the creator
  performer, the site tag, and a title of
  `<username> <Site> <MediaDir> (<category>)` — the category being the path
  segments between the creator's directory and the media folder, without which
  every image folder of one creator (Posts/Free, Posts/Paid, Messages/Free,
  Archived/...) would collide on the same title. Deliberately **additive** for
  performers and tags: a folder is not a post, so there is no authoritative cast
  to replace with, and manual curation survives. Only title/studio are asserted,
  under the usual organized rule (plain sync skips organized, full refreshes
  all), and `build_folder_gallery_update` returns None when nothing would change
  so re-runs write nothing. The creator is matched by whole **path segment**, not
  substring, because Stash's `path` filter is a substring match and would
  otherwise let `jake` claim `/data/jakeson/...`.
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

## Hard constraints — keep these intact

- **Standard library only.** No third-party Python deps; the plugin runs inside
  the Stash container with nothing installed. (This is why `media.py` has its own
  emoji regex instead of the `emojis` package.) Don't add `requirements.txt` or
  imports outside the stdlib.
- **Databases are opened read-only.** Never change `OFDatabase` to open for write.
- **GraphQL fields must match the Stash schema** (currently v0.31.x). Verify any
  new field/query against the running Stash version before relying on it.
  Note the asymmetry: `director` exists only on scenes, `photographer` only on
  images — credit each on the media type that has it.
- **The tag-only and crew paths are surgical.** `build_tag_only_update` only ever
  adds tags; `build_crew_only_update` only ever touches `performer_ids` and the
  `director`/`photographer` field, and returns `(None, None)` when nothing
  changes. Both leave all other fields untouched (Stash only mutates fields you
  send), so manual edits survive. Keep them that way.
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

## Conventions

- Python: stdlib only, classes for clients/resolvers, `.format()` string
  formatting (as in existing code), docstrings explaining *why* (schema versions,
  edge cases) rather than restating the code.
- There is no test suite or linter configured in this repo.
- Commit messages: short imperative subject lines (see `git log`).
