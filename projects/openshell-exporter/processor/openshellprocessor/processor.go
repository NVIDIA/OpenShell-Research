// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package openshellprocessor

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
	"time"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/plog"
	"go.opentelemetry.io/otel/metric"
	"go.uber.org/zap"
)

const redacted = "[REDACTED]"

type processorImpl struct {
	config       *Config
	logger       *zap.Logger
	metrics      *processorMetrics
	redactedKeys map[string]struct{}
	patterns     []*regexp.Regexp
}

func newProcessor(config *Config, logger *zap.Logger) (*processorImpl, error) {
	return newProcessorWithMeter(config, logger, nil)
}

func newProcessorWithMeter(
	config *Config,
	logger *zap.Logger,
	meterProvider metric.MeterProvider,
) (*processorImpl, error) {
	if err := config.Validate(); err != nil {
		return nil, err
	}
	metrics, err := newProcessorMetrics(meterProvider)
	if err != nil {
		return nil, fmt.Errorf("create processor metrics: %w", err)
	}
	keys := make(map[string]struct{}, len(config.Redaction.Keys))
	for _, key := range config.Redaction.Keys {
		keys[strings.ToLower(key)] = struct{}{}
	}
	patterns := make([]*regexp.Regexp, 0, len(config.Redaction.Patterns))
	for _, pattern := range config.Redaction.Patterns {
		compiled, err := regexp.Compile(pattern)
		if err != nil {
			return nil, fmt.Errorf("compile redaction pattern: %w", err)
		}
		patterns = append(patterns, compiled)
	}
	return &processorImpl{
		config:       config,
		logger:       logger,
		metrics:      metrics,
		redactedKeys: keys,
		patterns:     patterns,
	}, nil
}

func (p *processorImpl) start(ctx context.Context, _ component.Host) error {
	capabilities := sourceCapabilitySummaries(p.config.SourceProfiles)
	p.logger.Info(
		"OpenShell source capability summary",
		zap.String("schema_version", "1.0"),
		zap.Any("sources", capabilities),
	)
	p.metrics.capabilitiesConfigured(ctx, p.config.SourceProfiles, capabilities)
	return nil
}

var unavailableSourceCapabilities = []string{
	"gateway_structured_ocsf_export",
	"watch_events",
}

func sourceCapabilitySets(enabledProfiles []string) ([]string, []string) {
	disabledProfiles := make([]string, 0, len(supportedSourceProfiles))
	enabled := make(map[string]struct{}, len(enabledProfiles))
	for _, profile := range enabledProfiles {
		enabled[profile] = struct{}{}
	}
	for _, profile := range supportedSourceProfiles {
		if _, ok := enabled[profile]; !ok {
			disabledProfiles = append(disabledProfiles, profile)
		}
	}
	declaredUnobserved := []string{}
	if _, ok := enabled["watchsandbox"]; ok {
		declaredUnobserved = append(
			declaredUnobserved,
			"policy.draft_updated",
			"stream.warning",
		)
	}
	return disabledProfiles, declaredUnobserved
}

func (p *processorImpl) processLogs(ctx context.Context, logs plog.Logs) (plog.Logs, error) {
	for resourceIndex := 0; resourceIndex < logs.ResourceLogs().Len(); resourceIndex++ {
		resourceLogs := logs.ResourceLogs().At(resourceIndex)
		resourceLogs.Resource().Attributes().PutStr("service.name", "openshell")
		resourceLogs.Resource().Attributes().PutStr("openshell.gateway.id", p.config.GatewayID)
		for scopeIndex := 0; scopeIndex < resourceLogs.ScopeLogs().Len(); scopeIndex++ {
			records := resourceLogs.ScopeLogs().At(scopeIndex).LogRecords()
			for recordIndex := 0; recordIndex < records.Len(); recordIndex++ {
				p.normalize(ctx, records.At(recordIndex))
			}
		}
	}
	return logs, nil
}

func (p *processorImpl) normalize(ctx context.Context, record plog.LogRecord) {
	observed := record.ObservedTimestamp()
	if observed == 0 {
		observed = pcommon.NewTimestampFromTime(time.Now().UTC())
		record.SetObservedTimestamp(observed)
	}

	original := record.Body().AsRaw()
	kind := acquisitionKind(record, original)
	if isForwardedFile(kind) {
		original = decodeForwardedRecord(original)
	}
	if isCheckpointedFile(kind) {
		promoteCheckpointedFileContext(record)
	}
	if isForwardedFile(kind) {
		promoteForwardedFileProvenance(record, original)
	}
	if isKubernetesContext(kind) {
		promoteKubernetesContext(record, original)
	}
	eventID := stableID(p.config, record, kind, original)
	validation := validateSource(kind, original)
	sourceAttributes := record.Attributes().AsRaw()
	redactedOriginal, bodyRedactionCount := p.redactRaw(original)
	redactedSourceAttributes, attributeRedactionCount := p.redactRaw(sourceAttributes)
	redactionCount := bodyRedactionCount + attributeRedactionCount
	correlation := correlations(original, p.config.CorrelationFields)
	mergeMissingCorrelations(correlation, correlations(sourceAttributes, p.config.CorrelationFields))
	envelope := buildEnvelope(
		p.config,
		record,
		kind,
		observed,
		validation,
		redactionCount,
		correlation,
		redactedOriginal,
		redactedSourceAttributes,
	)

	if err := record.Body().FromRaw(envelope); err != nil {
		p.logger.Error(
			"construct event envelope",
			zap.Error(err),
		)
		return
	}
	p.metrics.observeSource(ctx, kind)
	p.metrics.record(ctx, kind, validation, redactionCount, p.config.Redaction.ProfileID)
	setCloudEventAttributes(p.config, record, kind, eventID, original)
	switch validation.Status {
	case validationInvalid:
		record.Attributes().PutBool("openshell.ocsf.valid", false)
		record.Attributes().PutStr(
			"openshell.ocsf.validation_error",
			strings.Join(validation.Errors, "; "),
		)
		p.logger.Warn(
			"invalid source evidence retained",
			zap.Strings("errors", validation.Errors),
		)
	case validationValid:
		record.Attributes().PutBool("openshell.ocsf.valid", true)
	}
	if value := firstCorrelation(correlation); value != "" {
		record.Attributes().PutStr("openshell.correlation.id", value)
	}
}

