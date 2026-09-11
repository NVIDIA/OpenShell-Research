#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/quality/budgets.env"

fail() {
  echo "quality budget: $*" >&2
  exit 1
}

file_size() {
  test -f "$1" || fail "missing artifact: $1"
  wc -c < "$1" | tr -d '[:space:]'
}

check_size() {
  name=$1
  file=$2
  maximum=$3
  actual=$(file_size "$file")
  test "$actual" -le "$maximum" ||
    fail "$name is $actual bytes; maximum is $maximum bytes"
  echo "quality budget: $name $actual/$maximum bytes"
}

check_coverage() {
  profile=$1
  minimum=$2
  label=$3
  command -v go >/dev/null 2>&1 || fail "go is required for coverage checks"
  test -s "$profile" || fail "missing coverage profile: $profile"
  actual=$(go tool cover -func="$profile" | awk '$1 == "total:" {gsub(/%/, "", $3); print $3}')
  test -n "$actual" || fail "could not read total coverage from $profile"
  awk -v actual="$actual" -v minimum="$minimum" \
    'BEGIN { exit !(actual + 0 >= minimum + 0) }' ||
    fail "$label coverage is $actual%; minimum is $minimum%"
  echo "quality budget: $label coverage $actual% (minimum $minimum%)"
}

positive_integer() {
  case "$1" in
    ''|*[!0-9]*|0) fail "$2 must be a positive integer; got '$1'" ;;
  esac
}

benchmark_metric() {
  benchmark=$1
  unit=$2
  file=$3
  awk -v benchmark="$benchmark" -v unit="$unit" '
    $1 ~ ("^" benchmark "(-[0-9]+)?$") {
      rows++
      for (field = 2; field <= NF; field++) {
        if ($field == unit) {
          metrics++
          value = $(field - 1)
        }
      }
    }
    END {
      if (rows != 1 || metrics != 1 || value !~ /^[0-9]+$/) {
        exit 1
      }
      print value
    }
  ' "$file"
}

check_benchmark() {
  benchmark=$1
  maximum_bytes=$2
  maximum_allocations=$3
  file=$4
  positive_integer "$maximum_bytes" "$benchmark byte budget"
  positive_integer "$maximum_allocations" "$benchmark allocation budget"
  actual_bytes=$(benchmark_metric "$benchmark" B/op "$file") ||
    fail "could not read exactly one $benchmark B/op result from $file"
  actual_allocations=$(benchmark_metric "$benchmark" allocs/op "$file") ||
    fail "could not read exactly one $benchmark allocs/op result from $file"
  test "$actual_bytes" -le "$maximum_bytes" ||
    fail "$benchmark uses $actual_bytes B/op; maximum is $maximum_bytes B/op"
  test "$actual_allocations" -le "$maximum_allocations" ||
    fail "$benchmark uses $actual_allocations allocs/op; maximum is $maximum_allocations allocs/op"
  echo "quality budget: $benchmark $actual_bytes/$maximum_bytes B/op; $actual_allocations/$maximum_allocations allocs/op"
}

check_benchmarks() {
  file=$1
  test -s "$file" || fail "missing benchmark output: $file"
  check_benchmark BenchmarkOCSFNormalization \
    "$OCSF_NORMALIZATION_MAX_BYTES_PER_OP" "$OCSF_NORMALIZATION_MAX_ALLOCS_PER_OP" "$file"
  check_benchmark BenchmarkRelayAllowListProcessing \
    "$RELAY_ALLOWLIST_MAX_BYTES_PER_OP" "$RELAY_ALLOWLIST_MAX_ALLOCS_PER_OP" "$file"
  check_benchmark BenchmarkCloudEventsBatchDelivery \
    "$CLOUDEVENTS_BATCH_MAX_BYTES_PER_OP" "$CLOUDEVENTS_BATCH_MAX_ALLOCS_PER_OP" "$file"
  check_benchmark BenchmarkPolicyCheckpointEncoding \
    "$POLICY_CHECKPOINT_MAX_BYTES_PER_OP" "$POLICY_CHECKPOINT_MAX_ALLOCS_PER_OP" "$file"
  check_benchmark BenchmarkPolicyRevisionAccumulator \
    "$POLICY_REVISION_PAGINATION_MAX_BYTES_PER_OP" "$POLICY_REVISION_PAGINATION_MAX_ALLOCS_PER_OP" "$file"
}

