// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
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
	"strings"
	"time"

	pb "github.com/NVIDIA/OpenShell/sdk/go/proto/openshellv1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/reflect/protoreflect"
)

const reportSchemaVersion = "1.0"

var (
	commitPattern  = regexp.MustCompile(`^[0-9a-f]{40}$`)
	digestPattern  = regexp.MustCompile(`^.+@sha256:[0-9a-f]{64}$`)
	eventIDPattern = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
)

type options struct {
	endpoint              string
	workspace             string
	sandboxName           string
	sandboxID             string
	noDraftSandbox        string
	notFoundSandbox       string
	tokenFile             string
	unauthorizedTokenFile string
	caFile                string
	certFile              string
	keyFile               string
	allowInsecureHTTP     bool
	candidateCommit       string
	candidateClean        bool
	gatewayImage          string
	expectedGateway       string
	cloudEventsFile       string
	output                string
	timeout               time.Duration
	requireComplete       bool
}

type report struct {
	SchemaVersion      string            `json:"schema_version"`
	GeneratedAt        string            `json:"generated_at"`
	Complete           bool              `json:"complete"`
	Candidate          candidateEvidence `json:"candidate"`
	Gateway            gatewayEvidence   `json:"gateway"`
	Scope              requestScope      `json:"scope"`
	APIProbes          []probeResult     `json:"api_probes"`
	ExportEvidence     exportEvidence    `json:"export_evidence"`
	UnobservedVariants []string          `json:"unobserved_variants"`
	Limitations        []string          `json:"limitations"`
}

type candidateEvidence struct {
	Commit        string `json:"commit"`
	WorktreeClean bool   `json:"worktree_clean"`
	Bound         bool   `json:"bound"`
}

type gatewayEvidence struct {
	EndpointSecurity string `json:"endpoint_security"`
	Image            string `json:"image"`
	ExpectedVersion  string `json:"expected_version"`
	ObservedVersion  string `json:"observed_version,omitempty"`
	VersionMatched   bool   `json:"version_matched"`
}

type requestScope struct {
	Workspace   string `json:"workspace"`
	SandboxName string `json:"sandbox_name"`
	SandboxID   string `json:"sandbox_id"`
}

type probeResult struct {
	Name        string            `json:"name"`
	Expectation string            `json:"expectation"`
	Configured  bool              `json:"configured"`
	Matched     bool              `json:"matched"`
	Operations  []operationResult `json:"operations"`
}

type operationResult struct {
	Operation               string         `json:"operation"`
	Request                 map[string]any `json:"request"`
	StartedAt               string         `json:"started_at"`
	FinishedAt              string         `json:"finished_at"`
	GRPCCode                string         `json:"grpc_code"`
	Authorization           string         `json:"authorization"`
	Variant                 string         `json:"variant"`
	SensitiveFieldsExcluded int            `json:"sensitive_fields_excluded"`
	ResponseSHA256          string         `json:"response_sha256,omitempty"`
	Summary                 map[string]any `json:"summary,omitempty"`
}

type exportEvidence struct {
	Configured           bool                 `json:"configured"`
	MatchedSandboxEvents int                  `json:"matched_sandbox_events"`
	ExpectedTypes        []string             `json:"expected_types"`
	MissingTypes         []string             `json:"missing_types"`
	SensitiveKeyLeaks    int                  `json:"sensitive_key_leaks"`
	SensitiveValueLeaks  int                  `json:"sensitive_value_leaks"`
	Events               []cloudEventEvidence `json:"events"`
}

type cloudEventEvidence struct {
	Type            string `json:"type"`
	Source          string `json:"source"`
	ID              string `json:"id"`
	SourceTime      string `json:"source_time,omitempty"`
	ObservedTime    string `json:"observed_time"`
	Consistency     string `json:"consistency"`
	AcquisitionKind string `json:"acquisition_kind"`
}

type bearerCredentials struct{ token string }

