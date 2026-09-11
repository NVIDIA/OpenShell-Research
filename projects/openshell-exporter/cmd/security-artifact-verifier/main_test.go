// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"archive/tar"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const testRevision = "0123456789abcdef0123456789abcdef01234567"

func TestValidateSPDXRejectsIncompleteOrMislabeledEvidence(t *testing.T) {
	document := validSPDX()
	path := writeJSONFixture(t, "exporter-amd64.spdx.json", document)
	if _, _, err := validateSPDX(path, "exporter-amd64.oci", exporterDistributionModule); err != nil {
		t.Fatalf("valid SPDX rejected: %v", err)
	}
	proxyDocument := validSPDXForModule(proxyDistributionModule)
	proxyPath := writeJSONFixture(t, "otlp-proxy-amd64.spdx.json", proxyDocument)
	if _, _, err := validateSPDX(proxyPath, "exporter-amd64.oci", proxyDistributionModule); err != nil {
		t.Fatalf("valid proxy SPDX rejected: %v", err)
	}

	tests := map[string]func(*spdxDocument){
		"wrong version":               func(value *spdxDocument) { value.SPDXVersion = "SPDX-2.2" },
		"wrong archive":               func(value *spdxDocument) { value.Name = "dist/other.oci" },
		"no Syft creator":             func(value *spdxDocument) { value.CreationInfo.Creators = []string{"Tool: other-1.0"} },
		"no packages":                 func(value *spdxDocument) { value.Packages = nil },
		"duplicate package ID":        func(value *spdxDocument) { value.Packages[1].SPDXID = value.Packages[0].SPDXID },
		"missing distribution module": func(value *spdxDocument) { value.Packages[0].Name = "example.com/other" },
		"wrong distribution PURL": func(value *spdxDocument) {
			value.Packages[0].ExternalRef[0].Locator = "pkg:golang/" + rootModule + "#other"
		},
		"no package URL":         func(value *spdxDocument) { value.Packages[0].ExternalRef = nil },
		"malformed relationship": func(value *spdxDocument) { value.Relationships[0].RelatedElement = "not-an-spdx-id" },
		"missing document root":  func(value *spdxDocument) { value.Relationships[0].Type = "CONTAINS" },
	}
	for name, mutate := range tests {
		t.Run(name, func(t *testing.T) {
			candidate := validSPDX()
			mutate(&candidate)
			candidatePath := writeJSONFixture(t, "candidate.json", candidate)
			if _, _, err := validateSPDX(candidatePath, "exporter-amd64.oci", exporterDistributionModule); err == nil {
				t.Fatal("invalid SPDX evidence was accepted")
			}
		})
	}

	empty := writeRawFixture(t, "empty.json", "{}\n")
	if _, _, err := validateSPDX(empty, "exporter-amd64.oci", exporterDistributionModule); err == nil {
		t.Fatal("empty SPDX evidence was accepted")
	}
	trailing := writeRawFixture(t, "trailing.json", string(mustJSON(t, validSPDX()))+"{}")
	if _, _, err := validateSPDX(trailing, "exporter-amd64.oci", exporterDistributionModule); err == nil {
		t.Fatal("trailing SPDX JSON was accepted")
	}
}

