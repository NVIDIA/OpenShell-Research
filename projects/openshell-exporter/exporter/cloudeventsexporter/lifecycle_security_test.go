// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package cloudeventsexporter

import (
	"context"
	"encoding/pem"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/config/configopaque"
	"go.opentelemetry.io/collector/consumer/consumererror"
	"go.opentelemetry.io/collector/exporter"
	"go.uber.org/zap"
)

type cloudEventsTestHost struct{}

func (cloudEventsTestHost) GetExtensions() map[component.ID]component.Component {
	return map[component.ID]component.Component{}
}

func TestExporterLifecycleUsesVerifiedTLSAndBearerAuthentication(t *testing.T) {
	const token = "destination-token-that-is-long-enough-for-a-real-secret"
	var accepted atomic.Int32
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.TLS == nil {
			t.Error("request did not use TLS")
		}
		if request.Header.Get("Authorization") != "Bearer "+token {
			writer.WriteHeader(http.StatusUnauthorized)
			return
		}
		accepted.Add(1)
		writer.WriteHeader(http.StatusAccepted)
	}))
	server.Config.ErrorLog = log.New(io.Discard, "", 0)
	server.StartTLS()
	defer server.Close()

	cfg := validCloudEventsConfig()
	cfg.Endpoint = server.URL + "/v1/events"
	cfg.TLS.CAFile = writeServerCA(t, server)
	cfg.Headers.Set("Authorization", configopaque.String("Bearer "+token))
	implementation := lifecycleExporter(cfg)
	if err := implementation.start(context.Background(), cloudEventsTestHost{}); err != nil {
		t.Fatal(err)
	}
	if err := implementation.pushLogs(context.Background(), oneLog(t, "verified evidence")); err != nil {
		t.Fatal(err)
	}
	if accepted.Load() != 1 {
		t.Fatalf("accepted requests=%d, want 1", accepted.Load())
	}
	if err := implementation.shutdown(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := implementation.shutdown(context.Background()); err != nil {
		t.Fatalf("second shutdown failed: %v", err)
	}
}

func TestExporterRejectsUntrustedDestinationCertificate(t *testing.T) {
	var received atomic.Int32
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		received.Add(1)
		writer.WriteHeader(http.StatusAccepted)
	}))
	server.Config.ErrorLog = log.New(io.Discard, "", 0)
	server.StartTLS()
	defer server.Close()

	cfg := validCloudEventsConfig()
	cfg.Endpoint = server.URL + "/v1/events"
	implementation := lifecycleExporter(cfg)
	if err := implementation.start(context.Background(), cloudEventsTestHost{}); err != nil {
		t.Fatal(err)
	}
	err := implementation.pushLogs(context.Background(), oneLog(t, "untrusted evidence"))
	if err == nil || consumererror.IsPermanent(err) {
		t.Fatalf("untrusted certificate error=%v, want retryable transport failure", err)
	}
	if received.Load() != 0 {
		t.Fatal("application handler received evidence across an untrusted TLS connection")
	}
}

func TestExporterStartRevalidatesSecurityConfiguration(t *testing.T) {
	cfg := validCloudEventsConfig()
	cfg.Endpoint = "http://receiver.test/v1/events"
	cfg.AllowInsecureHTTP = true
	cfg.Headers.Set("Authorization", configopaque.String("Bearer must-not-cross-plaintext"))
	implementation := lifecycleExporter(cfg)
	err := implementation.start(context.Background(), cloudEventsTestHost{})
	if err == nil {
		t.Fatal("start accepted destination credentials over plaintext HTTP")
	}
}

func TestFactoryValidatesConfiguration(t *testing.T) {
	factory := NewFactory()
	if factory.Type() != componentType {
		t.Fatalf("factory type=%q, want %q", factory.Type(), componentType)
	}
	if _, ok := factory.CreateDefaultConfig().(*Config); !ok {
		t.Fatal("factory returned an unexpected default config type")
	}

	cfg := validCloudEventsConfig()
	cfg.Endpoint = "ftp://receiver.example/v1/events"
	_, err := createLogsExporter(context.Background(), exporter.Settings{
		ID:                component.NewID(componentType),
		TelemetrySettings: component.TelemetrySettings{Logger: zap.NewNop()},
	}, cfg)
	if err == nil {
		t.Fatal("factory accepted an unsupported destination scheme")
	}
}

func lifecycleExporter(cfg *Config) *cloudEventsExporter {
	return &cloudEventsExporter{
		config: cfg,
		settings: exporter.Settings{TelemetrySettings: component.TelemetrySettings{
			Logger: zap.NewNop(),
		}},
		metrics: newExporterMetrics(nil),
	}
}

func writeServerCA(t *testing.T, server *httptest.Server) string {
	t.Helper()
	certificate := server.Certificate()
	if certificate == nil {
		t.Fatal("TLS server certificate is missing")
	}
	path := filepath.Join(t.TempDir(), "server-ca.pem")
	encoded := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certificate.Raw})
	if err := os.WriteFile(path, encoded, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}
