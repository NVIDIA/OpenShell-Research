#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

docker inspect "$NODE_CONTAINER" >/dev/null 2>&1 || fail "Minikube node container $NODE_CONTAINER is not running"
if docker exec "$NODE_CONTAINER" ctr -n k8s.io images inspect "$AGENT_SANDBOX_IMAGE" >/dev/null 2>&1; then
  exit 0
fi

archive="$RUNTIME_DIR/agent-sandbox-source-$AGENT_SANDBOX_VERSION.tar.gz"
source_dir="$RUNTIME_DIR/agent-sandbox-$AGENT_SANDBOX_VERSION"
pinned_dockerfile="$RUNTIME_DIR/agent-sandbox-controller.Dockerfile"
image_archive="$RUNTIME_DIR/agent-sandbox-controller.oci"
if [[ ! -s "$archive" ]]; then
  note "downloading verified Agent Sandbox $AGENT_SANDBOX_VERSION source"
  download "$AGENT_SANDBOX_SOURCE_URL" "$archive"
fi
sha256_check "$AGENT_SANDBOX_SOURCE_SHA256" "$archive"
if [[ ! -f "$source_dir/Dockerfile" ]]; then
  tar -xzf "$archive" -C "$RUNTIME_DIR"
fi
sed \
  -e "s|golang:1.26.5 AS builder|$AGENT_SANDBOX_GO_IMAGE AS builder|" \
  -e "s|gcr.io/distroless/static-debian13:nonroot|$AGENT_SANDBOX_RUNTIME_IMAGE|" \
  "$source_dir/Dockerfile" >"$pinned_dockerfile"
rg -qF "$AGENT_SANDBOX_GO_IMAGE" "$pinned_dockerfile" || fail "Agent Sandbox Go base pin was not applied"
rg -qF "$AGENT_SANDBOX_RUNTIME_IMAGE" "$pinned_dockerfile" || fail "Agent Sandbox runtime base pin was not applied"
note "building verified Agent Sandbox $AGENT_SANDBOX_VERSION controller for linux/amd64"
docker buildx build --platform linux/amd64 --file "$pinned_dockerfile" \
  --build-arg "GIT_VERSION=v$AGENT_SANDBOX_VERSION" \
  --tag "$AGENT_SANDBOX_IMAGE" --output "type=oci,dest=$image_archive" "$source_dir" >&2
docker exec -i "$NODE_CONTAINER" ctr -n k8s.io images import - <"$image_archive" >/dev/null
docker exec "$NODE_CONTAINER" ctr -n k8s.io images inspect "$AGENT_SANDBOX_IMAGE" >/dev/null
note "loaded Agent Sandbox controller into $NODE_CONTAINER"
