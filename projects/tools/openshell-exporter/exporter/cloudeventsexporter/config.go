// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package cloudeventsexporter

import (
	"errors"
	"fmt"
	"net/url"

	"go.opentelemetry.io/collector/config/confighttp"
	"go.opentelemetry.io/collector/config/configoptional"
	"go.opentelemetry.io/collector/config/configretry"
	"go.opentelemetry.io/collector/confmap"
	"go.opentelemetry.io/collector/exporter/exporterhelper"
)

// ClientConfig exposes HTTP fields for strict decoding without inheriting the
// upstream Unmarshal method, which ignores unknown sibling settings.
type ClientConfig confighttp.ClientConfig

type Config struct {
	ClientConfig      `mapstructure:",squash"`
	RetryConfig       configretry.BackOffConfig                                `mapstructure:"retry_on_failure"`
	QueueConfig       configoptional.Optional[exporterhelper.QueueBatchConfig] `mapstructure:"sending_queue"`
	AllowInsecureHTTP bool                                                     `mapstructure:"allow_insecure_http"`
	MaxEvents         int                                                      `mapstructure:"max_events"`
	MaxEventBytes     int                                                      `mapstructure:"max_event_bytes"`
	MaxRequestBytes   int                                                      `mapstructure:"max_request_bytes"`
	DefaultSource     string                                                   `mapstructure:"default_source"`
}

// Unmarshal decodes the complete configuration strictly before letting the HTTP
// client normalize its deprecated and current keepalive settings.
func (cfg *Config) Unmarshal(conf *confmap.Conf) error {
	if err := conf.Unmarshal(cfg); err != nil {
		return err
	}
	return (*confighttp.ClientConfig)(&cfg.ClientConfig).Unmarshal(conf)
}

func (cfg *Config) Validate() error {
	if err := (*confighttp.ClientConfig)(&cfg.ClientConfig).Validate(); err != nil {
		return err
	}
	if err := cfg.TLS.Validate(); err != nil {
		return fmt.Errorf("tls: %w", err)
	}
	if err := cfg.Headers.Validate(); err != nil {
		return fmt.Errorf("headers: %w", err)
	}
	parsed, err := url.Parse(cfg.Endpoint)
	if err != nil || !parsed.IsAbs() || parsed.Host == "" || parsed.Hostname() == "" {
		return errors.New("endpoint must be an absolute HTTP(S) URL")
	}
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return errors.New("endpoint scheme must be http or https")
	}
	if parsed.User != nil {
		return errors.New("endpoint must not contain user information")
	}
	if parsed.RawQuery != "" {
		return errors.New("endpoint must not contain a query string")
	}
	if parsed.Fragment != "" {
		return errors.New("endpoint must not contain a fragment")
	}
	if cfg.TLS.Insecure || cfg.TLS.InsecureSkipVerify {
		return errors.New("TLS verification cannot be disabled")
	}
	if parsed.Scheme == "http" && !cfg.AllowInsecureHTTP {
		return errors.New("endpoint must use HTTPS unless allow_insecure_http is true")
	}
	if parsed.Scheme == "http" && (cfg.Auth.HasValue() || len(cfg.Headers) > 0 ||
		len(cfg.Middlewares) > 0 || cfg.Cookies.HasValue()) {
		return errors.New("HTTP endpoints cannot use authentication, headers, middleware, or cookies")
	}
	if cfg.DefaultSource == "" {
		return errors.New("default_source must not be empty")
	}
	if cfg.MaxEvents <= 0 || cfg.MaxEvents > 500 {
		return errors.New("max_events must be between 1 and 500")
	}
	if cfg.MaxEventBytes <= 0 || cfg.MaxEventBytes > 1024*1024 {
		return errors.New("max_event_bytes must be between 1 and 1048576")
	}
	if cfg.MaxRequestBytes <= 2 || cfg.MaxRequestBytes > 4*1024*1024 {
		return errors.New("max_request_bytes must be between 3 and 4194304")
	}
	if cfg.MaxRequestBytes < cfg.MaxEventBytes+2 {
		return errors.New("max_request_bytes must fit at least one serialized event")
	}
	return nil
}
