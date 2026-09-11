// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package openshellprocessor

import (
	"fmt"
	"net/url"
	"strconv"
	"strings"
	"time"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/plog"
)

func buildEnvelope(
	config *Config,
	record plog.LogRecord,
	kind string,
	observed pcommon.Timestamp,
	validation validationResult,
	redactionCount int,
	correlation map[string]any,
	original any,
	sourceAttributes any,
) map[string]any {
	attributes := record.Attributes()
	workspace := stringAttribute(record, "openshell.workspace", config.Workspace)
	sourceInstance := stringAttribute(
		record,
		"openshell.acquisition.source_instance",
		config.SourceInstance,
	)
	sandboxID := stringAttribute(
		record,
		"openshell.sandbox.id",
		sandboxIDFrom(original, config.DefaultSandboxID),
	)
	acquisition := map[string]any{
		"kind":            kind,
		"durability":      durability(kind),
		"source_instance": sourceInstance,
	}
	for _, item := range []struct {
		attr  string
		field string
	}{
		{attr: "log.file.path", field: "file_path"},
		{attr: "log.file.path_resolved", field: "file_path_resolved"},
		{attr: "log.file.record_offset", field: "record_offset"},
		{attr: "log.file.record_number", field: "record_number"},
		{attr: "openshell.acquisition.transport", field: "transport"},
		{attr: "openshell.acquisition.api_operation", field: "api_operation"},
		{attr: "openshell.acquisition.payload_type", field: "payload_type"},
		{attr: "openshell.acquisition.stream_resumable", field: "stream_resumable"},
		{attr: "openshell.acquisition.follow_status", field: "follow_status"},
		{attr: "openshell.acquisition.follow_logs", field: "follow_logs"},
		{attr: "openshell.acquisition.follow_events", field: "follow_events"},
		{attr: "openshell.acquisition.stop_on_terminal", field: "stop_on_terminal"},
		{attr: "openshell.acquisition.log_since_ms", field: "log_since_ms"},
		{attr: "openshell.acquisition.log_sources", field: "log_sources"},
		{attr: "openshell.acquisition.log_min_level", field: "log_min_level"},
		{attr: "openshell.acquisition.log_tail_lines", field: "log_tail_lines"},
		{attr: "openshell.acquisition.event_tail", field: "event_tail"},
		{attr: "openshell.acquisition.reconciliation_trigger", field: "reconciliation_trigger"},
		{attr: "openshell.acquisition.notification_draft_version", field: "notification_draft_version"},
		{attr: "openshell.acquisition.consistency", field: "consistency"},
	} {
		if value, ok := attributes.Get(item.attr); ok {
			acquisition[item.field] = value.AsRaw()
		}
	}
	if isFileEvidence(kind) {
		acquisition["transport"] = "file"
		acquisition["payload_type"] = "source_record"
	}
	acquisition["coverage"] = acquisitionCoverage(kind, attributes)
	switch {
	case isSourceCapability(kind):
		acquisition["transport"] = "otlp_http"
		acquisition["replay_limitations"] =
			"topology diagnostic is re-emitted with the same stable identity when the forwarder restarts"
	case isForwardedFile(kind):
		acquisition["transport"] = "otlp_http"
		acquisition["replay_limitations"] =
			"checkpoint and file ownership remain with the authorized forwarder"
	case isPolicyReconciliationKind(kind):
		acquisition["replay_limitations"] =
			"authoritative read snapshot with no event cursor; file_storage suppresses unchanged snapshots and downstream source+id deduplicates replay"
	case !isFileEvidence(kind) && !isKubernetesContext(kind):
		acquisition["replay_limitations"] =
			"non-resumable stream; configured tails reduce but do not eliminate gaps"
	}

	openshell := map[string]any{
		"gateway_id": config.GatewayID,
		"workspace":  workspace,
		"sandbox_id": sandboxID,
	}
	copyStringAttribute(
		attributes,
		openshell,
		"openshell.gateway.version",
		"gateway_version",
	)
	copyStringAttribute(
		attributes,
		openshell,
		"openshell.sandbox.name",
		"sandbox_name",
	)
	copyAttribute(attributes, openshell, "openshell.policy.version", "policy_version")
	copyAttribute(attributes, openshell, "openshell.policy.revision", "policy_revision")
	correlation = canonicalCorrelationContext(
		correlation,
		config.GatewayID,
		workspace,
		sandboxID,
		attributes,
	)

	security := map[string]any{
		"validation": map[string]any{
			"status": validation.Status,
			"errors": stringsToAny(validation.Errors),
		},
		"redaction": map[string]any{
			"profile_id":      config.Redaction.ProfileID,
			"profile_version": config.Redaction.Version,
			"applied":         redactionCount > 0,
			"count":           int64(redactionCount),
		},
	}
	if isOCSFEvidence(kind) {
		security["ocsf"] = ocsfIdentifiers(original)
	}

	envelope := map[string]any{
		"schema_version": "1.0",
		"observed_time":  observed.AsTime().UTC().Format(time.RFC3339Nano),
		"acquisition":    acquisition,
		"openshell":      openshell,
		"security":       security,
		"correlation":    correlation,
		"original":       original,
	}
	if attributes, ok := sourceAttributes.(map[string]any); ok && len(attributes) > 0 {
		envelope["source_attributes"] = attributes
	}
	return envelope
}

