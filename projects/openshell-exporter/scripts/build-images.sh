#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
fail() { printf 'build images: %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'EOF'
Usage: build-images.sh [exporter|proxy|all] [options]
  --engine docker|podman  Build engine (default: docker)
  --platform PLATFORMS   linux/amd64 or linux/arm64; comma-separated with Docker --push
  --registry PREFIX      Your registry/namespace (default: localhost)
  --push                 Publish to the explicitly supplied registry

Builds from this project directory, regardless of the current working directory.
Local builds never push. Images are named openshell-event-exporter and
openshell-otlp-proxy, tagged v<VERSION>. Authenticate to your registry separately.
EOF
}
product=exporter engine=docker registry=localhost platform= push=false registry_set=false
while (($#)); do
  case "$1" in
    exporter|proxy|all) product=$1; shift ;;
    --engine|--platform|--registry)
      (($# >= 2)) || fail "$1 needs a value"
      case "$1" in
        --engine) engine=$2 ;;
        --platform) platform=$2 ;;
        --registry) registry=${2%/}; registry_set=true ;;
      esac
      shift 2 ;;
    --push) push=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; fail "unknown argument: $1" ;;
  esac
done
case "$engine" in docker|podman) ;; *) fail 'engine must be docker or podman' ;; esac
[[ "$registry" =~ ^[a-z0-9][a-z0-9.:-]*(/[a-z0-9][a-z0-9._-]*)*$ ]] || fail 'invalid registry/namespace'
if [[ "$push" == true ]]; then
  [[ "$registry_set" == true ]] || fail '--push requires --registry'
  host=${registry%%/*}
  [[ "$host" == *.* || "$host" == *:* || "$host" == localhost ]] || fail 'specify the registry hostname, not a short namespace'
  if [[ "$engine" == docker ]]; then
    command -v jq >/dev/null || fail 'jq is required to read publication metadata'
  fi
fi
if [[ -z "$platform" ]]; then
  case "$(uname -m)" in
    x86_64|amd64) platform=linux/amd64 ;;
    aarch64|arm64) platform=linux/arm64 ;;
    *) fail 'set --platform linux/amd64 or linux/arm64' ;;
  esac
fi
[[ "$platform" =~ ^linux/(amd64|arm64)(,linux/(amd64|arm64))*$ ]] || fail 'supported platforms are linux/amd64 and linux/arm64'
if [[ "$platform" == *,* && ( "$engine" != docker || "$push" != true ) ]]; then
  fail 'multiple platforms require Docker --push; local builds use one platform'
fi
command -v "$engine" >/dev/null || fail "$engine is not installed"
version=$(tr -d '\r\n' < "$ROOT/VERSION")
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]] || fail 'invalid VERSION'
revision=$(git -C "$ROOT" rev-parse --verify HEAD 2>/dev/null || printf unknown)
if [[ "$revision" != unknown && -n $(git -C "$ROOT" status --porcelain --untracked-files=normal -- .) ]]; then
  revision+=-dirty
fi
created=$(date -u +%Y-%m-%dT%H:%M:%SZ)
temporary=$(mktemp -d)
trap 'rm -rf -- "$temporary"' EXIT

build() {
  local name=$1 dockerfile=$2 ref metadata digest
  ref="$registry/$name:v$version"
  metadata="$temporary/$name.json"
  local args=(--file "$ROOT/$dockerfile" --tag "$ref" --platform "$platform"
    --build-arg "VERSION=$version" --build-arg "VCS_REF=$revision" --build-arg "CREATED=$created")
  printf 'Building %s (%s)\n' "$ref" "$platform"
  if [[ "$engine" == docker ]]; then
    if [[ "$push" == true ]]; then
      docker buildx build "${args[@]}" --push --metadata-file "$metadata" "$ROOT"
      digest=$(jq -er '.["containerimage.digest"] | select(test("^sha256:[0-9a-f]{64}$"))' "$metadata")
    else
      docker buildx build "${args[@]}" --load "$ROOT"
    fi
  else
    podman build "${args[@]}" "$ROOT"
    if [[ "$push" == true ]]; then
      podman push --digestfile "$metadata" "$ref" "docker://$ref"
      digest=$(cat "$metadata")
      [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || fail 'push returned an invalid digest'
    fi
  fi
  if [[ "$push" == true ]]; then
    printf 'Registry image: %s@%s\n' "${ref%:*}" "$digest"
  else
    "$engine" image inspect --format '{{.Id}}' "$ref"
    printf 'Local image: %s (pull policy: never)\n' "$ref"
  fi
}
case "$product" in
  exporter) build openshell-event-exporter Dockerfile ;;
  proxy) build openshell-otlp-proxy otlpproxy/Dockerfile ;;
  all) build openshell-event-exporter Dockerfile; build openshell-otlp-proxy otlpproxy/Dockerfile ;;
esac
