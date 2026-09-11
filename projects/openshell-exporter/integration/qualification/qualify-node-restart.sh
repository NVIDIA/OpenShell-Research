#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -eu
umask 077

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
DOCKER_DEPLOY_DIR="$REPO_ROOT/deploy/docker"
cd "$DOCKER_DEPLOY_DIR"

if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

fail() {
  printf 'node-restart qualification: %s\n' "$*" >&2
  exit 1
}

usage() {
  printf 'usage: NODE_RESTART_RUN_ID=<id> NODE_RESTART_EVIDENCE_DIR=<secure-dir> %s prepare|resume\n' "$0" >&2
  exit 2
}

command_required() {
  command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
}

required_value() {
  name=$1
  value=$(printenv "$name" 2>/dev/null || true)
  [ -n "$value" ] || fail "$name is required"
}

compose() {
  docker compose --env-file .env "$@"
}

metric_count() {
  metrics_file=$1
  metric_name=$2
  component_match=$3
  awk -v metric="$metric_name" -v component="$component_match" '
    $0 !~ /^#/ &&
    ($1 == metric || index($1, metric "{") == 1) &&
    (component == "" || index($0, component) > 0) {
      count++
    }
    END { print count + 0 }
  ' "$metrics_file"
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
    }
    END { printf "%.17g\n", sum + 0 }
  ' "$metrics_file"
}

number_greater_than() {
  awk -v left="$1" -v right="$2" 'BEGIN { exit !(left > right) }'
}

number_is_zero() {
  awk -v value="$1" 'BEGIN { exit !(value == 0) }'
}

directory_file_count() {
  find "$1" -type f -print | wc -l | tr -d ' '
}

directory_bytes() {
  find "$1" -type f -printf '%s\n' |
    awk '{ total += $1 } END { printf "%.0f\n", total + 0 }'
}

directory_manifest_hash() {
  find "$1" -type f -printf '%P\t%s\t%T@\n' |
    sort |
    sha256sum |
    awk '{ print $1 }'
}

assert_evidence_path_is_separate() {
  evidence_path=$(realpath "$1")
  for candidate in "$CHECKPOINT_DIR" "$QUEUE_DIR" "$RECOVERY_DIR" "$OPENSHELL_LOG_DIR"; do
    candidate_path=$(realpath "$candidate")
    case "$evidence_path/" in
      "$candidate_path/"*) fail "evidence directory must not be inside $candidate_path" ;;
    esac
    case "$candidate_path/" in
      "$evidence_path/"*) fail "evidence directory must not contain $candidate_path" ;;
    esac
  done
}

