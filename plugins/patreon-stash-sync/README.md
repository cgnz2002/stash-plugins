# Patreon Metadata Sync (Stash plugin)

A native [Stash](https://stashapp.cc) plugin that syncs **Patreon** post metadata
onto the media you downloaded with
[**patreon-dl**](https://github.com/patrickkfkan/patreon-dl).

Patreon posts are image posts, and patreon-dl saves each post as a folder, which
Stash ingests as a **gallery**. So this plugin, for each post:

- updates the matching Stash **gallery** (title, description, date, URL, studio,
  performers, tags, organized), and
- writes the **same metadata onto every image** inside that post folder.

For each Patreon **collection** it creates a Stash gallery (named after the
collection) and attaches the member posts' images to it.

It is a sibling of this repo's [`of-stash-sync`](../of-stash-sync/) plugin and
reuses its Stash client, logging and text handling.

## Pipeline

```
                     downloads               convert_patreon_to_db.py
  Patreon  ────────────────────────▶  disk  ─────────────────────────▶  user_data.db
           patreon-dl (Docker)        layout  (walk post_info + collections)    │
                                                                                 │ reads
                                                                                 ▼
   Stash galleries + images   ◀───────────────────────────────────   patreon-stash-sync
   (matched by post folder)      galleryUpdate / imageUpdate /            (this plugin)
   + one gallery per collection  galleryCreate + addGalleryImages
```

1. **patreon-dl** downloads to
   `/volume1/stash/media/patreon/<vanity> - <Name>/posts/<id> - <title>/`.
2. **`convert_patreon_to_db.py`** walks that tree and writes `user_data.db` at
   the media root (post rows + collection rows + creator profiles).
3. **This plugin** finds `user_data.db` under its data path and syncs metadata
   onto the galleries/images whose folder matches each post, and builds a gallery
   per collection.

## On-disk layout patreon-dl produces

```
/volume1/stash/media/patreon/
└── <vanity> - <Creator Name>/
    ├── posts/
    │   └── <postId> - <Post Title>/
    │       ├── images/            full-res media   (Stash gallery + images)
    │       ├── attachments/  audio/
    │       ├── .thumbnails/       webp thumbnails  (IGNORED)
    │       └── post_info/
    │           ├── info.txt       flat Key: value summary   (PARSED, primary)
    │           ├── post-api.json  raw Patreon JSON:API      (parsed for ids/vanity)
    │           └── *.webp         cover/thumbnail  (IGNORED)
    └── collections/
        └── <collectionId> - <Title>/
            └── *.json             raw collection JSON       (PARSED)
```

## The converter

`convert_patreon_to_db.py` needs only the Python 3 standard library. Point it at
the media root; it writes `<root>/user_data.db`:

```bash
python3 convert_patreon_to_db.py /volume1/stash/media/patreon
# or choose the output explicitly:
python3 convert_patreon_to_db.py /volume1/stash/media/patreon --db /somewhere/user_data.db
```

For each post it reads **`post_info/info.txt`** first — a flat, already-rendered
summary that is simpler and more reliable than the raw API (its `Content` is HTML
even when the API's `content` field is `null`, in which case the body otherwise
only lives in `content_json_string`). It falls back to **`post-api.json`** for the
body when needed and reads it for the creator's campaign id and authoritative
`vanity`. HTML/ProseMirror bodies are stripped to plain text before storing, so
no markup reaches Stash. Collections are read from the JSON under `collections/`.

It is **idempotent** — every row is upserted by primary key, so it is safe to
re-run after each new download. Delete `user_data.db` to force a clean rebuild.

### Automating it after each patreon-dl run

Run the converter right after patreon-dl finishes so the database is fresh before
the plugin's sync task:

- **Scheduled task (Synology Task Scheduler / cron):** run patreon-dl, then
  `python3 /path/to/convert_patreon_to_db.py /volume1/stash/media/patreon`.
- **Sidecar container:** mount the media volume into a small `python:3-slim`
  container and run the converter on a schedule. Only the standard library is
  used, so nothing extra needs installing.

Then run **Sync Patreon Metadata** in Stash (or schedule it) after a library scan.

## Field mapping (Patreon → DB → Stash)

| DB column (`posts`)   | Patreon source                                   | Synced to |
|-----------------------|--------------------------------------------------|-----------|
| `title`               | `Title` / `attributes.title`                     | gallery + image **title** |
| `content`             | `Content` (HTML) / `content_json_string`         | gallery + image **details** |
| `posted_at`           | `Published` / `attributes.published_at`          | **date** |
| `url`                 | `URL` / `attributes.url`                          | **URL** |
| `directory`           | post folder path                                  | match key (gallery/image path) |
| `model_id`            | `campaign` id, else vanity-derived                | groups posts by creator |
| `paid` / `price`      | `access_rules` type (patron-only)                 | optional `paid` tag |
| `profiles.username`   | `user.vanity` / URL / folder                      | studio name + performer |

| DB column (`collections`) | Patreon source                     | Synced to |
|---------------------------|------------------------------------|-----------|
| `title`                   | `attributes.title`                 | new gallery **title** |
| `description`             | `attributes.description`           | gallery **details** |
| `posted_at`               | `attributes.created_at`            | **date** |
| `post_ids`                | `attributes.post_ids`              | member images attached to the gallery |

## Plugin settings

| Setting | Default | Description |
|---|---|---|
| Patreon Data Path | (required) | Directory searched recursively for `user_data.db`, as seen inside the Stash container (e.g. `/volume1/stash/media/patreon`). |
| Parent Studio Name | `Patreon (network)` | Top-level studio that per-creator studios are nested under. |
| Max Title Length | `65` | Titles longer than this are truncated at a sentence or word boundary. |
| Create Missing Performers | off | Create a sparse performer for the creator (and unmatched `@mentions`) instead of leaving it unset. |
| Auto-tag From Post Text | off | Attach existing Stash tags whose name/alias appears in the post text. |
| Sync Collections | on | Create a gallery per Patreon collection and attach the member posts' images. |
| Crew Tag ID | (empty) | The Stash tag **ID** (from the tag's URL) marking crew performers, credited in the Photographer field instead of the performers list. Empty disables crew handling. |

The parent studio (default `Patreon (network)`) must already exist in Stash, and
performers should exist with the creator vanity as their name or an alias (unless
you enable *Create Missing Performers*).

## Tasks

Scan your library in Stash first (so the galleries/images exist) and run the
converter first (so `user_data.db` is current), then run:

- **Sync Patreon Metadata** — sync unorganized Patreon galleries and images, and
  build collection galleries.
- **Full Sync Patreon Metadata** — re-sync everything, ignoring the organized flag.
- **Tag Patreon From Text** — add tags from post text to ALL galleries and images.
  Only *adds* tags; never changes any other field. Safe over manual edits.
- **Update Crew** — re-apply only the crew logic (move photographer-tagged people
  into the Photographer field and out of the performers list). Leaves every other
  field untouched.
- **Preview (Dry Run)** — run a full sync that **writes nothing**: the log lists
  every gallery/image it would update and every studio/performer/collection
  gallery it would create. Run this first to check the mapping before a real
  sync. (The *Dry Run* setting does the same for whichever task you run.)

## Notes

- `user_data.db` is opened **read-only** by the plugin, so re-running the
  converter during a sync task is safe.
- Galleries and images are matched by the **post folder basename** (which embeds
  the numeric post id), so it survives renaming the post title.
- On full/sync passes, post tags are **merged** with any tags already on the
  gallery/image — existing manual tags are never removed.
- Requirements: Stash v0.31.x; no Python dependencies (standard library only) for
  both the plugin and the converter.
