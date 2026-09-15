// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package watchsandboxreceiver

import (
	"encoding/json"
	"errors"
	"fmt"
	"time"

	pb "github.com/NVIDIA/OpenShell/sdk/go/proto/openshellv1"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/plog"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
)

func convertStreamEvent(
	config *Config,
	sandbox sandboxRef,
	gatewayVersion string,
	event *pb.SandboxStreamEvent,
) (plog.Logs, error) {
	if event == nil {
		return plog.Logs{}, errors.New("nil SandboxStreamEvent")
	}
	switch payload := event.GetPayload().(type) {
	case *pb.SandboxStreamEvent_Sandbox:
		if payload.Sandbox == nil {
			return plog.Logs{}, errors.New("nil sandbox status payload")
		}
		return lifecycleLogs(config, gatewayVersion, payload.Sandbox)
	case *pb.SandboxStreamEvent_Log:
		if payload.Log == nil {
			return plog.Logs{}, errors.New("nil sandbox log payload")
		}
		return logLineLogs(config, sandbox, gatewayVersion, payload.Log)
	case *pb.SandboxStreamEvent_Event:
		if payload.Event == nil {
			return plog.Logs{}, errors.New("nil platform event payload")
		}
		return platformEventLogs(config, sandbox, gatewayVersion, payload.Event)
	case *pb.SandboxStreamEvent_DraftPolicyUpdate:
		if payload.DraftPolicyUpdate == nil {
			return plog.Logs{}, errors.New("nil draft policy update payload")
		}
		return draftUpdateLogs(
			config,
			sandbox,
			gatewayVersion,
			payload.DraftPolicyUpdate,
		)
	case *pb.SandboxStreamEvent_Warning:
		if payload.Warning == nil {
			return plog.Logs{}, errors.New("nil stream warning payload")
		}
		return warningLogs(
			config,
			sandbox,
			gatewayVersion,
			"gateway_stream_warning",
			payload.Warning.GetMessage(),
			payload.Warning,
		)
	default:
		return plog.Logs{}, fmt.Errorf(
			"unsupported SandboxStreamEvent payload %T",
			event.GetPayload(),
		)
	}
}

func lifecycleLogs(
	config *Config,
	gatewayVersion string,
	sandbox *pb.Sandbox,
) (plog.Logs, error) {
	metadata := sandbox.GetMetadata()
	if metadata == nil || metadata.GetId() == "" {
		return plog.Logs{}, errors.New("sandbox payload has no stable ID")
	}
	eventType := "MODIFIED"
	if metadata.GetDeletionTimestampMs() > 0 {
		eventType = "DELETED"
	}
	status := sandbox.GetStatus()
	sandboxBody := map[string]any{
		"id":               metadata.GetId(),
		"name":             metadata.GetName(),
		"workspace":        metadata.GetWorkspace(),
		"labels":           stringMapToAny(metadata.GetLabels()),
		"resource_version": metadata.GetResourceVersion(),
		"phase":            status.GetPhase().String(),
	}
	policyVersion := status.GetCurrentPolicyVersion()
	if policyVersion > 0 {
		sandboxBody["policy_version"] = policyVersion
	}
	body := map[string]any{
		"event_type": eventType,
		"sandbox":    sandboxBody,
	}
	body, payloadType, err := withPublicPayload(body, sandbox)
	if err != nil {
		return plog.Logs{}, err
	}
	logs, record, err := sourceLogs(
		config,
		sandboxRef{ID: metadata.GetId(), Name: metadata.GetName()},
		gatewayVersion,
		"sandbox.lifecycle",
		body,
		payloadType,
	)
	if err != nil {
		return plog.Logs{}, err
	}
	record.Attributes().PutInt(
		"openshell.resource.version",
		int64(metadata.GetResourceVersion()),
	)
	if policyVersion > 0 {
		record.Attributes().PutInt(
			"openshell.policy.version",
			int64(policyVersion),
		)
	}
	return logs, nil
}

