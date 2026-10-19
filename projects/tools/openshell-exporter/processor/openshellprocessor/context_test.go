// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package openshellprocessor

import (
	"reflect"
	"strings"
	"testing"

	"go.uber.org/zap"
)

func TestSearchableOCSFContextPreservesFactsAndPrivacy(t *testing.T) {
	for _, kind := range []string{"ocsf.file", "ocsf.forwarded", "ocsf.gateway", "ocsf.supervisor"} {
		t.Run(kind, func(t *testing.T) {
			input := validOCSF()
			input["actor"] = map[string]any{"process": map[string]any{"name": "agent", "pid": int64(42), "uid": "proc-1", "cmd_line": "authorization=Bearer private-value"}, "user": map[string]any{"name": "operator", "uid": "user-1"}}
			input["api"] = map[string]any{"operation": "Connect", "service": map[string]any{"name": "inference"}}
			input["dst_endpoint"] = map[string]any{"domain": "private-host", "ip": "192.0.2.1", "port": int64(443)}
			input["file"] = map[string]any{"name": "audit.json", "path": "/audit.json", "uid": "file-1"}
			input["activity_name"] = "Open"
			input["status_id"], input["status"], input["status_detail"] = int64(2), "Failure", "source reason"
			input["action_id"], input["action"] = int64(2), "Denied"
			input["disposition_id"], input["disposition"] = int64(2), "Blocked"
			input["firewall_rule"] = map[string]any{"name": "network-egress", "type": "policy", "uid": "rule-1"}
			input["future_field"] = map[string]any{"keep": true, "password": "private-value"}
			cfg := createDefaultConfig().(*Config)
			cfg.Redaction.Keys = append(cfg.Redaction.Keys, "domain")
			p, err := newProcessor(cfg, zap.NewNop())
			if err != nil {
				t.Fatal(err)
			}
			logs := testLogs(t, input)
			record := onlyRecord(logs)
			record.Attributes().PutStr("openshell.acquisition.kind", kind)
			beforeID := stableID(cfg, record, kind, record.Body().AsRaw())
			if _, err := p.processLogs(t.Context(), logs); err != nil {
				t.Fatal(err)
			}
			got := record.Body().Map().AsRaw()
			want := map[string]any{
				"actor":   map[string]any{"process_name": "agent", "process_pid": int64(42), "process_uid": "proc-1", "user_name": "operator", "user_uid": "user-1"},
				"action":  map[string]any{"activity_id": input["activity_id"], "activity_name": "Open", "api_operation": "Connect"},
				"target":  map[string]any{"domain": redacted, "ip": "192.0.2.1", "port": int64(443), "file_name": "audit.json", "file_path": "/audit.json", "file_uid": "file-1", "api_service": "inference"},
				"outcome": map[string]any{"status_id": int64(2), "status": "Failure", "status_detail": "source reason", "action_id": int64(2), "action": "Denied", "disposition_id": int64(2), "disposition": "Blocked"},
				"policy":  map[string]any{"rule_name": "network-egress", "rule_type": "policy", "rule_uid": "rule-1"},
			}
			for key, expected := range want {
				if !reflect.DeepEqual(got[key], expected) {
					t.Fatalf("%s=%#v, want %#v", key, got[key], expected)
				}
			}
			original := got["original"].(map[string]any)
			if !reflect.DeepEqual(original["future_field"], map[string]any{"keep": true, "password": redacted}) {
				t.Fatalf("unknown source changed: %#v", original)
			}
			if original["actor"].(map[string]any)["process"].(map[string]any)["cmd_line"] == input["actor"].(map[string]any)["process"].(map[string]any)["cmd_line"] {
				t.Fatal("command line not redacted")
			}
			id, _ := record.Attributes().Get("event.id")
			if id.Str() != beforeID {
				t.Fatal("projection changed identity")
			}
		})
	}
}

func TestOCSFContextDoesNotInventOrCoerceFacts(t *testing.T) {
	for _, input := range []any{
		nil, "CONNECT denied", map[string]any{"message": "denied by policy"},
		map[string]any{"actor": []any{"agent"}, "dst_endpoint": map[string]any{"domain": true, "port": "443"}, "status_id": 1.5, "action": map[string]any{"value": "Denied"}, "firewall_rule": false},
		map[string]any{"actor": map[string]any{"process": map[string]any{"pid": int64(-1), "name": ""}}, "file": map[string]any{"path": []any{"/secret"}}},
	} {
		got := map[string]any{}
		addOCSFContext(got, input)
		if len(got) != 0 {
			t.Fatalf("invented context for %#v: %#v", input, got)
		}
	}
	logs := testLogs(t, map[string]any{"action": "Denied", "status": "Failure"})
	onlyRecord(logs).Attributes().PutStr("openshell.acquisition.kind", "sandbox.lifecycle")
	p, err := newProcessor(createDefaultConfig().(*Config), zap.NewNop())
	if err != nil {
		t.Fatal(err)
	}
	if _, err = p.processLogs(t.Context(), logs); err != nil {
		t.Fatal(err)
	}
	if _, ok := onlyRecord(logs).Body().Map().Get("outcome"); ok {
		t.Fatal("OCSF semantics applied to another source")
	}
}

func TestRelayCorrelationAliasesAndPrecedence(t *testing.T) {
	for _, sessionKey := range []string{"session.id", "nemo_relay.session.instance_id", "nemo_relay.session_id"} {
		input := map[string]any{"unmapped": map[string]any{sessionKey: "session-1", "nemo_relay.request_id": "request-1", "nemo_relay.tool_call_id": "tool-1"}}
		got := correlations(input, nil)
		for key, want := range map[string]string{"agent_session_id": "session-1", "request_id": "request-1", "tool_call_id": "tool-1"} {
			if got[key] != want {
				t.Fatalf("%s: %s=%#v", sessionKey, key, got[key])
			}
		}
	}
	got := correlations(map[string]any{"agent.session.id": "canonical", "nemo_relay.session_id": "alias", "unmapped": map[string]any{"agent.session.id": "nested"}}, nil)
	if got["agent_session_id"] != "canonical" {
		t.Fatalf("precedence: %#v", got)
	}
	got = correlations(map[string]any{"nemo_relay.session_id": []any{"invalid"}, "nemo_relay.request_id": map[string]any{"invalid": true}}, nil)
	if len(got) != 0 {
		t.Fatalf("non-scalar identifiers: %#v", got)
	}
	logs := testLogs(t, map[string]any{"nemo_relay.session_id": "private-session"})
	cfg := createDefaultConfig().(*Config)
	cfg.Redaction.Patterns = append(cfg.Redaction.Patterns, "private-session")
	p, err := newProcessor(cfg, zap.NewNop())
	if err != nil {
		t.Fatal(err)
	}
	if _, err = p.processLogs(t.Context(), logs); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(onlyRecord(logs).Body().AsString(), "private-session") {
		t.Fatal("alias bypassed redaction")
	}
}