func canonicalCorrelationContext(
	values map[string]any,
	gatewayID string,
	workspace string,
	sandboxID string,
	attributes pcommon.Map,
) map[string]any {
	result := make(map[string]any, len(values)+5)
	for key, value := range values {
		result[key] = value
	}
	for key, value := range map[string]string{
		"openshell.gateway.id": gatewayID,
		"openshell.workspace":  workspace,
		"openshell.sandbox.id": sandboxID,
	} {
		if value != "" {
			result[key] = value
		}
	}
	if value, ok := attributes.Get("openshell.policy.version"); ok {
		switch value.Type() {
		case pcommon.ValueTypeInt:
			result["openshell.policy.version"] = value.Int()
		case pcommon.ValueTypeStr:
			if value.Str() != "" {
				result["openshell.policy.version"] = value.Str()
			}
		}
	}
	if _, exists := result["agent.session.id"]; !exists {
		if value, ok := result["agent_session_id"]; ok {
			result["agent.session.id"] = value
		}
	}
	return result
}

func setCloudEventAttributes(
	config *Config,
	record plog.LogRecord,
	kind string,
	eventID string,
	original any,
) {
	workspace := stringAttribute(record, "openshell.workspace", config.Workspace)
	sandboxID := stringAttribute(
		record,
		"openshell.sandbox.id",
		sandboxIDFrom(original, config.DefaultSandboxID),
	)
	attributes := record.Attributes()
	attributes.PutStr("event.id", eventID)
	attributes.PutStr("event.domain", "openshell")
	attributes.PutStr("cloudevents.specversion", "1.0")
	attributes.PutStr("cloudevents.id", eventID)
	attributes.PutStr(
		"cloudevents.source",
		fmt.Sprintf(
			"openshell://%s/workspaces/%s/sandboxes/%s/sources/%s",
			escape(config.GatewayID),
			escape(workspace),
			escape(sandboxID),
			escape(kind),
		),
	)
	attributes.PutStr("cloudevents.type", eventType(kind, original))
	attributes.PutStr("cloudevents.dataschema", "urn:openshell:event-envelope:1")
	attributes.PutStr("cloudevents.datacontenttype", "application/json")
	attributes.PutStr("cloudevents.subject", "sandboxes/"+sandboxID)

	if sourceMillis := sourceTimeMillis(kind, original, attributes); sourceMillis > 0 {
		record.SetTimestamp(pcommon.Timestamp(sourceMillis * int64(time.Millisecond)))
	} else {
		record.SetTimestamp(0)
	}
}

