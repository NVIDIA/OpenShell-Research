# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import os
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
HEADER_TEMPLATE = ROOT / "overrides" / "partials" / "header.html"
MAIN_TEMPLATE = ROOT / "overrides" / "main.html"
DRAWER_SCRIPT = ROOT / "docs" / "javascripts" / "navigation-drawer.js"
DRAWER_STYLES = ROOT / "docs" / "stylesheets" / "dev-notes.css"
RENDERED_PAGE = ROOT / "site" / "documentation" / "index.html"


class NavigationDrawerTests(unittest.TestCase):
    def test_header_renders_the_final_control(self) -> None:
        header = HEADER_TEMPLATE.read_text(encoding="utf-8")
        script = DRAWER_SCRIPT.read_text(encoding="utf-8")

        self.assertEqual(header.count("openshell-drawer-button"), 1)
        self.assertIn("openshell-drawer-icon-expand", header)
        self.assertIn("openshell-drawer-icon-collapse", header)
        self.assertNotIn("material/menu", header)
        self.assertNotIn(".innerHTML", script)
        self.assertNotIn("replaceWith", script)

    def test_saved_state_is_available_before_first_render(self) -> None:
        main = MAIN_TEMPLATE.read_text(encoding="utf-8")
        styles = DRAWER_STYLES.read_text(encoding="utf-8")

        self.assertIn("document.documentElement.dataset.navigationDrawer", main)
        self.assertIn(':root[data-navigation-drawer="open"] .md-main', styles)
        self.assertIn(
            ':root[data-navigation-drawer="open"] .openshell-drawer-icon-collapse',
            styles,
        )
        self.assertNotIn("calc(50% - 36rem)", styles)

    def test_rendered_page_contains_one_stable_control(self) -> None:
        if os.environ.get("REQUIRE_RENDERED_NAVIGATION") != "1":
            self.skipTest("rendered output is checked after the documentation build")
        if not RENDERED_PAGE.exists():
            self.fail("the rendered documentation page does not exist")

        html = RENDERED_PAGE.read_text(encoding="utf-8")
        head = html[: html.index("</head>")]

        self.assertEqual(html.count("openshell-drawer-button"), 1)
        self.assertIn("openshell-drawer-icon-expand", html)
        self.assertIn("openshell-drawer-icon-collapse", html)
        self.assertIn("document.documentElement.dataset.navigationDrawer", head)


if __name__ == "__main__":
    unittest.main()
