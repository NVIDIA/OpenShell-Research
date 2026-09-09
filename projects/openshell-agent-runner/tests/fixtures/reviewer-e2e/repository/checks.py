# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run with python3 checks.py; no dependencies or installation required."""

import unittest

from src.totals import arithmetic_mean


class MeanTests(unittest.TestCase):
    def test_positive_values(self):
        self.assertEqual(arithmetic_mean([2.0, 4.0, 6.0]), 4.0)

    def test_mixed_sign_values(self):
        self.assertEqual(arithmetic_mean([-4.0, 2.0]), -1.0)


if __name__ == "__main__":
    unittest.main()
