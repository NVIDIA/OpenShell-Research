---
name: docs-visual-review
description: Use for docs visual audits, layout, typography, heroes, CSS, navigation, or interaction changes. Skip prose-only edits.
---

# Documentation visual review

Run commands from the repository root. Read `docs/development/index.md` and any
applicable `AGENTS.md` instructions before changing the site.

## Choose pages and checks

Review the pages affected by the change and representative pages that share their
styles or templates. Include both `default` and `slate` themes and desktop,
tablet, and phone widths. Check Chromium and WebKit for affected surfaces.

Use the existing browser suite, `tests/test_dev_notes_layout.py`, for layout and
interaction checks. It discovers current Dev Notes and saves screenshots. Select
relevant tests with pytest's `-k`; run the full suite for a site-wide audit or a
shared layout change. Useful selections include:

- Index and featured cards: `index_reading_proportions or featured_image_also_works_as_a_thumbnail`.
- Articles and heroes: `post_reading_proportions`.
- Filters and byline links: `filters or bylines or without_javascript`.
- Shared navigation and search: `shared_documentation_navigation or search_and_saved_navigation`.
- Images, transcripts, or video: select the corresponding test names with `--collect-only -q`.

For a single article, use `--collect-only -q` to find its parametrized test ID,
then select its filename with `-k`.

## Build and inspect

Build before running browser tests; they read the generated `site/` directory:

```sh
scripts/build-docs.sh
```

Install the browsers if they are not already available:

```sh
uv run --locked --script tests/test_dev_notes_layout.py --install-browser
```

Run focused checks during iteration, then validate the affected surfaces across
the complete browser, theme, and viewport matrix. For example:

```sh
uv run --locked --script tests/test_dev_notes_layout.py -q -k index_reading_proportions
```

For a site-wide audit:

```sh
uv run --locked --script tests/test_dev_notes_layout.py -q
```

The suite serves `site/` locally and writes screenshots to
`.cache/dev-notes-layout/`. Inspect screenshots from the current run; older files
may still be present. The suite blocks external requests, so inspect relevant
external avatars or embeds separately.

Open the affected pages in a browser and interact with them. To serve the built
site manually:

```sh
python3 -m http.server 8000 --directory site
```

Inspect the rendered pages, not just test results. Check visual hierarchy,
readability, spacing, image detail and proportions, theme contrast, clipping,
overlap, and horizontal overflow. Confirm that tables and navigation work at
narrow widths. On the Dev Notes index, confirm that the featured card is
prominent, the archive scrolls naturally, and category and author filters work
together, clear correctly, and survive refresh and browser history. On articles,
check that the title, subtitle, and complete hero fit the opening viewport, images
keep their proportions, and diagram labels remain legible at full size. Exercise
mobile navigation, search, transcripts, and video where relevant.

If you make a fix, change the source styles, templates, or metadata, rebuild,
and rerun the affected checks. Report the pages, themes, widths, and browsers
reviewed, the screenshots inspected, and any surfaces or checks you could not
verify.
