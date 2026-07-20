# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import datetime as dt
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "render-dev-notes.py"
SPEC = importlib.util.spec_from_file_location("render_dev_notes", SCRIPT_PATH)
assert SPEC and SPEC.loader
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


def make_post(
    filename: str,
    *,
    date: str = "2026-06-05",
    title: str = "A Dev Note",
    description: str = "What the experiment taught us.",
    categories: list[str] | None = None,
    tags: list[str] | None = None,
    card_tags: list[str] | None = None,
    variant: str = "",
    hero_image: str = "",
) -> dict[str, object]:
    path = renderer.POSTS_DIR / filename
    metadata: dict[str, object] = {
        "title": title,
        "date": date,
        "description": description,
        "categories": categories or ["Systems"],
        "tags": tags or ["agents", "runtime"],
    }
    if card_tags is not None:
        metadata["card_tags"] = card_tags
    if variant:
        metadata["card_variant"] = variant
    if hero_image:
        metadata["hero_image"] = hero_image
    return {
        "path": path,
        "metadata": metadata,
        "frontmatter": "",
        "body": f"# {title}\n",
        "authors": [{"name": "Ada Lovelace", "github": "ada"}],
        "published": renderer.parse_date(date, path),
    }


class DateValidationTests(unittest.TestCase):
    def test_accepts_only_canonical_calendar_dates(self) -> None:
        path = Path("post.md")
        self.assertEqual(renderer.parse_date("2026-01-02", path), dt.date(2026, 1, 2))

        for value in ("20260102", "2026-1-02", "２０２６-０１-０２", "2026-02-30"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                renderer.parse_date(value, path)

    def test_discovery_sorts_by_parsed_date_then_title(self) -> None:
        authors = {"ada": {"name": "Ada Lovelace", "github": "ada"}}
        with tempfile.TemporaryDirectory() as directory:
            posts_dir = Path(directory)
            for filename, title, date in (
                ("older.md", "Older", "2025-12-31"),
                ("zeta.md", "Zeta", "2026-01-01"),
                ("alpha.md", "Alpha", "2026-01-01"),
            ):
                (posts_dir / filename).write_text(
                    "\n".join(
                        (
                            "---",
                            f"title: {title}",
                            f"date: {date}",
                            "description: Test post.",
                            "authors:",
                            "  - ada",
                            "---",
                            f"# {title}",
                            "",
                        )
                    ),
                    encoding="utf-8",
                )

            with mock.patch.object(renderer, "POSTS_DIR", posts_dir):
                posts = renderer.discover_posts(authors)

        self.assertEqual([post["metadata"]["title"] for post in posts], ["Zeta", "Alpha", "Older"])


class CardRenderingTests(unittest.TestCase):
    def test_zero_one_and_many_post_layouts(self) -> None:
        first = make_post("2026-06-05-first.md")
        second = make_post("2026-06-04-second.md", date="2026-06-04", title="Second")
        third = make_post("2026-06-03-third.md", date="2026-06-03", title="Third")

        self.assertIn("first Dev Note", renderer.render_index_cards([]))
        one = renderer.render_index_cards([first])
        self.assertIn("Featured note", one)
        self.assertNotIn("Recent notes", one)
        many = renderer.render_index_cards([first, second, third])
        self.assertEqual(many.count("dev-note-card--recent"), 2)

    def test_visual_stamp_uses_stable_canonical_date(self) -> None:
        post = make_post("2026-06-05-note.md")
        visual = renderer.render_card_visual(post)
        self.assertIn("2026.06.05", visual)
        self.assertNotIn("OSR—", visual)

    def test_card_tags_override_tags_and_variant_reaches_outer_card(self) -> None:
        post = make_post(
            "2026-06-05-note.md",
            tags=["fallback"],
            card_tags=["preferred", "evaluation"],
            variant="launch",
        )
        card = renderer.render_featured_card(post)
        self.assertIn("dev-note-card--launch", card)
        self.assertIn(">preferred<", card)
        self.assertIn(">evaluation<", card)
        self.assertNotIn(">fallback<", card)

    def test_hero_image_replaces_generated_visual(self) -> None:
        post = make_post(
            "2026-06-05-note.md",
            hero_image="../../assets/reachy-mini-openshell/hero.svg",
        )

        featured = renderer.render_featured_card(post)
        self.assertIn("dev-note-card--has-image", featured)
        self.assertIn("dev-note-card__visual--image", featured)
        self.assertIn('src="../assets/reachy-mini-openshell/hero.svg"', featured)
        self.assertIn('loading="eager" fetchpriority="high"', featured)
        self.assertNotIn("dev-note-card__visual-label", featured)

        recent = renderer.render_recent_card(post)
        self.assertIn('loading="lazy"', recent)
        self.assertNotIn("fetchpriority", recent)

    def test_hero_image_must_exist_inside_docs(self) -> None:
        outside = make_post("2026-06-05-note.md", hero_image="../../../../outside.svg")
        with self.assertRaisesRegex(ValueError, "must stay inside"):
            renderer.render_featured_card(outside)

        missing = make_post("2026-06-05-note.md", hero_image="../../assets/missing.svg")
        with self.assertRaisesRegex(ValueError, "does not exist"):
            renderer.render_featured_card(missing)

    def test_metadata_is_html_escaped(self) -> None:
        post = make_post(
            "2026-06-05-note.md",
            title="Runtime <script>",
            description=r"Literal \1 and <strong>markup</strong>",
            card_tags=["<unsafe>"],
        )
        card = renderer.render_featured_card(post)
        self.assertNotIn("<script>", card)
        self.assertIn("Runtime &lt;script&gt;", card)
        self.assertIn(r"Literal \1", card)
        self.assertIn("&lt;unsafe&gt;", card)

    def test_invalid_variant_is_rejected(self) -> None:
        post = make_post("2026-06-05-note.md", variant="launch mode")
        with self.assertRaises(ValueError):
            renderer.render_featured_card(post)


class MarkerReplacementTests(unittest.TestCase):
    def test_replacement_is_literal_and_idempotent(self) -> None:
        start = "<!-- start -->"
        end = "<!-- end -->"
        replacement = start + r"\1 \g<missing> C:\notes" + end
        original = f"before\n{start}\nstale\n{end}\nafter"
        path = Path("fixture.md")

        once = renderer.replace_between_markers(original, start, end, replacement, path)
        twice = renderer.replace_between_markers(once, start, end, replacement, path)

        self.assertEqual(once, twice)
        self.assertIn(r"\1 \g<missing> C:\notes", once)

    def test_existing_byline_replacement_preserves_backslashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "post.md"
            post = make_post("post.md")
            post["path"] = path
            post["frontmatter"] = "title: A Dev Note\ndate: 2026-06-05"
            post["body"] = (
                "# A Dev Note\n\n"
                f"{renderer.BYLINE_START}\nstale\n{renderer.BYLINE_END}\n\n"
                "Body.\n"
            )
            post["authors"] = [
                {"name": "Ada Lovelace", "github": "ada", "description": r"Lab C:\notes\1"}
            ]
            path.write_text("placeholder", encoding="utf-8")

            renderer.update_post_byline(post)

            rendered = path.read_text(encoding="utf-8")
        self.assertIn(r"Lab C:\notes\1", rendered)


if __name__ == "__main__":
    unittest.main()