func (c bearerCredentials) GetRequestMetadata(context.Context, ...string) (map[string]string, error) {
	return map[string]string{"authorization": "Bearer " + c.token}, nil
}

func (c bearerCredentials) RequireTransportSecurity() bool { return true }

func main() {
	opts := parseFlags()
	result, err := qualify(context.Background(), opts)
	if err != nil {
		fmt.Fprintln(os.Stderr, "policy API qualification:", err)
		os.Exit(1)
	}
	if err := writeReport(opts.output, result); err != nil {
		fmt.Fprintln(os.Stderr, "policy API qualification:", err)
		os.Exit(1)
	}
	fmt.Println(opts.output)
	if opts.requireComplete && !result.Complete {
		fmt.Fprintln(os.Stderr, "policy API qualification: report is incomplete")
		os.Exit(1)
	}
}

func parseFlags() options {
	var opts options
	flag.StringVar(&opts.endpoint, "endpoint", "", "OpenShell gateway HTTP(S) endpoint")
	flag.StringVar(&opts.workspace, "workspace", "default", "OpenShell workspace")
	flag.StringVar(&opts.sandboxName, "sandbox-name", "", "sandbox name with a draft")
	flag.StringVar(&opts.sandboxID, "sandbox-id", "", "sandbox ID used to select exported CloudEvents")
	flag.StringVar(&opts.noDraftSandbox, "no-draft-sandbox", "", "sandbox name expected to have no draft")
	flag.StringVar(&opts.notFoundSandbox, "not-found-sandbox", "", "sandbox name expected not to exist")
	flag.StringVar(&opts.tokenFile, "token-file", "", "primary bearer token file")
	flag.StringVar(&opts.unauthorizedTokenFile, "unauthorized-token-file", "", "bearer token file expected to be rejected")
	flag.StringVar(&opts.caFile, "ca-file", "", "gateway CA bundle")
	flag.StringVar(&opts.certFile, "cert-file", "", "gateway client certificate")
	flag.StringVar(&opts.keyFile, "key-file", "", "gateway client private key")
	flag.BoolVar(&opts.allowInsecureHTTP, "allow-insecure-http", false, "allow an explicitly recorded plaintext fixture")
	flag.StringVar(&opts.candidateCommit, "candidate-commit", "", "full candidate Git commit")
	flag.BoolVar(&opts.candidateClean, "candidate-clean", false, "assert the candidate worktree was clean")
	flag.StringVar(&opts.gatewayImage, "gateway-image", "", "gateway image with sha256 digest")
	flag.StringVar(&opts.expectedGateway, "expected-gateway-version", "0.0.113", "expected gateway version")
	flag.StringVar(&opts.cloudEventsFile, "cloudevents-file", "", "captured CloudEvents JSON or JSONL")
	flag.StringVar(&opts.output, "output", "", "0600 qualification report path in an existing 0700 directory")
	flag.DurationVar(&opts.timeout, "timeout", 15*time.Second, "per-RPC timeout")
	flag.BoolVar(&opts.requireComplete, "require-complete", false, "exit unsuccessfully unless every qualification gate matches")
	flag.Parse()
	return opts
}

