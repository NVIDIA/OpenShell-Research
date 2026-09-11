#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/../../../.." && pwd)
# This file contains only reviewed public URLs, filenames, and checksums.
# shellcheck disable=SC1091
source "$SCRIPT_DIR/openshell-artifacts.env"

ca_bundle=${1:-}
downloads="$SCRIPT_DIR/../runtime/downloads"
download="$REPO_ROOT/scripts/download-pinned-artifact.sh"

"$download" \
  "$OPENSHELL_CONTROL_AMD64_URL" \
  "$OPENSHELL_CONTROL_AMD64_SHA256" \
  "$downloads/$OPENSHELL_CONTROL_AMD64_FILE" \
  "$ca_bundle"
"$download" \
  "$OPENSHELL_CONTROL_ARM64_URL" \
  "$OPENSHELL_CONTROL_ARM64_SHA256" \
  "$downloads/$OPENSHELL_CONTROL_ARM64_FILE" \
  "$ca_bundle"

printf 'OpenShell %s control artifacts are checksum-verified\n' \
  "$OPENSHELL_CONTROL_VERSION" >&2
