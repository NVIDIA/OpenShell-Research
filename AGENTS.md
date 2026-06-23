# Repository Instructions

## Brand Assets

OpenShell documentation brand assets live in `docs/assets/brand/`. Use the SVG
assets for site UI and authored docs because they stay crisp in the header,
high-density displays, and generated static pages. The header uses the compact
mark through `project.theme.logo`, the browser tab uses `favicon.svg`, the
regular horizontal lockup is best on light page surfaces, and the reversed
horizontal lockup is best on dark hero or announcement surfaces.

The docs theme supports system-aware light and dark modes through
`project.theme.palette` entries in `zensical.toml`. When adding branded page
surfaces, verify both `default` and `slate` schemes and prefer CSS variables in
`docs/stylesheets/dev-notes.css` over one-off hard-coded colors.

Do not reference local files from `~/Downloads` in documentation content or
theme config. Copy intentionally selected assets into `docs/assets/brand/` and
keep paths relative to `docs_dir` so Zensical can copy them into the generated
site.

## Static Site Development

When developing the static documentation site associated with this repository,
serve the generated site locally so the user can view it. Print the URL and the
directory or command being served before finishing.

## Dev Notes

Dev Notes are implemented as plain Markdown posts under
`docs/dev-notes/posts/`, but the listing cards, visible author bylines, and
`zensical.toml` navigation entries are generated. Do not hand-edit content
inside these marker pairs:

- `<!-- dev-notes:posts:start -->` / `<!-- dev-notes:posts:end -->` in
  `docs/dev-notes/index.md`
- `<!-- dev-note:byline:start -->` / `<!-- dev-note:byline:end -->` in each
  Dev Note post
- `# dev-notes:nav:start` / `# dev-notes:nav:end` in `zensical.toml`

Author records live in `docs/dev-notes/authors.json`. A post's `authors` front
matter must reference IDs from that file. The renderer derives GitHub profile
links and avatar URLs from the `github` field, but an author can also provide
explicit `url` or `avatar` fields when needed.

Each Dev Note post needs `title`, `date`, `description`, and at least one
author. The landing card uses `description` as its summary, `categories[0]` as
the topic after `Dev Note`, and `card_tags` when present; otherwise it falls
back to `tags`. `card_variant` is optional and maps to a CSS modifier such as
`.dev-note-card--launch`.

Posts are sorted newest first by ISO `date`, then title. Use dated filenames
such as `YYYY-MM-DD-short-title.md` so file paths stay stable and readable, but
the generated ordering comes from front matter rather than filenames.

After editing Dev Note posts, author metadata, or the Dev Notes nav markers,
run:

```sh
python scripts/render-dev-notes.py
zensical build --clean --strict
```

The repository build script already runs `scripts/render-dev-notes.py` before
the Zensical build, so CI should catch unknown author IDs, missing required post
metadata, invalid dates, or invalid `card_variant` values.
