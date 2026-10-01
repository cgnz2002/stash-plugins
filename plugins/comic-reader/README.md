# Comic Reader

A proper comics experience inside Stash. Comics get their own **Comics**
section, a reader built for them, and a **Comics** tab on every performer and
studio that has any. They stop cluttering the Galleries and Images pages.

- **Paged comics** read as side-by-side spreads: the cover on its own, then
  pairs. A double-page image always gets the screen to itself. Narrow or
  portrait screens show one page at a time.
- **Webtoons** read as one continuous vertical scroll with no gaps.
- **`.cbz` files** are comics automatically, the moment Stash scans them.
- **Raw image galleries** become comics with one click: **Mark as comic** on
  the gallery's page.
- **Comics posted one page per post** can be assembled with **New comic from
  images**.

## Install

Add this repository as a plugin source (Settings → Plugins → Available Plugins
→ Add Source):

```
https://cgnz2002.github.io/stash-plugins/main/index.yml
```

Install **Comic Reader**, then reload the page.

## First run

Settings → Tasks → Plugin Tasks → **Set Up Comics**. It:

1. creates the `Comic`, `Webtoon` and `Comic Page` tags (or reuses tags you
   already have with those names)
2. marks every `.cbz` gallery as a comic
3. tags the pages of every comic
4. hides comics from the Galleries and Images pages, including the Galleries
   and Images tabs on performer and studio pages

It is safe to run again at any time. It only adds what is missing, so it
doubles as a repair. It never re-marks a `.cbz` you said is not a comic, and
if you chose **Show Comics in Galleries and Images** it leaves them showing.
The same task is in the Comics page's **…** menu as **Set up / repair comics**.

## Using it

**Comics section.** Choose **Comics** in the navigation bar. Like Stash's own
items, it can be shown or hidden under **Settings → Interface → Menu items**.
It is switched on once when the plugin is installed; after that your choice
stands.

The Comics page is Stash's own gallery list, so it works like every other
list: search, the filter button, saved filters and **Set as default**, sort,
per page, the **…** menu, zoom and selection. Its default filter is its own,
separate from the Galleries page. Each comic shows as a portrait cover with
its page count, a **Webtoon** badge where it applies and a **Read** (or
**Continue**) button on hover. The bar under a cover shows how far you have
read, a **Read** badge marks one you finished, and **3 new** marks pages added
since you last read it.

**Continue reading.** The comics you are part-way through sit in a row at the
top of the Comics page, most recent first. The **×** on a card takes it out
of the row without losing your place; it comes back the next time you read
it.

**Your place is saved on the comic itself**, so it is the same on your phone,
tablet and computer. It is stored in the gallery's custom fields, where you
can see it in Stash: `comic_page` (the page you're on), `comic_finished`,
`comic_read_at` and `comic_seen` (how many pages it had then). Saving it
updates the gallery's **Updated** date, so sorting Galleries by Updated puts
recently read comics first.

**Series.** A series ties comics together in release order (by date, then
title), whether a creator posts a chapter per post (each chapter is its own
gallery) or keeps adding pages to one comic.

- Put a comic in a series from the reader's details panel, from its gallery
  page (**Add to series**), or by selecting comics on the Comics page and
  choosing **… → Add to series**. Type a new name or pick an existing one.
- The **Series** tab lists every series, the ones you read most recently
  first, with how many of each you have read.
- A series' page has a **Continue** button (the comic you are part-way
  through, else the next one you haven't finished) and the series' comics,
  oldest first. Its sort and filters are Stash's, with their own default.
- At the end of a comic, **Next in** *series* opens the next one. The details
  panel has **Previous** and **Next** too.
- A series is a tag under `Comic Series`, so **Edit series in Stash** (the tag
  page) is where you rename it or give it a cover image or description.

**Performer and studio pages.** Performers and studios with comics get a
**Comics** tab, with the same list. A parent studio, such as your Patreon
network studio, shows every comic of the studios under it.

**Marking a gallery.** Open the gallery and use **Mark as comic** (side-by-side
pages) or **Mark as webtoon** (scroll). **Not a comic** undoes it and returns
the gallery to Galleries. To mark many at once, select them on the Galleries
page and bulk-edit the `Comic` tag onto them. The plugin tags their pages
automatically.

Unmarking a `.cbz`, by the button or by removing its tag, is remembered: the
gallery gets a `not_comic` custom field, and nothing marks it again. Mark it as
a comic to clear that.

**Comics posted one page per post.** Comics → **New comic from images**:

1. Pick the creator, and optionally search by title (e.g. `Moon Quest`).
2. Click pages to pick them. Shift-click picks a whole run.
3. Check the title (it is suggested from the page titles) and choose Pages or
   Webtoon scroll.
4. **Create comic** opens it in the reader.

Picking order doesn't matter: pages are read in file order (for Patreon that
is release order, because post folders start with the post id), and the
reader's **Pages** view can change it. "Hide pages already in a comic" keeps
the list short as you work through a series.

### Reader controls

