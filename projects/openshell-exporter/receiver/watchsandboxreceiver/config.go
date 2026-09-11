// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package watchsandboxreceiver

import (
	"errors"
	"net/url"
	"time"

	"go.opentelemetry.io/collector/component"
)

type Config struct {
	Endpoint             string                     `mapstructure:"endpoint"`
	GatewayID            string                     `mapstructure:"gateway_id"`
	Workspace            string                     `mapstructure:"workspace"`
	SandboxNames         []string                   `mapstructure:"sandbox_names"`
	LabelSelector        string                     `mapstructure:"label_selector"`
	LogTailLines         uint32                     `mapstructure:"log_tail_lines"`
	EventTail            uint32                     `mapstructure:"event_tail"`
	ReconnectInitial     time.Duration              `mapstructure:"reconnect_initial"`
	ReconnectMax         time.Duration              `mapstructure:"reconnect_max"`
	DiscoveryInterval    time.Duration              `mapstructure:"discovery_interval"`
	TokenEnv             string                     `mapstructure:"token_env"`
	TokenFile            string                     `mapstructure:"token_file"`
	AllowInsecureHTTP    bool                       `mapstructure:"allow_insecure_http"`
	TLS                  TLSConfig                  `mapstructure:"tls"`
	PolicyReconciliation PolicyReconciliationConfig `mapstructure:"policy_reconciliation"`
}

type PolicyReconciliationConfig struct {
	Enabled                  bool          `mapstructure:"enabled"`
	Interval                 time.Duration `mapstructure:"interval"`
	Timeout                  time.Duration `mapstructure:"timeout"`
	Workers                  int           `mapstructure:"workers"`
	QueueSize                int           `mapstructure:"queue_size"`
	MaxRevisions             uint32        `mapstructure:"max_revisions"`
	IncludeHistory           bool          `mapstructure:"include_history"`
	IncludeEffectivePolicies bool          `mapstructure:"include_effective_policies"`
	StorageID                *component.ID `mapstructure:"storage"`
}

type TLSConfig struct {
	CAFile             string `mapstructure:"ca_file"`
	CertFile           string `mapstructure:"cert_file"`
	KeyFile            string `mapstructure:"key_file"`
	InsecureSkipVerify bool   `mapstructure:"insecure_skip_verify"`
}

func (cfg *Config) Validate() error {
	parsed, err := url.Parse(cfg.Endpoint)
	if err != nil || !parsed.IsAbs() || parsed.Host == "" {
		return errors.New("endpoint must be an absolute HTTP(S) URL")
	}
	if parsed.Scheme != "https" && parsed.Scheme != "http" {
		return errors.New("endpoint must use HTTP or HTTPS")
	}
	if parsed.Scheme != "https" && !cfg.AllowInsecureHTTP {
		return errors.New("endpoint must use HTTPS unless allow_insecure_http is true")
	}
	if parsed.User != nil {
		return errors.New("endpoint must not contain user information")
	}
	if (parsed.Path != "" && parsed.Path != "/") || parsed.RawQuery != "" || parsed.Fragment != "" {
		return errors.New("endpoint must not contain a path, query, or fragment")
	}
	if cfg.TLS.InsecureSkipVerify {
		return errors.New("tls.insecure_skip_verify is not supported; configure tls.ca_file instead")
	}
	if parsed.Scheme != "https" && (cfg.TokenEnv != "" || cfg.TokenFile != "") {
		return errors.New("OpenShell bearer credentials require an HTTPS endpoint")
	}
	if cfg.Workspace == "" {
		return errors.New("workspace must not be empty")
	}
	if cfg.DiscoveryInterval <= 0 {
		return errors.New("discovery_interval must be greater than zero")
	}
	if cfg.GatewayID == "" {
		return errors.New("gateway_id must not be empty")
	}
	if cfg.LogTailLines == 0 || cfg.EventTail == 0 {
		return errors.New("log_tail_lines and event_tail must be greater than zero")
	}
	if cfg.ReconnectInitial <= 0 || cfg.ReconnectMax < cfg.ReconnectInitial {
		return errors.New("reconnect durations must be positive and reconnect_max must not be smaller than reconnect_initial")
	}
	if cfg.LogTailLines > 10000 || cfg.EventTail > 10000 {
		return errors.New("tail sizes must not exceed 10000 records")
	}
	if (cfg.TLS.CertFile == "") != (cfg.TLS.KeyFile == "") {
		return errors.New("tls.cert_file and tls.key_file must be configured together")
	}
	if cfg.TokenEnv != "" && cfg.TokenFile != "" {
		return errors.New("token_env and token_file are mutually exclusive")
	}
	if cfg.PolicyReconciliation.Enabled {
		if cfg.PolicyReconciliation.Interval <= 0 {
			return errors.New("policy_reconciliation.interval must be greater than zero")
		}
		if cfg.PolicyReconciliation.Timeout <= 0 {
			return errors.New("policy_reconciliation.timeout must be greater than zero")
		}
		if cfg.PolicyReconciliation.Workers <= 0 || cfg.PolicyReconciliation.Workers > 16 {
			return errors.New("policy_reconciliation.workers must be between 1 and 16")
		}
		if cfg.PolicyReconciliation.QueueSize <= 0 || cfg.PolicyReconciliation.QueueSize > 10000 {
			return errors.New("policy_reconciliation.queue_size must be between 1 and 10000")
		}
		if cfg.PolicyReconciliation.MaxRevisions == 0 || cfg.PolicyReconciliation.MaxRevisions > 100000 {
			return errors.New("policy_reconciliation.max_revisions must be between 1 and 100000")
		}
		if cfg.PolicyReconciliation.StorageID == nil {
			return errors.New("policy_reconciliation.storage is required when reconciliation is enabled")
		}
	}
	if _, err := configuredBearerToken(cfg); err != nil {
		return err
	}
	return nil
}
