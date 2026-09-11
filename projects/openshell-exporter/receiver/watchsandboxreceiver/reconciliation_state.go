// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package watchsandboxreceiver

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"sort"

	"go.opentelemetry.io/collector/pdata/plog"
)

const (
	legacyPolicyStateVersion   = 1
	policyStateVersion         = 2
	maxPolicyStateReadBytes    = 1 << 20
	maxPolicyStateEncodedBytes = 4 << 10
)

type policySequenceCheckpoint struct {
	Count  uint64 `json:"count,omitempty"`
	Digest string `json:"digest,omitempty"`
}

type policyReconciliationState struct {
	Version    int                      `json:"version"`
	DraftHash  string                   `json:"draft_hash,omitempty"`
	StatusHash string                   `json:"status_hash,omitempty"`
	Chunks     policySequenceCheckpoint `json:"chunks,omitempty"`
	History    policySequenceCheckpoint `json:"history,omitempty"`
	Revisions  policySequenceCheckpoint `json:"revisions,omitempty"`

	migrated bool
}

type legacyPolicyReconciliationState struct {
	Version        int               `json:"version"`
	DraftHash      string            `json:"draft_hash,omitempty"`
	StatusHash     string            `json:"status_hash,omitempty"`
	ChunkHashes    map[string]string `json:"chunk_hashes,omitempty"`
	HistoryHashes  map[string]string `json:"history_hashes,omitempty"`
	RevisionHashes map[string]string `json:"revision_hashes,omitempty"`
}

type policySequenceEvent struct {
	logs  plog.Logs
	hash  string
	order int64
	tie   string
}

func newPolicySequenceEvent(logs plog.Logs, order int64, tie string) (policySequenceEvent, error) {
	hash := policyEventHash(logs)
	if hash == "" {
		return policySequenceEvent{}, errors.New("policy conversion produced no event")
	}
	return policySequenceEvent{logs: logs, hash: hash, order: order, tie: tie}, nil
}

func canonicalizePolicySequence(events []policySequenceEvent) {
	sort.SliceStable(events, func(i, j int) bool {
		if events[i].order != events[j].order {
			return events[i].order < events[j].order
		}
		if events[i].tie != events[j].tie {
			return events[i].tie < events[j].tie
		}
		return events[i].hash < events[j].hash
	})
}

func policySequenceDigest(events []policySequenceEvent) string {
	hasher := sha256.New()
	var length [8]byte
	for _, event := range events {
		binary.BigEndian.PutUint64(length[:], uint64(len(event.hash)))
		_, _ = hasher.Write(length[:])
		_, _ = hasher.Write([]byte(event.hash))
	}
	return hex.EncodeToString(hasher.Sum(nil))
}

func policySequenceState(events []policySequenceEvent) policySequenceCheckpoint {
	return policySequenceCheckpoint{
		Count:  uint64(len(events)),
		Digest: policySequenceDigest(events),
	}
}

// policySequencePlan returns the first event that must be delivered. When a
// previously observed canonical prefix still matches, only its suffix is new.
// A mismatch is never guessed around: the complete current snapshot is replayed.
func policySequencePlan(previous policySequenceCheckpoint, events []policySequenceEvent) (int, policySequenceCheckpoint, bool) {
	next := policySequenceState(events)
	if previous.Count == 0 && previous.Digest == "" {
		return 0, next, false
	}
	if previous.Count > uint64(len(events)) {
		return 0, next, true
	}
	prefixLength := int(previous.Count)
	if policySequenceDigest(events[:prefixLength]) != previous.Digest {
		return 0, next, true
	}
	return prefixLength, next, false
}

// policySnapshotPlan deduplicates a complete mutable snapshot. A changed
// snapshot is replayed in full; unlike append-only history, it has no suffix
// continuity claim.
func policySnapshotPlan(previous policySequenceCheckpoint, events []policySequenceEvent) (int, policySequenceCheckpoint) {
	next := policySequenceState(events)
	if previous == next {
		return len(events), next
	}
	return 0, next
}