func eventType(kind string, original any) string {
	if isOCSFEvidence(kind) {
		if object, ok := original.(map[string]any); ok {
			if classUID, ok := exactInteger(object["class_uid"]); ok && classUID > 0 {
				return "com.nvidia.openshell.ocsf." +
					strconv.FormatInt(classUID, 10) +
					".v1"
			}
		}
		return "com.nvidia.openshell.ocsf.unknown.v1"
	}
	types := map[string]string{
		"sandbox.lifecycle":             "com.nvidia.openshell.sandbox.lifecycle.v1",
		"gateway.log":                   "com.nvidia.openshell.gateway.log.v1",
		"sandbox.log":                   "com.nvidia.openshell.sandbox.log.v1",
		"nemo_relay.log":                "com.nvidia.openshell.nemo_relay.log.v1",
		"sandbox.file_log":              "com.nvidia.openshell.sandbox.file_log.v1",
		"openshell.log.forwarded":       "com.nvidia.openshell.sandbox.file_log.v1",
		"platform.event":                "com.nvidia.openshell.platform.event.v1",
		"policy.draft_updated":          "com.nvidia.openshell.policy.draft_updated.v1",
		"policy.draft.snapshot":         "com.nvidia.openshell.policy.draft.snapshot.v1",
		"policy.draft.chunk":            "com.nvidia.openshell.policy.draft.chunk.v1",
		"policy.draft.history":          "com.nvidia.openshell.policy.draft.history.v1",
		"policy.status":                 "com.nvidia.openshell.policy.status.v1",
		"policy.revision":               "com.nvidia.openshell.policy.revision.v1",
		"policy.reconciliation.warning": "com.nvidia.openshell.policy.reconciliation.warning.v1",
		"stream.warning":                "com.nvidia.openshell.stream.warning.v1",
		"source.capability":             "com.nvidia.openshell.source.capability.v1",
		"kubernetes.context":            "com.nvidia.openshell.kubernetes.context.v1",
	}
	if value := types[kind]; value != "" {
		return value
	}
	return "com.nvidia.openshell.stream.warning.v1"
}

func sourceTimeMillis(kind string, original any, attributes pcommon.Map) int64 {
	if isOCSFEvidence(kind) {
		if object, ok := original.(map[string]any); ok {
			value, _ := exactInteger(object["time"])
			return value
		}
	}
	if value, ok := attributes.Get("openshell.event.time_unix_ms"); ok &&
		value.Type() == pcommon.ValueTypeInt {
		return value.Int()
	}
	return 0
}

func ocsfIdentifiers(original any) map[string]any {
	result := map[string]any{}
	object, ok := original.(map[string]any)
	if !ok {
		return result
	}
	if metadata, ok := object["metadata"].(map[string]any); ok {
		if version, ok := metadata["version"].(string); ok && version != "" {
			result["version"] = version
		}
	}
	for _, key := range []string{
		"class_uid",
		"category_uid",
		"activity_id",
		"type_uid",
		"severity_id",
	} {
		if value, exists := object[key]; exists {
			result[key] = value
		}
	}
	return result
}

func correlations(original any, configured []string) map[string]any {
	result := map[string]any{}
	if value, ok := lookupPath(original, "metadata.original_event_uid"); ok {
		result["original_event_uid"] = value
	}
	identifiers := []struct {
		name    string
		aliases []string
	}{
		{name: "trace_id", aliases: []string{"trace_id", "trace.id"}},
		{name: "span_id", aliases: []string{"span_id", "span.id"}},
		{name: "parent_span_id", aliases: []string{"parent_span_id", "parent.span.id"}},
		{name: "transaction_id", aliases: []string{"transaction_id", "transaction.id"}},
		{name: "request_id", aliases: []string{"request_id", "request.id", "gen_ai.request.id"}},
		{name: "session_id", aliases: []string{"session_id", "session.id", "gen_ai.conversation.id"}},
		{name: "agent_session_id", aliases: []string{"agent_session_id", "agent.session.id"}},
		{name: "tool_call_id", aliases: []string{"tool_call_id", "tool.call.id", "gen_ai.tool.call.id"}},
		{name: "correlation_id", aliases: []string{"correlation_id", "correlation.id"}},
		{name: "policy_chunk_id", aliases: []string{"policy_chunk_id", "policy.chunk.id"}},
		{name: "operation_id", aliases: []string{"operation_id", "operation.id"}},
		{name: "run_id", aliases: []string{"run_id", "run.id"}},
		{name: "invocation_id", aliases: []string{"invocation_id", "invocation.id"}},
	}
	for _, containerPath := range []string{
		"",
		"unmapped",
		"fields",
		"metadata",
		"metadata.annotations",
		"source_payload",
		"source_payload.fields",
		"source_payload.metadata",
		"source_payload.metadata.annotations",
	} {
		container, ok := correlationContainer(original, containerPath)
		if !ok {
			continue
		}
		for _, identifier := range identifiers {
			if _, exists := result[identifier.name]; exists {
				continue
			}
			for _, alias := range identifier.aliases {
				if value, ok := scalarMapValue(container, alias); ok {
					result[identifier.name] = value
					break
				}
			}
		}
	}
	for _, path := range configured {
		name := correlationName(path)
		if _, exists := result[name]; exists {
			continue
		}
		if value, ok := lookupPath(original, path); ok {
			result[name] = value
		}
	}
	return result
}

