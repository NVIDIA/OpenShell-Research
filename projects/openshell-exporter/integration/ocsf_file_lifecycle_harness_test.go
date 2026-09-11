// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package integration

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestOCSFFileLifecyclePrepareProducesCandidateBoundManifest(t *testing.T) {
	if _, err := exec.LookPath("jq"); err != nil {
		t.Skip("jq is required to exercise the executable qualification harness")
	}
	root := t.TempDir()
	evidence := filepath.Join(root, "evidence")
	source := filepath.Join(root, "source")
	checkpoints := filepath.Join(root, "checkpoints")
	queue := filepath.Join(root, "queue")
	recovery := filepath.Join(root, "recovery")
	hooks := filepath.Join(root, "hooks")
	fakeBin := filepath.Join(root, "bin")
	for _, directory := range []string{evidence, source, checkpoints, queue, recovery, hooks, fakeBin} {
		if err := os.Mkdir(directory, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(source, "openshell-ocsf.2026-08-30.log"), []byte(`{"class_uid":4001,"sandbox_id":"sandbox-a"}`+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(source, 0o500); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chmod(source, 0o700) })
	config := filepath.Join(root, "exporter.yaml")
	if err := os.WriteFile(config, []byte("processors:\n  openshell/ocsf:\n    source_instance: gateway-ocsf-jsonl\nservice: {}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	writeExecutable := func(path, body string) {
		t.Helper()
		if err := os.WriteFile(path, []byte("#!/bin/sh\n"+body+"\n"), 0o700); err != nil {
			t.Fatal(err)
		}
	}
	for _, name := range []string{"outage", "restore", "activity", "restart"} {
		writeExecutable(filepath.Join(hooks, name), "exit 0")
	}
	writeExecutable(filepath.Join(hooks, "identity"), "printf 'instance-a\\n'")
	writeExecutable(filepath.Join(fakeBin, "git"), `
case "$1 $2" in
  "status --porcelain") exit 0 ;;
  "rev-parse HEAD") printf '%s\n' aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ;;
  *) exit 2 ;;
esac`)
	writeExecutable(filepath.Join(fakeBin, "findmnt"), "printf 'ro,relatime\n'")

	metrics := strings.Join([]string{
		`otelcol_exporter_queue_size{exporter="cloudevents"} 0`,
		`otelcol_exporter_queue_capacity{exporter="cloudevents"} 100`,
		"",
	}, "\n")
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.URL.Path == "/metrics" {
			_, _ = response.Write([]byte(metrics))
			return
		}
		response.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	command := exec.Command("sh", "qualification/qualify-ocsf-file-lifecycle.sh", "prepare")
	command.Env = append(os.Environ(),
		"PATH="+fakeBin+string(os.PathListSeparator)+os.Getenv("PATH"),
		"OCSF_LIFECYCLE_RUN_ID=smoke",
		"OCSF_LIFECYCLE_EVIDENCE_DIR="+evidence,
		"OCSF_LIFECYCLE_SOURCE_DIR="+source,
		"OCSF_LIFECYCLE_GATEWAY_ID=gateway",
		"OCSF_LIFECYCLE_WORKSPACE=default",
		"OCSF_LIFECYCLE_SOURCE_INSTANCE=gateway-ocsf-jsonl",
		"OCSF_LIFECYCLE_EXPORTER_IMAGE=registry/exporter@sha256:"+strings.Repeat("b", 64),
		"OCSF_LIFECYCLE_GATEWAY_IMAGE=registry/gateway@sha256:"+strings.Repeat("c", 64),
		"OCSF_LIFECYCLE_GATEWAY_VERSION=0.0.113",
		"OCSF_LIFECYCLE_CONFIG_FILE="+config,
		"OCSF_LIFECYCLE_CHECKPOINT_DIR="+checkpoints,
		"OCSF_LIFECYCLE_QUEUE_DIR="+queue,
		"OCSF_LIFECYCLE_RECOVERY_DIR="+recovery,
		"OCSF_LIFECYCLE_DESTINATION_OUTAGE_HOOK="+filepath.Join(hooks, "outage"),
		"OCSF_LIFECYCLE_DESTINATION_RESTORE_HOOK="+filepath.Join(hooks, "restore"),
		"OCSF_LIFECYCLE_ACTIVITY_HOOK="+filepath.Join(hooks, "activity"),
		"OCSF_LIFECYCLE_EXPORTER_RESTART_HOOK="+filepath.Join(hooks, "restart"),
		"OCSF_LIFECYCLE_EXPORTER_IDENTITY_HOOK="+filepath.Join(hooks, "identity"),
		"OCSF_LIFECYCLE_METRICS_URL="+server.URL+"/metrics",
		"OCSF_LIFECYCLE_HEALTH_URL="+server.URL+"/health",
	)
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("prepare failed: %v\n%s", err, output)
	}
	runDirectory := filepath.Join(evidence, "smoke")
	for _, name := range []string{"metadata.json", "prepared.json", "source-before.json", "metrics.json", "deliveries.jsonl"} {
		if _, err := os.Stat(filepath.Join(runDirectory, name)); err != nil {
			t.Fatalf("prepare did not create %s: %v", name, err)
		}
	}
	metricsEncoded, err := os.ReadFile(filepath.Join(runDirectory, "metrics.json"))
	if err != nil {
		t.Fatal(err)
	}
	var metricPoints map[string]struct {
		RetryableFailures float64 `json:"retryable_failures"`
		DeliveredEvents   float64 `json:"delivered_events"`
	}
	if err := json.Unmarshal(metricsEncoded, &metricPoints); err != nil {
		t.Fatal(err)
	}
	baseline := metricPoints["baseline"]
	if baseline.RetryableFailures != 0 || baseline.DeliveredEvents != 0 {
		t.Fatalf("absent counters were not normalized to zero: %#v", baseline)
	}
	encoded, err := os.ReadFile(filepath.Join(runDirectory, "source-before.json"))
	if err != nil {
		t.Fatal(err)
	}
	var manifest struct {
		TotalRecords int64 `json:"total_records"`
		Files        []struct {
			Device  uint64 `json:"device"`
			Inode   uint64 `json:"inode"`
			Records []struct {
				Offset int64  `json:"record_offset"`
				Hash   string `json:"body_sha256"`
				ID     string `json:"id"`
			} `json:"records"`
		} `json:"files"`
	}
	if err := json.Unmarshal(encoded, &manifest); err != nil {
		t.Fatal(err)
	}
	if manifest.TotalRecords != 1 || len(manifest.Files) != 1 || manifest.Files[0].Device == 0 || manifest.Files[0].Inode == 0 || len(manifest.Files[0].Records) != 1 {
		t.Fatalf("manifest lacks source identity: %#v", manifest)
	}
	record := manifest.Files[0].Records[0]
	if record.Offset != 0 || !strings.HasPrefix(record.Hash, "sha256:") || !strings.HasPrefix(record.ID, "sha256:") {
		t.Fatalf("manifest lacks stable record provenance: %#v", record)
	}
}