func qualify(ctx context.Context, opts options) (report, error) {
	if err := validateOptions(opts); err != nil {
		return report{}, err
	}
	primary, conn, security, err := newClient(opts, opts.tokenFile)
	if err != nil {
		return report{}, err
	}
	defer func() { _ = conn.Close() }()

	versionCtx, cancel := context.WithTimeout(ctx, opts.timeout)
	info, versionErr := primary.GetGatewayInfo(versionCtx, &pb.GetGatewayInfoRequest{})
	cancel()
	observedVersion := ""
	if versionErr == nil {
		observedVersion = info.GetGatewayVersion()
	}

	primaryProbe, secrets := runProbe(ctx, primary, "draft_present", "authorized_draft_present", opts.workspace, opts.sandboxName, opts.timeout)
	noDraftProbe := unconfiguredProbe("no_draft", "authorized_no_draft")
	if opts.noDraftSandbox != "" {
		noDraftProbe, _ = runProbe(ctx, primary, "no_draft", "authorized_no_draft", opts.workspace, opts.noDraftSandbox, opts.timeout)
	}
	notFoundProbe := unconfiguredProbe("sandbox_not_found", "sandbox_not_found")
	if opts.notFoundSandbox != "" {
		notFoundProbe, _ = runProbe(ctx, primary, "sandbox_not_found", "sandbox_not_found", opts.workspace, opts.notFoundSandbox, opts.timeout)
	}
	unauthorizedProbe := unconfiguredProbe("unauthorized", "unauthorized")
	if opts.unauthorizedTokenFile != "" {
		unauthorized, unauthorizedConn, _, unauthorizedErr := newClient(opts, opts.unauthorizedTokenFile)
		if unauthorizedErr != nil {
			return report{}, unauthorizedErr
		}
		unauthorizedProbe, _ = runProbe(ctx, unauthorized, "unauthorized", "unauthorized", opts.workspace, opts.sandboxName, opts.timeout)
		_ = unauthorizedConn.Close()
	}

	exports, err := inspectCloudEvents(opts.cloudEventsFile, opts.sandboxID, secrets, expectedEventTypes(primaryProbe))
	if err != nil {
		return report{}, err
	}
	probes := []probeResult{primaryProbe, noDraftProbe, notFoundProbe, unauthorizedProbe}
	unobserved := unobservedVariants(probes)
	complete := opts.candidateClean && commitPattern.MatchString(opts.candidateCommit) &&
		digestPattern.MatchString(opts.gatewayImage) && versionErr == nil && observedVersion == opts.expectedGateway &&
		allProbesMatch(probes) && exports.Configured && len(exports.MissingTypes) == 0 &&
		exports.SensitiveKeyLeaks == 0 && exports.SensitiveValueLeaks == 0
	limitations := []string{
		"read-only polling snapshots have no event cursor",
		"unchanged snapshots may be suppressed by checkpointed reconciliation state",
		"server variants not produced by this run remain unobserved",
	}
	if versionErr != nil {
		limitations = append(limitations, "GetGatewayInfo failed: "+status.Code(versionErr).String())
	}
	return report{
		SchemaVersion:      reportSchemaVersion,
		GeneratedAt:        time.Now().UTC().Format(time.RFC3339Nano),
		Complete:           complete,
		Candidate:          candidateEvidence{Commit: opts.candidateCommit, WorktreeClean: opts.candidateClean, Bound: opts.candidateClean && commitPattern.MatchString(opts.candidateCommit)},
		Gateway:            gatewayEvidence{EndpointSecurity: security, Image: opts.gatewayImage, ExpectedVersion: opts.expectedGateway, ObservedVersion: observedVersion, VersionMatched: versionErr == nil && observedVersion == opts.expectedGateway},
		Scope:              requestScope{Workspace: opts.workspace, SandboxName: opts.sandboxName, SandboxID: opts.sandboxID},
		APIProbes:          probes,
		ExportEvidence:     exports,
		UnobservedVariants: unobserved,
		Limitations:        limitations,
	}, nil
}

func validateOptions(opts options) error {
	for name, value := range map[string]string{
		"endpoint": opts.endpoint, "workspace": opts.workspace, "sandbox-name": opts.sandboxName,
		"sandbox-id": opts.sandboxID, "candidate-commit": opts.candidateCommit,
		"gateway-image": opts.gatewayImage, "output": opts.output,
	} {
		if strings.TrimSpace(value) == "" {
			return fmt.Errorf("--%s is required", name)
		}
	}
	if !commitPattern.MatchString(opts.candidateCommit) {
		return errors.New("--candidate-commit must be 40 lowercase hexadecimal characters")
	}
	if !digestPattern.MatchString(opts.gatewayImage) {
		return errors.New("--gateway-image must include an immutable sha256 digest")
	}
	if opts.timeout <= 0 {
		return errors.New("--timeout must be positive")
	}
	if (opts.certFile == "") != (opts.keyFile == "") {
		return errors.New("--cert-file and --key-file must be configured together")
	}
	return validateOutputPath(opts.output)
}

