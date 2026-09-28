---
name: docs-visual-review
description: Review the OpenShell Research documentation site's rendering and interactions using the existing browser layout suite and screenshots. Use for visual regressions, Dev Notes typography or hero changes, shared CSS or navigation changes, or a requested visual audit; routine documentation edits do not require it.
---

# Documentation visual review

Run commands from the OpenShell Research repository root. Read
`docs/development/index.md` and applicable `AGENTS.md` instructions first.
Browser validation is on demand, not a CI gate. Preserve the ordinary renderer,
JavaScript, and clean-build checks; do not add browser runs back to CI.

## Choose the scope

Use `tests/test_dev_notes_layout.py` and its adjacent uv lock rather than writing
another browser harness. It discovers current Dev Notes and covers Chromium and
WebKit, light (`default`) and dark (`slate`) themes, and desktop, tablet, and phone
viewports. Start with tests relevant to the change. Run the full suite when
changing shared layout or when the user requests a comprehensive review.

Examples of focused selections with pytest's `-k`:

- Index hierarchy, title, subtitle, featured card: `index_reading_proportions`.
- Hero or card changes: `post_reading_proportions or featured_image_also_works_as_a_thumbnail`.
- Filters, author links, history, no-JavaScript fallback: `filters or bylines or without_javascript`.
- Breadcrumbs, footer, mobile drawer, tables: `shared_documentation_navigation`.
- Embedded recordings: `embedded_video_playback`.

For a single note, use `--collect-only -q` to find its parametrized test IDs,
then select the relevant filename with `-k`. Keep both browsers, themes, and
screen sizes for final validation of affected surfaces. A quick single-browser
run is useful during iteration but does not establish cross-browser correctness.

## Build and run

Build the current sources before testing; browser tests read `site/` and do not
rebuild it:

```sh
scripts/build-docs.sh
```

Install browsers once per environment, or again if the locked Playwright version
changes. This installs Chromium, WebKit, and their system dependencies:

```sh
uv run --locked --script tests/test_dev_notes_layout.py --install-browser
```

For example, check the index across the browser/theme/viewport matrix:

```sh
uv run --locked --script tests/test_dev_notes_layout.py -q -k index_reading_proportions
```

For a comprehensive review:

```sh
uv run --locked --script tests/test_dev_notes_layout.py -q
```

The full suite can take about six minutes. It starts and stops its own local HTTP
server. Screenshots from page-fixture tests are saved to
`.cache/dev-notes-layout/`, with test and parameter names in the filenames.
Inspect files produced by the current run; other screenshots may be stale.
External requests are blocked for deterministic checks, so separately inspect
external avatars or embeds when those are relevant to the task.

## Inspect the rendered result

Open the relevant screenshots with an available image-viewing tool. Passing
geometry assertions does not establish visual quality or diagram-label
readability. For a visual audit, also serve the complete built site and interact
with the affected pages in an available browser:

```sh
python3 -m http.server 8000 --directory site
```

Reuse an existing artifact preview when available. On a remote machine,
localhost alone is not a user-accessible preview; report a verified forwarded or
published preview URL if the user needs to view it.

Judge the result against these established design decisions:

- The Dev Notes masthead has a prominent serif title and readable subtitle with
  breathing room. Divider lines separate filters from the introduction and
  posts. The featured note is larger than archive entries; the archive scrolls
  naturally rather than shrinking or acquiring its own scroll panel.
- Article titles, subtitles, and complete heroes fit the opening viewport using
  shared styles. Heroes preserve their proportions without cropping, stretching,
  overflowing figures, or overlapping the byline. Inspect both theme variants
  and the full-size asset for detailed diagrams. Author thumbnails stay left;
  sidebar post links contain titles only.
- Text columns remain readable, phones have no page-wide horizontal overflow,
  tables scroll within the article, and navigation labels do not clip or overlap.
- Exercise category and author combinations, empty results, clear filters,
  refresh, Back/Forward, and byline links when filtering changes. Check mobile
  navigation and video playback when those surfaces change.

Fix shared styles, templates, or source metadata rather than hand-editing
renderer-owned HTML or adding per-post sizing overrides. Rebuild after changes
and rerun affected checks. Do not weaken assertions solely to silence failures;
update a constraint only when the requested design has intentionally changed.

Report the tested pages and scope, browser results, screenshots inspected, and
any unverified surfaces or blocked checks. Do not claim a visual review based
only on passing tests or test collection.
