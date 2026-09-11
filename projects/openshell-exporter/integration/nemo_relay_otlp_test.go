// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package integration

import (
	"bytes"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/pem"
	"fmt"
	"math/big"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

const (
	relayTraceID = "00112233445566778899aabbccddeeff"
	relaySpanID  = "0011223344556677"
)

func TestNeMoRelayOTLPTLSAuthenticationAndCorrelation(t *testing.T) {
	binary := os.Getenv("TEST_EXPORTER_BINARY")
	if binary == "" {
		t.Skip("TEST_EXPORTER_BINARY is required for Collector integration")
	}
	absoluteBinary, err := filepath.Abs(binary)
	if err != nil {
		t.Fatal(err)
	}

	root := t.TempDir()
	caPath, certPath, keyPath := writeRelayTestCertificates(t, root)
	grpcPort := freeTCPPort(t)
	httpPort := freeTCPPort(t)
	grpcEndpoint := fmt.Sprintf("127.0.0.1:%d", grpcPort)
	httpEndpoint := fmt.Sprintf("https://127.0.0.1:%d/v1/traces", httpPort)
	capturePath := filepath.Join(root, "relay-traces.json")
	configPath := filepath.Join(root, "config.yaml")
	config := fmt.Sprintf(`extensions:
  bearertokenauth/nemo_relay:
    token: relay-test-token-that-is-not-a-production-secret
receivers:
  otlp/nemo_relay:
    protocols:
      grpc:
        endpoint: 127.0.0.1:%d
        auth:
          authenticator: bearertokenauth/nemo_relay
        tls:
          cert_file: %s
          key_file: %s
      http:
        endpoint: 127.0.0.1:%d
        auth:
          authenticator: bearertokenauth/nemo_relay
        tls:
          cert_file: %s
          key_file: %s
processors:
  resource/nemo_relay:
    attributes:
      - key: telemetry.source
        value: nemo_relay
        action: upsert
  relay:
    gateway_id: integration-gateway
    workspace: integration-workspace
exporters:
  file/capture:
    path: %s
    format: json
service:
  telemetry:
    metrics:
      level: none
  extensions: [bearertokenauth/nemo_relay]
  pipelines:
    traces:
      receivers: [otlp/nemo_relay]
      processors: [resource/nemo_relay, relay]
      exporters: [file/capture]
`, grpcPort, certPath, keyPath, httpPort, certPath, keyPath, capturePath)
	if err := os.WriteFile(configPath, []byte(config), 0o600); err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	command := exec.CommandContext(ctx, absoluteBinary, "--config", configPath)
	command.Stdout = os.Stdout
	command.Stderr = os.Stderr
	if err := command.Start(); err != nil {
		cancel()
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = command.Process.Signal(os.Interrupt)
		done := make(chan struct{})
		go func() {
			_ = command.Wait()
			close(done)
		}()
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			cancel()
			<-done
		}
	})

	payload := relayTracePayload(t)
	trustedClient := relayHTTPClient(t, caPath)
	waitFor(t, "authenticated OTLP receiver startup", func() bool {
		status, requestErr := postRelayTrace(trustedClient, httpEndpoint, "wrong-token", payload)
		return requestErr == nil && status == http.StatusUnauthorized
	})

	untrustedClient := &http.Client{Timeout: 2 * time.Second}
	if _, requestErr := postRelayTrace(untrustedClient, httpEndpoint, "relay-test-token-that-is-not-a-production-secret", payload); requestErr == nil {
		t.Fatal("expected an untrusted server certificate to fail")
	}
	if status, requestErr := postRelayTrace(trustedClient, httpEndpoint, "wrong-token", payload); requestErr != nil || status != http.StatusUnauthorized {
		t.Fatalf("wrong token status = %d, err = %v; want 401", status, requestErr)
	}
	if status, requestErr := postRelayTrace(trustedClient, httpEndpoint, "relay-test-token-that-is-not-a-production-secret", payload); requestErr != nil || status != http.StatusOK {
		t.Fatalf("authenticated HTTP trace status = %d, err = %v; want 200", status, requestErr)
	}
	if requestErr := exportRelayTraceGRPC(grpcEndpoint, caPath, "wrong-token", payload); status.Code(requestErr) != codes.Unauthenticated {
		t.Fatalf("wrong gRPC token error = %v; want Unauthenticated", requestErr)
	}
	if requestErr := exportRelayTraceGRPC(grpcEndpoint, caPath, "relay-test-token-that-is-not-a-production-secret", payload); requestErr != nil {
		t.Fatalf("authenticated gRPC trace failed: %v", requestErr)
	}

	waitFor(t, "preserved Relay correlation and source enrichment", func() bool {
		encoded, readErr := os.ReadFile(capturePath)
		if readErr != nil {
			return false
		}
		output := string(encoded)
		return strings.Contains(output, relayTraceID) &&
			strings.Contains(output, relaySpanID) &&
			strings.Contains(output, "nemo_relay") &&
			strings.Contains(output, "relay.span") &&
			strings.Contains(output, "integration-gateway") &&
			strings.Contains(output, "integration-workspace") &&
			strings.Contains(output, "sandbox-integration") &&
			strings.Contains(output, "session-integration") &&
			strings.Contains(output, "openshell.policy.version") &&
			strings.Contains(output, "trace_id") &&
			strings.Contains(output, "gen_ai.usage.input_tokens") &&
			strings.Contains(output, "llm.cost.total")
	})
	encoded, err := os.ReadFile(capturePath)
	if err != nil {
		t.Fatal(err)
	}
	for _, forbidden := range []string{
		"relay lifecycle",
		"private integration prompt",
		"source-invented-trace",
		"private mark result",
		"private error detail",
		"usage-shaped prompt",
		"private cost context",
	} {
		if strings.Contains(string(encoded), forbidden) {
			t.Fatalf("Relay processor leaked or trusted %q", forbidden)
		}
	}
}

