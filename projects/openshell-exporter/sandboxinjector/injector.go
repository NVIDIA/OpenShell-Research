// Package sandboxinjector injects an operator-owned evidence forwarder into
// OpenShell Agent Sandbox resources before their Pods are created.
package sandboxinjector

import (
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"slices"
	"strings"
)

const (
	AnnotationInjected    = "observability.openshell.nvidia.com/evidence-injected"
	AnnotationFileProfile = "observability.openshell.nvidia.com/file-evidence-profile"
	AnnotationNetworkLane = "observability.openshell.nvidia.com/network-file-lane"
	AnnotationSandboxID   = "openshell.io/sandbox-id"
	AgentContainer        = "agent"
	NetworkContainer      = "openshell-supervisor-network"
	ForwarderContainer    = "openshell-evidence-forwarder"
	AgentEvidenceVolume   = "openshell-evidence-agent"
	NetworkEvidenceVolume = "openshell-evidence-network"
	ForwarderStateVolume  = "openshell-forwarder-state"
	ForwarderConfigVolume = "openshell-forwarder-config"
	ForwarderTLSVolume    = "openshell-forwarder-tls"
)

var digestReference = regexp.MustCompile(`^[^[:space:]]+@sha256:[0-9a-f]{64}$`)

// Config is the immutable, operator-owned injection policy.
type Config struct {
	AllowedNamespaces    []string
	AllowedUsernames     []string
	FluentBitImage       string
	ExporterHost         string
	GatewayID            string
	Workspace            string
	AgentConfigMapName   string
	NetworkConfigMapName string
	TLSSecretName        string
	AuthSecretName       string
	EvidenceSize         string
	EvidenceClass        string
	StateSize            string
	StateClass           string
	CPURequest           string
	MemoryRequest        string
	CPULimit             string
	MemoryLimit          string
}

// Validate rejects incomplete policies and mutable sidecar images.
func (config Config) Validate() error {
	if len(config.AllowedNamespaces) == 0 || len(config.AllowedUsernames) == 0 {
		return errors.New("allowed namespaces and OpenShell gateway usernames are required")
	}
	if !digestReference.MatchString(config.FluentBitImage) {
		return errors.New("fluent Bit image must be an immutable digest reference")
	}
	required := map[string]string{
		"exporter host": config.ExporterHost, "gateway ID": config.GatewayID,
		"workspace": config.Workspace, "agent ConfigMap name": config.AgentConfigMapName,
		"network ConfigMap name": config.NetworkConfigMapName,
		"TLS Secret name":        config.TLSSecretName, "authentication Secret name": config.AuthSecretName,
		"evidence size": config.EvidenceSize, "state size": config.StateSize,
		"CPU request": config.CPURequest, "memory request": config.MemoryRequest,
		"CPU limit": config.CPULimit, "memory limit": config.MemoryLimit,
	}
	for name, value := range required {
		if strings.TrimSpace(value) == "" {
			return fmt.Errorf("%s is required", name)
		}
	}
	if strings.Contains(config.ExporterHost, "://") || strings.ContainsAny(config.ExporterHost, "/ :") {
		return errors.New("exporter host must be a DNS name without scheme, port, or path")
	}
	return nil
}

// Injector applies the trusted sidecar policy.
type Injector struct {
	config Config
}

// New constructs an Injector.
func New(config Config) (*Injector, error) {
	if err := config.Validate(); err != nil {
		return nil, err
	}
	return &Injector{config: config}, nil
}

// Authorized reports whether an admission request is in the explicit trust set.
func (injector *Injector) Authorized(namespace, username string) bool {
	return slices.Contains(injector.config.AllowedNamespaces, namespace) &&
		slices.Contains(injector.config.AllowedUsernames, username)
}