func validateOutputPath(path string) error {
	directory := filepath.Dir(path)
	info, err := os.Stat(directory)
	if err != nil {
		return fmt.Errorf("inspect output directory: %w", err)
	}
	if !info.IsDir() || info.Mode().Perm() != 0o700 {
		return errors.New("output directory must exist and have mode 0700")
	}
	if _, err := os.Lstat(path); err == nil {
		return errors.New("output path already exists")
	} else if !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("inspect output path: %w", err)
	}
	return nil
}

func newClient(opts options, tokenFile string) (pb.OpenShellClient, *grpc.ClientConn, string, error) {
	parsed, err := url.Parse(opts.endpoint)
	if err != nil || !parsed.IsAbs() || parsed.Host == "" || (parsed.Path != "" && parsed.Path != "/") || parsed.RawQuery != "" || parsed.Fragment != "" || parsed.User != nil {
		return nil, nil, "", errors.New("endpoint must be an absolute HTTP(S) URL without user info, path, query, or fragment")
	}
	dialOptions := make([]grpc.DialOption, 0, 2)
	security := "tls"
	switch parsed.Scheme {
	case "https":
		tlsConfig, tlsErr := qualifierTLSConfig(opts, parsed.Hostname())
		if tlsErr != nil {
			return nil, nil, "", tlsErr
		}
		dialOptions = append(dialOptions, grpc.WithTransportCredentials(credentials.NewTLS(tlsConfig)))
	case "http":
		if !opts.allowInsecureHTTP {
			return nil, nil, "", errors.New("HTTP requires --allow-insecure-http")
		}
		if tokenFile != "" {
			return nil, nil, "", errors.New("bearer credentials are forbidden over HTTP")
		}
		security = "plaintext_fixture"
		dialOptions = append(dialOptions, grpc.WithTransportCredentials(insecure.NewCredentials()))
	default:
		return nil, nil, "", errors.New("endpoint must use HTTP or HTTPS")
	}
	if tokenFile != "" {
		token, tokenErr := readSecretFile(tokenFile)
		if tokenErr != nil {
			return nil, nil, "", tokenErr
		}
		dialOptions = append(dialOptions, grpc.WithPerRPCCredentials(bearerCredentials{token: token}))
	}
	conn, err := grpc.NewClient(parsed.Host, dialOptions...)
	if err != nil {
		return nil, nil, "", fmt.Errorf("create gateway client: %w", err)
	}
	return pb.NewOpenShellClient(conn), conn, security, nil
}

func qualifierTLSConfig(opts options, serverName string) (*tls.Config, error) {
	config := &tls.Config{MinVersion: tls.VersionTLS12, ServerName: serverName}
	if opts.caFile != "" {
		encoded, err := os.ReadFile(opts.caFile)
		if err != nil {
			return nil, fmt.Errorf("read CA file: %w", err)
		}
		roots, err := x509.SystemCertPool()
		if err != nil {
			roots = x509.NewCertPool()
		}
		if !roots.AppendCertsFromPEM(encoded) {
			return nil, errors.New("CA file contains no certificates")
		}
		config.RootCAs = roots
	}
	if opts.certFile != "" {
		certificate, err := tls.LoadX509KeyPair(opts.certFile, opts.keyFile)
		if err != nil {
			return nil, fmt.Errorf("load client certificate: %w", err)
		}
		config.Certificates = []tls.Certificate{certificate}
	}
	return config, nil
}

func readSecretFile(path string) (string, error) {
	encoded, err := os.ReadFile(path)
	if err != nil {
		return "", fmt.Errorf("read token file: %w", err)
	}
	token := strings.TrimSpace(string(encoded))
	if token == "" {
		return "", errors.New("token file is empty")
	}
	return token, nil
}

