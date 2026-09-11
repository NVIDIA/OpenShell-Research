#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

fail() {
  echo "preflight: $*" >&2
  exit 1
}

for command_name in docker curl jq openssl; do
  command -v "$command_name" >/dev/null 2>&1 || fail "missing required command: $command_name"
done

if [ "$(uname -s)" = "Darwin" ]; then
  command -v security >/dev/null 2>&1 || fail "macOS security command is required for CA export"
fi

docker info >/dev/null 2>&1 || fail "Docker daemon is not reachable; start Docker Desktop or Docker Engine and retry"
docker compose version >/dev/null 2>&1 || fail "Docker Compose plugin is not available"
docker_socket_uri=$(docker context inspect "$(docker context show)" --format "{{ (index .Endpoints \"docker\").Host }}" 2>/dev/null) \
  || fail "could not inspect the active Docker context"
case "$docker_socket_uri" in
  unix://*) docker_socket=${docker_socket_uri#unix://} ;;
  *) fail "the active Docker context must use a Unix socket, got: $docker_socket_uri" ;;
esac
[ -S "$docker_socket" ] || fail "Docker socket is not available at $docker_socket"

host_platform="$(uname -s)/$(uname -m)"
case "$host_platform" in
  Linux/x86_64) ;;
  Darwin/arm64|Darwin/x86_64)
    emulated_arch=$(docker run --rm --platform linux/amd64 \
      docker.io/library/debian:13-slim@sha256:3a39a0592364683e6bab97937b72cad5a8fa6dcbbee90edb3bb48c7f8e94f258 \
      uname -m 2>/dev/null) \
      || fail "Docker Desktop cannot run linux/amd64 images; enable Rosetta x86_64 emulation"
    [ "$emulated_arch" = "x86_64" ] \
      || fail "Docker Desktop returned unexpected linux/amd64 architecture: $emulated_arch"
    ;;
  *) fail "unsupported host platform: $host_platform (expected Linux x86_64 or macOS with Docker Desktop)" ;;
esac
[ -n "${NVIDIA_API_KEY:-}" ] || fail "set NVIDIA_API_KEY to a build.nvidia.com API key; it is passed only to the gateway provider setup"
[ "${#NVIDIA_API_KEY}" -ge 20 ] || fail "NVIDIA_API_KEY is unexpectedly short"

echo "preflight: Docker, Compose, tools, architecture, and NVIDIA credential presence are valid"
