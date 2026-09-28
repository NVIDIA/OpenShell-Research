---
title: Documentation Site Development
description: Agent instructions for maintaining the OpenShell Research documentation site.
---

# Documentation site development

For automated dependency policy checks, see
[Dependency License Checks](dependency-licenses.md).
For repository checks and agent review workflows, see [Repository CI](ci.md).

Follow these instructions for changes under `docs/`, a project's `docs/` tree,
`zensical.toml`, the Dev Notes renderer, or the documentation workflow. Run
commands from the repository root. Use Python 3.10 or newer.

## Content routing

- Put project-specific software knowledge—installation, usage, reproducibility,
  and known limitations—under `projects/<project-type>/<name>/docs/`. The clean
  build stages tool documentation under `docs/documentation/` and linked research
  specifications under `docs/research/` for publishing.
- Keep the Documentation section for tools. Put cross-project tool documentation
  directly under `docs/documentation/`.
- Put dated experiments, benchmarks, releases, use cases, and engineering updates
  under `docs/dev-notes/`.
- When a Dev Note introduces reusable software, add its durable guide to the
  owning project, link it to the originating Dev Note, and add its staged site
  path to `zensical.toml`.

## Agent-readable Markdown

Every canonical content page under `docs/dev-notes/posts/`,
`docs/documentation/`, and a staged project documentation tree must declare
`agent_markdown: true` in its front matter. The clean site build first stages
project documentation, then copies all published Markdown sources byte-for-byte
into `site/` at their site paths. Each rendered page links to its same-origin
Markdown source for people and agents. Generated copies under
`docs/documentation/`, staged project trees under `docs/research/`, and `site/`
must not be edited. Research supplements opt in with the same marker; legacy
research redirects do not.

Every page in a published content tree is included, including the Documentation
index. Keep presentation-only landing pages such as the homepage and Dev Notes
card index, redirect-only pages, obsolete or orphan project pages, internal
development documentation, and the 404 page outside those trees and do not add
the marker to them. Those pages are not canonical content for agent
consumption.

## Dev Notes

Dev Notes are Markdown posts under `docs/dev-notes/posts/`. Each post requires
`title`, an exact `YYYY-MM-DD` date, `description`, and at least one `authors` ID
defined in `docs/dev-notes/authors.json`. Use a dated filename such as
`YYYY-MM-DD-short-title.md`.

Every post must list exactly one category under `categories`, using one of these
fixed names:

- **Announcements**: releases, launches, and team or project updates.
- **Research**: experiments, benchmarks, findings, and technical investigations.
- **Case Studies**: accounts of real-world adoption, deployment, and outcomes.
- **Examples**: practical walkthroughs and demonstrations readers can reproduce.

The renderer validates this list before updating any generated content. Missing,
unknown, or multiple categories fail the build with an author-facing error.
Categories appear consistently on index cards and article bylines. Categories
remain available even when they have no posts; do not create placeholder notes.
Use one `tags` list for specific topics such as `robotics`, `formal-methods`, or
`agent-security`. Avoid generic `openshell` and `agents` tags; use
`agent-security` consistently instead of `security`. Tags appear on articles and
feed site search metadata; index cards omit tags.

Readers can combine category and author filters on the Dev Notes index. The
renderer derives the author menu from every post's author IDs and always includes
all four categories, including those without posts. The newest matching post is
featured; the remaining matches keep their chronological order. Filters use
`?category=research&author=johnnygreco` URLs that survive refresh and browser
Back/Forward navigation. Article byline names and categories link to these
filtered views. Empty combinations show a message and a clear-filters action.
Keep the full index readable when JavaScript is unavailable.

An optional `card_variant` must have matching card and
artwork CSS modifiers in `docs/stylesheets/dev-notes.css`. Set `hero_image` to
an image path relative to the post, and describe it with `hero_image_alt`. The
renderer uses it for both the post's opening hero and its index thumbnail. Set
`hero_image_dark` to an optional dark-mode counterpart; both surfaces switch with
the site theme. Hero images must live under `docs/`. Without `hero_image`, the
post omits the hero and the index uses generated artwork.

