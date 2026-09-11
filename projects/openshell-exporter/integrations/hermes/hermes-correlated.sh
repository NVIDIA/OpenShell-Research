#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -eu
umask 077

sandbox_id=${HERMES_OPENSHELL_SANDBOX_ID:-}
[ -n "$sandbox_id" ] || {
  echo "authorized launcher did not provide HERMES_OPENSHELL_SANDBOX_ID" >&2
  exit 1
}
sandbox_name=${HERMES_OPENSHELL_SANDBOX_NAME:-unknown}
policy_version=${HERMES_OPENSHELL_POLICY_VERSION:-}
case "$sandbox_id:$sandbox_name" in
  *[!A-Za-z0-9._:-]*)
    echo "OpenShell sandbox identity contains unsupported characters" >&2
    exit 1
    ;;
esac
case "$policy_version" in
  ""|*[!0-9]*)
    if [ -n "$policy_version" ]; then
      echo "OpenShell policy version contains unsupported characters" >&2
      exit 1
    fi
    ;;
  *) ;;
esac

template=${HERMES_NEMO_RELAY_TEMPLATE:-/sandbox/.hermes/nemo-relay/plugins.toml}
runtime=${HERMES_NEMO_RELAY_RUNTIME:-/sandbox/.hermes/nemo-relay/runtime-plugins.toml}
hermes_binary=${HERMES_BINARY:-/usr/local/bin/hermes}
[ -r "$template" ] || {
  echo "NeMo Relay configuration template is not readable" >&2
  exit 1
}
[ -x "$hermes_binary" ] || {
  echo "Hermes binary is not executable" >&2
  exit 1
}

if [ -n "$policy_version" ]; then
  sed \
    -e "s/__OPENSHELL_SANDBOX_ID__/$sandbox_id/g" \
    -e "s/__OPENSHELL_SANDBOX_NAME__/$sandbox_name/g" \
    -e "s/__OPENSHELL_POLICY_VERSION__/$policy_version/g" \
    "$template" >"$runtime"
else
  sed \
    -e "s/__OPENSHELL_SANDBOX_ID__/$sandbox_id/g" \
    -e "s/__OPENSHELL_SANDBOX_NAME__/$sandbox_name/g" \
    -e '/__OPENSHELL_POLICY_VERSION__/d' \
    "$template" >"$runtime"
fi
chmod 0600 "$runtime"
export HERMES_NEMO_RELAY_PLUGINS_TOML=$runtime

# Provider v2 exposes only an opaque, endpoint-bound placeholder. NeMo Relay
# expects the complete Authorization header value, so construct that value in
# process memory without persisting or logging it. The OpenShell proxy replaces
# the placeholder only for the profile-bound OTLP endpoint.
if [ -z "${NEMO_RELAY_OTEL_AUTHORIZATION:-}" ] && [ -n "${NEMO_RELAY_OTLP_TOKEN:-}" ]; then
  export NEMO_RELAY_OTEL_AUTHORIZATION="Bearer ${NEMO_RELAY_OTLP_TOKEN}"
fi
exec "$hermes_binary" "$@"
