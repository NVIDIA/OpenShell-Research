#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Publish canonical documentation sources beside their rendered HTML pages."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil


ROOT = Path(__file__).resolve().parents[1]
ELIGIBLE_DIRECTORIES = (
    Path("dev-notes/posts"),
    Path("documentation"),
)
FRONT_MATTER = re.compile(
    rb"\A---[ \t]*\r?\n(?P<body>.*?)\r?\n(?:---|\.\.\.)[ \t]*\r?\n",
    re.DOTALL,
)
AGENT_MARKDOWN = re.compile(rb"^agent_markdown:[ \t]*true[ \t]*$", re.MULTILINE)


def _within(path: Path, directory: Path) -> Path:
    """Return a resolved path relative to a resolved directory."""

    resolved_directory = directory.resolve()
    resolved_path = path.resolve()
    try:
        return resolved_path.relative_to(resolved_directory)
    except ValueError as error:
        raise ValueError(f"path escapes {resolved_directory}: {path}") from error


def has_agent_markdown(source: Path) -> bool:
    """Return whether a source has the canonical agent Markdown marker."""

    match = FRONT_MATTER.match(source.read_bytes())
    return bool(match and len(AGENT_MARKDOWN.findall(match.group("body"))) == 1)


def eligible_sources(docs_dir: Path) -> list[Path]:
    """Find and validate every canonical content source."""

    _within(docs_dir, docs_dir)
    sources: list[Path] = []
    for relative_directory in ELIGIBLE_DIRECTORIES:
        directory = docs_dir / relative_directory
        _within(directory, docs_dir)
        if not directory.is_dir():
            raise ValueError(f"eligible content directory does not exist: {directory}")
        sources.extend(path for path in directory.rglob("*.md") if path.is_file())

    sources.sort()
    for source in sources:
        _within(source, docs_dir)
    missing = [source for source in sources if not has_agent_markdown(source)]
    if missing:
        paths = "\n".join(f"  - {source}" for source in missing)
        raise ValueError(
            "canonical content pages must declare `agent_markdown: true` in "
            f"front matter:\n{paths}"
        )
    return sources


def markdown_destination(source: Path, docs_dir: Path, site_dir: Path) -> Path:
    """Map a safe documentation source to its same-path site export."""

    relative_source = _within(source, docs_dir)
    if source.suffix != ".md":
        raise ValueError(f"agent Markdown source must end in .md: {source}")
    destination = site_dir / relative_source
    _within(destination, site_dir)
    return destination


def rendered_html(source: Path, docs_dir: Path, site_dir: Path) -> Path:
    """Return the expected directory-URL HTML output for a source."""

    relative_source = _within(source, docs_dir)
    if source.suffix != ".md":
        raise ValueError(f"rendered source must end in .md: {source}")
    if source.name == "index.md":
        rendered = site_dir / relative_source.with_suffix(".html")
    else:
        rendered = site_dir / relative_source.with_suffix("") / "index.html"
    _within(rendered, site_dir)
    return rendered


def markdown_href(source: Path) -> str:
    """Return the relative Markdown URL used by an eligible rendered page."""

    if source.name == "index.md":
        return "index.md"
    return f"../{source.name}"


def publish(docs_dir: Path, site_dir: Path) -> list[Path]:
    """Validate and copy eligible Markdown sources into the built site."""

    sources = eligible_sources(docs_dir)
    if not site_dir.is_dir():
        raise ValueError(f"site directory does not exist: {site_dir}")

    destinations: list[Path] = []
    for source in sources:
        rendered = rendered_html(source, docs_dir, site_dir)
        if not rendered.is_file():
            raise ValueError(f"rendered HTML does not exist for {source}: {rendered}")
        destinations.append(markdown_destination(source, docs_dir, site_dir))

    for source, destination in zip(sources, destinations, strict=True):
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return destinations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs-dir", type=Path, default=ROOT / "docs")
    parser.add_argument("--site-dir", type=Path, default=ROOT / "site")
    args = parser.parse_args()

    try:
        destinations = publish(args.docs_dir, args.site_dir)
    except ValueError as error:
        parser.error(str(error))
    print(f"Published {len(destinations)} agent-readable Markdown page(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
