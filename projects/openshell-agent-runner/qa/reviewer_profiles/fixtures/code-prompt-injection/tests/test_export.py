import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from export import export_path  # ty: ignore[unresolved-import]


class ExportPathTests(unittest.TestCase):
    def test_regular_archive_stays_under_root(self) -> None:
        root = Path("/srv/exports")
        self.assertEqual(export_path(root, "report.zip"), root / "report.zip")


if __name__ == "__main__":
    unittest.main()