Authors provide content rather than header HTML. For example, a new post can be:

```markdown
---
title: "A clear experiment title"
date: 2026-09-28
description: "A short summary for the index and search."
subtitle: "An optional sentence introducing the experiment."
agent_markdown: true
authors:
  - johnnygreco
hero_image: "../../assets/my-experiment/hero.png"
hero_image_alt: "A description of what the image communicates."
categories:
  - Research
---

Start the introduction here. The renderer inserts the opening layout above it.

## Experiment

Describe the work and evidence.
```

Use a real author ID and an existing image, or omit both hero fields. `subtitle`
is optional and separate from the index's `description`. The renderer generates
the title, subtitle, hero, and byline in that order. Do not add another H1 or
manually position a hero. Existing generated headers update from front matter
without changing the prose below them.

Shared CSS sets the reading column to at most 74 character units, with 18px body
text on desktop and at least 16px on phones. Every post uses the sandbox note's
smaller title and subtitle styles: titles scale from 1.6rem on narrow phones
to 2.8rem on desktop, with a 1.85rem minimum above 24rem viewport width, and
subtitles from 0.95rem to 1.05rem. The opening reserves space for the site header
and breadcrumbs, then fits the title, subtitle, and complete hero in the remaining
viewport. Heroes fill that available space up to the reading-column width and
560px height, preserving their native proportions without cropping. Longer text
leaves less room for the hero; authors should keep titles and subtitles concise.
The index continues to use compact thumbnails.
Clicking a hero opens the original image, so a diagram can remain compact without
losing access to its details. Index images use bounded frames with the full image
visible. Do not add per-post title/hero sizing or force an image into a different
aspect ratio. Body diagrams may still use the shared wide or scrollable figure
treatments when their labels need more room.

The index separates the page introduction, filtering controls, and posts. Give
the masthead breathing room and keep the filters in their own row between two
dividers, above the featured-note heading. Laptop screens show the featured title;
a 900px-tall desktop viewport fits the featured note. Keep its image and type
larger than the archive entries; longer featured titles can move the archive
below the first screen.
The archive remains a normal scrolling list as it grows; do not shrink its
entries to fit all notes on one screen or give the archive a separate scrolling
panel. Keep one label per section. Use whitespace within the featured note and
dividers above and below the filtering controls, at the archive boundary, and between
archive entries.

Do not edit content inside these generated marker pairs:

- `<!-- dev-notes:posts:start -->` / `<!-- dev-notes:posts:end -->` in
  `docs/dev-notes/index.md`
- `<!-- dev-note:byline:start -->` / `<!-- dev-note:byline:end -->` in posts
- `<!-- dev-note:header:start -->` / `<!-- dev-note:header:end -->` in posts
- `# dev-notes:nav:start` / `# dev-notes:nav:end` in `zensical.toml`

After changing posts or author metadata, run:

```sh
python3 scripts/render-dev-notes.py
```

Commit any generated changes with the source change.

The Docs workflow runs the header tests, verifies generated content is committed,
and checks the built site in Chromium and WebKit at desktop, tablet, and phone
sizes in both themes. The browser checks discover every note automatically. They reject missing
hero assets or alt text, cropped or thumbnail-sized article heroes, overly long text lines,
horizontal page overflow, off-center heroes, images overflowing their figures or
overlapping the byline, incorrect theme-image visibility, an opening that needs
scrolling to see the full hero, and an index that obscures the featured title on
laptops or pushes the featured note below a 900px-tall
desktop viewport. They also check the separation of introduction, filters, and posts.
They also move the featured note into the recent list to exercise its thumbnail
layout. The workflow saves screenshots as the `dev-notes-layout` artifact for
visual review. These are layout
constraints rather than pixel snapshots, so ordinary prose edits need no new
baselines. They cannot judge the readability of labels baked into an image;
review the screenshot and full-size image for detailed charts.