| | Pages | Scroll |
|---|---|---|
| Next / previous | → / ←, Space, PageDown / PageUp, click the right / left third, swipe | scroll, Space, arrow keys |
| First / last page | Home / End | Home / End |
| Show / hide toolbars | click the middle, or move the mouse | tap |
| Details panel | I, or the ⓘ button | I, or the ⓘ button |
| O-count | the O button (Stash's own), for the page on screen | the same, for the page you're on |
| Pages (overview, order) | G, or the ▦ button | G, or the ▦ button |
| Full screen | F | F |
| Leave | Esc or Back | Esc or Back |

- **Pages / Scroll** switches the layout. The choice is saved on the gallery:
  Scroll adds the `Webtoon` tag and Pages removes it, so it is visible in Stash
  and the same on every device.
- **Auto / 1 / 2** chooses spreads. Auto shows two pages on a wide screen and
  one on a narrow one.
- **− / +** in scroll mode sets the strip width.
- **The O button** counts on the page in view. Stash keeps O counts on
  images, not galleries, so it is the page that gets it (you see it on that
  image in Stash). With two pages on screen it asks which one. It follows
  Stash's SFW mode (a thumbs-up), and like Stash's own button its arrow has
  Decrement and Reset.
- **The end** of a comic has **Rate it**, the same rating as the details
  panel.

### Page order

Pages are read in **file order**: by folder and file name, naturally (`2`
before `10`). For Patreon that is the creator's order: patreon-dl numbers a
post's files in the order they were posted, and post folders start with the
post id, so a comic gathered from several posts reads in release order.
(Dates aren't the default: a comic shared in one post gives every page the
same date.)

When a comic is still out of order, open **Pages** (G):

- Every page is shown with its number, file name, date and, for a comic
  gathered from several posts, the post folder it came from, so you can see
  why a page sits where it does. Tap a page to go to it.
- **Order by** switches that comic between **File path** (the default),
  **Date** (post date, then file path) and **Custom**.
- **Reorder** lets you drag pages into place (or use the arrows on a touch
  screen). **Save order** keeps it on the comic, on every device; pages added
  later go after it. Switching back to File path keeps your custom order, so
  Custom brings it back.

The choice is stored on the gallery as custom fields: `comic_order` and
`comic_page_order` (the page ids, in order).
- **Details** shows the comic's studio, title, date, page count, rating,
  description, performers, tags and links, like the panel on a gallery page.
  Rate it right there. A performer or studio opens their **Comics** tab, a tag
  opens the Comics page filtered to that tag, and **Open gallery in Stash** is
  there for editing and file details. On a wide screen it sits beside the
  pages and stays as you last left it; on a phone it is a sheet over the page
  that starts closed (tap above it or press the X to close it).

A comic with just the `Comic` tag whose pages are tall strips opens as a scroll
anyway. If one is mis-detected, press **Pages** and that comic stays paged.

## How it works

Everything is plain Stash tags, so you can see and edit it:

| Tag | Means |
|---|---|
| `Comic` | this gallery is a comic |
| `Webtoon` | (under `Comic`) read it as a scroll |
| `Comic Page` | (under `Comic`) this image is a page of a comic. The plugin keeps this one up to date, so you never need to add it. |
| `Comic Series` | (under `Comic`) each tag under it is a series; its comics are the galleries tagged with it |

`Webtoon` and `Comic Page` sit under `Comic`, so tagging a gallery `Webtoon`
alone is enough, and one filter rule ("tags exclude Comic, including sub-tags")
hides every comic and every comic page. You can nest your own tags under
`Comic` too, and they count as comic tags.

The plugin stores these tags' ids in its settings, so renaming a tag doesn't
break anything. They are created with **Ignore Auto Tag** on. Otherwise Stash's
Auto Tag task would turn anything under a folder called `comics` into a comic.

Comics are still galleries underneath. **Show Comics in Galleries and Images**
removes only the comic exclusion from those pages' default filters, and the
rest of each filter is left as you set it.

## Settings

| Setting | |
|---|---|
| Comic Tag ID / Webtoon Tag ID / Comic Page Tag ID / Comic Series Tag ID | Filled in automatically. Change them only to point the plugin at different tags. |
| Show Comics in Galleries and Images | Off by default: comics leave those pages. The Hide/Show tasks switch it and apply it at once; if you change it here, run Set Up Comics. |
| Webtoon Page Ratio | How tall a page must be, as a multiple of its width, before an untagged comic auto-opens as a scroll. Default 2. |

## Notes

- **Other plugins.** Comic Reader shares the navigation bar and the performer,
  studio and gallery pages with other plugins, and is tested alongside Stash
  TV, Binge, role-tagger, refract and others. If any part of it ever fails, it
  hides just that part; the rest of Stash keeps working. (0.1.0 could blank
  Stash when Stash TV was installed; fixed in 0.1.1.)

- **PDFs.** Stash can't read PDFs. Convert them to `.cbz` and put them in a
  library folder; Stash picks them up on the next scan and they arrive as
  comics.
- **Fan Site Metadata Sync** knows about these tags: its Full Sync keeps
  `Comic` and every tag under it, so re-syncing your Patreon library never
  un-marks a comic.
- **Themes.** The plugin is built from Stash's own cards, toolbar, buttons,
  toasts and image-viewer backdrop, so Stash themes and your Custom CSS apply
  to it too.
- **Adding pages with Stash's own gallery Add tab** (or "Remove from
  gallery") doesn't notify plugins. The new pages are hidden from Images the
  next time the comic or its gallery page is opened, or on **Set Up Comics**.
  Moving images through image edit is picked up straight away.
- The Comics tab and the gallery-page buttons are added to Stash's pages
  directly, because Stash v0.31 has no plugin hook for either spot. A future
  Stash release may need a small update to their placement.