check_dependencies() {
  file=$1
  test -s "$file" || fail "missing dependency graph: $file"
  positive_integer "$GO_MODULE_GRAPH_MAX_COUNT" "Go module graph budget"
  positive_integer "$GO_DIRECT_MODULE_MAX_COUNT" "direct Go module budget"

  counts=$(awk -F '\t' '
    NF != 2 || $1 == "" || ($2 != "direct" && $2 != "indirect") { exit 1 }
    seen[$1]++ { exit 1 }
    { total++; if ($2 == "direct") direct++ }
    END {
      if (total == 0) exit 1
      print total, direct + 0
    }
  ' "$file") || fail "dependency graph is malformed or contains duplicate modules: $file"
  set -- $counts
  actual_total=$1
  actual_direct=$2
  test "$actual_total" -le "$GO_MODULE_GRAPH_MAX_COUNT" ||
    fail "Go module graph contains $actual_total modules; maximum is $GO_MODULE_GRAPH_MAX_COUNT"
  test "$actual_direct" -le "$GO_DIRECT_MODULE_MAX_COUNT" ||
    fail "Go module graph contains $actual_direct direct modules; maximum is $GO_DIRECT_MODULE_MAX_COUNT"
  echo "quality budget: Go module graph $actual_total/$GO_MODULE_GRAPH_MAX_COUNT total; $actual_direct/$GO_DIRECT_MODULE_MAX_COUNT direct"
}

case "${1:-}" in
  coverage)
    profile=${2:-}
    test -n "$profile" || fail "usage: $0 coverage COVERAGE_PROFILE"
    check_coverage "$profile" "$COVERAGE_MIN_PERCENT" total
    ;;
  watchsandbox-coverage)
    profile=${2:-}
    test -n "$profile" || fail "usage: $0 watchsandbox-coverage COVERAGE_PROFILE"
    check_coverage "$profile" "$WATCHSANDBOX_COVERAGE_MIN_PERCENT" watchsandbox
    ;;
  cloudevents-coverage)
    profile=${2:-}
    test -n "$profile" || fail "usage: $0 cloudevents-coverage COVERAGE_PROFILE"
    check_coverage "$profile" "$CLOUDEVENTS_COVERAGE_MIN_PERCENT" cloudevents
    ;;
  artifacts)
    directory=${2:-}
    test -n "$directory" || fail "usage: $0 artifacts ARTIFACT_DIRECTORY"
    check_size exporter-amd64 "$directory/exporter-amd64.oci" "$EXPORTER_OCI_MAX_BYTES"
    check_size exporter-arm64 "$directory/exporter-arm64.oci" "$EXPORTER_OCI_MAX_BYTES"
    check_size otlp-proxy-amd64 "$directory/otlp-proxy-amd64.oci" "$OTLP_PROXY_OCI_MAX_BYTES"
    check_size otlp-proxy-arm64 "$directory/otlp-proxy-arm64.oci" "$OTLP_PROXY_OCI_MAX_BYTES"
    ;;
  benchmarks)
    output=${2:-}
    test -n "$output" || fail "usage: $0 benchmarks BENCHMARK_OUTPUT"
    check_benchmarks "$output"
    ;;
  dependencies)
    graph=${2:-}
    test -n "$graph" || fail "usage: $0 dependencies MODULE_GRAPH"
    check_dependencies "$graph"
    ;;
  *)
    fail "usage: $0 {coverage COVERAGE_PROFILE|watchsandbox-coverage COVERAGE_PROFILE|cloudevents-coverage COVERAGE_PROFILE|artifacts ARTIFACT_DIRECTORY|benchmarks BENCHMARK_OUTPUT|dependencies MODULE_GRAPH}"
    ;;
esac
