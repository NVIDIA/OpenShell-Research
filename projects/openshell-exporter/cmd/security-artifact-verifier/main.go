// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

// Command security-artifact-verifier validates the security evidence emitted
// for the released exporter and OTLP proxy images. It deliberately supports a
// narrow, versioned evidence contract instead of accepting arbitrary reports.
package main

import (
	"archive/tar"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

const (
	rootModule                 = "github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter"
	exporterDistributionModule = rootModule + "/distribution"
	proxyDistributionModule    = rootModule + "/otlp-proxy-distribution"
	maxEvidenceBytes           = 128 << 20
	maxOCIMetadata             = 4 << 20
)

var (
	semverPattern = regexp.MustCompile(`^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?$`)
	shaPattern    = regexp.MustCompile(`^[0-9a-f]{40}$`)
	digestPattern = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
	numericUser   = regexp.MustCompile(`^[0-9]+$`)
	spdxIDPattern = regexp.MustCompile(`^SPDXRef-[A-Za-z0-9.-]+$`)
	relationType  = regexp.MustCompile(`^[A-Z][A-Z0-9_]*$`)
)

type artifactSpec struct {
	Product string
	Arch    string
	Module  string
	Archive string
	SBOM    string
	Trivy   string
	Binary  string
}

var releaseArtifacts = []artifactSpec{
	{Product: "openshell-event-exporter", Arch: "amd64", Module: exporterDistributionModule, Archive: "exporter-amd64.oci", SBOM: "exporter-amd64.spdx.json", Trivy: "exporter-amd64-trivy.json", Binary: "usr/local/bin/openshell-event-exporter"},
	{Product: "openshell-event-exporter", Arch: "arm64", Module: exporterDistributionModule, Archive: "exporter-arm64.oci", SBOM: "exporter-arm64.spdx.json", Trivy: "exporter-arm64-trivy.json", Binary: "usr/local/bin/openshell-event-exporter"},
	{Product: "openshell-otlp-proxy", Arch: "amd64", Module: proxyDistributionModule, Archive: "otlp-proxy-amd64.oci", SBOM: "otlp-proxy-amd64.spdx.json", Trivy: "otlp-proxy-amd64-trivy.json", Binary: "usr/local/bin/openshell-otlp-proxy"},
	{Product: "openshell-otlp-proxy", Arch: "arm64", Module: proxyDistributionModule, Archive: "otlp-proxy-arm64.oci", SBOM: "otlp-proxy-arm64.spdx.json", Trivy: "otlp-proxy-arm64-trivy.json", Binary: "usr/local/bin/openshell-otlp-proxy"},
}

type spdxDocument struct {
	SPDXVersion       string             `json:"spdxVersion"`
	DataLicense       string             `json:"dataLicense"`
	SPDXID            string             `json:"SPDXID"`
	Name              string             `json:"name"`
	DocumentNamespace string             `json:"documentNamespace"`
	CreationInfo      spdxCreationInfo   `json:"creationInfo"`
	Packages          []spdxPackage      `json:"packages"`
	Files             []spdxFile         `json:"files"`
	Relationships     []spdxRelationship `json:"relationships"`
}

type spdxCreationInfo struct {
	Creators []string `json:"creators"`
	Created  string   `json:"created"`
}

type spdxPackage struct {
	SPDXID      string            `json:"SPDXID"`
	Name        string            `json:"name"`
	ExternalRef []spdxExternalRef `json:"externalRefs"`
}

type spdxExternalRef struct {
	Type    string `json:"referenceType"`
	Locator string `json:"referenceLocator"`
}

type spdxFile struct {
	SPDXID string `json:"SPDXID"`
}

type spdxRelationship struct {
	ElementID      string `json:"spdxElementId"`
	Type           string `json:"relationshipType"`
	RelatedElement string `json:"relatedSpdxElement"`
}

type trivyReport struct {
	SchemaVersion int           `json:"SchemaVersion"`
	ArtifactName  string        `json:"ArtifactName"`
	ArtifactType  string        `json:"ArtifactType"`
	Metadata      trivyMetadata `json:"Metadata"`
	Results       []trivyResult `json:"Results"`
}

type trivyMetadata struct {
	Size        int64            `json:"Size"`
	ImageID     string           `json:"ImageID"`
	DiffIDs     []string         `json:"DiffIDs"`
	ImageConfig trivyImageConfig `json:"ImageConfig"`
}

type trivyImageConfig struct {
	Architecture string      `json:"architecture"`
	OS           string      `json:"os"`
	Config       trivyConfig `json:"config"`
}

type trivyConfig struct {
	User   string            `json:"User"`
	Labels map[string]string `json:"Labels"`
}

type trivyResult struct {
	Target          string            `json:"Target"`
	Class           string            `json:"Class"`
	Type            string            `json:"Type"`
	Vulnerabilities []json.RawMessage `json:"Vulnerabilities"`
}

type verificationReport struct {
	SchemaVersion string                 `json:"schema_version"`
	Version       string                 `json:"version"`
	Revision      string                 `json:"revision"`
	Verified      bool                   `json:"verified"`
	Artifacts     []artifactVerification `json:"artifacts"`
}

type artifactVerification struct {
	Name          string `json:"name"`
	Kind          string `json:"kind"`
	Product       string `json:"product"`
	Architecture  string `json:"architecture"`
	SHA256        string `json:"sha256"`
	Archive       string `json:"archive"`
	ArchiveSHA256 string `json:"archive_sha256"`
	ImageID       string `json:"image_id,omitempty"`
	Packages      int    `json:"packages,omitempty"`
	Relationships int    `json:"relationships,omitempty"`
	ScanResults   int    `json:"scan_results,omitempty"`
}

type ociIndex struct {
	SchemaVersion int             `json:"schemaVersion"`
	Manifests     []ociDescriptor `json:"manifests"`
}

type ociDescriptor struct {
	MediaType string      `json:"mediaType"`
	Digest    string      `json:"digest"`
	Size      int64       `json:"size"`
	Platform  ociPlatform `json:"platform"`
}

type ociPlatform struct {
	Architecture string `json:"architecture"`
	OS           string `json:"os"`
}

type ociManifest struct {
	SchemaVersion int             `json:"schemaVersion"`
	MediaType     string          `json:"mediaType"`
	Config        ociDescriptor   `json:"config"`
	Layers        []ociDescriptor `json:"layers"`
}

type ociImageConfig struct {
	Architecture string      `json:"architecture"`
	OS           string      `json:"os"`
	Config       trivyConfig `json:"config"`
	RootFS       struct {
		Type    string   `json:"type"`
		DiffIDs []string `json:"diff_ids"`
	} `json:"rootfs"`
}

type ociImageIdentity struct {
	ImageID      string
	DiffIDs      []string
	Architecture string
	OS           string
	User         string
	Labels       map[string]string
}

func main() {
	directory := flag.String("directory", "dist", "directory containing SBOM and Trivy JSON artifacts")
	version := flag.String("version", "", "expected semantic version without v prefix")
	revision := flag.String("revision", "", "expected 40-character Git revision")
	output := flag.String("output", "", "verification report path (default DIRECTORY/security-evidence-verification.json)")
	flag.Parse()
	if flag.NArg() != 0 || !semverPattern.MatchString(*version) || !shaPattern.MatchString(*revision) {
		fmt.Fprintln(os.Stderr, "usage: security-artifact-verifier --directory DIR --version MAJOR.MINOR.PATCH[-PRERELEASE] --revision 40_HEX_SHA [--output FILE]")
		os.Exit(2)
	}
	if *output == "" {
		*output = filepath.Join(*directory, "security-evidence-verification.json")
	}
	report, err := verifyReleaseArtifacts(*directory, *version, *revision)
	if err != nil {
		fmt.Fprintf(os.Stderr, "security artifact verification: %v\n", err)
		os.Exit(1)
	}
	if err := writeReport(*output, report); err != nil {
		fmt.Fprintf(os.Stderr, "security artifact verification: write report: %v\n", err)
		os.Exit(1)
	}
	fmt.Printf("security artifact verification: %d SBOMs and %d vulnerability reports verified\n", len(releaseArtifacts), len(releaseArtifacts))
}

func verifyReleaseArtifacts(directory, version, revision string) (verificationReport, error) {
	if !semverPattern.MatchString(version) || !shaPattern.MatchString(revision) {
		return verificationReport{}, errors.New("version or revision is not a valid release identity")
	}
	report := verificationReport{SchemaVersion: "1.0", Version: version, Revision: revision, Verified: true}
	for _, spec := range releaseArtifacts {
		archivePath := filepath.Join(directory, spec.Archive)
		archiveIdentity, err := validateOCIArchive(archivePath, spec.Arch, version, revision)
		if err != nil {
			return verificationReport{}, fmt.Errorf("%s: %w", spec.Archive, err)
		}
		archiveDigest, err := fileDigest(archivePath)
		if err != nil {
			return verificationReport{}, err
		}
		sbomPath := filepath.Join(directory, spec.SBOM)
		packages, relationships, err := validateSPDX(sbomPath, spec.Archive, spec.Module)
		if err != nil {
			return verificationReport{}, fmt.Errorf("%s: %w", spec.SBOM, err)
		}
		sbomDigest, err := fileDigest(sbomPath)
		if err != nil {
			return verificationReport{}, err
		}
		report.Artifacts = append(report.Artifacts, artifactVerification{
			Name: spec.SBOM, Kind: "spdx", Product: spec.Product, Architecture: spec.Arch,
			SHA256: sbomDigest, Archive: spec.Archive, ArchiveSHA256: archiveDigest,
			Packages: packages, Relationships: relationships,
		})

		trivyPath := filepath.Join(directory, spec.Trivy)
		results, err := validateTrivy(trivyPath, spec.Arch, spec.Archive, spec.Binary, version, revision, archiveIdentity)
		if err != nil {
			return verificationReport{}, fmt.Errorf("%s: %w", spec.Trivy, err)
		}
		trivyDigest, err := fileDigest(trivyPath)
		if err != nil {
			return verificationReport{}, err
		}
		report.Artifacts = append(report.Artifacts, artifactVerification{
			Name: spec.Trivy, Kind: "trivy", Product: spec.Product, Architecture: spec.Arch,
			SHA256: trivyDigest, Archive: spec.Archive, ArchiveSHA256: archiveDigest,
			ImageID: archiveIdentity.ImageID, ScanResults: results,
		})
	}
	sort.Slice(report.Artifacts, func(i, j int) bool { return report.Artifacts[i].Name < report.Artifacts[j].Name })
	return report, nil
}

func validateSPDX(path, expectedArchive, expectedModule string) (int, int, error) {
	var document spdxDocument
	if err := decodeOne(path, &document); err != nil {
		return 0, 0, err
	}
	if document.SPDXVersion != "SPDX-2.3" || document.DataLicense != "CC0-1.0" || document.SPDXID != "SPDXRef-DOCUMENT" {
		return 0, 0, errors.New("not a supported SPDX 2.3 document")
	}
	if filepath.Base(document.Name) != expectedArchive {
		return 0, 0, fmt.Errorf("documents %q, want %q", filepath.Base(document.Name), expectedArchive)
	}
	namespace, err := url.Parse(document.DocumentNamespace)
	if err != nil || namespace.Scheme != "https" || namespace.Host == "" {
		return 0, 0, errors.New("document namespace must be an absolute HTTPS URL")
	}
	if _, err := time.Parse(time.RFC3339, document.CreationInfo.Created); err != nil {
		return 0, 0, errors.New("creationInfo.created must be RFC3339")
	}
	hasSyftCreator := false
	for _, creator := range document.CreationInfo.Creators {
		if strings.HasPrefix(strings.ToLower(creator), "tool: syft-") {
			hasSyftCreator = true
		}
	}
	if !hasSyftCreator {
		return 0, 0, errors.New("creationInfo does not identify Syft")
	}
	if len(document.Packages) == 0 || len(document.Relationships) == 0 {
		return 0, 0, errors.New("package and relationship inventories must be non-empty")
	}
	knownIDs := map[string]bool{document.SPDXID: true}
	hasDistributionModulePURL := false
	purlCount := 0
	for _, pkg := range document.Packages {
		if pkg.SPDXID == "" || pkg.Name == "" || knownIDs[pkg.SPDXID] {
			return 0, 0, errors.New("package IDs and names must be non-empty and unique")
		}
		knownIDs[pkg.SPDXID] = true
		for _, ref := range pkg.ExternalRef {
			if ref.Type == "purl" && strings.HasPrefix(ref.Locator, "pkg:") {
				purlCount++
				if pkg.Name == expectedModule && matchesModulePURL(ref.Locator, expectedModule) {
					hasDistributionModulePURL = true
				}
			}
		}
	}
	for _, file := range document.Files {
		if file.SPDXID == "" || knownIDs[file.SPDXID] {
			return 0, 0, errors.New("file IDs must be non-empty and unique")
		}
		knownIDs[file.SPDXID] = true
	}
	if !hasDistributionModulePURL || purlCount == 0 {
		return 0, 0, fmt.Errorf("declared distribution module %q and its package URL must be present", expectedModule)
	}
	hasDocumentDescription := false
	for _, relationship := range document.Relationships {
		if !relationType.MatchString(relationship.Type) ||
			!spdxIDPattern.MatchString(relationship.ElementID) ||
			!spdxIDPattern.MatchString(relationship.RelatedElement) {
			return 0, 0, errors.New("relationship contains an invalid SPDX identifier or type")
		}
		if relationship.ElementID == document.SPDXID && relationship.Type == "DESCRIBES" && knownIDs[relationship.RelatedElement] {
			hasDocumentDescription = true
		}
	}
	if !hasDocumentDescription {
		return 0, 0, errors.New("document does not describe a known package")
	}
	return len(document.Packages), len(document.Relationships), nil
}

func matchesModulePURL(locator, module string) bool {
	subpath := strings.TrimPrefix(module, rootModule+"/")
	if subpath == module || subpath == "" {
		return false
	}
	base := "pkg:golang/" + rootModule
	return locator == base+"#"+subpath ||
		(strings.HasPrefix(locator, base+"@") && strings.HasSuffix(locator, "#"+subpath))
}

func validateTrivy(path, architecture, expectedArchive, expectedBinary, version, revision string, archive ociImageIdentity) (int, error) {
	var report trivyReport
	if err := decodeOne(path, &report); err != nil {
		return 0, err
	}
	if report.SchemaVersion != 2 || report.ArtifactType != "container_image" {
		return 0, errors.New("not a supported Trivy container-image report")
	}
	expectedLayout := strings.TrimSuffix(expectedArchive, filepath.Ext(expectedArchive)) + "-layout"
	artifactName := filepath.Base(filepath.Clean(report.ArtifactName))
	if artifactName != expectedArchive && artifactName != expectedLayout {
		return 0, fmt.Errorf("scans %q, want %q or %q", artifactName, expectedArchive, expectedLayout)
	}
	if report.Metadata.Size <= 0 || !digestPattern.MatchString(report.Metadata.ImageID) || len(report.Metadata.DiffIDs) == 0 {
		return 0, errors.New("image identity and layer metadata must be present")
	}
	for _, digest := range report.Metadata.DiffIDs {
		if !digestPattern.MatchString(digest) {
			return 0, errors.New("invalid image layer digest")
		}
	}
	if report.Metadata.ImageID != archive.ImageID || !equalStrings(report.Metadata.DiffIDs, archive.DiffIDs) {
		return 0, errors.New("scan image identity or layers do not match the OCI archive")
	}
	config := report.Metadata.ImageConfig
	if config.Architecture != architecture || config.OS != "linux" {
		return 0, fmt.Errorf("image platform is %s/%s, want linux/%s", config.OS, config.Architecture, architecture)
	}
	if err := validateNonRootUser(config.Config.User); err != nil {
		return 0, errors.New("final image must declare a non-root user")
	}
	if err := validateCandidateLabels(config.Config.Labels, version, revision); err != nil {
		return 0, errors.New("OCI version, revision, or license label does not match the candidate")
	}
	if config.Architecture != archive.Architecture || config.OS != archive.OS ||
		config.Config.User != archive.User || !candidateLabelsEqual(config.Config.Labels, archive.Labels) {
		return 0, errors.New("scan platform, user, or labels do not match the OCI archive config")
	}
	if len(report.Results) == 0 {
		return 0, errors.New("scan result inventory must be non-empty")
	}
	hasBinary := false
	vulnerabilities := 0
	for _, result := range report.Results {
		if result.Target == "" || result.Class == "" || result.Type == "" {
			return 0, errors.New("scan results require target, class, and type")
		}
		if strings.TrimPrefix(result.Target, "/") == expectedBinary && result.Type == "gobinary" {
			hasBinary = true
		}
		vulnerabilities += len(result.Vulnerabilities)
	}
	if !hasBinary {
		return 0, fmt.Errorf("scan does not include expected Go binary %q", expectedBinary)
	}
	if vulnerabilities != 0 {
		return 0, fmt.Errorf("scan contains %d High/Critical fixed vulnerability findings", vulnerabilities)
	}
	return len(report.Results), nil
}

func validateOCIArchive(path, architecture, version, revision string) (ociImageIdentity, error) {
	info, err := os.Stat(path)
	if err != nil {
		return ociImageIdentity{}, err
	}
	if info.Size() <= 0 || info.Size() > maxEvidenceBytes {
		return ociImageIdentity{}, fmt.Errorf("OCI archive size %d is outside the supported 1..%d byte range", info.Size(), maxEvidenceBytes)
	}
	indexData, err := readTarEntry(path, "index.json", maxOCIMetadata)
	if err != nil {
		return ociImageIdentity{}, fmt.Errorf("read OCI index: %w", err)
	}
	var index ociIndex
	if err := decodeJSON(indexData, &index); err != nil || index.SchemaVersion != 2 {
		return ociImageIdentity{}, errors.New("invalid OCI image index")
	}
	var imageManifest *ociDescriptor
	for i := range index.Manifests {
		descriptor := &index.Manifests[i]
		if descriptor.Platform.OS == "linux" && descriptor.Platform.Architecture == architecture {
			if imageManifest != nil {
				return ociImageIdentity{}, fmt.Errorf("OCI index contains multiple linux/%s manifests", architecture)
			}
			imageManifest = descriptor
		}
	}
	if imageManifest == nil || imageManifest.MediaType != "application/vnd.oci.image.manifest.v1+json" ||
		!digestPattern.MatchString(imageManifest.Digest) || imageManifest.Size <= 0 {
		return ociImageIdentity{}, fmt.Errorf("OCI index has no valid linux/%s image manifest", architecture)
	}
	manifestData, err := readOCIBlob(path, *imageManifest)
	if err != nil {
		return ociImageIdentity{}, fmt.Errorf("read OCI manifest: %w", err)
	}
	var manifest ociManifest
	if err := decodeJSON(manifestData, &manifest); err != nil || manifest.SchemaVersion != 2 ||
		manifest.MediaType != "application/vnd.oci.image.manifest.v1+json" {
		return ociImageIdentity{}, errors.New("invalid OCI image manifest")
	}
	if manifest.Config.MediaType != "application/vnd.oci.image.config.v1+json" ||
		!digestPattern.MatchString(manifest.Config.Digest) || manifest.Config.Size <= 0 || len(manifest.Layers) == 0 {
		return ociImageIdentity{}, errors.New("OCI manifest has no valid image config or layers")
	}
	for _, layer := range manifest.Layers {
		if !digestPattern.MatchString(layer.Digest) || layer.Size <= 0 || layer.Size > maxEvidenceBytes || !strings.HasPrefix(layer.MediaType, "application/vnd.oci.image.layer.v1") {
			return ociImageIdentity{}, errors.New("OCI manifest contains an invalid layer descriptor")
		}
		if err := verifyOCIBlob(path, layer, maxEvidenceBytes); err != nil {
			return ociImageIdentity{}, fmt.Errorf("verify OCI layer %s: %w", layer.Digest, err)
		}
	}
	configData, err := readOCIBlob(path, manifest.Config)
	if err != nil {
		return ociImageIdentity{}, fmt.Errorf("read OCI image config: %w", err)
	}
	var config ociImageConfig
	if err := decodeJSON(configData, &config); err != nil || config.Architecture != architecture || config.OS != "linux" {
		return ociImageIdentity{}, errors.New("OCI image config has the wrong platform")
	}
	if err := validateNonRootUser(config.Config.User); err != nil {
		return ociImageIdentity{}, err
	}
	if err := validateCandidateLabels(config.Config.Labels, version, revision); err != nil {
		return ociImageIdentity{}, err
	}
	if config.RootFS.Type != "layers" || len(config.RootFS.DiffIDs) != len(manifest.Layers) || len(config.RootFS.DiffIDs) == 0 {
		return ociImageIdentity{}, errors.New("OCI root filesystem layer identity is incomplete")
	}
	for _, digest := range config.RootFS.DiffIDs {
		if !digestPattern.MatchString(digest) {
			return ociImageIdentity{}, errors.New("OCI root filesystem contains an invalid DiffID")
		}
	}
	return ociImageIdentity{
		ImageID: manifest.Config.Digest, DiffIDs: append([]string(nil), config.RootFS.DiffIDs...),
		Architecture: config.Architecture, OS: config.OS, User: config.Config.User,
		Labels: cloneLabels(config.Config.Labels),
	}, nil
}

func verifyOCIBlob(path string, descriptor ociDescriptor, maxBytes int64) error {
	if !digestPattern.MatchString(descriptor.Digest) || descriptor.Size <= 0 || descriptor.Size > maxBytes {
		return errors.New("OCI blob descriptor is invalid or exceeds the supported size")
	}
	name := "blobs/sha256/" + strings.TrimPrefix(descriptor.Digest, "sha256:")
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer func() { _ = file.Close() }()
	reader := tar.NewReader(file)
	found := false
	for {
		header, err := reader.Next()
		if errors.Is(err, io.EOF) {
			if !found {
				return os.ErrNotExist
			}
			return nil
		}
		if err != nil {
			return err
		}
		if header.Name != name {
			continue
		}
		if found {
			return fmt.Errorf("archive contains duplicate %q entries", name)
		}
		if header.Typeflag != tar.TypeReg || header.Size != descriptor.Size || header.Size > maxBytes {
			return errors.New("OCI blob size does not match its descriptor")
		}
		digest := sha256.New()
		if _, err := io.CopyN(digest, reader, header.Size); err != nil {
			return err
		}
		if "sha256:"+hex.EncodeToString(digest.Sum(nil)) != descriptor.Digest {
			return errors.New("OCI blob digest does not match its descriptor")
		}
		found = true
	}
}

func readOCIBlob(path string, descriptor ociDescriptor) ([]byte, error) {
	data, err := readTarEntry(path, "blobs/sha256/"+strings.TrimPrefix(descriptor.Digest, "sha256:"), maxOCIMetadata)
	if err != nil {
		return nil, err
	}
	if int64(len(data)) != descriptor.Size || "sha256:"+digestBytes(data) != descriptor.Digest {
		return nil, errors.New("OCI blob size or digest does not match its descriptor")
	}
	return data, nil
}

func readTarEntry(path, name string, maxBytes int64) ([]byte, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer func() { _ = file.Close() }()
	reader := tar.NewReader(file)
	var found []byte
	for {
		header, err := reader.Next()
		if errors.Is(err, io.EOF) {
			if found == nil {
				return nil, os.ErrNotExist
			}
			return found, nil
		}
		if err != nil {
			return nil, err
		}
		if header.Name != name {
			continue
		}
		if found != nil {
			return nil, fmt.Errorf("archive contains duplicate %q entries", name)
		}
		if header.Typeflag != tar.TypeReg || header.Size <= 0 || header.Size > maxBytes {
			return nil, fmt.Errorf("entry size %d is outside the supported 1..%d byte range", header.Size, maxBytes)
		}
		found = make([]byte, header.Size)
		if _, err := io.ReadFull(reader, found); err != nil {
			return nil, err
		}
	}
}

func validateNonRootUser(value string) error {
	user := strings.ToLower(strings.TrimSpace(value))
	principal := strings.TrimSpace(strings.SplitN(user, ":", 2)[0])
	if principal == "" || principal == "root" || principal == "0" {
		return errors.New("final image must declare a non-root user")
	}
	if numericUser.MatchString(principal) {
		uid, err := strconv.ParseUint(principal, 10, 64)
		if err != nil || uid == 0 {
			return errors.New("final image must declare a non-root user")
		}
	}
	return nil
}

func validateCandidateLabels(labels map[string]string, version, revision string) error {
	if labels["org.opencontainers.image.version"] != version ||
		labels["org.opencontainers.image.revision"] != revision ||
		labels["org.opencontainers.image.licenses"] != "Apache-2.0" {
		return errors.New("OCI version, revision, or license label does not match the candidate")
	}
	return nil
}

func candidateLabelsEqual(left, right map[string]string) bool {
	for _, key := range []string{"org.opencontainers.image.version", "org.opencontainers.image.revision", "org.opencontainers.image.licenses"} {
		if left[key] != right[key] {
			return false
		}
	}
	return true
}

func equalStrings(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	for i := range left {
		if left[i] != right[i] {
			return false
		}
	}
	return true
}

func cloneLabels(labels map[string]string) map[string]string {
	result := make(map[string]string, len(labels))
	for key, value := range labels {
		result[key] = value
	}
	return result
}

func decodeOne(path string, destination any) error {
	info, err := os.Stat(path)
	if err != nil {
		return err
	}
	if info.Size() <= 0 || info.Size() > maxEvidenceBytes {
		return fmt.Errorf("evidence size %d is outside the supported 1..%d byte range", info.Size(), maxEvidenceBytes)
	}
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer func() { _ = file.Close() }()
	return decodeJSONReader(file, destination)
}

func decodeJSON(data []byte, destination any) error {
	return decodeJSONReader(bytes.NewReader(data), destination)
}

func decodeJSONReader(reader io.Reader, destination any) error {
	decoder := json.NewDecoder(reader)
	if err := decoder.Decode(destination); err != nil {
		return fmt.Errorf("decode JSON: %w", err)
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		return errors.New("JSON contains trailing data")
	}
	return nil
}

func digestBytes(data []byte) string {
	digest := sha256.Sum256(data)
	return hex.EncodeToString(digest[:])
}

func fileDigest(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer func() { _ = file.Close() }()
	digest := sha256.New()
	if _, err := io.Copy(digest, file); err != nil {
		return "", err
	}
	return hex.EncodeToString(digest.Sum(nil)), nil
}

func writeReport(path string, report verificationReport) error {
	directory := filepath.Dir(path)
	if err := os.MkdirAll(directory, 0o755); err != nil {
		return err
	}
	temporary, err := os.CreateTemp(directory, ".security-evidence-*.json")
	if err != nil {
		return err
	}
	temporaryPath := temporary.Name()
	defer func() { _ = os.Remove(temporaryPath) }()
	if err := temporary.Chmod(0o644); err != nil {
		return closeAfterError(temporary, err)
	}
	encoder := json.NewEncoder(temporary)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(report); err != nil {
		return closeAfterError(temporary, err)
	}
	if err := temporary.Sync(); err != nil {
		return closeAfterError(temporary, err)
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return os.Rename(temporaryPath, path)
}

func closeAfterError(file *os.File, operationError error) error {
	if closeError := file.Close(); closeError != nil {
		return errors.Join(operationError, closeError)
	}
	return operationError
}
