#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -eu

password_file=/run/secrets/apm-elasticsearch-password
token_file=/run/secrets/apm-secret-token
[ -s "$password_file" ] || { echo "APM Server Elasticsearch password file is missing" >&2; exit 1; }
[ -s "$token_file" ] || { echo "APM Server intake token file is missing" >&2; exit 1; }

ELASTIC_OTLP_PASSWORD=$(cat "$password_file")
ELASTIC_APM_SECRET_TOKEN=$(cat "$token_file")
export ELASTIC_OTLP_PASSWORD ELASTIC_APM_SECRET_TOKEN

exec /usr/share/apm-server/apm-server --environment=container "$@"
