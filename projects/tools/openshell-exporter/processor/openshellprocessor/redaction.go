// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package openshellprocessor

import (
	"strings"

	"go.opentelemetry.io/collector/pdata/pcommon"
)

// Sanitize the pdata containers as well as the envelope's copies. File and OTLP
// exporters serialize these attributes independently of the log body.
func (p *processorImpl) redactAttributes(attributes pcommon.Map) {
	attributes.Range(func(key string, value pcommon.Value) bool {
		if _, sensitive := p.redactedKeys[strings.ToLower(key)]; sensitive {
			value.SetStr(redacted)
		} else {
			p.redactValue(value)
		}
		return true
	})
}

func (p *processorImpl) redactValue(value pcommon.Value) {
	switch value.Type() {
	case pcommon.ValueTypeMap:
		p.redactAttributes(value.Map())
	case pcommon.ValueTypeSlice:
		values := value.Slice()
		for i := 0; i < values.Len(); i++ {
			p.redactValue(values.At(i))
		}
	case pcommon.ValueTypeStr:
		if text, count := p.redactString(value.Str()); count > 0 {
			value.SetStr(text)
		}
	}
}

func (p *processorImpl) redactString(value string) (string, int) {
	count := 0
	for _, pattern := range p.patterns {
		// Most evidence strings contain no secret. Avoid allocating a replacement
		// copy on that path, including when checking an already-redacted envelope.
		if !pattern.MatchString(value) {
			continue
		}
		next := pattern.ReplaceAllString(value, redacted)
		if next != value {
			count++
		}
		value = next
	}
	return value, count
}

func (p *processorImpl) redactRaw(value any) (any, int) {
	switch typed := value.(type) {
	case map[string]any:
		result := make(map[string]any, len(typed))
		count := 0
		for key, child := range typed {
			if _, sensitive := p.redactedKeys[strings.ToLower(key)]; sensitive {
				result[key] = redacted
				count++
				continue
			}
			redactedChild, childCount := p.redactRaw(child)
			result[key] = redactedChild
			count += childCount
		}
		return result, count
	case []any:
		result := make([]any, len(typed))
		count := 0
		for index, child := range typed {
			redactedChild, childCount := p.redactRaw(child)
			result[index] = redactedChild
			count += childCount
		}
		return result, count
	case string:
		return p.redactString(typed)
	default:
		return value, 0
	}
}
