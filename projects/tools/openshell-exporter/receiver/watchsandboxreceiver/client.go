// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package watchsandboxreceiver

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"net/url"
	"os"
	"strings"

	pb "github.com/NVIDIA/OpenShell/sdk/go/proto/openshellv1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
)

type sandboxRef struct {
	ID   string
	Name string
}

type eventStream interface {
	Recv() (*pb.SandboxStreamEvent, error)
}

type gatewayClient interface {
	List(context.Context) ([]sandboxRef, error)
	Watch(context.Context, string) (eventStream, error)
	GatewayVersion(context.Context) (string, error)
	GetDraft(context.Context, string) (*pb.GetDraftPolicyResponse, error)
	GetDraftHistory(context.Context, string) (*pb.GetDraftHistoryResponse, error)
	GetPolicyStatus(context.Context, string) (*pb.GetSandboxPolicyStatusResponse, error)
	ListPolicyRevisions(context.Context, string) ([]*pb.SandboxPolicyRevision, error)
	Close() error
}

type clientFactory func(*Config) (gatewayClient, error)

type grpcGatewayClient struct {
	api    pb.OpenShellClient
	conn   *grpc.ClientConn
	config *Config
}

const policyRevisionPageSize = uint32(1000)

const (
	policyRevisionLimitExceeded   = "source_limit_exceeded"
	policyRevisionPaginationDrift = "pagination_drift"
	policyRevisionPaginationRead  = "pagination_read_failed"
)

type policyRevisionPaginationError struct {
	Reason          string
	Retained        int
	ObservedAtLeast int
	Limit           uint32
	Cause           error
}

func (e *policyRevisionPaginationError) Error() string {
	switch e.Reason {
	case policyRevisionLimitExceeded:
		return fmt.Sprintf("policy revisions exceed configured maximum: retained %d, observed at least %d, maximum %d", e.Retained, e.ObservedAtLeast, e.Limit)
	case policyRevisionPaginationDrift:
		return fmt.Sprintf("policy revision pagination order changed after %d retained records", e.Retained)
	case policyRevisionPaginationRead:
		return fmt.Sprintf("policy revision pagination failed after %d retained records: %v", e.Retained, e.Cause)
	default:
		return fmt.Sprintf("policy revision pagination incomplete after %d retained records", e.Retained)
	}
}

func (e *policyRevisionPaginationError) Unwrap() error { return e.Cause }

func policyRevisionGapReason(err error) (string, bool) {
	var paginationErr *policyRevisionPaginationError
	if !errors.As(err, &paginationErr) {
		return "", false
	}
	return paginationErr.Reason, true
}

type policyRevisionAccumulator struct {
	limit       uint32
	revisions   []*pb.SandboxPolicyRevision
	lastVersion uint32
	haveLast    bool
}

func newPolicyRevisionAccumulator(limit uint32) *policyRevisionAccumulator {
	return &policyRevisionAccumulator{
		limit:     limit,
		revisions: make([]*pb.SandboxPolicyRevision, 0, min(int(limit), int(policyRevisionPageSize))),
	}
}

func (a *policyRevisionAccumulator) add(page []*pb.SandboxPolicyRevision, requested uint32) error {
	retainedBefore := len(a.revisions)
	accepted := min(len(page), int(requested))
	for _, revision := range page[:accepted] {
		if len(a.revisions) >= int(a.limit) {
			return a.limitError(retainedBefore + len(page))
		}
		version := revision.GetVersion()
		if revision == nil || version == 0 || (a.haveLast && version >= a.lastVersion) {
			return &policyRevisionPaginationError{
				Reason:   policyRevisionPaginationDrift,
				Retained: len(a.revisions),
				Limit:    a.limit,
			}
		}
		a.revisions = append(a.revisions, revision)
		a.lastVersion = version
		a.haveLast = true
	}
	if len(page) > int(requested) {
		return a.limitError(retainedBefore + len(page))
	}
	return nil
}

func (a *policyRevisionAccumulator) limitError(observedAtLeast int) error {
	return &policyRevisionPaginationError{
		Reason:          policyRevisionLimitExceeded,
		Retained:        len(a.revisions),
		ObservedAtLeast: observedAtLeast,
		Limit:           a.limit,
	}
}

