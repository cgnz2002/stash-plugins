# Patreon Metadata Sync (Stash plugin)

A native [Stash](https://stashapp.cc) plugin that syncs **Patreon** post metadata
onto the media you downloaded with
[**patreon-dl**](https://github.com/patrickkfkan/patreon-dl).

patreon-dl writes each post's metadata beside the media, in
`post_info/info.txt` and `post_info/post-api.json`, and Stash ingests each post
folder as a **gallery**. This plugin reads those files **directly** off the data
path — there is no separate database or converter step — and for each post:

- updates the matching Stash **gallery** (title, description, date, URL, studio,
  creator performer, organized), and
- writes the **same metadata onto every image** inside that post folder.

For each Patreon **collection** it creates a Stash gallery (named after the
collection) and attaches the member posts' images to it.

## Pipeline

```
             downloads                                reads files directly
  Patreon ───────────────▶  /data/patreon/...  ◀───────────────────────────  patreon-stash-sync
          patreon-dl        (info.txt +              galleryUpdate / imageUpdate /
                             post-api.json)          galleryCreate + addGalleryImages
                                  │
                                  └── Stash scans the folders into galleries + images
```

There's nothing to run between patreon-dl and Stash: download, let Stash scan the
library, then run the plugin's **Sync** task.

## On-disk layout the plugin reads

```
/data/patreon/
└── <creator-vanity> - <Creator Name>/
    ├── posts/
    │   └── <postId> - <Post Title>/
    │       ├── images/            full-res media   (Stash gallery + images)
    │       ├── attachments/  audio/
    │       ├── .thumbnails/       webp thumbnails  (IGNORED)
    │       └── post_info/
    │           ├── info.txt       flat Key: value summary   (primary source)
    │           ├── post-api.json  raw Patreon JSON:API      (fallback + vanity)
    │           └── *.webp         cover/thumbnail  (IGNORED)
    └── collections/
        └── <collectionId> - <Title>/
            └── *.json             raw collection JSON
```

For each post the plugin reads **`info.txt`** first — a flat, already-rendered
summary whose `Content` is HTML even when the API's `content` field is `null`
(the body then only lives in `content_json_string`). It falls back to
**`post-api.json`** for the body and reads it for the authoritative creator
`vanity`. HTML/ProseMirror is stripped to plain text before it reaches Stash.
`.thumbnails/` and the `post_info/*.webp` files are never touched — the plugin
only edits the galleries/images Stash made from `images/`.

Galleries and images are matched by the **post folder basename**
(`<postId> - <Post Title>`), which embeds the numeric post id, so it survives
renaming the post title.

## Field mapping (Patreon → Stash)

| Stash field (gallery + image) | Patreon source                                   |
|-------------------------------|--------------------------------------------------|
| title                         | `Title` / `attributes.title`                     |
| details                       | `Content` (HTML) / `content_json_string`         |
| date                          | `Published` / `attributes.published_at`          |
| url                           | `URL` / `attributes.url`                          |
| studio                        | `<vanity> (Patreon)` (created if missing)        |
| performer                     | the creator vanity (created if missing)          |

| Collection gallery | Patreon source                     |
|--------------------|------------------------------------|
| title              | `attributes.title`                 |
| details            | `attributes.description`           |
| date               | `attributes.created_at`            |
| images             | member posts' images (`post_ids`)  |

## Settings

| Setting | Default | Description |
|---|---|---|
| Patreon Data Path | (required) | Directory patreon-dl downloads into, as seen inside the Stash container (e.g. `/data/patreon`). |
| Parent Studio Name | `Patreon (network)` | Top-level studio the per-creator studios nest under. Created automatically if missing. |
| Auto-tag From Post Text | off | Attach existing Stash tags whose name/alias appears in the post text. Only adds existing tags. |
| Sync Collections | on | Create a gallery per Patreon collection and attach the member posts' images. |
| Dry Run | off | Preview every change in the log without writing anything to Stash. |

The plugin creates the parent studio, the per-creator studio, and the creator
performer if they don't already exist, so no manual Stash setup is required. If a
creator vanity already matches an existing performer (by name or alias), that
performer is used instead of creating a duplicate.

## Tasks

Scan your Patreon library in Stash first (so the galleries/images exist), then:

- **Sync Patreon Metadata** — sync unorganized Patreon galleries and images, and
  build collection galleries.
- **Full Sync Patreon Metadata** — re-sync everything, ignoring the organized flag.
- **Tag Patreon From Text** — add tags from post text to ALL galleries and images.
  Only *adds* tags; never changes any other field. Safe over manual edits.
- **Preview (Dry Run)** — run a full sync that **writes nothing**; the log lists
  every gallery/image it would update and every studio/performer/collection
  gallery it would create. Run this first to check the mapping.

## Requirements

- Stash v0.31.x (verified against v0.31.1).
- No Python dependencies — the plugin uses only the standard library. It does
  require a `python` interpreter to be available in the Stash container (the
  same requirement as any Python Stash plugin).
