// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package relayprocessor

import (
	"context"
	"fmt"
	"path"
	"sort"
	"strings"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/otel/metric"
	"go.uber.org/zap"
)

var protectedAttributes = map[string]struct{}{
	"agent.session.id":              {},
	"openshell.correlation.invalid": {},
	"openshell.correlation.missing": {},
	"openshell.correlation.status":  {},
	"openshell.gateway.id":          {},
	"openshell.policy.version":      {},
	"openshell.sandbox.id":          {},
	"openshell.workspace":           {},
	"request_id":                    {},
	"telemetry.source":              {},
	"trace_id":                      {},
	"tool_call_id":                  {},
}

const maxCorrelationIdentifierLength = 256

func isBenignUsageAttribute(key string, value pcommon.Value) bool {
	lower := strings.ToLower(key)
	if lower == "token_type" {
		return value.Type() == pcommon.ValueTypeStr
	}
	isNumeric := value.Type() == pcommon.ValueTypeInt || value.Type() == pcommon.ValueTypeDouble
	if !isNumeric {
		return false
	}
	if lower == "tokens_in" || lower == "tokens_out" {
		return true
	}
	for _, prefix := range []string{
		"gen_ai.usage.",
		"llm.token_count.",
		"nemo_relay.llm.token_count.",
	} {
		if strings.HasPrefix(lower, prefix) {
			return true
		}
	}
	return false
}

func isBenignCostAttribute(key string, value pcommon.Value) bool {
	isNumeric := value.Type() == pcommon.ValueTypeInt || value.Type() == pcommon.ValueTypeDouble
	if !isNumeric {
		return false
	}
	lower := strings.ToLower(key)
	return strings.HasPrefix(lower, "llm.cost.") || strings.HasPrefix(lower, "nemo_relay.llm.cost.")
}

func isUsageOrCostAttribute(key string) bool {
	lower := strings.ToLower(key)
	if lower == "token_type" || lower == "tokens_in" || lower == "tokens_out" {
		return true
	}
	for _, prefix := range []string{
		"gen_ai.usage.",
		"llm.cost.",
		"llm.token_count.",
		"nemo_relay.llm.cost.",
		"nemo_relay.llm.token_count.",
	} {
		if strings.HasPrefix(lower, prefix) {
			return true
		}
	}
	return false
}

type processorImpl struct {
	config  *Config
	logger  *zap.Logger
	metrics *relayMetrics
}

type relaySourceCapability struct {
	Source     string `json:"source"`
	Profile    string `json:"profile"`
	Durability string `json:"durability"`
	Limitation string `json:"limitation_code"`
}

func relaySourceCapabilities(telemetrySource string) []relaySourceCapability {
	switch telemetrySource {
	case "nemo_relay":
		return []relaySourceCapability{{
			Source: "relay_otlp", Profile: "nemo_relay.trace",
			Durability: "sender_dependent", Limitation: "privacy_filtered_no_source_replay",
		}}
	case "openshell_native":
		return []relaySourceCapability{
			{Source: "gateway_otlp", Profile: "openshell.trace", Durability: "sender_dependent", Limitation: "sender_delivery_not_source_replay"},
			{Source: "driver_otlp", Profile: "openshell.trace", Durability: "sender_dependent", Limitation: "sender_delivery_not_source_replay"},
		}
	default:
		return []relaySourceCapability{{
			Source: "other_otlp", Profile: "other_otlp",
			Durability: "sender_dependent", Limitation: "unclassified_sender_no_source_replay",
		}}
	}
}

func (p *processorImpl) start(ctx context.Context, _ component.Host) error {
	capabilities := relaySourceCapabilities(p.config.TelemetrySource)
	p.logger.Info(
		"OpenShell OTLP source capability summary",
		zap.String("schema_version", "1.0"),
		zap.Any("sources", capabilities),
	)
	p.metrics.capabilitiesConfigured(ctx, capabilities)
	return nil
}

func newProcessor(config *Config, logger *zap.Logger, meterProvider metric.MeterProvider) (*processorImpl, error) {
	if err := config.Validate(); err != nil {
		return nil, err
	}
	metrics, err := newRelayMetrics(meterProvider)
	if err != nil {
		return nil, fmt.Errorf("create Relay processor metrics: %w", err)
	}
	return &processorImpl{config: config, logger: logger, metrics: metrics}, nil
}