func runProbe(ctx context.Context, client pb.OpenShellClient, name, expectation, workspace, sandbox string, timeout time.Duration) (probeResult, []string) {
	probe := probeResult{Name: name, Expectation: expectation, Configured: true}
	secrets := make([]string, 0)
	operations := []struct {
		name string
		call func(context.Context) (proto.Message, error)
	}{
		{"openshell.v1.OpenShell/GetDraftPolicy", func(callCtx context.Context) (proto.Message, error) {
			return client.GetDraftPolicy(callCtx, &pb.GetDraftPolicyRequest{Name: sandbox, Workspace: workspace})
		}},
		{"openshell.v1.OpenShell/GetDraftHistory", func(callCtx context.Context) (proto.Message, error) {
			return client.GetDraftHistory(callCtx, &pb.GetDraftHistoryRequest{Name: sandbox, Workspace: workspace})
		}},
		{"openshell.v1.OpenShell/GetSandboxPolicyStatus", func(callCtx context.Context) (proto.Message, error) {
			return client.GetSandboxPolicyStatus(callCtx, &pb.GetSandboxPolicyStatusRequest{Name: sandbox, Workspace: workspace})
		}},
		{"openshell.v1.OpenShell/ListSandboxPolicies", func(callCtx context.Context) (proto.Message, error) {
			return client.ListSandboxPolicies(callCtx, &pb.ListSandboxPoliciesRequest{Name: sandbox, Workspace: workspace, Limit: 1000})
		}},
	}
	for _, operation := range operations {
		started := time.Now().UTC()
		callCtx, cancel := context.WithTimeout(ctx, timeout)
		response, err := operation.call(callCtx)
		cancel()
		finished := time.Now().UTC()
		result, discovered := summarizeOperation(operation.name, workspace, sandbox, started, finished, response, err)
		probe.Operations = append(probe.Operations, result)
		secrets = append(secrets, discovered...)
	}
	probe.Matched = probeMatches(probe)
	return probe, uniqueNonEmpty(secrets)
}

func unconfiguredProbe(name, expectation string) probeResult {
	return probeResult{Name: name, Expectation: expectation, Configured: false, Matched: false, Operations: []operationResult{}}
}

func summarizeOperation(operation, workspace, sandbox string, started, finished time.Time, response proto.Message, err error) (operationResult, []string) {
	code := status.Code(err)
	result := operationResult{
		Operation: operation,
		Request:   map[string]any{"workspace": workspace, "sandbox_name": sandbox, "global": false},
		StartedAt: started.Format(time.RFC3339Nano), FinishedAt: finished.Format(time.RFC3339Nano),
		GRPCCode: code.String(), Authorization: authorizationClass(code), Variant: errorVariant(code),
	}
	if err != nil {
		return result, nil
	}
	encoded, excluded, secrets, marshalErr := sanitizedProtoJSON(response)
	if marshalErr != nil {
		result.GRPCCode = codes.Internal.String()
		result.Variant = "qualification_error"
		return result, nil
	}
	sum := sha256.Sum256(encoded)
	result.ResponseSHA256 = "sha256:" + hex.EncodeToString(sum[:])
	result.SensitiveFieldsExcluded = excluded
	result.Summary, result.Variant = safeSummary(response)
	return result, secrets
}

func authorizationClass(code codes.Code) string {
	switch code {
	case codes.OK:
		return "authorized"
	case codes.PermissionDenied:
		return "denied"
	case codes.Unauthenticated:
		return "not_authenticated"
	default:
		return "not_determined"
	}
}

func errorVariant(code codes.Code) string {
	switch code {
	case codes.PermissionDenied, codes.Unauthenticated:
		return "unauthorized"
	case codes.NotFound:
		return "sandbox_not_found"
	case codes.Unimplemented:
		return "unavailable"
	case codes.DeadlineExceeded:
		return "timeout"
	default:
		return "rpc_error"
	}
}

