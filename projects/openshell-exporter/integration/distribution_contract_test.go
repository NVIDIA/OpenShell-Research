// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package integration

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"

	"go.yaml.in/yaml/v3"
)

type distributionComponent struct {
	GoMod  string `yaml:"gomod"`
	Import string `yaml:"import"`
	Name   string `yaml:"name"`
	Path   string `yaml:"path"`
}

type distributionManifest struct {
	Dist struct {
		Version string `yaml:"version"`
	} `yaml:"dist"`
	Receivers  []distributionComponent `yaml:"receivers"`
	Processors []distributionComponent `yaml:"processors"`
	Exporters  []distributionComponent `yaml:"exporters"`
	Extensions []distributionComponent `yaml:"extensions"`
	Providers  []distributionComponent `yaml:"providers"`
}

func repositoryFile(t *testing.T, name string) string {
	t.Helper()
	path := filepath.Join("..", filepath.Clean(name))
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return string(data)
}

func standaloneWorkflow(t *testing.T, name string) string {
	t.Helper()
	if _, err := os.Stat(filepath.Join("..", ".github")); os.IsNotExist(err) {
		t.Skip("standalone workflows are excluded from the Research project; shared CI is tested by its owning repository")
	}
	return repositoryFile(t, name)
}

func TestCollectorDistributionHasReviewedComponentSurface(t *testing.T) {
	var manifest distributionManifest
	if err := yaml.Unmarshal([]byte(repositoryFile(t, "builder-config.yaml")), &manifest); err != nil {
		t.Fatalf("parse builder-config.yaml: %v", err)
	}

	version := strings.TrimSpace(repositoryFile(t, "VERSION"))
	if manifest.Dist.Version != version {
		t.Fatalf("distribution version = %q, VERSION = %q", manifest.Dist.Version, version)
	}
	rootModule := "github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter v" + version

	assertComponentSurface(t, "receivers", manifest.Receivers, []string{
		"github.com/open-telemetry/opentelemetry-collector-contrib/receiver/filelogreceiver v0.160.0",
		"github.com/open-telemetry/opentelemetry-collector-contrib/receiver/k8sobjectsreceiver v0.160.0",
		"go.opentelemetry.io/collector/receiver/otlpreceiver v0.160.0",
		rootModule + "|github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter/receiver/watchsandboxreceiver|watchsandboxreceiver|.",
	})
	assertComponentSurface(t, "processors", manifest.Processors, []string{
		"github.com/open-telemetry/opentelemetry-collector-contrib/processor/attributesprocessor v0.160.0",
		"github.com/open-telemetry/opentelemetry-collector-contrib/processor/resourceprocessor v0.160.0",
		"go.opentelemetry.io/collector/processor/memorylimiterprocessor v0.160.0",
		rootModule + "|github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter/processor/evidencecontractprocessor|evidencecontractprocessor|.",
		rootModule + "|github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter/processor/openshellprocessor|openshellprocessor|.",
		rootModule + "|github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter/processor/relayprocessor|relayprocessor|.",
	})
	assertComponentSurface(t, "exporters", manifest.Exporters, []string{
		"github.com/open-telemetry/opentelemetry-collector-contrib/exporter/fileexporter v0.160.0",
		"github.com/open-telemetry/opentelemetry-collector-contrib/exporter/loadbalancingexporter v0.160.0",
		"go.opentelemetry.io/collector/exporter/otlpexporter v0.160.0",
		"go.opentelemetry.io/collector/exporter/otlphttpexporter v0.160.0",
		rootModule + "|github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter/exporter/cloudeventsexporter|cloudeventsexporter|.",
	})
	assertComponentSurface(t, "extensions", manifest.Extensions, []string{
		"github.com/open-telemetry/opentelemetry-collector-contrib/extension/bearertokenauthextension v0.160.0",
		"github.com/open-telemetry/opentelemetry-collector-contrib/extension/healthcheckextension v0.160.0",
		"github.com/open-telemetry/opentelemetry-collector-contrib/extension/storage/filestorage v0.160.0",
		rootModule + "|github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter/extension/storagehealthextension|storagehealthextension|.",
	})
	assertComponentSurface(t, "providers", manifest.Providers, []string{
		"go.opentelemetry.io/collector/confmap/provider/envprovider v1.66.0",
		"go.opentelemetry.io/collector/confmap/provider/fileprovider v1.66.0",
		"go.opentelemetry.io/collector/confmap/provider/yamlprovider v1.66.0",
	})
}

