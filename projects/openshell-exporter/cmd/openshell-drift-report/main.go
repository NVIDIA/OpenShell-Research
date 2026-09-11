// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

// openshell-drift-report compares the exporter's qualified OpenShell release
// with an official candidate and writes review evidence. It never changes pins.
package main

import (
	"bufio"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"time"
)

const (
	reportSchema = "1.0"
	upstreamRepo = "https://github.com/NVIDIA/OpenShell.git"
	latestAPI    = "https://api.github.com/repos/NVIDIA/OpenShell/releases/latest"
)

var relevantRPCs = []string{
	"GetDraftHistory", "GetDraftPolicy", "GetGatewayInfo", "GetSandboxPolicyStatus",
	"ListSandboxPolicies", "ListSandboxes", "WatchEvents", "WatchSandbox",
}

type options struct {
	root, pinnedVersion, latestVersion, pinnedSource, candidateSource string
	output, jsonOutput, releaseAPI, generatedAt                       string
	skipRegistry                                                      bool
}

type component struct {
	Pinned          string `json:"pinned"`
	Candidate       string `json:"candidate"`
	Changed         bool   `json:"changed"`
	RegistryRef     string `json:"registry_ref,omitempty"`
	RegistryDigest  string `json:"registry_digest,omitempty"`
	RegistryChecked bool   `json:"registry_checked"`
}

type apiDiff struct {
	RelevantRPCs map[string]string `json:"relevant_rpcs"`
	Added        []string          `json:"added"`
	Removed      []string          `json:"removed"`
}

type driftReport struct {
	SchemaVersion  string               `json:"schema_version"`
	GeneratedAt    string               `json:"generated_at"`
	PinnedRelease  string               `json:"pinned_release"`
	LatestRelease  string               `json:"latest_release"`
	ReleaseURL     string               `json:"release_url"`
	ReleaseChanged bool                 `json:"release_changed"`
	Components     map[string]component `json:"components"`
	PublicAPI      apiDiff              `json:"public_api"`
	ReviewRequired bool                 `json:"review_required"`
}

type githubRelease struct {
	TagName string `json:"tag_name"`
	HTMLURL string `json:"html_url"`
}

func main() {
	var opts options
	flag.StringVar(&opts.root, "root", ".", "exporter repository root")
	flag.StringVar(&opts.pinnedVersion, "pinned-version", "", "qualified OpenShell release (defaults to docs/compatibility.md)")
	flag.StringVar(&opts.latestVersion, "latest-version", "", "official candidate release override")
	flag.StringVar(&opts.pinnedSource, "pinned-source", "", "existing pinned OpenShell source tree")
	flag.StringVar(&opts.candidateSource, "candidate-source", "", "existing candidate OpenShell source tree")
	flag.StringVar(&opts.output, "output", "dist/openshell-drift-report.md", "Markdown report path")
	flag.StringVar(&opts.jsonOutput, "json-output", "dist/openshell-drift-report.json", "JSON report path")
	flag.StringVar(&opts.releaseAPI, "release-api", latestAPI, "official latest-release API")
	flag.StringVar(&opts.generatedAt, "generated-at", "", "RFC3339 report time override")
	flag.BoolVar(&opts.skipRegistry, "skip-registry", false, "skip authoritative registry digest checks")
	flag.Parse()
	if err := run(opts); err != nil {
		fmt.Fprintln(os.Stderr, "openshell drift report:", err)
		os.Exit(1)
	}
}