func safeSummary(message proto.Message) (map[string]any, string) {
	switch response := message.(type) {
	case *pb.GetDraftPolicyResponse:
		variant := "draft_present"
		if response.GetDraftVersion() == 0 && len(response.GetChunks()) == 0 {
			variant = "no_draft"
		}
		return map[string]any{"draft_version": response.GetDraftVersion(), "chunk_count": len(response.GetChunks()), "last_analyzed_at_ms": response.GetLastAnalyzedAtMs()}, variant
	case *pb.GetDraftHistoryResponse:
		return map[string]any{"entry_count": len(response.GetEntries()), "latest_timestamp_ms": latestHistoryTime(response.GetEntries())}, collectionVariant(len(response.GetEntries()))
	case *pb.GetSandboxPolicyStatusResponse:
		revision := uint32(0)
		created := int64(0)
		if response.GetRevision() != nil {
			revision = response.GetRevision().GetVersion()
			created = response.GetRevision().GetCreatedAtMs()
		}
		variant := "policy_present"
		if response.GetActiveVersion() == 0 && revision == 0 {
			variant = "no_policy"
		}
		return map[string]any{"active_version": response.GetActiveVersion(), "revision": revision, "revision_created_at_ms": created}, variant
	case *pb.ListSandboxPoliciesResponse:
		latest, oldest := revisionRange(response.GetRevisions())
		return map[string]any{"revision_count": len(response.GetRevisions()), "latest_version": latest, "oldest_version": oldest}, collectionVariant(len(response.GetRevisions()))
	default:
		return map[string]any{}, "present"
	}
}

func latestHistoryTime(entries []*pb.DraftHistoryEntry) int64 {
	var latest int64
	for _, entry := range entries {
		if entry.GetTimestampMs() > latest {
			latest = entry.GetTimestampMs()
		}
	}
	return latest
}

func revisionRange(revisions []*pb.SandboxPolicyRevision) (uint32, uint32) {
	var latest, oldest uint32
	for _, revision := range revisions {
		version := revision.GetVersion()
		if version > latest {
			latest = version
		}
		if oldest == 0 || (version > 0 && version < oldest) {
			oldest = version
		}
	}
	return latest, oldest
}

func collectionVariant(length int) string {
	if length == 0 {
		return "empty"
	}
	return "present"
}

func sanitizedProtoJSON(message proto.Message) ([]byte, int, []string, error) {
	if message == nil {
		return nil, 0, nil, errors.New("nil response")
	}
	cloned := proto.Clone(message)
	excluded, secrets := removeSensitiveProtoFields(cloned.ProtoReflect())
	encoded, err := (protojson.MarshalOptions{UseProtoNames: true}).Marshal(cloned)
	return encoded, excluded, secrets, err
}

func removeSensitiveProtoFields(message protoreflect.Message) (int, []string) {
	excluded := 0
	secrets := make([]string, 0)
	message.Range(func(field protoreflect.FieldDescriptor, value protoreflect.Value) bool {
		if sensitiveName(string(field.Name())) || sensitiveName(field.JSONName()) {
			if field.Kind() == protoreflect.StringKind {
				secrets = append(secrets, value.String())
			}
			message.Clear(field)
			excluded++
			return true
		}
		switch {
		case field.IsList() && field.Kind() == protoreflect.MessageKind:
			list := value.List()
			for index := 0; index < list.Len(); index++ {
				count, nested := removeSensitiveProtoFields(list.Get(index).Message())
				excluded += count
				secrets = append(secrets, nested...)
			}
		case field.Kind() == protoreflect.MessageKind:
			count, nested := removeSensitiveProtoFields(value.Message())
			excluded += count
			secrets = append(secrets, nested...)
		}
		return true
	})
	return excluded, secrets
}

func sensitiveName(name string) bool {
	normalized := strings.ToLower(strings.ReplaceAll(name, "_", ""))
	return normalized == "reviewtoken"
}