func correlationContainer(root any, path string) (map[string]any, bool) {
	current, ok := root.(map[string]any)
	if !ok {
		return nil, false
	}
	if path == "" {
		return current, true
	}
	for _, segment := range strings.Split(path, ".") {
		value, exists := current[segment]
		if !exists {
			return nil, false
		}
		current, ok = value.(map[string]any)
		if !ok {
			return nil, false
		}
	}
	return current, true
}

func scalarMapValue(object map[string]any, key string) (any, bool) {
	value, ok := object[key]
	if !ok {
		return nil, false
	}
	switch typed := value.(type) {
	case nil, map[string]any, []any:
		return nil, false
	case string:
		return typed, typed != ""
	default:
		return typed, true
	}
}

func correlationName(path string) string {
	name := path[strings.LastIndex(path, ".")+1:]
	if name == "original_event_uid" {
		return "original_event_uid"
	}
	return name
}

func firstCorrelation(values map[string]any) string {
	for _, key := range []string{
		"original_event_uid",
		"trace_id",
		"span_id",
		"parent_span_id",
		"transaction_id",
		"request_id",
		"session_id",
		"agent_session_id",
		"tool_call_id",
		"correlation_id",
		"policy_chunk_id",
		"operation_id",
		"run_id",
		"invocation_id",
	} {
		if value, ok := values[key]; ok {
			return fmt.Sprint(value)
		}
	}
	return ""
}

func lookupPath(root any, path string) (any, bool) {
	current := root
	for _, segment := range strings.Split(path, ".") {
		object, ok := current.(map[string]any)
		if !ok {
			return nil, false
		}
		current, ok = object[segment]
		if !ok {
			return nil, false
		}
	}
	switch value := current.(type) {
	case nil, map[string]any, []any:
		return nil, false
	case string:
		return value, value != ""
	default:
		return value, true
	}
}

func sandboxIDFrom(original any, fallback string) string {
	for _, path := range []string{"sandbox.id", "sandbox_id", "unmapped.sandbox_id"} {
		if value, ok := lookupPath(original, path); ok && fmt.Sprint(value) != "" {
			return fmt.Sprint(value)
		}
	}
	if fallback != "" {
		return fallback
	}
	return "unknown"
}

func isCheckpointedFile(kind string) bool {
	return kind == "ocsf.file" ||
		kind == "sandbox.file_log" ||
		kind == "nemo_relay.log"
}

func isFileEvidence(kind string) bool {
	return isCheckpointedFile(kind) || isForwardedFile(kind)
}

func isForwardedFile(kind string) bool {
	return kind == "ocsf.forwarded" || kind == "openshell.log.forwarded"
}

func isOCSFEvidence(kind string) bool {
	return kind == "ocsf.file" || kind == "ocsf.forwarded"
}

func isKubernetesContext(kind string) bool {
	return kind == "kubernetes.context"
}

func isSourceCapability(kind string) bool {
	return kind == "source.capability"
}

func durability(kind string) string {
	if isSourceCapability(kind) {
		return "upstream_buffered_diagnostic"
	}
	if isCheckpointedFile(kind) {
		return "checkpointed_file"
	}
	if isForwardedFile(kind) {
		return "upstream_checkpointed_file"
	}
	if isKubernetesContext(kind) {
		return "resource_version_checkpointed_api"
	}
	if isPolicyReconciliationKind(kind) {
		return "checkpointed_api_snapshot"
	}
	return "non_resumable_stream"
}

