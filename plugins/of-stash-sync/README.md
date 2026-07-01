# OnlyFans Metadata Sync (Stash plugin)

A native [Stash](https://stashapp.cc) plugin that syncs metadata scraped by
[OF-Scraper](https://github.com/datawhores/OF-Scraper) into matching Stash
scenes and images. It reads OF-Scraper's `user_data.db` files and writes title,
details, date, URL, performers and studio onto the corresponding media, then
marks them organized.

This is a native re-implementation of
[`timekillerj/ofscraper-stash-sync`](https://github.com/timekillerj/ofscraper-stash-sync),
moved inside Stash so there is no separate container or `config.ini`.

## What it does

For every `user_data.db` found under the configured data path, and for each
creator profile in it:

- Resolves (or creates) a per-creator studio `<username> (OnlyFans)` as a child
  of the configured parent studio (default `OnlyFans (network)`).
- Finds that creator's Stash scenes and images by file path and matches each
  file back to the OF-Scraper database by filename.
- Sets **title** and **details** from the post text, **date** from the OF post
  date, **studio**, **code** (OF media id), **URL** to the original post, and
  **performers** (the creator plus any `@mentioned` accounts).
- Credits anyone tagged as **crew** (see *Crew Tag Name*) in the scene
  **Director** / image **Photographer** field instead of the performers list.
- Optionally tags media `paid` / `archived`.
- Marks each synced item **organized** so the normal sync skips it next time.

Both current and older OF-Scraper database layouts are supported.

## Requirements

- Stash v0.31.x (verified against v0.31.1).
- No Python dependencies. The plugin uses only the Python standard library, so
  nothing needs to be installed into the Stash container.
- The parent studio (default `OnlyFans (network)`) must already exist in Stash.
- Performers should already exist with the OF username as their name or an
  alias (unless you enable *Create Missing Performers*).

## Installation

1. Copy the `of-stash-sync` folder into Stash's `plugins` directory
   (`<stash config>/plugins/of-stash-sync`).
2. In Stash, go to **Settings -> Plugins** and click **Reload Plugins**.
3. Configure the plugin settings (see below). At minimum set the data path.

## Settings

| Setting | Default | Description |
|---|---|---|
| OF-Scraper Data Path | (required) | Directory searched recursively for `user_data.db` files, as seen inside the Stash container (e.g. `/data/only fans`). |
| Parent Studio Name | `OnlyFans (network)` | Top-level studio that per-creator studios are nested under. |
| Max Title Length | `65` | Titles longer than this are truncated at a sentence or word boundary. |
| Allow Multiple Performer Matches | off | If several performers match a username, attach all of them instead of skipping. |
| Create Missing Performers | off | Create a sparse performer for the creator and any unmatched `@mentions` instead of skipping. |
| Auto-tag From Post Text | off | Scan each post's text and attach any existing Stash tags whose name or alias appears in it. |
| Skip Multi-file Scenes and Images | off | Sync and Full Sync skip any scene or image with more than one file (e.g. merged scenes), to protect their performers and metadata. Does not affect the Tag task. |
| Crew Tag ID | (empty) | The Stash tag **ID** (from the tag's URL, e.g. `.../tags/42` → `42`) marking crew performers. A performer with this tag has their name put in each scene's Director field and each image's Photographer field instead of the performers list. Applies to the creator and any `@mentioned` collaborator. Empty disables crew handling. |

## Tasks

Run these from **Settings -> Tasks** (or schedule them). Scan your library in
Stash first so the scenes and images exist.

- **Sync OF Metadata** - sync only unorganized OnlyFans scenes and images.
- **Full Sync OF Metadata** - re-sync everything, ignoring the organized flag.
- **Tag OF From Text** - add tags from post text to ALL OnlyFans scenes and
  images (organized or not). This only *adds* tags; it never changes title,
  details, date, performers, studio or any other field, and never removes
  existing tags. Use this to tag media without a full re-sync overwriting manual
  edits. It always matches tags from text regardless of the *Auto-tag From Post
  Text* setting.
- **Update Crew** - re-apply only the crew logic to ALL OnlyFans scenes and
  images: move crew-tagged people (see *Crew Tag Name*) into the Director /
  Photographer field and out of the performers list. It leaves titles, dates,
  studios, tags and everything else untouched, skips media that already match,
  and never creates performers. Use it after tagging newly generated performers
  as crew, instead of a full re-sync.

The settings toggles above appear alongside these task buttons and apply to the
sync tasks.

### Crew (directors / photographers)

Some creators you follow are directors or photographers rather than the on-screen
talent. Make a tag for them in Stash (any name), note its ID from the tag page
URL (e.g. `.../tags/42` → `42`), set that as the *Crew Tag ID*, and apply the
tag to their performer. On any sync or the **Update Crew** task the plugin then
puts their name in the scene **Director** and image **Photographer** fields
instead of adding them as a performer. This applies both to the creator whose
database is being read and to anyone they `@mention` (e.g. a guest director on a
performer's own page). Matching is by tag ID, so you can rename the tag freely.
The studio always follows where the media was sourced from, and if a post
credits only crew the creator is still added as a performer so the media is
never left empty.

## Notes

- Databases are opened read-only, so running OF-Scraper at the same time is
  safe.
- Post text is read from the `posts`, `stories`, `messages`, `others` and
  `products` tables.
- Older OF-Scraper databases (empty `profiles` table, no `medias.model_id`, date
  in `created_at`) are detected automatically; the creator name is recovered from
  the media directory path.
- **Auto-tag From Post Text** exists because Stash's built-in auto-tagger skips
  organized media, and this plugin marks synced media organized. It matches the
  post text against existing tag names and aliases using the same word-boundary
  rules as Stash's auto-tagger (separator-insensitive, case-insensitive). It
  only adds existing tags, never creates them, and skips tags set to ignore
  auto-tag. Performers and studios are unaffected.
- Credit to timekillerj for the original tool this is based on.
