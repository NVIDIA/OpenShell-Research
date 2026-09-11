// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package openshellprocessor

import "strings"

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
		result := typed
		count := 0
		for _, pattern := range p.patterns {
			next := pattern.ReplaceAllString(result, redacted)
			if next != result {
				count++
			}
			result = next
		}
		return result, count
	default:
		return value, 0
	}
}
