---
title: Documentation Site Development
description: Agent instructions for maintaining the OpenShell Research documentation site.
---

# Documentation site development

Follow these instructions for changes under `docs/`, `zensical.toml`, the Dev
Notes renderer, or the documentation workflow. Run commands from the repository
root. Use Python 3.10 or newer.

## Content routing

- Put durable software knowledge—installation, usage, reproducibility, and known
  limitations—under `docs/documentation/`.
- Put dated experiments, benchmarks, releases, use cases, and engineering updates
  under `docs/dev-notes/`.
- When a Dev Note introduces reusable software, add its durable guide under
  `docs/documentation/`, link it to the originating Dev Note, and add it to
  `zensical.toml`.

## Dev Notes

Dev Notes are Markdown posts under `docs/dev-notes/posts/`. Each post requires
`title`, an exact `YYYY-MM-DD` date, `description`, and at least one `authors` ID
defined in `docs/dev-notes/authors.json`. Use a dated filename such as
`YYYY-MM-DD-short-title.md`.

The renderer uses `categories[0]` as the card topic and `card_tags` as its tags,
falling back to `tags`. An optional `card_variant` must have matching card and
artwork CSS modifiers in `docs/stylesheets/dev-notes.css`.

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
Keep asset paths relative to `docs_dir`.

## Validate and preview

Run the renderer tests and the same clean build used by CI:

```sh
python3 tests/test_render_dev_notes.py
scripts/build-docs.sh
```

`scripts/build-docs.sh` recreates `.venv-docs`, installs the pinned toolchain,
renders Dev Notes metadata, and runs `zensical build --clean --strict`. Do not
report success unless it completes without issues.

For documentation-site changes, serve the site before handing the task back:

```sh
.venv-docs/bin/zensical serve
```

Confirm <http://localhost:8000> is reachable and report the URL and command being
served. Pull requests validate without deploying; pushes and manual workflow runs
from `main` publish the validated `site/` directory to GitHub Pages.