func inspectCloudEvents(path, sandboxID string, secrets, expected []string) (exportEvidence, error) {
	result := exportEvidence{Configured: path != "", ExpectedTypes: expected, Events: []cloudEventEvidence{}}
	if path == "" {
		result.MissingTypes = append([]string(nil), expected...)
		return result, nil
	}
	file, err := os.Open(path)
	if err != nil {
		return result, fmt.Errorf("open CloudEvents evidence: %w", err)
	}
	defer func() { _ = file.Close() }()
	values, err := decodeJSONValues(file)
	if err != nil {
		return result, fmt.Errorf("decode CloudEvents evidence: %w", err)
	}
	seenTypes := make(map[string]bool)
	for _, value := range flattenJSONArrays(values) {
		object, ok := value.(map[string]any)
		if !ok || object["specversion"] != "1.0" {
			continue
		}
		encoded, _ := json.Marshal(object)
		result.SensitiveKeyLeaks += countSensitiveJSONKeys(object)
		for _, secret := range secrets {
			if secret != "" && strings.Contains(string(encoded), secret) {
				result.SensitiveValueLeaks++
			}
		}
		source, _ := object["source"].(string)
		subject, _ := object["subject"].(string)
		if subject != "sandboxes/"+sandboxID && !strings.Contains(source, "/sandboxes/"+url.PathEscape(sandboxID)+"/") {
			continue
		}
		typeName, _ := object["type"].(string)
		if !strings.HasPrefix(typeName, "com.nvidia.openshell.policy.") {
			continue
		}
		id, _ := object["id"].(string)
		if !eventIDPattern.MatchString(id) || source == "" {
			return result, fmt.Errorf("policy CloudEvent has invalid source or id")
		}
		data, _ := object["data"].(map[string]any)
		observed, _ := data["observed_time"].(string)
		acquisition, _ := data["acquisition"].(map[string]any)
		kind, _ := acquisition["kind"].(string)
		consistency, _ := acquisition["consistency"].(string)
		if observed == "" || kind == "" || consistency == "" {
			return result, fmt.Errorf("policy CloudEvent %s lacks observed_time, acquisition kind, or consistency", id)
		}
		sourceTime, _ := object["time"].(string)
		result.Events = append(result.Events, cloudEventEvidence{Type: typeName, Source: source, ID: id, SourceTime: sourceTime, ObservedTime: observed, Consistency: consistency, AcquisitionKind: kind})
		seenTypes[typeName] = true
	}
	result.MatchedSandboxEvents = len(result.Events)
	for _, expectedType := range expected {
		if !seenTypes[expectedType] {
			result.MissingTypes = append(result.MissingTypes, expectedType)
		}
	}
	sort.Slice(result.Events, func(i, j int) bool {
		if result.Events[i].Type == result.Events[j].Type {
			return result.Events[i].ID < result.Events[j].ID
		}
		return result.Events[i].Type < result.Events[j].Type
	})
	return result, nil
}

func decodeJSONValues(reader io.Reader) ([]any, error) {
	decoder := json.NewDecoder(reader)
	values := make([]any, 0)
	for {
		var value any
		if err := decoder.Decode(&value); errors.Is(err, io.EOF) {
			return values, nil
		} else if err != nil {
			return nil, err
		}
		values = append(values, value)
	}
}

func flattenJSONArrays(values []any) []any {
	result := make([]any, 0)
	for _, value := range values {
		if array, ok := value.([]any); ok {
			result = append(result, array...)
		} else {
			result = append(result, value)
		}
	}
	return result
}

func countSensitiveJSONKeys(value any) int {
	count := 0
	switch typed := value.(type) {
	case map[string]any:
		for key, nested := range typed {
			if sensitiveName(key) {
				count++
			}
			count += countSensitiveJSONKeys(nested)
		}
	case []any:
		for _, nested := range typed {
			count += countSensitiveJSONKeys(nested)
		}
	}
	return count
}