func run(opts options) error {
	if opts.pinnedVersion == "" {
		version, err := pinnedVersion(filepath.Join(opts.root, "docs", "compatibility.md"))
		if err != nil {
			return err
		}
		opts.pinnedVersion = version
	}
	release := githubRelease{TagName: opts.latestVersion}
	if release.TagName == "" {
		var err error
		release, err = latestRelease(opts.releaseAPI)
		if err != nil {
			return err
		}
	}
	if release.HTMLURL == "" {
		release.HTMLURL = "https://github.com/NVIDIA/OpenShell/releases/tag/" + release.TagName
	}

	temporary, err := os.MkdirTemp("", "openshell-drift-")
	if err != nil {
		return fmt.Errorf("create temporary directory: %w", err)
	}
	defer func() {
		if err := os.RemoveAll(temporary); err != nil {
			fmt.Fprintln(os.Stderr, "openshell drift report: remove temporary directory:", err)
		}
	}()
	pinnedSource, err := sourceTree(opts.pinnedSource, opts.pinnedVersion, filepath.Join(temporary, "pinned"))
	if err != nil {
		return err
	}
	candidateSource, err := sourceTree(opts.candidateSource, release.TagName, filepath.Join(temporary, "candidate"))
	if err != nil {
		return err
	}

	report, err := compare(opts, release, pinnedSource, candidateSource)
	if err != nil {
		return err
	}
	if err := writeJSON(opts.jsonOutput, report); err != nil {
		return err
	}
	if err := writeFile(opts.output, []byte(markdown(report))); err != nil {
		return err
	}
	fmt.Printf("OpenShell drift review: pinned=%s latest=%s review_required=%t\n", report.PinnedRelease, report.LatestRelease, report.ReviewRequired)
	return nil
}

func pinnedVersion(path string) (string, error) {
	body, err := os.ReadFile(path)
	if err != nil {
		return "", fmt.Errorf("read compatibility contract: %w", err)
	}
	match := regexp.MustCompile("(?m)^\\| OpenShell gateway \\| `([^`]+)` \\|").FindSubmatch(body)
	if len(match) != 2 {
		return "", errors.New("OpenShell gateway pin is missing from docs/compatibility.md")
	}
	return string(match[1]), nil
}

func latestRelease(endpoint string) (githubRelease, error) {
	request, err := http.NewRequest(http.MethodGet, endpoint, nil)
	if err != nil {
		return githubRelease{}, fmt.Errorf("create latest-release request: %w", err)
	}
	request.Header.Set("Accept", "application/vnd.github+json")
	request.Header.Set("User-Agent", "openshell-event-exporter-drift-report")
	client := &http.Client{Timeout: 30 * time.Second}
	response, err := client.Do(request)
	if err != nil {
		return githubRelease{}, fmt.Errorf("read official latest release: %w", err)
	}
	defer func() { _ = response.Body.Close() }()
	if response.StatusCode != http.StatusOK {
		return githubRelease{}, fmt.Errorf("official latest release returned HTTP %d", response.StatusCode)
	}
	var release githubRelease
	if err := json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&release); err != nil {
		return githubRelease{}, fmt.Errorf("decode official latest release: %w", err)
	}
	if release.TagName == "" {
		return githubRelease{}, errors.New("official latest release omitted tag_name")
	}
	return release, nil
}

func sourceTree(existing, version, destination string) (string, error) {
	if existing != "" {
		info, err := os.Stat(existing)
		if err != nil || !info.IsDir() {
			return "", fmt.Errorf("source tree %q is not a directory", existing)
		}
		return existing, nil
	}
	command := exec.Command("git", "clone", "--quiet", "--depth", "1", "--branch", version, upstreamRepo, destination)
	if output, err := command.CombinedOutput(); err != nil {
		return "", fmt.Errorf("clone official OpenShell %s: %w: %s", version, err, strings.TrimSpace(string(output)))
	}
	return destination, nil
}

func compare(opts options, release githubRelease, pinnedSource, candidateSource string) (driftReport, error) {
	pinnedInventory, err := protoInventory(pinnedSource)
	if err != nil {
		return driftReport{}, err
	}
	candidateInventory, err := protoInventory(candidateSource)
	if err != nil {
		return driftReport{}, err
	}
	api := compareInventories(pinnedInventory, candidateInventory)
	components, err := componentReport(opts, pinnedSource, candidateSource, release.TagName)
	if err != nil {
		return driftReport{}, err
	}
	generated := opts.generatedAt
	if generated == "" {
		generated = time.Now().UTC().Format(time.RFC3339)
	}
	review := opts.pinnedVersion != release.TagName || len(api.Added) > 0 || len(api.Removed) > 0
	for _, value := range components {
		review = review || value.Changed
	}
	return driftReport{
		SchemaVersion: reportSchema, GeneratedAt: generated,
		PinnedRelease: opts.pinnedVersion, LatestRelease: release.TagName, ReleaseURL: release.HTMLURL,
		ReleaseChanged: opts.pinnedVersion != release.TagName, Components: components,
		PublicAPI: api, ReviewRequired: review,
	}, nil
}