func TestValidateTrivyRejectsUnsafeOrUnboundEvidence(t *testing.T) {
	report := validTrivy()
	identity := identityFromTrivy(report)
	path := writeJSONFixture(t, "exporter-amd64-trivy.json", report)
	if _, err := validateTrivy(path, "amd64", "exporter-amd64.oci", "usr/local/bin/openshell-event-exporter", "0.0.3", testRevision, identity); err != nil {
		t.Fatalf("valid Trivy report rejected: %v", err)
	}

	tests := map[string]func(*trivyReport){
		"wrong schema":   func(value *trivyReport) { value.SchemaVersion = 1 },
		"wrong artifact": func(value *trivyReport) { value.ArtifactName = "dist/other-layout" },
		"wrong image ID": func(value *trivyReport) { value.Metadata.ImageID = "sha256:" + strings.Repeat("c", 64) },
		"wrong layer identity": func(value *trivyReport) {
			value.Metadata.DiffIDs[0] = "sha256:" + strings.Repeat("c", 64)
		},
		"wrong architecture": func(value *trivyReport) { value.Metadata.ImageConfig.Architecture = "arm64" },
		"root user":          func(value *trivyReport) { value.Metadata.ImageConfig.Config.User = "0:0" },
		"root named user":    func(value *trivyReport) { value.Metadata.ImageConfig.Config.User = "root:nonroot" },
		"root numeric user":  func(value *trivyReport) { value.Metadata.ImageConfig.Config.User = "0:65532" },
		"root padded user":   func(value *trivyReport) { value.Metadata.ImageConfig.Config.User = "0000:65532" },
		"wrong version": func(value *trivyReport) {
			value.Metadata.ImageConfig.Config.Labels["org.opencontainers.image.version"] = "9.9.9"
		},
		"wrong revision": func(value *trivyReport) {
			value.Metadata.ImageConfig.Config.Labels["org.opencontainers.image.revision"] = strings.Repeat("f", 40)
		},
		"missing binary": func(value *trivyReport) { value.Results[1].Target = "usr/local/bin/other" },
		"empty results":  func(value *trivyReport) { value.Results = nil },
		"vulnerability finding": func(value *trivyReport) {
			value.Results[1].Vulnerabilities = []json.RawMessage{json.RawMessage(`{"VulnerabilityID":"CVE-1"}`)}
		},
	}
	for name, mutate := range tests {
		t.Run(name, func(t *testing.T) {
			candidate := validTrivy()
			mutate(&candidate)
			candidatePath := writeJSONFixture(t, "candidate.json", candidate)
			if _, err := validateTrivy(candidatePath, "amd64", "exporter-amd64.oci", "usr/local/bin/openshell-event-exporter", "0.0.3", testRevision, identity); err == nil {
				t.Fatal("invalid Trivy evidence was accepted")
			}
		})
	}

	empty := writeRawFixture(t, "empty.json", "{}\n")
	if _, err := validateTrivy(empty, "amd64", "exporter-amd64.oci", "usr/local/bin/openshell-event-exporter", "0.0.3", testRevision, identity); err == nil {
		t.Fatal("empty Trivy evidence was accepted")
	}
}

func TestValidateOCIArchiveRejectsUnsafeOrUnboundImages(t *testing.T) {
	path := filepath.Join(t.TempDir(), "exporter-amd64.oci")
	identity := writeOCIArchive(t, path, "amd64", "nonroot:nonroot", "0.0.3", testRevision)
	got, err := validateOCIArchive(path, "amd64", "0.0.3", testRevision)
	if err != nil {
		t.Fatalf("valid OCI archive rejected: %v", err)
	}
	if got.ImageID != identity.ImageID || !equalStrings(got.DiffIDs, identity.DiffIDs) {
		t.Fatalf("OCI identity mismatch: got %+v want %+v", got, identity)
	}
	for name, validate := range map[string]func() error{
		"wrong architecture": func() error { _, err := validateOCIArchive(path, "arm64", "0.0.3", testRevision); return err },
		"wrong version":      func() error { _, err := validateOCIArchive(path, "amd64", "9.9.9", testRevision); return err },
		"wrong revision": func() error {
			_, err := validateOCIArchive(path, "amd64", "0.0.3", strings.Repeat("f", 40))
			return err
		},
	} {
		t.Run(name, func(t *testing.T) {
			if err := validate(); err == nil {
				t.Fatal("unbound OCI archive was accepted")
			}
		})
	}
	rootPath := filepath.Join(t.TempDir(), "root.oci")
	writeOCIArchive(t, rootPath, "amd64", "root:root", "0.0.3", testRevision)
	if _, err := validateOCIArchive(rootPath, "amd64", "0.0.3", testRevision); err == nil {
		t.Fatal("root OCI archive was accepted")
	}
	malformed := writeRawFixture(t, "malformed.oci", "not a tar archive")
	if _, err := validateOCIArchive(malformed, "amd64", "0.0.3", testRevision); err == nil {
		t.Fatal("malformed OCI archive was accepted")
	}
}