func expectedEventTypes(probe probeResult) []string {
	types := []string{"com.nvidia.openshell.policy.draft.snapshot.v1", "com.nvidia.openshell.policy.status.v1"}
	for _, operation := range probe.Operations {
		switch operation.Operation {
		case "openshell.v1.OpenShell/GetDraftPolicy":
			if numericSummary(operation.Summary, "chunk_count") > 0 {
				types = append(types, "com.nvidia.openshell.policy.draft.chunk.v1")
			}
		case "openshell.v1.OpenShell/GetDraftHistory":
			if numericSummary(operation.Summary, "entry_count") > 0 {
				types = append(types, "com.nvidia.openshell.policy.draft.history.v1")
			}
		case "openshell.v1.OpenShell/ListSandboxPolicies":
			if numericSummary(operation.Summary, "revision_count") > 0 {
				types = append(types, "com.nvidia.openshell.policy.revision.v1")
			}
		}
	}
	sort.Strings(types)
	return types
}

func numericSummary(summary map[string]any, key string) int64 {
	value, ok := summary[key]
	if !ok {
		return 0
	}
	switch typed := value.(type) {
	case int:
		return int64(typed)
	case int64:
		return typed
	case uint64:
		return int64(typed)
	default:
		return 0
	}
}

func probeMatches(probe probeResult) bool {
	if !probe.Configured || len(probe.Operations) != 4 {
		return false
	}
	for _, operation := range probe.Operations {
		switch probe.Expectation {
		case "authorized_draft_present":
			if operation.GRPCCode != codes.OK.String() || operation.Authorization != "authorized" {
				return false
			}
			if strings.HasSuffix(operation.Operation, "/GetDraftPolicy") && operation.Variant != "draft_present" {
				return false
			}
		case "authorized_no_draft":
			if operation.GRPCCode != codes.OK.String() || operation.Authorization != "authorized" {
				return false
			}
			if strings.HasSuffix(operation.Operation, "/GetDraftPolicy") && operation.Variant != "no_draft" {
				return false
			}
		case "sandbox_not_found":
			if operation.Variant != "sandbox_not_found" {
				return false
			}
		case "unauthorized":
			if operation.Variant != "unauthorized" {
				return false
			}
		default:
			return false
		}
	}
	return true
}

func allProbesMatch(probes []probeResult) bool {
	for _, probe := range probes {
		if !probe.Configured || !probe.Matched {
			return false
		}
	}
	return true
}

func unobservedVariants(probes []probeResult) []string {
	result := make([]string, 0)
	for _, probe := range probes {
		if !probe.Configured {
			result = append(result, probe.Name+":not_configured")
			continue
		}
		if !probe.Matched {
			result = append(result, probe.Name+":expectation_not_observed")
		}
	}
	sort.Strings(result)
	return result
}

func uniqueNonEmpty(values []string) []string {
	seen := make(map[string]struct{})
	result := make([]string, 0, len(values))
	for _, value := range values {
		if value == "" {
			continue
		}
		if _, ok := seen[value]; ok {
			continue
		}
		seen[value] = struct{}{}
		result = append(result, value)
	}
	return result
}

func writeReport(path string, value report) error {
	encoded, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return fmt.Errorf("encode report: %w", err)
	}
	temporary, err := os.CreateTemp(filepath.Dir(path), ".policy-qualification-*")
	if err != nil {
		return fmt.Errorf("create temporary report: %w", err)
	}
	temporaryPath := temporary.Name()
	defer func() { _ = os.Remove(temporaryPath) }()
	if err := temporary.Chmod(0o600); err != nil {
		_ = temporary.Close()
		return fmt.Errorf("protect temporary report: %w", err)
	}
	if _, err := temporary.Write(append(encoded, '\n')); err != nil {
		_ = temporary.Close()
		return fmt.Errorf("write temporary report: %w", err)
	}
	if err := temporary.Sync(); err != nil {
		_ = temporary.Close()
		return fmt.Errorf("sync temporary report: %w", err)
	}
	if err := temporary.Close(); err != nil {
		return fmt.Errorf("close temporary report: %w", err)
	}
	if err := os.Rename(temporaryPath, path); err != nil {
		return fmt.Errorf("publish report: %w", err)
	}
	return nil
}
