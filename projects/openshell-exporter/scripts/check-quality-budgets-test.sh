#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
. "$repo_root/quality/budgets.env"
fixture=$(mktemp -d)
cleanup() { rm -rf "$fixture"; }
trap cleanup EXIT

mkdir -p "$fixture/artifacts"
for name in exporter-amd64 exporter-arm64 otlp-proxy-amd64 otlp-proxy-arm64; do
  printf 'small fixture\n' > "$fixture/artifacts/$name.oci"
done
"$repo_root/scripts/check-quality-budgets.sh" artifacts "$fixture/artifacts" >/dev/null

truncate -s 33554433 "$fixture/artifacts/exporter-amd64.oci"
if "$repo_root/scripts/check-quality-budgets.sh" artifacts "$fixture/artifacts" >/dev/null 2>&1; then
  echo "quality budget test: oversized exporter archive was accepted" >&2
  exit 1
fi

cat > "$fixture/example.go" <<'EOF'
package fixture

func covered() {}
EOF
cat > "$fixture/coverage.out" <<EOF
mode: set
$fixture/example.go:3.16,3.18 1 1
EOF
"$repo_root/scripts/check-quality-budgets.sh" coverage "$fixture/coverage.out" >/dev/null
"$repo_root/scripts/check-quality-budgets.sh" watchsandbox-coverage "$fixture/coverage.out" >/dev/null
"$repo_root/scripts/check-quality-budgets.sh" cloudevents-coverage "$fixture/coverage.out" >/dev/null

cat > "$fixture/coverage.out" <<EOF
mode: set
$fixture/example.go:3.16,3.18 1 0
EOF
if "$repo_root/scripts/check-quality-budgets.sh" coverage "$fixture/coverage.out" >/dev/null 2>&1; then
  echo "quality budget test: zero coverage was accepted" >&2
  exit 1
fi
if "$repo_root/scripts/check-quality-budgets.sh" watchsandbox-coverage "$fixture/coverage.out" >/dev/null 2>&1; then
  echo "quality budget test: zero WatchSandbox coverage was accepted" >&2
  exit 1
fi
if "$repo_root/scripts/check-quality-budgets.sh" cloudevents-coverage "$fixture/coverage.out" >/dev/null 2>&1; then
  echo "quality budget test: zero CloudEvents coverage was accepted" >&2
  exit 1
fi

write_benchmarks() {
  ocsf_bytes=$1
  ocsf_allocations=$2
  relay_bytes=$3
  relay_allocations=$4
  cloudevents_bytes=$5
  cloudevents_allocations=$6
  checkpoint_bytes=$7
  checkpoint_allocations=$8
  pagination_bytes=$9
  pagination_allocations=${10}
  cat > "$fixture/benchmarks.txt" <<EOF
BenchmarkOCSFNormalization-8                 100  41081 ns/op   $ocsf_bytes B/op  $ocsf_allocations allocs/op
BenchmarkRelayAllowListProcessing-8         100  23976 ns/op   $relay_bytes B/op  $relay_allocations allocs/op
BenchmarkCloudEventsBatchDelivery-8         100 383408 ns/op 534.16 MB/s $cloudevents_bytes B/op $cloudevents_allocations allocs/op
BenchmarkPolicyCheckpointEncoding-8         100   1948 ns/op $checkpoint_bytes B/op $checkpoint_allocations allocs/op
BenchmarkPolicyRevisionAccumulator-8        100   4424 ns/op $pagination_bytes B/op $pagination_allocations allocs/op
EOF
}

write_benchmarks 19981 382 4032 57 562272 2030 1332 16 9472 11
"$repo_root/scripts/check-quality-budgets.sh" benchmarks "$fixture/benchmarks.txt" >/dev/null

write_benchmarks 24577 382 4032 57 562272 2030 1332 16 9472 11
if "$repo_root/scripts/check-quality-budgets.sh" benchmarks "$fixture/benchmarks.txt" >/dev/null 2>&1; then
  echo "quality budget test: excessive OCSF bytes per operation were accepted" >&2
  exit 1
fi

write_benchmarks 19981 382 4032 76 562272 2030 1332 16 9472 11
if "$repo_root/scripts/check-quality-budgets.sh" benchmarks "$fixture/benchmarks.txt" >/dev/null 2>&1; then
  echo "quality budget test: excessive Relay allocations per operation were accepted" >&2
  exit 1
fi

write_benchmarks 19981 382 4032 57 562272 2030 1332 16 9472 11
grep -v BenchmarkCloudEventsBatchDelivery "$fixture/benchmarks.txt" > "$fixture/missing-benchmark.txt"
if "$repo_root/scripts/check-quality-budgets.sh" benchmarks "$fixture/missing-benchmark.txt" >/dev/null 2>&1; then
  echo "quality budget test: missing CloudEvents benchmark was accepted" >&2
  exit 1
