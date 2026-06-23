#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Render Dev Notes index cards and author bylines from post metadata."""

from __future__ import annotations

import datetime as dt
import html
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEV_NOTES_DIR = ROOT / "docs" / "dev-notes"
POSTS_DIR = DEV_NOTES_DIR / "posts"
INDEX_PATH = DEV_NOTES_DIR / "index.md"
AUTHORS_PATH = DEV_NOTES_DIR / "authors.json"
CONFIG_PATH = ROOT / "zensical.toml"

POSTS_START = "<!-- dev-notes:posts:start -->"
POSTS_END = "<!-- dev-notes:posts:end -->"
BYLINE_START = "<!-- dev-note:byline:start -->"
BYLINE_END = "<!-- dev-note:byline:end -->"
NAV_START = "      # dev-notes:nav:start"
NAV_END = "      # dev-notes:nav:end"


def parse_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def parse_frontmatter(markdown: str, path: Path) -> tuple[dict[str, Any], str, str]:
    if not markdown.startswith("---\n"):
        raise ValueError(f"{path} is missing front matter")

    end = markdown.find("\n---\n", 4)
    if end == -1:
        raise ValueError(f"{path} has unterminated front matter")

    raw_frontmatter = markdown[4:end]
    body = markdown[end + 5 :].lstrip("\n")
    data: dict[str, Any] = {}
    current_list: str | None = None

    for raw_line in raw_frontmatter.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if not line.startswith((" ", "\t")) and ":" in line:
            key, raw_value = line.split(":", 1)
            key = key.strip()
            raw_value = raw_value.strip()
            if raw_value:
                data[key] = parse_scalar(raw_value)
                current_list = None
            else:
                data[key] = []
                current_list = key
            continue

        if current_list and stripped.startswith("- "):
            data[current_list].append(parse_scalar(stripped[2:]))
            continue

        raise ValueError(f"{path} has unsupported front matter line: {raw_line}")

    return data, raw_frontmatter, body


def load_authors() -> dict[str, dict[str, str]]:
    with AUTHORS_PATH.open(encoding="utf-8") as file:
        authors = json.load(file)
    if not isinstance(authors, dict):
        raise ValueError(f"{AUTHORS_PATH} must contain a JSON object")
    for author_id, author in authors.items():
        if not isinstance(author, dict):
            raise ValueError(f"{AUTHORS_PATH} entry {author_id!r} must be a JSON object")
        if not author.get("name"):
            raise ValueError(f"{AUTHORS_PATH} entry {author_id!r} needs a name")
    return authors


def require_string(metadata: dict[str, Any], key: str, path: Path) -> str:
    value = str(metadata.get(key, "")).strip()
    if not value:
        raise ValueError(f"{path} front matter field {key!r} is required")
    return value


def require_list(metadata: dict[str, Any], key: str, path: Path) -> list[str]:
    value = metadata.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{path} front matter field {key!r} must be a list")
    return [str(item) for item in value]


def require_authors(ids: list[str], authors: dict[str, dict[str, str]], path: Path) -> list[dict[str, str]]:
    if not ids:
        raise ValueError(f"{path} needs at least one Dev Notes author")
    resolved = []
    for author_id in ids:
        author = authors.get(author_id)
        if author is None:
            raise ValueError(f"{path} references unknown Dev Notes author {author_id!r}")
        resolved.append(author)
    return resolved


def avatar_url(author: dict[str, str], size: int) -> str:
    if "avatar" in author:
        return author["avatar"]
    github = author.get("github")
    if not github:
        raise ValueError(f"Author {author.get('name', '<unknown>')} needs a github or avatar field")
    return f"https://github.com/{github}.png?size={size}"


def profile_url(author: dict[str, str]) -> str:
    if "url" in author:
        return author["url"]
    github = author.get("github")
    if not github:
        raise ValueError(f"Author {author.get('name', '<unknown>')} needs a github or url field")
    return f"https://github.com/{github}"


def natural_join(values: list[str]) -> str:
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    return f"{', '.join(values[:-1])} and {values[-1]}"


def author_label(authors: list[dict[str, str]]) -> str:
    names = [author["name"] for author in authors]
    prefix = "Author" if len(names) == 1 else "Authors"
    return f"{prefix}: {natural_join(names)}"


def format_date(value: str, path: Path) -> str:
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{path} has invalid ISO date {value!r}") from exc
    return f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}"


def post_sort_key(post: dict[str, Any]) -> tuple[str, str]:
    return (str(post["metadata"].get("date", "")), str(post["metadata"].get("title", "")))