func TestOTLPProxyDistributionHasReviewedComponentSurface(t *testing.T) {
	var manifest distributionManifest
	if err := yaml.Unmarshal([]byte(repositoryFile(t, "otlpproxy/builder-config.yaml")), &manifest); err != nil {
		t.Fatalf("parse otlpproxy/builder-config.yaml: %v", err)
	}

	version := strings.TrimSpace(repositoryFile(t, "VERSION"))
	if manifest.Dist.Version != version {
		t.Fatalf("proxy distribution version = %q, VERSION = %q", manifest.Dist.Version, version)
	}
	rootModule := "github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter v" + version

	assertComponentSurface(t, "proxy receivers", manifest.Receivers, []string{
		"go.opentelemetry.io/collector/receiver/otlpreceiver v0.160.0",
	})
	assertComponentSurface(t, "proxy processors", manifest.Processors, []string{
		"go.opentelemetry.io/collector/processor/memorylimiterprocessor v0.160.0",
	})
	assertComponentSurface(t, "proxy exporters", manifest.Exporters, []string{
		"go.opentelemetry.io/collector/exporter/otlphttpexporter v0.160.0",
	})
	assertComponentSurface(t, "proxy extensions", manifest.Extensions, []string{
		"github.com/open-telemetry/opentelemetry-collector-contrib/extension/bearertokenauthextension v0.160.0",
		"github.com/open-telemetry/opentelemetry-collector-contrib/extension/healthcheckextension v0.160.0",
		"github.com/open-telemetry/opentelemetry-collector-contrib/extension/storage/filestorage v0.160.0",
		rootModule + "|github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter/extension/storagehealthextension|storagehealthextension|.",
	})
	assertComponentSurface(t, "proxy providers", manifest.Providers, []string{
		"go.opentelemetry.io/collector/confmap/provider/envprovider v1.66.0",
		"go.opentelemetry.io/collector/confmap/provider/fileprovider v1.66.0",
		"go.opentelemetry.io/collector/confmap/provider/yamlprovider v1.66.0",
	})
}

func TestCIBuildValidatesEveryShippedDurableProfile(t *testing.T) {
	ci := standaloneWorkflow(t, ".github/workflows/ci.yml")
	if !strings.Contains(ci, "/var/lib/openshell-exporter/recovery /var/lib/otelcol") {
		t.Fatal("build-and-config CI does not provision the proxy persistent queue before validation")
	}
	for _, command := range []string{
		"OPENSHELL_EXPORTER_HOST=127.0.0.1 ./_build/otlp-proxy/openshell-otlp-proxy validate --config otlpproxy/config.yaml",
		"./_build/openshell-event-exporter validate --config deploy/docker/config.yaml",
		"./_build/openshell-event-exporter validate --config deploy/docker/config.full.yaml",
		"./_build/openshell-event-exporter validate --config deploy/docker/config.complete.yaml",
		"./_build/openshell-event-exporter validate --config deploy/podman/config.yaml",
		"./_build/openshell-event-exporter validate --config deploy/podman/config.full.yaml",
		"./_build/openshell-event-exporter validate --config deploy/podman/config.complete.yaml",
		"./_build/openshell-event-exporter validate --config deploy/kubernetes/chart/files/config-stream.yaml",
		"./_build/openshell-event-exporter validate --config dist/config-complete.yaml",
		"./_build/openshell-event-exporter validate --config dist/config-edge.yaml",
		"./_build/openshell-event-exporter validate --config dist/config-central.yaml",
	} {
		if !strings.Contains(ci, command) {
			t.Errorf("build-and-config CI does not validate %q", command)
		}
	}
}

func TestCICapacitySmokeEnforcesReviewedResourceBudgets(t *testing.T) {
	budgets := repositoryFile(t, "quality/budgets.env")
	for _, name := range []string{
		"CAPACITY_MAX_RSS_BYTES",
		"CAPACITY_MAX_PROCESS_WRITTEN_BYTES",
		"CAPACITY_MAX_CHECKPOINT_BYTES",
		"CAPACITY_MAX_QUEUE_BYTES",
		"CAPACITY_MAX_RECOVERY_BYTES",
	} {
		pattern := regexp.MustCompile(`(?m)^` + regexp.QuoteMeta(name) + `=[1-9][0-9]*$`)
		if !pattern.MatchString(budgets) {
			t.Errorf("quality/budgets.env does not define a positive %s", name)
		}
	}
	for _, workflow := range []string{".github/workflows/ci.yml"} {
		ci := standaloneWorkflow(t, workflow)
		var parsed any
		if err := yaml.Unmarshal([]byte(ci), &parsed); err != nil {
			t.Errorf("parse %s: %v", workflow, err)
		}
		for _, required := range []string{
			". ./quality/budgets.env",
			"RUN_CAPACITY_QUALIFICATION",
			"TestSustainedCapacityQualification",
			"CAPACITY_REPORT_PATH",
		} {
			if !strings.Contains(ci, required) {
				t.Errorf("%s capacity smoke does not enforce %q", workflow, required)
			}
		}
	}
}