func TestDecodeOneRejectsOversizedEvidence(t *testing.T) {
	path := filepath.Join(t.TempDir(), "oversized.json")
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := file.Truncate(maxEvidenceBytes + 1); err != nil {
		if closeErr := file.Close(); closeErr != nil {
			t.Errorf("close oversized fixture: %v", closeErr)
		}
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	var report trivyReport
	if err := decodeOne(path, &report); err == nil {
		t.Fatal("oversized evidence was accepted")
	}
}

func TestReadTarEntryRejectsDuplicateMetadata(t *testing.T) {
	path := filepath.Join(t.TempDir(), "duplicate.oci")
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	writer := tar.NewWriter(file)
	for _, data := range [][]byte{[]byte(`{"schemaVersion":2}`), []byte(`{"schemaVersion":1}`)} {
		if err := writer.WriteHeader(&tar.Header{Name: "index.json", Mode: 0o644, Size: int64(len(data))}); err != nil {
			_ = writer.Close()
			_ = file.Close()
			t.Fatal(err)
		}
		if _, err := writer.Write(data); err != nil {
			_ = writer.Close()
			_ = file.Close()
			t.Fatal(err)
		}
	}
	if err := writer.Close(); err != nil {
		_ = file.Close()
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := readTarEntry(path, "index.json", maxOCIMetadata); err == nil {
		t.Fatal("duplicate OCI metadata was accepted")
	}
}

func TestVerifyOCIBlobRejectsMissingTamperedOrDuplicateLayers(t *testing.T) {
	data := []byte("layer")
	descriptor := ociDescriptor{Digest: "sha256:" + fixtureDigest(data), Size: int64(len(data))}
	valid := writeTarFixture(t, map[string][][]byte{
		"blobs/sha256/" + strings.TrimPrefix(descriptor.Digest, "sha256:"): {data},
	})
	if err := verifyOCIBlob(valid, descriptor, maxEvidenceBytes); err != nil {
		t.Fatalf("valid OCI layer rejected: %v", err)
	}
	for name, entries := range map[string]map[string][][]byte{
		"missing": {},
		"tampered": {
			"blobs/sha256/" + strings.TrimPrefix(descriptor.Digest, "sha256:"): {[]byte("other")},
		},
		"duplicate": {
			"blobs/sha256/" + strings.TrimPrefix(descriptor.Digest, "sha256:"): {data, data},
		},
	} {
		t.Run(name, func(t *testing.T) {
			if err := verifyOCIBlob(writeTarFixture(t, entries), descriptor, maxEvidenceBytes); err == nil {
				t.Fatal("invalid OCI layer blob was accepted")
			}
		})
	}
}

func TestVerifyReleaseArtifactsWritesBoundDigests(t *testing.T) {
	directory := t.TempDir()
	for _, spec := range releaseArtifacts {
		identity := writeOCIArchive(t, filepath.Join(directory, spec.Archive), spec.Arch, "nonroot:nonroot", "0.0.3", testRevision)
		document := validSPDXForModule(spec.Module)
		document.Name = "dist/" + spec.Archive
		writeJSONAt(t, filepath.Join(directory, spec.SBOM), document)
		report := validTrivy()
		report.ArtifactName = "dist/" + strings.TrimSuffix(spec.Archive, ".oci") + "-layout"
		report.Metadata.ImageID = identity.ImageID
		report.Metadata.DiffIDs = append([]string(nil), identity.DiffIDs...)
		report.Metadata.ImageConfig.Architecture = spec.Arch
		report.Results[1].Target = spec.Binary
		writeJSONAt(t, filepath.Join(directory, spec.Trivy), report)
	}
	report, err := verifyReleaseArtifacts(directory, "0.0.3", testRevision)
	if err != nil {
		t.Fatalf("verify release artifacts: %v", err)
	}
	if !report.Verified || len(report.Artifacts) != 8 {
		t.Fatalf("unexpected verification report: %+v", report)
	}
	for _, artifact := range report.Artifacts {
		if len(artifact.SHA256) != 64 {
			t.Errorf("%s digest length = %d", artifact.Name, len(artifact.SHA256))
		}
	}
}

func TestSemverPatternAcceptsStableAndPrereleaseVersions(t *testing.T) {
	t.Parallel()
	tests := []struct {
		version string
		valid   bool
	}{
		{version: "0.0.4", valid: true},
		{version: "0.0.4-rc.1", valid: true},
		{version: "0.0.4-beta.1-build-7", valid: true},
		{version: "v0.0.4-rc.1", valid: false},
		{version: "0.0.3.1", valid: false},
		{version: "0.0.4-", valid: false},
		{version: "0.0.4+metadata", valid: false},
	}
	for _, test := range tests {
		test := test
		t.Run(test.version, func(t *testing.T) {
			t.Parallel()
			if got := semverPattern.MatchString(test.version); got != test.valid {
				t.Fatalf("semverPattern.MatchString(%q) = %v, want %v", test.version, got, test.valid)
			}
		})
	}
}

func validSPDX() spdxDocument {
	return spdxDocument{
		SPDXVersion: "SPDX-2.3", DataLicense: "CC0-1.0", SPDXID: "SPDXRef-DOCUMENT",
		Name: "dist/exporter-amd64.oci", DocumentNamespace: "https://anchore.example/sbom/123",
		CreationInfo: spdxCreationInfo{Creators: []string{"Organization: Anchore, Inc", "Tool: syft-1.33.0"}, Created: "2026-08-27T18:00:00Z"},
		Packages: []spdxPackage{
			{SPDXID: "SPDXRef-Package-Root", Name: exporterDistributionModule, ExternalRef: []spdxExternalRef{{Type: "purl", Locator: "pkg:golang/" + rootModule + "#distribution"}}},
			{SPDXID: "SPDXRef-Package-Dependency", Name: "example.com/dependency", ExternalRef: []spdxExternalRef{{Type: "purl", Locator: "pkg:golang/example.com/dependency@v1.0.0"}}},
		},
		Files: []spdxFile{{SPDXID: "SPDXRef-File-Binary"}},
		Relationships: []spdxRelationship{
			{ElementID: "SPDXRef-DOCUMENT", Type: "DESCRIBES", RelatedElement: "SPDXRef-Package-Root"},
			{ElementID: "SPDXRef-Package-Root", Type: "CONTAINS", RelatedElement: "SPDXRef-File-Binary"},
		},
	}
}

func validSPDXForModule(module string) spdxDocument {
	document := validSPDX()
	document.Packages[0].Name = module
	document.Packages[0].ExternalRef[0].Locator = "pkg:golang/" + rootModule + "#" + strings.TrimPrefix(module, rootModule+"/")
	return document
}

func validTrivy() trivyReport {
	return trivyReport{
		SchemaVersion: 2, ArtifactName: "dist/exporter-amd64-layout", ArtifactType: "container_image",
		Metadata: trivyMetadata{
			Size: 1024, ImageID: "sha256:" + strings.Repeat("a", 64), DiffIDs: []string{"sha256:" + strings.Repeat("b", 64)},
			ImageConfig: trivyImageConfig{Architecture: "amd64", OS: "linux", Config: trivyConfig{
				User: "nonroot:nonroot", Labels: map[string]string{
					"org.opencontainers.image.version":  "0.0.3",
					"org.opencontainers.image.revision": testRevision,
					"org.opencontainers.image.licenses": "Apache-2.0",
				},
			}},
		},
		Results: []trivyResult{
			{Target: "dist/exporter-amd64-layout (debian 12)", Class: "os-pkgs", Type: "debian"},
			{Target: "usr/local/bin/openshell-event-exporter", Class: "lang-pkgs", Type: "gobinary"},
		},
	}
}

func identityFromTrivy(report trivyReport) ociImageIdentity {
	return ociImageIdentity{
		ImageID: report.Metadata.ImageID, DiffIDs: append([]string(nil), report.Metadata.DiffIDs...),
		Architecture: report.Metadata.ImageConfig.Architecture, OS: report.Metadata.ImageConfig.OS,
		User: report.Metadata.ImageConfig.Config.User, Labels: cloneLabels(report.Metadata.ImageConfig.Config.Labels),
	}
}

func writeOCIArchive(t *testing.T, path, architecture, user, version, revision string) ociImageIdentity {
	t.Helper()
	layerData := []byte("layer")
	diffID := "sha256:" + fixtureDigest(layerData)
	config := ociImageConfig{Architecture: architecture, OS: "linux", Config: trivyConfig{
		User: user, Labels: map[string]string{
			"org.opencontainers.image.version":  version,
			"org.opencontainers.image.revision": revision,
			"org.opencontainers.image.licenses": "Apache-2.0",
		},
	}}
	config.RootFS.Type = "layers"
	config.RootFS.DiffIDs = []string{diffID}
	configData := mustJSON(t, config)
	configDescriptor := ociDescriptor{
		MediaType: "application/vnd.oci.image.config.v1+json",
		Digest:    "sha256:" + fixtureDigest(configData), Size: int64(len(configData)),
	}
	manifest := ociManifest{
		SchemaVersion: 2, MediaType: "application/vnd.oci.image.manifest.v1+json", Config: configDescriptor,
		Layers: []ociDescriptor{{
			MediaType: "application/vnd.oci.image.layer.v1.tar",
			Digest:    "sha256:" + fixtureDigest(layerData), Size: int64(len(layerData)),
		}},
	}
	manifestData := mustJSON(t, manifest)
	manifestDescriptor := ociDescriptor{
		MediaType: "application/vnd.oci.image.manifest.v1+json",
		Digest:    "sha256:" + fixtureDigest(manifestData), Size: int64(len(manifestData)),
		Platform: ociPlatform{Architecture: architecture, OS: "linux"},
	}
	indexData := mustJSON(t, ociIndex{SchemaVersion: 2, Manifests: []ociDescriptor{manifestDescriptor}})
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	writer := tar.NewWriter(file)
	for name, data := range map[string][]byte{
		"index.json": indexData,
		"blobs/sha256/" + strings.TrimPrefix(manifestDescriptor.Digest, "sha256:"): manifestData,
		"blobs/sha256/" + strings.TrimPrefix(configDescriptor.Digest, "sha256:"):   configData,
		"blobs/sha256/" + strings.TrimPrefix(manifest.Layers[0].Digest, "sha256:"): layerData,
	} {
		if err := writer.WriteHeader(&tar.Header{Name: name, Mode: 0o644, Size: int64(len(data))}); err != nil {
			_ = writer.Close()
			_ = file.Close()
			t.Fatal(err)
		}
		if _, err := writer.Write(data); err != nil {
			_ = writer.Close()
			_ = file.Close()
			t.Fatal(err)
		}
	}
	if err := writer.Close(); err != nil {
		_ = file.Close()
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	return ociImageIdentity{
		ImageID: configDescriptor.Digest, DiffIDs: []string{diffID}, Architecture: architecture,
		OS: "linux", User: user, Labels: cloneLabels(config.Config.Labels),
	}
}

func writeTarFixture(t *testing.T, entries map[string][][]byte) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "fixture.tar")
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	writer := tar.NewWriter(file)
	for name, values := range entries {
		for _, data := range values {
			if err := writer.WriteHeader(&tar.Header{Name: name, Mode: 0o644, Size: int64(len(data))}); err != nil {
				_ = writer.Close()
				_ = file.Close()
				t.Fatal(err)
			}
			if _, err := writer.Write(data); err != nil {
				_ = writer.Close()
				_ = file.Close()
				t.Fatal(err)
			}
		}
	}
	if err := writer.Close(); err != nil {
		_ = file.Close()
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	return path
}

func fixtureDigest(data []byte) string {
	digest := sha256.Sum256(data)
	return hex.EncodeToString(digest[:])
}

func writeJSONFixture(t *testing.T, name string, value any) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), name)
	writeJSONAt(t, path, value)
	return path
}

func writeJSONAt(t *testing.T, path string, value any) {
	t.Helper()
	if err := os.WriteFile(path, mustJSON(t, value), 0o600); err != nil {
		t.Fatal(err)
	}
}

func writeRawFixture(t *testing.T, name, value string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), name)
	if err := os.WriteFile(path, []byte(value), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func mustJSON(t *testing.T, value any) []byte {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return data
}