// Patch returns a fail-closed JSON Patch for one Agent Sandbox object.
func (injector *Injector) Patch(raw json.RawMessage) ([]byte, error) {
	var object map[string]any
	if err := json.Unmarshal(raw, &object); err != nil {
		return nil, errors.New("sandbox object is not valid JSON")
	}
	spec, ok := object["spec"].(map[string]any)
	if !ok {
		return nil, errors.New("sandbox spec is missing")
	}
	podTemplate, ok := spec["podTemplate"].(map[string]any)
	if !ok {
		return nil, errors.New("sandbox podTemplate is missing")
	}
	podSpec, ok := podTemplate["spec"].(map[string]any)
	if !ok {
		return nil, errors.New("sandbox podTemplate.spec is missing")
	}
	if serviceAccountToken, exists := podSpec["automountServiceAccountToken"]; exists && serviceAccountToken != false {
		return nil, errors.New("sandbox must disable automatic service-account token mounting")
	}
	podSpec["automountServiceAccountToken"] = false
	containers, ok := podSpec["containers"].([]any)
	if !ok || len(containers) == 0 {
		return nil, errors.New("sandbox has no containers")
	}
	agentIndex := objectIndex(containers, AgentContainer)
	if agentIndex < 0 {
		return nil, errors.New("sandbox agent container is missing")
	}
	if objectIndex(containers, ForwarderContainer) >= 0 {
		return nil, errors.New("untrusted container uses the reserved evidence-forwarder name")
	}
	if annotation(podTemplate, AnnotationSandboxID) == "" {
		return nil, errors.New("sandbox podTemplate is missing the authoritative OpenShell sandbox ID annotation")
	}

	claims, _ := spec["volumeClaimTemplates"].([]any)
	reservedNames := []string{
		AgentEvidenceVolume, NetworkEvidenceVolume, ForwarderStateVolume,
		ForwarderConfigVolume, ForwarderTLSVolume,
	}
	for _, name := range reservedNames {
		if claimIndex(claims, name) >= 0 {
			return nil, fmt.Errorf("sandbox already defines reserved volume claim %q", name)
		}
	}
	volumes, _ := podSpec["volumes"].([]any)
	for _, name := range reservedNames {
		if objectIndex(volumes, name) >= 0 {
			return nil, fmt.Errorf("sandbox already defines reserved volume %q", name)
		}
	}

	agent := containers[agentIndex].(map[string]any)
	if err := addMount(agent, AgentEvidenceVolume, "/var/log", false); err != nil {
		return nil, err
	}
	claims = append(claims, injector.claim(AgentEvidenceVolume, injector.config.EvidenceSize, injector.config.EvidenceClass))
	evidenceMounts := []any{mount(AgentEvidenceVolume, "/evidence/agent", true)}
	configMapName := injector.config.AgentConfigMapName
	fileProfile := "agent-only"
	networkLane := "unavailable"

	if networkIndex := objectIndex(containers, NetworkContainer); networkIndex >= 0 {
		network := containers[networkIndex].(map[string]any)
		if err := addMount(network, NetworkEvidenceVolume, "/var/log", false); err != nil {
			return nil, err
		}
		claims = append(claims, injector.claim(NetworkEvidenceVolume, injector.config.EvidenceSize, injector.config.EvidenceClass))
		evidenceMounts = append(evidenceMounts, mount(NetworkEvidenceVolume, "/evidence/network", true))
		configMapName = injector.config.NetworkConfigMapName
		fileProfile = "agent-and-network"
		networkLane = "enabled"
	}
	claims = append(claims, injector.claim(ForwarderStateVolume, injector.config.StateSize, injector.config.StateClass))
	if err := ensurePodFSGroup(podSpec); err != nil {
		return nil, err
	}
	volumes = append(volumes,
		map[string]any{"name": ForwarderConfigVolume, "configMap": map[string]any{"name": configMapName}},
		map[string]any{"name": ForwarderTLSVolume, "secret": map[string]any{
			"secretName": injector.config.TLSSecretName, "defaultMode": 288,
		}},
	)
	containers = append(containers, injector.sidecar(podSpec, evidenceMounts))

	metadata := mapValue(podTemplate, "metadata")
	annotations := mapValue(metadata, "annotations")
	annotations[AnnotationInjected] = "v1"
	annotations[AnnotationFileProfile] = fileProfile
	annotations[AnnotationNetworkLane] = networkLane
	podSpec["containers"] = containers
	podSpec["volumes"] = volumes

	claimOperation := "replace"
	if _, exists := spec["volumeClaimTemplates"]; !exists {
		claimOperation = "add"
	}
	operations := []map[string]any{
		{"op": "replace", "path": "/spec/podTemplate", "value": podTemplate},
		{"op": claimOperation, "path": "/spec/volumeClaimTemplates", "value": claims},
	}
	return json.Marshal(operations)
}

func (injector *Injector) claim(name, size, class string) map[string]any {
	spec := map[string]any{
		"accessModes": []any{"ReadWriteOncePod"},
		"resources":   map[string]any{"requests": map[string]any{"storage": size}},
	}
	if class != "" {
		spec["storageClassName"] = class
	}
	return map[string]any{"metadata": map[string]any{"name": name}, "spec": spec}
}