func defaultAliases() map[string]string {
	return map[string]string{
		"agent.session.id":         "session.id",
		"openshell.policy.version": "policy.version",
		"request_id":               "gen_ai.request.id",
		"tool_call_id":             "nemo_relay.tool_call_id",
	}
}

func boundedOTLPSource(telemetrySource string, resource pcommon.Map) string {
	if telemetrySource == "nemo_relay" {
		return "relay_otlp"
	}
	if telemetrySource != "openshell_native" {
		return "other_otlp"
	}
	for _, key := range []string{"service.name", "service.namespace", "openshell.component"} {
		value, ok := resource.Get(key)
		if !ok || value.Type() != pcommon.ValueTypeStr {
			continue
		}
		name := strings.ToLower(value.Str())
		switch {
		case strings.Contains(name, "driver"):
			return "driver_otlp"
		case strings.Contains(name, "gateway"):
			return "gateway_otlp"
		}
	}
	return "native_otlp_unclassified"
}

func (p *processorImpl) processTraces(ctx context.Context, traces ptrace.Traces) (ptrace.Traces, error) {
	for resourceIndex := 0; resourceIndex < traces.ResourceSpans().Len(); resourceIndex++ {
		resourceSpans := traces.ResourceSpans().At(resourceIndex)
		resource := resourceSpans.Resource().Attributes()
		source := boundedOTLPSource(p.config.TelemetrySource, resource)
		traceContexts := collectTraceContexts(resourceSpans)
		resource.PutStr("telemetry.source", p.config.TelemetrySource)
		resource.PutStr("openshell.gateway.id", p.config.GatewayID)
		resource.PutStr("openshell.workspace", p.config.Workspace)
		resourceInvalid := sanitizeCorrelationIdentifiers(resource)

		for scopeIndex := 0; scopeIndex < resourceSpans.ScopeSpans().Len(); scopeIndex++ {
			scopeSpans := resourceSpans.ScopeSpans().At(scopeIndex)
			spans := scopeSpans.Spans()
			for spanIndex := 0; spanIndex < spans.Len(); spanIndex++ {
				span := spans.At(spanIndex)
				status := p.normalize(resource, span.Attributes(), traceContexts[span.TraceID().String()], span.TraceID(), resourceInvalid)
				p.metrics.recordSpan(ctx, status, p.config.Privacy.Mode)
				p.metrics.recordRemoved(ctx, "span", p.filter(span.Attributes()))
				span.Status().SetMessage("")
				if p.config.Privacy.Mode == privacyAllow {
					if p.config.CanonicalizeSpanNames {
						span.SetName(canonicalSpanName(span.Attributes()))
					}
					span.TraceState().FromRaw("")
				}
				for eventIndex := 0; eventIndex < span.Events().Len(); eventIndex++ {
					event := span.Events().At(eventIndex)
					p.metrics.recordRemoved(ctx, "event", p.filter(event.Attributes()))
					if p.config.Privacy.Mode == privacyAllow {
						event.SetName("relay.event")
					}
				}
				for linkIndex := 0; linkIndex < span.Links().Len(); linkIndex++ {
					p.metrics.recordRemoved(ctx, "link", p.filter(span.Links().At(linkIndex).Attributes()))
				}
			}
			p.metrics.recordRemoved(ctx, "scope", p.filter(scopeSpans.Scope().Attributes()))
		}
		p.metrics.recordRemoved(ctx, "resource", p.filter(resource))
		p.metrics.observeSource(ctx, source)
	}
	return traces, nil
}

func canonicalSpanName(attributes pcommon.Map) string {
	for _, key := range []string{"openinference.span.kind", "gen_ai.operation.name", "nemo_relay.scope_type"} {
		value, ok := attributes.Get(key)
		if !ok || value.Type() != pcommon.ValueTypeStr {
			continue
		}
		name := strings.ToLower(value.Str())
		switch {
		case strings.Contains(name, "tool"):
			return "relay.tool"
		case strings.Contains(name, "llm"), strings.Contains(name, "model"), strings.Contains(name, "chat"):
			return "relay.model"
		case strings.Contains(name, "chain"):
			return "relay.chain"
		case strings.Contains(name, "agent"), strings.Contains(name, "session"):
			return "relay.agent"
		}
	}
	return "relay.span"
}

