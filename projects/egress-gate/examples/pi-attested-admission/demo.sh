#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0


set -euo pipefail

print_only=false
if [[ ${1:-} == "--print" ]]; then
	print_only=true
	shift
fi

action=${1:-help}
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
egress_gate_dir=$(cd -- "$script_dir/../.." && pwd)
workspace_dir=$(cd -- "$egress_gate_dir/../../.." && pwd)

pi_repo=${PI_REPO:-$workspace_dir/pi}
openshell_repo=${OPENSHELL_REPO:-$workspace_dir/OpenShell}
pi_branch=johnny/before-user-message-commit
openshell_branch=openshell/pi-egress-admission
host_ip=${EGRESS_GATE_HOST_IP:-YOUR_HOST_IPV4}
pack_dir=${PI_EGRESS_PACK_DIR:-/tmp/pi-egress-pack}
runtime_dir=${PI_EGRESS_RUNTIME_DIR:-/tmp/pi-egress-runtime}
openshell_cli=$openshell_repo/scripts/bin/openshell

print_command() {
	local directory=$1
	shift
	printf '(cd %q &&' "$directory"
	printf ' %q' "$@"
	printf ')\n'
}

run_in() {
	local directory=$1
	shift
	if $print_only; then
		print_command "$directory" "$@"
	else
		(cd -- "$directory" && "$@")
	fi
}

require_file() {
	local path=$1
	local description=$2
	if [[ ! -f $path ]]; then
		printf 'Missing %s: %s\n' "$description" "$path" >&2
		exit 1
	fi
}

require_directory() {
	local path=$1
	local description=$2
	if [[ ! -d $path ]]; then
		printf 'Missing %s: %s\n' "$description" "$path" >&2
		exit 1
	fi
}

require_value() {
	local value=$1
	local name=$2
	if [[ -z $value ]]; then
		printf 'Set %s before running this action.\n' "$name" >&2
		exit 1
	fi
}

require_branch() {
	local repository=$1
	local expected=$2
	local actual
	actual=$(git -C "$repository" branch --show-current)
	if [[ $actual != "$expected" ]]; then
		printf 'Expected %s to be on branch %s, but found %s.\n' "$repository" "$expected" "${actual:-detached HEAD}" >&2
		exit 1
	fi
}

sync_forks() {
	if ! $print_only; then
		require_directory "$pi_repo" "Pi checkout"
		require_directory "$openshell_repo" "OpenShell checkout"
		require_branch "$pi_repo" "$pi_branch"
		require_branch "$openshell_repo" "$openshell_branch"
	fi
	run_in "$pi_repo" git pull --ff-only origin "$pi_branch"
	run_in "$openshell_repo" git pull --ff-only origin "$openshell_branch"
}

pi_tarball() {
	require_file "$pi_repo/packages/coding-agent/package.json" "Pi coding-agent package"
	local version
	version=$(node -p "require(process.argv[1]).version" "$pi_repo/packages/coding-agent/package.json")
	printf '%s/earendil-works-pi-coding-agent-%s.tgz' "$pack_dir" "$version"
}

prepare() {
	sync_forks
	if ! $print_only; then
		require_value "${EGRESS_GATE_HOST_IP:-}" EGRESS_GATE_HOST_IP
	fi
	local tarball
	tarball=$(pi_tarball)

	run_in "$pi_repo" npm install --ignore-scripts
	run_in "$pi_repo" npm run build
	run_in "$pi_repo" mkdir -p "$pack_dir" "$runtime_dir"
	run_in "$pi_repo" npm pack --workspace @earendil-works/pi-coding-agent --pack-destination "$pack_dir"
	run_in "$pi_repo" npm install --prefix "$runtime_dir" --ignore-scripts "$tarball"
	run_in "$egress_gate_dir" uv run egress-gate add-gateway-registration \
		--host-ip "$host_ip" --name pi-egress --port 50051
}

serve() {
	run_in "$egress_gate_dir" uv run egress-gate --debug serve \
		--listen 0.0.0.0:50051 --timeout 4s --require-pi-receipt
}

gateway() {
	if ! $print_only; then
		require_directory "$openshell_repo" "OpenShell checkout"
		require_branch "$openshell_repo" "$openshell_branch"
	fi
	run_in "$openshell_repo" mise trust
	run_in "$openshell_repo" mise run gateway
}

