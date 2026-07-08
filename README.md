# OpenShell Research

This repository is the home for OpenShell research engineering work. It is for
building, documenting, and sharing applications of cutting-edge research that use
OpenShell as the runtime.

## Start here: Reachy Mini + OpenShell

Follow the **[Reachy Mini OpenShell sandbox tutorial](docs/projects/reachy-mini-openshell-sandbox.md)**
to build the policy-controlled physical-robot demo from start to finish.

The tutorial walks through:

1. Running the Reachy robot runtime and authenticated MCP server on the host.
2. Running the conversation application inside an OpenShell sandbox.
3. Routing camera images through an approved vision model.
4. Allowing or denying individual Reachy MCP tools with OpenShell policy.
5. Testing movement, camera capture, scene scans, model routing, and policy denials.

Related links:

- [Reachy Mini project overview](docs/projects/reachy-mini-openshell/index.md)
- [Reachy Mini application source](projects/reachy-mini-openshell/)
- [Safe OpenShell policy](projects/reachy-mini-openshell/openshell/policy-safe.yaml)

## Repository layout

- `docs/` contains the Zensical documentation source.
- `docs/assets/brand/` contains the OpenShell logo and favicon assets used by
  the documentation theme and authored pages.
- `docs/dev-notes/` contains research engineering updates, release notes, and
  build logs worth sharing.
- `docs/dev-notes/authors.json` contains reusable Dev Notes author metadata.
- `projects/` contains self-contained research project folders, including the
  Reachy Mini conversation demo for OpenShell.
- `scripts/render-dev-notes.py` renders Dev Notes cards, post bylines, and
  navigation entries from post front matter.
- `zensical.toml` configures the documentation site.
- `requirements-docs.txt` pins the documentation build toolchain.
- `.gitlab-ci.yml` builds the documentation site in GitLab CI.

## Documentation workflow

Use Python 3.10 or newer.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-docs.txt
zensical serve
```

The local preview runs at <http://localhost:8000>. To build the static site:

```sh
python scripts/render-dev-notes.py
zensical build --clean --strict
```

The generated site is written to `site/`.

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
