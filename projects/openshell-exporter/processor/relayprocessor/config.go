// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package relayprocessor

import (
	"errors"
	"fmt"
	"path"
)

const (
	privacyAllow = "allow"
	privacyDeny  = "deny"
)

type Config struct {
	GatewayID                     string            `mapstructure:"gateway_id"`
	Workspace                     string            `mapstructure:"workspace"`
	TelemetrySource               string            `mapstructure:"telemetry_source"`
	CanonicalizeSpanNames         bool              `mapstructure:"canonicalize_span_names"`
	RequiredCorrelationAttributes []string          `mapstructure:"required_correlation_attributes"`
	Privacy                       PrivacyConfig     `mapstructure:"privacy"`
	Aliases                       map[string]string `mapstructure:"aliases"`
}

type PrivacyConfig struct {
	Mode              string   `mapstructure:"mode"`
	AllowedAttributes []string `mapstructure:"allowed_attributes"`
	DeniedAttributes  []string `mapstructure:"denied_attributes"`
}

func (cfg *Config) Validate() error {
	if cfg.GatewayID == "" {
		return errors.New("gateway_id must not be empty")
	}
	if !isSafeCorrelationString(cfg.GatewayID) {
		return errors.New("gateway_id must be a bounded ASCII identifier")
	}
	if cfg.Workspace == "" {
		return errors.New("workspace must not be empty")
	}
	if cfg.TelemetrySource == "" {
		return errors.New("telemetry_source must not be empty")
	}
	if !isSafeCorrelationString(cfg.TelemetrySource) {
		return errors.New("telemetry_source must be a bounded ASCII identifier")
	}
	seenRequired := map[string]struct{}{}
	for _, attribute := range cfg.RequiredCorrelationAttributes {
		if _, allowed := protectedAttributes[attribute]; !allowed {
			return fmt.Errorf("required correlation attribute %q is not protected", attribute)
		}
		if _, duplicate := seenRequired[attribute]; duplicate {
			return fmt.Errorf("duplicate required correlation attribute %q", attribute)
		}
		seenRequired[attribute] = struct{}{}
	}
	if !isSafeCorrelationString(cfg.Workspace) {
		return errors.New("workspace must be a bounded ASCII identifier")
	}
	switch cfg.Privacy.Mode {
	case privacyAllow:
		if len(cfg.Privacy.AllowedAttributes) == 0 {
			return errors.New("privacy.allowed_attributes must not be empty in allow mode")
		}
	case privacyDeny:
		if len(cfg.Privacy.DeniedAttributes) == 0 {
			return errors.New("privacy.denied_attributes must not be empty in deny mode")
		}
	default:
		return fmt.Errorf("privacy.mode must be allow or deny, got %q", cfg.Privacy.Mode)
	}
	for _, pattern := range append(append([]string{}, cfg.Privacy.AllowedAttributes...), cfg.Privacy.DeniedAttributes...) {
		if pattern == "" {
			return errors.New("privacy attribute patterns must not be empty")
		}
		if _, err := path.Match(pattern, pattern); err != nil {
			return fmt.Errorf("invalid privacy attribute pattern %q: %w", pattern, err)
		}
	}
	for canonical, alias := range cfg.Aliases {
		if canonical == "" || alias == "" {
			return errors.New("correlation aliases must have non-empty keys and values")
		}
	}
	return nil
}