fetch_metrics() {
  output=$1
  curl --fail --silent --show-error --max-time 10 "$METRICS_URL" >"$output"
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

running_container_id() {
  container_id=$(compose ps -q exporter 2>/dev/null || true)
  if [ -n "$container_id" ] &&
    [ "$(docker inspect --format '{{.State.Running}}' "$container_id" 2>/dev/null || true)" = true ]; then
    printf '%s\n' "$container_id"
  fi
}

wait_for_automatic_container_recovery() {
  deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    container_id=$(running_container_id)
    if [ -n "$container_id" ]; then
      printf '%s\n' "$container_id"
      return
    fi
    sleep 2
  done
  fail "exporter did not return automatically within ${TIMEOUT_SECONDS}s; do not run compose up for this gate"
}

extract_newest_event_key() {
  recovery_file=$1
  first_new_line=$2
  tail -n "+$first_new_line" "$recovery_file" |
    jq -s -c '
      def string_attr($name):
        if ((.attributes // null) | type) == "array" then
          ([.attributes[] |
            select(.key == $name) |
            (.value.stringValue // .value)] | first // "")
        elif ((.attributes // null) | type) == "object" then
          (.attributes[$name] // "")
        else
          ""
        end;
      [.[] |
        .resourceLogs[]? |
        .scopeLogs[]? |
        .logRecords[]? |
        {
          source: string_attr("cloudevents.source"),
          id: string_attr("cloudevents.id")
        } |
        select(.source != "" and .id != "")
      ] |
      last // empty
    '
}

phase=${1:-}
case "$phase" in
  prepare|resume) ;;
  *) usage ;;
esac

[ -f .env ] || fail "deploy/docker/.env is required for the Compose qualification"
for command_name in awk chmod curl date docker find grep hostname jq mkdir mv printenv realpath rm sha256sum sleep sort stat sync tail tr wc; do
  command_required "$command_name"
done

for name in \
  OPENSHELL_EXPORTER_IMAGE \
  OPENSHELL_LOG_DIR \
  CHECKPOINT_DIR \
  QUEUE_DIR \
  RECOVERY_DIR \
  NODE_RESTART_RUN_ID \
  NODE_RESTART_EVIDENCE_DIR; do
  required_value "$name"
done

case "$NODE_RESTART_RUN_ID" in
  *[!A-Za-z0-9._-]*|'') fail "NODE_RESTART_RUN_ID contains an unsafe character" ;;
esac

case "${NODE_RESTART_TIMEOUT_SECONDS:-600}" in
  *[!0-9]*|'') fail "NODE_RESTART_TIMEOUT_SECONDS must be a positive integer" ;;
esac
TIMEOUT_SECONDS=${NODE_RESTART_TIMEOUT_SECONDS:-600}
[ "$TIMEOUT_SECONDS" -gt 0 ] || fail "NODE_RESTART_TIMEOUT_SECONDS must be positive"

METRICS_URL=${NODE_RESTART_METRICS_URL:-http://127.0.0.1:8888/metrics}
HEALTH_URL=${NODE_RESTART_HEALTH_URL:-http://127.0.0.1:13133/}
RECOVERY_FILE=${NODE_RESTART_RECOVERY_FILE:-$RECOVERY_DIR/openshell-events.json}
QUEUE_COMPONENT_MATCH=${NODE_RESTART_QUEUE_COMPONENT_MATCH:-cloudevents}
EVIDENCE_ROOT=$(realpath "$NODE_RESTART_EVIDENCE_DIR")
[ -d "$EVIDENCE_ROOT" ] && [ -w "$EVIDENCE_ROOT" ] ||
  fail "NODE_RESTART_EVIDENCE_DIR must be an existing writable directory"
[ "$(stat -c '%a' "$EVIDENCE_ROOT")" = 700 ] ||
  fail "NODE_RESTART_EVIDENCE_DIR must have mode 0700"
assert_evidence_path_is_separate "$EVIDENCE_ROOT"

RUN_DIR="$EVIDENCE_ROOT/$NODE_RESTART_RUN_ID"
STATE_FILE="$RUN_DIR/state.json"
REPORT_FILE="$RUN_DIR/report.json"
METRICS_TMP="$RUN_DIR/metrics.tmp"
cleanup_tmp=
trap '[ -z "$cleanup_tmp" ] || rm -f "$cleanup_tmp"' EXIT HUP INT TERM

if [ "$phase" = prepare ]; then
  [ ! -e "$RUN_DIR" ] || fail "run already exists: $RUN_DIR"
  ./preflight.sh
  mkdir "$RUN_DIR"
  chmod 700 "$RUN_DIR"
  cleanup_tmp=$METRICS_TMP

  container_id=$(running_container_id)
  [ -n "$container_id" ] || fail "exporter must already be running; this script never starts it"
  wait_for_health
  restart_policy=$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "$container_id")
  [ "$restart_policy" = unless-stopped ] ||
    fail "exporter restart policy must be unless-stopped, got $restart_policy"
  image_id=$(docker inspect --format '{{.Image}}' "$container_id")
  started_at=$(docker inspect --format '{{.State.StartedAt}}' "$container_id")
  boot_id=$(tr -d '\n' </proc/sys/kernel/random/boot_id)
  [ -n "$boot_id" ] || fail "Linux boot ID is unavailable"
  host_name=$(hostname)

  fetch_metrics "$METRICS_TMP"
  [ "$(metric_count "$METRICS_TMP" otelcol_exporter_queue_size "$QUEUE_COMPONENT_MATCH")" -gt 0 ] ||
    fail "CloudEvents queue metric was not found; inspect metrics and set NODE_RESTART_QUEUE_COMPONENT_MATCH"
  queue_baseline=$(metric_sum "$METRICS_TMP" otelcol_exporter_queue_size "$QUEUE_COMPONENT_MATCH")
  number_is_zero "$queue_baseline" ||
    fail "qualification must start with an empty CloudEvents queue, got $queue_baseline"
  retry_baseline=$(metric_sum "$METRICS_TMP" openshell_exporter_delivery_retryable_failures "")
  delivered_baseline=$(metric_sum "$METRICS_TMP" openshell_exporter_delivery_events "")

  if [ -f "$RECOVERY_FILE" ]; then
    recovery_line_baseline=$(wc -l <"$RECOVERY_FILE" | tr -d ' ')
  else
    recovery_line_baseline=0
  fi

  printf '%s\n' \
    "Prepared baseline on boot $boot_id." \
    "Now make the external CloudEvents destination unavailable and generate new real OpenShell activity." \
    "Waiting for a retryable failure, a persistent queue entry, and a new recovery record."

  deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
  queue_before_reboot=0
  retry_before_reboot=$retry_baseline
  recovery_lines_before_reboot=$recovery_line_baseline
  while [ "$(date +%s)" -lt "$deadline" ]; do
    fetch_metrics "$METRICS_TMP"
    queue_before_reboot=$(metric_sum "$METRICS_TMP" otelcol_exporter_queue_size "$QUEUE_COMPONENT_MATCH")
    retry_before_reboot=$(metric_sum "$METRICS_TMP" openshell_exporter_delivery_retryable_failures "")
    if [ -f "$RECOVERY_FILE" ]; then
      recovery_lines_before_reboot=$(wc -l <"$RECOVERY_FILE" | tr -d ' ')
    else
      recovery_lines_before_reboot=0
    fi
    if number_greater_than "$queue_before_reboot" 0 &&
      number_greater_than "$retry_before_reboot" "$retry_baseline" &&
      [ "$recovery_lines_before_reboot" -gt "$recovery_line_baseline" ]; then
      break
    fi
    sleep 2
  done

  number_greater_than "$queue_before_reboot" 0 || fail "CloudEvents queue did not become non-empty"
  number_greater_than "$retry_before_reboot" "$retry_baseline" ||
    fail "no new retryable destination failure was observed"
  [ "$recovery_lines_before_reboot" -gt "$recovery_line_baseline" ] ||
    fail "no new redacted recovery record was observed"

  first_new_line=$((recovery_line_baseline + 1))
  event_key=$(extract_newest_event_key "$RECOVERY_FILE" "$first_new_line")
  [ -n "$event_key" ] || fail "could not extract a new CloudEvents source+id from recovery"
  expected_source=$(printf '%s' "$event_key" | jq -er '.source')
  expected_id=$(printf '%s' "$event_key" | jq -er '.id')
  case "$expected_source" in openshell://*) ;; *) fail "unexpected CloudEvents source in recovery" ;; esac
  printf '%s\n' "$expected_id" | grep -Eq '^sha256:[0-9a-f]{64}$' ||
    fail "unexpected CloudEvents id in recovery"

  checkpoint_files=$(directory_file_count "$CHECKPOINT_DIR")
  queue_files=$(directory_file_count "$QUEUE_DIR")
  recovery_files=$(directory_file_count "$RECOVERY_DIR")
  [ "$checkpoint_files" -gt 0 ] || fail "checkpoint persistence is empty"
  [ "$queue_files" -gt 0 ] || fail "queue persistence is empty"
  [ "$recovery_files" -gt 0 ] || fail "recovery persistence is empty"
  checkpoint_bytes=$(directory_bytes "$CHECKPOINT_DIR")
  queue_bytes=$(directory_bytes "$QUEUE_DIR")
  recovery_bytes=$(directory_bytes "$RECOVERY_DIR")
  checkpoint_manifest=$(directory_manifest_hash "$CHECKPOINT_DIR")
  queue_manifest=$(directory_manifest_hash "$QUEUE_DIR")
  recovery_manifest=$(directory_manifest_hash "$RECOVERY_DIR")
  prepared_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  cleanup_tmp="$STATE_FILE.tmp.$$"
  jq -n \
    --arg schema_version "1.0" \
    --arg status "prepared" \
    --arg run_id "$NODE_RESTART_RUN_ID" \
    --arg prepared_at "$prepared_at" \
    --arg host_name "$host_name" \
    --arg boot_id "$boot_id" \
    --arg image_reference "$OPENSHELL_EXPORTER_IMAGE" \
    --arg image_id "$image_id" \
    --arg container_id "$container_id" \
    --arg container_started_at "$started_at" \
    --arg restart_policy "$restart_policy" \
    --arg expected_source "$expected_source" \
    --arg expected_id "$expected_id" \
    --arg checkpoint_manifest_sha256 "$checkpoint_manifest" \
    --arg queue_manifest_sha256 "$queue_manifest" \
    --arg recovery_manifest_sha256 "$recovery_manifest" \
    --argjson queue_size_before_reboot "$queue_before_reboot" \
    --argjson retryable_failures_before_reboot "$retry_before_reboot" \
    --argjson delivered_events_before_reboot "$delivered_baseline" \
    --argjson checkpoint_files "$checkpoint_files" \
    --argjson checkpoint_bytes "$checkpoint_bytes" \
    --argjson queue_files "$queue_files" \
    --argjson queue_bytes "$queue_bytes" \
    --argjson recovery_files "$recovery_files" \
    --argjson recovery_bytes "$recovery_bytes" \
    '{
      schema_version: $schema_version,
      status: $status,
      run_id: $run_id,
      prepared_at: $prepared_at,
      host: {name: $host_name, boot_id: $boot_id},
      exporter: {
        image_reference: $image_reference,
        image_id: $image_id,
        container_id: $container_id,
        container_started_at: $container_started_at,
        restart_policy: $restart_policy
      },
      expected_event: {source: $expected_source, id: $expected_id},
      metrics: {
        queue_size_before_reboot: $queue_size_before_reboot,
        retryable_failures_before_reboot: $retryable_failures_before_reboot,
        delivered_events_before_reboot: $delivered_events_before_reboot
      },
      persistence: {
        checkpoints: {
          files: $checkpoint_files,
          bytes: $checkpoint_bytes,
          manifest_sha256: $checkpoint_manifest_sha256
        },
        queue: {
          files: $queue_files,
          bytes: $queue_bytes,
          manifest_sha256: $queue_manifest_sha256
        },
        recovery: {
          files: $recovery_files,
          bytes: $recovery_bytes,
          manifest_sha256: $recovery_manifest_sha256
        }
      }
    }' >"$cleanup_tmp"
  mv "$cleanup_tmp" "$STATE_FILE"
  cleanup_tmp=
  chmod 600 "$STATE_FILE"
  rm -f "$METRICS_TMP"
  sync
  printf '%s\n' \
    "Prepared node-restart evidence: $STATE_FILE" \
    "Keep the destination unavailable, reboot through the approved operator procedure, restore the destination, export receiver evidence JSON, then run resume." \
    "The script does not reboot, start, stop, or recreate services."
  exit 0
fi

[ -f "$STATE_FILE" ] || fail "prepared state does not exist: $STATE_FILE"
[ ! -e "$REPORT_FILE" ] || fail "qualification report already exists: $REPORT_FILE"
[ "$(jq -er '.status' "$STATE_FILE")" = prepared ] || fail "state is not prepared"
required_value NODE_RESTART_RECEIVER_EVIDENCE_FILE
[ -f "$NODE_RESTART_RECEIVER_EVIDENCE_FILE" ] &&
  [ -r "$NODE_RESTART_RECEIVER_EVIDENCE_FILE" ] ||
  fail "NODE_RESTART_RECEIVER_EVIDENCE_FILE must be readable"

boot_before=$(jq -er '.host.boot_id' "$STATE_FILE")
boot_after=$(tr -d '\n' </proc/sys/kernel/random/boot_id)
[ -n "$boot_after" ] && [ "$boot_after" != "$boot_before" ] ||
  fail "Linux boot ID did not change; a real node restart is not proven"
host_before=$(jq -er '.host.name' "$STATE_FILE")
[ "$(hostname)" = "$host_before" ] || fail "resume must run on the same host identity"

container_id=$(wait_for_automatic_container_recovery)
wait_for_health
restart_policy=$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "$container_id")
[ "$restart_policy" = unless-stopped ] || fail "restart policy changed after reboot"
image_before=$(jq -er '.exporter.image_id' "$STATE_FILE")
image_after=$(docker inspect --format '{{.Image}}' "$container_id")
[ "$image_after" = "$image_before" ] || fail "exporter image changed across node restart"
started_after=$(docker inspect --format '{{.State.StartedAt}}' "$container_id")

cleanup_tmp=$METRICS_TMP
deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
queue_after=-1
delivered_after=0
last_success_after=0
while [ "$(date +%s)" -lt "$deadline" ]; do
  fetch_metrics "$METRICS_TMP"
  queue_after=$(metric_sum "$METRICS_TMP" otelcol_exporter_queue_size "$QUEUE_COMPONENT_MATCH")
  delivered_after=$(metric_sum "$METRICS_TMP" openshell_exporter_delivery_events "")
  last_success_after=$(metric_sum "$METRICS_TMP" openshell_exporter_destination_last_success_unixtime "")
  if number_is_zero "$queue_after" &&
    number_greater_than "$delivered_after" 0 &&
    number_greater_than "$last_success_after" 0; then
    break
  fi
  sleep 2
done
number_is_zero "$queue_after" || fail "persistent queue did not drain after destination recovery"
number_greater_than "$delivered_after" 0 || fail "no post-reboot CloudEvents delivery was observed"
number_greater_than "$last_success_after" 0 ||
  fail "no post-reboot destination success timestamp was observed"

expected_source=$(jq -er '.expected_event.source' "$STATE_FILE")
expected_id=$(jq -er '.expected_event.id' "$STATE_FILE")
receiver_source=$(jq -er '.source' "$NODE_RESTART_RECEIVER_EVIDENCE_FILE")
receiver_id=$(jq -er '.id' "$NODE_RESTART_RECEIVER_EVIDENCE_FILE")
[ "$receiver_source" = "$expected_source" ] || fail "external receiver source does not match queued evidence"
[ "$receiver_id" = "$expected_id" ] || fail "external receiver id does not match queued evidence"
jq -e '
  (.deduplicated_rows | type) == "number" and
  .deduplicated_rows == 1 and
  (.delivery_attempts | type) == "number" and
  .delivery_attempts >= 1 and
  (.observed_at | type) == "string" and
  (.receiver | type) == "string"
' "$NODE_RESTART_RECEIVER_EVIDENCE_FILE" >/dev/null ||
  fail "receiver evidence must prove one deduplicated row, at least one attempt, observed_at, and receiver"
deduplicated_rows=$(jq -er '.deduplicated_rows' "$NODE_RESTART_RECEIVER_EVIDENCE_FILE")
delivery_attempts=$(jq -er '.delivery_attempts' "$NODE_RESTART_RECEIVER_EVIDENCE_FILE")
receiver_name=$(jq -er '.receiver' "$NODE_RESTART_RECEIVER_EVIDENCE_FILE")
receiver_observed_at=$(jq -er '.observed_at' "$NODE_RESTART_RECEIVER_EVIDENCE_FILE")
receiver_evidence_sha256=$(sha256sum "$NODE_RESTART_RECEIVER_EVIDENCE_FILE" | awk '{print $1}')

checkpoint_manifest_after=$(directory_manifest_hash "$CHECKPOINT_DIR")
queue_manifest_after=$(directory_manifest_hash "$QUEUE_DIR")
recovery_manifest_after=$(directory_manifest_hash "$RECOVERY_DIR")
completed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)

cleanup_tmp="$REPORT_FILE.tmp.$$"
jq -n \
  --arg schema_version "1.0" \
  --arg result "passed" \
  --arg run_id "$NODE_RESTART_RUN_ID" \
  --arg prepared_at "$(jq -er '.prepared_at' "$STATE_FILE")" \
  --arg completed_at "$completed_at" \
  --arg host_name "$host_before" \
  --arg boot_id_before "$boot_before" \
  --arg boot_id_after "$boot_after" \
  --arg image_reference "$OPENSHELL_EXPORTER_IMAGE" \
  --arg image_id "$image_after" \
  --arg container_id_after "$container_id" \
  --arg container_started_after "$started_after" \
  --arg expected_source "$expected_source" \
  --arg expected_id "$expected_id" \
  --arg receiver "$receiver_name" \
  --arg receiver_observed_at "$receiver_observed_at" \
  --arg receiver_evidence_sha256 "$receiver_evidence_sha256" \
  --arg checkpoint_manifest_after "$checkpoint_manifest_after" \
  --arg queue_manifest_after "$queue_manifest_after" \
  --arg recovery_manifest_after "$recovery_manifest_after" \
  --argjson queue_size_before_reboot "$(jq -er '.metrics.queue_size_before_reboot' "$STATE_FILE")" \
  --argjson queue_size_after_reboot "$queue_after" \
  --argjson delivered_events_after_reboot "$delivered_after" \
  --argjson destination_last_success_after_reboot "$last_success_after" \
  --argjson deduplicated_rows "$deduplicated_rows" \
  --argjson delivery_attempts "$delivery_attempts" \
  '{
    schema_version: $schema_version,
    result: $result,
    qualification_kind: "real Linux node restart with persistent CloudEvents queue",
    run_id: $run_id,
    prepared_at: $prepared_at,
    completed_at: $completed_at,
    host: {
      name: $host_name,
      boot_id_before: $boot_id_before,
      boot_id_after: $boot_id_after
    },
    exporter: {
      image_reference: $image_reference,
      image_id: $image_id,
      container_id_after: $container_id_after,
      container_started_after: $container_started_after,
      automatic_restart: true
    },
    queued_event: {
      source: $expected_source,
      id: $expected_id
    },
    metrics: {
      queue_size_before_reboot: $queue_size_before_reboot,
      queue_size_after_reboot: $queue_size_after_reboot,
      delivered_events_after_reboot: $delivered_events_after_reboot,
      destination_last_success_after_reboot: $destination_last_success_after_reboot
    },
    receiver_evidence: {
      receiver: $receiver,
      observed_at: $receiver_observed_at,
      deduplicated_rows: $deduplicated_rows,
      delivery_attempts: $delivery_attempts,
      sha256: $receiver_evidence_sha256
    },
    persistence_after: {
      checkpoint_manifest_sha256: $checkpoint_manifest_after,
      queue_manifest_sha256: $queue_manifest_after,
      recovery_manifest_sha256: $recovery_manifest_after
    },
    limitations: [
      "This proves the named host reboot and CloudEvents queue drain only.",
      "It does not replace real gateway variant, OTLP destination, capacity, signing, canary, or pilot gates."
    ]
  }' >"$cleanup_tmp"
mv "$cleanup_tmp" "$REPORT_FILE"
cleanup_tmp=
chmod 600 "$REPORT_FILE"
cleanup_tmp="$STATE_FILE.tmp.$$"
jq --arg completed_at "$completed_at" '.status = "complete" | .completed_at = $completed_at' \
  "$STATE_FILE" >"$cleanup_tmp"
mv "$cleanup_tmp" "$STATE_FILE"
cleanup_tmp=
chmod 600 "$STATE_FILE"
rm -f "$METRICS_TMP"
sync
printf 'Node-restart qualification passed: %s\n' "$REPORT_FILE"
