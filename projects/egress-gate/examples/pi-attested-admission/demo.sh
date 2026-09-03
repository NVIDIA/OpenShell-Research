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
env_file=${PI_EGRESS_ENV_FILE-$script_dir/.env}
if [[ -n $env_file && -f $env_file ]]; then
	set -a
	# shellcheck disable=SC1090
	source "$env_file"
	set +a
fi

forks_dir=${PI_EGRESS_FORKS_DIR:-$egress_gate_dir/.workspaces/pi-attested-admission}
pi_repo=${PI_REPO:-$forks_dir/pi}
openshell_repo=${OPENSHELL_REPO:-$forks_dir/OpenShell}
pi_branch=johnny/before-user-message-commit
openshell_branch=openshell/pi-egress-admission
pi_remote=https://github.com/johnnygreco/pi.git
openshell_remote=https://github.com/johnnygreco/OpenShell.git
host_ip=${EGRESS_GATE_HOST_IP:-YOUR_HOST_IPV4}
models_path_value=${PI_MODELS_PATH:-YOUR_MODELS_PATH}
if [[ $models_path_value == /* || $models_path_value == YOUR_MODELS_PATH ]]; then
	models_path=$models_path_value
else
	models_path=$script_dir/${models_path_value#./}
fi
workspace_path=${PI_WORKSPACE_PATH:-}
pack_dir=${PI_EGRESS_PACK_DIR:-/tmp/pi-egress-pack}
runtime_dir=${PI_EGRESS_RUNTIME_DIR:-/tmp/pi-egress-runtime}
openshell_cli=$openshell_repo/scripts/bin/openshell
gateway_name=${PI_EGRESS_GATEWAY_NAME:-pi-egress-demo-gateway}
runtime_policy=$script_dir/policy.yaml
runtime_provider_profile=$script_dir/provider-profile.yaml
runtime_gateway_fragment=$runtime_dir/gateway-middleware.toml
gateway_fragment_template=$script_dir/gateway-middleware.toml.example
pi_settings=$script_dir/settings.json
runtime_extension_source=$script_dir/runtime-extension
runtime_extension_build=$runtime_dir/integration
egress_gate_log=${EGRESS_GATE_LOG:-$runtime_dir/egress-gate.jsonl}
z3_library_path_override=${Z3_LIBRARY_PATH_OVERRIDE:-}

bold=""
green=""
yellow=""
blue=""
cyan=""
reset=""
if [[ ${NO_COLOR+x} != x && (${FORCE_COLOR:-0} == 1 || (-t 1 && ${TERM:-} != dumb)) ]]; then
	bold=$'\033[1m'
	green=$'\033[32m'
	yellow=$'\033[33m'
	blue=$'\033[34m'
	cyan=$'\033[36m'
	reset=$'\033[0m'
fi

print_command() {
	local directory=$1
	shift
	local argument
	local column=2
	local token
	printf '  %bworking directory%b: %s\n' "$cyan" "$reset" "$directory"
	printf '  %bcommand%b:\n    ' "$green" "$reset"
	for argument in "$@"; do
		printf -v token '%q' "$argument"
		if ((column > 2 && column + ${#token} + 1 > 96)); then
			printf ' \\\n      '
			column=6
		fi
		if ((column > 2)); then
			printf ' '
			((column += 1))
		fi
		printf '%s' "$token"
		((column += ${#token}))
	done
	printf '\n'
}

describe_printed_commands() {
	if $print_only; then
		printf '\n%b%s%b\n' "$bold$blue" "$1" "$reset"
	fi
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

require_file_contains() {
	local path=$1
	local expected_text=$2
	local description=$3
	require_file "$path" "$description"
	if ! grep -Fq -- "$expected_text" "$path"; then
		printf '%s is missing the required admission hook: %s\n' "$description" "$path" >&2
		printf 'Run `./demo.sh prepare` to rebuild the local Pi runtime.\n' >&2
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

require_compute_backend() {
	local requested_driver=${OPENSHELL_DRIVERS:-}
	if [[ -n ${KUBERNETES_SERVICE_HOST:-} ]]; then
		return
	fi
	if [[ -z $requested_driver || $requested_driver == podman ]]; then
		if command -v podman >/dev/null 2>&1 && podman info >/dev/null 2>&1; then
			return
		fi
	fi
	if [[ -z $requested_driver || $requested_driver == docker ]]; then
		if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
			return
		fi
	fi
	if [[ -n $requested_driver && $requested_driver != podman && $requested_driver != docker ]]; then
		return
	fi

	printf 'No running OpenShell compute backend was detected.\n' >&2
	printf 'Start Docker Desktop or Podman, wait until its info command succeeds, then retry:\n' >&2
	printf '  docker info\n' >&2
	printf '  # or: podman info\n' >&2
	printf 'For another supported backend, set OPENSHELL_DRIVERS before running gateway.\n' >&2
	exit 1
}

raise_gateway_open_file_limit() {
	local target=10240
	local hard_limit
	local soft_limit
	hard_limit=$(ulimit -Hn)
	soft_limit=$(ulimit -Sn)
	if [[ $soft_limit == unlimited ]]; then
		return
	fi
	if [[ $hard_limit != unlimited && $hard_limit -lt $target ]]; then
		target=$hard_limit
	fi
	if ((soft_limit >= target)); then
		return
	fi
	if ! ulimit -Sn "$target"; then
		printf 'Could not raise the open-file limit from %s to %s for the OpenShell build.\n' \
			"$soft_limit" "$target" >&2
		printf 'Run `ulimit -n %s` in this terminal, then retry.\n' "$target" >&2
		exit 1
	fi
}

require_gateway_z3() {
	local z3_prefix
	if [[ -n $z3_library_path_override ]]; then
		return
	fi
	if command -v pkg-config >/dev/null 2>&1 && pkg-config --exists z3; then
		return
	fi
	case $(uname -s) in
	Darwin)
		if command -v brew >/dev/null 2>&1; then
			z3_prefix=$(brew --prefix z3 2>/dev/null || true)
			if [[ -f $z3_prefix/lib/libz3.dylib ]]; then
				z3_library_path_override=$z3_prefix/lib
				return
			fi
		fi
		printf 'The OpenShell gateway build requires Z3. Install it, then retry:\n' >&2
		printf '  brew install z3\n' >&2
		;;
	Linux)
		if command -v ldconfig >/dev/null 2>&1 && ldconfig -p 2>/dev/null | grep -q 'libz3\.so'; then
			return
		fi
		printf 'The OpenShell gateway build requires the Z3 development library.\n' >&2
		printf 'On Debian or Ubuntu, install it with: sudo apt-get install libz3-dev\n' >&2
		;;
	*)
		printf 'The OpenShell gateway build requires the Z3 native library.\n' >&2
		printf 'Install Z3 or set Z3_LIBRARY_PATH_OVERRIDE to its library directory.\n' >&2
		;;
	esac
	exit 1
}

require_host_configuration() {
	if [[ -n ${EGRESS_GATE_HOST_IP:-} && ${EGRESS_GATE_HOST_IP:-} != YOUR_HOST_IPV4 ]]; then
		return
	fi
	printf 'Set EGRESS_GATE_HOST_IP in %s before starting the gateway.\n' "$env_file" >&2
	exit 1
}

require_setup_configuration() {
	local missing=()
	if [[ -z ${EGRESS_GATE_HOST_IP:-} || ${EGRESS_GATE_HOST_IP:-} == YOUR_HOST_IPV4 ]]; then
		missing+=(EGRESS_GATE_HOST_IP)
	fi
	if [[ -z ${PI_MODELS_PATH:-} || ${PI_MODELS_PATH:-} == YOUR_MODELS_PATH ]]; then
		missing+=(PI_MODELS_PATH)
	fi
	if [[ -z ${PI_MODEL_API_KEY:-} || ${PI_MODEL_API_KEY:-} == your-provider-key ]]; then
		missing+=(PI_MODEL_API_KEY)
	fi
	if ((${#missing[@]} == 0)); then
		require_file "$models_path" "Pi model configuration"
		if [[ -n $workspace_path ]]; then
			require_directory "$workspace_path" "Pi workspace"
		fi
		return
	fi

	printf 'The Pi attested-admission example is not configured.\n' >&2
	printf 'Set these environment variables:\n' >&2
	printf '  %s\n' "${missing[@]}" >&2
	printf '\n' >&2
	printf 'Configure %s:\n' "$env_file" >&2
	printf '  cd %s\n' "$script_dir" >&2
	if [[ ! -f $env_file ]]; then
		printf '  cp .env.example .env\n' >&2
	fi
	printf '  # Edit .env and replace every example value.\n' >&2
	exit 1
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

ensure_checkout() {
	local repository=$1
	local description=$2
	local remote=$3
	local branch=$4
	local parent
	parent=$(dirname -- "$repository")
	if $print_only; then
		describe_printed_commands "$description (only when missing):"
		print_command "$parent" git clone --branch "$branch" "$remote" "$repository"
		return
	fi
	if [[ -e $repository && ! -d $repository/.git ]]; then
		printf '%s path exists but is not a Git checkout: %s\n' "$description" "$repository" >&2
		exit 1
	fi
	if [[ ! -d $repository/.git ]]; then
		mkdir -p "$parent"
		run_in "$parent" git clone --branch "$branch" "$remote" "$repository"
	fi
}

sync_forks() {
	ensure_checkout "$pi_repo" "Pi checkout" "$pi_remote" "$pi_branch"
	ensure_checkout "$openshell_repo" "OpenShell checkout" "$openshell_remote" "$openshell_branch"
	if ! $print_only; then
		require_branch "$pi_repo" "$pi_branch"
		require_branch "$openshell_repo" "$openshell_branch"
	fi
	describe_printed_commands "Update the Pi fork:"
	run_in "$pi_repo" git pull --no-rebase --ff-only origin "$pi_branch"
	describe_printed_commands "Update the OpenShell fork:"
	run_in "$openshell_repo" git pull --no-rebase --ff-only origin "$openshell_branch"
}

pi_package_tarball() {
	local package_directory=$1
	local archive_name=$2
	if $print_only; then
		printf '%s/%s-VERSION.tgz' "$pack_dir" "$archive_name"
		return
	fi
	require_file "$package_directory/package.json" "Pi package"
	local version
	version=$(node -p "require(process.argv[1]).version" "$package_directory/package.json")
	printf '%s/%s-%s.tgz' "$pack_dir" "$archive_name" "$version"
}

prepare_gateway_configuration() {
	if $print_only; then
		describe_printed_commands "Write the one host-specific gateway setting:"
		printf '  %bsource%b: %s\n' "$cyan" "$reset" "$gateway_fragment_template"
		printf '  %boutput%b: %s\n' "$green" "$reset" "$runtime_gateway_fragment"
		printf '  Replace YOUR_HOST_IPV4 with %s.\n' "$host_ip"
		return
	fi
	require_file "$gateway_fragment_template" "gateway middleware configuration template"
	mkdir -p "$runtime_dir"
	sed "s/YOUR_HOST_IPV4/$host_ip/" "$gateway_fragment_template" >"$runtime_gateway_fragment"
}

prepare() {
	sync_forks
	local agent_tarball
	local coding_agent_tarball
	agent_tarball=$(pi_package_tarball "$pi_repo/packages/agent" "earendil-works-pi-agent-core")
	coding_agent_tarball=$(pi_package_tarball "$pi_repo/packages/coding-agent" "earendil-works-pi-coding-agent")

	describe_printed_commands "Build and package Pi:"
	run_in "$pi_repo" npm install --ignore-scripts
	run_in "$pi_repo" npm run build:offline
	run_in "$pi_repo" mkdir -p "$pack_dir" "$runtime_dir"
	run_in "$pi_repo" npm pack --workspace @earendil-works/pi-agent-core --pack-destination "$pack_dir"
	run_in "$pi_repo" npm pack --workspace @earendil-works/pi-coding-agent --pack-destination "$pack_dir"
	run_in "$pi_repo" npm install --prefix "$runtime_dir" --ignore-scripts "$agent_tarball" "$coding_agent_tarball"
	describe_printed_commands "Type-check and compile the trusted runtime extension:"
	run_in "$pi_repo" mkdir -p "$runtime_dir/integration-src" "$runtime_extension_build"
	run_in "$pi_repo" cp -R "$runtime_extension_source/." "$runtime_dir/integration-src"
	run_in "$runtime_dir/integration-src" "$pi_repo/node_modules/.bin/tsgo" -p tsconfig.json
	run_in "$runtime_dir/integration-src" cp package.json "$runtime_extension_build/package.json"
}

serve() {
	describe_printed_commands "Run Egress Gate and keep it open:"
	run_in "$egress_gate_dir" uv run egress-gate --debug serve \
		--listen 0.0.0.0:50051 --timeout 4s --require-agent-attestation \
		--json-log "$egress_gate_log"
}

gateway() {
	if ! $print_only; then
		require_host_configuration
		require_compute_backend
		raise_gateway_open_file_limit
		require_gateway_z3
		require_directory "$openshell_repo" "OpenShell checkout"
		require_branch "$openshell_repo" "$openshell_branch"
	fi
	prepare_gateway_configuration
	# A custom checkout may be nested below this uv project. Keep OpenShell's
	# mise-pinned uv from inheriting Egress Gate's uv configuration.
	describe_printed_commands "Start the matching OpenShell gateway and keep it open:"
	run_in "$openshell_repo" env UV_NO_CONFIG=1 mise trust
	local gateway_environment=(
		env
		UV_NO_CONFIG=1
		CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-4}"
		OPENSHELL_GATEWAY_NAME="$gateway_name"
		OPENSHELL_GATEWAY_CONFIG_FRAGMENT="$runtime_gateway_fragment"
	)
	if [[ -n $z3_library_path_override ]]; then
		gateway_environment+=(Z3_LIBRARY_PATH_OVERRIDE="$z3_library_path_override")
	fi
	run_in "$openshell_repo" "${gateway_environment[@]}" mise run gateway
}

ensure_model_provider() {
	if $print_only; then
		run_in "$openshell_repo" "$openshell_cli" --gateway "$gateway_name" \
			provider delete pi-model
		run_in "$openshell_repo" "$openshell_cli" --gateway "$gateway_name" \
			provider profile delete pi-attested-model
		run_in "$openshell_repo" "$openshell_cli" --gateway "$gateway_name" \
			provider profile import --file "$runtime_provider_profile"
		run_in "$openshell_repo" "$openshell_cli" --gateway "$gateway_name" provider create \
			--name pi-model --type pi-attested-model --credential PI_MODEL_API_KEY
		return
	fi
	if (cd -- "$openshell_repo" && "$openshell_cli" --gateway "$gateway_name" \
		provider get pi-model >/dev/null 2>&1); then
		run_in "$openshell_repo" "$openshell_cli" --gateway "$gateway_name" \
			provider delete pi-model
	fi
	if (cd -- "$openshell_repo" && "$openshell_cli" --gateway "$gateway_name" \
		provider profile export pi-attested-model >/dev/null 2>&1); then
		run_in "$openshell_repo" "$openshell_cli" --gateway "$gateway_name" \
			provider profile delete pi-attested-model
	fi
	run_in "$openshell_repo" "$openshell_cli" --gateway "$gateway_name" \
		provider profile import --file "$runtime_provider_profile"
	run_in "$openshell_repo" "$openshell_cli" --gateway "$gateway_name" provider create \
		--name pi-model --type pi-attested-model --credential PI_MODEL_API_KEY
}

delete_demo_sandbox_if_present() {
	if $print_only; then
		run_in "$openshell_repo" "$openshell_cli" --gateway "$gateway_name" \
			sandbox delete pi-egress-demo
		return
	fi
	if ! $print_only && (cd -- "$openshell_repo" && "$openshell_cli" --gateway "$gateway_name" \
		sandbox list --names | grep -Fxq pi-egress-demo); then
		printf 'Replacing existing sandbox pi-egress-demo with the current example runtime.\n'
		run_in "$openshell_repo" "$openshell_cli" --gateway "$gateway_name" \
			sandbox delete pi-egress-demo
	fi
}

create_demo_sandbox() {
	run_in "$script_dir" "$openshell_cli" --gateway "$gateway_name" sandbox create \
		--name pi-egress-demo \
		--from "$script_dir/sandbox" \
		--provider pi-model \
		--policy "$runtime_policy" \
		--upload "$runtime_dir/node_modules:/sandbox/pi-runtime" \
		--upload "$models_path:/sandbox/.pi/agent/models.json" \
		--upload "$pi_settings:/sandbox/.pi/agent/settings.json" \
		--upload "$runtime_extension_build/openshell-context-admission.js:/sandbox/pi-runtime/integration/openshell-context-admission.js" \
		--upload "$runtime_extension_build/openshell-pi.js:/sandbox/pi-runtime/integration/openshell-pi.js" \
		--upload "$runtime_extension_build/package.json:/sandbox/pi-runtime/integration/package.json" \
		--no-git-ignore \
		--detach
	if [[ -n $workspace_path ]]; then
		describe_printed_commands "Upload the selected workspace with its .gitignore rules:"
		run_in "$workspace_path" "$openshell_cli" --gateway "$gateway_name" sandbox upload \
			pi-egress-demo . /sandbox/workspace
	elif $print_only; then
		describe_printed_commands "No workspace selected; Pi starts in an empty /sandbox/workspace."
	fi
}

reset_demo() {
	if ! $print_only; then
		require_setup_configuration
		require_file "$openshell_cli" "OpenShell CLI wrapper"
		require_file "$(pi_package_tarball "$pi_repo/packages/agent" "earendil-works-pi-agent-core")" \
			"packed Pi agent core"
		require_file "$(pi_package_tarball "$pi_repo/packages/coding-agent" "earendil-works-pi-coding-agent")" \
			"packed Pi coding-agent"
		require_file_contains \
			"$runtime_dir/node_modules/@earendil-works/pi-agent-core/dist/agent-loop.js" \
			"beforeToolResultAppend" \
			"installed Pi agent core"
		require_file_contains \
			"$runtime_dir/node_modules/@earendil-works/pi-coding-agent/dist/bundle/index.js" \
			"runCli" \
			"installed Pi SDK"
		require_file "$pi_settings" "Pi settings"
		require_file "$runtime_extension_build/openshell-context-admission.js" \
			"compiled OpenShell context-admission adapter"
		require_file "$runtime_extension_build/openshell-pi.js" "compiled OpenShell Pi launcher"
		require_file "$runtime_extension_build/package.json" "runtime-extension package metadata"
		require_file "$runtime_policy" "OpenShell sandbox policy"
		require_file "$runtime_provider_profile" "OpenShell provider profile"
	fi

	describe_printed_commands "Remove an earlier example sandbox, if present:"
	delete_demo_sandbox_if_present
	describe_printed_commands "Register the endpoint-scoped model credential in OpenShell:"
	ensure_model_provider
	describe_printed_commands "Create a fresh sandbox and upload the Pi runtime:"
	create_demo_sandbox
	printf 'The demo sandbox is ready. Run: ./demo.sh launch\n'
}

require_demo_sandbox() {
	if (cd -- "$openshell_repo" && "$openshell_cli" --gateway "$gateway_name" \
		sandbox list --names | grep -Fxq pi-egress-demo); then
		return
	fi
	printf 'The pi-egress-demo sandbox does not exist. Create it with: ./demo.sh reset\n' >&2
	exit 1
}

launch() {
	if ! $print_only; then
		require_file "$openshell_cli" "OpenShell CLI wrapper"
		require_demo_sandbox
	fi
	describe_printed_commands "Launch Pi interactively in the prepared sandbox:"
	run_in "$openshell_repo" "$openshell_cli" --gateway "$gateway_name" \
		sandbox exec --tty -n pi-egress-demo --workdir /sandbox/workspace -- \
		env \
		OPENSHELL_AGENT_CONVERSATION_URL=http://127.0.0.1:8193/v1/agent/conversation \
		node /sandbox/pi-runtime/integration/openshell-pi.js
}

run_sandbox_command() {
	run_in "$openshell_repo" "$openshell_cli" --gateway "$gateway_name" \
		sandbox exec --no-tty -n pi-egress-demo --workdir /sandbox/workspace -- "$@"
}

capture_sandbox_command() {
	local output=$1
	local error=$2
	shift 2
	if $print_only; then
		run_sandbox_command "$@"
		return
	fi
	(cd -- "$openshell_repo" && "$openshell_cli" --gateway "$gateway_name" \
		sandbox exec --no-tty -n pi-egress-demo --workdir /sandbox/workspace -- "$@") \
		>"$output" 2>"$error"
}

copy_session() {
	local session_file=$1
	local output=$2
	local error=$3
	capture_sandbox_command "$output" "$error" /bin/cat "$session_file"
}

log_line_count() {
	if [[ -f $egress_gate_log ]]; then
		wc -l <"$egress_gate_log"
	else
		printf '0\n'
	fi
}

assert_logged_reason() {
	local first_line=$1
	local reason=$2
	local label=$3
	if ! awk -v first="$first_line" -v reason="\"reason_code\":\"$reason\"" \
		'NR >= first && index($0, reason) { found = 1 } END { exit !found }' \
		"$egress_gate_log"; then
		printf '%s did not produce reason code %s in %s.\n' "$label" "$reason" "$egress_gate_log" >&2
		exit 1
	fi
	printf 'PASS %-18s reason_code=%s\n' "$label" "$reason"
}

verify() {
	local verify_id="$(date +%s)-$$"
	local verify_dir="/sandbox/pi-admission-verify/$verify_id"
	local bridge_url="http://127.0.0.1:8193/v1/agent/conversation"
	local temporary_dir
	local output
	local error
	local status
	local session
	local first_log_line
	local raw_request_script='fetch("https://inference-api.nvidia.com/v1/chat/completions", {method:"POST", headers:{"content-type":"application/json", authorization:"Bearer openshell-proxy"}, body:JSON.stringify({model:"nvidia/qwen/qwen3.8-flash-next", messages:[{role:"user",content:"hello"}]})}).then(async response => { console.error(`provider status=${response.status}`); process.exit(response.ok ? 0 : 1); }).catch(error => { console.error(String(error)); process.exit(1); });'

	if $print_only; then
		describe_printed_commands "Create fresh real Pi sessions and run every verification case:"
		temporary_dir=/tmp/pi-admission-verify
	else
		require_file "$openshell_cli" "OpenShell CLI wrapper"
		require_demo_sandbox
		if [[ ! -f $egress_gate_log ]]; then
			printf 'Missing Egress Gate JSON log: %s\n' "$egress_gate_log" >&2
			printf 'Start Egress Gate with ./demo.sh serve before running verify.\n' >&2
			exit 1
		fi
		temporary_dir=$(mktemp -d)
		trap 'rm -rf -- "$temporary_dir"' RETURN
	fi

	output=$temporary_dir/deny.out
	error=$temporary_dir/deny.err
	session=$verify_dir/deny.jsonl
	status=0
	capture_sandbox_command "$output" "$error" env \
		OPENSHELL_AGENT_CONVERSATION_URL="$bridge_url" \
		node /sandbox/pi-runtime/integration/openshell-pi.js \
		--session "$session" -p "Reply with exactly: DENY_THIS" || status=$?
	if ! $print_only; then
		if ((status == 0)) || ! grep -Fq "OpenShell denied this context addition" "$error"; then
			printf 'Denied-prompt verification failed. See %s and %s.\n' "$output" "$error" >&2
			exit 1
		fi
		if capture_sandbox_command "$output.session" "$error.session" test -e "$session"; then
			printf 'Denied prompt unexpectedly created a session: %s\n' "$session" >&2
			exit 1
		fi
		printf 'PASS %-18s denied; session not written\n' "denied prompt"
	fi

	output=$temporary_dir/redact.out
	error=$temporary_dir/redact.err
	session=$verify_dir/redact.jsonl
	capture_sandbox_command "$output" "$error" env \
		OPENSHELL_AGENT_CONVERSATION_URL="$bridge_url" \
		node /sandbox/pi-runtime/integration/openshell-pi.js \
		--session "$session" -p "Reply with exactly: REDACT_THIS"
	copy_session "$session" "$temporary_dir/redact.jsonl" "$temporary_dir/redact-session.err"
	if ! $print_only; then
		if ! grep -Fq "[REDACTED]" "$temporary_dir/redact.jsonl" || \
			grep -Fq "REDACT_THIS" "$temporary_dir/redact.jsonl"; then
			printf 'Redacted-prompt verification failed for %s.\n' "$session" >&2
			exit 1
		fi
		if ! grep -Fq '"role":"assistant"' "$temporary_dir/redact.jsonl" || \
			grep -Fq '"stopReason":"error"' "$temporary_dir/redact.jsonl"; then
			printf 'Provider-response verification failed for %s.\n' "$session" >&2
			exit 1
		fi
		printf 'PASS %-18s provider answered; session contains only [REDACTED]\n' \
			"redacted prompt"
	fi

	capture_sandbox_command "$temporary_dir/bridge.out" "$temporary_dir/bridge.err" \
		/usr/bin/curl --silent --show-error \
		--header "content-type: application/json" \
		--data '{}' \
		--write-out $'\n%{http_code}\n' \
		"$bridge_url"
	if ! $print_only; then
		if ! grep -Fq '"error":"caller_not_authorized"' "$temporary_dir/bridge.out" || \
			! tail -n 1 "$temporary_dir/bridge.out" | grep -Fxq 401; then
			printf 'Unauthenticated bridge request was not rejected. See %s and %s.\n' \
				"$temporary_dir/bridge.out" "$temporary_dir/bridge.err" >&2
			exit 1
		fi
		printf 'PASS %-18s caller_not_authorized\n' "raw bridge"
	fi

	first_log_line=$(( $(log_line_count) + 1 ))
	status=0
	capture_sandbox_command "$temporary_dir/raw.out" "$temporary_dir/raw.err" \
		/usr/bin/node -e "$raw_request_script" || status=$?
	if ! $print_only; then
		if ((status == 0)); then
			printf 'Raw provider request unexpectedly succeeded.\n' >&2
			exit 1
		fi
		assert_logged_reason "$first_log_line" attestation_missing "raw provider"
	fi

	first_log_line=$(( $(log_line_count) + 1 ))
	status=0
	capture_sandbox_command "$temporary_dir/stock.out" "$temporary_dir/stock.err" \
		/usr/bin/pi --session "$verify_dir/stock.jsonl" -p hello || status=$?
	if ! $print_only; then
		if ((status == 0)); then
			printf 'Stock Pi unexpectedly reached the provider.\n' >&2
			exit 1
		fi
		assert_logged_reason "$first_log_line" attestation_missing "stock Pi"
	fi

	for marker in DENY REDACT; do
		local marker_lower=${marker,,}
		local expected="[REDACTED]"
		if [[ $marker == DENY ]]; then
			expected="[Tool result blocked by context admission]"
		fi
		session=$verify_dir/tool-$marker_lower.jsonl
		capture_sandbox_command "$temporary_dir/tool-$marker_lower.out" "$temporary_dir/tool-$marker_lower.err" env \
			OPENSHELL_AGENT_CONVERSATION_URL="$bridge_url" \
			node /sandbox/pi-runtime/integration/openshell-pi.js --session "$session" -p \
			"Use bash to print the concatenation of ${marker}_ and THIS, then tell me the output."
		copy_session "$session" "$temporary_dir/tool-$marker_lower.jsonl" \
			"$temporary_dir/tool-$marker_lower-session.err"
		if ! $print_only; then
			status=0
			awk -v expected="$expected" -v forbidden="${marker}_THIS" '
				index($0, "\"role\":\"toolResult\"") {
					found = 1
					if (index($0, expected)) admitted = 1
					if (index($0, forbidden)) leaked = 1
				}
				END {
					if (!found) exit 2
					if (!admitted || leaked) exit 1
				}
			' "$temporary_dir/tool-$marker_lower.jsonl" || status=$?
			if ((status == 0)); then
				printf 'PASS %-18s tool result contains %s\n' "tool $marker_lower" "$expected"
			elif ((status == 2)); then
				printf 'SKIP %-18s model did not call bash\n' "tool $marker_lower"
			else
				printf 'Tool %s result was not safely admitted.\n' "$marker_lower" >&2
				exit 1
			fi
		fi
	done

	if $print_only; then
		printf '  Assertions inspect each fresh session and %s; request content is never logged.\n' \
			"$egress_gate_log"
	fi
}

cleanup() {
	if ! $print_only; then
		require_file "$openshell_cli" "OpenShell CLI wrapper"
	fi
	describe_printed_commands "Delete the example sandbox:"
	run_in "$openshell_repo" "$openshell_cli" --gateway "$gateway_name" \
		sandbox delete pi-egress-demo
	describe_printed_commands "Delete the example credential provider:"
	run_in "$openshell_repo" "$openshell_cli" --gateway "$gateway_name" provider delete pi-model
	describe_printed_commands "Delete the example provider profile:"
	run_in "$openshell_repo" "$openshell_cli" --gateway "$gateway_name" \
		provider profile delete pi-attested-model
}

usage() {
	printf '%bUsage%b: ./demo.sh [--print] ACTION\n\n' "$bold$cyan" "$reset"
	printf '%bActions%b:\n' "$bold$blue" "$reset"
	cat <<'EOF'

  prepare  Update the forks and package Pi
  serve    Start Egress Gate
  gateway  Start the forked OpenShell gateway
  reset    Recreate the demo sandbox and configure its model credential
  launch   Start Pi in the existing sandbox without deleting its sessions
  verify   Run real deny, redact, negative-control, and best-effort tool cases
  cleanup  Delete the example sandbox and credential provider
  all      Show the concise workflow walkthrough (requires --print)
EOF

	printf '\n%bPreview before running%b:\n' "$bold$blue" "$reset"
	cat <<'EOF'
  ./demo.sh --print prepare
  ./demo.sh --print all
  ./demo.sh --print verify
EOF
}

print_plan() {
	local configuration_status="ready"
	local status_color="$green"
	local credential_status="not set"
	local displayed_host="$host_ip"
	local displayed_models_path="$models_path"
	local displayed_workspace="empty /sandbox/workspace"
	if [[ $displayed_host == YOUR_HOST_IPV4 ]]; then
		displayed_host="not set"
		configuration_status="incomplete — edit .env"
	fi
	if [[ $displayed_models_path == YOUR_MODELS_PATH ]]; then
		displayed_models_path="not set"
		configuration_status="incomplete — edit .env"
	fi
	if [[ -n ${PI_MODEL_API_KEY:-} && ${PI_MODEL_API_KEY:-} != your-provider-key ]]; then
		credential_status="set (value hidden)"
	else
		configuration_status="incomplete — edit .env"
	fi
	if [[ -n $workspace_path ]]; then
		displayed_workspace="$workspace_path"
	fi
	if [[ $configuration_status != ready ]]; then
		status_color="$yellow"
	fi
	cat <<EOF
${bold}${cyan}Pi attested-admission walkthrough${reset}

This is a preview; no commands are running. The numbered items show the order
of operations and which terminal to use. Print one action separately to inspect
its exact commands.

${bold}${blue}Configuration loaded by demo.sh${reset}
  Status:             ${status_color}${configuration_status}${reset}
  Egress Gate host:   $displayed_host
  Pi models file:     $displayed_models_path
  Pi workspace:       $displayed_workspace
  Model credential:  $credential_status

${bold}${blue}Local fork workspace${reset}
  $forks_dir
  prepare clones missing Pi and OpenShell forks here. The directory is ignored by Git.

${bold}${blue}Workflow${reset}
  ${green}1. prepare${reset}  Clone or update the forks and build the configured Pi fork.
  ${green}2. serve${reset}    Start Egress Gate in Terminal 1 and leave it running.
  ${green}3. gateway${reset}  Start the OpenShell gateway in Terminal 2 and leave it running.
  ${green}4. reset${reset}    Recreate the sandbox; upload a workspace only when one is selected.
  ${green}5. verify${reset}   Run the real non-interactive cases.
  ${green}6. launch${reset}   Optionally explore interactively with:
                Reply with exactly: DENY_THIS
                Reply with exactly: REDACT_THIS
  ${green}7. cleanup${reset}  Delete the sandbox and credential provider.

${bold}${blue}Inspect exact commands${reset}
  ./demo.sh --print prepare
  ./demo.sh --print serve
  ./demo.sh --print gateway
  ./demo.sh --print reset
  ./demo.sh --print launch
  ./demo.sh --print verify
  ./demo.sh --print cleanup

Run an action without --print when you are ready.
EOF
}

case "$action" in
	prepare) prepare ;;
	serve) serve ;;
	gateway) gateway ;;
	reset) reset_demo ;;
	launch) launch ;;
	verify) verify ;;
	cleanup) cleanup ;;
	all)
		if ! $print_only; then
			printf 'The all action is print-only. Run: ./demo.sh --print all\n' >&2
			exit 1
		fi
		print_plan
		;;
	help | --help | -h) usage ;;
	*)
		printf 'Unknown action: %s\n\n' "$action" >&2
		usage >&2
		exit 1
		;;
esac
