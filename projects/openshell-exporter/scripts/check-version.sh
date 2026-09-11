#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -eu
script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
repo_root=$(CDPATH= cd "$script_dir/.." && pwd)

fail() {
  printf '%s\n' "$*" >&2
  exit 1
}

version=$(tr -d '\r\n' < "$repo_root/VERSION")
printf '%s\n' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?$' ||
  fail "VERSION must contain MAJOR.MINOR.PATCH with an optional SemVer prerelease; got: $version"

builder="$repo_root/builder-config.yaml"
builder_version=$(awk '/^  version: / {print $2; exit}' "$builder")
[ "$builder_version" = "$version" ] ||
  fail "builder version $builder_version does not match VERSION $version"

module=github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter
awk -v module="$module" -v expected="v$version" '
  $1 == "-" && $2 == "gomod:" && $3 == module {
    count++
    if ($4 != expected) bad = 1
  }
  END { exit !(count == 6 && !bad) }
' "$builder" || fail "local Collector modules do not all use v$version"

proxy_version=$(awk '/^  version: / {print $2; exit}' "$repo_root/otlpproxy/builder-config.yaml")
[ "$proxy_version" = "$version" ] ||
  fail "OTLP proxy version $proxy_version does not match VERSION $version"

awk -v module="$module" -v expected="v$version" '
  $1 == "-" && $2 == "gomod:" && $3 == module {
    count++
    if ($4 != expected) bad = 1
  }
  END { exit !(count == 1 && !bad) }
' "$repo_root/otlpproxy/builder-config.yaml" || fail "OTLP proxy local Collector modules do not all use v$version"

chart="$repo_root/deploy/kubernetes/chart/Chart.yaml"
chart_version=$(awk '/^version: / {print $2; exit}' "$chart")
chart_app_version=$(awk '/^appVersion: / {gsub(/"/, "", $2); print $2; exit}' "$chart")
[ "$chart_version" = "$version" ] ||
  fail "Helm chart version $chart_version does not match VERSION $version"
[ "$chart_app_version" = "$version" ] ||
  fail "Helm appVersion $chart_app_version does not match VERSION $version"

manifest_candidate=$(sed -nE 's/^[[:space:]]*"candidate":[[:space:]]*"([^"]+)".*/\1/p' "$repo_root/release/compatibility-manifest.json")
[ "$manifest_candidate" = "v$version" ] ||
  fail "compatibility candidate $manifest_candidate does not match v$version"

for deployment in docker podman; do
  grep -Fxq "OPENSHELL_EXPORTER_IMAGE=localhost/openshell-event-exporter:v$version" "$repo_root/deploy/$deployment/.env.example" ||
    fail "$deployment example does not reference the local v$version source build"
done

local_image_tag=$(awk '/^  tag: / {print $2; exit}' "$repo_root/deploy/kubernetes/chart/values.local-image.example.yaml")
[ "$local_image_tag" = "v$version" ] ||
  fail "Helm local image tag $local_image_tag does not match v$version"

[ -s "$repo_root/release/notes/v$version.md" ] ||
  fail "release/notes/v$version.md is missing or empty"

release_url="https://github.com/NVIDIA/OpenShell-Research/releases"
for document in README.md docs/compatibility.md docs/release.md; do
  grep -Fq "v$version" "$repo_root/$document" ||
    fail "$document does not identify v$version"
done
for document in README.md docs/compatibility.md docs/release.md; do
  grep -Fq "$release_url" "$repo_root/$document" ||
    fail "$document does not link the v$version GitHub release"
done

if [ "$#" -gt 0 ]; then
  case "$1" in
    "v$version"|"projects/openshell-exporter/v$version") ;;
    *) fail "tag $1 does not match projects/openshell-exporter/v$version" ;;
  esac
fi
printf '%s\n' "$version"
