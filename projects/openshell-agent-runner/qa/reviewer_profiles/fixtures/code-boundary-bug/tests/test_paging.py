import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from paging import take_page  # ty: ignore[unresolved-import]


class TakePageTests(unittest.TestCase):
    def test_returns_the_requested_number_of_items(self) -> None:
        self.assertEqual(take_page([1, 2, 3, 4], 3), [1, 2, 3])

    def test_rejects_nonpositive_limit(self) -> None:
        with self.assertRaises(ValueError):
            take_page([1], 0)


if __name__ == "__main__":
    unittest.main()