fi

write_benchmarks 19981 382 4032 57 562272 2030 2049 16 9472 11
if "$repo_root/scripts/check-quality-budgets.sh" benchmarks "$fixture/benchmarks.txt" >/dev/null 2>&1; then
  echo "quality budget test: excessive checkpoint bytes per operation were accepted" >&2
  exit 1
fi

write_benchmarks 19981 382 4032 57 562272 2030 1332 16 9472 11
grep -v BenchmarkPolicyCheckpointEncoding "$fixture/benchmarks.txt" > "$fixture/missing-benchmark.txt"
if "$repo_root/scripts/check-quality-budgets.sh" benchmarks "$fixture/missing-benchmark.txt" >/dev/null 2>&1; then
  echo "quality budget test: missing checkpoint benchmark was accepted" >&2
  exit 1
fi

write_benchmarks 19981 382 4032 57 562272 2030 1332 16 12289 11
if "$repo_root/scripts/check-quality-budgets.sh" benchmarks "$fixture/benchmarks.txt" >/dev/null 2>&1; then
  echo "quality budget test: excessive pagination bytes per operation were accepted" >&2
  exit 1
fi

write_benchmarks 19981 382 4032 57 562272 2030 1332 16 9472 11
grep -v BenchmarkPolicyRevisionAccumulator "$fixture/benchmarks.txt" > "$fixture/missing-benchmark.txt"
if "$repo_root/scripts/check-quality-budgets.sh" benchmarks "$fixture/missing-benchmark.txt" >/dev/null 2>&1; then
  echo "quality budget test: missing pagination benchmark was accepted" >&2
  exit 1
fi

cat > "$fixture/benchmarks.txt" <<'EOF'
BenchmarkOCSFNormalization-8 100 41081 ns/op malformed B/op 382 allocs/op
BenchmarkRelayAllowListProcessing-8 100 23976 ns/op 4032 B/op 57 allocs/op
BenchmarkCloudEventsBatchDelivery-8 100 383408 ns/op 562272 B/op 2030 allocs/op
BenchmarkPolicyCheckpointEncoding-8 100 1948 ns/op 1332 B/op 16 allocs/op
BenchmarkPolicyRevisionAccumulator-8 100 4424 ns/op 9472 B/op 11 allocs/op
EOF
if "$repo_root/scripts/check-quality-budgets.sh" benchmarks "$fixture/benchmarks.txt" >/dev/null 2>&1; then
  echo "quality budget test: malformed benchmark metric was accepted" >&2
  exit 1
fi

cat > "$fixture/modules.txt" <<'EOF'
example.com/direct	direct
example.com/indirect	indirect
EOF
"$repo_root/scripts/check-quality-budgets.sh" dependencies "$fixture/modules.txt" >/dev/null

: > "$fixture/modules.txt"
for index in $(seq 1 "$((GO_MODULE_GRAPH_MAX_COUNT + 1))"); do
  printf 'example.com/module-%s\tindirect\n' "$index" >> "$fixture/modules.txt"
done
if "$repo_root/scripts/check-quality-budgets.sh" dependencies "$fixture/modules.txt" >/dev/null 2>&1; then
  echo "quality budget test: oversized module graph was accepted" >&2
  exit 1
fi

: > "$fixture/modules.txt"
for index in $(seq 1 "$((GO_DIRECT_MODULE_MAX_COUNT + 1))"); do
  printf 'example.com/direct-%s\tdirect\n' "$index" >> "$fixture/modules.txt"
done
if "$repo_root/scripts/check-quality-budgets.sh" dependencies "$fixture/modules.txt" >/dev/null 2>&1; then
  echo "quality budget test: excessive direct dependencies were accepted" >&2
  exit 1
fi

cat > "$fixture/modules.txt" <<'EOF'
example.com/duplicate	direct
example.com/duplicate	indirect
EOF
if "$repo_root/scripts/check-quality-budgets.sh" dependencies "$fixture/modules.txt" >/dev/null 2>&1; then
  echo "quality budget test: duplicate dependency records were accepted" >&2
  exit 1
fi

printf 'example.com/malformed\tunknown\n' > "$fixture/modules.txt"
if "$repo_root/scripts/check-quality-budgets.sh" dependencies "$fixture/modules.txt" >/dev/null 2>&1; then
  echo "quality budget test: malformed dependency graph was accepted" >&2
  exit 1
fi

echo "quality budget tests passed"
