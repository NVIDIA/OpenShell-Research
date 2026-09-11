#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

import_oci() {
  local archive=$1
  if ! docker exec -i "$NODE_CONTAINER" ctr -n k8s.io images import - <"$archive" >/dev/null; then
    fail "failed to import OCI archive $archive into $NODE_CONTAINER"
  fi
}

build_oci() {
  local name=$1 dockerfile=$2 tag=$3
  local archive="$RUNTIME_DIR/$name.oci" metadata="$RUNTIME_DIR/$name.metadata.json"
  local sandbox_revision=""
  local -a build_secrets=() build_args=()
  if [[ "$name" == sandbox && -r /etc/ssl/certs/ca-certificates.crt ]]; then
    build_secrets+=(--secret id=host_ca_bundle,src=/etc/ssl/certs/ca-certificates.crt)
  fi
  if [[ "$name" == sandbox ]]; then
    [[ -s "$RUNTIME_DIR/openshell-exporter-root-ca.crt" ]] || fail "run install-dependencies.sh before building the sandbox image"
    build_secrets+=(--secret id=openshell_exporter_ca,src="$RUNTIME_DIR/openshell-exporter-root-ca.crt")
    sandbox_revision=$(sha256sum "$dockerfile" "$DEMO_DIR/sandbox/config.yaml" "$DEMO_DIR/sandbox/plugins.toml" "$REPO_ROOT/integrations/hermes/hermes-correlated.sh" "$RUNTIME_DIR/openshell-exporter-root-ca.crt" | sha256sum | awk '{print $1}') \
      || fail "failed to calculate the sandbox build revision"
    build_args+=(--build-arg "OPENSHELL_DEMO_BUILD_REVISION=$sandbox_revision")
  fi
  note "building $name for linux/amd64" >&2
  rm -f -- "$archive" "$metadata" || fail "failed to clear stale $name build outputs"
  if ! docker buildx build --platform linux/amd64 --file "$dockerfile" --tag "$tag" \
    "${build_secrets[@]}" "${build_args[@]}" --output "type=oci,dest=$archive" --metadata-file "$metadata" "$REPO_ROOT" >&2; then
    fail "failed to build $name OCI archive"
  fi
  [[ -s "$archive" ]] || fail "$name build produced no OCI archive"
  [[ -s "$metadata" ]] || fail "$name build produced no metadata"
  import_oci "$archive"
  local digest repository
  digest=$(jq -er '.["containerimage.digest"] | select(test("^sha256:[0-9a-f]{64}$"))' "$metadata") \
    || fail "failed to extract a valid digest from $metadata"
  repository=${tag%:*}
  if ! docker exec "$NODE_CONTAINER" ctr -n k8s.io images tag "$tag" "$repository@$digest" >/dev/null; then
    fail "failed to tag imported $name image as $repository@$digest"
  fi
  printf '%s\n' "$digest"
}

main() {
  resolve_tools
  docker inspect "$NODE_CONTAINER" >/dev/null 2>&1 \
    || fail "Minikube node container $NODE_CONTAINER is not running"
  local ca_bundle=""
  if [[ -r /etc/ssl/certs/ca-certificates.crt ]]; then
    ca_bundle=/etc/ssl/certs/ca-certificates.crt
  fi
  "$REPO_ROOT/examples/demo/real-gateway/control/prepare-build-context.sh" "$ca_bundle" \
    || fail "failed to prepare the checksum-pinned OpenShell control artifacts"

  local exporter_digest control_digest sandbox_digest images_env
  exporter_digest=$(build_oci exporter "$REPO_ROOT/Dockerfile" docker.io/local/openshell-event-exporter:kubernetes-demo) \
    || fail "exporter image build pipeline failed"
  control_digest=$(build_oci control "$REPO_ROOT/examples/demo/real-gateway/control/Dockerfile" docker.io/local/openshell-control:kubernetes-demo) \
    || fail "control image build pipeline failed"
  sandbox_digest=$(build_oci sandbox "$DEMO_DIR/sandbox/Dockerfile" docker.io/local/openshell-sandbox:kubernetes-demo) \
    || fail "sandbox image build pipeline failed"

  images_env=$(mktemp "$RUNTIME_DIR/images.env.XXXXXX") \
    || fail "failed to create the image digest manifest"
  cat >"$images_env" <<ENV
EXPORTER_DIGEST=$exporter_digest
CONTROL_DIGEST=$control_digest
SANDBOX_DIGEST=$sandbox_digest
ENV
  chmod 0600 "$images_env" || fail "failed to protect the image digest manifest"
  mv -f -- "$images_env" "$RUNTIME_DIR/images.env" \
    || fail "failed to publish the image digest manifest"
  note "loaded exporter, OpenShell control, and Hermes/NeMo Relay sandbox images into $NODE_CONTAINER"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
