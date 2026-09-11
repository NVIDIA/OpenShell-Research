#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -eu

password_file=/run/secrets/elasticsearch-password
[ -s "$password_file" ] || {
  echo "Logstash Elasticsearch password file is missing" >&2
  exit 1
}
recovery_dir=/usr/share/logstash/data/recovery
mkdir -p "$recovery_dir"
chmod 0750 "$recovery_dir"

ELASTIC_INGEST_PASSWORD=$(cat "$password_file")
export ELASTIC_INGEST_PASSWORD

exec /usr/local/bin/docker-entrypoint "$@"