func TestSourceReleaseKeepsImageVerificationWithoutPublishingImages(t *testing.T) {
	githubCI := standaloneWorkflow(t, ".github/workflows/ci.yml")
	for _, required := range []string{
		"dist/exporter-amd64-trivy.json",
		"dist/exporter-arm64-trivy.json",
		"dist/otlp-proxy-amd64-trivy.json",
		"dist/otlp-proxy-arm64-trivy.json",
		"go run ./cmd/security-artifact-verifier --directory dist --version \"$(./scripts/check-version.sh)\" --revision \"$GITHUB_SHA\"",
	} {
		if !strings.Contains(githubCI, required) {
			t.Errorf("GitHub CI evidence contract is missing %q", required)
		}
	}

	releaseWorkflow := standaloneWorkflow(t, ".github/workflows/release.yml")
	var parsedReleaseWorkflow any
	if err := yaml.Unmarshal([]byte(releaseWorkflow), &parsedReleaseWorkflow); err != nil {
		t.Fatalf("parse GitHub release workflow: %v", err)
	}
	for _, required := range []string{"contents: write", "./scripts/check-version.sh", "gh release create", "--notes-file"} {
		if !strings.Contains(releaseWorkflow, required) {
			t.Errorf("source release workflow is missing %q", required)
		}
	}
	for _, forbidden := range []string{"packages: write", "docker login", "docker push", "--push", "ghcr.io/nvidia-dev/"} {
		if strings.Contains(releaseWorkflow, forbidden) || strings.Contains(githubCI, forbidden) {
			t.Errorf("repository CI must not publish container images: %q", forbidden)
		}
	}
}

func TestCIDependencyGraphEnforcesReviewedFootprint(t *testing.T) {
	budgets := repositoryFile(t, "quality/budgets.env")
	for _, name := range []string{"GO_MODULE_GRAPH_MAX_COUNT", "GO_DIRECT_MODULE_MAX_COUNT"} {
		pattern := regexp.MustCompile(`(?m)^` + regexp.QuoteMeta(name) + `=[1-9][0-9]*$`)
		if !pattern.MatchString(budgets) {
			t.Errorf("quality/budgets.env does not define a positive %s", name)
		}
	}
	for _, workflow := range []string{".github/workflows/ci.yml"} {
		ci := standaloneWorkflow(t, workflow)
		for _, required := range []string{
			"go list -m -f",
			"./scripts/check-quality-budgets.sh dependencies dist/modules.txt",
		} {
			if !strings.Contains(ci, required) {
				t.Errorf("%s dependency budget does not enforce %q", workflow, required)
			}
		}
	}
}

func assertComponentSurface(t *testing.T, section string, got []distributionComponent, want []string) {
	t.Helper()
	gotModules := make([]string, 0, len(got))
	for _, component := range got {
		if component.GoMod == "" || !strings.Contains(component.GoMod, " v") {
			t.Fatalf("%s contains an unversioned component %q", section, component.GoMod)
		}
		key := component.GoMod
		if component.Import != "" || component.Name != "" || component.Path != "" {
			key += "|" + component.Import + "|" + component.Name + "|" + component.Path
		}
		gotModules = append(gotModules, key)
	}
	sort.Strings(gotModules)
	sort.Strings(want)
	if strings.Join(gotModules, "\n") != strings.Join(want, "\n") {
		t.Fatalf("%s component surface changed without contract review\ngot:\n%s\nwant:\n%s", section, strings.Join(gotModules, "\n"), strings.Join(want, "\n"))
	}
}

