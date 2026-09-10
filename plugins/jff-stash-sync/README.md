# JustFor.Fans Metadata Sync (Stash plugin)

A native [Stash](https://stashapp.cc) plugin that syncs metadata scraped by
**jff-scraper** into matching Stash scenes and images. It reads the scraper's
`user_data.db` files and writes title, details, date, the real post URL,
performers, studio, hashtags and tags onto the corresponding media, then marks
them organized.

It is the JustFor.Fans sibling of `of-stash-sync` and behaves the same way; the
platform differences are listed under [JustFor.Fans specifics](#justforfans-specifics).

## What it does

For every jff-scraper `user_data.db` found under the configured data path, and
for each creator profile in it:

- Resolves (or creates) a per-creator studio `<username> (JustForFans)` as a
  child of the configured parent studio (default `JustForFans (network)`).
- Finds that creator's Stash scenes and images by file path and matches each
  file back to the database by filename.
- Sets **title** and **details** from the post text, **date** from the post date,
  **studio**, **code** (media id), **URL** to the real JustFor.Fans post, and
  **performers** (the creator plus any collaborators credited in the post text,
  whether `@mentioned` or linked by a bare `justfor.fans/username` profile URL).
- Adds the post's own **JustFor.Fans hashtags** as Stash tags.
- Tags **paid** content (from the scraper's Free/Paid tier) and **pinned** posts.
- Credits anyone tagged as **crew** in the scene **Director** / image
  **Photographer** field instead of the performers list.
- Groups each post's media into a **gallery**, stamped with the post id in the
  gallery's `code`.
- Tags every synced scene, image and gallery with a **`JustFor.Fans`** tag
  (created if missing), so all JFF media is filterable by one tag.
- Marks each synced item **organized** so the normal sync skips it next time.

### Post galleries

On the **Sync** and **Full Sync** tasks the plugin groups a creator's media by
post id and makes one **gallery** per post, carrying the same metadata (title,
details, date, studio, performers, tags, URL) as the post's scenes/images. A
gallery is created when a post has **2+ images**, or **an image alongside a
video** — because Stash relates scenes to galleries (not to images), so the
gallery is how a post's stills get linked to its video. Galleries are keyed by
the post URL, so re-runs don't duplicate them; a plain Sync creates missing
galleries and adds any new images (never overwriting a gallery you've edited),
while Full Sync also refreshes their metadata.

## JustFor.Fans specifics

These are the real differences from the OnlyFans plugin, all driven by what the
scraper records:

| Thing | How it works here |
|---|---|
| **Post URL** | A JFF link is `justfor.fans/<user>?Post=<encoded key>` — it carries an encoded key and **cannot be rebuilt from the post id**. The plugin uses the real `post_url` the scraper captured in `jff_posts`. |
| **Post id on galleries** | Because the post id isn't in the URL (it is on OnlyFans), each gallery is stamped with its post id in the gallery **`code`** field, which is how the tag pass correlates a gallery back to its post. |
| **Paid content** | JFF exposes no per-post price, so `posts.paid`/`price` stay `0`. The **`tier`** column (Free/Paid) is the authoritative signal and is what drives the `paid` tag. |
| **Hashtags** | The post's own hashtags (`jff_posts.tags`) are synced as Stash tags, created if missing. This is separate from *Auto-tag From Post Text*, which only ever attaches tags that already exist. |
| **Collaborators** | Matched on `@mentions` and bare **`justfor.fans/<username>`** profile links (the OF plugin matches `onlyfans.com/...`). |
| **Which databases** | Only databases whose `schema_flags` row says `source = jff` are processed, so pointing this at a path that also holds an OF-Scraper `user_data.db` is safe — it skips them with a warning. |

The per-post `.json` sidecars the scraper writes are **not** read: everything the
plugin needs is already in `user_data.db`, and one indexed query beats thousands
of file reads. Keep the sidecars as your rebuild insurance.

## Requirements

- Stash v0.31.x (verified against v0.31.1).
- No Python dependencies — standard library only, so nothing needs installing
  into the Stash container.
- The parent studio (default `JustForFans (network)`) must already exist in Stash.
- **For large libraries, set an API key** in Stash **Settings → Security** (the
  plugin picks it up automatically). A big Full Sync can outlive Stash's session
  cookie, which then returns `HTTP 401` on every remaining request. The API key
  doesn't expire. Without one it falls back to the cookie and logs a warning.
- Performers should already exist with the JFF username as their name or an
  alias (unless you enable *Create Missing Performers*).

## Installation

1. Install from the plugin source, or copy the `jff-stash-sync` folder into
   Stash's `plugins` directory (`<stash config>/plugins/jff-stash-sync`).
2. In Stash, go to **Settings → Plugins** and click **Reload Plugins**.
3. Configure the settings below. At minimum set the data path.
4. Add your JFF media folder as a Stash library and **Scan** it first, so the
   scenes and images exist for the plugin to match.

## Settings

| Setting | Default | Description |
|---|---|---|
| JFF Data Path | (required) | Directory searched recursively for jff-scraper `user_data.db` files, as seen inside the Stash container (e.g. `/data/justforfans`). |
| Parent Studio Name | `JustForFans (network)` | Top-level studio that per-creator studios are nested under. |
| Max Title Length | `65` | Titles longer than this are truncated at a sentence or word boundary. |
| Allow Multiple Performer Matches | off | If several performers match a username, attach all of them instead of skipping. |
| Create Missing Performers | off | Create a sparse performer for the creator and any unmatched collaborators instead of skipping. |
| Keep Manual Performers & Tags | off | Sync becomes **non-destructive** to performers and tags: it **merges** instead of replacing, so hand-added ones survive a Full Sync. Everything else is still updated. |
| Sync Workers | `2` | How many writes to send to Stash at once. SQLite has a **single writer**, so too much concurrency piles up transactions until they time out. **Lower this if you see "database is locked" / "timed out"**; `1` is safest. Range 1–16. |
| Auto-tag From Post Text | off | Scan post text and attach any *existing* Stash tags whose name or alias appears in it. Never creates tags. (The post's own hashtags are synced regardless.) |
| Skip Multi-file Scenes and Images | off | Skip any scene/image with more than one file, to protect their performers and metadata. |
| Title Exclusions | (empty) | Phrases/regexes stripped from generated **titles** only (the description keeps the original post text). Edit via **Settings → Tools → "JustFor.Fans Sync: Title Exclusions"**. |
| Crew Tag ID | (empty) | The Stash tag **ID** marking crew performers. A performer with this tag is credited in Director/Photographer instead of the performers list. Empty disables crew handling. |

## Tasks

- **Sync JFF Metadata** — sync only unorganized JustFor.Fans scenes and images.
- **Full Sync JFF Metadata** — re-sync everything, ignoring the organized flag.
- **Tag JFF From Text** — additive tag pass over ALL JFF scenes, images **and
  galleries**: the `JustFor.Fans` tag, the post's hashtags, paid/pinned, plus any
  tags matched from post text. Only *adds* tags; never changes any other field.
- **Update Crew** — re-apply only the crew logic, leaving everything else
  untouched. Use after tagging new performers as crew.
- **Sync Performer** — a full re-sync scoped to a **single** performer, triggered
  by the **"Sync JustFor.Fans" button** added to each performer's page. The
  performer's Stash display name doesn't need to equal the JFF username — the
  plugin maps it back via the performer's name *and aliases*.

### Crew (directors / photographers)

Make a tag in Stash for crew, note its ID from the tag page URL (e.g.
`.../tags/42` → `42`), set that as the *Crew Tag ID*, and apply the tag to their
performer. On any sync (or the **Update Crew** task) the plugin then puts their
name in the scene **Director** and image **Photographer** fields instead of
adding them as a performer. This applies to the creator and to anyone they credit
as a collaborator. The studio always follows where the media was sourced from,
and if a post credits only crew the creator is still added as a performer so the
media is never left empty.

On **galleries** crew are instead **kept as linked performers**, because Stash's
Director/Photographer fields are free text with no link back to a performer.

### Title exclusions

Some creators prefix every title with boilerplate (e.g. `New collab: <the real
title>`). **Title Exclusions** is a list of phrases/regexes stripped from the
generated **title** — and only the title; the description keeps the original post
text. Edit it from **Settings → Tools → "JustFor.Fans Sync: Title Exclusions"**.
Each entry is a case-insensitive regex (a plain phrase works too), leftover
separators are tidied, a title is never left empty, and an invalid regex is
ignored with a warning rather than breaking the sync.

### Studio logo

The plugin ships `justforfans.png` and uses it as the image for the per-creator
studios it creates, the same way of-stash-sync uses the OnlyFans logo.

Stash only accepts an image when a studio is **created**, so studios made before
the logo shipped would otherwise stay blank. The sync therefore also **back-fills**
it: any per-creator studio that Stash reports as having no image of its own gets
the logo set once. A studio with an image already — including one you picked
yourself — is never touched. Swap the logo by replacing `justforfans.png` in this
plugin's folder.

The **parent** studio is yours (the plugin only looks it up, never creates it), so
set its image by hand if you want one.

## Notes

- Databases are opened **read-only**, so running the scraper at the same time is
  safe.
- Post text is read from the `posts`, `stories`, `messages`, `others` and
  `products` tables.
- `jff_posts` is bulk-loaded once per creator, so per-post lookups (URL, tier,
  hashtags, pinned) are dict hits rather than a query each.
