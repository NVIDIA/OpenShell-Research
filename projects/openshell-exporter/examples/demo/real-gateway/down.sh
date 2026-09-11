#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ENV_FILE="$SCRIPT_DIR/runtime/demo.env"
[ -f "$ENV_FILE" ] || { echo "nothing to stop"; exit 0; }
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$SCRIPT_DIR/compose.yaml")

if curl --fail --silent http://127.0.0.1:8081/healthz >/dev/null 2>&1; then
  "${COMPOSE[@]}" run --rm control sandbox delete hermes-demo || true
fi
"${COMPOSE[@]}" down

echo "Stopped the demo and deleted hermes-demo. Gateway state, OCSF volume, exporter queues, and recovery files were preserved."