func componentReport(opts options, pinnedSource, candidateSource, tag string) (map[string]component, error) {
	pinnedSDK := moduleVersion(filepath.Join(opts.root, "go.mod"), "github.com/NVIDIA/OpenShell/sdk/go")
	candidateSDK := gitRevision(candidateSource)
	values := map[string][2]string{
		"gateway":    {opts.pinnedVersion, tag},
		"supervisor": {opts.pinnedVersion, tag},
		"helm":       {opts.pinnedVersion, tag},
		"cli":        {opts.pinnedVersion, tag},
		"sdk_go":     {pinnedSDK, candidateSDK},
		"ocsf":       {readTrimmed(filepath.Join(pinnedSource, "crates/openshell-ocsf/schemas/ocsf/v1.8.0/VERSION")), readOCSFVersion(candidateSource)},
	}
	components := make(map[string]component, len(values))
	for name, pair := range values {
		changed := pair[0] != pair[1]
		if name == "sdk_go" {
			changed = !sdkVersionMatchesCommit(pair[0], pair[1])
		}
		components[name] = component{Pinned: pair[0], Candidate: pair[1], Changed: changed}
	}
	if opts.skipRegistry {
		return components, nil
	}
	imageTag := strings.TrimPrefix(tag, "v")
	for _, name := range []string{"gateway", "supervisor", "helm"} {
		repository := name
		if name == "helm" {
			repository = "helm-chart"
		}
		ref := "ghcr.io/nvidia/openshell/" + repository + ":" + imageTag
		digest, err := registryDigest(ref)
		if err != nil {
			return nil, err
		}
		value := components[name]
		value.RegistryRef, value.RegistryDigest, value.RegistryChecked = ref, digest, true
		components[name] = value
	}
	return components, nil
}

func sdkVersionMatchesCommit(version, commit string) bool {
	parts := strings.Split(version, "-")
	if len(parts) < 3 || commit == "unknown" {
		return false
	}
	revision := parts[len(parts)-1]
	return len(revision) >= 12 && strings.HasPrefix(commit, revision)
}

func protoInventory(root string) ([]string, error) {
	files, err := filepath.Glob(filepath.Join(root, "proto", "*.proto"))
	if err != nil || len(files) == 0 {
		return nil, fmt.Errorf("find public proto files under %s", root)
	}
	servicePattern := regexp.MustCompile(`^\s*service\s+([A-Za-z0-9_]+)`)
	rpcPattern := regexp.MustCompile(`^\s*rpc\s+([A-Za-z0-9_]+)\s*\(([^)]*)\)\s*returns\s*\(([^)]*)\)`)
	messagePattern := regexp.MustCompile(`^\s*message\s+([A-Za-z0-9_]+)`)
	fieldPattern := regexp.MustCompile(`^\s*(?:optional\s+|repeated\s+)?([.A-Za-z0-9_<>]+)\s+([A-Za-z0-9_]+)\s*=\s*([0-9]+)\s*;`)
	var inventory []string
	for _, path := range files {
		file, openErr := os.Open(path)
		if openErr != nil {
			return nil, fmt.Errorf("open proto %s: %w", path, openErr)
		}
		service, message := "", ""
		rpcBuffer := ""
		scanner := bufio.NewScanner(file)
		for scanner.Scan() {
			line := scanner.Text()
			trimmed := strings.TrimSpace(line)
			if rpcBuffer != "" {
				rpcBuffer += " " + trimmed
				if match := rpcPattern.FindStringSubmatch(rpcBuffer); len(match) == 4 {
					inventory = append(inventory, fmt.Sprintf("rpc %s.%s (%s) returns (%s)", service, match[1], strings.TrimSpace(match[2]), strings.TrimSpace(match[3])))
					rpcBuffer = ""
				}
				continue
			}
			if match := servicePattern.FindStringSubmatch(line); len(match) == 2 {
				service, message = match[1], ""
				inventory = append(inventory, "service "+service)
			}
			if strings.HasPrefix(trimmed, "rpc ") {
				rpcBuffer = trimmed
				if match := rpcPattern.FindStringSubmatch(rpcBuffer); len(match) == 4 {
					inventory = append(inventory, fmt.Sprintf("rpc %s.%s (%s) returns (%s)", service, match[1], strings.TrimSpace(match[2]), strings.TrimSpace(match[3])))
					rpcBuffer = ""
				}
				continue
			}
			if match := messagePattern.FindStringSubmatch(line); len(match) == 2 {
				message, service = match[1], ""
				inventory = append(inventory, "message "+message)
			}
			if message != "" {
				if match := fieldPattern.FindStringSubmatch(line); len(match) == 4 {
					inventory = append(inventory, fmt.Sprintf("field %s.%s %s = %s", message, match[2], match[1], match[3]))
				}
			}
		}
		if rpcBuffer != "" {
			return nil, fmt.Errorf("incomplete RPC declaration in %s: %s", path, rpcBuffer)
		}
		closeErr := file.Close()
		if err := errors.Join(scanner.Err(), closeErr); err != nil {
			return nil, fmt.Errorf("scan proto %s: %w", path, err)
		}
	}
	slices.Sort(inventory)
	return slices.Compact(inventory), nil
}