func logLineLogs(
	config *Config,
	sandbox sandboxRef,
	gatewayVersion string,
	line *pb.SandboxLogLine,
) (plog.Logs, error) {
	source := line.GetSource()
	if source == "" {
		source = "gateway"
	}
	kind := "gateway.log"
	if source == "sandbox" {
		kind = "sandbox.log"
	}
	body := map[string]any{
		"sandbox_id":   line.GetSandboxId(),
		"timestamp_ms": line.GetTimestampMs(),
		"level":        line.GetLevel(),
		"target":       line.GetTarget(),
		"message":      line.GetMessage(),
		"source":       source,
		"fields":       stringMapToAny(line.GetFields()),
	}
	body, payloadType, err := withPublicPayload(body, line)
	if err != nil {
		return plog.Logs{}, err
	}
	logs, record, err := sourceLogs(
		config,
		sandbox,
		gatewayVersion,
		kind,
		body,
		payloadType,
	)
	if err != nil {
		return plog.Logs{}, err
	}
	if line.GetTimestampMs() > 0 {
		record.Attributes().PutInt("openshell.event.time_unix_ms", line.GetTimestampMs())
	}
	return logs, nil
}

func platformEventLogs(
	config *Config,
	sandbox sandboxRef,
	gatewayVersion string,
	event *pb.PlatformEvent,
) (plog.Logs, error) {
	body := map[string]any{
		"sandbox_id":   sandbox.ID,
		"timestamp_ms": event.GetTimestampMs(),
		"source":       event.GetSource(),
		"type":         event.GetType(),
		"reason":       event.GetReason(),
		"message":      event.GetMessage(),
		"metadata":     stringMapToAny(event.GetMetadata()),
	}
	body, payloadType, err := withPublicPayload(body, event)
	if err != nil {
		return plog.Logs{}, err
	}
	logs, record, err := sourceLogs(
		config,
		sandbox,
		gatewayVersion,
		"platform.event",
		body,
		payloadType,
	)
	if err != nil {
		return plog.Logs{}, err
	}
	if event.GetTimestampMs() > 0 {
		record.Attributes().PutInt(
			"openshell.event.time_unix_ms",
			event.GetTimestampMs(),
		)
	}
	return logs, nil
}

func draftUpdateLogs(
	config *Config,
	sandbox sandboxRef,
	gatewayVersion string,
	update *pb.DraftPolicyUpdate,
) (plog.Logs, error) {
	body := map[string]any{
		"sandbox_id":    sandbox.ID,
		"draft_version": update.GetDraftVersion(),
		"new_chunks":    update.GetNewChunks(),
		"total_pending": update.GetTotalPending(),
		"summary":       update.GetSummary(),
	}
	body, payloadType, err := withPublicPayload(body, update)
	if err != nil {
		return plog.Logs{}, err
	}
	logs, record, err := sourceLogs(
		config,
		sandbox,
		gatewayVersion,
		"policy.draft_updated",
		body,
		payloadType,
	)
	if err != nil {
		return plog.Logs{}, err
	}
	record.Attributes().PutInt(
		"openshell.policy.revision",
		int64(update.GetDraftVersion()),
	)
	return logs, nil
}

func warningLogs(
	config *Config,
	sandbox sandboxRef,
	gatewayVersion string,
	code string,
	message string,
	payload ...proto.Message,
) (plog.Logs, error) {
	body := map[string]any{
		"sandbox_id": sandbox.ID,
		"code":       code,
		"message":    message,
	}
	payloadType := "openshell.exporter.watchsandbox.Diagnostic"
	if len(payload) > 0 {
		var err error
		body, payloadType, err = withPublicPayload(body, payload[0])
		if err != nil {
			return plog.Logs{}, err
		}
	}
	returnLogs, _, err := sourceLogs(
		config,
		sandbox,
		gatewayVersion,
		"stream.warning",
		body,
		payloadType,
	)
	return returnLogs, err
}

