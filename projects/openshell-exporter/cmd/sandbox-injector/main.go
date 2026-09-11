package main

import (
	"crypto/tls"
	"log"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter/sandboxinjector"
)

func main() {
	config := sandboxinjector.Config{
		AllowedNamespaces:    splitRequired("SANDBOX_INJECTOR_ALLOWED_NAMESPACES"),
		AllowedUsernames:     splitRequired("SANDBOX_INJECTOR_ALLOWED_USERNAMES"),
		FluentBitImage:       required("SANDBOX_INJECTOR_FLUENT_BIT_IMAGE"),
		ExporterHost:         required("SANDBOX_INJECTOR_EXPORTER_HOST"),
		GatewayID:            required("SANDBOX_INJECTOR_GATEWAY_ID"),
		Workspace:            required("SANDBOX_INJECTOR_WORKSPACE"),
		AgentConfigMapName:   required("SANDBOX_INJECTOR_AGENT_CONFIG_MAP"),
		NetworkConfigMapName: required("SANDBOX_INJECTOR_NETWORK_CONFIG_MAP"),
		TLSSecretName:        required("SANDBOX_INJECTOR_FORWARDER_TLS_SECRET"),
		AuthSecretName:       required("SANDBOX_INJECTOR_FORWARDER_AUTH_SECRET"),
		EvidenceSize:         value("SANDBOX_INJECTOR_EVIDENCE_SIZE", "2Gi"),
		EvidenceClass:        os.Getenv("SANDBOX_INJECTOR_EVIDENCE_STORAGE_CLASS"),
		StateSize:            value("SANDBOX_INJECTOR_STATE_SIZE", "1Gi"),
		StateClass:           os.Getenv("SANDBOX_INJECTOR_STATE_STORAGE_CLASS"),
		CPURequest:           value("SANDBOX_INJECTOR_CPU_REQUEST", "25m"),
		MemoryRequest:        value("SANDBOX_INJECTOR_MEMORY_REQUEST", "64Mi"),
		CPULimit:             value("SANDBOX_INJECTOR_CPU_LIMIT", "250m"),
		MemoryLimit:          value("SANDBOX_INJECTOR_MEMORY_LIMIT", "256Mi"),
	}
	injector, err := sandboxinjector.New(config)
	if err != nil {
		log.Fatalf("invalid injector configuration: %v", err)
	}
	mux := http.NewServeMux()
	mux.Handle("/mutate", injector)
	mux.HandleFunc("/healthz", func(writer http.ResponseWriter, _ *http.Request) {
		writer.WriteHeader(http.StatusNoContent)
	})
	certFile := required("SANDBOX_INJECTOR_TLS_CERT_FILE")
	keyFile := required("SANDBOX_INJECTOR_TLS_KEY_FILE")
	if _, err := tls.LoadX509KeyPair(certFile, keyFile); err != nil {
		log.Fatalf("load injector TLS certificate: %v", err)
	}
	server := &http.Server{
		Addr:              value("SANDBOX_INJECTOR_LISTEN", ":9443"),
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      10 * time.Second,
		IdleTimeout:       30 * time.Second,
		TLSConfig: &tls.Config{
			MinVersion: tls.VersionTLS13,
			GetCertificate: func(*tls.ClientHelloInfo) (*tls.Certificate, error) {
				certificate, err := tls.LoadX509KeyPair(certFile, keyFile)
				return &certificate, err
			},
		},
	}
	log.Printf("OpenShell Sandbox evidence injector listening on %s", server.Addr)
	log.Fatal(server.ListenAndServeTLS("", ""))
}

func required(name string) string {
	result := strings.TrimSpace(os.Getenv(name))
	if result == "" {
		log.Fatalf("%s is required", name)
	}
	return result
}

func splitRequired(name string) []string {
	values := strings.Split(required(name), ",")
	result := make([]string, 0, len(values))
	for _, item := range values {
		if item = strings.TrimSpace(item); item != "" {
			result = append(result, item)
		}
	}
	return result
}

func value(name, fallback string) string {
	if result := strings.TrimSpace(os.Getenv(name)); result != "" {
		return result
	}
	return fallback
}
