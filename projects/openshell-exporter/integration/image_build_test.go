// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package integration

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

const imageEngineStub = `#!/usr/bin/env bash
set -eu
printf '<%s>' "$@" >> "$IMAGE_CALL_LOG"
printf '\n' >> "$IMAGE_CALL_LOG"
case "$1" in
  image) [[ ${IMAGE_MISSING:-false} != true ]]; printf 'sha256:%064d\n' 1 ;;
  pull) [[ ${PULL_FAIL:-false} != true ]] ;;
  build|buildx)
    [[ ${BUILD_FAIL:-false} != true ]] || exit 9
    while (($#)); do
      if [[ $1 == --metadata-file ]]; then
        printf '{"containerimage.digest":"sha256:%064d"}\n' 1 > "$2"
        break
      fi
      shift
    done ;;
  push)
    [[ ${PUSH_FAIL:-false} != true ]] || exit 10
    printf 'sha256:%064d\n' 1 > "$3" ;;
esac
`

func runImageScript(t *testing.T, script string, environment []string, args ...string) (string, string, error) {
	t.Helper()
	root, err := filepath.Abs("..")
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	for _, engine := range []string{"docker", "podman"} {
		if err := os.WriteFile(filepath.Join(dir, engine), []byte(imageEngineStub), 0700); err != nil {
			t.Fatal(err)
		}
	}
	log := filepath.Join(dir, "calls")
	cmd := exec.Command("bash", append([]string{filepath.Join(root, "scripts", script)}, args...)...)
	cmd.Dir = dir // The build context must not depend on the caller's directory.
	cmd.Env = append(os.Environ(), "PATH="+dir+":"+os.Getenv("PATH"), "IMAGE_CALL_LOG="+log)
	cmd.Env = append(cmd.Env, environment...)
	output, err := cmd.CombinedOutput()
	calls, _ := os.ReadFile(log)
	return string(output), string(calls), err
}

func TestSourceImageBuildsStayLocalByDefault(t *testing.T) {
	for _, engine := range []string{"docker", "podman"} {
		t.Run(engine, func(t *testing.T) {
			output, calls, err := runImageScript(t, "build-images.sh", nil, "all", "--engine", engine, "--platform", "linux/arm64")
			if err != nil {
				t.Fatalf("build: %v\n%s", err, output)
			}
			for _, expected := range []string{"<--platform><linux/arm64>", "localhost/openshell-event-exporter:v", "localhost/openshell-otlp-proxy:v", "<--build-arg><VERSION=", "<--build-arg><VCS_REF="} {
				if !strings.Contains(calls, expected) {
					t.Errorf("missing %q in calls: %s", expected, calls)
				}
			}
			root, _ := filepath.Abs("..")
			if !strings.Contains(calls, "<"+root+">") || !strings.Contains(calls, "<"+filepath.Join(root, "otlpproxy", "Dockerfile")+">") {
				t.Fatalf("wrong project context: %s", calls)
			}
			if strings.Contains(calls, "<--push>") || strings.Contains(calls, "<push>") || strings.Contains(calls, "nvidia-dev") {
				t.Fatalf("local build attempted publication: %s", calls)
			}
			if engine == "docker" && strings.Count(calls, "<--load>") != 2 {
				t.Fatalf("Docker must load both images: %s", calls)
			}
		})
	}
}

func TestImagePushRequiresExplicitDestination(t *testing.T) {
	for _, args := range [][]string{
		{"--push"},
		{"--push", "--registry", "implicit-dockerhub-user"},
		{"--platform", "linux/amd64,linux/arm64"},
	} {
		output, calls, err := runImageScript(t, "build-images.sh", nil, args...)
		if err == nil || calls != "" {
			t.Fatalf("unsafe arguments reached engine: args=%v err=%v calls=%s output=%s", args, err, calls, output)
		}
	}
}

