# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""One fixed cancellation example, not a general numerical benchmark."""

import json
import math

values = [1e16, 1.0, -1e16]
left_to_right = 0.0
for value in values:
    left_to_right += value
result = {"left_to_right": left_to_right, "compensated": math.fsum(values)}
assert result == {"left_to_right": 0.0, "compensated": 1.0}
print(json.dumps(result))
