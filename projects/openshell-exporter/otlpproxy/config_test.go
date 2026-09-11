// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package otlpproxy_test

import (
	"os"
	"sort"
	"strings"
	"testing"

	"go.yaml.in/yaml/v3"
)

type builderManifest struct {
	Receivers  []module `yaml:"receivers"`
	Processors []module `yaml:"processors"`
	Exporters  []module `yaml:"exporters"`
	Extensions []module `yaml:"extensions"`
	Providers  []module `yaml:"providers"`
}

type module struct {
	GoMod  string `yaml:"gomod"`
	Import string `yaml:"import"`
	Name   string `yaml:"name"`
	Path   string `yaml:"path"`
}

func TestMinimalDistributionAllowList(t *testing.T) {
	raw, err := os.ReadFile("builder-config.yaml")
	if err != nil {
		t.Fatal(err)
	}

	var manifest builderManifest
	if err := yaml.Unmarshal(raw, &manifest); err != nil {
		t.Fatalf("parse builder manifest: %v", err)
	}

	version, err := os.ReadFile("../VERSION")
	if err != nil {
		t.Fatal(err)
	}
	rootModule := "github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter v" + strings.TrimSpace(string(version))
	assertModuleSurface(t, "receivers", manifest.Receivers, []string{
		"go.opentelemetry.io/collector/receiver/otlpreceiver v0.160.0",
	})
	assertModuleSurface(t, "processors", manifest.Processors, []string{
		"go.opentelemetry.io/collector/processor/memorylimiterprocessor v0.160.0",
	})
	assertModuleSurface(t, "exporters", manifest.Exporters, []string{
		"go.opentelemetry.io/collector/exporter/otlphttpexporter v0.160.0",
	})
	assertModuleSurface(t, "extensions", manifest.Extensions, []string{
		"github.com/open-telemetry/opentelemetry-collector-contrib/extension/bearertokenauthextension v0.160.0",
		"github.com/open-telemetry/opentelemetry-collector-contrib/extension/healthcheckextension v0.160.0",
		"github.com/open-telemetry/opentelemetry-collector-contrib/extension/storage/filestorage v0.160.0",
		rootModule + "|github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter/extension/storagehealthextension|storagehealthextension|.",
	})
	assertModuleSurface(t, "providers", manifest.Providers, []string{
		"go.opentelemetry.io/collector/confmap/provider/envprovider v1.66.0",
		"go.opentelemetry.io/collector/confmap/provider/fileprovider v1.66.0",
		"go.opentelemetry.io/collector/confmap/provider/yamlprovider v1.66.0",
	})
}

func assertModuleSurface(t *testing.T, section string, got []module, want []string) {
	t.Helper()
	gotKeys := make([]string, 0, len(got))
	for _, component := range got {
		if component.GoMod == "" || !strings.Contains(component.GoMod, " v") {
			t.Fatalf("%s contains an unpinned component %q", section, component.GoMod)
		}
		key := component.GoMod
		if component.Import != "" || component.Name != "" || component.Path != "" {
			key += "|" + component.Import + "|" + component.Name + "|" + component.Path
		}
		gotKeys = append(gotKeys, key)
	}
	sort.Strings(gotKeys)
	sort.Strings(want)
	if strings.Join(gotKeys, "\n") != strings.Join(want, "\n") {
		t.Fatalf("%s component surface changed without review\ngot:\n%s\nwant:\n%s", section, strings.Join(gotKeys, "\n"), strings.Join(want, "\n"))
	}
}

func TestRuntimeSecurityContract(t *testing.T) {
	canonical, err := os.ReadFile("config.yaml")
	if err != nil {
		t.Fatal(err)
	}
	var parsed map[string]any
	if err := yaml.Unmarshal(canonical, &parsed); err != nil {
		t.Fatalf("parse runtime config: %v", err)
	}

	text := string(canonical)
	required := []string{
		"endpoint: 127.0.0.1:4318",
		"filename: /run/secrets/relay-auth/token",
		"cert_file: /run/secrets/relay/tls.crt",
		"key_file: /run/secrets/relay/tls.key",
		"min_version: \"1.3\"",
		"storage: file_storage/relay_queue",
		"queue_size: 2048",
		"block_on_overflow: true",
		"batch:",
		"otlp_http/exporter",
		"storagehealth:",
		"otlp_http_queue: /var/lib/otelcol",
		"storagehealth, health_check",
	}
	for _, value := range required {
		if !strings.Contains(text, value) {
			t.Errorf("runtime config is missing %q", value)
		}
	}
	forbidden := []string{"0.0.0.0:4318", "insecure: true", "token: ${env:"}
	for _, value := range forbidden {
		if strings.Contains(text, value) {
			t.Errorf("runtime config contains forbidden value %q", value)
		}
	}

	if _, err := os.Stat("Dockerfile"); err != nil {
		t.Fatalf("first-class proxy Dockerfile: %v", err)
	}
}
