// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package watchsandboxreceiver

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"testing"

	dm "github.com/NVIDIA/OpenShell/sdk/go/proto/datamodelv1"
	pb "github.com/NVIDIA/OpenShell/sdk/go/proto/openshellv1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type policyRevisionAPI struct {
	pb.OpenShellClient
	mu       sync.Mutex
	requests []*pb.ListSandboxPoliciesRequest
	respond  func(*pb.ListSandboxPoliciesRequest) (*pb.ListSandboxPoliciesResponse, error)
}

func (a *policyRevisionAPI) ListSandboxPolicies(
	_ context.Context,
	request *pb.ListSandboxPoliciesRequest,
	_ ...grpc.CallOption,
) (*pb.ListSandboxPoliciesResponse, error) {
	a.mu.Lock()
	a.requests = append(a.requests, request)
	a.mu.Unlock()
	return a.respond(request)
}

func (a *policyRevisionAPI) snapshotRequests() []*pb.ListSandboxPoliciesRequest {
	a.mu.Lock()
	defer a.mu.Unlock()
	return append([]*pb.ListSandboxPoliciesRequest(nil), a.requests...)
}

func TestPolicyRevisionPaginationUsesOneRecordBoundaryProbe(t *testing.T) {
	t.Parallel()
	api := &policyRevisionAPI{}
	api.respond = func(request *pb.ListSandboxPoliciesRequest) (*pb.ListSandboxPoliciesResponse, error) {
		switch request.GetPageToken() {
		case "":
			return revisionPage("next", 3, 2, 1), nil
		case "next":
			return revisionResponse(), nil
		default:
			return nil, fmt.Errorf("unexpected page token %q", request.GetPageToken())
		}
	}
	client := testPolicyRevisionClient(api, 3)

	revisions, err := client.ListPolicyRevisions(context.Background(), "sandbox")
	if err != nil {
		t.Fatal(err)
	}
	assertRevisionVersions(t, revisions, 3, 2, 1)
	requests := api.snapshotRequests()
	if len(requests) != 2 || requests[0].GetPageSize() != 3 || requests[0].GetPageToken() != "" ||
		requests[1].GetPageSize() != 1 || requests[1].GetPageToken() != "next" {
		t.Fatalf("pagination requests=%#v, want bounded page and one-record probe", requests)
	}
}

func TestPolicyRevisionPaginationReportsOverflowAndRetainsBoundedNewestSet(t *testing.T) {
	t.Parallel()
	api := &policyRevisionAPI{}
	api.respond = func(request *pb.ListSandboxPoliciesRequest) (*pb.ListSandboxPoliciesResponse, error) {
		if request.GetPageToken() == "" {
			return revisionPage("next", 5, 4, 3), nil
		}
		return revisionResponse(2), nil
	}
	client := testPolicyRevisionClient(api, 3)

	revisions, err := client.ListPolicyRevisions(context.Background(), "sandbox")
	assertPaginationError(t, err, policyRevisionLimitExceeded, 3)
	assertRevisionVersions(t, revisions, 5, 4, 3)
}

func TestPolicyRevisionPaginationRejectsServerPageAboveRequestedLimit(t *testing.T) {
	t.Parallel()
	api := &policyRevisionAPI{respond: func(*pb.ListSandboxPoliciesRequest) (*pb.ListSandboxPoliciesResponse, error) {
		return revisionResponse(8, 7, 6, 5, 4), nil
	}}
	client := testPolicyRevisionClient(api, 3)

	revisions, err := client.ListPolicyRevisions(context.Background(), "sandbox")
	assertPaginationError(t, err, policyRevisionLimitExceeded, 3)
	assertRevisionVersions(t, revisions, 8, 7, 6)
}

func TestPolicyRevisionPaginationReturnsPartialEvidenceAfterLaterPageFailure(t *testing.T) {
	t.Parallel()
	firstPage := make([]uint32, policyRevisionPageSize)
	for index := range firstPage {
		firstPage[index] = policyRevisionPageSize + 1 - uint32(index)
	}
	api := &policyRevisionAPI{}
	api.respond = func(request *pb.ListSandboxPoliciesRequest) (*pb.ListSandboxPoliciesResponse, error) {
		if request.GetPageToken() == "" {
			return revisionPage("next", firstPage...), nil
		}
		return nil, status.Error(codes.Unavailable, "injected later-page failure")
	}
	client := testPolicyRevisionClient(api, policyRevisionPageSize+10)

	revisions, err := client.ListPolicyRevisions(context.Background(), "sandbox")
	assertPaginationError(t, err, policyRevisionPaginationRead, int(policyRevisionPageSize))
	if !errors.Is(err, status.Error(codes.Unavailable, "injected later-page failure")) && status.Code(err) != codes.Unavailable {
		t.Fatalf("pagination error lost gRPC status: %v", err)
	}
	if len(revisions) != int(policyRevisionPageSize) {
		t.Fatalf("partial revisions=%d, want %d", len(revisions), policyRevisionPageSize)
	}
}

