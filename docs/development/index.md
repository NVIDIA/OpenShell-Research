---
title: Documentation Site Development
description: Agent instructions for maintaining the OpenShell Research documentation site.
---

# Documentation site development

Follow these instructions for changes under `docs/`, a project's `docs/` tree,
`zensical.toml`, the Dev Notes renderer, or the documentation workflow. Run
commands from the repository root. Use Python 3.10 or newer.

## Content routing

- Put project-specific software knowledge—installation, usage, reproducibility,
  and known limitations—under `projects/<name>/docs/`. The clean build stages
  configured project documentation under `docs/documentation/` for publishing.
- Put cross-project software documentation directly under
  `docs/documentation/`.
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
`docs/documentation/` and `site/` must not be edited.

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

The renderer uses `categories[0]` as the card topic and `card_tags` as its tags,
falling back to `tags`. An optional `card_variant` must have matching card and
artwork CSS modifiers in `docs/stylesheets/dev-notes.css`. Set `hero_image` to
an image path relative to the post when its card should use the post's hero
instead of generated artwork. Hero images must live under `docs/`.

Do not edit content inside these generated marker pairs:

- `<!-- dev-notes:posts:start -->` / `<!-- dev-notes:posts:end -->` in
  `docs/dev-notes/index.md`
- `<!-- dev-note:byline:start -->` / `<!-- dev-note:byline:end -->` in posts
- `# dev-notes:nav:start` / `# dev-notes:nav:end` in `zensical.toml`

After changing posts or author metadata, run:

```sh
python3 scripts/render-dev-notes.py
```

Commit any generated changes with the source change.

## Theme and brand assets

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
python3 tests/test_render_dev_notes.py
scripts/build-docs.sh
```

`scripts/build-docs.sh` recreates `.venv-docs`, installs the pinned toolchain,
stages canonical Egress Gate documentation from
`projects/egress-gate/docs/`, renders Dev Notes metadata, and runs
`zensical build --clean --strict`. Do not report success unless it completes
without issues.

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
