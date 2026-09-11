#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
resolve_tools
password=$(k -n "$OBS_NAMESPACE" get secret openshell-elastic-credentials -o jsonpath='{.data.ELASTIC_PASSWORD}' | base64 --decode)
printf 'Kibana URL: https://127.0.0.1:15601\nUsername: elastic\nPassword: %s\n' "$password"
unset password
