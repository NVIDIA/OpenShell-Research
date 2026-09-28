#!/usr/bin/env bash
# Build the reviewed standalone CLI without changing any OpenShell checkout.
set -euo pipefail

# OpenShell PR #3533 after merging main on 2026-09-28. Pin because --version
# reports the workspace version, not the boundary-check contract or commit.
prover_revision=df10520d3828768189faa348ba9f6e6f807ab1cb
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build_dir="$(mktemp -d "${TMPDIR:-/tmp}/openshell-prover-build.XXXXXX")"
trap 'rm -rf "$build_dir"' EXIT

if command -v cargo >/dev/null 2>&1; then
    cargo_bin="$(command -v cargo)"
else
    cargo_bin="${HOME}/.cargo/bin/cargo"
fi
if [[ ! -x "$cargo_bin" ]]; then
    echo 'Install Rust with rustup before building openshell-prover.' >&2
    exit 1
fi

# Homebrew Z3 works without pkg-config when its library directory is supplied.
# Other platforms can set Z3_LIBRARY_PATH_OVERRIDE themselves, or pass
# --features bundled-z3 (requires CMake) to this script.
if [[ -z "${Z3_LIBRARY_PATH_OVERRIDE:-}" ]] && command -v brew >/dev/null 2>&1; then
    z3_prefix="$(brew --prefix z3 2>/dev/null || true)"
    if [[ -d "$z3_prefix/lib" ]]; then
        export Z3_LIBRARY_PATH_OVERRIDE="$z3_prefix/lib"
    fi
fi
if [[ -n "${Z3_LIBRARY_PATH_OVERRIDE:-}" ]]; then
    export RUSTFLAGS="${RUSTFLAGS:-} -L native=$Z3_LIBRARY_PATH_OVERRIDE"
fi

git -C "$build_dir" init --quiet
git -C "$build_dir" fetch --quiet --depth=1 https://github.com/NVIDIA/OpenShell.git "$prover_revision"
git -C "$build_dir" checkout --quiet --detach FETCH_HEAD
(
    cd "$build_dir"
    "$cargo_bin" build --locked -p openshell-prover-cli --bin openshell-prover "$@"
)
mkdir -p "$project_dir/.state/bin"
install -m 0755 "$build_dir/target/debug/openshell-prover" "$project_dir/.state/bin/openshell-prover"
printf '%s\n' "$prover_revision" > "$project_dir/.state/bin/openshell-prover.commit"
"$project_dir/.state/bin/openshell-prover" --version
printf 'Installed prover commit %s in %s/.state/bin\n' "$prover_revision" "$project_dir"
