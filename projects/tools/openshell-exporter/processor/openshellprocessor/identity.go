// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package openshellprocessor

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"

	"go.opentelemetry.io/collector/pdata/plog"
)

func stableID(config *Config, record plog.LogRecord, kind string, original any) string {
	identitySource := original
	if !isFileEvidence(kind) {
		if object, ok := original.(map[string]any); ok {
			compatibilityProjection := make(map[string]any, len(object))
			for key, value := range object {
				if key != "source_payload" {
					compatibilityProjection[key] = value
				}
			}
			identitySource = compatibilityProjection
		}
	}
	body, _ := json.Marshal(identitySource)
	bodyHash := sha256.Sum256(body)
	identity := map[string]any{
		"gateway_id": config.GatewayID,
		"workspace":  stringAttribute(record, "openshell.workspace", config.Workspace),
		"kind":       kind,
		"body_hash":  hex.EncodeToString(bodyHash[:]),
	}
	attributes := record.Attributes()
	if isFileEvidence(kind) {
		identity["source_instance"] = config.SourceInstance
		if isForwardedFile(kind) {
			identity["source_instance"] = stringAttribute(
				record,
				"openshell.acquisition.source_instance",
				config.SourceInstance,
			)
		}
		for _, key := range []string{
			"log.file.path",
			"log.file.path_resolved",
			"log.file.record_offset",
			"log.file.record_number",
		} {
			if value, ok := attributes.Get(key); ok {
				identity[key] = value.AsRaw()
			}
		}
	} else {
		for _, key := range []string{
			"openshell.sandbox.id",
			"openshell.resource.version",
			"openshell.event.kind",
			"k8s.object.uid",
			"k8s.object.resource_version",
		} {
			if value, ok := attributes.Get(key); ok {
				identity[key] = value.AsRaw()
			}
		}
	}
	encoded, _ := json.Marshal(identity)
	sum := sha256.Sum256(encoded)
	return "sha256:" + hex.EncodeToString(sum[:])
}
