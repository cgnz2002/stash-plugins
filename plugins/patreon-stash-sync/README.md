# Patreon Metadata Sync (Stash plugin)

A native [Stash](https://stashapp.cc) plugin that syncs **Patreon** metadata into
matching Stash scenes and images: title, details, date, URL, performers, studio,
code, tags, and the `organized` flag.

It is a fork of this repo's [`of-stash-sync`](../of-stash-sync/) plugin. Instead
of reading OF-Scraper's database directly, it reads a `user_data.db` that a small
converter builds from the on-disk output of
[**patreon-dl**](https://github.com/patrickkfkan/patreon-dl). Because the
converter emits the exact OF-Scraper database shape, the plugin's database reader
(`of_database.py`) is byte-for-byte identical to of-stash-sync's — only the
Patreon-specific strings (studio suffix, parent studio, URLs, icon) differ.

## Pipeline

```
                    downloads                convert_patreon_to_db.py
  Patreon  ─────────────────────▶  disk  ──────────────────────────▶  user_data.db
           patreon-dl (Docker)    layout   (walk post-api.json,        (OF-Scraper
                                            read real filenames         shaped)
                                            from images/)                   │
                                                                            │ reads
                                                                            ▼
   Stash library scenes/images  ◀───────────────────────────────  patreon-stash-sync
        (matched by filename)          sceneUpdate / imageUpdate      (this plugin)
```

1. **patreon-dl** downloads to `/volume1/stash/media/patreon/<vanity> - <Name>/posts/<id> - <title>/`.
2. **`convert_patreon_to_db.py`** walks that tree, parses each
   `post_info/post-api.json`, reads the *real* media basenames from the sibling
   `images/` (and `attachments/`, `audio/`) directories, and writes
   `user_data.db` at the media root.
3. **This plugin** finds `user_data.db` under its configured data path and syncs
   metadata onto the Stash scenes/images whose file basename matches, exactly the
   way of-stash-sync does for OnlyFans.

## On-disk layout patreon-dl produces

```
/volume1/stash/media/patreon/
└── <vanity> - <Creator Name>/
    └── posts/
        └── <postId> - <Post Title>/
            ├── images/                 full-res, named media   (MATCHED)
            │   ├── Jake.png
            │   └── Laios comic.png
            ├── attachments/            downloadable files      (matched)
            ├── audio/                  audio files             (matched)
            ├── .thumbnails/            webp thumbnails         (IGNORED)
            └── post_info/
                ├── post-api.json       raw Patreon JSON:API    (PARSED)
                ├── info.txt            human-readable summary  (ignored)
                ├── cover-image.webp    auxiliary               (ignored)
                └── thumbnail.webp      auxiliary               (ignored)
```

The converter matches Stash against the files in `images/`, `attachments/` and
`audio/` — never `.thumbnails/*.webp` or the `post_info/` cover/thumbnail images,
which are auxiliary.

## Running the converter

`convert_patreon_to_db.py` needs only the Python 3 standard library (no `pip`
install). Point it at the media root; it writes `<root>/user_data.db`:

```bash
python3 convert_patreon_to_db.py /volume1/stash/media/patreon
# or choose the output explicitly:
python3 convert_patreon_to_db.py /volume1/stash/media/patreon --db /volume1/stash/media/patreon/user_data.db
```

It is **idempotent** — every row is upserted by primary key, so it is safe to
re-run after each new download. Deleting posts on disk does not prune old rows;
delete `user_data.db` and re-run if you want a clean rebuild.

### Automating it after each patreon-dl run

Run the converter right after patreon-dl finishes, so the database is fresh
before the plugin's sync task runs. Two common setups on a NAS:

- **Scheduled task (Synology Task Scheduler / cron):** schedule patreon-dl, then
  a second step that runs
  `python3 /path/to/convert_patreon_to_db.py /volume1/stash/media/patreon`.
- **Sidecar container:** mount the same media volume into a small `python:3-slim`
  container and run the converter on a schedule (or as the last step of your
  patreon-dl compose job). Only the standard library is used, so the base image
  needs nothing extra.

Then run **Sync Patreon Metadata** in Stash (or schedule it) after a library
scan.

## Field mapping (Patreon → OF-Scraper DB → Stash)

| DB column          | Patreon source (`post-api.json`)                     | Used in Stash as |
|--------------------|------------------------------------------------------|------------------|
| `medias.filename`  | real basename in the post's `images/` dir            | match key + `code` |
| `medias.post_id`   | `data.id`                                            | post URL + text lookup |
| `medias.model_id`  | `data.relationships.campaign.data.id`                | creator filter |
| `medias.link`      | `data.attributes.url`                                | (informational) |
| `medias.posted_at` | `data.attributes.published_at`                       | scene/image date |
| `medias.media_type`| derived from file extension                          | (informational) |
| `medias.directory` | folder path of the media file                        | (informational) |
| `profiles.username`| creator vanity (folder name / campaign)              | studio name + path filter |
| `profiles.user_id` | = `medias.model_id`                                  | creator filter |
| `posts.text`       | `title` + plaintext(`content`)                       | title + details |
| `posts.paid/price` | patron-only → `1` + tier amount, else `0`/`0`        | `paid` tag |
| `posts.archived`   | `0` (no Patreon equivalent)                          | `archived` tag |

> **Note on `post-api.json` keys.** Patreon renames JSON:API keys occasionally.
> The converter reads defensively (e.g. paid detection checks `is_public`,
> `is_paid`, `min_cents_pledged_to_view`, and any attached tier/access-rule
> gate). If a future API revision changes a key, adjust the `_first(...)`
> candidate lists in `convert_patreon_to_db.py`.

## Plugin settings

| Setting | Default | Description |
|---|---|---|
| Patreon Data Path | (required) | Directory searched recursively for `user_data.db`, as seen inside the Stash container (e.g. `/volume1/stash/media/patreon`). |
| Parent Studio Name | `Patreon (network)` | Top-level studio that per-creator studios are nested under. |
| Max Title Length | `65` | Titles longer than this are truncated at a sentence or word boundary. |
| Allow Multiple Performer Matches | off | If several performers match a creator vanity, attach all of them instead of skipping. |
| Create Missing Performers | off | Create a sparse performer for the creator and any unmatched `@mentions` instead of skipping. |
| Auto-tag From Post Text | off | Scan each post's text and attach any existing Stash tags whose name or alias appears in it. |
| Skip Multi-file Scenes and Images | off | Sync and Full Sync skip any scene or image with more than one file, to protect their performers and metadata. |
| Crew Tag ID | (empty) | The Stash tag **ID** (from the tag's URL, e.g. `.../tags/42` → `42`) marking crew performers, credited in the Director/Photographer field instead of the performers list. Empty disables crew handling. |

The parent studio (default `Patreon (network)`) must already exist in Stash, and
performers should exist with the creator vanity as their name or an alias (unless
you enable *Create Missing Performers*).

## Tasks

Run these from **Settings → Tasks** (or schedule them). Scan your library in
Stash first so the scenes and images exist, and run the converter first so
`user_data.db` is up to date.

- **Sync Patreon Metadata** — sync only unorganized Patreon scenes and images.
- **Full Sync Patreon Metadata** — re-sync everything, ignoring the organized flag.
- **Tag Patreon From Text** — add tags from post text to ALL Patreon scenes and
  images. Only *adds* tags; never changes any other field. Safe over manual edits.
- **Update Crew** — re-apply only the crew logic (move crew-tagged people into
  the Director/Photographer field and out of the performers list). Leaves every
  other field untouched.

## Requirements

- Stash v0.31.x (verified against v0.31.1).
- No Python dependencies — the plugin and the converter use only the standard
  library, so nothing needs installing into the Stash container.

## Notes

- The `user_data.db` is opened **read-only** by the plugin, so re-running the
  converter while a sync task is running is safe.
- Post text is `title` + the plaintext of the HTML post body; a locked post with
  no visible body falls back to its teaser text.
- Filename-basename matching is what makes the sync work — the converter always
  records the actual on-disk basename, never a reconstructed name.
- This is a fork of `of-stash-sync`; credit to timekillerj for the original tool
  that plugin was based on.
