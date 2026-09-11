# Fan Site Metadata Sync (Stash plugin)

A native [Stash](https://stashapp.cc) plugin that syncs scraped fan-site
metadata into matching Stash scenes and images. It reads the `user_data.db`
files written by **[OF-Scraper](https://github.com/datawhores/OF-Scraper)**
(OnlyFans) and **jff-scraper** (JustFor.Fans) and writes title, details, date,
post URL, performers, studio and tags onto the corresponding media, then marks
them organized.

**One task covers every site.** Which site a database belongs to is read from
the database itself, so you don't pick a mode — point the plugin at each
library's path and run one sync. See [Multiple sites](#multiple-sites).

The OnlyFans half is a native re-implementation of
[`timekillerj/ofscraper-stash-sync`](https://github.com/timekillerj/ofscraper-stash-sync),
moved inside Stash so there is no separate container or `config.ini`.

## Multiple sites

Set the data path for each site you have (leave the other blank) and everything
else is shared — one Sync, one Full Sync, one set of tags/crew/title rules.

Each database declares its own site, so a path holding both kinds of library
sorts itself out and nothing has to be configured twice. What differs per site
is only what has to:

| | OnlyFans | JustFor.Fans |
|---|---|---|
| Post URL | rebuilt from the post id | read from the link the scraper captured (JFF URLs carry an encoded key and can't be rebuilt) |
| Studio | `<user> (OnlyFans)` | `<user> (JustForFans)` |
| Site tag | `OnlyFans` | `JustFor.Fans` |
| Paid content | post price > 0 | the scraper's Free/**Paid** tier (JFF has no price, so a price rule would never fire) |
| Scene `code` | filename stem (OF-Scraper names files by media id) | the post id (jff-scraper names files `<date> - <post id> - <desc>`) |
| Extra tags | — | the post's own hashtags, and `pinned` |

The same creator on both sites gets a separate studio for each, and both are
synced in one run.

## What it does

For every `user_data.db` found under the configured data path, and for each
creator profile in it:

- Resolves (or creates) a per-creator studio for that site (`<username>
  (OnlyFans)` / `<username> (JustForFans)`) as a child of that site's parent
  studio.
- Finds that creator's Stash scenes and images by file path and matches each
  file back to the scraper database by filename.
- Sets **title** and **details** from the post text, **date** from the post
  date, **studio**, **code**, **URL** to the original post, and **performers**
  (the creator plus any collaborators credited in the post text, whether
  `@mentioned` or linked by a bare profile URL like `onlyfans.com/username` or
  `justfor.fans/username`).
  A collaborator the plugin has to create gets the profile URL of the site
  they were actually linked on — so a JustFor.Fans post plugging someone's
  OnlyFans creates that performer with their OnlyFans URL, not a JustFor.Fans
  one. A bare `@mention` names no site, so it uses the site being synced.
- Credits anyone tagged as **crew** (see *Crew Tag Name*) in the scene
  **Director** / image **Photographer** field instead of the performers list.
- Drops anyone tagged as a **sponsor** (see *Sponsor Tag ID*) from the performers
  list and tags the media `sponsored` instead.
- Groups each post's media into a **gallery** (see below).
- Tags every synced scene, image and gallery with that site's tag (`OnlyFans` /
  `JustFor.Fans`, created if missing), so each site's media is filterable by one
  tag.
- Optionally tags media `paid` / `archived` / `sponsored`.
- Marks each synced item **organized** so the normal sync skips it next time.

### Post galleries

On the **Sync** and **Full Sync** tasks, the plugin groups a creator's media by
post id and makes one **gallery** per post, carrying the same metadata (title,
details, date, studio, performers, tags, URL) as the post's scenes/images. A
gallery is created when a post has **2+ images**, or **an image alongside a
video** — because Stash relates scenes to galleries (not to images), so the
gallery is how a post's stills get linked to its video: the gallery's scene link
points at the post's scene. Galleries are keyed by the post URL, so re-runs don't
duplicate them; a plain Sync creates missing galleries and adds any new images
(never overwriting a gallery you've edited), while Full Sync also refreshes their
metadata.

### Folder galleries (scanned image folders)

When Stash scans a folder of images it creates a **gallery for that folder**.
Those arrive with no performer, no studio and just the folder name for a title,
so the Sync and Full Sync tasks adopt each of a creator's folder galleries:

- **Title** → `<creator> OnlyFans Images (<category>)`, e.g.
  `jake_od OnlyFans Images (Posts/Free)`. The category comes from the path
  between the creator's folder and the media folder — without it every one of a
  creator's image folders (Posts/Free, Posts/Paid, Messages/Free, Archived/...)
  would end up with the same title.
- **Studio** → the creator's `<username> (OnlyFans)` studio.
- **Performer** → the creator, *added* to whoever is already on the gallery.
- **Tag** → the `OnlyFans` tag, *added* to whatever tags are already there.

These are kept strictly apart from the per-post galleries above: a folder
gallery has a `folder` in Stash's schema and a plugin-made one does not, so the
two can never be confused.

Performers and tags here are **only ever added** — a folder isn't a post, so
there's no authoritative cast to replace the gallery's with, and anything you
curated by hand survives. Only the title and studio are asserted, and those
follow the usual organized rule: a plain **Sync** leaves organized folder
galleries alone, a **Full Sync** refreshes them all. A gallery that already
matches isn't rewritten at all.

Both current and older OF-Scraper database layouts are supported.

## Requirements

- Stash v0.31.x (verified against v0.31.1).
- No Python dependencies. The plugin uses only the Python standard library, so
  nothing needs to be installed into the Stash container.
- The parent studio (default `OnlyFans (network)`) must already exist in Stash.
- **For large libraries, set an API key** in Stash **Settings → Security** (the
  plugin picks it up automatically). A big Full Sync can run long enough to
  outlive Stash's session cookie, which then returns `HTTP 401` on every
  remaining request and fails the creators processed near the end. The API key
  doesn't expire, so the plugin uses it in preference to the cookie and the run
  completes. Without one it falls back to the cookie and logs a warning.
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
| OnlyFans Data Path | (one path required) | Directory searched recursively for `user_data.db` files, as seen inside the Stash container (e.g. `/data/only fans`). |
| OnlyFans Parent Studio | `OnlyFans (network)` | Top-level studio that per-creator studios are nested under. |
| JustFor.Fans Data Path | (blank) | Directory searched recursively for jff-scraper `user_data.db` files (e.g. `/data/justforfans`). Leave blank if you have no JFF library. |
| JustFor.Fans Parent Studio | `JustForFans (network)` | Top-level studio for JustFor.Fans creators. Must already exist in Stash. |
| Max Title Length | `65` | Titles longer than this are truncated at a sentence or word boundary. |
| Allow Multiple Performer Matches | off | If several performers match a username, attach all of them instead of skipping. |
| Create Missing Performers | off | Create a sparse performer for the creator and any unmatched `@mentions` instead of skipping. |
| Keep Manual Performers & Tags | off | Sync is **non-destructive** to performers and tags: it **merges** them instead of replacing, so anything you added by hand survives a Full Sync (or the per-performer button), while the post's creator/@mentions and tags are added alongside. Everything else (title, details, date, studio, URL) is still updated. Crew are still moved out of the performers list on scenes/images. Turn this on if you manually curate performers/tags. |
| Sync Workers | `2` | How many scene/image/gallery writes to send to Stash at once. Stash is SQLite-backed and SQLite has a **single writer**, so parallel writes queue on the DB write lock rather than truly committing at once; a little concurrency hides latency but too much piles up transactions until they time out (and starves the rest of Stash). Writes retry automatically on transient "database is locked" / "FOREIGN KEY constraint" / "timed out" contention. **If you see those errors on a big Full Sync, lower this** — `1` (fully sequential) is safest. Range 1–16. |
| Auto-tag From Post Text | off | Scan each post's text and attach any existing Stash tags whose name or alias appears in it. |
| Skip Multi-file Scenes and Images | off | Sync and Full Sync skip any scene or image with more than one file (e.g. merged scenes), to protect their performers and metadata. Does not affect the Tag task. |
| Title Exclusions | (empty) | Phrases/regexes stripped from generated **titles** only (the description keeps the original post text). Best edited via **Settings → Tools → "Fan Site Sync: Title Exclusions"** (a list editor like Stash's scan Exclusions). See below. |
| Crew Tag ID | (empty) | The Stash tag **ID** (from the tag's URL, e.g. `.../tags/42` → `42`) marking crew performers. A performer with this tag has their name put in each scene's Director field and each image's Photographer field instead of the performers list. Applies to the creator and any collaborator credited by `@mention` or profile URL (`onlyfans.com/username`). Empty disables crew handling. |
| Sponsor Tag ID | (empty) | The Stash tag **ID** marking **sponsor** performers (brands/advertisers). A performer with this tag is **removed** from the performers list and the scene/image/gallery is tagged `sponsored` instead — Stash has no field to credit a sponsor in. Unlike crew, sponsors are dropped from galleries too. Applies to the creator and any collaborator credited by `@mention` or profile URL. Empty disables sponsor handling. See below. |

## Tasks

Run these from **Settings -> Tasks** (or schedule them). Scan your library in
Stash first so the scenes and images exist.

- **Sync Metadata** - sync only unorganized scenes and images, across every configured site.
- **Full Sync Metadata** - re-sync everything, ignoring the organized flag.
- **Tag From Text** - add tags to ALL synced scenes, images **and galleries**
  (organized or not): the `OnlyFans` tag plus any tags matched from the post text.
  This only *adds* tags; it never changes title, details, date, performers, studio
  or any other field, and never removes existing tags. Use this to tag media
  without a full re-sync overwriting manual edits. It always matches tags from
  text regardless of the *Auto-tag From Post Text* setting.
- **Sync Performer** - a full re-sync scoped to a **single** performer. You don't
  run this from the Tasks page; instead a **"Sync Fan Sites" button** is added to
  each performer's page (via the plugin's UI JavaScript). Clicking it re-syncs
  just that performer -- handy for fixing one creator's titles/details or
  rebuilding their galleries without a library-wide Full Sync. The performer's
  Stash **display name doesn't need to equal the OF username**: the plugin maps
  the performer back to its OF username via the performer's name *and aliases*, so
  as long as the OF username is set as the performer's name or an alias (as usual),
  the button finds the right creator.
- **Update Crew & Sponsors** - re-apply only the crew and sponsor logic to ALL
  synced scenes and images: move crew-tagged people (see *Crew Tag ID*) into the
  Director / Photographer field and out of the performers list, and drop
  sponsor-tagged accounts (see *Sponsor Tag ID*) from it in favour of a
  `sponsored` tag. It leaves titles, dates, studios and everything else
  untouched (the only tag it ever changes is adding `sponsored`), skips media
  that already match, and never creates performers. Use it after tagging newly
  generated performers as crew or sponsors, instead of a full re-sync.

The settings toggles above appear alongside these task buttons and apply to the
sync tasks.

### Crew (directors / photographers)

Some creators you follow are directors or photographers rather than the on-screen
talent. Make a tag for them in Stash (any name), note its ID from the tag page
URL (e.g. `.../tags/42` → `42`), set that as the *Crew Tag ID*, and apply the
tag to their performer. On any sync or the **Update Crew** task the plugin then
puts their name in the scene **Director** and image **Photographer** fields
instead of adding them as a performer. This applies both to the creator whose
database is being read and to anyone they credit as a collaborator (e.g. a guest
director on a performer's own page), whether by `@mention` or by a bare profile
URL such as `onlyfans.com/username`. Matching is by tag ID, so you can rename the
tag freely.
The studio always follows where the media was sourced from, and if a post
credits only crew the creator is still added as a performer so the media is
never left empty.

On **galleries**, crew are instead **kept as linked performers**. Stash's
Director/Photographer fields are free text with no link back to a performer, so
browsing their work is hard; a gallery groups a whole post, so it carries the
crew credit as a real performer link (their crew tag still distinguishes them).
The gallery's Photographer field is left empty. So: scenes and images move crew
into Director/Photographer, while the post's gallery keeps them clickable.

### Sponsors (brands and advertisers)

Creators often `@mention` or link a **brand** that sponsored a post. Stash has no
field to credit a sponsor in, and they aren't in the media, so leaving them in
the cast list is just wrong. Set up sponsors the same way as crew: make a tag,
note its ID from the tag page URL, set it as the **Sponsor Tag ID**, and apply
that tag to the brand's performer.

From then on, on any sync or the **Update Crew & Sponsors** task, a credited
sponsor is **removed from the performers list** and the media is tagged
**`sponsored`** instead. Like crew, this covers both the creator whose database
is being read and anyone they credit, by `@mention` or profile URL, and matching
is by tag ID so you can rename the tag freely.

Details worth knowing:

- **Galleries drop sponsors too** — this is the one place sponsors differ from
  crew. Crew stay linked on a gallery because their credit field loses the link;
  a sponsor has no credit field at all, so the `sponsored` tag is the whole
  record and keeping the brand in the cast would serve no purpose.
- **The `sponsored` tag is only ever added, never removed.** Un-sponsoring
  something is a manual call.
- It's **created if missing**, and an existing tag carrying `sponsored` as its
  name *or an alias* is reused rather than duplicated.
- If a post's only credits are sponsors, the creator is kept as a performer so
  the media is never left performer-less.
- A performer tagged **both** crew and sponsor gets both treatments: the
  Director/Photographer credit *and* the `sponsored` tag.
- **Keep Manual Performers & Tags** doesn't protect a sponsor: they're pruned
  even in non-destructive mode, otherwise a brand added by an older sync could
  never be cleaned out.
- Leave *Sponsor Tag ID* empty and nothing changes — sponsor handling is off and
  those accounts stay ordinary performers.

### Title exclusions (cleaning up boilerplate titles)

Some creators prefix every title with boilerplate, e.g. `New collab: <the real
title>`. **Title Exclusions** is a list of phrases/regexes that are stripped from
the generated **title** — and only the title. The **description/details keeps the
original post text**, so nothing is lost.

Edit the list from **Settings → Tools → "Fan Site Sync: Title Exclusions"**. That
opens a list editor (a row per pattern with add/remove, then **Save**). Add
`new collab:` and the title `New collab: Beach day` becomes `Beach day`, while the
description is unchanged.

How matching works:

- Each entry is a **case-insensitive regular expression** (a plain phrase like
  `new collab:` is itself a valid regex, so you don't need to know regex to use
  it). It is removed everywhere it appears in the title.
- After removals, leftover separators (`:`, `-`, `|`, `…`) and extra spaces at the
  title's edges are tidied.
- If an entry would remove the *entire* title, the original title is kept (a title
  is never left empty).
- Invalid regexes are ignored (logged as a warning), so a typo can't break a sync.
- Applies to scenes, images **and** galleries.

The list is saved into the plugin's own config; the **Title Exclusions** field on
the Settings → Plugins page is a hand-editable fallback (type one pattern, or
paste a JSON array) if you'd rather not use the editor.

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