func decodePolicyState(encoded []byte) (*policyReconciliationState, error) {
	if len(encoded) == 0 {
		return newPolicyState(), nil
	}
	if len(encoded) > maxPolicyStateReadBytes {
		return nil, fmt.Errorf("policy reconciliation state is %d bytes; maximum accepted size is %d", len(encoded), maxPolicyStateReadBytes)
	}
	var header struct {
		Version int `json:"version"`
	}
	if err := json.Unmarshal(encoded, &header); err != nil {
		return nil, fmt.Errorf("decode policy reconciliation state: %w", err)
	}
	switch header.Version {
	case policyStateVersion:
		var state policyReconciliationState
		if err := json.Unmarshal(encoded, &state); err != nil {
			return nil, fmt.Errorf("decode policy reconciliation state: %w", err)
		}
		if err := state.validate(); err != nil {
			return nil, err
		}
		return &state, nil
	case legacyPolicyStateVersion:
		var legacy legacyPolicyReconciliationState
		if err := json.Unmarshal(encoded, &legacy); err != nil {
			return nil, fmt.Errorf("decode legacy policy reconciliation state: %w", err)
		}
		if err := legacy.validate(); err != nil {
			return nil, err
		}
		return &policyReconciliationState{
			Version:    policyStateVersion,
			DraftHash:  legacy.DraftHash,
			StatusHash: legacy.StatusHash,
			migrated:   true,
		}, nil
	default:
		return nil, fmt.Errorf("unsupported policy reconciliation state version %d", header.Version)
	}
}

func encodePolicyState(state *policyReconciliationState) ([]byte, error) {
	if state == nil {
		return nil, errors.New("policy reconciliation state is nil")
	}
	state.Version = policyStateVersion
	if err := state.validate(); err != nil {
		return nil, err
	}
	encoded, err := json.Marshal(state)
	if err != nil {
		return nil, err
	}
	if len(encoded) > maxPolicyStateEncodedBytes {
		return nil, fmt.Errorf("encoded policy reconciliation state is %d bytes; maximum is %d", len(encoded), maxPolicyStateEncodedBytes)
	}
	return encoded, nil
}

func newPolicyState() *policyReconciliationState {
	return &policyReconciliationState{Version: policyStateVersion}
}

func (s *policyReconciliationState) validate() error {
	if s.Version != policyStateVersion {
		return fmt.Errorf("unsupported policy reconciliation state version %d", s.Version)
	}
	for name, value := range map[string]string{
		"draft_hash":       s.DraftHash,
		"status_hash":      s.StatusHash,
		"chunks.digest":    s.Chunks.Digest,
		"history.digest":   s.History.Digest,
		"revisions.digest": s.Revisions.Digest,
	} {
		if value != "" && !isPolicyHash(value) {
			return fmt.Errorf("invalid policy reconciliation state %s", name)
		}
	}
	for name, sequence := range map[string]policySequenceCheckpoint{
		"chunks":    s.Chunks,
		"history":   s.History,
		"revisions": s.Revisions,
	} {
		if sequence.Count > 0 && sequence.Digest == "" {
			return fmt.Errorf("invalid policy reconciliation state %s: count requires digest", name)
		}
	}
	return nil
}

func (s *legacyPolicyReconciliationState) validate() error {
	for name, value := range map[string]string{
		"draft_hash":  s.DraftHash,
		"status_hash": s.StatusHash,
	} {
		if value != "" && !isPolicyHash(value) {
			return fmt.Errorf("invalid legacy policy reconciliation state %s", name)
		}
	}
	for name, hashes := range map[string]map[string]string{
		"chunk_hashes":    s.ChunkHashes,
		"history_hashes":  s.HistoryHashes,
		"revision_hashes": s.RevisionHashes,
	} {
		for _, value := range hashes {
			if !isPolicyHash(value) {
				return fmt.Errorf("invalid legacy policy reconciliation state %s", name)
			}
		}
	}
	return nil
}

func isPolicyHash(value string) bool {
	if len(value) != sha256.Size*2 {
		return false
	}
	decoded, err := hex.DecodeString(value)
	return err == nil && len(decoded) == sha256.Size && value == hex.EncodeToString(decoded)
}