func TestImagePushUsesOnlyUserRegistryAndStopsOnBuildFailure(t *testing.T) {
	if _, err := exec.LookPath("jq"); err != nil {
		t.Skip("jq is required for push metadata")
	}
	for _, engine := range []string{"docker", "podman"} {
		args := []string{"proxy", "--engine", engine, "--registry", "registry.example.org:5000/team", "--push"}
		output, calls, err := runImageScript(t, "build-images.sh", nil, args...)
		if err != nil || !strings.Contains(output, "Registry image: registry.example.org:5000/team/openshell-otlp-proxy@sha256:") {
			t.Fatalf("push metadata: %v\n%s\n%s", err, output, calls)
		}
		output, calls, err = runImageScript(t, "build-images.sh", []string{"BUILD_FAIL=true"}, args...)
		if err == nil || strings.Contains(output, "Registry image:") || (engine == "podman" && strings.Contains(calls, "<push>")) {
			t.Fatalf("failed build reported publication: %v\n%s\n%s", err, output, calls)
		}
	}
}

func TestDeploymentImagePullPolicy(t *testing.T) {
	digest := "registry.example.org/team/exporter@sha256:" + strings.Repeat("a", 64)
	for _, engine := range []string{"docker", "podman"} {
		for _, test := range []struct {
			name, ref, policy   string
			missing, failPull   bool
			wantError, wantPull bool
		}{
			{name: "local-present", ref: "localhost/exporter:v1", policy: "never"},
			{name: "local-missing", ref: "localhost/exporter:v1", policy: "never", missing: true, wantError: true},
			{name: "local-no-tag", ref: "localhost/exporter", policy: "never", wantError: true},
			{name: "remote-no-digest", ref: "registry.example.org/exporter:v1", policy: "always", wantError: true},
			{name: "remote-cached", ref: digest, policy: "missing"},
			{name: "remote-missing", ref: digest, policy: "missing", missing: true, wantPull: true},
			{name: "remote-always", ref: digest, policy: "always", wantPull: true},
			{name: "remote-failed", ref: digest, policy: "always", failPull: true, wantError: true, wantPull: true},
			{name: "invalid-policy", ref: digest, policy: "sometimes", wantError: true},
		} {
			t.Run(engine+"/"+test.name, func(t *testing.T) {
				env := []string{}
				if test.missing {
					env = append(env, "IMAGE_MISSING=true")
				}
				if test.failPull {
					env = append(env, "PULL_FAIL=true")
				}
				output, calls, err := runImageScript(t, "prepare-image.sh", env, engine, test.ref, test.policy)
				if (err != nil) != test.wantError || strings.Contains(calls, "<pull>") != test.wantPull {
					t.Fatalf("policy mismatch: error=%v calls=%s output=%s", err, calls, output)
				}
			})
		}
	}
}

func TestHelmImageModes(t *testing.T) {
	var schema map[string]any
	if err := json.Unmarshal([]byte(readFile(t, "kubernetes/chart/values.schema.json")), &schema); err != nil {
		t.Fatal(err)
	}
	imageSchema := schema["properties"].(map[string]any)["image"]
	compiler := jsonschema.NewCompiler()
	if err := compiler.AddResource("image.json", imageSchema); err != nil {
		t.Fatal(err)
	}
	compiled, err := compiler.Compile("image.json")
	if err != nil {
		t.Fatal(err)
	}
	for _, test := range []struct {
		name                string
		local               bool
		tag, digest, policy string
		valid               bool
	}{
		{"local", true, "v0.0.4", "", "Never", true},
		{"local-pulls", true, "v0.0.4", "", "Always", false},
		{"local-missing-tag", true, "", "", "Never", false},
		{"local-latest", true, "latest", "", "Never", false},
		{"local-digest-conflict", true, "v0.0.4", "sha256:" + strings.Repeat("a", 64), "Never", false},
		{"registry", false, "", "sha256:" + strings.Repeat("a", 64), "IfNotPresent", true},
		{"registry-missing-digest", false, "v0.0.4", "", "IfNotPresent", false},
	} {
		t.Run(test.name, func(t *testing.T) {
			value := map[string]any{"repository": "localhost/exporter", "local": test.local, "tag": test.tag, "digest": test.digest, "pullPolicy": test.policy}
			if err := compiled.Validate(value); (err == nil) != test.valid {
				t.Fatalf("validation=%v; want valid=%t", err, test.valid)
			}
		})
	}
}