func (injector *Injector) sidecar(_ map[string]any, evidenceMounts []any) map[string]any {
	mounts := append(slices.Clone(evidenceMounts),
		mount(ForwarderStateVolume, "/var/lib/fluent-bit", false),
		map[string]any{"name": ForwarderConfigVolume, "mountPath": "/fluent-bit/etc/fluent-bit.conf", "subPath": "fluent-bit.conf", "readOnly": true},
		mount(ForwarderTLSVolume, "/run/secrets/exporter", true),
	)
	environment := []any{
		fieldEnv("OPENSHELL_SANDBOX_ID", "metadata.annotations['"+AnnotationSandboxID+"']"),
		fieldEnv("OPENSHELL_SOURCE_INSTANCE", "metadata.name"),
		valueEnv("OPENSHELL_EXPORTER_HOST", injector.config.ExporterHost),
		valueEnv("OPENSHELL_GATEWAY_ID", injector.config.GatewayID),
		valueEnv("OPENSHELL_WORKSPACE", injector.config.Workspace),
		map[string]any{"name": "FORWARDED_OCSF_TOKEN", "valueFrom": map[string]any{
			"secretKeyRef": map[string]any{"name": injector.config.AuthSecretName, "key": "token"},
		}},
	}
	return map[string]any{
		"name": ForwarderContainer, "image": injector.config.FluentBitImage,
		"imagePullPolicy": "IfNotPresent", "args": []any{"-c", "/fluent-bit/etc/fluent-bit.conf"},
		"ports": []any{map[string]any{"name": "fwd-health", "containerPort": int64(2020)}},
		"readinessProbe": map[string]any{"httpGet": map[string]any{
			"path": "/api/v1/health", "port": "fwd-health",
		}},
		"livenessProbe": map[string]any{
			"httpGet":       map[string]any{"path": "/api/v1/health", "port": "fwd-health"},
			"periodSeconds": int64(30),
		},
		"env": environment,
		"securityContext": map[string]any{
			"allowPrivilegeEscalation": false, "readOnlyRootFilesystem": true,
			"runAsNonRoot": true, "runAsUser": int64(65532), "runAsGroup": int64(65532),
			"capabilities":   map[string]any{"drop": []any{"ALL"}},
			"seccompProfile": map[string]any{"type": "RuntimeDefault"},
		},
		"resources": map[string]any{
			"requests": map[string]any{"cpu": injector.config.CPURequest, "memory": injector.config.MemoryRequest},
			"limits":   map[string]any{"cpu": injector.config.CPULimit, "memory": injector.config.MemoryLimit},
		},
		"volumeMounts": mounts,
	}
}

func ensurePodFSGroup(podSpec map[string]any) error {
	context := map[string]any{}
	if raw, exists := podSpec["securityContext"]; exists {
		var valid bool
		context, valid = raw.(map[string]any)
		if !valid {
			return errors.New("sandbox pod securityContext must be an object")
		}
	} else {
		podSpec["securityContext"] = context
	}
	if raw, exists := context["fsGroup"]; exists {
		group, valid := integral(raw)
		if !valid || group <= 0 {
			return errors.New("sandbox pod securityContext.fsGroup must be a positive integer")
		}
		return nil
	}
	context["fsGroup"] = int64(65532)
	if _, exists := context["fsGroupChangePolicy"]; !exists {
		context["fsGroupChangePolicy"] = "OnRootMismatch"
	}
	return nil
}

func addMount(container map[string]any, name, path string, readOnly bool) error {
	mounts, _ := container["volumeMounts"].([]any)
	for _, item := range mounts {
		existing, ok := item.(map[string]any)
		if ok && (existing["name"] == name || existing["mountPath"] == path) {
			return fmt.Errorf("container %q already uses reserved mount %q", container["name"], path)
		}
	}
	container["volumeMounts"] = append(mounts, mount(name, path, readOnly))
	return nil
}

func mount(name, path string, readOnly bool) map[string]any {
	return map[string]any{"name": name, "mountPath": path, "readOnly": readOnly}
}

func fieldEnv(name, path string) map[string]any {
	return map[string]any{"name": name, "valueFrom": map[string]any{"fieldRef": map[string]any{"fieldPath": path}}}
}

func valueEnv(name, value string) map[string]any {
	return map[string]any{"name": name, "value": value}
}

func objectIndex(objects []any, name string) int {
	for index, item := range objects {
		object, ok := item.(map[string]any)
		if ok && object["name"] == name {
			return index
		}
	}
	return -1
}

func claimIndex(objects []any, name string) int {
	for index, item := range objects {
		object, ok := item.(map[string]any)
		if !ok {
			continue
		}
		metadata, _ := object["metadata"].(map[string]any)
		if metadata["name"] == name {
			return index
		}
	}
	return -1
}

func annotation(template map[string]any, key string) string {
	metadata, _ := template["metadata"].(map[string]any)
	annotations, _ := metadata["annotations"].(map[string]any)
	value, _ := annotations[key].(string)
	return value
}

func mapValue(parent map[string]any, key string) map[string]any {
	if value, ok := parent[key].(map[string]any); ok {
		return value
	}
	value := map[string]any{}
	parent[key] = value
	return value
}

func integral(value any) (int64, bool) {
	number, ok := value.(float64)
	if !ok || number != float64(int64(number)) {
		return 0, false
	}
	return int64(number), true
}