launch() {
	if ! $print_only; then
		require_file "$openshell_cli" "OpenShell CLI wrapper"
		require_file "$(pi_tarball)" "packed Pi coding-agent"
		require_value "${OPENAI_API_KEY:-}" OPENAI_API_KEY
	fi

	run_in "$openshell_repo" "$openshell_cli" provider create \
		--name pi-openai --type openai --credential OPENAI_API_KEY
	run_in "$script_dir" "$openshell_cli" sandbox create \
		--name pi-egress-demo \
		--from base \
		--provider pi-openai \
		--policy policy.yaml \
		--upload "$runtime_dir:/sandbox/pi-runtime" \
		--upload "$script_dir/openshell-input-admission.ts:/sandbox/openshell-input-admission.ts" \
		--upload "$script_dir/models.json:/sandbox/pi-agent/models.json" \
		-- env PI_CODING_AGENT_DIR=/sandbox/pi-agent \
		node /sandbox/pi-runtime/node_modules/@earendil-works/pi-coding-agent/dist/cli.js \
		--provider openai-chat-completions \
		--model gpt-4o-mini \
		--extension /sandbox/openshell-input-admission.ts \
		--session-dir /sandbox/pi-sessions
}

verify() {
	if ! $print_only; then
		require_file "$openshell_cli" "OpenShell CLI wrapper"
	fi
	local redacted='\[REDACTED\]'
	local forbidden='DENY_THIS|REDACT_THIS'

	if $print_only; then
		print_command "$openshell_repo" "$openshell_cli" sandbox exec -n pi-egress-demo -- \
			grep -R -n -E "$redacted" /sandbox/pi-sessions
		printf '! '
		print_command "$openshell_repo" "$openshell_cli" sandbox exec -n pi-egress-demo -- \
			grep -R -n -E "$forbidden" /sandbox/pi-sessions
		return
	fi

	if ! run_in "$openshell_repo" "$openshell_cli" sandbox exec -n pi-egress-demo -- \
		grep -R -n -E "$redacted" /sandbox/pi-sessions; then
		printf 'Verification failed: [REDACTED] was not found in Pi session history.\n' >&2
		exit 1
	fi
	if run_in "$openshell_repo" "$openshell_cli" sandbox exec -n pi-egress-demo -- \
		grep -R -n -E "$forbidden" /sandbox/pi-sessions; then
		printf 'Verification failed: denied or unredacted input was found in Pi session history.\n' >&2
		exit 1
	fi
	printf 'Verified: session history contains [REDACTED] and no original test markers.\n'
}

cleanup() {
	if ! $print_only; then
		require_file "$openshell_cli" "OpenShell CLI wrapper"
	fi
	run_in "$openshell_repo" "$openshell_cli" sandbox delete pi-egress-demo
	run_in "$openshell_repo" "$openshell_cli" provider delete pi-openai
	run_in "$egress_gate_dir" uv run egress-gate remove-gateway-registration --name pi-egress
}

usage() {
	cat <<'EOF'
Usage: ./demo.sh [--print] ACTION

Actions:
  prepare  Update the forks, package Pi, and register Egress Gate with OpenShell
  serve    Start Egress Gate
  gateway  Start the forked OpenShell gateway
  launch   Create the OpenAI provider and launch Pi in a managed sandbox
  verify   Confirm redaction and absence of original text in Pi session history
  cleanup  Delete the sandbox and provider, then remove the registration
  all      Print every action in order (requires --print)

Use --print to show exact commands without running them:
  ./demo.sh --print prepare
  ./demo.sh --print all
EOF
}

case "$action" in
	prepare) prepare ;;
	serve) serve ;;
	gateway) gateway ;;
	launch) launch ;;
	verify) verify ;;
	cleanup) cleanup ;;
	all)
		if ! $print_only; then
			printf 'The all action is print-only. Run: ./demo.sh --print all\n' >&2
			exit 1
		fi
		for step in prepare serve gateway launch verify cleanup; do
			printf '\n# %s\n' "$step"
			"$step"
		done
		;;
	help | --help | -h) usage ;;
	*)
		printf 'Unknown action: %s\n\n' "$action" >&2
		usage >&2
		exit 1
		;;
esac
