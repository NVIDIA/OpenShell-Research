// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package openshellprocessor

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"
	"go.uber.org/zap/zaptest/observer"
)

func TestSourceCapabilitySummariesCoverEveryProfile(t *testing.T) {
	t.Parallel()
	tests := []struct {
		profile string
		sources []string
	}{
		{profile: "ocsf.file", sources: []string{"ocsf_files"}},
		{profile: "ocsf.forwarded", sources: []string{"ocsf_files"}},
		{profile: "openshell.log", sources: []string{"operational_files"}},
		{profile: "openshell.log.forwarded", sources: []string{"operational_files"}},
		{profile: "watchsandbox", sources: []string{"watchsandbox"}},
		{profile: "policy.reconciliation", sources: []string{"policy_reconciliation"}},
		{profile: "kubernetes.context", sources: []string{"kubernetes_context"}},
		{profile: "nemo_relay.log", sources: []string{"relay_files"}},
		{profile: "nemo_relay.trace", sources: []string{"relay_otlp"}},
		{profile: "openshell.trace", sources: []string{"gateway_otlp", "driver_otlp"}},
	}
	for _, test := range tests {
		test := test
		t.Run(test.profile, func(t *testing.T) {
			t.Parallel()
			summaries := sourceCapabilitySummaries([]string{test.profile})
			configured := map[string]bool{}
			for _, summary := range summaries {
				if summary.Configured {
					configured[summary.Source] = true
					if summary.Observed || summary.Health != sourceHealthUnobserved {
						t.Fatalf("configured source %#v must start unobserved", summary)
					}
				}
			}
			if len(configured) != len(test.sources) {
				t.Fatalf("configured=%v, want %v", configured, test.sources)
			}
			for _, source := range test.sources {
				if !configured[source] {
					t.Fatalf("source %q was not configured by profile %q", source, test.profile)
				}
			}
		})
	}
}

func TestSourceCapabilitySummariesDistinguishDisabledAndUnavailable(t *testing.T) {
	t.Parallel()
	summaries := sourceCapabilitySummaries(nil)
	for _, summary := range summaries {
		if summary.Source == "watch_events" {
			if summary.Configured || summary.Observed || summary.Health != sourceHealthUnavailable ||
				summary.LimitationCode != "public_rpc_absent" {
				t.Fatalf("WatchEvents summary=%#v", summary)
			}
			continue
		}
		if summary.Configured || summary.Observed || summary.Health != sourceHealthDisabled {
			t.Fatalf("disabled summary=%#v", summary)
		}
	}
}

func TestSourceCapabilityKindMappingAndGapHealthAreBounded(t *testing.T) {
	t.Parallel()
	tests := []struct {
		kind   string
		source string
		gap    bool
	}{
		{kind: "ocsf.file", source: "ocsf_files"},
		{kind: "ocsf.forwarded", source: "ocsf_files"},
		{kind: "sandbox.file_log", source: "operational_files"},
		{kind: "openshell.log.forwarded", source: "operational_files"},
		{kind: "sandbox.lifecycle", source: "watchsandbox"},
		{kind: "stream.warning", source: "watchsandbox", gap: true},
		{kind: "policy.draft.chunk", source: "policy_reconciliation"},
		{kind: "policy.reconciliation.warning", source: "policy_reconciliation", gap: true},
		{kind: "kubernetes.context", source: "kubernetes_context"},
		{kind: "nemo_relay.log", source: "relay_files"},
	}
	for _, test := range tests {
		if got := sourceCapabilityLane(test.kind); got != test.source {
			t.Fatalf("kind %q source=%q, want %q", test.kind, got, test.source)
		}
		if got := sourceKindReportsGap(test.kind); got != test.gap {
			t.Fatalf("kind %q gap=%t, want %t", test.kind, got, test.gap)
		}
	}
	for health, want := range map[string]int64{
		sourceHealthDisabled: sourceHealthClassDisabled, sourceHealthUnobserved: sourceHealthClassUnobserved,
		sourceHealthHealthy: sourceHealthClassHealthy, sourceHealthGapReported: sourceHealthClassGapReported,
		sourceHealthUnavailable: sourceHealthClassUnavailable,
	} {
		if got := sourceCapabilityHealthClass(health); got != want {
			t.Fatalf("health %q class=%d, want %d", health, got, want)
		}
	}
}

func TestMetricSourceVocabularyIsBounded(t *testing.T) {
	t.Parallel()
	for _, kind := range []string{
		"ocsf.file", "ocsf.forwarded", "sandbox.lifecycle", "gateway.log", "sandbox.log",
		"nemo_relay.log", "sandbox.file_log", "openshell.log.forwarded", "platform.event",
		"policy.draft_updated", "policy.draft.snapshot", "policy.draft.chunk",
		"policy.draft.history", "policy.status", "policy.revision",
		"policy.reconciliation.warning", "stream.warning", "source.capability", "kubernetes.context",
	} {
		if got := boundedMetricSource(kind); got != kind {
			t.Fatalf("kind %q metric source=%q", kind, got)
		}
	}
	for _, kind := range []string{"", "unknown", "https://customer.example/secret", "sandbox-123"} {
		if got := boundedMetricSource(kind); got != "unsupported" {
			t.Fatalf("untrusted kind %q metric source=%q, want unsupported", kind, got)
		}
	}
}

func TestStartupSourceCapabilitySummaryExcludesDeploymentIdentifiers(t *testing.T) {
	t.Parallel()
	core, observed := observer.New(zapcore.InfoLevel)
	config := createDefaultConfig().(*Config)
	config.SourceProfiles = []string{"ocsf.file", "watchsandbox", "nemo_relay.trace"}
	config.GatewayID = "gateway-must-not-appear"
	config.SourceInstance = "source-instance-must-not-appear"
	processor, err := newProcessor(config, zap.New(core))
	if err != nil {
		t.Fatal(err)
	}
	if err := processor.start(context.Background(), nil); err != nil {
		t.Fatal(err)
	}
	entries := observed.AllUntimed()
	if len(entries) != 1 || entries[0].Message != "OpenShell source capability summary" {
		t.Fatalf("startup entries=%#v", entries)
	}
	encoded, err := json.Marshal(entries[0].ContextMap())
	if err != nil {
		t.Fatal(err)
	}
	text := string(encoded)
	for _, forbidden := range []string{"gateway-must-not-appear", "source-instance-must-not-appear", "credential", "token"} {
		if strings.Contains(strings.ToLower(text), strings.ToLower(forbidden)) {
			t.Fatalf("startup summary leaked %q: %s", forbidden, text)
		}
	}
	for _, required := range []string{`"schema_version":"1.0"`, `"source":"watch_events"`, `"health":"unavailable"`} {
		if !strings.Contains(text, required) {
			t.Fatalf("startup summary missing %q: %s", required, text)
		}
	}
}
