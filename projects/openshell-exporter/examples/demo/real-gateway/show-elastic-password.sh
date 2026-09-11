#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ENV_FILE="$SCRIPT_DIR/runtime/elastic.env"
[ -f "$ENV_FILE" ] || { echo "run ./run-elastic.sh first" >&2; exit 1; }
# shellcheck disable=SC1090
source "$ENV_FILE"
printf '%s\n' "$ELASTIC_PASSWORD"
