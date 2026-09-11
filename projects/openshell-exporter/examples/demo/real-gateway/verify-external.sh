#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/../../.." && pwd)
LOCAL_EVIDENCE="$SCRIPT_DIR/runtime/evidence/real-gateway-correlation.json"
BACKEND_EVIDENCE=${1:-${DEMO_EXTERNAL_BACKEND_EVIDENCE:-}}

[ -s "$LOCAL_EVIDENCE" ] || { echo "run the external-enabled real demo first" >&2; exit 1; }
[ -n "$BACKEND_EVIDENCE" ] || {
  echo "usage: ./verify-external.sh /path/to/external-correlated-timeline.json" >&2
  exit 1
}
[ -s "$BACKEND_EVIDENCE" ] || { echo "external backend evidence is missing or empty: $BACKEND_EVIDENCE" >&2; exit 1; }

current_commit=$(git -C "$REPO_ROOT" rev-parse HEAD)
report_commit=$(jq -r '.candidate.repository_commit // empty' "$LOCAL_EVIDENCE")
[ "$report_commit" = "$current_commit" ] || {
  echo "real correlation evidence belongs to $report_commit, current checkout is $current_commit" >&2
  exit 1
}
[ -z "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=normal)" ] || {
  echo "current checkout is dirty; external proof must bind a clean exact candidate" >&2
  exit 1
}
jq -e '.candidate_bound == true and .external_routing_enabled == true' "$LOCAL_EVIDENCE" >/dev/null \
  || { echo "real correlation evidence is not candidate-bound with external routing enabled" >&2; exit 1; }

backend_dir=$(cd -- "$(dirname -- "$BACKEND_EVIDENCE")" && pwd)
backend_name=$(basename -- "$BACKEND_EVIDENCE")
evidence_dir="$SCRIPT_DIR/runtime/evidence"
verification="$evidence_dir/external-correlated-timeline-verification.json"
[ ! -e "$verification" ] || {
  echo "archive the existing external-correlated-timeline-verification.json before a new proof" >&2
  exit 1
}
image=openshell-destination-conformance:real-correlated-timeline

docker build --build-arg VCS_REF="$current_commit" -f "$REPO_ROOT/conformance/Dockerfile" -t "$image" "$REPO_ROOT"
docker run --rm \
  --user "$(id -u):$(id -g)" \
  -e CONFORMANCE_VERIFY_CORRELATED_TIMELINE=true \
  -e CONFORMANCE_REAL_CORRELATION_EVIDENCE=/real/real-gateway-correlation.json \
  -e CONFORMANCE_CORRELATED_BACKEND_EVIDENCE="/backend/$backend_name" \
  -e CONFORMANCE_REPORT_DIR=/evidence \
  -v "$evidence_dir:/real:ro" \
  -v "$backend_dir:/backend:ro" \
  -v "$evidence_dir:/evidence:rw" \
  "$image" -test.run '^TestVerifyExternalCorrelatedTimeline$' -test.v

jq -e '
  .schema_version == "1.0" and
  .result == "passed_external_correlated_timeline" and
  .identity_contract == "urn:openshell:correlation-context:1" and
  (.candidate_commit | test("^[0-9a-f]{40}$")) and
  (.correlation_evidence_sha256 | test("^sha256:[0-9a-f]{64}$")) and
  (.backend_evidence_sha256 | test("^sha256:[0-9a-f]{64}$")) and
  .cloud_events > 0 and
  .relay_spans > 0
' "$verification" >/dev/null
verification_sha256=$(openssl dgst -sha256 "$verification" | awk '{print $NF}')
printf 'External correlated timeline verified: %s (sha256:%s).\n' "$verification" "$verification_sha256"
printf 'This closes only the real correlated-timeline query proof; the other release gates remain separate.\n'
