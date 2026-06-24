# OpenShell Research

This repository is the home for OpenShell research engineering work. It is for
building, documenting, and sharing applications of cutting-edge research that use
OpenShell as the runtime.

## Repository layout

- `docs/` contains the Zensical documentation source.
- `docs/assets/brand/` contains the OpenShell logo and favicon assets used by
  the documentation theme and authored pages.
- `docs/dev-notes/` contains research engineering updates, release notes, and
  build logs worth sharing.
- `docs/dev-notes/authors.json` contains reusable Dev Notes author metadata.
- `scripts/render-dev-notes.py` renders Dev Notes cards, post bylines, and
  navigation entries from post front matter.
- `zensical.toml` configures the documentation site.
- `requirements-docs.txt` pins the documentation build toolchain.
- `.github/workflows/docs.yml` validates documentation builds in GitHub Actions
  and deploys the generated site to GitHub Pages.

## Documentation workflow

Use Python 3.10 or newer.

To preview the documentation locally:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-docs.txt
zensical serve
```

The local preview runs at <http://localhost:8000>. To run the same clean build
used by CI:

```sh
scripts/build-docs.sh
```

The build script recreates `.venv-docs`, installs `requirements-docs.txt`,
renders Dev Notes metadata, and runs `zensical build --clean --strict`. The
generated site is written to `site/`.

GitHub Actions runs the docs workflow for every pull request and `main` push.
Pull requests validate the generated site without deploying. Pushes to `main`
and manual workflow dispatches from `main` publish `site/` to GitHub Pages
after validation passes.

### Dev Notes

Add new posts under `docs/dev-notes/posts/` with a dated filename such as
`YYYY-MM-DD-short-title.md`. Set `authors` to IDs from
`docs/dev-notes/authors.json`; add new authors there once, including their
GitHub handle and description. Run `python scripts/render-dev-notes.py` after
editing posts or authors so the landing-page cards, visible post bylines, and
Dev Notes navigation stay in sync.

## Content model

Use the documentation for durable project knowledge: architecture notes,
application guides, runtime integration details, and reproducible research
engineering workflows. Use Dev Notes for dated updates: experiments, milestones,
benchmarks, release notes, and lessons learned while turning research into OpenShell
applications.
