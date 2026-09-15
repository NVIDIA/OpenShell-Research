// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package cloudeventsexporter

import (
	"net/url"
	"strings"
	"testing"
	"time"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/config/configauth"
	"go.opentelemetry.io/collector/config/confighttp"
	"go.opentelemetry.io/collector/config/configmiddleware"
	"go.opentelemetry.io/collector/config/configopaque"
	"go.opentelemetry.io/collector/config/configoptional"
	"go.opentelemetry.io/collector/confmap"
)

func TestConfigUnmarshalPreservesExporterAndHTTPSettings(t *testing.T) {
	cfg := createDefaultConfig().(*Config)
	values := map[string]any{
		"endpoint":            "http://127.0.0.1:8080/v1/events",
		"allow_insecure_http": true,
		"default_source":      "openshell://configured-source",
		"max_events":          7,
		"keepalive":           map[string]any{"idle_conn_timeout": "12s"},
		"sending_queue":       map[string]any{"enabled": false},
	}
	if err := confmap.NewFromStringMap(values).Unmarshal(cfg); err != nil {
		t.Fatal(err)
	}
	if !cfg.AllowInsecureHTTP || cfg.MaxEvents != 7 || cfg.DefaultSource != "openshell://configured-source" || cfg.QueueConfig.HasValue() {
		t.Fatalf("exporter settings were lost: %#v", cfg)
	}
	// confighttp normalizes the new section into the effective legacy fields.
	if cfg.IdleConnTimeout != 12*time.Second { //nolint:staticcheck // Verify the upstream decoder's documented normalization.
		t.Fatal("HTTP keepalive settings were lost")
	}
	if err := cfg.Validate(); err != nil {
		t.Fatalf("decoded config rejected: %v", err)
	}
	values["unknown_exporter_setting"] = true
	if err := confmap.NewFromStringMap(values).Unmarshal(createDefaultConfig()); err == nil {
		t.Fatal("unknown settings must be rejected")
	}
}

func TestConfigAcceptsVerifiedHTTPSAndExplicitCredentialFreeHTTP(t *testing.T) {
	httpsConfig := validCloudEventsConfig()
	httpsConfig.Headers.Set("Authorization", configopaque.String("Bearer destination-token"))
	httpsConfig.Auth = configoptional.Some(configauth.Config{
		AuthenticatorID: component.NewID(component.MustNewType("bearertokenauth")),
	})
	if err := httpsConfig.Validate(); err != nil {
		t.Fatalf("verified HTTPS config rejected: %v", err)
	}

	httpConfig := validCloudEventsConfig()
	httpConfig.Endpoint = "http://127.0.0.1:8080/v1/events"
	httpConfig.AllowInsecureHTTP = true
	if err := httpConfig.Validate(); err != nil {
		t.Fatalf("explicit credential-free HTTP fixture rejected: %v", err)
	}
}

func TestConfigRejectsAmbiguousOrUnverifiedDestinations(t *testing.T) {
	tests := []struct {
		name      string
		want      string
		configure func(*Config)
	}{
		{name: "relative endpoint", want: "absolute HTTP(S)", configure: func(cfg *Config) { cfg.Endpoint = "/v1/events" }},
		{name: "missing hostname", want: "absolute HTTP(S)", configure: func(cfg *Config) { cfg.Endpoint = "https://:443/v1/events" }},
		{name: "unsupported scheme", want: "scheme must be http or https", configure: func(cfg *Config) { cfg.Endpoint = "ftp://receiver.example/events"; cfg.AllowInsecureHTTP = true }},
		{name: "userinfo", want: "user information", configure: func(cfg *Config) { cfg.Endpoint = "https://user:secret@receiver.example/v1/events" }},
		{name: "query", want: "query string", configure: func(cfg *Config) { cfg.Endpoint = "https://receiver.example/v1/events?token=secret" }},
		{name: "fragment", want: "fragment", configure: func(cfg *Config) { cfg.Endpoint = "https://receiver.example/v1/events#ignored" }},
		{name: "implicit HTTP", want: "must use HTTPS", configure: func(cfg *Config) { cfg.Endpoint = "http://127.0.0.1:8080/v1/events" }},
		{name: "TLS insecure", want: "cannot be disabled", configure: func(cfg *Config) { cfg.TLS.Insecure = true }},
		{name: "skip verification", want: "cannot be disabled", configure: func(cfg *Config) { cfg.TLS.InsecureSkipVerify = true }},
		{name: "invalid TLS version", want: "invalid TLS min_version", configure: func(cfg *Config) { cfg.TLS.MinVersion = "SSL3.0" }},
		{name: "HTTP headers", want: "cannot use authentication", configure: func(cfg *Config) {
			cfg.Endpoint = "http://receiver.test/v1/events"
			cfg.AllowInsecureHTTP = true
			cfg.Headers.Set("Authorization", configopaque.String("Bearer secret"))
		}},
		{name: "HTTP auth extension", want: "cannot use authentication", configure: func(cfg *Config) {
			cfg.Endpoint = "http://receiver.test/v1/events"
			cfg.AllowInsecureHTTP = true
			cfg.Auth = configoptional.Some(configauth.Config{AuthenticatorID: component.NewID(component.MustNewType("bearertokenauth"))})
		}},
		{name: "HTTP middleware", want: "cannot use authentication", configure: func(cfg *Config) {
			cfg.Endpoint = "http://receiver.test/v1/events"
			cfg.AllowInsecureHTTP = true
			cfg.Middlewares = []configmiddleware.Config{{ID: component.NewID(component.MustNewType("headersetter"))}}
		}},
		{name: "HTTP cookies", want: "cannot use authentication", configure: func(cfg *Config) {
			cfg.Endpoint = "http://receiver.test/v1/events"
			cfg.AllowInsecureHTTP = true
			cfg.Cookies = configoptional.Some(confighttp.CookiesConfig{})
		}},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			cfg := validCloudEventsConfig()
			test.configure(cfg)
			err := cfg.Validate()
			if err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("Validate() error=%v, want substring %q", err, test.want)
			}
		})
	}
}

