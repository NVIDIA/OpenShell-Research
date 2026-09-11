// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package watchsandboxreceiver

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestLoadTLSConfigUsesVerifiedTLSAndPrivateTrust(t *testing.T) {
	caFile, certFile, keyFile := writeTestCertificate(t)
	config := createDefaultConfig().(*Config)
	config.TokenEnv = ""
	config.TLS.CAFile = caFile
	config.TLS.CertFile = certFile
	config.TLS.KeyFile = keyFile

	tlsConfig, err := loadTLSConfig(config, "gateway.internal")
	if err != nil {
		t.Fatal(err)
	}
	if tlsConfig.MinVersion != tls.VersionTLS12 {
		t.Fatalf("minimum TLS version=%d, want TLS 1.2", tlsConfig.MinVersion)
	}
	if tlsConfig.ServerName != "gateway.internal" {
		t.Fatalf("server name=%q", tlsConfig.ServerName)
	}
	if tlsConfig.InsecureSkipVerify {
		t.Fatal("certificate verification must never be disabled")
	}
	if tlsConfig.RootCAs == nil {
		t.Fatal("private CA pool was not loaded")
	}
	encodedCA, err := os.ReadFile(caFile)
	if err != nil {
		t.Fatal(err)
	}
	certificateBlock, _ := pem.Decode(encodedCA)
	if certificateBlock == nil {
		t.Fatal("test CA PEM could not be decoded")
	}
	caCertificate, err := x509.ParseCertificate(certificateBlock.Bytes)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := caCertificate.Verify(x509.VerifyOptions{
		Roots:     tlsConfig.RootCAs,
		KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageAny},
	}); err != nil {
		t.Fatalf("private CA did not verify against loaded trust: %v", err)
	}
	if len(tlsConfig.Certificates) != 1 {
		t.Fatalf("client certificate count=%d, want 1", len(tlsConfig.Certificates))
	}
}

func TestLoadTLSConfigFailsClosed(t *testing.T) {
	config := createDefaultConfig().(*Config)
	config.TokenEnv = ""

	config.TLS.InsecureSkipVerify = true
	if _, err := loadTLSConfig(config, "gateway.internal"); err == nil || !strings.Contains(err.Error(), "not supported") {
		t.Fatalf("expected insecure TLS rejection, got %v", err)
	}

	config.TLS.InsecureSkipVerify = false
	config.TLS.CAFile = filepath.Join(t.TempDir(), "missing-ca.pem")
	if _, err := loadTLSConfig(config, "gateway.internal"); err == nil || !strings.Contains(err.Error(), "read OpenShell CA") {
		t.Fatalf("expected missing CA error, got %v", err)
	}

	config.TLS.CAFile = filepath.Join(t.TempDir(), "invalid-ca.pem")
	if err := os.WriteFile(config.TLS.CAFile, []byte("not a certificate"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := loadTLSConfig(config, "gateway.internal"); err == nil || !strings.Contains(err.Error(), "contains no certificates") {
		t.Fatalf("expected invalid CA error, got %v", err)
	}
}

func TestBearerCredentialsAlwaysRequireTransportSecurity(t *testing.T) {
	credentials := bearerCredentials{token: "source-token"}
	metadata, err := credentials.GetRequestMetadata(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if metadata["authorization"] != "Bearer source-token" {
		t.Fatalf("authorization metadata=%q", metadata["authorization"])
	}
	if !credentials.RequireTransportSecurity() {
		t.Fatal("bearer credentials must require transport security")
	}
}

func TestNewGRPCGatewayClientValidatesSecurityAtConstruction(t *testing.T) {
	config := createDefaultConfig().(*Config)
	config.Endpoint = "http://127.0.0.1:50051"
	config.AllowInsecureHTTP = true
	config.TokenEnv = ""
	config.TokenFile = filepath.Join(t.TempDir(), "token")
	if err := os.WriteFile(config.TokenFile, []byte("source-token"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := newGRPCGatewayClient(config); err == nil || !strings.Contains(err.Error(), "require an HTTPS endpoint") {
		t.Fatalf("constructor accepted plaintext credentials: %v", err)
	}

	config.TokenFile = ""
	config.Endpoint = "https://127.0.0.1:65535"
	client, err := newGRPCGatewayClient(config)
	if err != nil {
		t.Fatalf("verified HTTPS client construction failed: %v", err)
	}
	if err := client.Close(); err != nil {
		t.Fatalf("close client: %v", err)
	}
}

func writeTestCertificate(t *testing.T) (string, string, string) {
	t.Helper()
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	template := &x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: "watchsandbox-test-ca"},
		NotBefore:             now.Add(-time.Minute),
		NotAfter:              now.Add(time.Hour),
		IsCA:                  true,
		BasicConstraintsValid: true,
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth},
	}
	certificateDER, err := x509.CreateCertificate(rand.Reader, template, template, publicKey, privateKey)
	if err != nil {
		t.Fatal(err)
	}
	privateDER, err := x509.MarshalPKCS8PrivateKey(privateKey)
	if err != nil {
		t.Fatal(err)
	}

	directory := t.TempDir()
	caFile := filepath.Join(directory, "ca.pem")
	certFile := filepath.Join(directory, "client.pem")
	keyFile := filepath.Join(directory, "client-key.pem")
	certificatePEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certificateDER})
	if err := os.WriteFile(caFile, certificatePEM, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(certFile, certificatePEM, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(keyFile, pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: privateDER}), 0o600); err != nil {
		t.Fatal(err)
	}
	return caFile, certFile, keyFile
}
