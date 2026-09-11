// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestCompareReportsBoundedAPIDriftAndComponents(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	pinned := filepath.Join(root, "pinned")
	candidate := filepath.Join(root, "candidate")
	writeFixture(t, filepath.Join(root, "exporter", "go.mod"), "module example\nrequire github.com/NVIDIA/OpenShell/sdk/go v0.0.0-pinned\n")
	writeFixture(t, filepath.Join(pinned, "proto", "openshell.proto"), `
service OpenShell {
  rpc ListSandboxes (ListSandboxesRequest) returns (ListSandboxesResponse);
  rpc WatchSandbox (WatchSandboxRequest) returns (stream SandboxStreamEvent);
}
message SandboxStreamEvent {
  string sandbox_id = 1;
}
`)
	writeFixture(t, filepath.Join(candidate, "proto", "openshell.proto"), `
service OpenShell {
  rpc ListSandboxes (ListSandboxesRequest) returns (ListSandboxesResponse);
  rpc WatchSandbox (WatchSandboxRequest) returns (stream SandboxStreamEvent);
  rpc WatchEvents (WatchEventsRequest) returns (stream GatewayEvent);
}
message SandboxStreamEvent {
  string sandbox_id = 1;
  string resume_cursor = 2;
}
`)
	for _, source := range []string{pinned, candidate} {
		writeFixture(t, filepath.Join(source, "deploy", "helm", "openshell", "Chart.yaml"), "version: 0.0.113\n")
		writeFixture(t, filepath.Join(source, "Cargo.toml"), "[workspace.package]\nversion = \"0.0.113\"\n")
		writeFixture(t, filepath.Join(source, "crates", "openshell-ocsf", "schemas", "ocsf", "v1.8.0", "VERSION"), "1.8.0\n")
	}

	report, err := compare(options{
		root: filepath.Join(root, "exporter"), pinnedVersion: "v0.0.113",
		generatedAt: "2026-08-26T00:00:00Z", skipRegistry: true,
	}, githubRelease{TagName: "v0.0.114", HTMLURL: "https://example.test/release"}, pinned, candidate)
	if err != nil {
		t.Fatal(err)
	}
	if !report.ReviewRequired || !report.ReleaseChanged {
		t.Fatalf("expected release review, got %#v", report)
	}
	if report.PublicAPI.RelevantRPCs["WatchEvents"] != "present" {
		t.Fatalf("WatchEvents status = %q", report.PublicAPI.RelevantRPCs["WatchEvents"])
	}
	joined := strings.Join(report.PublicAPI.Added, "\n")
	for _, expected := range []string{"rpc OpenShell.WatchEvents", "field SandboxStreamEvent.resume_cursor"} {
		if !strings.Contains(joined, expected) {
			t.Fatalf("added symbols missing %q: %s", expected, joined)
		}
	}
	if report.Components["gateway"].Candidate != "v0.0.114" || report.Components["ocsf"].Changed {
		t.Fatalf("unexpected components: %#v", report.Components)
	}
}

func TestPinnedVersionUsesCompatibilityContract(t *testing.T) {
	t.Parallel()
	path := filepath.Join(t.TempDir(), "compatibility.md")
	writeFixture(t, path, "| Component | Version |\n|---|---|\n| OpenShell gateway | `v0.0.113` | qualified |\n")
	got, err := pinnedVersion(path)
	if err != nil {
		t.Fatal(err)
	}
	if got != "v0.0.113" {
		t.Fatalf("pinned version = %q", got)
	}
}

func TestCompareInventoriesKeepsWatchEventsUnavailableVisible(t *testing.T) {
	t.Parallel()
	api := compareInventories(
		[]string{"rpc OpenShell.WatchSandbox (Request) returns (Event)"},
		[]string{"rpc OpenShell.WatchSandbox (Request) returns (Event)"},
	)
	if api.RelevantRPCs["WatchEvents"] != "absent" || len(api.Added) != 0 || len(api.Removed) != 0 {
		t.Fatalf("unexpected API report: %#v", api)
	}
}

func writeFixture(t *testing.T, path, body string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
}
