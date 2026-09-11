package sandboxinjector

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

const fluentBitImage = "docker.io/fluent/fluent-bit:4.2.0@sha256:aff743e1a5e61d036d10a4472d67cdd7a6b408c92218eb86e3f301e2bac7817a"

func testConfig() Config {
	return Config{
		AllowedNamespaces: []string{"openshell-sandboxes"},
		AllowedUsernames:  []string{"system:serviceaccount:openshell:gateway"},
		FluentBitImage:    fluentBitImage, ExporterHost: "openshell-exporter.observability.svc.cluster.local",
		GatewayID: "gateway-1", Workspace: "default",
		AgentConfigMapName: "forwarder-agent", NetworkConfigMapName: "forwarder-network",
		TLSSecretName: "forwarder-tls", AuthSecretName: "forwarder-auth",
		EvidenceSize: "2Gi", StateSize: "1Gi", CPURequest: "25m", MemoryRequest: "64Mi",
		CPULimit: "250m", MemoryLimit: "256Mi",
	}
}

func sandboxObject(sidecarTopology bool) json.RawMessage {
	containers := []any{map[string]any{"name": "agent", "volumeMounts": []any{}}}
	if sidecarTopology {
		containers = append(containers, map[string]any{"name": NetworkContainer, "volumeMounts": []any{}})
	}
	object := map[string]any{
		"apiVersion": "agents.x-k8s.io/v1beta1", "kind": "Sandbox",
		"spec": map[string]any{
			"volumeClaimTemplates": []any{map[string]any{
				"metadata": map[string]any{"name": "workspace"},
				"spec":     map[string]any{"accessModes": []any{"ReadWriteOnce"}},
			}},
			"podTemplate": map[string]any{
				"metadata": map[string]any{"annotations": map[string]any{AnnotationSandboxID: "sandbox-1"}},
				"spec": map[string]any{
					"securityContext": map[string]any{"fsGroup": 1000},
					"containers":      containers,
					"volumes":         []any{},
				},
			},
		},
	}
	result, _ := json.Marshal(object)
	return result
}

func TestPatchCombinedTopology(t *testing.T) {
	injector, err := New(testConfig())
	if err != nil {
		t.Fatal(err)
	}
	patch, err := injector.Patch(sandboxObject(false))
	if err != nil {
		t.Fatal(err)
	}
	var operations []map[string]any
	if err := json.Unmarshal(patch, &operations); err != nil {
		t.Fatal(err)
	}
	if len(operations) != 2 || operations[0]["path"] != "/spec/podTemplate" {
		t.Fatalf("unexpected patch: %s", patch)
	}
	template := operations[0]["value"].(map[string]any)
	spec := template["spec"].(map[string]any)
	containers := spec["containers"].([]any)
	if objectIndex(containers, ForwarderContainer) < 0 {
		t.Fatal("forwarder sidecar was not injected")
	}
	agent := containers[objectIndex(containers, AgentContainer)].(map[string]any)
	if !hasMount(agent, AgentEvidenceVolume, "/var/log", false) {
		t.Fatal("agent evidence mount is missing")
	}
	claims := operations[1]["value"].([]any)
	for _, name := range []string{AgentEvidenceVolume, ForwarderStateVolume} {
		if claimIndex(claims, name) < 0 {
			t.Errorf("claim %s is missing", name)
		}
	}
	if len(containers) != 2 {
		t.Fatalf("expected agent plus one file-evidence sidecar, got %d containers", len(containers))
	}
	if claimIndex(claims, NetworkEvidenceVolume) >= 0 {
		t.Fatal("combined topology received an unnecessary network evidence claim")
	}
	assertForwarderProfile(t, template, "forwarder-agent", "agent-only", "unavailable")
	assertRWOP(t, claims)
}

func TestPatchNetworkSidecarTopology(t *testing.T) {
	injector, _ := New(testConfig())
	patch, err := injector.Patch(sandboxObject(true))
	if err != nil {
		t.Fatal(err)
	}
	var operations []map[string]any
	_ = json.Unmarshal(patch, &operations)
	template := operations[0]["value"].(map[string]any)
	containers := template["spec"].(map[string]any)["containers"].([]any)
	network := containers[objectIndex(containers, NetworkContainer)].(map[string]any)
	if !hasMount(network, NetworkEvidenceVolume, "/var/log", false) {
		t.Fatal("network-supervisor evidence mount is missing")
	}
	forwarder := containers[objectIndex(containers, ForwarderContainer)].(map[string]any)
	if !hasMount(forwarder, NetworkEvidenceVolume, "/evidence/network", true) {
		t.Fatal("forwarder does not read the network-supervisor evidence volume")
	}
	assertForwarderProfile(t, template, "forwarder-network", "agent-and-network", "enabled")
	assertRWOP(t, operations[1]["value"].([]any))
}

