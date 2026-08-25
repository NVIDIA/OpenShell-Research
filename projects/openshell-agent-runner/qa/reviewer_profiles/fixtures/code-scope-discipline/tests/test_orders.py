import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from orders import order_total  # ty: ignore[unresolved-import]


class OrderTotalTests(unittest.TestCase):
    def test_adds_line_items(self) -> None:
        self.assertEqual(order_total([125, 375]), 500)

    def test_empty_order_has_zero_total(self) -> None:
        self.assertEqual(order_total([]), 0)


if __name__ == "__main__":
    unittest.main()