func relayTracePayload(t *testing.T) []byte {
	t.Helper()
	request := ptraceotlp.NewExportRequest()
	resourceSpans := request.Traces().ResourceSpans().AppendEmpty()
	resourceSpans.Resource().Attributes().PutStr("service.name", "relay-integration")
	resourceSpans.Resource().Attributes().PutStr("openshell.sandbox.id", "sandbox-integration")
	resourceSpans.Resource().Attributes().PutInt("policy.version", 11)
	scopeSpans := resourceSpans.ScopeSpans().AppendEmpty()
	span := scopeSpans.Spans().AppendEmpty()
	traceBytes, err := hex.DecodeString(relayTraceID)
	if err != nil {
		t.Fatal(err)
	}
	var traceID pcommon.TraceID
	copy(traceID[:], traceBytes)
	spanBytes, err := hex.DecodeString(relaySpanID)
	if err != nil {
		t.Fatal(err)
	}
	var spanID pcommon.SpanID
	copy(spanID[:], spanBytes)
	span.SetTraceID(traceID)
	span.SetSpanID(spanID)
	span.SetName("relay lifecycle")
	span.SetStartTimestamp(pcommon.NewTimestampFromTime(time.Now().Add(-time.Millisecond)))
	span.SetEndTimestamp(pcommon.NewTimestampFromTime(time.Now()))
	span.Attributes().PutStr("nvidia.nemo.relay.event", "lifecycle")
	span.Attributes().PutStr("session.id", "session-integration")
	span.Attributes().PutStr("trace_id", "source-invented-trace")
	span.Attributes().PutStr("input.value", "private integration prompt")
	span.Attributes().PutStr("nemo_relay.mark.data.result", "private mark result")
	span.Attributes().PutStr("error.message", "private error detail")
	span.Attributes().PutStr("gen_ai.usage.prompt", "usage-shaped prompt")
	span.Attributes().PutInt("gen_ai.usage.input_tokens", 21)
	span.Attributes().PutStr("llm.cost.explanation", "private cost context")
	span.Attributes().PutDouble("llm.cost.total", 0.25)
	payload, err := request.MarshalProto()
	if err != nil {
		t.Fatal(err)
	}
	return payload
}

