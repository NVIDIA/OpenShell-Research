#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -eu
umask 077

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)

fail() {
  printf 'OCSF file lifecycle qualification: %s\n' "$*" >&2
  exit 1
}

usage() {
  printf 'usage: OCSF_LIFECYCLE_RUN_ID=<id> OCSF_LIFECYCLE_EVIDENCE_DIR=<secure-dir> %s prepare|execute\n' "$0" >&2
  exit 2
}

command_required() {
  command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
}

required_value() {
  variable=$1
  value=$(printenv "$variable" 2>/dev/null || true)
  [ -n "$value" ] || fail "$variable is required"
}

number_greater_than() {
  awk -v left="$1" -v right="$2" 'BEGIN { exit !(left > right) }'
}

number_is_zero() {
  awk -v value="$1" 'BEGIN { exit !(value == 0) }'
}

metric_sum() {
  metrics_file=$1
  metric_name=$2
  component_match=$3
  awk -v metric="$metric_name" -v component="$component_match" '
    $0 !~ /^#/ &&
    ($1 == metric || index($1, metric "{") == 1) &&
    (component == "" || index($0, component) > 0) {
      sum += $NF
      found = 1
    }
    END {
      if (!found) exit 2
      printf "%.17g\n", sum + 0
    }
  ' "$metrics_file"
}

sha256_file() {
  sha256sum "$1" | awk '{ print "sha256:" $1 }'
}

validate_digest_reference() {
  printf '%s\n' "$1" | grep -Eq '^.+@sha256:[0-9a-f]{64}$' ||
    fail "$2 must be an immutable sha256 digest reference"
}

