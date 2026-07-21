# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from html.parser import HTMLParser
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SITE = ROOT / "site"
SCRIPT = ROOT / "scripts" / "publish-agent-markdown.py"

SPEC = importlib.util.spec_from_file_location("publish_agent_markdown", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"could not load {SCRIPT}")
PUBLISHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PUBLISHER)


class MarkdownDiscoveryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.actions: list[str] = []
        self.alternates: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if (
            tag == "a"
            and attributes.get("title") == "View Markdown"
            and attributes.get("aria-label") == "View Markdown"
            and {"md-content__button", "md-icon"}
            <= set((attributes.get("class") or "").split())
        ):
            self.actions.append(attributes.get("href") or "")
        if (
            tag == "link"
            and "alternate" in (attributes.get("rel") or "").split()
            and attributes.get("type") == "text/markdown"
            and attributes.get("title") == "Markdown source"
        ):
            self.alternates.append(attributes.get("href") or "")


def parse_discovery(html: Path) -> MarkdownDiscoveryParser:
    parser = MarkdownDiscoveryParser()
    parser.feed(html.read_text(encoding="utf-8"))
    return parser


class AgentMarkdownUnitTests(unittest.TestCase):
    def test_all_canonical_content_opts_in(self) -> None:
        sources = PUBLISHER.eligible_sources(DOCS)

        self.assertTrue(sources)
        self.assertTrue(all(PUBLISHER.has_agent_markdown(path) for path in sources))

    def test_missing_front_matter_marker_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            docs = Path(temporary_directory) / "docs"
            (docs / "dev-notes/posts").mkdir(parents=True)
            (docs / "documentation").mkdir()
            (docs / "dev-notes/posts/note.md").write_text(
                "---\ntitle: Note\n---\n\n# Note\n", encoding="utf-8"
            )
            (docs / "documentation/index.md").write_text(
                "---\nagent_markdown: true\n---\n\n# Docs\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "agent_markdown: true"):
                PUBLISHER.eligible_sources(docs)

    def test_relative_href_mapping(self) -> None:
        self.assertEqual(PUBLISHER.markdown_href(Path("documentation/index.md")), "index.md")
        self.assertEqual(
            PUBLISHER.markdown_href(Path("dev-notes/posts/example.md")),
            "../example.md",
        )

    def test_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            docs = root / "docs"
            site = root / "site"
            docs.mkdir()
            site.mkdir()

            with self.assertRaisesRegex(ValueError, "path escapes"):
                PUBLISHER.markdown_destination(root / "outside.md", docs, site)

    def test_publish_copies_sources_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            docs = root / "docs"
            site = root / "site"
            post = docs / "dev-notes/posts/example.md"
            documentation = docs / "documentation/index.md"
            post.parent.mkdir(parents=True)
            documentation.parent.mkdir(parents=True)
            site.mkdir()
            post_bytes = b"---\r\nagent_markdown: true\r\n---\r\n\r\n# Post\r\n"
            documentation_bytes = b"---\nagent_markdown: true\n---\n\n# Documentation\n"
            post.write_bytes(post_bytes)
            documentation.write_bytes(documentation_bytes)
            for source in (post, documentation):
                rendered = PUBLISHER.rendered_html(source, docs, site)
                rendered.parent.mkdir(parents=True, exist_ok=True)
                rendered.write_text("<!doctype html>", encoding="utf-8")

            destinations = PUBLISHER.publish(docs, site)

            self.assertEqual(len(destinations), 2)
            self.assertEqual((site / "dev-notes/posts/example.md").read_bytes(), post_bytes)
            self.assertEqual(
                (site / "documentation/index.md").read_bytes(), documentation_bytes
            )


@unittest.skipUnless(
    os.environ.get("REQUIRE_RENDERED_AGENT_MARKDOWN") == "1",
    "rendered output is checked after the documentation build",
)
class AgentMarkdownRenderedTests(unittest.TestCase):
    def test_eligible_pages_publish_matching_discovery_links(self) -> None:
        for source in PUBLISHER.eligible_sources(DOCS):
            with self.subTest(source=source.relative_to(DOCS)):
                rendered = PUBLISHER.rendered_html(source, DOCS, SITE)
                export = PUBLISHER.markdown_destination(source, DOCS, SITE)
                self.assertTrue(rendered.is_file(), f"missing {rendered}")
                self.assertTrue(export.is_file(), f"missing {export}")
                self.assertEqual(export.read_bytes(), source.read_bytes())

                discovery = parse_discovery(rendered)
                expected_href = PUBLISHER.markdown_href(source)
                self.assertEqual(discovery.actions, [expected_href])
                self.assertEqual(discovery.alternates, [expected_href])
                linked_export = (rendered.parent / expected_href).resolve()
                self.assertEqual(linked_export, export.resolve())
                self.assertTrue(linked_export.is_file())

    def test_ineligible_pages_have_no_markdown_discovery_links(self) -> None:
        eligible_html = {
            PUBLISHER.rendered_html(source, DOCS, SITE).resolve()
            for source in PUBLISHER.eligible_sources(DOCS)
        }
        rendered_pages = sorted(SITE.rglob("*.html"))
        self.assertTrue(rendered_pages)

        for rendered in rendered_pages:
            if rendered.resolve() in eligible_html:
                continue
            with self.subTest(rendered=rendered.relative_to(SITE)):
                discovery = parse_discovery(rendered)
                self.assertEqual(discovery.actions, [])
                self.assertEqual(discovery.alternates, [])


if __name__ == "__main__":
    unittest.main()