func newGRPCGatewayClient(config *Config) (gatewayClient, error) {
	if err := config.Validate(); err != nil {
		return nil, fmt.Errorf("validate OpenShell gRPC client config: %w", err)
	}
	parsed, err := url.Parse(config.Endpoint)
	if err != nil {
		return nil, fmt.Errorf("parse endpoint: %w", err)
	}
	options := make([]grpc.DialOption, 0, 2)
	if parsed.Scheme == "http" {
		options = append(options, grpc.WithTransportCredentials(insecure.NewCredentials()))
	} else {
		tlsConfig, err := loadTLSConfig(config, parsed.Hostname())
		if err != nil {
			return nil, err
		}
		options = append(options, grpc.WithTransportCredentials(credentials.NewTLS(tlsConfig)))
	}
	token, err := configuredBearerToken(config)
	if err != nil {
		return nil, err
	}
	if token != "" {
		options = append(options, grpc.WithPerRPCCredentials(bearerCredentials{
			token: token,
		}))
	}
	connection, err := grpc.NewClient(parsed.Host, options...)
	if err != nil {
		return nil, fmt.Errorf("create OpenShell gRPC client: %w", err)
	}
	return &grpcGatewayClient{
		api:    pb.NewOpenShellClient(connection),
		conn:   connection,
		config: config,
	}, nil
}

func configuredBearerToken(config *Config) (string, error) {
	if config.TokenFile != "" {
		encoded, err := os.ReadFile(config.TokenFile)
		if err != nil {
			return "", fmt.Errorf("read OpenShell token file: %w", err)
		}
		token := strings.TrimSpace(string(encoded))
		if token == "" {
			return "", errors.New("OpenShell token file is empty")
		}
		return token, nil
	}
	if config.TokenEnv != "" {
		token := strings.TrimSpace(os.Getenv(config.TokenEnv))
		if token == "" {
			return "", errors.New("configured token_env is not set")
		}
		return token, nil
	}
	return "", nil
}

func loadTLSConfig(config *Config, serverName string) (*tls.Config, error) {
	if config.TLS.InsecureSkipVerify {
		return nil, errors.New("tls.insecure_skip_verify is not supported; configure tls.ca_file instead")
	}
	tlsConfig := &tls.Config{
		MinVersion: tls.VersionTLS12,
		ServerName: serverName,
	}
	if config.TLS.CAFile != "" {
		pem, err := os.ReadFile(config.TLS.CAFile)
		if err != nil {
			return nil, fmt.Errorf("read OpenShell CA: %w", err)
		}
		roots, err := x509.SystemCertPool()
		if err != nil {
			roots = x509.NewCertPool()
		}
		if !roots.AppendCertsFromPEM(pem) {
			return nil, errors.New("OpenShell CA file contains no certificates")
		}
		tlsConfig.RootCAs = roots
	}
	if config.TLS.CertFile != "" {
		certificate, err := tls.LoadX509KeyPair(config.TLS.CertFile, config.TLS.KeyFile)
		if err != nil {
			return nil, fmt.Errorf("load OpenShell client certificate: %w", err)
		}
		tlsConfig.Certificates = []tls.Certificate{certificate}
	}
	return tlsConfig, nil
}

func (c *grpcGatewayClient) List(ctx context.Context) ([]sandboxRef, error) {
	const pageSize = uint32(1000)
	result := make([]sandboxRef, 0)
	for offset := uint32(0); ; offset += pageSize {
		response, err := c.api.ListSandboxes(ctx, &pb.ListSandboxesRequest{
			Limit:         pageSize,
			Offset:        offset,
			LabelSelector: c.config.LabelSelector,
			Workspace:     c.config.Workspace,
		})
		if err != nil {
			return nil, err
		}
		for _, sandbox := range response.GetSandboxes() {
			metadata := sandbox.GetMetadata()
			if metadata == nil || metadata.GetId() == "" {
				continue
			}
			result = append(result, sandboxRef{
				ID:   metadata.GetId(),
				Name: metadata.GetName(),
			})
		}
		if len(response.GetSandboxes()) < int(pageSize) {
			return result, nil
		}
	}
}