func acquisitionCoverage(kind string, attributes pcommon.Map) map[string]any {
	coverage := map[string]any{
		"status":         "observed",
		"source_profile": sourceProfile(kind),
	}
	switch {
	case isSourceCapability(kind):
		coverage["payload_preservation"] = "exporter_diagnostic"
		coverage["known_limitations"] = []any{
			"reports_configured_sandbox_file_lanes_not_all_possible_gateway_evidence",
		}
		if value, ok := attributes.Get("openshell.source.network_file_lane"); ok &&
			value.Type() == pcommon.ValueTypeStr && value.Str() != "enabled" {
			coverage["status"] = "gap_reported"
		}
	case isFileEvidence(kind):
		coverage["payload_preservation"] = "redacted_source_record"
		coverage["known_limitations"] = []any{
			"durability_requires_persistent_upstream_file_and_checkpoint_storage",
		}
		if isForwardedFile(kind) {
			coverage["known_limitations"] = []any{
				"forwarder_owns_file_checkpoint_and_retry_state",
				"exporter_cannot_verify_forwarder_file_coordinates_when_omitted",
			}
		}
	case isPolicyReconciliationKind(kind):
		coverage["payload_preservation"] = "full_public_proto_json"
		coverage["known_limitations"] = []any{
			"read_only_polling_snapshot_without_event_cursor",
			"review_tokens_are_never_exported",
			"effective_policy_bodies_require_explicit_opt_in",
		}
		if kind == "policy.reconciliation.warning" {
			coverage["status"] = "gap_reported"
			coverage["payload_preservation"] = "exporter_diagnostic"
		}
	case isKubernetesContext(kind):
		coverage["payload_preservation"] = "redacted_kubernetes_api_object"
		coverage["known_limitations"] = []any{
			"namespace_and_object_allow_list_only",
			"not_sandbox_application_or_host_logs",
		}
	case isWatchSandboxKind(kind):
		payloadPreservation := "exporter_diagnostic"
		if value, ok := attributes.Get("openshell.acquisition.payload_type"); ok &&
			value.Type() == pcommon.ValueTypeStr &&
			strings.HasPrefix(value.Str(), "openshell.v1.") {
			payloadPreservation = "full_public_proto_json"
		}
		coverage["payload_preservation"] = payloadPreservation
		coverage["known_limitations"] = []any{
			"non_resumable",
			"best_effort_reconnect_tails",
			"watch_events_unavailable",
		}
		if kind == "stream.warning" {
			coverage["status"] = "gap_reported"
		}
	default:
		coverage["payload_preservation"] = "redacted_source_record"
		coverage["known_limitations"] = []any{"unclassified_source"}
	}
	return coverage
}

func sourceProfile(kind string) string {
	switch {
	case kind == "ocsf.file":
		return "ocsf.file"
	case kind == "ocsf.forwarded":
		return "ocsf.forwarded"
	case kind == "openshell.log.forwarded":
		return "openshell.log.forwarded"
	case isSourceCapability(kind):
		return "sandbox.forwarder"
	case isKubernetesContext(kind):
		return "kubernetes.context"
	case isPolicyReconciliationKind(kind):
		return "policy.reconciliation"
	case kind == "sandbox.file_log":
		return "openshell.log"
	case kind == "nemo_relay.log":
		return "nemo_relay.log"
	case isWatchSandboxKind(kind):
		return "watchsandbox"
	default:
		return "unsupported"
	}
}

func isPolicyReconciliationKind(kind string) bool {
	switch kind {
	case "policy.draft.snapshot",
		"policy.draft.chunk",
		"policy.draft.history",
		"policy.status",
		"policy.revision",
		"policy.reconciliation.warning":
		return true
	default:
		return false
	}
}

func isWatchSandboxKind(kind string) bool {
	switch kind {
	case "sandbox.lifecycle",
		"gateway.log",
		"sandbox.log",
		"platform.event",
		"policy.draft_updated",
		"stream.warning":
		return true
	default:
		return false
	}
}

func escape(value string) string {
	return url.PathEscape(value)
}

func stringAttribute(record plog.LogRecord, key, fallback string) string {
	value, ok := record.Attributes().Get(key)
	if !ok || value.Type() != pcommon.ValueTypeStr || value.Str() == "" {
		return fallback
	}
	return value.Str()
}

func copyStringAttribute(
	from pcommon.Map,
	to map[string]any,
	attribute string,
	field string,
) {
	if value, ok := from.Get(attribute); ok &&
		value.Type() == pcommon.ValueTypeStr &&
		value.Str() != "" {
		to[field] = value.Str()
	}
}

func copyAttribute(from pcommon.Map, to map[string]any, attribute string, field string) {
	if value, ok := from.Get(attribute); ok {
		to[field] = value.AsRaw()
	}
}

func stringsToAny(values []string) []any {
	result := make([]any, len(values))
	for index, value := range values {
		result[index] = value
	}
	return result
}
