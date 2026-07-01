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

Currently there is one plugin:

- **`plugins/of-stash-sync/`** — OnlyFans Metadata Sync. Syncs metadata scraped by
  [OF-Scraper](https://github.com/datawhores/OF-Scraper) (read from its
  `user_data.db` sqlite files) into matching Stash scenes and images: title,
  details, date, URL, performers, studio, code, tags, and the `organized` flag.
  It is a native, dependency-free re-implementation of
  [`timekillerj/ofscraper-stash-sync`](https://github.com/timekillerj/ofscraper-stash-sync).

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
```

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

- `mode: sync` — only unorganized OnlyFans scenes/images.
- `mode: full` — re-sync everything, ignoring the `organized` flag.
- `mode: tag` — additive only; adds tags from post text, never touches other
  fields. Safe over manually edited media.
- `mode: crew` — surgical crew-credit pass. For all OnlyFans media it moves
  crew-tagged people out of the performers list and into the scene `director` /
  image `photographer` field, leaving every other field untouched. Skips media
  that already match, and never creates performers (even with auto-create on).

## Architecture notes

- **`StashClient` (stash.py)** — every GraphQL query/mutation. Fields were
  verified against the **Stash v0.31.1** schema. It connects to
  `localhost:<port>` (the plugin always runs on the same host as Stash).
- **`OFDatabase` (of_database.py)** — opens `user_data.db` files **read-only**
  (`mode=ro` URI) so a concurrently running OF-Scraper never causes a write or
  "readonly database" error. Schema verified against **OF-Scraper 3.14.7**. Post
  text is searched across the `posts`, `stories`, `messages`, `others`, and
  `products` tables. It **detects the schema at open time** (`_detect_schema`) to
  also support older OF-Scraper databases, which lack `medias.model_id`, store
  the date in `created_at` instead of `posted_at`, and leave `profiles` empty
  (the creator name is then recovered from `medias.directory`).
- **`MediaProcessor` (media.py)** — turns post text into title/details, parses
  `@mentions`, derives studio code from filename, formats dates. Tag matching
  (`compile_name_pattern`) mirrors Stash's own auto-tagger
  (separator-insensitive, word-bounded, case-insensitive).
- **sync.py resolvers** — `PerformerResolver`, `StudioResolver`, `TagResolver`,
  `TagTextMatcher` each cache lookups and create-if-missing where appropriate.
  Updates are routed by where the media actually lives in Stash
  (scene -> `sceneUpdate`, image -> `imageUpdate`).
- **Crew credit** — a performer carrying the configured *Crew Tag* (matched by
  **tag id**, not name, so renames don't break it) is treated as crew:
  `collect_crew` credits them in the scene
  `director` / image `photographer` field instead of the performers list, for
  both the creator and any @mentioned collaborator. The creator is still added
  as a performer when a post credits only crew (so media is never
  performer-less), and the **studio always follows the source db**, never the
  credited director. `build_update` applies this during sync; `build_crew_only_update`
  applies just this part for the `crew` task.

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