func postRelayTrace(client *http.Client, endpoint, token string, payload []byte) (int, error) {
	request, err := http.NewRequest(http.MethodPost, endpoint, bytes.NewReader(payload))
	if err != nil {
		return 0, err
	}
	request.Header.Set("Content-Type", "application/x-protobuf")
	request.Header.Set("Authorization", "Bearer "+token)
	response, err := client.Do(request)
	if err != nil {
		return 0, err
	}
	defer func() { _ = response.Body.Close() }()
	return response.StatusCode, nil
}

func exportRelayTraceGRPC(address, caPath, token string, payload []byte) error {
	encoded, err := os.ReadFile(caPath)
	if err != nil {
		return err
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(encoded) {
		return fmt.Errorf("load Relay test CA")
	}
	connection, err := grpc.NewClient(address, grpc.WithTransportCredentials(credentials.NewTLS(&tls.Config{
		MinVersion: tls.VersionTLS12,
		RootCAs:    pool,
	})))
	if err != nil {
		return err
	}
	defer func() { _ = connection.Close() }()
	request := ptraceotlp.NewExportRequest()
	if err := request.UnmarshalProto(payload); err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	ctx = metadata.AppendToOutgoingContext(ctx, "authorization", "Bearer "+token)
	_, err = ptraceotlp.NewGRPCClient(connection).Export(ctx, request)
	return err
}

func relayHTTPClient(t *testing.T, caPath string) *http.Client {
	t.Helper()
	encoded, err := os.ReadFile(caPath)
	if err != nil {
		t.Fatal(err)
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(encoded) {
		t.Fatal("failed to load Relay test CA")
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.TLSClientConfig.RootCAs = pool
	return &http.Client{Transport: transport, Timeout: 2 * time.Second}
}

func freeTCPPort(t *testing.T) int {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = listener.Close() }()
	return listener.Addr().(*net.TCPAddr).Port
}

func writeRelayTestCertificates(t *testing.T, root string) (string, string, string) {
	t.Helper()
	now := time.Now()
	caKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	caTemplate := &x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: "Relay integration CA"},
		NotBefore:             now.Add(-time.Minute),
		NotAfter:              now.Add(time.Hour),
		IsCA:                  true,
		BasicConstraintsValid: true,
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature,
	}
	caDER, err := x509.CreateCertificate(rand.Reader, caTemplate, caTemplate, &caKey.PublicKey, caKey)
	if err != nil {
		t.Fatal(err)
	}
	serverKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	serverTemplate := &x509.Certificate{
		SerialNumber: big.NewInt(2),
		Subject:      pkix.Name{CommonName: "127.0.0.1"},
		NotBefore:    now.Add(-time.Minute),
		NotAfter:     now.Add(time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		IPAddresses:  []net.IP{net.ParseIP("127.0.0.1")},
	}
	serverDER, err := x509.CreateCertificate(rand.Reader, serverTemplate, caTemplate, &serverKey.PublicKey, caKey)
	if err != nil {
		t.Fatal(err)
	}
	keyDER, err := x509.MarshalPKCS8PrivateKey(serverKey)
	if err != nil {
		t.Fatal(err)
	}
	caPath := filepath.Join(root, "ca.pem")
	certPath := filepath.Join(root, "server.pem")
	keyPath := filepath.Join(root, "server-key.pem")
	writePEMFile(t, caPath, "CERTIFICATE", caDER, 0o600)
	writePEMFile(t, certPath, "CERTIFICATE", serverDER, 0o600)
	writePEMFile(t, keyPath, "PRIVATE KEY", keyDER, 0o600)
	return caPath, certPath, keyPath
}

func writePEMFile(t *testing.T, path, blockType string, der []byte, mode os.FileMode) {
	t.Helper()
	encoded := pem.EncodeToMemory(&pem.Block{Type: blockType, Bytes: der})
	if err := os.WriteFile(path, encoded, mode); err != nil {
		t.Fatal(err)
	}
}