func TestExporterFinalImageHasMinimalPinnedRuntimeSurface(t *testing.T) {
	dockerfile := repositoryFile(t, "Dockerfile")
	assertLocalCollectorSourcesCopied(t, dockerfile)
	for name, pattern := range map[string]string{
		"BuildKit frontend": `(?m)^# syntax=docker/dockerfile:[^@\s]+@sha256:[0-9a-f]{64}$`,
		"Go builder":        `(?m)^FROM --platform=\$BUILDPLATFORM docker\.io/library/golang:1\.26\.8-bookworm@sha256:[0-9a-f]{64} AS builder$`,
		"runtime":           `(?m)^FROM gcr\.io/distroless/static-debian12:nonroot@sha256:[0-9a-f]{64}$`,
	} {
		if !regexp.MustCompile(pattern).MatchString(dockerfile) {
			t.Errorf("Dockerfile does not pin the %s by digest", name)
		}
	}

	finalFrom := strings.LastIndex(dockerfile, "\nFROM ")
	if finalFrom < 0 {
		t.Fatal("Dockerfile has no final runtime stage")
	}
	runtime := dockerfile[finalFrom+1:]
	for _, required := range []string{
		"COPY --from=builder /src/_build/openshell-event-exporter /usr/local/bin/openshell-event-exporter",
		"COPY --from=builder /out/healthcheck /usr/local/bin/healthcheck",
		"COPY --from=builder /out/sandbox-injector /usr/local/bin/sandbox-injector",
		"USER nonroot:nonroot",
		`HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD ["/usr/local/bin/healthcheck"]`,
		`ENTRYPOINT ["/usr/local/bin/openshell-event-exporter"]`,
	} {
		if !strings.Contains(runtime, required) {
			t.Errorf("final image contract is missing %q", required)
		}
	}
	if got := strings.Count(runtime, "COPY --from=builder "); got != 3 {
		t.Errorf("final image copies %d artifacts, want exactly 3", got)
	}
	for _, forbidden := range []string{"\nRUN ", "\nADD ", "apt-get", "apk add", "/bin/sh", "curl ", "wget "} {
		if strings.Contains(runtime, forbidden) {
			t.Errorf("final image contains forbidden runtime surface %q", forbidden)
		}
	}
}

func TestOTLPProxyFinalImageHasMinimalPinnedRuntimeSurface(t *testing.T) {
	dockerfile := repositoryFile(t, "otlpproxy/Dockerfile")
	assertLocalCollectorSourcesCopied(t, dockerfile)
	for name, pattern := range map[string]string{
		"BuildKit frontend": `(?m)^# syntax=docker/dockerfile:[^@\s]+@sha256:[0-9a-f]{64}$`,
		"Go builder":        `(?m)^FROM --platform=\$BUILDPLATFORM docker\.io/library/golang:1\.26\.8-bookworm@sha256:[0-9a-f]{64} AS builder$`,
		"runtime":           `(?m)^FROM gcr\.io/distroless/static-debian12:nonroot@sha256:[0-9a-f]{64}$`,
	} {
		if !regexp.MustCompile(pattern).MatchString(dockerfile) {
			t.Errorf("proxy Dockerfile does not pin the %s by digest", name)
		}
	}

	finalFrom := strings.LastIndex(dockerfile, "\nFROM ")
	if finalFrom < 0 {
		t.Fatal("proxy Dockerfile has no final runtime stage")
	}
	runtime := dockerfile[finalFrom+1:]
	for _, required := range []string{
		"COPY --from=builder /src/_build/otlp-proxy/openshell-otlp-proxy /usr/local/bin/openshell-otlp-proxy",
		"USER nonroot:nonroot",
		"EXPOSE 13134 8889 4318",
		`ENTRYPOINT ["/usr/local/bin/openshell-otlp-proxy"]`,
		`CMD ["--config", "/etc/openshell-otlp-proxy/config.yaml"]`,
	} {
		if !strings.Contains(runtime, required) {
			t.Errorf("proxy final image contract is missing %q", required)
		}
	}
	if got := strings.Count(runtime, "COPY --from=builder "); got != 1 {
		t.Errorf("proxy final image copies %d artifacts, want exactly 1", got)
	}
	for _, forbidden := range []string{"\nRUN ", "\nADD ", "apt-get", "apk add", "/bin/sh", "curl ", "wget "} {
		if strings.Contains(runtime, forbidden) {
			t.Errorf("proxy final image contains forbidden runtime surface %q", forbidden)
		}
	}
}

func assertLocalCollectorSourcesCopied(t *testing.T, dockerfile string) {
	t.Helper()
	if !strings.Contains(dockerfile, "COPY extension ./extension") {
		t.Fatal("Dockerfile must copy local Collector extension sources into the build context")
	}
}
