// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package openshellprocessor

const (
	sourceHealthDisabled    = "disabled"
	sourceHealthUnobserved  = "unobserved"
	sourceHealthHealthy     = "healthy"
	sourceHealthGapReported = "gap_reported"
	sourceHealthUnavailable = "unavailable"
)

const (
	sourceHealthClassDisabled int64 = iota
	sourceHealthClassUnobserved
	sourceHealthClassHealthy
	sourceHealthClassGapReported
	sourceHealthClassUnavailable
)

type sourceCapabilitySummary struct {
	Source         string `json:"source"`
	Configured     bool   `json:"configured"`
	Observed       bool   `json:"observed"`
	Durability     string `json:"durability"`
	Health         string `json:"health"`
	LimitationCode string `json:"limitation_code"`
}

type sourceCapabilityDefinition struct {
	source         string
	profiles       []string
	durability     string
	limitationCode string
	available      bool
}

var sourceCapabilityDefinitions = []sourceCapabilityDefinition{
	{
		source: "ocsf_files", profiles: []string{"ocsf.file", "ocsf.forwarded"},
		durability:     "checkpointed_file",
		limitationCode: "persistent_upstream_file_and_checkpoint_required", available: true,
	},
	{
		source: "operational_files", profiles: []string{"openshell.log", "openshell.log.forwarded"},
		durability:     "checkpointed_file",
		limitationCode: "persistent_upstream_file_and_checkpoint_required", available: true,
	},
	{
		source: "watchsandbox", profiles: []string{"watchsandbox"},
		durability:     "non_resumable_stream",
		limitationCode: "no_resume_cursor_best_effort_tails", available: true,
	},
	{
		source: "policy_reconciliation", profiles: []string{"policy.reconciliation"},
		durability:     "checkpointed_api_snapshot",
		limitationCode: "current_state_read_without_event_cursor", available: true,
	},
	{
		source: "gateway_otlp", profiles: []string{"openshell.trace"},
		durability:     "sender_dependent",
		limitationCode: "sender_delivery_not_source_replay", available: true,
	},
	{
		source: "driver_otlp", profiles: []string{"openshell.trace"},
		durability:     "sender_dependent",
		limitationCode: "sender_delivery_not_source_replay", available: true,
	},
	{
		source: "relay_otlp", profiles: []string{"nemo_relay.trace"},
		durability:     "sender_dependent",
		limitationCode: "privacy_filtered_no_source_replay", available: true,
	},
	{
		source: "relay_files", profiles: []string{"nemo_relay.log"},
		durability:     "checkpointed_file",
		limitationCode: "persistent_upstream_file_and_checkpoint_required", available: true,
	},
	{
		source: "kubernetes_context", profiles: []string{"kubernetes.context"},
		durability:     "resource_version_checkpointed_api",
		limitationCode: "namespace_and_object_allow_list_only", available: true,
	},
	{
		source:         "watch_events",
		durability:     "unavailable",
		limitationCode: "public_rpc_absent", available: false,
	},
}

func sourceCapabilitySummaries(enabledProfiles []string) []sourceCapabilitySummary {
	enabled := make(map[string]struct{}, len(enabledProfiles))
	for _, profile := range enabledProfiles {
		enabled[profile] = struct{}{}
	}
	summaries := make([]sourceCapabilitySummary, 0, len(sourceCapabilityDefinitions))
	for _, definition := range sourceCapabilityDefinitions {
		configured := false
		for _, profile := range definition.profiles {
			if _, ok := enabled[profile]; ok {
				configured = true
				break
			}
		}
		health := sourceHealthDisabled
		if !definition.available {
			configured = false
			health = sourceHealthUnavailable
		} else if configured {
			health = sourceHealthUnobserved
		}
		summaries = append(summaries, sourceCapabilitySummary{
			Source: definition.source, Configured: configured, Observed: false,
			Durability: definition.durability, Health: health,
			LimitationCode: definition.limitationCode,
		})
	}
	return summaries
}

func sourceCapabilityLane(kind string) string {
	switch sourceProfile(kind) {
	case "ocsf.file", "ocsf.forwarded":
		return "ocsf_files"
	case "openshell.log", "openshell.log.forwarded":
		return "operational_files"
	case "watchsandbox":
		return "watchsandbox"
	case "policy.reconciliation":
		return "policy_reconciliation"
	case "kubernetes.context":
		return "kubernetes_context"
	case "nemo_relay.log":
		return "relay_files"
	default:
		return ""
	}
}

// boundedMetricSource prevents source-controlled acquisition kinds from becoming
// unbounded metric labels. Keep this list aligned with eventType; unsupported
// source records remain exportable, but their metric label fails closed.
func boundedMetricSource(kind string) string {
	switch kind {
	case "ocsf.file",
		"ocsf.forwarded",
		"sandbox.lifecycle",
		"gateway.log",
		"sandbox.log",
		"nemo_relay.log",
		"sandbox.file_log",
		"openshell.log.forwarded",
		"platform.event",
		"policy.draft_updated",
		"policy.draft.snapshot",
		"policy.draft.chunk",
		"policy.draft.history",
		"policy.status",
		"policy.revision",
		"policy.reconciliation.warning",
		"stream.warning",
		"source.capability",
		"kubernetes.context":
		return kind
	default:
		return "unsupported"
	}
}

func sourceCapabilityHealthClass(health string) int64 {
	switch health {
	case sourceHealthDisabled:
		return sourceHealthClassDisabled
	case sourceHealthUnobserved:
		return sourceHealthClassUnobserved
	case sourceHealthHealthy:
		return sourceHealthClassHealthy
	case sourceHealthGapReported:
		return sourceHealthClassGapReported
	case sourceHealthUnavailable:
		return sourceHealthClassUnavailable
	default:
		return sourceHealthClassUnavailable
	}
}

func sourceKindReportsGap(kind string) bool {
	return kind == "stream.warning" || kind == "policy.reconciliation.warning"
}
