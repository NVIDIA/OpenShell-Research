# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import os
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "overrides" / "404.html"
RENDERED = ROOT / "site" / "404.html"


class Docs404Tests(unittest.TestCase):
    def test_template_is_standalone_and_self_contained(self) -> None:
        html = TEMPLATE.read_text(encoding="utf-8")

        self.assertTrue(html.lstrip().lower().startswith("<!doctype html>"))
        self.assertNotIn("{% extends", html)
        self.assertNotRegex(html, r"<(?:img|script)[^>]+src=")
        self.assertNotRegex(html, r"<link[^>]+rel=[\"']stylesheet")
        self.assertNotRegex(html, r"https?://")
        self.assertIn("<style>", html)
        self.assertIn("<main aria-labelledby=", html)
        self.assertIn("prefers-color-scheme: dark", html)

    def test_template_handles_expired_preview_paths(self) -> None:
        html = TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("pr-preview", html)
        self.assertIn("Preview unavailable", html)
        self.assertIn("This pull request preview has expired or was removed.", html)

    def test_rendered_page_has_no_root_asset_dependencies(self) -> None:
        if os.environ.get("REQUIRE_RENDERED_404") != "1":
            self.skipTest("rendered output is checked after the documentation build")
        if not RENDERED.exists():
            self.fail("site/404.html was not generated")

        html = RENDERED.read_text(encoding="utf-8")
        asset_reference = re.compile(
            r"(?:href|src)=[\"']/(?:assets|stylesheets|javascripts)/",
        )
        self.assertNotRegex(html, asset_reference)
        self.assertIn("<style>", html)
        self.assertIn("This pull request preview has expired or was removed.", html)


if __name__ == "__main__":
    unittest.main()
