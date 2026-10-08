# Handover: moving development onto the NAS

Written 2026-10-08 by the cloud session that built most of `of-stash-sync`,
for the Claude Code session now running in a container on the NAS next to
Stash. Read this, then `CLAUDE.md`, then `docs/stash-platform.md`.

## Where things stand

Everything is on `main`. Every working branch, including the comic-reader
session's, has been merged, and nothing is waiting on a PR.

| Plugin | Version | State |
|---|---|---|
| `of-stash-sync` (Fan Site Metadata Sync) | 3.1.1 | Active. OnlyFans, JustFor.Fans and Patreon. |
| `comic-reader` | 0.6.0 | Active. Built by a separate session; see its section in `CLAUDE.md`. |
| `patreon-stash-sync` | 3.3.0 | Deprecated and frozen. Don't develop it. |

`python3 tests/run.py` passes 23/23.

## The workflow change

Until now, changes went branch → PR → merge to `main` → GitHub Pages rebuilt
the plugin source → **Check for Updates** in Stash. That is being dropped. The
plugins aren't shared with anyone, and a session that can see the running
Stash catches bugs the cloud session couldn't (see *Why* below).

**Keep git.** It is the undo button, and `CLAUDE.md` plus `tests/` carry
everything learned so far. Pushing to GitHub is optional, as an off-site
backup. The repo is public, so cloning needs no credentials; pushing needs a
fine-grained token scoped to this repo with *Contents: read and write*.

### One-time setup

1. Clone the repo into a **mounted volume**, not the container's own
   filesystem, or the next container rebuild wipes it.
2. Run `python3 tests/run.py`. The comic-reader test runs its JavaScript half
   only when `node` is on PATH.
3. Back up Stash's `config.yml` and run Stash's **Backup** task.
4. In Stash → Settings → Plugins, uninstall the **source-installed** copies of
   `of-stash-sync` and `comic-reader` (and `patreon-stash-sync` if present),
   then remove the `cgnz2002.github.io/stash-plugins` source. Otherwise *Check
   for Updates* overwrites local edits, and two plugins with the same id clash.
5. Copy `plugins/of-stash-sync/` and `plugins/comic-reader/` into Stash's
   plugins folder, keeping those folder names. The plugin id is the manifest
   filename, and Stash stores settings by id.
6. Call the `reloadPlugins` GraphQL mutation (or Settings → Plugins → Reload).
7. Check that both plugins appear under Tasks and Settings, and that their
   settings survived: data paths, parent studios, Crew/Sponsor/Content House
   tag IDs, Title Exclusions, and comic-reader's tag IDs. Restore them from
   the `config.yml` backup if they didn't.

### Every change after that

1. Edit under `plugins/<id>/`. Keep tests in `tests/`, never inside a plugin
   folder.
2. `python3 tests/run.py`. Add a test for any bug you fix; most existing tests
   encode a bug that actually shipped.
3. Bump `version:` in the manifest, then commit.
4. Copy the plugin folder into Stash's plugins folder and run `reloadPlugins`.
5. **Confirm the plugin is still listed.** A manifest that fails to parse makes
   Stash drop the plugin *silently*. That happened in 3.1.0.
6. Preview with a **Dry Run** task before any real run that writes.

`build_site.sh` and `.github/workflows/deploy.yml` can stay; they only matter
if publishing is ever wanted again.

## Why being next to Stash matters

Every serious bug in this stretch came from not seeing the real library:

- **Substring paths.** Stash's `path` filter is a substring match over the
  whole library. A Patreon creator called `Mirenac` matched torrent downloads,
  and the plugin wrote Patreon metadata onto them.
- **`embed/` holds video.** It was excluded on the assumption that it held only
  descriptors, and whole scenes went unsynced.
- **Zip contents.** Images inside a `.zip` have the path `<zip>/<inner>`, which
  no disk walk finds.
- **The broken manifest.** An unquoted `: ` in a task description made Stash
  drop the plugin.

With access to Stash you can query GraphQL, read the log, look at real paths,
and check fields against the version that is actually running (v0.31.x) instead
of reading Stash's source.

## Ground rules from the user

These came from corrections during this work. Treat them as fixed.

- **Only the user's own downloads are ours.** Metadata must never be applied
  to content outside the configured data paths (torrents etc.), unless Stash
  merged that file with one of the user's own. Everything is confined to
  each site's data path for this reason.
- **Never delete files.** Clearing metadata and resetting `organized` on a
  stray is fine. Deleting media is not.
- **Hand-curated work is sacred.** A studio under a network parent is not
  evidence the plugin made it; the user files torrented creators there too.
  Anything with a **stash-box ID** (FansDB, StashDB) is the user's. A plain
  sync never overwrites an organized item.
- **Be conservative when unsure.** A missed stray costs a manual fix. A false
  positive destroys curation that can't be recovered.
- **Show before changing.** The user reads dry-run logs, often on a phone, so
  make log lines legible and specific.
- **Merging and deploying happen when the user says so.**

## Facts about this install

- Stash runs at `127.0.0.1:9999` inside its container. If an API key is set,
  the plugin switches to it so long runs don't outlive the session cookie;
  without one it logs a warning and uses the cookie.
- Patreon data path in Stash: `/data/patreon`. The patreon-dl container sees
  the same files as `/Patreon/Download`. The plugin setting must use **Stash's**
  view of the path.
- Layout: `<vanity> - <Name>/posts/<id> - <title>/{images,attachments,embed,post_info}/`.
- Torrent downloads live under `/torrents/downloads/...`, which is outside
  every data path.
- The user has their own studios for torrented OnlyFans creators, filed under
  `OnlyFans (network)`, and a *Location* tag group.
- Another installed plugin, **Tag Images From Performer Tags** (CommunityScripts),
  fails with HTTP 401 on every image update. That is its own login problem,
  not ours, but it floods the log during syncs.

## Loose ends

- **Patreon "Skipped: 430".** These are probably images from `post_info/`,
  `.thumbnails/` and `image_previews/`. If so, add those three as Library
  exclusion patterns (see the README) and run Stash's **Clean** task.
- **Patreon folder galleries already organized** by the old Patreon plugin are
  skipped by a plain sync. Find them with Galleries → Studio = Patreon network,
  Path *is not null*, Studio Code *is null*. **Full Sync Patreon Only** fills
  them, but it rewrites image metadata too, so turn on *Keep Manual Performers
  & Tags* first.
- **comic-reader `Webtoon` tags** were lost when the old per-post Patreon
  galleries were deleted. Run comic-reader's **Set Up Comics**; re-mark any
  non-`.cbz` comics by hand.
- **Content House Tag ID** (3.1.0) shipped but may not be set up yet: create a
  marker tag, set its ID, tag the content house's performer, then run **Update
  Crew & Sponsors**.
- Earlier plugin-built Patreon per-post galleries were deleted by the user.
  Patreon now writes post data onto Stash's own folder/zip galleries
  (`SourceProfile.post_folders`), so they should not come back.