func decodeForwardedRecord(original any) any {
	text, ok := original.(string)
	if !ok {
		return original
	}
	var decoded any
	if err := json.Unmarshal([]byte(text), &decoded); err != nil {
		return original
	}
	return decoded
}

func mergeMissingCorrelations(destination, source map[string]any) {
	for key, value := range source {
		if _, exists := destination[key]; !exists {
			destination[key] = value
		}
	}
}

func acquisitionKind(record plog.LogRecord, original any) string {
	if value, ok := record.Attributes().Get("openshell.acquisition.kind"); ok &&
		value.Type() == pcommon.ValueTypeStr {
		return value.Str()
	}
	if object, ok := original.(map[string]any); ok {
		if _, isOCSF := object["class_uid"]; isOCSF {
			return "ocsf.file"
		}
	}
	return "unknown"
}

func promoteCheckpointedFileContext(record plog.LogRecord) {
	attributes := record.Attributes()
	if _, exists := attributes.Get("openshell.sandbox.name"); exists {
		return
	}
	for _, key := range []string{"log.file.path_resolved", "log.file.path"} {
		value, ok := attributes.Get(key)
		if !ok {
			continue
		}
		if sandboxName := sandboxNameFromEvidencePath(value.Str()); sandboxName != "" {
			attributes.PutStr("openshell.sandbox.name", sandboxName)
			return
		}
	}
}

func sandboxNameFromEvidencePath(value string) string {
	parts := strings.Split(strings.ReplaceAll(value, "\\", "/"), "/")
	for index := 0; index+1 < len(parts); index++ {
		if parts[index] == "sandboxes" && isDNS1123Label(parts[index+1]) {
			return parts[index+1]
		}
	}
	return ""
}

func isDNS1123Label(value string) bool {
	if len(value) == 0 || len(value) > 63 || !isLowerAlphanumeric(value[0]) ||
		!isLowerAlphanumeric(value[len(value)-1]) {
		return false
	}
	for index := 1; index+1 < len(value); index++ {
		if !isLowerAlphanumeric(value[index]) && value[index] != '-' {
			return false
		}
	}
	return true
}

func isLowerAlphanumeric(value byte) bool {
	return value >= 'a' && value <= 'z' || value >= '0' && value <= '9'
}

func promoteForwardedFileProvenance(record plog.LogRecord, original any) {
	object, ok := original.(map[string]any)
	if !ok {
		return
	}
	attributes := record.Attributes()
	for _, key := range []string{
		"log.file.path",
		"log.file.path_resolved",
		"openshell.acquisition.source_instance",
		"openshell.sandbox.id",
	} {
		if _, exists := attributes.Get(key); exists {
			continue
		}
		if value, ok := object[key].(string); ok && value != "" {
			attributes.PutStr(key, value)
		}
	}
	for _, key := range []string{"log.file.record_offset", "log.file.record_number"} {
		if _, exists := attributes.Get(key); exists {
			continue
		}
		if value, ok := exactInteger(object[key]); ok && value >= 0 {
			attributes.PutInt(key, value)
		}
	}
}

func promoteKubernetesContext(record plog.LogRecord, original any) {
	object := kubernetesObject(original)
	metadata, ok := object["metadata"].(map[string]any)
	if !ok {
		return
	}
	attributes := record.Attributes()
	putStringAttributeIfMissing(attributes, "k8s.object.uid", metadata["uid"])
	putStringAttributeIfMissing(
		attributes,
		"k8s.object.resource_version",
		metadata["resourceVersion"],
	)
	for _, containerName := range []string{"annotations", "labels"} {
		container, ok := metadata[containerName].(map[string]any)
		if !ok {
			continue
		}
		putStringAttributeIfMissing(
			attributes,
			"openshell.sandbox.id",
			firstNonEmpty(container["openshell.io/sandbox-id"], container["openshell.ai/sandbox-id"]),
		)
		putStringAttributeIfMissing(
			attributes,
			"openshell.workspace",
			container["openshell.ai/sandbox-workspace"],
		)
	}
}

func kubernetesObject(original any) map[string]any {
	object, _ := original.(map[string]any)
	if nested, ok := object["object"].(map[string]any); ok {
		return nested
	}
	return object
}

func putStringAttributeIfMissing(attributes pcommon.Map, key string, raw any) {
	if _, exists := attributes.Get(key); exists {
		return
	}
	if value, ok := raw.(string); ok && value != "" {
		attributes.PutStr(key, value)
	}
}

func firstNonEmpty(values ...any) any {
	for _, raw := range values {
		if value, ok := raw.(string); ok && value != "" {
			return value
		}
	}
	return nil
}
