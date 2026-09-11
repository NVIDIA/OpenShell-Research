#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -eu

if [ "$#" -lt 2 ]; then
  echo "usage: sandbox-ssh-exec.sh SANDBOX COMMAND [ARG ...]" >&2
  exit 2
fi

gateway_endpoint=${OPENSHELL_GATEWAY_ENDPOINT:-}
if [ -z "$gateway_endpoint" ]; then
  echo "OPENSHELL_GATEWAY_ENDPOINT is required" >&2
  exit 1
fi

sandbox_name=$1
shift
ssh_config=$(mktemp)
trap 'rm -f "$ssh_config"' EXIT HUP INT TERM

# Unlike direct API commands, ssh-config resolves stored gateway metadata to
# construct its ProxyCommand. Register the Compose-local endpoint only inside
# this short-lived control container, then address it by its stable local name.
unset OPENSHELL_GATEWAY_ENDPOINT
openshell gateway add "$gateway_endpoint" --local --name exporter-demo >/dev/null
export OPENSHELL_GATEWAY=exporter-demo

openshell sandbox ssh-config "$sandbox_name" >"$ssh_config"
ssh_host=$(awk '$1 == "Host" { print $2; exit }' "$ssh_config")
if [ -z "$ssh_host" ]; then
  echo "OpenShell returned an SSH configuration without a Host entry" >&2
  exit 1
fi

# OpenShell issue #828 documents that the gRPC sandbox-exec stream can receive
# END_STREAM but leave the CLI in a keepalive loop. The SSH path uses the same
# sandbox and policy boundary without depending on that stream completing.
ssh -T \
  -F "$ssh_config" \
  -o BatchMode=yes \
  -o ConnectTimeout=30 \
  "$ssh_host" "$@"
