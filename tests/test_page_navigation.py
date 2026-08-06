# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import os
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "zensical.toml"
STYLES = ROOT / "docs" / "stylesheets" / "dev-notes.css"
DOCUMENTATION_LANDING = ROOT / "site" / "documentation" / "index.html"
EGRESS_GATE_LANDING = ROOT / "site" / "documentation" / "egress-gate" / "index.html"
CONFIGURATION_GUIDE = (
    ROOT / "site" / "documentation" / "egress-gate" / "configuration" / "index.html"
)


class PageNavigationTests(unittest.TestCase):
    def test_landing_page_width_does_not_change_the_shared_header(self) -> None:
        styles = STYLES.read_text(encoding="utf-8")

        self.assertNotIn("body:has(.dev-notes-page) .md-grid", styles)
        self.assertNotIn("body:has(.openshell-home-page) .md-grid", styles)
        self.assertIn(
            "body:has(.openshell-home-page) .md-main__inner.md-grid",
            styles,
        )

    def test_footer_navigation_is_enabled(self) -> None:
        config = CONFIG.read_text(encoding="utf-8")
        styles = STYLES.read_text(encoding="utf-8")

        self.assertIn('"navigation.footer"', config)
        self.assertRegex(
            styles,
            re.compile(
                r':root\[data-navigation-drawer="open"\] \.md-footer\s*\{'
                r"[^}]*padding-left: var\(--openshell-sidebar-width\)",
                re.DOTALL,
            ),
        )

    def test_rendered_links_follow_the_reading_path(self) -> None:
        if os.environ.get("REQUIRE_RENDERED_PAGE_NAVIGATION") != "1":
            self.skipTest("rendered output is checked after the documentation build")

        documentation = DOCUMENTATION_LANDING.read_text(encoding="utf-8")
        egress_gate = EGRESS_GATE_LANDING.read_text(encoding="utf-8")
        configuration = CONFIGURATION_GUIDE.read_text(encoding="utf-8")

        self.assertIn("Back to OpenShell Research", documentation)
        self.assertIn("Next: Egress Gate", documentation)
        self.assertNotIn("Previous: Bringing Privacy", documentation)
        self.assertIn("Previous: Documentation", egress_gate)
        self.assertIn("Next: Configure policies", egress_gate)
        self.assertIn("Previous: Egress Gate", configuration)
        self.assertIn("Next: Test policies offline", configuration)


if __name__ == "__main__":
    unittest.main()
