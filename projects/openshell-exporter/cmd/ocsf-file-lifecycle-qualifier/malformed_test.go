// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"
	"testing"
)

func TestBuildRecordRetainsMalformedLineAsUnknownEvidence(t *testing.T) {
	body := []byte(`{"class_uid":4001,"unfinished":`)
	record, err := buildRecord(body, 0, 1, int64(len(body)+1), "/var/log/openshell-ocsf.2026-08-30.log", "/logs/openshell-ocsf.2026-08-30.log", "gateway", "default", "unknown", "gateway-ocsf-jsonl")
	if err != nil {
		t.Fatal(err)
	}
	if record.Type != "com.nvidia.openshell.ocsf.unknown.v1" {
		t.Fatalf("malformed line type=%q", record.Type)
	}
	if !strings.Contains(record.Source, "/sandboxes/unknown/") {
		t.Fatalf("malformed line source=%q", record.Source)
	}
	canonical, err := json.Marshal(string(body))
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(canonical)
	if record.BodySHA256 != "sha256:"+hex.EncodeToString(sum[:]) {
		t.Fatalf("malformed line body hash=%q", record.BodySHA256)
	}
	if !strings.HasPrefix(record.ID, "sha256:") || len(record.ID) != 71 {
		t.Fatalf("malformed line identity=%q", record.ID)
	}
}