func (c *grpcGatewayClient) Watch(
	ctx context.Context,
	sandboxID string,
) (eventStream, error) {
	return c.api.WatchSandbox(ctx, &pb.WatchSandboxRequest{
		Id:             sandboxID,
		FollowStatus:   true,
		FollowLogs:     true,
		FollowEvents:   true,
		LogTailLines:   c.config.LogTailLines,
		EventTail:      c.config.EventTail,
		StopOnTerminal: false,
		LogSources:     []string{"gateway", "sandbox"},
	})
}

func (c *grpcGatewayClient) GatewayVersion(ctx context.Context) (string, error) {
	response, err := c.api.GetGatewayInfo(ctx, &pb.GetGatewayInfoRequest{})
	if err != nil {
		return "", err
	}
	return response.GetGatewayVersion(), nil
}

func (c *grpcGatewayClient) GetDraft(ctx context.Context, sandboxName string) (*pb.GetDraftPolicyResponse, error) {
	return c.api.GetDraftPolicy(ctx, &pb.GetDraftPolicyRequest{
		Name:      sandboxName,
		Workspace: c.config.Workspace,
	})
}

func (c *grpcGatewayClient) GetDraftHistory(ctx context.Context, sandboxName string) (*pb.GetDraftHistoryResponse, error) {
	return c.api.GetDraftHistory(ctx, &pb.GetDraftHistoryRequest{
		Name:      sandboxName,
		Workspace: c.config.Workspace,
	})
}

func (c *grpcGatewayClient) GetPolicyStatus(ctx context.Context, sandboxName string) (*pb.GetSandboxPolicyStatusResponse, error) {
	return c.api.GetSandboxPolicyStatus(ctx, &pb.GetSandboxPolicyStatusRequest{
		Name:      sandboxName,
		Workspace: c.config.Workspace,
	})
}

func (c *grpcGatewayClient) ListPolicyRevisions(ctx context.Context, sandboxName string) ([]*pb.SandboxPolicyRevision, error) {
	accumulator := newPolicyRevisionAccumulator(c.config.PolicyReconciliation.MaxRevisions)
	for {
		if uint32(len(accumulator.revisions)) == accumulator.limit {
			response, err := c.api.ListSandboxPolicies(ctx, &pb.ListSandboxPoliciesRequest{
				Name:      sandboxName,
				Workspace: c.config.Workspace,
				Limit:     1,
				Offset:    uint32(len(accumulator.revisions)),
			})
			if err != nil {
				return accumulator.revisions, &policyRevisionPaginationError{
					Reason:   policyRevisionPaginationRead,
					Retained: len(accumulator.revisions),
					Limit:    accumulator.limit,
					Cause:    err,
				}
			}
			if len(response.GetRevisions()) == 0 {
				return accumulator.revisions, nil
			}
			return accumulator.revisions, accumulator.limitError(len(accumulator.revisions) + len(response.GetRevisions()))
		}
		remaining := accumulator.limit - uint32(len(accumulator.revisions))
		requestLimit := min(policyRevisionPageSize, remaining)
		response, err := c.api.ListSandboxPolicies(ctx, &pb.ListSandboxPoliciesRequest{
			Name:      sandboxName,
			Workspace: c.config.Workspace,
			Limit:     requestLimit,
			Offset:    uint32(len(accumulator.revisions)),
		})
		if err != nil {
			if len(accumulator.revisions) == 0 {
				return nil, err
			}
			return accumulator.revisions, &policyRevisionPaginationError{
				Reason:   policyRevisionPaginationRead,
				Retained: len(accumulator.revisions),
				Limit:    accumulator.limit,
				Cause:    err,
			}
		}
		page := response.GetRevisions()
		if err := accumulator.add(page, requestLimit); err != nil {
			return accumulator.revisions, err
		}
		if len(page) < int(requestLimit) {
			return accumulator.revisions, nil
		}
	}
}

func (c *grpcGatewayClient) Close() error {
	return c.conn.Close()
}

type bearerCredentials struct {
	token string
}

func (c bearerCredentials) GetRequestMetadata(
	context.Context,
	...string,
) (map[string]string, error) {
	return map[string]string{"authorization": "Bearer " + c.token}, nil
}

func (c bearerCredentials) RequireTransportSecurity() bool {
	return true
}