func withPublicPayload(
	body map[string]any,
	message proto.Message,
) (map[string]any, string, error) {
	if message == nil || !message.ProtoReflect().IsValid() {
		return nil, "", errors.New("nil public OpenShell payload")
	}
	encoded, err := (protojson.MarshalOptions{UseProtoNames: true}).Marshal(message)
	if err != nil {
		return nil, "", fmt.Errorf("marshal public OpenShell payload: %w", err)
	}
	payload := map[string]any{}
	if err := json.Unmarshal(encoded, &payload); err != nil {
		return nil, "", fmt.Errorf("decode public OpenShell payload: %w", err)
	}
	body["source_payload"] = payload
	return body, string(message.ProtoReflect().Descriptor().FullName()), nil
}

func sourceLogs(
	config *Config,
	sandbox sandboxRef,
	gatewayVersion string,
	kind string,
	body any,
	payloadType string,
) (plog.Logs, plog.LogRecord, error) {
	logs := plog.NewLogs()
	resourceLogs := logs.ResourceLogs().AppendEmpty()
	resourceLogs.Resource().Attributes().PutStr("service.name", "openshell")
	resourceLogs.Resource().Attributes().PutStr(
		"openshell.gateway.id",
		config.GatewayID,
	)
	scopeLogs := resourceLogs.ScopeLogs().AppendEmpty()
	scopeLogs.Scope().SetName("openshell.watchsandbox")
	record := scopeLogs.LogRecords().AppendEmpty()
	record.Attributes().PutStr(
		"openshell.acquisition.source_instance",
		"watchsandbox",
	)
	record.SetObservedTimestamp(pcommon.NewTimestampFromTime(time.Now().UTC()))
	record.Attributes().PutStr("openshell.acquisition.kind", kind)
	record.Attributes().PutStr("openshell.acquisition.transport", "grpc")
	record.Attributes().PutStr(
		"openshell.acquisition.api_operation",
		"openshell.v1.OpenShell/WatchSandbox",
	)
	record.Attributes().PutStr("openshell.acquisition.payload_type", payloadType)
	record.Attributes().PutBool("openshell.acquisition.stream_resumable", false)
	record.Attributes().PutBool("openshell.acquisition.follow_status", true)
	record.Attributes().PutBool("openshell.acquisition.follow_logs", true)
	record.Attributes().PutBool("openshell.acquisition.follow_events", true)
	record.Attributes().PutBool("openshell.acquisition.stop_on_terminal", false)
	record.Attributes().PutInt("openshell.acquisition.log_since_ms", 0)
	record.Attributes().PutStr("openshell.acquisition.log_min_level", "")
	logSources := record.Attributes().PutEmptySlice("openshell.acquisition.log_sources")
	logSources.AppendEmpty().SetStr("gateway")
	logSources.AppendEmpty().SetStr("sandbox")
	record.Attributes().PutInt(
		"openshell.acquisition.log_tail_lines",
		int64(config.LogTailLines),
	)
	record.Attributes().PutInt(
		"openshell.acquisition.event_tail",
		int64(config.EventTail),
	)
	record.Attributes().PutStr("openshell.event.kind", kind)
	record.Attributes().PutStr("openshell.workspace", config.Workspace)
	record.Attributes().PutStr("openshell.sandbox.id", sandbox.ID)
	record.Attributes().PutStr("openshell.sandbox.name", sandbox.Name)
	if gatewayVersion != "" {
		record.Attributes().PutStr("openshell.gateway.version", gatewayVersion)
	}
	if err := record.Body().FromRaw(body); err != nil {
		return plog.Logs{}, plog.LogRecord{}, fmt.Errorf(
			"construct %s event: %w",
			kind,
			err,
		)
	}
	return logs, record, nil
}

func stringMapToAny(values map[string]string) map[string]any {
	result := make(map[string]any, len(values))
	for key, value := range values {
		result[key] = value
	}
	return result
}