func TestPolicyRevisionPaginationDetectsDuplicateAndAscendingVersions(t *testing.T) {
	t.Parallel()
	for name, versions := range map[string][]uint32{
		"duplicate": {5, 4, 4, 3},
		"ascending": {5, 4, 6, 3},
		"zero":      {5, 4, 0, 3},
	} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			api := &policyRevisionAPI{respond: func(*pb.ListSandboxPoliciesRequest) (*pb.ListSandboxPoliciesResponse, error) {
				return revisionResponse(versions...), nil
			}}
			client := testPolicyRevisionClient(api, 10)
			revisions, err := client.ListPolicyRevisions(context.Background(), "sandbox")
			assertPaginationError(t, err, policyRevisionPaginationDrift, 2)
			assertRevisionVersions(t, revisions, 5, 4)
		})
	}
}

func TestPolicyRevisionAccumulatorNeverRetainsPastConfiguredLimit(t *testing.T) {
	t.Parallel()
	page := make([]*pb.SandboxPolicyRevision, 100000)
	for index := range page {
		page[index] = &pb.SandboxPolicyRevision{Version: uint32(len(page) - index)}
	}
	accumulator := newPolicyRevisionAccumulator(10)
	err := accumulator.add(page, 10)
	assertPaginationError(t, err, policyRevisionLimitExceeded, 10)
	if len(accumulator.revisions) != 10 || cap(accumulator.revisions) > int(policyRevisionPageSize) {
		t.Fatalf("bounded accumulator len=%d cap=%d", len(accumulator.revisions), cap(accumulator.revisions))
	}
}

func FuzzPolicyRevisionAccumulator(f *testing.F) {
	f.Add([]byte{5, 4, 3}, uint8(3), uint8(3))
	f.Add([]byte{5, 4, 4}, uint8(10), uint8(10))
	f.Add([]byte{5, 4, 3, 2}, uint8(2), uint8(2))
	f.Fuzz(func(t *testing.T, encoded []byte, limitByte, requestedByte uint8) {
		limit := uint32(limitByte%64) + 1
		requested := uint32(requestedByte%64) + 1
		page := make([]*pb.SandboxPolicyRevision, len(encoded))
		for index, version := range encoded {
			page[index] = &pb.SandboxPolicyRevision{Version: uint32(version)}
		}
		accumulator := newPolicyRevisionAccumulator(limit)
		err := accumulator.add(page, requested)
		if len(accumulator.revisions) > int(limit) || len(accumulator.revisions) > int(requested) {
			t.Fatalf("retained %d records for limit=%d requested=%d", len(accumulator.revisions), limit, requested)
		}
		for index := 1; index < len(accumulator.revisions); index++ {
			if accumulator.revisions[index].GetVersion() >= accumulator.revisions[index-1].GetVersion() {
				t.Fatalf("accepted non-descending revisions with err=%v", err)
			}
		}
	})
}

func BenchmarkPolicyRevisionAccumulator(b *testing.B) {
	page := make([]*pb.SandboxPolicyRevision, policyRevisionPageSize)
	for index := range page {
		page[index] = &pb.SandboxPolicyRevision{Version: policyRevisionPageSize - uint32(index)}
	}
	b.ReportAllocs()
	for range b.N {
		accumulator := newPolicyRevisionAccumulator(policyRevisionPageSize)
		if err := accumulator.add(page, policyRevisionPageSize); err != nil {
			b.Fatal(err)
		}
	}
}

func testPolicyRevisionClient(api pb.OpenShellClient, maxRevisions uint32) *grpcGatewayClient {
	config := createDefaultConfig().(*Config)
	config.PolicyReconciliation.MaxRevisions = maxRevisions
	return &grpcGatewayClient{api: api, config: config}
}

func revisionResponse(versions ...uint32) *pb.ListSandboxPoliciesResponse {
	revisions := make([]*pb.SandboxPolicyRevision, 0, len(versions))
	for _, version := range versions {
		revisions = append(revisions, &pb.SandboxPolicyRevision{Version: version, PolicyHash: fmt.Sprintf("sha256:%d", version)})
	}
	return &pb.ListSandboxPoliciesResponse{Revisions: revisions}
}

func assertRevisionVersions(t *testing.T, revisions []*pb.SandboxPolicyRevision, want ...uint32) {
	t.Helper()
	if len(revisions) != len(want) {
		t.Fatalf("revision count=%d, want %d", len(revisions), len(want))
	}
	for index, revision := range revisions {
		if revision.GetVersion() != want[index] {
			t.Fatalf("revision[%d]=%d, want %d", index, revision.GetVersion(), want[index])
		}
	}
}

func assertPaginationError(t *testing.T, err error, reason string, retained int) {
	t.Helper()
	var paginationErr *policyRevisionPaginationError
	if !errors.As(err, &paginationErr) || paginationErr.Reason != reason || paginationErr.Retained != retained {
		t.Fatalf("pagination error=%#v, want reason=%q retained=%d", err, reason, retained)
	}
}

