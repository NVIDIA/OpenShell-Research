// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package kubernetesdemo

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestControlImageUsesPrefetchedChecksumPinnedArtifacts(t *testing.T) {
	dockerfile := read(t, "../real-gateway/control/Dockerfile")
	manifest := read(t, "../real-gateway/control/openshell-artifacts.env")
	prepare := read(t, "../real-gateway/control/prepare-build-context.sh")
	build := read(t, "build-images.sh")
	compose := read(t, "../real-gateway/compose.yaml")
	realGatewayRun := read(t, "../real-gateway/run.sh")
	dockerignore := read(t, "../../../.dockerignore")

	if strings.Contains(dockerfile, "ADD --checksum") || strings.Contains(dockerfile, "https://") {
		t.Fatal("control Dockerfile must not fetch remote artifacts")
	}
	for _, required := range []string{
		"OPENSHELL_CONTROL_VERSION=0.0.113",
		"OPENSHELL_CONTROL_AMD64_SHA256=e6bab4e7298f311a8e04a53a089ab836d239a90ca65f66f247f71a8d5926bdd7",
		"OPENSHELL_CONTROL_ARM64_SHA256=588692603cc518ab1aa062d69cde07cb8425a245030f222544f886e45a72f69d",
	} {
		if !strings.Contains(manifest, required) {
			t.Errorf("pinned control artifact manifest is missing %q", required)
		}
	}
	for _, required := range []string{
		"scripts/download-pinned-artifact.sh",
		"$OPENSHELL_CONTROL_AMD64_SHA256",
		"$OPENSHELL_CONTROL_ARM64_SHA256",
	} {
		if !strings.Contains(prepare, required) {
			t.Errorf("control artifact preparation is missing %q", required)
		}
	}
	for _, required := range []string{
		"COPY examples/demo/real-gateway/runtime/downloads/openshell-x86_64-unknown-linux-musl.tar.gz",
		"COPY examples/demo/real-gateway/runtime/downloads/openshell-aarch64-unknown-linux-musl.tar.gz",
		"sha256sum --check --strict",
	} {
		if !strings.Contains(dockerfile, required) {
			t.Errorf("control Dockerfile is missing %q", required)
		}
	}
	if !strings.Contains(build, "control/prepare-build-context.sh") {
		t.Error("Kubernetes image build does not prepare control artifacts")
	}
	if !strings.Contains(realGatewayRun, "control/prepare-build-context.sh") {
		t.Error("real-gateway run does not prepare control artifacts")
	}
	if !strings.Contains(compose, "context: ../../..") {
		t.Error("real-gateway control build must use the repository build context")
	}
	if !strings.Contains(dockerignore, "runtime/downloads/openshell-x86_64-unknown-linux-musl.tar.gz") {
		t.Error("repository Docker context excludes the prefetched control artifacts")
	}
}

func TestBuildOCIStopsAtFirstFailedStage(t *testing.T) {
	buildScript, err := filepath.Abs("build-images.sh")
	if err != nil {
		t.Fatal(err)
	}
	repositoryRoot, err := filepath.Abs("../../..")
	if err != nil {
		t.Fatal(err)
	}

	for _, stage := range []string{"build", "import", "digest", "tag"} {
		t.Run(stage, func(t *testing.T) {
			t.Parallel()
			temporary := t.TempDir()
			fakeBin := filepath.Join(temporary, "bin")
			runtimeDir := filepath.Join(temporary, "runtime")
			callLog := filepath.Join(temporary, "calls.log")
			if err := os.MkdirAll(fakeBin, 0o755); err != nil {
				t.Fatal(err)
			}

			writeExecutable(t, filepath.Join(fakeBin, "docker"), `#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$CALL_LOG"
if [[ "$1" == buildx && "$2" == build ]]; then
  [[ "$FAIL_STAGE" != build ]] || exit 71
  archive=""
  metadata=""
  shift 2
  while (( $# > 0 )); do
    case "$1" in
      --output) archive=${2#type=oci,dest=}; shift 2 ;;
      --metadata-file) metadata=$2; shift 2 ;;
      *) shift ;;
    esac
  done
  [[ -n "$archive" && -n "$metadata" ]]
  printf 'oci\n' >"$archive"
  printf '{}\n' >"$metadata"
  exit 0
fi
if [[ "$1" == exec && "$2" == -i ]]; then
  [[ "$FAIL_STAGE" != import ]] || exit 72
  cat >/dev/null
  exit 0
fi
if [[ "$1" == exec && "$*" == *" images tag "* ]]; then
  [[ "$FAIL_STAGE" != tag ]] || exit 74
  exit 0
fi
exit 90
`)
			writeExecutable(t, filepath.Join(fakeBin, "jq"), `#!/usr/bin/env bash
set -euo pipefail
[[ "$FAIL_STAGE" != digest ]] || exit 73
printf 'sha256:%064d\n' 0
`)

			command := exec.Command("bash", "-c", fmt.Sprintf(
				"source %q; NODE_CONTAINER=demo-node; build_oci control /unused/Dockerfile docker.io/local/control:test",
				buildScript,
			))
			command.Env = append(os.Environ(),
				"PATH="+fakeBin+string(os.PathListSeparator)+os.Getenv("PATH"),
				"OPENSHELL_DEMO_RUNTIME_DIR="+runtimeDir,
				"OPENSHELL_DEMO_REPO_ROOT="+repositoryRoot,
				"CALL_LOG="+callLog,
				"FAIL_STAGE="+stage,
			)
			output, err := command.CombinedOutput()
			if err == nil {
				t.Fatalf("build_oci unexpectedly succeeded at failed %s stage: %s", stage, output)
			}

			calls, readErr := os.ReadFile(callLog)
			if readErr != nil {
				t.Fatalf("read fake Docker call log: %v; command output: %s", readErr, output)
			}
			assertFailFastCalls(t, stage, string(calls))
		})
	}
}

func writeExecutable(t *testing.T, path, contents string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(contents), 0o755); err != nil {
		t.Fatal(err)
	}
}

func assertFailFastCalls(t *testing.T, stage, calls string) {
	t.Helper()
	if !strings.Contains(calls, "buildx build") {
		t.Fatal("build stage was not attempted")
	}
	imported := strings.Contains(calls, "exec -i demo-node ctr -n k8s.io images import")
	tagged := strings.Contains(calls, "images tag")
	switch stage {
	case "build":
		if imported || tagged {
			t.Fatalf("failed build continued to a later stage:\n%s", calls)
		}
	case "import", "digest":
		if !imported || tagged {
			t.Fatalf("failed %s stage did not stop before tagging:\n%s", stage, calls)
		}
	case "tag":
		if !imported || !tagged {
			t.Fatalf("tag failure did not exercise preceding stages:\n%s", calls)
		}
	}
}
