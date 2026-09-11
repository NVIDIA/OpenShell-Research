// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package watchsandboxreceiver

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"go.opentelemetry.io/collector/component"
)

func TestConfigRejectsInsecureHTTPByDefault(t *testing.T) {
	config := createDefaultConfig().(*Config)
	config.Endpoint = "http://127.0.0.1:50051"
	config.TokenEnv = ""
	if err := config.Validate(); err == nil {
		t.Fatal("HTTP endpoint should require explicit local-development opt-in")
	}
	config.AllowInsecureHTTP = true
	if err := config.Validate(); err != nil {
		t.Fatalf("explicit insecure HTTP should validate: %v", err)
	}
}

func TestConfigRejectsCredentialsOverInsecureHTTP(t *testing.T) {
	config := createDefaultConfig().(*Config)
	config.Endpoint = "http://127.0.0.1:50051"
	config.AllowInsecureHTTP = true
	config.TokenEnv = ""
	config.TokenFile = filepath.Join(t.TempDir(), "token")
	if err := os.WriteFile(config.TokenFile, []byte("must-not-cross-http\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := config.Validate(); err == nil || !strings.Contains(err.Error(), "require an HTTPS endpoint") {
		t.Fatalf("expected HTTPS credential error, got %v", err)
	}
}

func TestConfigRejectsCertificateVerificationBypass(t *testing.T) {
	config := createDefaultConfig().(*Config)
	config.TokenEnv = ""
	config.TLS.InsecureSkipVerify = true
	if err := config.Validate(); err == nil || !strings.Contains(err.Error(), "not supported") {
		t.Fatalf("expected certificate verification error, got %v", err)
	}
}

func TestConfigRejectsAmbiguousEndpointComponents(t *testing.T) {
	for _, endpoint := range []string{
		"https://user:password@gateway.example:8080",
		"https://gateway.example:8080/grpc",
		"https://gateway.example:8080?workspace=default",
		"https://gateway.example:8080#fragment",
	} {
		t.Run(endpoint, func(t *testing.T) {
			config := createDefaultConfig().(*Config)
			config.TokenEnv = ""
			config.Endpoint = endpoint
			if err := config.Validate(); err == nil {
				t.Fatalf("endpoint %q should be rejected", endpoint)
			}
		})
	}

	config := createDefaultConfig().(*Config)
	config.TokenEnv = ""
	config.Endpoint = "https://gateway.example:8080/"
	if err := config.Validate(); err != nil {
		t.Fatalf("root endpoint should validate: %v", err)
	}
}

func TestConfigAcceptsBearerTokenFile(t *testing.T) {
	config := createDefaultConfig().(*Config)
	config.TokenEnv = ""
	config.TokenFile = filepath.Join(t.TempDir(), "token")
	if err := os.WriteFile(config.TokenFile, []byte("secret-token\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := config.Validate(); err != nil {
		t.Fatalf("token file should validate: %v", err)
	}
	token, err := configuredBearerToken(config)
	if err != nil {
		t.Fatal(err)
	}
	if token != "secret-token" {
		t.Fatalf("unexpected token %q", token)
	}
}

func TestConfigRejectsAmbiguousBearerTokenSources(t *testing.T) {
	config := createDefaultConfig().(*Config)
	config.TokenFile = filepath.Join(t.TempDir(), "token")
	if err := config.Validate(); err == nil || !strings.Contains(err.Error(), "mutually exclusive") {
		t.Fatalf("expected mutually exclusive error, got %v", err)
	}
}

func TestConfigRejectsEmptyBearerTokenFile(t *testing.T) {
	config := createDefaultConfig().(*Config)
	config.TokenEnv = ""
	config.TokenFile = filepath.Join(t.TempDir(), "token")
	if err := os.WriteFile(config.TokenFile, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := config.Validate(); err == nil || !strings.Contains(err.Error(), "empty") {
		t.Fatalf("expected empty token error, got %v", err)
	}
}

func TestPolicyReconciliationRequiresPersistentStorageAndBoundedSettings(t *testing.T) {
	config := createDefaultConfig().(*Config)
	config.TokenEnv = ""
	config.PolicyReconciliation.Enabled = true
	if err := config.Validate(); err == nil || !strings.Contains(err.Error(), "storage is required") {
		t.Fatalf("expected policy storage error, got %v", err)
	}

	storageID := component.NewID(component.MustNewType("file_storage"))
	config.PolicyReconciliation.StorageID = &storageID
	if err := config.Validate(); err != nil {
		t.Fatalf("valid policy reconciliation config was rejected: %v", err)
	}

	config.PolicyReconciliation.Workers = 17
	if err := config.Validate(); err == nil || !strings.Contains(err.Error(), "workers") {
		t.Fatalf("expected bounded worker error, got %v", err)
	}

	config.PolicyReconciliation.Workers = 2
	config.PolicyReconciliation.MaxRevisions = 0
	if err := config.Validate(); err == nil || !strings.Contains(err.Error(), "max_revisions") {
		t.Fatalf("expected zero revision limit error, got %v", err)
	}
	config.PolicyReconciliation.MaxRevisions = 100001
	if err := config.Validate(); err == nil || !strings.Contains(err.Error(), "max_revisions") {
		t.Fatalf("expected excessive revision limit error, got %v", err)
	}
}
