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
doubles as a repair. The same task is also the **Set up / repair** button on
the Comics page.

## Using it

**Comics section.** Choose **Comics** in the navigation bar. Comics are
grouped by creator. You can search, filter to comics or webtoons, sort, and
switch grouping off. A blue bar under a cover shows how far you have read in
this browser.

**Performer and studio pages.** Performers and studios with comics get a
**Comics** tab. A parent studio, such as your Patreon network studio, shows
every comic of the studios under it.

**Marking a gallery.** Open the gallery and use **Mark as comic** (side-by-side
pages) or **Mark as webtoon** (scroll). **Not a comic** undoes it and returns
the gallery to Galleries. To mark many at once, select them on the Galleries
page and bulk-edit the `Comic` tag onto them. The plugin tags their pages
automatically.

**Comics posted one page per post.** Comics → **New comic from images**:

1. Pick the creator, and optionally search by title (e.g. `Moon Quest`).
2. Click pages to pick them. Shift-click picks a whole run.
3. Check the title (it is suggested from the page titles) and choose Pages or
   Webtoon scroll.
4. **Create comic** opens it in the reader.

Picking order doesn't matter. Pages are always read in post-date order, then
by file name. "Hide pages already in a comic" keeps the list short as you work
through a series.

### Reader controls

| | Pages | Scroll |
|---|---|---|
| Next / previous | → / ←, Space, PageDown / PageUp, click the right / left third, swipe | scroll, Space, arrow keys |
| First / last page | Home / End | Home / End |
| Show / hide toolbars | click the middle, or move the mouse | tap |
| Full screen | F | F |
| Leave | Esc or Back | Esc or Back |

- **Pages / Scroll** switches the layout. The choice is saved on the gallery:
  Scroll adds the `Webtoon` tag and Pages removes it, so it is visible in Stash
  and the same on every device.
- **Auto / 1 / 2** chooses spreads. Auto shows two pages on a wide screen and
  one on a narrow one.
- **− / +** in scroll mode sets the strip width.
- The reader remembers your page per comic in this browser, and starts over
  once you finish a comic.

A comic with just the `Comic` tag whose pages are tall strips opens as a scroll
anyway. If one is mis-detected, press **Pages** and that comic stays paged.

## How it works

Everything is plain Stash tags, so you can see and edit it:

| Tag | Means |
|---|---|
| `Comic` | this gallery is a comic |
| `Webtoon` | (under `Comic`) read it as a scroll |
| `Comic Page` | (under `Comic`) this image is a page of a comic. The plugin keeps this one up to date, so you never need to add it. |

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
| Comic Tag ID / Webtoon Tag ID / Comic Page Tag ID | Filled in automatically. Change them only to point the plugin at different tags. |
| Webtoon Page Ratio | How tall a page must be, as a multiple of its width, before an untagged comic auto-opens as a scroll. Default 2. |

## Notes

- **PDFs.** Stash can't read PDFs. Convert them to `.cbz` and put them in a
  library folder; Stash picks them up on the next scan and they arrive as
  comics.
- **Fan Site Metadata Sync.** A Full Sync of that plugin replaces tag lists
  unless *Keep Manual Performers & Tags* is on, which would remove the comic
  tags. Turn that setting on until the sync plugin learns to keep comic tags.
  Afterwards, **Set Up Comics** restores any page tags that were removed.
- The Comics tab and the gallery-page buttons are added to Stash's pages
  directly, because Stash v0.31 has no plugin hook for either spot. A future
  Stash release may need a small update to their placement.