validate_hook() {
  hook_name=$1
  hook_path=$2
  case "$hook_path" in
    /*) ;;
    *) fail "$hook_name must be an absolute path" ;;
  esac
  [ -f "$hook_path" ] && [ -x "$hook_path" ] || fail "$hook_name must be an executable regular file"
}

validate_status() {
  status=$1
  expected=$2
  case "$status" in
    *[!0-9]*|'') fail "$expected hook must print one HTTP status code" ;;
  esac
  if [ "$expected" = outage ]; then
    [ "$status" -eq 503 ] || fail "destination outage hook returned $status instead of 503"
  else
    [ "$status" -ge 200 ] && [ "$status" -lt 300 ] ||
      fail "destination restore hook returned non-2xx status $status"
  fi
}

fetch_metrics() {
  output=$1
  curl --fail --silent --show-error --max-time 10 "$METRICS_URL" >"$output"
}

write_metric_point() {
  label=$1
  scrape=$2
  queue_size=$(metric_sum "$scrape" otelcol_exporter_queue_size "$QUEUE_COMPONENT_MATCH") ||
    fail "CloudEvents queue-size metric was not found; set OCSF_LIFECYCLE_QUEUE_COMPONENT_MATCH"
  queue_capacity=$(metric_sum "$scrape" otelcol_exporter_queue_capacity "$QUEUE_COMPONENT_MATCH") ||
    fail "CloudEvents queue-capacity metric was not found; set OCSF_LIFECYCLE_QUEUE_COMPONENT_MATCH"
  # Prometheus counters are absent until their first observation. A fresh
  # exporter process can therefore expose no delivery-events series while a
  # persisted request is retrying after restart. Treat absent counters as zero;
  # queue gauges remain mandatory because they prove the component is present.
  retryable_failures=$(metric_sum "$scrape" openshell_exporter_delivery_retryable_failures "" 2>/dev/null || printf '0\n')
  delivered_events=$(metric_sum "$scrape" openshell_exporter_delivery_events "" 2>/dev/null || printf '0\n')
  metrics_hash=$(sha256_file "$scrape")
  captured_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  jq \
    --arg point_label "$label" \
    --arg captured_at "$captured_at" \
    --arg metrics_sha256 "$metrics_hash" \
    --argjson queue_size "$queue_size" \
    --argjson queue_capacity "$queue_capacity" \
    --argjson retryable_failures "$retryable_failures" \
    --argjson delivered_events "$delivered_events" \
    '.[$point_label] = {
      captured_at: $captured_at,
      queue_size: $queue_size,
      queue_capacity: $queue_capacity,
      retryable_failures: $retryable_failures,
      delivered_events: $delivered_events,
      metrics_sha256: $metrics_sha256
    }' "$METRICS_JSON" >"$METRICS_TMP"
  mv "$METRICS_TMP" "$METRICS_JSON"
}

capture_metrics() {
  label=$1
  scrape="$RUN_DIR/metrics-$label.prom"
  fetch_metrics "$scrape"
  write_metric_point "$label" "$scrape"
}

wait_for_health() {
  deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if curl --fail --silent --show-error --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then
      return
    fi
    sleep 2
  done
  fail "exporter health did not recover within ${TIMEOUT_SECONDS}s"
}

wait_for_outage() {
  baseline_queue=$(jq -r '.baseline.queue_size' "$METRICS_JSON")
  baseline_retry=$(jq -r '.baseline.retryable_failures' "$METRICS_JSON")
  deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
  scrape="$RUN_DIR/metrics-outage.prom"
  while [ "$(date +%s)" -lt "$deadline" ]; do
    fetch_metrics "$scrape"
    queue=$(metric_sum "$scrape" otelcol_exporter_queue_size "$QUEUE_COMPONENT_MATCH" 2>/dev/null || printf '0\n')
    retry=$(metric_sum "$scrape" openshell_exporter_delivery_retryable_failures "" 2>/dev/null || printf '0\n')
    if number_greater_than "$queue" "$baseline_queue" && number_greater_than "$retry" "$baseline_retry"; then
      write_metric_point outage "$scrape"
      return
    fi
    sleep 2
  done
  fail "persistent queue and retry counter did not grow during the 503 outage"
}

wait_for_queue_after_restart() {
  deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
  scrape="$RUN_DIR/metrics-after_restart.prom"
  while [ "$(date +%s)" -lt "$deadline" ]; do
    fetch_metrics "$scrape"
    queue=$(metric_sum "$scrape" otelcol_exporter_queue_size "$QUEUE_COMPONENT_MATCH" 2>/dev/null || printf '0\n')
    if number_greater_than "$queue" 0; then
      write_metric_point after_restart "$scrape"
      return
    fi
    sleep 2
  done
  fail "persistent queue was empty after exporter restart"
}

wait_for_drain() {
  post_restart_delivered=$(jq -r '.after_restart.delivered_events' "$METRICS_JSON")
  deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
  scrape="$RUN_DIR/metrics-drained.prom"
  while [ "$(date +%s)" -lt "$deadline" ]; do
    fetch_metrics "$scrape"
    queue=$(metric_sum "$scrape" otelcol_exporter_queue_size "$QUEUE_COMPONENT_MATCH" 2>/dev/null || printf '1\n')
    delivered=$(metric_sum "$scrape" openshell_exporter_delivery_events "" 2>/dev/null || printf '0\n')
    if number_is_zero "$queue" && number_greater_than "$delivered" "$post_restart_delivered"; then
      write_metric_point drained "$scrape"
      return
    fi
    sleep 2
  done
  fail "persistent queue did not drain after destination recovery"
}

directory_file_count() {
  find "$1" -type f -print | wc -l | tr -d ' '
}

directory_bytes() {
  find "$1" -type f -printf '%s\n' | awk '{ total += $1 } END { printf "%.0f\n", total + 0 }'
}

directory_manifest_hash() {
  find "$1" -type f -printf '%P\t%s\t%T@\n' | sort | sha256sum | awk '{ print "sha256:" $1 }'
}

storage_role() {
  role=$1
  path=$2
  resolved=$(realpath "$path")
  [ -d "$resolved" ] && [ -r "$resolved" ] || fail "$role storage path is not a readable directory"
  jq -n \
    --arg role "$role" \
    --arg path "$resolved" \
    --argjson device "$(stat -c %d "$resolved")" \
    --argjson inode "$(stat -c %i "$resolved")" \
    --argjson file_count "$(directory_file_count "$resolved")" \
    --argjson bytes "$(directory_bytes "$resolved")" \
    --arg manifest_sha256 "$(directory_manifest_hash "$resolved")" \
    '{key: $role, value: {
      path: $path,
      device: $device,
      inode: $inode,
      file_count: $file_count,
      bytes: $bytes,
      manifest_sha256: $manifest_sha256
    }}'
}

capture_storage() {
  output=$1
  captured_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  storage_role checkpoints "$CHECKPOINT_DIR" >"$RUN_DIR/storage-checkpoints.tmp"
  storage_role queue "$QUEUE_DIR" >"$RUN_DIR/storage-queue.tmp"
  storage_role recovery "$RECOVERY_DIR" >"$RUN_DIR/storage-recovery.tmp"
  jq -s --arg captured_at "$captured_at" '{captured_at: $captured_at, roles: (from_entries)}' \
    "$RUN_DIR/storage-checkpoints.tmp" "$RUN_DIR/storage-queue.tmp" "$RUN_DIR/storage-recovery.tmp" >"$output"
  rm -f "$RUN_DIR/storage-checkpoints.tmp" "$RUN_DIR/storage-queue.tmp" "$RUN_DIR/storage-recovery.tmp"
}

assert_distinct_paths() {
  first_name=$1
  first_path=$(realpath "$2")
  second_name=$3
  second_path=$(realpath "$4")
  [ "$first_path" != "$second_path" ] || fail "$first_name and $second_name must use distinct paths"
  case "$first_path/" in "$second_path/"*) fail "$first_name must not be nested under $second_name" ;; esac
  case "$second_path/" in "$first_path/"*) fail "$second_name must not be nested under $first_name" ;; esac
}

snapshot_source() {
  output=$1
  "$GO_COMMAND" run ./cmd/ocsf-file-lifecycle-qualifier snapshot \
    --source-dir "$SOURCE_DIR" \
    --pattern "openshell-ocsf.*.log" \
    --logical-dir "$LOGICAL_DIR" \
    --resolved-dir "$RESOLVED_DIR" \
    --gateway-id "$GATEWAY_ID" \
    --workspace "$WORKSPACE" \
    --sandbox-id "$DEFAULT_SANDBOX_ID" \
    --source-instance "$SOURCE_INSTANCE" \
    --output "$output"
}

phase=${1:-}
case "$phase" in
  prepare|execute) ;;
  *) usage ;;
esac

for command_name in awk curl date find findmnt git grep jq mv printenv realpath rm sha256sum sort stat tr wc; do
  command_required "$command_name"
done

for name in \
  OCSF_LIFECYCLE_RUN_ID \
  OCSF_LIFECYCLE_EVIDENCE_DIR \
  OCSF_LIFECYCLE_SOURCE_DIR \
  OCSF_LIFECYCLE_GATEWAY_ID \
  OCSF_LIFECYCLE_WORKSPACE \
  OCSF_LIFECYCLE_SOURCE_INSTANCE \
  OCSF_LIFECYCLE_EXPORTER_IMAGE \
  OCSF_LIFECYCLE_GATEWAY_IMAGE \
  OCSF_LIFECYCLE_GATEWAY_VERSION \
  OCSF_LIFECYCLE_CONFIG_FILE \
  OCSF_LIFECYCLE_CHECKPOINT_DIR \
  OCSF_LIFECYCLE_QUEUE_DIR \
  OCSF_LIFECYCLE_RECOVERY_DIR \
  OCSF_LIFECYCLE_DESTINATION_OUTAGE_HOOK \
  OCSF_LIFECYCLE_DESTINATION_RESTORE_HOOK \
  OCSF_LIFECYCLE_ACTIVITY_HOOK \
  OCSF_LIFECYCLE_EXPORTER_RESTART_HOOK \
  OCSF_LIFECYCLE_EXPORTER_IDENTITY_HOOK; do
  required_value "$name"
done

case "$OCSF_LIFECYCLE_RUN_ID" in
  *[!A-Za-z0-9._-]*|'') fail "OCSF_LIFECYCLE_RUN_ID contains an unsafe character" ;;
esac

case "${OCSF_LIFECYCLE_TIMEOUT_SECONDS:-600}" in
  *[!0-9]*|'') fail "OCSF_LIFECYCLE_TIMEOUT_SECONDS must be a positive integer" ;;
esac
TIMEOUT_SECONDS=${OCSF_LIFECYCLE_TIMEOUT_SECONDS:-600}
[ "$TIMEOUT_SECONDS" -gt 0 ] || fail "OCSF_LIFECYCLE_TIMEOUT_SECONDS must be positive"

GO_COMMAND=${OCSF_LIFECYCLE_GO_COMMAND:-go}
command_required "$GO_COMMAND"
[ "$($GO_COMMAND env GOVERSION)" = go1.26.8 ] || fail "qualification requires Go 1.26.8"

EVIDENCE_ROOT=$(realpath "$OCSF_LIFECYCLE_EVIDENCE_DIR")
[ -d "$EVIDENCE_ROOT" ] && [ -w "$EVIDENCE_ROOT" ] || fail "OCSF_LIFECYCLE_EVIDENCE_DIR must be an existing writable directory"
[ "$(stat -c %a "$EVIDENCE_ROOT")" = 700 ] || fail "OCSF_LIFECYCLE_EVIDENCE_DIR must have mode 0700"
RUN_DIR="$EVIDENCE_ROOT/$OCSF_LIFECYCLE_RUN_ID"
SOURCE_DIR=$(realpath "$OCSF_LIFECYCLE_SOURCE_DIR")
CHECKPOINT_DIR=$(realpath "$OCSF_LIFECYCLE_CHECKPOINT_DIR")
QUEUE_DIR=$(realpath "$OCSF_LIFECYCLE_QUEUE_DIR")
RECOVERY_DIR=$(realpath "$OCSF_LIFECYCLE_RECOVERY_DIR")
CONFIG_FILE=$(realpath "$OCSF_LIFECYCLE_CONFIG_FILE")
LOGICAL_DIR=${OCSF_LIFECYCLE_LOGICAL_DIR:-/var/log}
RESOLVED_DIR=${OCSF_LIFECYCLE_RESOLVED_DIR:-/var/log}
GATEWAY_ID=$OCSF_LIFECYCLE_GATEWAY_ID
WORKSPACE=$OCSF_LIFECYCLE_WORKSPACE
DEFAULT_SANDBOX_ID=${OCSF_LIFECYCLE_DEFAULT_SANDBOX_ID:-unknown}
SOURCE_INSTANCE=$OCSF_LIFECYCLE_SOURCE_INSTANCE
METRICS_URL=${OCSF_LIFECYCLE_METRICS_URL:-http://127.0.0.1:8888/metrics}
HEALTH_URL=${OCSF_LIFECYCLE_HEALTH_URL:-http://127.0.0.1:13133/}
QUEUE_COMPONENT_MATCH=${OCSF_LIFECYCLE_QUEUE_COMPONENT_MATCH:-cloudevents}
METRICS_JSON="$RUN_DIR/metrics.json"
METRICS_TMP="$RUN_DIR/metrics.tmp"
DELIVERY_LEDGER="$RUN_DIR/deliveries.jsonl"
export OCSF_LIFECYCLE_RUN_DIR="$RUN_DIR" OCSF_LIFECYCLE_DELIVERY_LEDGER="$DELIVERY_LEDGER"

validate_digest_reference "$OCSF_LIFECYCLE_EXPORTER_IMAGE" OCSF_LIFECYCLE_EXPORTER_IMAGE
validate_digest_reference "$OCSF_LIFECYCLE_GATEWAY_IMAGE" OCSF_LIFECYCLE_GATEWAY_IMAGE
validate_hook OCSF_LIFECYCLE_DESTINATION_OUTAGE_HOOK "$OCSF_LIFECYCLE_DESTINATION_OUTAGE_HOOK"
validate_hook OCSF_LIFECYCLE_DESTINATION_RESTORE_HOOK "$OCSF_LIFECYCLE_DESTINATION_RESTORE_HOOK"
validate_hook OCSF_LIFECYCLE_ACTIVITY_HOOK "$OCSF_LIFECYCLE_ACTIVITY_HOOK"
validate_hook OCSF_LIFECYCLE_EXPORTER_RESTART_HOOK "$OCSF_LIFECYCLE_EXPORTER_RESTART_HOOK"
validate_hook OCSF_LIFECYCLE_EXPORTER_IDENTITY_HOOK "$OCSF_LIFECYCLE_EXPORTER_IDENTITY_HOOK"
[ -d "$SOURCE_DIR" ] && [ -r "$SOURCE_DIR" ] || fail "OCSF source directory is not readable"
source_mount_options=$(findmnt --noheadings --output OPTIONS --target "$SOURCE_DIR" | awk 'NR == 1 { print; exit }')
case ",$source_mount_options," in
  *,ro,*) ;;
  *) fail "OCSF source directory must be mounted read-only into the qualifier" ;;
esac
[ -f "$CONFIG_FILE" ] && [ -r "$CONFIG_FILE" ] || fail "exporter configuration is not readable"
assert_distinct_paths checkpoints "$CHECKPOINT_DIR" queue "$QUEUE_DIR"
assert_distinct_paths checkpoints "$CHECKPOINT_DIR" recovery "$RECOVERY_DIR"
assert_distinct_paths queue "$QUEUE_DIR" recovery "$RECOVERY_DIR"
assert_distinct_paths source "$SOURCE_DIR" checkpoints "$CHECKPOINT_DIR"
assert_distinct_paths source "$SOURCE_DIR" queue "$QUEUE_DIR"
assert_distinct_paths source "$SOURCE_DIR" recovery "$RECOVERY_DIR"

cd "$REPO_ROOT"

configured_source_instance=$("$GO_COMMAND" run ./cmd/ocsf-file-lifecycle-qualifier config-source-instance --config "$CONFIG_FILE")
[ "$SOURCE_INSTANCE" = "$configured_source_instance" ] ||
  fail "OCSF_LIFECYCLE_SOURCE_INSTANCE ($SOURCE_INSTANCE) does not match the openshell processor source_instance ($configured_source_instance)"

if [ "$phase" = prepare ]; then
  [ ! -e "$RUN_DIR" ] || fail "run already exists: $RUN_DIR"
  [ -z "$(git status --porcelain)" ] || fail "candidate worktree must be clean"
  commit=$(git rev-parse HEAD)
  printf '%s\n' "$commit" | grep -Eq '^[0-9a-f]{40}$' || fail "candidate commit is invalid"
  mkdir "$RUN_DIR"
  chmod 700 "$RUN_DIR"
  printf '{}\n' >"$METRICS_JSON"
  : >"$DELIVERY_LEDGER"
  chmod 600 "$DELIVERY_LEDGER"
  wait_for_health
  capture_metrics baseline
  number_is_zero "$(jq -r '.baseline.queue_size' "$METRICS_JSON")" || fail "qualification must begin with an empty CloudEvents queue"
  instance_before=$("$OCSF_LIFECYCLE_EXPORTER_IDENTITY_HOOK" | tr -d '\r\n')
  case "$instance_before" in *[!A-Za-z0-9._:-]*|'') fail "exporter identity hook returned an unsafe or empty identity" ;; esac
  snapshot_source "$RUN_DIR/source-before.json"
  started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  exporter_version=$(tr -d '\r\n' <VERSION)
  config_sha256=$(sha256_file "$CONFIG_FILE")
  jq -n \
    --arg run_id "$OCSF_LIFECYCLE_RUN_ID" \
    --arg started_at "$started_at" \
    --arg commit "$commit" \
    --arg exporter_version "$exporter_version" \
    --arg exporter_image "$OCSF_LIFECYCLE_EXPORTER_IMAGE" \
    --arg config_sha256 "$config_sha256" \
    --arg gateway_version "$OCSF_LIFECYCLE_GATEWAY_VERSION" \
    --arg gateway_image "$OCSF_LIFECYCLE_GATEWAY_IMAGE" \
    --arg instance_before "$instance_before" \
    --arg outage_hook "$(sha256_file "$OCSF_LIFECYCLE_DESTINATION_OUTAGE_HOOK")" \
    --arg restore_hook "$(sha256_file "$OCSF_LIFECYCLE_DESTINATION_RESTORE_HOOK")" \
    --arg activity_hook "$(sha256_file "$OCSF_LIFECYCLE_ACTIVITY_HOOK")" \
    --arg restart_hook "$(sha256_file "$OCSF_LIFECYCLE_EXPORTER_RESTART_HOOK")" \
    --arg identity_hook "$(sha256_file "$OCSF_LIFECYCLE_EXPORTER_IDENTITY_HOOK")" \
    '{
      schema_version: "1.0",
      run_id: $run_id,
      started_at: $started_at,
      candidate: {
        commit: $commit,
        worktree_clean: true,
        exporter_version: $exporter_version,
        exporter_image: $exporter_image,
        config_sha256: $config_sha256
      },
      gateway: {version: $gateway_version, image: $gateway_image},
      hook_sha256: {
        destination_outage: $outage_hook,
        destination_restore: $restore_hook,
        real_activity: $activity_hook,
        exporter_restart: $restart_hook,
        exporter_identity: $identity_hook
      },
      limitations: [
        "This gate qualifies the supported OCSF daily rotation path only; supported truncation and replacement mechanisms remain a separate real-gateway gate.",
        "WatchSandbox is non-resumable and is outside this durable-file reconciliation.",
        "The isolated destination ledger must append every attempted CloudEvents batch, including HTTP 503 attempts."
      ],
      exporter_instance_before: $instance_before
    }' >"$RUN_DIR/prepared.json"
  jq '{schema_version, run_id, started_at, candidate, gateway, hook_sha256, limitations}' "$RUN_DIR/prepared.json" >"$RUN_DIR/metadata.json"
  printf '%s\n' \
    "Prepared candidate-bound run: $RUN_DIR" \
    "Wait for OpenShell to perform its natural daily rotation." \
    "Generate normal gateway activity before and after rotation, then run the execute phase." \
    "Do not rename, truncate, copy, or rewrite any OCSF source file."
  exit 0
fi

[ -d "$RUN_DIR" ] || fail "prepared run does not exist: $RUN_DIR"
for required_file in metadata.json prepared.json source-before.json metrics.json deliveries.jsonl; do
  [ -f "$RUN_DIR/$required_file" ] || fail "prepared run is missing $required_file"
done
[ -z "$(git status --porcelain)" ] || fail "candidate worktree must remain clean"
[ "$(git rev-parse HEAD)" = "$(jq -r '.candidate.commit' "$RUN_DIR/metadata.json")" ] || fail "candidate commit changed after prepare"
[ "$(sha256_file "$CONFIG_FILE")" = "$(jq -r '.candidate.config_sha256' "$RUN_DIR/metadata.json")" ] || fail "exporter configuration changed after prepare"
for hook_entry in \
  "destination_outage:$OCSF_LIFECYCLE_DESTINATION_OUTAGE_HOOK" \
  "destination_restore:$OCSF_LIFECYCLE_DESTINATION_RESTORE_HOOK" \
  "real_activity:$OCSF_LIFECYCLE_ACTIVITY_HOOK" \
  "exporter_restart:$OCSF_LIFECYCLE_EXPORTER_RESTART_HOOK" \
  "exporter_identity:$OCSF_LIFECYCLE_EXPORTER_IDENTITY_HOOK"; do
  hook_name=${hook_entry%%:*}
  hook_path=${hook_entry#*:}
  expected_hash=$(jq -r --arg name "$hook_name" '.hook_sha256[$name]' "$RUN_DIR/metadata.json")
  [ "$(sha256_file "$hook_path")" = "$expected_hash" ] || fail "$hook_name hook changed after prepare"
done

outage_status=$("$OCSF_LIFECYCLE_DESTINATION_OUTAGE_HOOK" | tr -d '\r\n')
validate_status "$outage_status" outage
restore_required=true
restore_destination() {
  if [ "$restore_required" = true ]; then
    "$OCSF_LIFECYCLE_DESTINATION_RESTORE_HOOK" >/dev/null 2>&1 || true
  fi
}
trap restore_destination EXIT HUP INT TERM
"$OCSF_LIFECYCLE_ACTIVITY_HOOK"
wait_for_outage
capture_storage "$RUN_DIR/storage-before-restart.json"
"$OCSF_LIFECYCLE_EXPORTER_RESTART_HOOK"
wait_for_health
instance_after=$("$OCSF_LIFECYCLE_EXPORTER_IDENTITY_HOOK" | tr -d '\r\n')
case "$instance_after" in *[!A-Za-z0-9._:-]*|'') fail "exporter identity hook returned an unsafe or empty identity after restart" ;; esac
instance_before=$(jq -r '.exporter_instance_before' "$RUN_DIR/prepared.json")
[ "$instance_after" != "$instance_before" ] || fail "exporter instance identity did not change across restart"
wait_for_queue_after_restart
capture_storage "$RUN_DIR/storage-after-restart.json"
restore_status=$("$OCSF_LIFECYCLE_DESTINATION_RESTORE_HOOK" | tr -d '\r\n')
validate_status "$restore_status" restore
restore_required=false
wait_for_drain
snapshot_source "$RUN_DIR/source-after.json"
[ -s "$DELIVERY_LEDGER" ] || fail "isolated destination ledger is empty"
jq -n \
  --arg instance_before "$instance_before" \
  --arg instance_after "$instance_after" \
  --argjson outage_status "$outage_status" \
  --argjson restore_status "$restore_status" \
  '{
    exporter_instance_before: $instance_before,
    exporter_instance_after: $instance_after,
    destination_outage_status: $outage_status,
    destination_restore_status: $restore_status
  }' >"$RUN_DIR/runtime.json"
"$GO_COMMAND" run ./cmd/ocsf-file-lifecycle-qualifier reconcile \
  --metadata "$RUN_DIR/metadata.json" \
  --before "$RUN_DIR/source-before.json" \
  --after "$RUN_DIR/source-after.json" \
  --deliveries "$DELIVERY_LEDGER" \
  --metrics "$METRICS_JSON" \
  --storage-before-restart "$RUN_DIR/storage-before-restart.json" \
  --storage-after-restart "$RUN_DIR/storage-after-restart.json" \
  --runtime "$RUN_DIR/runtime.json" \
  --output "$RUN_DIR/report.json" \
  --require-complete
printf 'OCSF file lifecycle qualification passed: %s\n' "$RUN_DIR/report.json"
