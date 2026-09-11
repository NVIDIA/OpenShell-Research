// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package storagehealthextension

import (
	"context"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/extension"
)

var componentType = component.MustNewType("storagehealth")

func NewFactory() extension.Factory {
	return extension.NewFactory(
		componentType,
		createDefaultConfig,
		createExtension,
		component.StabilityLevelAlpha,
	)
}

func createDefaultConfig() component.Config {
	return &Config{
		Interval:   defaultInterval,
		MaxEntries: defaultMaxEntries,
		Paths:      map[string]string{},
	}
}

func createExtension(
	_ context.Context,
	settings extension.Settings,
	componentConfig component.Config,
) (extension.Extension, error) {
	config := componentConfig.(*Config)
	if err := config.Validate(); err != nil {
		return nil, err
	}
	return newStorageHealthExtension(config, settings)
}