func TestConfigRejectsInvalidContractLimits(t *testing.T) {
	tests := []struct {
		name      string
		want      string
		configure func(*Config)
	}{
		{name: "empty source", want: "default_source", configure: func(cfg *Config) { cfg.DefaultSource = "" }},
		{name: "zero events", want: "max_events", configure: func(cfg *Config) { cfg.MaxEvents = 0 }},
		{name: "too many events", want: "max_events", configure: func(cfg *Config) { cfg.MaxEvents = 501 }},
		{name: "zero event bytes", want: "max_event_bytes", configure: func(cfg *Config) { cfg.MaxEventBytes = 0 }},
		{name: "large event", want: "max_event_bytes", configure: func(cfg *Config) { cfg.MaxEventBytes = 1024*1024 + 1 }},
		{name: "tiny request", want: "max_request_bytes", configure: func(cfg *Config) { cfg.MaxRequestBytes = 2 }},
		{name: "large request", want: "max_request_bytes", configure: func(cfg *Config) { cfg.MaxRequestBytes = 4*1024*1024 + 1 }},
		{name: "event cannot fit", want: "fit at least one", configure: func(cfg *Config) { cfg.MaxEventBytes = 100; cfg.MaxRequestBytes = 101 }},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			cfg := validCloudEventsConfig()
			test.configure(cfg)
			err := cfg.Validate()
			if err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("Validate() error=%v, want substring %q", err, test.want)
			}
		})
	}
}

func validCloudEventsConfig() *Config {
	cfg := createDefaultConfig().(*Config)
	cfg.Endpoint = "https://receiver.example/v1/events"
	return cfg
}

func FuzzConfigTransportInvariants(f *testing.F) {
	f.Add("https://receiver.example/v1/events", false, true, false)
	f.Add("http://127.0.0.1:8080/v1/events", true, false, false)
	f.Add("https://user:secret@receiver.example/v1/events?token=x#ignored", true, true, true)
	f.Fuzz(func(t *testing.T, endpoint string, allowHTTP, withHeaders, skipVerify bool) {
		cfg := validCloudEventsConfig()
		cfg.Endpoint = endpoint
		cfg.AllowInsecureHTTP = allowHTTP
		cfg.TLS.InsecureSkipVerify = skipVerify
		if withHeaders {
			cfg.Headers.Set("Authorization", configopaque.String("Bearer fuzz-secret"))
		}
		if err := cfg.Validate(); err != nil {
			return
		}
		parsed, err := url.Parse(cfg.Endpoint)
		if err != nil {
			t.Fatalf("validated endpoint cannot be parsed: %v", err)
		}
		if parsed.Scheme != "http" && parsed.Scheme != "https" {
			t.Fatalf("validated unsupported scheme %q", parsed.Scheme)
		}
		if parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
			t.Fatalf("validated ambiguous endpoint: %q", cfg.Endpoint)
		}
		if cfg.TLS.Insecure || cfg.TLS.InsecureSkipVerify {
			t.Fatal("validated configuration disables TLS verification")
		}
		if parsed.Scheme == "http" && (len(cfg.Headers) > 0 || cfg.Auth.HasValue() ||
			len(cfg.Middlewares) > 0 || cfg.Cookies.HasValue()) {
			t.Fatal("validated plaintext configuration can carry credentials or state")
		}
	})
}
