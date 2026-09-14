// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package evidencecontractprocessor

import (
	"fmt"
	"regexp"
)

const (
	modeStamp  = "stamp"
	modeVerify = "verify"
	versionV1  = "1.0"
)

var tenantIDPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]{0,62}$`)

// Config controls the internal edge-to-central hand-off contract.
type Config struct {
	Mode            string `mapstructure:"mode"`
	ContractVersion string `mapstructure:"contract_version"`
	TenantID        string `mapstructure:"tenant_id"`
}

func (cfg *Config) Validate() error {
	if cfg.Mode != modeStamp && cfg.Mode != modeVerify {
		return fmt.Errorf("mode must be %q or %q, got %q", modeStamp, modeVerify, cfg.Mode)
	}
	if cfg.ContractVersion != versionV1 {
		return fmt.Errorf("contract_version must be %q, got %q", versionV1, cfg.ContractVersion)
	}
	if !tenantIDPattern.MatchString(cfg.TenantID) {
		return fmt.Errorf("tenant_id must match %s", tenantIDPattern)
	}
	return nil
}
