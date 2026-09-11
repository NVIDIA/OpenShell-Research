#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
target=${1:-}
action=${2:-}

usage() {
  cat >&2 <<'USAGE'
usage: ./deploy/deploy.sh TARGET ACTION

Targets:
  docker          OCI image with Docker Compose
  podman          OCI image with rootless Podman
  kubernetes      Helm-managed exporter beside an OpenShell gateway

Actions:
  validate | up | down | status | logs
USAGE
  exit 2
}

[ -n "$target" ] && [ -n "$action" ] || usage

case "$target" in
  docker)
    exec "$ROOT/docker/manage.sh" "$action"
    ;;
  podman)
    exec "$ROOT/podman/manage.sh" "$action"
    ;;
  kubernetes|k8s)
    exec "$ROOT/kubernetes/manage.sh" "$action"
    ;;
  *)
    usage
    ;;
esac
