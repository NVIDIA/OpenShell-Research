#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -eu
umask 077

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
cd "$REPO_ROOT"

fail() {
  printf 'policy API qualification: %s\n' "$*" >&2
  exit 1
}

required_value() {
  name=$1
  value=$(printenv "$name" 2>/dev/null || true)
  [ -n "$value" ] || fail "$name is required"
}

for command_name in git go mkdir printenv stat; do
  command -v "$command_name" >/dev/null 2>&1 || fail "missing required command: $command_name"
done

for name in \
  POLICY_QUALIFICATION_ENDPOINT \
  POLICY_QUALIFICATION_WORKSPACE \
  POLICY_QUALIFICATION_SANDBOX_NAME \
  POLICY_QUALIFICATION_SANDBOX_ID \
  POLICY_QUALIFICATION_NO_DRAFT_SANDBOX \
  POLICY_QUALIFICATION_NOT_FOUND_SANDBOX \
  POLICY_QUALIFICATION_TOKEN_FILE \
  POLICY_QUALIFICATION_UNAUTHORIZED_TOKEN_FILE \
  POLICY_QUALIFICATION_GATEWAY_IMAGE \
  POLICY_QUALIFICATION_CLOUDEVENTS_FILE \
  POLICY_QUALIFICATION_EVIDENCE_DIR \
  POLICY_QUALIFICATION_RUN_ID; do
  required_value "$name"
done

case "$POLICY_QUALIFICATION_RUN_ID" in
  *[!A-Za-z0-9._-]*|'') fail "POLICY_QUALIFICATION_RUN_ID contains an unsafe character" ;;
esac

[ -d "$POLICY_QUALIFICATION_EVIDENCE_DIR" ] || fail "evidence directory does not exist"
[ "$(stat -c '%a' "$POLICY_QUALIFICATION_EVIDENCE_DIR")" = 700 ] ||
  fail "POLICY_QUALIFICATION_EVIDENCE_DIR must have mode 0700"

[ -z "$(git status --porcelain --untracked-files=normal)" ] ||
  fail "candidate worktree is not clean"
CANDIDATE_COMMIT=$(git rev-parse HEAD)
RUN_DIR=$POLICY_QUALIFICATION_EVIDENCE_DIR/$POLICY_QUALIFICATION_RUN_ID
[ ! -e "$RUN_DIR" ] || fail "qualification run already exists: $RUN_DIR"
mkdir "$RUN_DIR"
chmod 700 "$RUN_DIR"

set -- \
  --endpoint "$POLICY_QUALIFICATION_ENDPOINT" \
  --workspace "$POLICY_QUALIFICATION_WORKSPACE" \
  --sandbox-name "$POLICY_QUALIFICATION_SANDBOX_NAME" \
  --sandbox-id "$POLICY_QUALIFICATION_SANDBOX_ID" \
  --no-draft-sandbox "$POLICY_QUALIFICATION_NO_DRAFT_SANDBOX" \
  --not-found-sandbox "$POLICY_QUALIFICATION_NOT_FOUND_SANDBOX" \
  --token-file "$POLICY_QUALIFICATION_TOKEN_FILE" \
  --unauthorized-token-file "$POLICY_QUALIFICATION_UNAUTHORIZED_TOKEN_FILE" \
  --candidate-commit "$CANDIDATE_COMMIT" \
  --candidate-clean \
  --gateway-image "$POLICY_QUALIFICATION_GATEWAY_IMAGE" \
  --expected-gateway-version 0.0.113 \
  --cloudevents-file "$POLICY_QUALIFICATION_CLOUDEVENTS_FILE" \
  --output "$RUN_DIR/report.json" \
  --require-complete

if [ -n "${POLICY_QUALIFICATION_CA_FILE:-}" ]; then
  set -- "$@" --ca-file "$POLICY_QUALIFICATION_CA_FILE"
fi
if [ -n "${POLICY_QUALIFICATION_CLIENT_CERT_FILE:-}" ] || [ -n "${POLICY_QUALIFICATION_CLIENT_KEY_FILE:-}" ]; then
  [ -n "${POLICY_QUALIFICATION_CLIENT_CERT_FILE:-}" ] && [ -n "${POLICY_QUALIFICATION_CLIENT_KEY_FILE:-}" ] ||
    fail "client certificate and key files must be configured together"
  set -- "$@" \
    --cert-file "$POLICY_QUALIFICATION_CLIENT_CERT_FILE" \
    --key-file "$POLICY_QUALIFICATION_CLIENT_KEY_FILE"
fi

cat >"$RUN_DIR/command.txt" <<EOF
candidate_commit=$CANDIDATE_COMMIT
gateway_image=$POLICY_QUALIFICATION_GATEWAY_IMAGE
endpoint=$POLICY_QUALIFICATION_ENDPOINT
workspace=$POLICY_QUALIFICATION_WORKSPACE
sandbox_name=$POLICY_QUALIFICATION_SANDBOX_NAME
sandbox_id=$POLICY_QUALIFICATION_SANDBOX_ID
no_draft_sandbox=$POLICY_QUALIFICATION_NO_DRAFT_SANDBOX
not_found_sandbox=$POLICY_QUALIFICATION_NOT_FOUND_SANDBOX
token_file=$POLICY_QUALIFICATION_TOKEN_FILE
unauthorized_token_file=$POLICY_QUALIFICATION_UNAUTHORIZED_TOKEN_FILE
ca_file=${POLICY_QUALIFICATION_CA_FILE:-}
client_cert_file=${POLICY_QUALIFICATION_CLIENT_CERT_FILE:-}
client_key_file=${POLICY_QUALIFICATION_CLIENT_KEY_FILE:-}
cloudevents_file=$POLICY_QUALIFICATION_CLOUDEVENTS_FILE
invocation=./integration/qualification/qualify-policy-api.sh
EOF
chmod 600 "$RUN_DIR/command.txt"

go run ./cmd/openshell-policy-qualifier "$@"
printf 'policy API qualification report: %s\n' "$RUN_DIR/report.json"
