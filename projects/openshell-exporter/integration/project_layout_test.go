// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package integration

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestDemoPathsResolveInsideResearchRepository(t *testing.T) {
	parent := t.TempDir()
	if output, err := exec.Command("git", "init", "--quiet", parent).CombinedOutput(); err != nil {
		t.Fatalf("initialize parent repository: %v: %s", err, output)
	}
	project := filepath.Join(parent, "projects", "openshell-exporter")
	for _, name := range []string{
		"examples/demo/kubernetes/lib.sh",
		"examples/demo/real-gateway/control/prepare-build-context.sh",
		"examples/demo/real-gateway/control/openshell-artifacts.env",
	} {
		target := filepath.Join(project, name)
		if err := os.MkdirAll(filepath.Dir(target), 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(target, []byte(repositoryFile(t, name)), 0755); err != nil {
			t.Fatal(err)
		}
	}
	cmd := exec.Command("bash", "-c", `source "$1"; printf '%s' "$REPO_ROOT"`, "bash", filepath.Join(project, "examples/demo/kubernetes/lib.sh"))
	cmd.Env = append(os.Environ(), "OPENSHELL_DEMO_REPO_ROOT=", "OPENSHELL_DEMO_RUNTIME_DIR="+filepath.Join(parent, "runtime"))
	output, err := cmd.CombinedOutput()
	if err != nil || string(output) != project {
		t.Fatalf("Kubernetes demo resolved outside project: %v: %s", err, output)
	}
	// Exercise the real control script with a recording downloader; never contact
	// registries or create a gateway just to verify the build context.
	if err := os.MkdirAll(filepath.Join(project, "scripts"), 0755); err != nil {
		t.Fatal(err)
	}
	downloader := "#!/usr/bin/env bash\nprintf '%s\\n' \"$3\"\n"
	if err := os.WriteFile(filepath.Join(project, "scripts/download-pinned-artifact.sh"), []byte(downloader), 0755); err != nil {
		t.Fatal(err)
	}
	cmd = exec.Command("bash", filepath.Join(project, "examples/demo/real-gateway/control/prepare-build-context.sh"))
	cmd.Dir = parent
	output, err = cmd.CombinedOutput()
	if err != nil || strings.Count(string(output), project+"/examples/demo/real-gateway/control/../runtime/downloads/") != 2 {
		t.Fatalf("control artifacts resolved outside project: %v: %s", err, output)
	}
}

func TestResearchReleaseTagMatchesNestedGoModule(t *testing.T) {
	version := strings.TrimSpace(repositoryFile(t, "VERSION"))
	root, err := filepath.Abs("..")
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(filepath.Join(root, "scripts/check-version.sh"), "projects/openshell-exporter/v"+version)
	cmd.Dir = t.TempDir()
	if output, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("nested release tag rejected: %v: %s", err, output)
	}
}