func compareInventories(pinned, candidate []string) apiDiff {
	pinnedSet, candidateSet := make(map[string]struct{}, len(pinned)), make(map[string]struct{}, len(candidate))
	for _, entry := range pinned {
		pinnedSet[entry] = struct{}{}
	}
	for _, entry := range candidate {
		candidateSet[entry] = struct{}{}
	}
	result := apiDiff{RelevantRPCs: make(map[string]string, len(relevantRPCs))}
	for _, entry := range candidate {
		if _, ok := pinnedSet[entry]; !ok {
			result.Added = append(result.Added, entry)
		}
	}
	for _, entry := range pinned {
		if _, ok := candidateSet[entry]; !ok {
			result.Removed = append(result.Removed, entry)
		}
	}
	for _, rpc := range relevantRPCs {
		status := "absent"
		for _, entry := range candidate {
			if strings.HasPrefix(entry, "rpc ") && strings.Contains(entry, "."+rpc+" ") {
				status = "present"
				break
			}
		}
		result.RelevantRPCs[rpc] = status
	}
	return result
}

func registryDigest(ref string) (string, error) {
	parts := strings.SplitN(ref, ":", 2)
	if len(parts) != 2 || !strings.HasPrefix(parts[0], "ghcr.io/") {
		return "", fmt.Errorf("unsupported authoritative registry reference %q", ref)
	}
	repository := strings.TrimPrefix(parts[0], "ghcr.io/")
	tokenURL := "https://ghcr.io/token?service=ghcr.io&scope=" + url.QueryEscape("repository:"+repository+":pull")
	response, err := http.Get(tokenURL) // #nosec G107 -- fixed authoritative GHCR endpoint.
	if err != nil {
		return "", fmt.Errorf("request GHCR token for %s: %w", ref, err)
	}
	defer func() { _ = response.Body.Close() }()
	var token struct {
		Token string `json:"token"`
	}
	decodeErr := json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&token)
	if response.StatusCode != http.StatusOK || decodeErr != nil || token.Token == "" {
		return "", fmt.Errorf("GHCR token request failed for %s with HTTP %d", ref, response.StatusCode)
	}
	manifestURL := "https://ghcr.io/v2/" + repository + "/manifests/" + url.PathEscape(parts[1])
	request, err := http.NewRequest(http.MethodGet, manifestURL, nil)
	if err != nil {
		return "", fmt.Errorf("create GHCR manifest request for %s: %w", ref, err)
	}
	request.Header.Set("Authorization", "Bearer "+token.Token)
	request.Header.Set("Accept", strings.Join([]string{
		"application/vnd.oci.image.index.v1+json",
		"application/vnd.oci.image.manifest.v1+json",
		"application/vnd.docker.distribution.manifest.list.v2+json",
		"application/vnd.docker.distribution.manifest.v2+json",
	}, ", "))
	manifestResponse, err := (&http.Client{Timeout: 30 * time.Second}).Do(request)
	if err != nil {
		return "", fmt.Errorf("verify authoritative registry reference %s: %w", ref, err)
	}
	defer func() { _ = manifestResponse.Body.Close() }()
	if manifestResponse.StatusCode != http.StatusOK {
		return "", fmt.Errorf("authoritative registry reference %s returned HTTP %d", ref, manifestResponse.StatusCode)
	}
	digest := manifestResponse.Header.Get("Docker-Content-Digest")
	if !regexp.MustCompile(`^sha256:[0-9a-f]{64}$`).MatchString(digest) {
		return "", fmt.Errorf("registry reference %s returned invalid digest %q", ref, digest)
	}
	return digest, nil
}

