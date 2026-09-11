#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ELASTIC_ENV="$SCRIPT_DIR/runtime/elastic.env"
"$SCRIPT_DIR/down.sh"
if [ -f "$ELASTIC_ENV" ]; then
  docker compose --env-file "$ELASTIC_ENV" \
    -f "$SCRIPT_DIR/compose.yaml" \
    -f "$SCRIPT_DIR/compose.elastic.yaml" \
    down
fi
echo "Elastic containers stopped; the Elasticsearch data volume was preserved."