func (p *processorImpl) normalize(
	resource pcommon.Map,
	attributes pcommon.Map,
	traceContext map[string]pcommon.Value,
	traceID pcommon.TraceID,
	resourceInvalid []string,
) string {
	for _, canonical := range []string{
		"openshell.gateway.id",
		"openshell.workspace",
		"openshell.sandbox.id",
		"openshell.policy.version",
	} {
		attributes.Remove(canonical)
		value, exists := firstValue(canonical, resource)
		if !exists {
			if alias, configured := p.config.Aliases[canonical]; configured {
				value, exists = firstValue(alias, resource)
			}
		}
		if exists {
			value.CopyTo(attributes.PutEmpty(canonical))
		}
	}
	if traceID == (pcommon.TraceID{}) {
		attributes.Remove("trace_id")
	} else {
		attributes.PutStr("trace_id", traceID.String())
	}

	for canonical, alias := range p.config.Aliases {
		if _, exists := firstValue(canonical, attributes); exists {
			continue
		}
		if value, ok := firstValue(alias, attributes, resource); ok {
			value.CopyTo(attributes.PutEmpty(canonical))
			continue
		}
		if value, ok := traceContext[canonical]; ok {
			value.CopyTo(attributes.PutEmpty(canonical))
		}
	}
	if _, exists := firstValue("tool_call_id", attributes); !exists {
		for _, alias := range []string{"gen_ai.tool.call.id", "tool_call.id", "tool_call_id"} {
			if value, ok := firstValue(alias, attributes, resource); ok {
				value.CopyTo(attributes.PutEmpty("tool_call_id"))
				break
			}
		}
	}
	if _, exists := firstValue("request_id", attributes); !exists {
		for _, alias := range []string{"request.id", "nemo_relay.request_id"} {
			if value, ok := firstValue(alias, attributes, resource); ok {
				value.CopyTo(attributes.PutEmpty("request_id"))
				break
			}
		}
	}
	if _, exists := firstValue("agent.session.id", attributes); !exists {
		for _, alias := range []string{"nemo_relay.session.instance_id", "nemo_relay.session_id"} {
			if value, ok := firstValue(alias, attributes, resource); ok {
				value.CopyTo(attributes.PutEmpty("agent.session.id"))
				break
			}
		}
	}

	invalid := append([]string{}, resourceInvalid...)
	invalid = append(invalid, sanitizeCorrelationIdentifiers(attributes)...)
	invalid = uniqueSortedStrings(invalid)
	if len(invalid) == 0 {
		attributes.Remove("openshell.correlation.invalid")
	} else {
		attributes.PutStr("openshell.correlation.invalid", strings.Join(invalid, ","))
	}

	missing := make([]string, 0, len(p.config.RequiredCorrelationAttributes))
	for _, key := range p.config.RequiredCorrelationAttributes {
		value, ok := firstValue(key, attributes, resource)
		if !ok || value.Type() != pcommon.ValueTypeStr || value.Str() == "" {
			missing = append(missing, key)
		}
	}
	if len(missing) == 0 && len(invalid) == 0 {
		attributes.PutStr("openshell.correlation.status", "complete")
		attributes.Remove("openshell.correlation.missing")
		return "complete"
	}
	attributes.PutStr("openshell.correlation.status", "partial")
	attributes.PutStr("openshell.correlation.missing", strings.Join(missing, ","))
	return "partial"
}

func sanitizeCorrelationIdentifiers(attributes pcommon.Map) []string {
	invalid := make([]string, 0)
	for _, key := range []string{
		"openshell.gateway.id",
		"openshell.workspace",
		"openshell.sandbox.id",
		"agent.session.id",
		"trace_id",
		"request_id",
		"tool_call_id",
		"openshell.policy.version",
	} {
		value, ok := attributes.Get(key)
		if !ok {
			continue
		}
		if isSafeCorrelationValue(key, value) {
			continue
		}
		attributes.Remove(key)
		invalid = append(invalid, key)
	}
	return invalid
}

