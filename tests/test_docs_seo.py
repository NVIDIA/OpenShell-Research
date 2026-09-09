# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from html.parser import HTMLParser
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ElementTree

import pytest


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SITE = ROOT / "site"
CONFIG = ROOT / "zensical.toml"
SITE_URL = "https://nvidia.github.io/OpenShell-Research/"
PUBLIC_SOURCE_ROOTS = (DOCS / "dev-notes", DOCS / "documentation")
NOINDEX_SOURCE_ROOTS = (DOCS / "development", DOCS / "projects", DOCS / "research")
REQUIRE_RENDERED = os.environ.get("REQUIRE_RENDERED_SEO") == "1"


class HeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_head = False
        self.in_title = False
        self.in_structured_data = False
        self.links: list[dict[str, str | None]] = []
        self.meta: list[dict[str, str | None]] = []
        self.structured_data: list[str] = []
        self.title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "head":
            self.in_head = True
        elif self.in_head and tag == "title":
            self.in_title = True
        elif self.in_head and tag == "link":
            self.links.append(attributes)
        elif self.in_head and tag == "meta":
            self.meta.append(attributes)
        elif (
            self.in_head
            and tag == "script"
            and attributes.get("type") == "application/ld+json"
        ):
            self.in_structured_data = True
            self.structured_data.append("")

    def handle_endtag(self, tag: str) -> None:
        if tag == "head":
            self.in_head = False
        elif tag == "title":
            self.in_title = False
        elif tag == "script":
            self.in_structured_data = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        elif self.in_structured_data:
            self.structured_data[-1] += data

    @property
    def title(self) -> str:
        return "".join(self.title_parts).strip()

    def meta_content(self, attribute: str, value: str) -> list[str]:
        return [
            item.get("content") or ""
            for item in self.meta
            if item.get(attribute) == value
        ]

    def link_hrefs(self, relation: str) -> list[str]:
        return [
            item.get("href") or ""
            for item in self.links
            if relation in (item.get("rel") or "").split()
        ]


def public_sources() -> list[Path]:
    sources = [DOCS / "index.md"]
    for source_root in PUBLIC_SOURCE_ROOTS:
        sources.extend(source_root.rglob("*.md"))
    return sorted(sources)


def noindex_sources() -> list[Path]:
    sources: list[Path] = []
    for source_root in NOINDEX_SOURCE_ROOTS:
        sources.extend(source_root.rglob("*.md"))
    return sorted(sources)


def canonical_url(source: Path) -> str:
    relative = source.relative_to(DOCS)
    if relative == Path("index.md"):
        return SITE_URL
    if relative.name == "index.md":
        return f"{SITE_URL}{relative.parent.as_posix()}/"
    return f"{SITE_URL}{relative.with_suffix('').as_posix()}/"


def rendered_page(source: Path) -> Path:
    relative = source.relative_to(DOCS)
    if relative.name == "index.md":
        return SITE / relative.parent / "index.html"
    return SITE / relative.with_suffix("") / "index.html"


def parse_head(source: Path) -> HeadParser:
    parser = HeadParser()
    parser.feed(rendered_page(source).read_text(encoding="utf-8"))
    return parser


def test_production_site_url_is_configured() -> None:
    config = CONFIG.read_text(encoding="utf-8")

    assert f'site_url = "{SITE_URL}"' in config


def test_non_public_source_roots_opt_out_of_indexing() -> None:
    config = CONFIG.read_text(encoding="utf-8")

    assert 'noindex_prefixes = ["development/", "projects/", "research/"]' in config


@pytest.mark.skipif(not REQUIRE_RENDERED, reason="rendered SEO is checked after build")
def test_sitemap_contains_only_public_canonical_urls() -> None:
    sitemap = ElementTree.parse(SITE / "sitemap.xml")
    namespace = {"sitemap": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    actual_locations = [
        location.text
        for location in sitemap.findall("sitemap:url/sitemap:loc", namespace)
    ]
    actual = set(actual_locations)
    expected = {canonical_url(source) for source in public_sources()}

    assert len(actual_locations) == len(actual)
    assert actual == expected


@pytest.mark.skipif(not REQUIRE_RENDERED, reason="rendered SEO is checked after build")
def test_public_pages_have_canonical_and_social_metadata() -> None:
    for source in public_sources():
        parser = parse_head(source)
        expected_url = canonical_url(source)

        assert parser.link_hrefs("canonical") == [expected_url]
        assert parser.meta_content("name", "robots") == []
        assert parser.meta_content("property", "og:url") == [expected_url]
        assert len(parser.meta_content("property", "og:title")) == 1
        assert len(parser.meta_content("property", "og:description")) == 1
        assert len(parser.meta_content("name", "twitter:card")) == 1
        assert len(parser.meta_content("name", "twitter:title")) == 1
        assert len(parser.meta_content("name", "twitter:description")) == 1


@pytest.mark.skipif(not REQUIRE_RENDERED, reason="rendered SEO is checked after build")
def test_non_public_pages_are_noindex_and_absent_from_social_metadata() -> None:
    for source in noindex_sources():
        parser = parse_head(source)

        assert parser.meta_content("name", "robots") == ["noindex, follow"]
        assert parser.meta_content("property", "og:url") == []
        assert parser.meta_content("name", "twitter:card") == []


@pytest.mark.skipif(not REQUIRE_RENDERED, reason="rendered SEO is checked after build")
def test_homepage_title_is_not_duplicated() -> None:
    parser = parse_head(DOCS / "index.md")

    assert parser.title == "OpenShell Research"


@pytest.mark.skipif(not REQUIRE_RENDERED, reason="rendered SEO is checked after build")
def test_dev_notes_have_valid_tech_article_metadata() -> None:
    posts = sorted((DOCS / "dev-notes" / "posts").glob("*.md"))

    assert posts
    for post in posts:
        parser = parse_head(post)
        assert len(parser.structured_data) == 1
        article = json.loads(parser.structured_data[0])

        assert article["@context"] == "https://schema.org"
        assert article["@type"] == "TechArticle"
        assert article["mainEntityOfPage"]["@id"] == canonical_url(post)
        assert article["image"].startswith(SITE_URL)
        assert (SITE / article["image"].removeprefix(SITE_URL)).is_file()
        assert parser.meta_content("property", "og:image") == [article["image"]]
        assert parser.meta_content("property", "article:published_time") == [
            article["datePublished"]
        ]
        assert article["author"]["name"]
        assert article["datePublished"]
        assert article["headline"]


@pytest.mark.skipif(not REQUIRE_RENDERED, reason="rendered SEO is checked after build")
def test_seo_metadata_is_confined_to_the_document_head() -> None:
    for source in public_sources() + noindex_sources():
        html = rendered_page(source).read_text(encoding="utf-8")
        body = html.split("<body", maxsplit=1)[1]

        assert 'property="og:' not in body
        assert 'name="twitter:' not in body
        assert 'name="robots"' not in body
        assert 'application/ld+json' not in body