func revisionPage(token string, versions ...uint32) *pb.ListSandboxPoliciesResponse {
	response := revisionResponse(versions...)
	response.NextPageToken = token
	return response
}

func TestPolicyRevisionPaginationFollowsShortPagesAndRejectsCycles(t *testing.T) {
	for _, cycle := range []bool{false, true} {
		t.Run(fmt.Sprint(cycle), func(t *testing.T) {
			api := &policyRevisionAPI{}
			api.respond = func(request *pb.ListSandboxPoliciesRequest) (*pb.ListSandboxPoliciesResponse, error) {
				if request.GetSandbox() != "agent" || request.GetWorkspaceScope().GetWorkspace() != "default" {
					t.Fatalf("scope lost: %v", request)
				}
				if request.GetPageToken() == "" {
					return revisionPage("opaque-next", 3), nil
				}
				if request.GetPageToken() != "opaque-next" {
					t.Fatalf("token changed: %v", request)
				}
				if cycle {
					return revisionPage("opaque-next", 2), nil
				}
				return revisionResponse(2), nil
			}
			revisions, err := testPolicyRevisionClient(api, 10).ListPolicyRevisions(context.Background(), "agent")
			if cycle {
				assertPaginationError(t, err, policyRevisionPaginationDrift, 2)
			} else if err != nil {
				t.Fatal(err)
			}
			assertRevisionVersions(t, revisions, 3, 2)
			if len(api.snapshotRequests()) != 2 {
				t.Fatal("pagination did not stop")
			}
		})
	}
}

type sandboxPaginationAPI struct {
	pb.OpenShellClient
	requests []*pb.ListSandboxesRequest
	cycle    bool
	watch    *pb.WatchSandboxRequest
}

func (a *sandboxPaginationAPI) ListSandboxes(_ context.Context, request *pb.ListSandboxesRequest, _ ...grpc.CallOption) (*pb.ListSandboxesResponse, error) {
	a.requests = append(a.requests, request)
	token := ""
	if request.GetPageToken() == "" || a.cycle {
		token = "opaque-next"
	}
	return &pb.ListSandboxesResponse{Sandboxes: []*pb.Sandbox{{Metadata: &dm.ObjectMeta{Id: fmt.Sprint(len(a.requests)), Name: "agent"}}}, NextPageToken: token}, nil
}
func (a *sandboxPaginationAPI) WatchSandbox(_ context.Context, request *pb.WatchSandboxRequest, _ ...grpc.CallOption) (grpc.ServerStreamingClient[pb.SandboxStreamEvent], error) {
	a.watch = request
	return nil, nil
}
func TestSandboxRequestsUseNamedWorkspaceAndOpaquePagination(t *testing.T) {
	for _, cycle := range []bool{false, true} {
		api := &sandboxPaginationAPI{cycle: cycle}
		config := createDefaultConfig().(*Config)
		config.Workspace = "team"
		config.LabelSelector = "owner=demo"
		client := &grpcGatewayClient{api: api, config: config}
		refs, err := client.List(context.Background())
		if (err != nil) != cycle {
			t.Fatalf("cycle=%t: %v", cycle, err)
		}
		if !cycle && len(refs) != 2 {
			t.Fatalf("short page lost: %v", refs)
		}
		if len(api.requests) != 2 || api.requests[1].GetPageToken() != "opaque-next" {
			t.Fatal("opaque pagination failed")
		}
		for _, request := range api.requests {
			if request.GetWorkspaceScope().GetWorkspace() != "team" || request.GetLabelSelector() != "owner=demo" {
				t.Fatalf("scope lost: %v", request)
			}
		}
		if _, err = client.Watch(context.Background(), "agent"); err != nil {
			t.Fatal(err)
		}
		if api.watch.GetSandbox() != "agent" || api.watch.GetWorkspaceScope().GetWorkspace() != "team" || api.watch.GetResumeAfterCursor() != "" {
			t.Fatalf("watch request=%v", api.watch)
		}
	}
}

func TestPolicyRevisionBoundaryProbeFollowsEmptyPageToken(t *testing.T) {
	api := &policyRevisionAPI{}
	api.respond = func(request *pb.ListSandboxPoliciesRequest) (*pb.ListSandboxPoliciesResponse, error) {
		switch request.GetPageToken() {
		case "":
			return revisionPage("empty-page", 3, 2), nil
		case "empty-page":
			return revisionPage("last-page"), nil
		case "last-page":
			return revisionResponse(1), nil
		default:
			t.Fatalf("unexpected token %q", request.GetPageToken())
			return nil, nil
		}
	}
	revisions, err := testPolicyRevisionClient(api, 2).ListPolicyRevisions(context.Background(), "sandbox")
	assertPaginationError(t, err, policyRevisionLimitExceeded, 2)
	assertRevisionVersions(t, revisions, 3, 2)
	if len(api.snapshotRequests()) != 3 {
		t.Fatal("empty page suppressed later revision")
	}
}
