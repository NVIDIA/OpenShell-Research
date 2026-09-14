// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package openshellprocessor

import (
	"fmt"
	"math"
)

const currentOCSFVersion = "1.8.0"

var supportedOCSFVersions = map[string]struct{}{
	"1.7.0":            {},
	currentOCSFVersion: {},
}

const (
	validationValid         = "valid"
	validationInvalid       = "invalid"
	validationNotApplicable = "not_applicable"
)

type validationResult struct {
	Status string
	Errors []string
}

func validateSource(kind string, original any) validationResult {
	if !isOCSFEvidence(kind) {
		return validationResult{Status: validationNotApplicable, Errors: []string{}}
	}
	event, ok := original.(map[string]any)
	if !ok {
		return validationResult{
			Status: validationInvalid,
			Errors: []string{"body must be a JSON object"},
		}
	}
	errs := make([]string, 0)
	for _, field := range []string{"class_uid", "category_uid", "activity_id", "type_uid"} {
		value, ok := exactInteger(event[field])
		if !ok || value < 0 || (field != "activity_id" && value == 0) {
			errs = append(errs, fmt.Sprintf("%s must be a positive integer", field))
		}
	}
	if value, ok := exactInteger(event["time"]); !ok || value <= 0 {
		errs = append(errs, "time must be a positive epoch-millisecond integer")
	}
	if severity, present := event["severity_id"]; present {
		if _, ok := exactInteger(severity); !ok {
			errs = append(errs, "severity_id must be an integer when present")
		}
	}
	metadata, ok := event["metadata"].(map[string]any)
	if !ok {
		errs = append(errs, "metadata must be an object")
	} else {
		version, ok := metadata["version"].(string)
		if !ok {
			errs = append(errs, "metadata.version must be a string")
		} else if _, supported := supportedOCSFVersions[version]; !supported {
			errs = append(
				errs,
				"metadata.version must equal 1.7.0 or 1.8.0",
			)
		}
	}
	if len(errs) > 0 {
		return validationResult{Status: validationInvalid, Errors: errs}
	}
	return validationResult{Status: validationValid, Errors: []string{}}
}

func exactInteger(value any) (int64, bool) {
	switch typed := value.(type) {
	case int:
		return int64(typed), true
	case int8:
		return int64(typed), true
	case int16:
		return int64(typed), true
	case int32:
		return int64(typed), true
	case int64:
		return typed, true
	case uint:
		return int64(typed), uint(int64(typed)) == typed
	case uint8:
		return int64(typed), true
	case uint16:
		return int64(typed), true
	case uint32:
		return int64(typed), true
	case uint64:
		return int64(typed), uint64(int64(typed)) == typed
	case float32:
		value := float64(typed)
		if math.IsNaN(value) || math.IsInf(value, 0) || math.Trunc(value) != value {
			return 0, false
		}
		return int64(value), float64(int64(value)) == value
	case float64:
		if math.IsNaN(typed) || math.IsInf(typed, 0) || math.Trunc(typed) != typed {
			return 0, false
		}
		return int64(typed), float64(int64(typed)) == typed
	default:
		return 0, false
	}
}
