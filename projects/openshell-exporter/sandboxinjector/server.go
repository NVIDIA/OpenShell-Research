package sandboxinjector

import (
	"encoding/json"
	"net/http"
)

const maximumAdmissionBodyBytes = 4 << 20

// AdmissionReview is the minimal admission.k8s.io/v1 shape used by the
// injector. Keeping this local avoids granting the service a Kubernetes client.
type AdmissionReview struct {
	APIVersion string             `json:"apiVersion"`
	Kind       string             `json:"kind"`
	Request    *AdmissionRequest  `json:"request,omitempty"`
	Response   *AdmissionResponse `json:"response,omitempty"`
}

type AdmissionRequest struct {
	UID       string           `json:"uid"`
	Kind      GroupVersionKind `json:"kind"`
	Namespace string           `json:"namespace"`
	Operation string           `json:"operation"`
	UserInfo  UserInfo         `json:"userInfo"`
	Object    json.RawMessage  `json:"object"`
}

type GroupVersionKind struct {
	Group   string `json:"group"`
	Version string `json:"version"`
	Kind    string `json:"kind"`
}

type UserInfo struct {
	Username string `json:"username"`
}

type AdmissionResponse struct {
	UID       string  `json:"uid"`
	Allowed   bool    `json:"allowed"`
	Patch     []byte  `json:"patch,omitempty"`
	PatchType *string `json:"patchType,omitempty"`
	Status    *Status `json:"status,omitempty"`
}

type Status struct {
	Code    int32  `json:"code,omitempty"`
	Message string `json:"message,omitempty"`
}

// ServeHTTP implements the fail-closed mutation endpoint.
func (injector *Injector) ServeHTTP(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		http.Error(writer, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	request.Body = http.MaxBytesReader(writer, request.Body, maximumAdmissionBodyBytes)
	var review AdmissionReview
	if err := json.NewDecoder(request.Body).Decode(&review); err != nil {
		http.Error(writer, "invalid admission review", http.StatusBadRequest)
		return
	}
	response := injector.review(review.Request)
	writer.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(writer).Encode(AdmissionReview{
		APIVersion: "admission.k8s.io/v1",
		Kind:       "AdmissionReview",
		Response:   response,
	})
}

func (injector *Injector) review(request *AdmissionRequest) *AdmissionResponse {
	if request == nil {
		return deny("", http.StatusBadRequest, "admission request is required")
	}
	if request.Operation != "CREATE" {
		return deny(request.UID, http.StatusBadRequest, "only CREATE operations are supported")
	}
	if request.Kind.Group != "agents.x-k8s.io" || request.Kind.Kind != "Sandbox" {
		return deny(request.UID, http.StatusBadRequest, "request is not an Agent Sandbox resource")
	}
	if !injector.Authorized(request.Namespace, request.UserInfo.Username) {
		return deny(request.UID, http.StatusForbidden, "namespace or OpenShell gateway identity is not allow-listed")
	}
	patch, err := injector.Patch(request.Object)
	if err != nil {
		return deny(request.UID, http.StatusUnprocessableEntity, err.Error())
	}
	if len(patch) == 0 {
		return &AdmissionResponse{UID: request.UID, Allowed: true}
	}
	patchType := "JSONPatch"
	return &AdmissionResponse{UID: request.UID, Allowed: true, Patch: patch, PatchType: &patchType}
}

func deny(uid string, code int, message string) *AdmissionResponse {
	return &AdmissionResponse{
		UID: uid, Allowed: false,
		Status: &Status{Code: int32(code), Message: message},
	}
}