def discover_posts(authors: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    posts = []
    for path in sorted(POSTS_DIR.glob("*.md")):
        markdown = path.read_text(encoding="utf-8")
        metadata, raw_frontmatter, body = parse_frontmatter(markdown, path)
        post_authors = require_authors(require_list(metadata, "authors", path), authors, path)
        posts.append(
            {
                "path": path,
                "metadata": metadata,
                "frontmatter": raw_frontmatter,
                "body": body,
                "authors": post_authors,
            }
        )
    return sorted(posts, key=post_sort_key, reverse=True)


def render_card(post: dict[str, Any]) -> str:
    path = post["path"]
    metadata = post["metadata"]
    title = require_string(metadata, "title", path)
    date = require_string(metadata, "date", path)
    description = require_string(metadata, "description", path)
    categories = require_list(metadata, "categories", path)
    tags = require_list(metadata, "card_tags", path) or require_list(metadata, "tags", path)
    authors = post["authors"]

    classes = ["dev-note-card"]
    variant = str(metadata.get("card_variant", "")).strip()
    if variant:
        if not re.fullmatch(r"[a-z0-9_-]+", variant):
            raise ValueError(f"{path} has invalid card_variant {variant!r}")
        classes.append(f"dev-note-card--{variant}")

    relative_url = path.relative_to(DEV_NOTES_DIR).with_suffix("").as_posix() + "/"
    meta_parts = [format_date(date, path), "Dev Note"]
    if categories:
        meta_parts.append(categories[0])

    author_images = "\n".join(
        f'        <img src="{html.escape(avatar_url(author, 64), quote=True)}" alt="" loading="lazy">'
        for author in authors
    )

    return f"""  <article class="{html.escape(' '.join(classes), quote=True)}">
    <a class="dev-note-card__link" href="{html.escape(relative_url, quote=True)}">
      <span class="dev-note-card__meta">{html.escape(' / '.join(meta_parts))}</span>
      <h2>{html.escape(title)}</h2>
      <p>{html.escape(description)}</p>
      <span class="dev-note-card__authors" aria-label="{html.escape(author_label(authors), quote=True)}">
{author_images}
      </span>
      <span class="dev-note-card__tags">{html.escape(' / '.join(tags))}</span>
    </a>
  </article>"""


def render_index_cards(posts: list[dict[str, Any]]) -> str:
    if not posts:
        body = '  <p class="dev-notes-empty">No Dev Notes yet.</p>'
    else:
        body = "\n".join(render_card(post) for post in posts)
    return (
        f"{POSTS_START}\n"
        "<!-- Generated by scripts/render-dev-notes.py; edit posts and authors.json. -->\n"
        f'<section class="dev-notes-grid" aria-label="Dev Notes posts">\n{body}\n</section>\n'
        f"{POSTS_END}"
    )


def replace_between_markers(content: str, start: str, end: str, replacement: str, path: Path) -> str:
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if not pattern.search(content):
        raise ValueError(f"{path} is missing generated section markers {start!r} and {end!r}")
    return pattern.sub(replacement, content, count=1)


def update_index(posts: list[dict[str, Any]]) -> None:
    content = INDEX_PATH.read_text(encoding="utf-8")
    updated = replace_between_markers(
        content,
        POSTS_START,
        POSTS_END,
        render_index_cards(posts),
        INDEX_PATH,
    )
    if updated != content:
        INDEX_PATH.write_text(updated, encoding="utf-8")


def toml_string(value: str) -> str:
    return json.dumps(value)


def render_nav(posts: list[dict[str, Any]]) -> str:
    if not posts:
        return f"{NAV_START}\n      # No Dev Notes posts yet.\n{NAV_END}"

    entries = []
    for index, post in enumerate(posts):
        metadata = post["metadata"]
        title = require_string(metadata, "title", post["path"])
        relative_path = post["path"].relative_to(ROOT / "docs").as_posix()
        suffix = "," if index < len(posts) - 1 else ""
        entries.append(f"      {{{toml_string(title)} = {toml_string(relative_path)}}}{suffix}")
    return f"{NAV_START}\n" + "\n".join(entries) + f"\n{NAV_END}"


def update_nav(posts: list[dict[str, Any]]) -> None:
    content = CONFIG_PATH.read_text(encoding="utf-8")
    updated = replace_between_markers(content, NAV_START, NAV_END, render_nav(posts), CONFIG_PATH)
    if updated != content:
        CONFIG_PATH.write_text(updated, encoding="utf-8")


def render_byline(post: dict[str, Any]) -> str:
    authors = post["authors"]
    label = "Author" if len(authors) == 1 else "Authors"
    blocks = []
    for author in authors:
        description = author.get("description", "")
        description_html = f"\n        <span>{html.escape(description)}</span>" if description else ""
        blocks.append(
            f"""    <a class="dev-note-byline__author" href="{html.escape(profile_url(author), quote=True)}">
      <img src="{html.escape(avatar_url(author, 96), quote=True)}" alt="" loading="lazy">
      <span class="dev-note-byline__copy">
        <strong>{html.escape(author["name"])}</strong>{description_html}
      </span>
    </a>"""
        )

    return f"""{BYLINE_START}
<!-- Generated by scripts/render-dev-notes.py; edit front matter and authors.json. -->
<div class="dev-note-byline" aria-labelledby="dev-note-authors">
  <p class="dev-note-byline__label" id="dev-note-authors">{label}</p>
  <div class="dev-note-byline__authors">
{chr(10).join(blocks)}
  </div>
</div>
{BYLINE_END}"""


def update_post_byline(post: dict[str, Any]) -> None:
    path = post["path"]
    body = post["body"]
    byline = render_byline(post)

    marker_pattern = re.compile(re.escape(BYLINE_START) + r".*?" + re.escape(BYLINE_END), re.DOTALL)
    if marker_pattern.search(body):
        updated_body = marker_pattern.sub(byline, body, count=1)
    else:
        heading = re.search(r"(?m)^# .+\n", body)
        if not heading:
            raise ValueError(f"{path} needs a top-level heading before the generated byline")
        updated_body = body[: heading.end()] + "\n" + byline + "\n\n" + body[heading.end() :].lstrip("\n")

    content = f"---\n{post['frontmatter']}\n---\n\n{updated_body}"
    original = path.read_text(encoding="utf-8")
    if content != original:
        path.write_text(content, encoding="utf-8")


def main() -> int:
    authors = load_authors()
    posts = discover_posts(authors)
    for post in posts:
        update_post_byline(post)
    update_index(posts)
    update_nav(posts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