func TestPatchAddsVolumeOwnershipAndHealthWhenFSGroupIsMissing(t *testing.T) {
	var object map[string]any
	_ = json.Unmarshal(sandboxObject(false), &object)
	podSpec := object["spec"].(map[string]any)["podTemplate"].(map[string]any)["spec"].(map[string]any)
	delete(podSpec, "securityContext")
	raw, _ := json.Marshal(object)
	injector, _ := New(testConfig())
	patch, err := injector.Patch(raw)
	if err != nil {
		t.Fatal(err)
	}
	var operations []map[string]any
	_ = json.Unmarshal(patch, &operations)
	injectedSpec := operations[0]["value"].(map[string]any)["spec"].(map[string]any)
	security := injectedSpec["securityContext"].(map[string]any)
	if security["fsGroup"] != float64(65532) || security["fsGroupChangePolicy"] != "OnRootMismatch" {
		t.Fatalf("pod security context=%#v", security)
	}
	containers := injectedSpec["containers"].([]any)
	forwarder := containers[objectIndex(containers, ForwarderContainer)].(map[string]any)
	if _, ok := forwarder["readinessProbe"].(map[string]any); !ok {
		t.Fatal("forwarder readiness probe is missing")
	}
	if _, ok := forwarder["livenessProbe"].(map[string]any); !ok {
		t.Fatal("forwarder liveness probe is missing")
	}
}

func TestPatchRejectsSpoofedForwarderAndMissingSandboxIdentity(t *testing.T) {
	injector, _ := New(testConfig())
	var object map[string]any
	_ = json.Unmarshal(sandboxObject(false), &object)
	template := object["spec"].(map[string]any)["podTemplate"].(map[string]any)
	template["metadata"].(map[string]any)["annotations"].(map[string]any)[AnnotationInjected] = "v1"
	podSpec := template["spec"].(map[string]any)
	podSpec["containers"] = append(podSpec["containers"].([]any), map[string]any{"name": ForwarderContainer})
	raw, _ := json.Marshal(object)
	if _, err := injector.Patch(raw); err == nil {
		t.Fatal("spoofed injected annotation and reserved container were accepted")
	}

	_ = json.Unmarshal(sandboxObject(false), &object)
	template = object["spec"].(map[string]any)["podTemplate"].(map[string]any)
	delete(template["metadata"].(map[string]any)["annotations"].(map[string]any), AnnotationSandboxID)
	raw, _ = json.Marshal(object)
	if _, err := injector.Patch(raw); err == nil {
		t.Fatal("missing authoritative sandbox identity was accepted")
	}
}

func TestAdmissionRejectsUntrustedCreator(t *testing.T) {
	injector, _ := New(testConfig())
	review := AdmissionReview{APIVersion: "admission.k8s.io/v1", Kind: "AdmissionReview", Request: &AdmissionRequest{
		UID: "request-1", Kind: GroupVersionKind{Group: "agents.x-k8s.io", Version: "v1beta1", Kind: "Sandbox"},
		Namespace: "openshell-sandboxes", Operation: "CREATE",
		UserInfo: UserInfo{Username: "system:serviceaccount:untrusted:creator"}, Object: sandboxObject(false),
	}}
	body, _ := json.Marshal(review)
	request := httptest.NewRequest(http.MethodPost, "/mutate", bytes.NewReader(body))
	response := httptest.NewRecorder()
	injector.ServeHTTP(response, request)
	var result AdmissionReview
	_ = json.Unmarshal(response.Body.Bytes(), &result)
	if result.Response == nil || result.Response.Allowed || result.Response.Status.Code != http.StatusForbidden {
		t.Fatalf("untrusted admission was not rejected: %s", response.Body.String())
	}
}

func TestPatchRejectsExistingVarLogMount(t *testing.T) {
	var object map[string]any
	_ = json.Unmarshal(sandboxObject(false), &object)
	agent := object["spec"].(map[string]any)["podTemplate"].(map[string]any)["spec"].(map[string]any)["containers"].([]any)[0].(map[string]any)
	agent["volumeMounts"] = []any{map[string]any{"name": "customer-log", "mountPath": "/var/log"}}
	raw, _ := json.Marshal(object)
	injector, _ := New(testConfig())
	if _, err := injector.Patch(raw); err == nil {
		t.Fatal("existing /var/log mount was accepted")
	}
}

func assertRWOP(t *testing.T, claims []any) {
	t.Helper()
	for _, item := range claims {
		claim := item.(map[string]any)
		metadata := claim["metadata"].(map[string]any)
		name := metadata["name"].(string)
		if name == "workspace" {
			continue
		}
		modes := claim["spec"].(map[string]any)["accessModes"].([]any)
		if len(modes) != 1 || modes[0] != "ReadWriteOncePod" {
			t.Errorf("claim %s is not RWOP", name)
		}
	}
}

func assertForwarderProfile(t *testing.T, template map[string]any, configMap, profile, networkLane string) {
	t.Helper()
	spec := template["spec"].(map[string]any)
	volumes := spec["volumes"].([]any)
	index := objectIndex(volumes, ForwarderConfigVolume)
	if index < 0 {
		t.Fatal("forwarder ConfigMap volume is missing")
	}
	gotConfig := volumes[index].(map[string]any)["configMap"].(map[string]any)["name"]
	if gotConfig != configMap {
		t.Fatalf("forwarder ConfigMap=%v, want %s", gotConfig, configMap)
	}
	annotations := template["metadata"].(map[string]any)["annotations"].(map[string]any)
	if annotations[AnnotationFileProfile] != profile || annotations[AnnotationNetworkLane] != networkLane {
		t.Fatalf("topology annotations=%v, want profile=%s network=%s", annotations, profile, networkLane)
	}
}

func hasMount(container map[string]any, name, path string, readOnly bool) bool {
	for _, item := range container["volumeMounts"].([]any) {
		mount := item.(map[string]any)
		if mount["name"] == name && mount["mountPath"] == path && mount["readOnly"] == readOnly {
			return true
		}
	}
	return false
}
