#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -eu
umask 077

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)

fail() { printf 'policy reconciliation recovery qualification: %s\n' "$*" >&2; exit 1; }
usage() {
  printf 'usage: POLICY_RECOVERY_RUN_ID=<id> POLICY_RECOVERY_EVIDENCE_DIR=<secure-dir> %s prepare|execute\n' "$0" >&2
  exit 2
}
required_command() { command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"; }
required_value() {
  name=$1
  value=$(printenv "$name" 2>/dev/null || true)
  [ -n "$value" ] || fail "$name is required"
}
sha256_file() { sha256sum "$1" | awk '{print "sha256:" $1}'; }
validate_digest() {
  printf '%s\n' "$1" | grep -Eq '^.+@sha256:[0-9a-f]{64}$' ||
    fail "$2 must be an immutable sha256 digest reference"
}
validate_hook() {
  case "$2" in /*) ;; *) fail "$1 must be an absolute path" ;; esac
  [ -f "$2" ] && [ -x "$2" ] || fail "$1 must be an executable regular file"
}
directory_file_count() { find "$1" -type f -print | wc -l | tr -d ' '; }
directory_bytes() { find "$1" -type f -printf '%s\n' | awk '{n += $1} END {printf "%.0f\n", n + 0}'; }
directory_manifest_hash() {
  find "$1" -type f -printf '%P\t%s\t%T@\n' | sort | sha256sum | awk '{print "sha256:" $1}'
}
capture_storage() {
  jq -n --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg path "$CHECKPOINT_DIR" \
    --arg digest "$(directory_manifest_hash "$CHECKPOINT_DIR")" \
    --argjson device "$(stat -c %d "$CHECKPOINT_DIR")" \
    --argjson inode "$(stat -c %i "$CHECKPOINT_DIR")" \
    --argjson files "$(directory_file_count "$CHECKPOINT_DIR")" \
    --argjson bytes "$(directory_bytes "$CHECKPOINT_DIR")" \
    '{captured_at:$at,path:$path,device:$device,inode:$inode,file_count:$files,bytes:$bytes,manifest_sha256:$digest}' >"$1"
}
metric_sum() {
  awk -v dotted="$2" -v underscored="$3" '
    $0 !~ /^#/ && (index($1,dotted)==1 || index($1,underscored)==1) {sum += $NF; found=1}
    END {if (!found) print "0"; else printf "%.17g\n", sum + 0}
  ' "$1"
}
capture_metrics() {
  label=$1
  output=$2
  scrape="$RUN_DIR/metrics-$label.prom"
  curl --fail --silent --show-error --max-time 10 "$METRICS_URL" >"$scrape"
  success=$(metric_sum "$scrape" openshell.exporter.policy.reconciliations openshell_exporter_policy_reconciliations)
  failures=$(metric_sum "$scrape" openshell.exporter.policy.reconciliation_failures openshell_exporter_policy_reconciliation_failures)
  jq -n --arg phase "$label" --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg digest "$(sha256_file "$scrape")" --argjson success "$success" --argjson failures "$failures" \
    '{label:$phase,captured_at:$at,successful_reconciliations:$success,failed_reconciliations:$failures,scrape_sha256:$digest}' >"$output"
}
greater_than() { awk -v left="$1" -v right="$2" 'BEGIN {exit !(left > right)}'; }
wait_for_metric_growth() {
  field=$1
  baseline=$2
  label=$3
  output=$4
  deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    capture_metrics "$label" "$output"
    current=$(jq -r ".$field" "$output")
    if greater_than "$current" "$baseline"; then return; fi
    sleep 2
  done
  fail "$field did not increase during $label"
}
wait_for_health() {
  deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if curl --fail --silent --show-error --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then return; fi
    sleep 2
  done
  fail "exporter health did not recover within $TIMEOUT_SECONDS seconds"
}
normalize_events() {
  jq -s --arg sandbox "$SANDBOX_ID" --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '
    def flattened: .[] | if type=="array" then .[] else . end;
    [flattened
      | select(type=="object" and .specversion=="1.0")
      | select(.subject==("sandboxes/"+$sandbox) or ((.source//"")|contains("/sandboxes/"+$sandbox+"/")))
      | select((.type//"")|startswith("com.nvidia.openshell.policy."))
      | {type,source,id,source_time:(.time//null),observed_time:(.data.observed_time//""),
         acquisition_kind:(.data.acquisition.kind//""),consistency:(.data.acquisition.consistency//""),
         policy_version:((.data.openshell.policy_version//"")|tostring),
         warning_code:(.data.original.code//null)}
    ] | unique_by(.source,.id) | sort_by(.source,.id) | {captured_at:$at,events:.}
  ' "$CLOUDEVENTS_FILE" >"$1"
}
validate_mutation_receipt() {
  jq -e '
    .approved_external_actuator==true and
    (.mutation_id|type=="string" and length>0) and
    (.expected_policy_version|type=="string" and length>0) and
    ((keys-["approved_external_actuator","expected_policy_version","mutation_id"])|length==0) and
    ([..|objects|keys[]|ascii_downcase|gsub("_";"")|select(.=="reviewtoken")]|length==0)
  ' "$1" >/dev/null || fail "mutation hook did not produce a safe approved actuator receipt"
}

phase=${1:-}
case "$phase" in prepare|execute) ;; *) usage ;; esac
for command_name in awk chmod curl date find git grep jq mkdir printenv realpath sha256sum sort stat tr wc; do
  required_command "$command_name"
done
for name in POLICY_RECOVERY_RUN_ID POLICY_RECOVERY_EVIDENCE_DIR POLICY_RECOVERY_SANDBOX_ID \
  POLICY_RECOVERY_EXPORTER_IMAGE POLICY_RECOVERY_GATEWAY_IMAGE POLICY_RECOVERY_GATEWAY_VERSION \
  POLICY_RECOVERY_CLOUDEVENTS_FILE POLICY_RECOVERY_CHECKPOINT_DIR \
  POLICY_RECOVERY_DISCONNECT_HOOK POLICY_RECOVERY_MUTATE_HOOK POLICY_RECOVERY_RESTART_HOOK \
  POLICY_RECOVERY_RECONCILE_HOOK POLICY_RECOVERY_API_FAILURE_ON_HOOK \
  POLICY_RECOVERY_API_FAILURE_OFF_HOOK POLICY_RECOVERY_EXPORTER_IDENTITY_HOOK; do
  required_value "$name"
done
case "$POLICY_RECOVERY_RUN_ID" in *[!A-Za-z0-9._-]*|'') fail "unsafe run ID" ;; esac
TIMEOUT_SECONDS=$(printenv POLICY_RECOVERY_TIMEOUT_SECONDS 2>/dev/null || printf '600\n')
case "$TIMEOUT_SECONDS" in *[!0-9]*|'') fail "timeout must be a positive integer" ;; esac
[ "$TIMEOUT_SECONDS" -gt 0 ] || fail "timeout must be positive"

EVIDENCE_ROOT=$(realpath "$POLICY_RECOVERY_EVIDENCE_DIR")
[ -d "$EVIDENCE_ROOT" ] && [ -w "$EVIDENCE_ROOT" ] || fail "evidence directory must be writable"
[ "$(stat -c %a "$EVIDENCE_ROOT")" = 700 ] || fail "evidence directory must have mode 0700"
RUN_DIR="$EVIDENCE_ROOT/$POLICY_RECOVERY_RUN_ID"
CLOUDEVENTS_FILE=$(realpath "$POLICY_RECOVERY_CLOUDEVENTS_FILE")
CHECKPOINT_DIR=$(realpath "$POLICY_RECOVERY_CHECKPOINT_DIR")
SANDBOX_ID=$POLICY_RECOVERY_SANDBOX_ID
METRICS_URL=$(printenv POLICY_RECOVERY_METRICS_URL 2>/dev/null || printf 'http://127.0.0.1:8888/metrics\n')
HEALTH_URL=$(printenv POLICY_RECOVERY_HEALTH_URL 2>/dev/null || printf 'http://127.0.0.1:13133/\n')
[ -f "$CLOUDEVENTS_FILE" ] && [ -r "$CLOUDEVENTS_FILE" ] || fail "CloudEvents ledger is unreadable"
[ -d "$CHECKPOINT_DIR" ] && [ -r "$CHECKPOINT_DIR" ] || fail "checkpoint directory is unreadable"
validate_digest "$POLICY_RECOVERY_EXPORTER_IMAGE" POLICY_RECOVERY_EXPORTER_IMAGE
validate_digest "$POLICY_RECOVERY_GATEWAY_IMAGE" POLICY_RECOVERY_GATEWAY_IMAGE
for hook in "disconnect:$POLICY_RECOVERY_DISCONNECT_HOOK" "mutate:$POLICY_RECOVERY_MUTATE_HOOK" \
  "restart:$POLICY_RECOVERY_RESTART_HOOK" "reconcile:$POLICY_RECOVERY_RECONCILE_HOOK" \
  "failure_on:$POLICY_RECOVERY_API_FAILURE_ON_HOOK" "failure_off:$POLICY_RECOVERY_API_FAILURE_OFF_HOOK" \
  "identity:$POLICY_RECOVERY_EXPORTER_IDENTITY_HOOK"; do
  validate_hook "${hook%%:*}" "${hook#*:}"
done
cd "$REPO_ROOT"

if [ "$phase" = prepare ]; then
  [ ! -e "$RUN_DIR" ] || fail "run already exists"
  [ -z "$(git status --porcelain --untracked-files=normal)" ] || fail "candidate worktree must be clean"
  commit=$(git rev-parse HEAD)
  printf '%s\n' "$commit" | grep -Eq '^[0-9a-f]{40}$' || fail "invalid candidate commit"
  mkdir "$RUN_DIR"
  chmod 700 "$RUN_DIR"
  normalize_events "$RUN_DIR/events-baseline.json"
  jq -e '
    any(.events[];.type=="com.nvidia.openshell.policy.draft.snapshot.v1") and
    all(.events[];
      (.source|type=="string" and length>0) and (.id|test("^sha256:[0-9a-f]{64}$")) and
      (.observed_time|length>0) and (.acquisition_kind|length>0) and (.consistency|length>0))
  ' "$RUN_DIR/events-baseline.json" >/dev/null || fail "baseline policy snapshot evidence is incomplete"
  capture_storage "$RUN_DIR/storage-before.json"
  capture_metrics baseline "$RUN_DIR/metrics-baseline.json"
  identity=$("$POLICY_RECOVERY_EXPORTER_IDENTITY_HOOK" | tr -d '\r\n')
  case "$identity" in *[!A-Za-z0-9._:-]*|'') fail "identity hook returned an unsafe value" ;; esac
  jq -n --arg run_id "$POLICY_RECOVERY_RUN_ID" --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg commit "$commit" --arg version "$(tr -d '\r\n' <VERSION)" \
    --arg exporter_image "$POLICY_RECOVERY_EXPORTER_IMAGE" --arg gateway_image "$POLICY_RECOVERY_GATEWAY_IMAGE" \
    --arg gateway_version "$POLICY_RECOVERY_GATEWAY_VERSION" --arg sandbox "$SANDBOX_ID" --arg identity "$identity" \
    --arg disconnect "$(sha256_file "$POLICY_RECOVERY_DISCONNECT_HOOK")" \
    --arg mutate "$(sha256_file "$POLICY_RECOVERY_MUTATE_HOOK")" \
    --arg restart "$(sha256_file "$POLICY_RECOVERY_RESTART_HOOK")" \
    --arg reconcile "$(sha256_file "$POLICY_RECOVERY_RECONCILE_HOOK")" \
    --arg failure_on "$(sha256_file "$POLICY_RECOVERY_API_FAILURE_ON_HOOK")" \
    --arg failure_off "$(sha256_file "$POLICY_RECOVERY_API_FAILURE_OFF_HOOK")" \
    --arg identity_hash "$(sha256_file "$POLICY_RECOVERY_EXPORTER_IDENTITY_HOOK")" '
    {schema_version:"1.0",run_id:$run_id,prepared_at:$at,
     candidate:{commit:$commit,worktree_clean:true,exporter_version:$version,exporter_image:$exporter_image},
     gateway:{version:$gateway_version,image:$gateway_image},sandbox_id:$sandbox,
     exporter_identity_before:$identity,
     hook_sha256:{disconnect:$disconnect,mutate:$mutate,restart:$restart,reconcile:$reconcile,
                  failure_on:$failure_on,failure_off:$failure_off,identity:$identity_hash}}
  ' >"$RUN_DIR/metadata.json"
  printf 'policy reconciliation recovery prepared: %s\n' "$RUN_DIR"
  exit 0
fi

for name in metadata.json events-baseline.json storage-before.json metrics-baseline.json; do
  [ -f "$RUN_DIR/$name" ] || fail "prepared run is missing $name"
done
[ -z "$(git status --porcelain --untracked-files=normal)" ] || fail "candidate worktree must remain clean"
[ "$(git rev-parse HEAD)" = "$(jq -r '.candidate.commit' "$RUN_DIR/metadata.json")" ] ||
  fail "candidate commit changed after prepare"
for hook in "disconnect:$POLICY_RECOVERY_DISCONNECT_HOOK" "mutate:$POLICY_RECOVERY_MUTATE_HOOK" \
  "restart:$POLICY_RECOVERY_RESTART_HOOK" "reconcile:$POLICY_RECOVERY_RECONCILE_HOOK" \
  "failure_on:$POLICY_RECOVERY_API_FAILURE_ON_HOOK" "failure_off:$POLICY_RECOVERY_API_FAILURE_OFF_HOOK" \
  "identity:$POLICY_RECOVERY_EXPORTER_IDENTITY_HOOK"; do
  name=${hook%%:*}; path=${hook#*:}
  [ "$(sha256_file "$path")" = "$(jq -r --arg name "$name" '.hook_sha256[$name]' "$RUN_DIR/metadata.json")" ] ||
    fail "$name hook changed after prepare"
done

failure_enabled=false
restore_api() {
  if [ "$failure_enabled" = true ]; then "$POLICY_RECOVERY_API_FAILURE_OFF_HOOK" >/dev/null 2>&1 || true; fi
}
trap restore_api EXIT HUP INT TERM
"$POLICY_RECOVERY_DISCONNECT_HOOK"
"$POLICY_RECOVERY_MUTATE_HOOK" >"$RUN_DIR/mutation-receipt.json"
chmod 600 "$RUN_DIR/mutation-receipt.json"
validate_mutation_receipt "$RUN_DIR/mutation-receipt.json"
"$POLICY_RECOVERY_RESTART_HOOK"
wait_for_health
identity_after=$("$POLICY_RECOVERY_EXPORTER_IDENTITY_HOOK" | tr -d '\r\n')
[ "$identity_after" != "$(jq -r '.exporter_identity_before' "$RUN_DIR/metadata.json")" ] ||
  fail "exporter identity did not change across restart"

baseline_success=$(jq -r '.successful_reconciliations' "$RUN_DIR/metrics-baseline.json")
"$POLICY_RECOVERY_RECONCILE_HOOK" changed
wait_for_metric_growth successful_reconciliations "$baseline_success" changed "$RUN_DIR/metrics-changed.json"
normalize_events "$RUN_DIR/events-changed.json"
changed_success=$(jq -r '.successful_reconciliations' "$RUN_DIR/metrics-changed.json")
"$POLICY_RECOVERY_RECONCILE_HOOK" unchanged
wait_for_metric_growth successful_reconciliations "$changed_success" unchanged "$RUN_DIR/metrics-unchanged.json"
normalize_events "$RUN_DIR/events-unchanged.json"
"$POLICY_RECOVERY_API_FAILURE_ON_HOOK"
failure_enabled=true
failure_baseline=$(jq -r '.failed_reconciliations' "$RUN_DIR/metrics-unchanged.json")
"$POLICY_RECOVERY_RECONCILE_HOOK" failure
wait_for_metric_growth failed_reconciliations "$failure_baseline" failure "$RUN_DIR/metrics-failure.json"
normalize_events "$RUN_DIR/events-failure.json"
"$POLICY_RECOVERY_API_FAILURE_OFF_HOOK"
failure_enabled=false
recovery_baseline=$(jq -r '.successful_reconciliations' "$RUN_DIR/metrics-failure.json")
"$POLICY_RECOVERY_RECONCILE_HOOK" recovery
wait_for_metric_growth successful_reconciliations "$recovery_baseline" recovery "$RUN_DIR/metrics-recovery.json"
normalize_events "$RUN_DIR/events-recovery.json"
capture_storage "$RUN_DIR/storage-after.json"
review_token_leaks=$(jq -s '[..|objects|keys[]|ascii_downcase|gsub("_";"")|select(.=="reviewtoken")]|length' "$CLOUDEVENTS_FILE")
raw_policy_event_count=$(jq -s --arg sandbox "$SANDBOX_ID" '
  def flattened:.[]|if type=="array" then .[] else . end;
  [flattened
    | select(type=="object" and .specversion=="1.0")
    | select(.subject==("sandboxes/"+$sandbox) or ((.source//"")|contains("/sandboxes/"+$sandbox+"/")))
    | select((.type//"")|startswith("com.nvidia.openshell.policy."))
  ]|length
' "$CLOUDEVENTS_FILE")

jq -n --slurpfile metadata "$RUN_DIR/metadata.json" --slurpfile mutation "$RUN_DIR/mutation-receipt.json" \
  --slurpfile baseline "$RUN_DIR/events-baseline.json" --slurpfile changed "$RUN_DIR/events-changed.json" \
  --slurpfile unchanged "$RUN_DIR/events-unchanged.json" --slurpfile failure "$RUN_DIR/events-failure.json" \
  --slurpfile recovery "$RUN_DIR/events-recovery.json" --slurpfile mb "$RUN_DIR/metrics-baseline.json" \
  --slurpfile mc "$RUN_DIR/metrics-changed.json" --slurpfile mu "$RUN_DIR/metrics-unchanged.json" \
  --slurpfile mf "$RUN_DIR/metrics-failure.json" --slurpfile mr "$RUN_DIR/metrics-recovery.json" \
  --slurpfile sb "$RUN_DIR/storage-before.json" --slurpfile sa "$RUN_DIR/storage-after.json" \
  --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg identity_after "$identity_after" \
  --argjson leaks "$review_token_leaks" --argjson raw_count "$raw_policy_event_count" '
  def key:.source+"|"+.id; def keys($x):[$x.events[]|key]|unique|sort;
  def expected:["com.nvidia.openshell.policy.draft.snapshot.v1","com.nvidia.openshell.policy.draft.chunk.v1",
    "com.nvidia.openshell.policy.draft.history.v1","com.nvidia.openshell.policy.status.v1",
    "com.nvidia.openshell.policy.revision.v1"];
  ($baseline[0]) as $b|($changed[0]) as $c|($unchanged[0]) as $u|($failure[0]) as $f|($recovery[0]) as $r|
  (keys($b)) as $base_keys|
  ([$c.events[]|select((key as $candidate|$base_keys|index($candidate))==null)]) as $new_events|
  ([$new_events[].type]|unique|sort) as $new_types|
  ([expected[]|select(. as $t|($new_types|index($t))==null)]) as $missing|
  ([$new_events[]|select(.type=="com.nvidia.openshell.policy.draft.snapshot.v1")]) as $new_snapshots|
  ([ $f.events[]|select(.type=="com.nvidia.openshell.policy.reconciliation.warning.v1")|key ]-
   [ $u.events[]|select(.type=="com.nvidia.openshell.policy.reconciliation.warning.v1")|key ]) as $warnings|
  ($mutation[0].expected_policy_version|tostring) as $expected_version|
  (any($new_events[];.policy_version==$expected_version)) as $version_bound|
  (all($r.events[];
    (.source|length>0) and (.id|test("^sha256:[0-9a-f]{64}$")) and
    (.observed_time|length>0) and (.acquisition_kind|length>0) and (.consistency|length>0))) as $valid_events|
  {schema_version:"1.0",generated_at:$at,
   complete:(($missing|length)==0 and ($new_snapshots|length)>0 and keys($u)==keys($c) and
     ($warnings|length)>0 and $mf[0].failed_reconciliations>$mu[0].failed_reconciliations and
     $mr[0].successful_reconciliations>$mf[0].successful_reconciliations and
     $sb[0].device==$sa[0].device and $sb[0].inode==$sa[0].inode and $sa[0].file_count>0 and
     $leaks==0 and $version_bound and $valid_events),
   candidate:$metadata[0].candidate,gateway:$metadata[0].gateway,sandbox_id:$metadata[0].sandbox_id,
   actuator:$mutation[0],
   restart:{exporter_identity_before:$metadata[0].exporter_identity_before,
            exporter_identity_after:$identity_after,
            checkpoint_storage_reused:($sb[0].device==$sa[0].device and $sb[0].inode==$sa[0].inode)},
   reconciliation:{expected_types:expected,observed_new_types:$new_types,missing_types:$missing,
     changed_snapshot_identity_count:($new_snapshots|length),unchanged_snapshot_suppressed:(keys($u)==keys($c)),
     warning_identity_count:($warnings|length),stable_source_id_pairs_after_repeat:(keys($u)==keys($c)),
     expected_policy_version_observed:$version_bound,structurally_valid_event_identities:$valid_events,
     observed_consistency_states:([$r.events[].consistency]|unique|sort),
     baseline_unique_events:(keys($b)|length),changed_unique_events:(keys($c)|length),
     failure_unique_events:(keys($f)|length),recovered_unique_events:(keys($r)|length),
     raw_deliveries:$raw_count,delivery_duplicates:($raw_count-(keys($r)|length)),gap_count:($missing|length)},
   evidence_sets:{baseline:$b.events,changed_after_disconnect:$new_events,
     unchanged_repeat:$u.events,failure:$f.events,recovery:$r.events},
   metrics:{baseline:$mb[0],changed:$mc[0],unchanged:$mu[0],failure:$mf[0],recovery:$mr[0]},
   storage:{before:$sb[0],after:$sa[0]},privacy:{review_token_key_leaks:$leaks},
   limitations:["The approved external actuator is outside the exporter and uses a separate write identity.",
     "WatchSandbox is non-resumable; this gate proves read-only snapshot recovery, not stream replay.",
     "Gateway consistency variants that cannot be produced remain unobserved rather than synthesized."]}
  ' >"$RUN_DIR/report.json"
chmod 600 "$RUN_DIR/report.json"
jq -e '.complete==true' "$RUN_DIR/report.json" >/dev/null ||
  fail "candidate-bound recovery report is incomplete: $RUN_DIR/report.json"
printf 'policy reconciliation recovery qualification passed: %s\n' "$RUN_DIR/report.json"
