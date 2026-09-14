# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""gRPC transport and servicer for the Egress Gate middleware."""

from egress_gate.service.server import EgressGateServer
from egress_gate.service.servicer import EgressGateMiddleware

__all__ = ["EgressGateMiddleware", "EgressGateServer"]