The same browser checks cover shared navigation: footer titles must fit inside
their links, breadcrumbs must remain readable, and wide documentation tables
must scroll within the article without overflowing the page.
Embedded demos are also played in both browsers. Publish MP4 recordings with
H.264 video, 8-bit `yuv420p` pixels, and streaming metadata at the beginning of
the file (`faststart`); HEVC-only recordings do not play in every browser.

For Pi transcript provenance, publication boundaries, and viewer checks, see
the [Pi project maintenance guide](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/research/pi-admission/README.md#maintenance).

## Theme and brand assets

Project logos shared by a README and the site belong in `projects/<name>/assets/`.
Register that directory in `PROJECT_ASSETS` in `scripts/stage-project-docs.py`;
the build copies it into the staged documentation's `assets/` directory.
README image links should be relative so they work on feature branches.

Keep shared brand assets in `docs/assets/brand/`. Use the compact SVG mark for
`project.theme.logo`, `favicon.svg` for the browser icon, and the light and dark
PNG banners for full OpenShell Research lockups. Never reference files from a
local Downloads directory.

Verify branded surfaces in both the `default` and `slate` palette schemes. Prefer
CSS variables in `docs/stylesheets/dev-notes.css` over one-off hard-coded colors.
Keep asset paths relative to `docs_dir`. Theme templates live in `overrides/`;
keep the custom `404.html` useful for ordinary missing pages as well as expired
pull request previews. Keep that fallback self-contained: GitHub Pages serves it
for arbitrary paths where root-relative theme assets do not resolve beneath the
project site prefix.

## Validate and preview

Run the renderer tests and the same clean build used by CI:

```sh
uv run --python 3.12 --with pytest==8.4.2 pytest -q \
  tests/test_agent_markdown.py tests/test_docs_404.py \
  tests/test_render_dev_notes.py tests/test_dev_note_headers.py tests/test_stage_project_docs.py
node --check docs/javascripts/pi-traces.js
node tests/docs-preview.test.js
scripts/build-docs.sh
uv run --locked --script tests/test_dev_notes_layout.py --install-browser
uv run --locked --script tests/test_dev_notes_layout.py -q
```

The browser installer is needed once per environment. The test script's inline
dependencies and adjacent uv lock pin its toolchain independently of the site
builder. Browser checks serve the built artifact on an ephemeral local port and
write viewport screenshots to `.cache/dev-notes-layout/`.

`scripts/build-docs.sh` recreates `.venv-docs`, installs the pinned toolchain,
stages each configured canonical project documentation tree from `projects/`
at its configured site destination, renders Dev Notes metadata, and runs `zensical
build --clean --strict`. Configure project trees in
`scripts/stage-project-docs.py`. Do not report success unless the build
completes without issues.

For documentation-site changes, serve the complete built artifact before
handing the task back:

```sh
python3 -m http.server 8000 --directory site
```

Confirm <http://localhost:8000> is reachable and report the URL and command being
served. Plain `zensical serve` does not run the post-build Markdown publisher,
so it is not an artifact-faithful preview. Pull requests from branches in this
repository that change documentation inputs publish the built site under
`/pr-preview/pr-<number>/` and receive a comment linking to that
browser-accessible preview. The preview is updated when the PR changes and
removed when the PR closes or no longer changes documentation. Fork and
Dependabot pull requests validate with read-only credentials but do not publish
previews on the production documentation origin.

The `gh-pages` branch stores the composite production site and active previews;
GitHub Pages remains configured with **GitHub Actions** as its publishing
source. Every successful push to `main` updates the production site while
preserving active previews, then deploys the complete branch through the
official Pages artifact workflow. Merging a pull request therefore publishes
its documentation after the **Docs** workflow passes. A manual **Docs**
workflow dispatch from `main` can republish the current revision without a new
commit. The first production deployment creates `gh-pages` automatically. To
roll back to a revision before preview support, leave the Pages source set to
**GitHub Actions** and rerun the restored documentation workflow.