func isSafeCorrelationValue(key string, value pcommon.Value) bool {
	if key == "openshell.policy.version" && value.Type() == pcommon.ValueTypeInt {
		return value.Int() >= 0
	}
	if value.Type() != pcommon.ValueTypeStr {
		return false
	}
	if key == "trace_id" {
		candidate := value.Str()
		if len(candidate) != 32 {
			return false
		}
		for index := range len(candidate) {
			character := candidate[index]
			if (character < '0' || character > '9') && (character < 'a' || character > 'f') {
				return false
			}
		}
		return true
	}
	return isSafeCorrelationString(value.Str())
}

func isSafeCorrelationString(candidate string) bool {
	if len(candidate) == 0 || len(candidate) > maxCorrelationIdentifierLength {
		return false
	}
	for index := range len(candidate) {
		character := candidate[index]
		if (character >= 'a' && character <= 'z') ||
			(character >= 'A' && character <= 'Z') ||
			(character >= '0' && character <= '9') {
			continue
		}
		switch character {
		case '.', '_', ':', '/', '@', '+', '-':
			continue
		default:
			return false
		}
	}
	return true
}

func uniqueSortedStrings(values []string) []string {
	if len(values) == 0 {
		return nil
	}
	sort.Strings(values)
	unique := values[:0]
	for _, value := range values {
		if len(unique) == 0 || unique[len(unique)-1] != value {
			unique = append(unique, value)
		}
	}
	return unique
}

func collectTraceContexts(resourceSpans ptrace.ResourceSpans) map[string]map[string]pcommon.Value {
	contexts := make(map[string]map[string]pcommon.Value)
	for scopeIndex := 0; scopeIndex < resourceSpans.ScopeSpans().Len(); scopeIndex++ {
		spans := resourceSpans.ScopeSpans().At(scopeIndex).Spans()
		for spanIndex := 0; spanIndex < spans.Len(); spanIndex++ {
			span := spans.At(spanIndex)
			traceID := span.TraceID().String()
			context := contexts[traceID]
			if context == nil {
				context = make(map[string]pcommon.Value)
				contexts[traceID] = context
			}
			for canonical, aliases := range map[string][]string{
				"agent.session.id": {"agent.session.id", "session.id", "nemo_relay.session.instance_id", "nemo_relay.session_id"},
				"request_id":       {"request_id", "request.id", "gen_ai.request.id", "nemo_relay.request_id"},
			} {
				if _, exists := context[canonical]; exists {
					continue
				}
				for _, alias := range aliases {
					if value, ok := firstValue(alias, span.Attributes()); ok {
						copyValue := pcommon.NewValueEmpty()
						value.CopyTo(copyValue)
						context[canonical] = copyValue
						break
					}
				}
			}
		}
	}
	return contexts
}

func firstValue(key string, maps ...pcommon.Map) (pcommon.Value, bool) {
	for _, values := range maps {
		if value, ok := values.Get(key); ok {
			if value.Type() == pcommon.ValueTypeStr && value.Str() == "" {
				continue
			}
			return value, true
		}
	}
	return pcommon.Value{}, false
}

func (p *processorImpl) filter(attributes pcommon.Map) int {
	removed := 0
	keys := make([]string, 0, attributes.Len())
	attributes.Range(func(key string, _ pcommon.Value) bool {
		keys = append(keys, key)
		return true
	})
	sort.Strings(keys)
	for _, key := range keys {
		if _, protected := protectedAttributes[key]; protected {
			continue
		}
		value, _ := attributes.Get(key)
		if p.config.Privacy.Mode == privacyAllow &&
			isUsageOrCostAttribute(key) &&
			!isBenignUsageAttribute(key, value) &&
			!isBenignCostAttribute(key, value) {
			attributes.Remove(key)
			removed++
			continue
		}
		if p.config.Privacy.Mode == privacyDeny && isBenignUsageAttribute(key, value) {
			continue
		}
		lower := strings.ToLower(key)
		keep := p.config.Privacy.Mode == privacyDeny
		patterns := p.config.Privacy.DeniedAttributes
		if p.config.Privacy.Mode == privacyAllow {
			patterns = p.config.Privacy.AllowedAttributes
			keep = false
		}
		for _, pattern := range patterns {
			matched, _ := path.Match(strings.ToLower(pattern), lower)
			if !matched {
				continue
			}
			keep = p.config.Privacy.Mode == privacyAllow
			break
		}
		if !keep {
			attributes.Remove(key)
			removed++
		}
	}
	return removed
}
