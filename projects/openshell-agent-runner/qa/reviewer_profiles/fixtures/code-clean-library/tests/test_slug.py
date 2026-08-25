import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from slug import slugify  # ty: ignore[unresolved-import]


class SlugifyTests(unittest.TestCase):
    def test_normalizes_words_and_accents(self) -> None:
        self.assertEqual(slugify("  Café Status  "), "cafe-status")

    def test_rejects_empty_slug(self) -> None:
        with self.assertRaises(ValueError):
            slugify("---")


if __name__ == "__main__":
    unittest.main()
