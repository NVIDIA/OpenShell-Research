// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package openshellprocessor

import (
	"errors"
	"fmt"
)

type Config struct {
	SourceProfiles    []string         `mapstructure:"source_profiles"`
	GatewayID         string           `mapstructure:"gateway_id"`
	Workspace         string           `mapstructure:"workspace"`
	SourceInstance    string           `mapstructure:"source_instance"`
	DefaultSandboxID  string           `mapstructure:"default_sandbox_id"`
	Validation        ValidationConfig `mapstructure:"validation"`
	Redaction         RedactionConfig  `mapstructure:"redaction"`
	CorrelationFields []string         `mapstructure:"correlation_fields"`
}

type ValidationConfig struct {
	Mode string `mapstructure:"mode"`
}

var supportedSourceProfiles = []string{
	"ocsf.file",
	"ocsf.forwarded",
	"openshell.log",
	"openshell.log.forwarded",
	"watchsandbox",
	"policy.reconciliation",
	"kubernetes.context",
	"nemo_relay.log",
	"nemo_relay.trace",
	"openshell.trace",
}

type RedactionConfig struct {
	ProfileID string   `mapstructure:"profile_id"`
	Version   string   `mapstructure:"version"`
	Keys      []string `mapstructure:"keys"`
	Patterns  []string `mapstructure:"patterns"`
}

func (cfg *Config) Validate() error {
	allowedProfiles := make(map[string]struct{}, len(supportedSourceProfiles))
	for _, profile := range supportedSourceProfiles {
		allowedProfiles[profile] = struct{}{}
	}
	if len(cfg.SourceProfiles) == 0 {
		return errors.New("source_profiles must declare at least one enabled profile")
	}
	seenProfiles := map[string]struct{}{}
	for _, profile := range cfg.SourceProfiles {
		if _, ok := allowedProfiles[profile]; !ok {
			return fmt.Errorf("unsupported source profile %q", profile)
		}
		if _, duplicate := seenProfiles[profile]; duplicate {
			return fmt.Errorf("duplicate source profile %q", profile)
		}
		seenProfiles[profile] = struct{}{}
	}
	if cfg.GatewayID == "" {
		return errors.New("gateway_id must not be empty")
	}
	if cfg.Workspace == "" {
		return errors.New("workspace must not be empty")
	}
	if cfg.SourceInstance == "" {
		return errors.New("source_instance must not be empty")
	}
	switch cfg.Validation.Mode {
	case "mark":
	default:
		return fmt.Errorf("validation.mode must be mark; dropping evidence is unsupported, got %q", cfg.Validation.Mode)
	}
	if cfg.Redaction.ProfileID == "" || cfg.Redaction.Version == "" {
		return errors.New("redaction profile_id and version must not be empty")
	}
	return nil
}
