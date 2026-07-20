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
CANONICAL_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


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


def parse_date(value: str, path: Path) -> dt.date:
    if not CANONICAL_DATE.fullmatch(value):
        raise ValueError(f"{path} date {value!r} must use YYYY-MM-DD")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{path} has invalid ISO date {value!r}") from exc


def format_date(value: dt.date) -> str:
    return f"{value.strftime('%B')} {value.day}, {value.year}"


def post_sort_key(post: dict[str, Any]) -> tuple[dt.date, str]:
    return (post["published"], str(post["metadata"].get("title", "")))


def discover_posts(authors: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    posts = []
    for path in sorted(POSTS_DIR.glob("*.md")):
        markdown = path.read_text(encoding="utf-8")
        metadata, raw_frontmatter, body = parse_frontmatter(markdown, path)
        post_authors = require_authors(require_list(metadata, "authors", path), authors, path)
        published = parse_date(require_string(metadata, "date", path), path)
        posts.append(
            {
                "path": path,
                "metadata": metadata,
                "frontmatter": raw_frontmatter,
                "body": body,
                "authors": post_authors,
                "published": published,
            }
        )
    return sorted(posts, key=post_sort_key, reverse=True)


def card_visual_class(post: dict[str, Any]) -> str:
    """Choose a stable visual treatment from authored metadata."""
    metadata = post["metadata"]
    variant = str(metadata.get("card_variant", "")).strip()
    if variant and not re.fullmatch(r"[a-z0-9_-]+", variant):
        raise ValueError(f"{post['path']} has invalid card_variant {variant!r}")
    categories = require_list(metadata, "categories", post["path"])
    source = variant or (categories[0] if categories else "research")
    slug = re.sub(r"[^a-z0-9_-]+", "-", source.lower()).strip("-")
    return slug or "research"


def card_hero_image_url(post: dict[str, Any]) -> str | None:
    """Resolve an optional post-relative hero image for the Dev Notes index."""
    raw_path = str(post["metadata"].get("hero_image", "")).strip()
    if not raw_path:
        return None

    docs_root = (ROOT / "docs").resolve()
    hero_path = (post["path"].parent / raw_path).resolve()
    try:
        docs_relative = hero_path.relative_to(docs_root)
    except ValueError as exc:
        raise ValueError(f"{post['path']} hero_image must stay inside {docs_root}") from exc
    if not hero_path.is_file():
        raise ValueError(f"{post['path']} hero_image does not exist: {hero_path}")

    return (Path("..") / docs_relative).as_posix()


def render_card_visual(post: dict[str, Any], *, eager: bool = False) -> str:
    metadata = post["metadata"]
    variant = html.escape(card_visual_class(post), quote=True)
    hero_image = card_hero_image_url(post)
    if hero_image:
        loading = "eager" if eager else "lazy"
        priority = ' fetchpriority="high"' if eager else ""
        return f"""      <div class="dev-note-card__visual dev-note-card__visual--{variant} dev-note-card__visual--image" aria-hidden="true">
        <img class="dev-note-card__visual-image" src="{html.escape(hero_image, quote=True)}" alt="" loading="{loading}"{priority}>
      </div>"""

    categories = require_list(metadata, "categories", post["path"])
    label = categories[0] if categories else "Research"
    date_stamp = post["published"].strftime("%Y.%m.%d")
    return f"""      <div class="dev-note-card__visual dev-note-card__visual--{variant}" aria-hidden="true">
        <span class="dev-note-card__visual-label">Dev Note / {html.escape(label)}</span>
        <span class="dev-note-card__visual-index">{date_stamp}</span>
        <span class="dev-note-card__visual-mark">&gt;_</span>
      </div>"""


def render_card_authors(authors: list[dict[str, str]]) -> str:
    author_images = "\n".join(
        f'          <img src="{html.escape(avatar_url(author, 64), quote=True)}" alt="" loading="lazy">'
        for author in authors
    )
    names = natural_join([author["name"] for author in authors])
    return f"""        <span class="dev-note-card__authors" aria-label="{html.escape(author_label(authors), quote=True)}">
{author_images}
          <span class="dev-note-card__author-names">{html.escape(names)}</span>
        </span>"""


def render_card_copy(post: dict[str, Any]) -> str:
    path = post["path"]
    metadata = post["metadata"]
    title = require_string(metadata, "title", path)
    published = post["published"]
    description = require_string(metadata, "description", path)
    categories = require_list(metadata, "categories", path)
    tags = require_list(metadata, "card_tags", path) or require_list(metadata, "tags", path)
    authors = post["authors"]
    category = categories[0] if categories else "Research"
    tags_html = ""
    if tags:
        tag_items = "\n".join(f"          <span>{html.escape(tag)}</span>" for tag in tags)
        tags_html = f"""
        <div class="dev-note-card__tags" aria-label="Tags">
{tag_items}
        </div>"""
    return f"""      <div class="dev-note-card__copy">
        <div class="dev-note-card__meta">
          <time datetime="{published.isoformat()}">{html.escape(format_date(published))}</time>
          <span>{html.escape(category)}</span>
        </div>
        <h3>{html.escape(title)}</h3>
        <p class="dev-note-card__summary">{html.escape(description)}</p>{tags_html}
        <div class="dev-note-card__footer">
{render_card_authors(authors)}
          <span class="dev-note-card__read">Read note</span>
        </div>
      </div>"""


def render_featured_card(post: dict[str, Any]) -> str:
    relative_url = post["path"].relative_to(DEV_NOTES_DIR).with_suffix("").as_posix() + "/"
    variant = card_visual_class(post)
    image_class = " dev-note-card--has-image" if card_hero_image_url(post) else ""
    return f"""    <article class="dev-note-card dev-note-card--featured dev-note-card--{html.escape(variant, quote=True)}{image_class}">
      <a class="dev-note-card__link" href="{html.escape(relative_url, quote=True)}">
{render_card_visual(post, eager=True)}
{render_card_copy(post)}
      </a>
    </article>"""


def render_recent_card(post: dict[str, Any]) -> str:
    relative_url = post["path"].relative_to(DEV_NOTES_DIR).with_suffix("").as_posix() + "/"
    variant = card_visual_class(post)
    image_class = " dev-note-card--has-image" if card_hero_image_url(post) else ""
    return f"""    <article class="dev-note-card dev-note-card--recent dev-note-card--{html.escape(variant, quote=True)}{image_class}">
      <a class="dev-note-card__link" href="{html.escape(relative_url, quote=True)}">
{render_card_visual(post)}
{render_card_copy(post)}
      </a>
    </article>"""


def render_index_cards(posts: list[dict[str, Any]]) -> str:
    if not posts:
        body = '  <p class="dev-notes-empty">The first Dev Note is being prepared.</p>'
    else:
        featured = render_featured_card(posts[0])
        recent_posts = posts[1:]
        recent = "\n".join(render_recent_card(post) for post in recent_posts)
        recent_section = ""
        if recent:
            recent_section = f"""
  <section class="journal-section dev-notes-recent" aria-labelledby="recent-notes-title">
    <div class="journal-section__head">
      <h2 id="recent-notes-title">Recent notes</h2>
      <span>The working archive</span>
    </div>
    <div class="dev-notes-recent-list">
{recent}
    </div>
  </section>"""
        body = f"""  <section class="journal-section dev-notes-featured" aria-labelledby="featured-note-title">
    <div class="journal-section__head">
      <h2 id="featured-note-title">Featured note</h2>
      <span>Latest from the team</span>
    </div>
{featured}
  </section>{recent_section}"""
    return (
        f"{POSTS_START}\n"
        "<!-- Generated by scripts/render-dev-notes.py; edit posts and authors.json. -->\n"
        f"{body}\n"
        f"{POSTS_END}"
    )


def replace_between_markers(content: str, start: str, end: str, replacement: str, path: Path) -> str:
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if not pattern.search(content):
        raise ValueError(f"{path} is missing generated section markers {start!r} and {end!r}")
    return pattern.sub(lambda _: replacement, content, count=1)


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
    metadata = post["metadata"]
    published = post["published"]
    categories = require_list(metadata, "categories", post["path"])
    category = categories[0] if categories else "Research"
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
<div class="dev-note-byline">
  <p class="dev-note-byline__label">
    <span>Dev Note</span>
    <time datetime="{published.isoformat()}">{html.escape(format_date(published))}</time>
    <span>{html.escape(category)}</span>
  </p>
  <div class="dev-note-byline__authors" aria-label="{html.escape(author_label(authors), quote=True)}">
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
        updated_body = marker_pattern.sub(lambda _: byline, body, count=1)
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
