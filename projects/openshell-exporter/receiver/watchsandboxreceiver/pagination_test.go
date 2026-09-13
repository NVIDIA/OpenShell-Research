// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package watchsandboxreceiver

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"testing"

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
		switch request.GetOffset() {
		case 0:
			return revisionResponse(3, 2, 1), nil
		case 3:
			return revisionResponse(), nil
		default:
			return nil, fmt.Errorf("unexpected offset %d", request.GetOffset())
		}
	}
	client := testPolicyRevisionClient(api, 3)

	revisions, err := client.ListPolicyRevisions(context.Background(), "sandbox")
	if err != nil {
		t.Fatal(err)
	}
	assertRevisionVersions(t, revisions, 3, 2, 1)
	requests := api.snapshotRequests()
	if len(requests) != 2 || requests[0].GetLimit() != 3 || requests[0].GetOffset() != 0 ||
		requests[1].GetLimit() != 1 || requests[1].GetOffset() != 3 {
		t.Fatalf("pagination requests=%#v, want bounded page and one-record probe", requests)
	}
}

func TestPolicyRevisionPaginationReportsOverflowAndRetainsBoundedNewestSet(t *testing.T) {
	t.Parallel()
	api := &policyRevisionAPI{}
	api.respond = func(request *pb.ListSandboxPoliciesRequest) (*pb.ListSandboxPoliciesResponse, error) {
		if request.GetOffset() == 0 {
			return revisionResponse(5, 4, 3), nil
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
		if request.GetOffset() == 0 {
			return revisionResponse(firstPage...), nil
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