func moduleVersion(path, module string) string {
	body, _ := os.ReadFile(path)
	match := regexp.MustCompile(`(?m)^\s*` + regexp.QuoteMeta(module) + `\s+(\S+)`).FindSubmatch(body)
	if len(match) == 2 {
		return string(match[1])
	}
	return "unknown"
}

func gitRevision(root string) string {
	command := exec.Command("git", "-c", "safe.directory=*", "-C", root, "rev-parse", "HEAD")
	output, err := command.Output()
	if err != nil {
		return "unknown"
	}
	return strings.TrimSpace(string(output))
}

func readOCSFVersion(root string) string {
	lib := readTrimmed(filepath.Join(root, "crates/openshell-ocsf/schemas/ocsf/v1.8.0/VERSION"))
	if lib != "unknown" {
		return lib
	}
	body, _ := os.ReadFile(filepath.Join(root, "crates/openshell-ocsf/src/lib.rs"))
	match := regexp.MustCompile(`OCSF_VERSION:\s*&str\s*=\s*"([^"]+)"`).FindSubmatch(body)
	if len(match) == 2 {
		return string(match[1])
	}
	return "unknown"
}

func readTrimmed(path string) string {
	body, err := os.ReadFile(path)
	if err != nil {
		return "unknown"
	}
	return strings.TrimSpace(string(body))
}

func writeJSON(path string, value any) error {
	body, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return fmt.Errorf("encode JSON report: %w", err)
	}
	return writeFile(path, append(body, '\n'))
}

func writeFile(path string, body []byte) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("create report directory: %w", err)
	}
	if err := os.WriteFile(path, body, 0o644); err != nil {
		return fmt.Errorf("write report %s: %w", path, err)
	}
	return nil
}

func markdown(report driftReport) string {
	var builder strings.Builder
	fmt.Fprintf(&builder, "# OpenShell upstream drift review\n\nGenerated: `%s`\n\nPinned release: `%s`  \nLatest official release: [`%s`](%s)  \nMaintainer review required: **%t**\n\n", report.GeneratedAt, report.PinnedRelease, report.LatestRelease, report.ReleaseURL, report.ReviewRequired)
	builder.WriteString("## Component drift\n\n| Component | Pinned | Candidate | Changed | Verified registry digest |\n|---|---|---|---:|---|\n")
	for _, name := range []string{"gateway", "supervisor", "helm", "cli", "sdk_go", "ocsf"} {
		value := report.Components[name]
		digest := "not checked"
		if value.RegistryChecked {
			digest = "`" + value.RegistryRef + "@" + value.RegistryDigest + "`"
		}
		fmt.Fprintf(&builder, "| %s | `%s` | `%s` | %t | %s |\n", name, value.Pinned, value.Candidate, value.Changed, digest)
	}
	builder.WriteString("\n## Relevant public RPCs in candidate\n\n")
	for _, rpc := range relevantRPCs {
		fmt.Fprintf(&builder, "- `%s`: %s\n", rpc, report.PublicAPI.RelevantRPCs[rpc])
	}
	builder.WriteString("\n## Added public proto symbols\n\n")
	writeEntries(&builder, report.PublicAPI.Added)
	builder.WriteString("\n## Removed public proto symbols\n\n")
	writeEntries(&builder, report.PublicAPI.Removed)
	builder.WriteString("\n## Required maintainer review\n\n1. Review every RPC/message change against receivers and policy reconciliation.\n2. Verify gateway and supervisor images plus Helm, CLI, SDK, and OCSF compatibility.\n3. Run real gateway and destination qualification before changing compatibility claims.\n4. Update pins only in a reviewed DCO-signed pull request. This report never updates or merges dependencies.\n")
	return builder.String()
}

func writeEntries(builder *strings.Builder, entries []string) {
	if len(entries) == 0 {
		builder.WriteString("None.\n")
		return
	}
	for _, entry := range entries {
		fmt.Fprintf(builder, "- `%s`\n", entry)
	}
}
