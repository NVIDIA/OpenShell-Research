// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"compress/gzip"
	"crypto/subtle"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"mime"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"go.opentelemetry.io/collector/pdata/ptrace"
)

const (
	maxCloudEventsBody = 4 << 20
	maxOTLPBody        = 16 << 20
	defaultCapacity    = 2000
)

//go:embed index.html
var dashboardHTML string

type eventItem struct {
	Sequence     uint64            `json:"sequence"`
	ReceivedAt   time.Time         `json:"received_at"`
	Kind         string            `json:"kind"`
	Type         string            `json:"type"`
	Source       string            `json:"source"`
	Subject      string            `json:"subject,omitempty"`
	ID           string            `json:"id"`
	Summary      string            `json:"summary"`
	Stage        string            `json:"stage"`
	OccurredAt   string            `json:"occurred_at,omitempty"`
	Correlation  map[string]string `json:"correlation,omitempty"`
	Valid        *bool             `json:"valid,omitempty"`
	DurationMS   float64           `json:"duration_ms,omitempty"`
	InputTokens  int64             `json:"input_tokens,omitempty"`
	OutputTokens int64             `json:"output_tokens,omitempty"`
	TotalTokens  int64             `json:"total_tokens,omitempty"`
	TokenUsage   string            `json:"token_usage_status,omitempty"`
	Outcome      string            `json:"outcome,omitempty"`
	Payload      map[string]any    `json:"payload"`
}

type eventStore struct {
	mu       sync.RWMutex
	capacity int
	next     uint64
	items    []eventItem
	watchers map[chan eventItem]struct{}
}

func newEventStore(capacity int) *eventStore {
	return &eventStore{
		capacity: capacity,
		items:    make([]eventItem, 0, capacity),
		watchers: make(map[chan eventItem]struct{}),
	}
}

func (s *eventStore) add(items []eventItem) {
	s.mu.Lock()
	for index := range items {
		s.next++
		items[index].Sequence = s.next
		s.items = append(s.items, items[index])
	}
	if overflow := len(s.items) - s.capacity; overflow > 0 {
		copy(s.items, s.items[overflow:])
		s.items = s.items[:s.capacity]
	}
	watchers := make([]chan eventItem, 0, len(s.watchers))
	for watcher := range s.watchers {
		watchers = append(watchers, watcher)
	}
	s.mu.Unlock()

	for _, item := range items {
		for _, watcher := range watchers {
			select {
			case watcher <- item:
			default:
			}
		}
	}
}

func (s *eventStore) snapshot(limit int) []eventItem {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if limit <= 0 || limit > len(s.items) {
		limit = len(s.items)
	}
	result := make([]eventItem, limit)
	for index := 0; index < limit; index++ {
		result[index] = s.items[len(s.items)-1-index]
	}
	return result
}

func (s *eventStore) subscribe() (chan eventItem, func()) {
	watcher := make(chan eventItem, 32)
	s.mu.Lock()
	s.watchers[watcher] = struct{}{}
	s.mu.Unlock()
	return watcher, func() {
		s.mu.Lock()
		delete(s.watchers, watcher)
		s.mu.Unlock()
	}
}

type dashboardServer struct {
	token string
	store *eventStore
}

func newDashboardServer(token string, capacity int) (*dashboardServer, error) {
	if len(token) < 32 {
		return nil, errors.New("WEBAPP_INGEST_TOKEN must contain at least 32 bytes")
	}
	if capacity <= 0 {
		return nil, errors.New("event capacity must be positive")
	}
	return &dashboardServer{token: token, store: newEventStore(capacity)}, nil
}

func (s *dashboardServer) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /", s.dashboard)
	mux.HandleFunc("GET /healthz", s.health)
	mux.HandleFunc("GET /api/events", s.events)
	mux.HandleFunc("GET /api/stats", s.stats)
	mux.HandleFunc("GET /api/stream", s.stream)
	mux.HandleFunc("POST /v1/events", s.cloudEvents)
	mux.HandleFunc("POST /v1/traces", s.traces)
	return securityHeaders(mux)
}

func securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		response.Header().Set("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'")
		response.Header().Set("X-Content-Type-Options", "nosniff")
		response.Header().Set("Referrer-Policy", "no-referrer")
		next.ServeHTTP(response, request)
	})
}

func (s *dashboardServer) authorized(request *http.Request) bool {
	header := request.Header.Get("Authorization")
	if !strings.HasPrefix(header, "Bearer ") {
		return false
	}
	provided := strings.TrimPrefix(header, "Bearer ")
	if len(provided) != len(s.token) {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(provided), []byte(s.token)) == 1
}

func (s *dashboardServer) requireAuth(response http.ResponseWriter, request *http.Request) bool {
	if s.authorized(request) {
		return true
	}
	response.Header().Set("WWW-Authenticate", "Bearer")
	http.Error(response, "unauthorized", http.StatusUnauthorized)
	return false
}

func (s *dashboardServer) dashboard(response http.ResponseWriter, _ *http.Request) {
	response.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = io.WriteString(response, dashboardHTML)
}

func (s *dashboardServer) health(response http.ResponseWriter, _ *http.Request) {
	response.Header().Set("Content-Type", "application/json")
	_, _ = io.WriteString(response, `{"status":"ok"}`)
}

func (s *dashboardServer) events(response http.ResponseWriter, request *http.Request) {
	limit := 200
	if raw := request.URL.Query().Get("limit"); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil || parsed < 1 || parsed > defaultCapacity {
			http.Error(response, "limit must be between 1 and 2000", http.StatusBadRequest)
			return
		}
		limit = parsed
	}
	writeJSON(response, http.StatusOK, s.store.snapshot(limit))
}

func (s *dashboardServer) stats(response http.ResponseWriter, _ *http.Request) {
	items := s.store.snapshot(defaultCapacity)
	byKind := make(map[string]int)
	byType := make(map[string]int)
	invalid := 0
	for _, item := range items {
		byKind[item.Kind]++
		byType[item.Type]++
		if item.Valid != nil && !*item.Valid {
			invalid++
		}
	}
	writeJSON(response, http.StatusOK, map[string]any{
		"total":   len(items),
		"invalid": invalid,
		"by_kind": byKind,
		"by_type": byType,
	})
}

func (s *dashboardServer) stream(response http.ResponseWriter, request *http.Request) {
	flusher, ok := response.(http.Flusher)
	if !ok {
		http.Error(response, "streaming unsupported", http.StatusInternalServerError)
		return
	}
	response.Header().Set("Content-Type", "text/event-stream")
	response.Header().Set("Cache-Control", "no-cache")
	response.Header().Set("Connection", "keep-alive")
	watcher, unsubscribe := s.store.subscribe()
	defer unsubscribe()
	ticker := time.NewTicker(15 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-request.Context().Done():
			return
		case item := <-watcher:
			encoded, err := json.Marshal(item)
			if err != nil {
				continue
			}
			_, _ = fmt.Fprintf(response, "data: %s\n\n", encoded)
			flusher.Flush()
		case <-ticker.C:
			_, _ = io.WriteString(response, ": keepalive\n\n")
			flusher.Flush()
		}
	}
}

func (s *dashboardServer) cloudEvents(response http.ResponseWriter, request *http.Request) {
	if !s.requireAuth(response, request) {
		return
	}
	mediaType, _, err := mime.ParseMediaType(request.Header.Get("Content-Type"))
	if err != nil || mediaType != "application/cloudevents-batch+json" {
		http.Error(response, "content type must be application/cloudevents-batch+json", http.StatusUnsupportedMediaType)
		return
	}
	body, err := readBounded(response, request, maxCloudEventsBody)
	if err != nil {
		return
	}
	var rawEvents []json.RawMessage
	if err := json.Unmarshal(body, &rawEvents); err != nil {
		http.Error(response, "invalid CloudEvents batch", http.StatusBadRequest)
		return
	}
	if len(rawEvents) == 0 || len(rawEvents) > 500 {
		http.Error(response, "batch must contain between 1 and 500 events", http.StatusBadRequest)
		return
	}
	items := make([]eventItem, 0, len(rawEvents))
	for _, raw := range rawEvents {
		if len(raw) > 1<<20 {
			http.Error(response, "CloudEvent exceeds 1 MiB", http.StatusRequestEntityTooLarge)
			return
		}
		item, err := cloudEventItem(raw)
		if err != nil {
			http.Error(response, err.Error(), http.StatusBadRequest)
			return
		}
		items = append(items, item)
	}
	s.store.add(items)
	response.WriteHeader(http.StatusNoContent)
}

func cloudEventItem(raw json.RawMessage) (eventItem, error) {
	var event map[string]any
	if err := json.Unmarshal(raw, &event); err != nil {
		return eventItem{}, errors.New("CloudEvent must be a JSON object")
	}
	for _, key := range []string{"specversion", "id", "source", "type"} {
		if value, ok := event[key].(string); !ok || strings.TrimSpace(value) == "" {
			return eventItem{}, fmt.Errorf("CloudEvent %s must be a non-empty string", key)
		}
	}
	if event["specversion"] != "1.0" {
		return eventItem{}, errors.New("CloudEvent specversion must be 1.0")
	}
	data, ok := event["data"].(map[string]any)
	if !ok {
		return eventItem{}, errors.New("CloudEvent data must be an object")
	}
	item := eventItem{
		ReceivedAt: time.Now().UTC(),
		Kind:       "openshell_event",
		Type:       event["type"].(string),
		Source:     event["source"].(string),
		ID:         event["id"].(string),
		Payload:    event,
	}
	item.Subject, _ = event["subject"].(string)
	item.Valid = nestedBool(data, "security", "validation", "valid")
	if item.Valid == nil {
		switch nestedString(data, "security", "validation", "status") {
		case "valid":
			valid := true
			item.Valid = &valid
		case "invalid":
			valid := false
			item.Valid = &valid
		}
	}
	if item.Valid == nil {
		if valid, ok := event["openshellocsfvalid"].(bool); ok {
			item.Valid = &valid
		}
	}
	item.Outcome = cloudEventOutcome(data, item.Type, item.Valid)
	acquisition := nestedString(data, "acquisition", "kind")
	if acquisition == "" {
		acquisition = strings.TrimSuffix(strings.TrimPrefix(item.Type, "com.nvidia.openshell."), ".v1")
	}
	item.Summary = acquisition
	if item.Subject != "" {
		item.Summary += " · " + item.Subject
	}
	item.Stage = cloudEventStage(item.Type, acquisition)
	item.Correlation = cloudEventCorrelation(data)
	if occurred, ok := event["time"].(string); ok {
		item.OccurredAt = occurred
	}
	return item, nil
}

func (s *dashboardServer) traces(response http.ResponseWriter, request *http.Request) {
	if !s.requireAuth(response, request) {
		return
	}
	mediaType, _, err := mime.ParseMediaType(request.Header.Get("Content-Type"))
	if err != nil || (mediaType != "application/x-protobuf" && mediaType != "application/protobuf") {
		http.Error(response, "OTLP traces require protobuf content", http.StatusUnsupportedMediaType)
		return
	}
	body, err := readBounded(response, request, maxOTLPBody)
	if err != nil {
		return
	}
	var unmarshaler ptrace.ProtoUnmarshaler
	traces, err := unmarshaler.UnmarshalTraces(body)
	if err != nil {
		http.Error(response, "invalid OTLP trace request", http.StatusBadRequest)
		return
	}
	items := traceItems(traces)
	if len(items) > 0 {
		s.store.add(items)
	}
	response.Header().Set("Content-Type", "application/x-protobuf")
	response.WriteHeader(http.StatusOK)
}

func traceItems(traces ptrace.Traces) []eventItem {
	items := make([]eventItem, 0, traces.SpanCount())
	for resourceIndex := 0; resourceIndex < traces.ResourceSpans().Len(); resourceIndex++ {
		resourceSpans := traces.ResourceSpans().At(resourceIndex)
		resource := resourceSpans.Resource().Attributes().AsRaw()
		service, _ := resource["service.name"].(string)
		for scopeIndex := 0; scopeIndex < resourceSpans.ScopeSpans().Len(); scopeIndex++ {
			scopeSpans := resourceSpans.ScopeSpans().At(scopeIndex)
			spans := scopeSpans.Spans()
			for spanIndex := 0; spanIndex < spans.Len(); spanIndex++ {
				span := spans.At(spanIndex)
				attributes := span.Attributes().AsRaw()
				spanType := attributeString(attributes, "openinference.span.kind")
				if spanType == "" {
					spanType = attributeString(attributes, "gen_ai.operation.name")
				}
				if spanType == "" {
					spanType = "span"
				}
				traceID := span.TraceID().String()
				spanID := span.SpanID().String()
				inputTokens, outputTokens, totalTokens, tokenUsage := traceTokens(attributes)
				items = append(items, eventItem{
					ReceivedAt:   time.Now().UTC(),
					Kind:         "nemo_relay_trace",
					Type:         spanType,
					Source:       service,
					ID:           traceID + ":" + spanID,
					Summary:      span.Name(),
					Stage:        traceStage(spanType, span.Name()),
					OccurredAt:   span.StartTimestamp().AsTime().UTC().Format(time.RFC3339Nano),
					DurationMS:   traceDurationMilliseconds(span),
					InputTokens:  inputTokens,
					OutputTokens: outputTokens,
					TotalTokens:  totalTokens,
					TokenUsage:   tokenUsage,
					Outcome:      traceOutcome(span, attributes),
					Correlation:  traceCorrelation(resource, attributes, traceID),
					Payload: map[string]any{
						"trace_id":            traceID,
						"span_id":             spanID,
						"parent_span_id":      span.ParentSpanID().String(),
						"name":                span.Name(),
						"start_time":          span.StartTimestamp().AsTime().UTC(),
						"end_time":            span.EndTimestamp().AsTime().UTC(),
						"status":              span.Status().Code().String(),
						"attributes":          attributes,
						"resource_attributes": resource,
						"instrumentation_scope": map[string]any{
							"name":    scopeSpans.Scope().Name(),
							"version": scopeSpans.Scope().Version(),
						},
					},
				})
			}
		}
	}
	return items
}

func cloudEventOutcome(data map[string]any, eventType string, valid *bool) string {
	if valid != nil && !*valid {
		return "invalid"
	}
	if strings.Contains(eventType, ".stream.warning.") {
		return "warning"
	}
	if containsDenied(data["original"]) {
		return "denied"
	}
	return ""
}

func containsDenied(value any) bool {
	switch typed := value.(type) {
	case string:
		return strings.Contains(strings.ToLower(typed), "connect denied ")
	case []any:
		for _, child := range typed {
			if containsDenied(child) {
				return true
			}
		}
	case map[string]any:
		for _, child := range typed {
			if containsDenied(child) {
				return true
			}
		}
	}
	return false
}

func traceDurationMilliseconds(span ptrace.Span) float64 {
	started := span.StartTimestamp().AsTime()
	finished := span.EndTimestamp().AsTime()
	if !finished.After(started) {
		return 0
	}
	return float64(finished.Sub(started)) / float64(time.Millisecond)
}

func traceTokens(attributes map[string]any) (int64, int64, int64, string) {
	input, inputObserved := firstNumericAttribute(attributes,
		"gen_ai.usage.input_tokens",
		"llm.token_count.prompt",
		"llm.token_count.input",
		"nemo_relay.llm.token_count.input",
	)
	output, outputObserved := firstNumericAttribute(attributes,
		"gen_ai.usage.output_tokens",
		"llm.token_count.completion",
		"llm.token_count.output",
		"nemo_relay.llm.token_count.output",
	)
	total, totalObserved := firstNumericAttribute(attributes,
		"gen_ai.usage.total_tokens",
		"llm.token_count.total",
		"nemo_relay.llm.token_count.total",
	)
	if total == 0 && input+output > 0 {
		total = input + output
	}
	status := "not_emitted"
	if inputObserved || outputObserved || totalObserved {
		status = "observed"
	}
	return input, output, total, status
}

func firstNumericAttribute(attributes map[string]any, keys ...string) (int64, bool) {
	for _, key := range keys {
		value, exists := attributes[key]
		if !exists {
			continue
		}
		switch number := value.(type) {
		case int64:
			return number, true
		case int:
			return int64(number), true
		case float64:
			return int64(number), true
		case string:
			parsed, err := strconv.ParseFloat(number, 64)
			if err == nil {
				return int64(parsed), true
			}
		}
	}
	return 0, false
}

func traceOutcome(span ptrace.Span, attributes map[string]any) string {
	if span.Status().Code() == ptrace.StatusCodeError {
		return "error"
	}
	for _, key := range []string{"error.type", "exception.type"} {
		if value, exists := attributes[key]; exists && fmt.Sprint(value) != "" {
			return "error"
		}
	}
	return ""
}

func cloudEventStage(eventType, acquisition string) string {
	value := strings.ToLower(eventType + " " + acquisition)
	for _, candidate := range []struct {
		contains string
		stage    string
	}{
		{".ocsf.4001.", "network"},
		{".ocsf.1007.", "process"},
		{".ocsf.1001.", "file"},
		{"policy.", "policy"},
		{"lifecycle", "sandbox"},
		{"platform.event", "platform"},
		{"stream.warning", "gap"},
		{".log", "log"},
		{"ocsf", "security"},
	} {
		if strings.Contains(value, candidate.contains) {
			return candidate.stage
		}
	}
	return "evidence"
}

func traceStage(spanType, name string) string {
	value := strings.ToLower(spanType + " " + name)
	switch {
	case strings.Contains(value, "tool"):
		return "tool"
	case strings.Contains(value, "llm"), strings.Contains(value, "model"), strings.Contains(value, "chat"):
		return "model"
	case strings.Contains(value, "session"), strings.Contains(value, "agent"):
		return "prompt"
	default:
		return "agent"
	}
}

func cloudEventCorrelation(data map[string]any) map[string]string {
	result := make(map[string]string)
	if openshell, ok := data["openshell"].(map[string]any); ok {
		copyCorrelation(result, openshell, map[string]string{
			"gateway_id":     "openshell.gateway.id",
			"workspace":      "openshell.workspace",
			"sandbox_id":     "openshell.sandbox.id",
			"policy_version": "openshell.policy.version",
		})
	}
	if correlation, ok := data["correlation"].(map[string]any); ok {
		copyCorrelation(result, correlation, map[string]string{
			"agent.session.id":         "agent.session.id",
			"openshell.gateway.id":     "openshell.gateway.id",
			"openshell.policy.version": "openshell.policy.version",
			"openshell.sandbox.id":     "openshell.sandbox.id",
			"openshell.workspace":      "openshell.workspace",
			"request_id":               "request_id",
			"tool_call_id":             "tool_call_id",
			"trace_id":                 "trace_id",
		})
		copyCorrelation(result, correlation, map[string]string{
			"agent_session_id":   "agent.session.id",
			"original_event_uid": "original_event_uid",
			"request_id":         "request_id",
			"session_id":         "agent.session.id",
			"tool_call_id":       "tool_call_id",
			"trace_id":           "trace_id",
		})
	}
	return result
}

func traceCorrelation(resource, attributes map[string]any, traceID string) map[string]string {
	result := map[string]string{"trace_id": traceID}
	for _, values := range []map[string]any{resource, attributes} {
		copyCorrelation(result, values, map[string]string{
			"agent.session.id":         "agent.session.id",
			"gen_ai.tool.call.id":      "tool_call_id",
			"nemo_relay.tool_call_id":  "tool_call_id",
			"openshell.gateway.id":     "openshell.gateway.id",
			"openshell.policy.version": "openshell.policy.version",
			"openshell.sandbox.id":     "openshell.sandbox.id",
			"openshell.workspace":      "openshell.workspace",
			"request.id":               "request_id",
			"request_id":               "request_id",
			"session.id":               "agent.session.id",
			"tool_call_id":             "tool_call_id",
		})
	}
	return result
}

func copyCorrelation(result map[string]string, values map[string]any, keys map[string]string) {
	for source, target := range keys {
		if _, exists := result[target]; exists {
			continue
		}
		value, exists := values[source]
		if !exists || value == nil || fmt.Sprint(value) == "" {
			continue
		}
		result[target] = fmt.Sprint(value)
	}
}

func readBounded(response http.ResponseWriter, request *http.Request, limit int64) ([]byte, error) {
	request.Body = http.MaxBytesReader(response, request.Body, limit)
	reader := io.Reader(request.Body)
	if encoding := strings.ToLower(strings.TrimSpace(request.Header.Get("Content-Encoding"))); encoding != "" && encoding != "identity" {
		if encoding != "gzip" {
			err := fmt.Errorf("unsupported content encoding: %s", encoding)
			http.Error(response, err.Error(), http.StatusUnsupportedMediaType)
			return nil, err
		}
		gzipReader, err := gzip.NewReader(request.Body)
		if err != nil {
			http.Error(response, "invalid gzip request body", http.StatusBadRequest)
			return nil, fmt.Errorf("initialize gzip reader: %w", err)
		}
		defer func() {
			_ = gzipReader.Close()
		}()
		reader = gzipReader
	}
	body, err := io.ReadAll(io.LimitReader(reader, limit+1))
	if err != nil {
		var maxBytesError *http.MaxBytesError
		if errors.As(err, &maxBytesError) {
			http.Error(response, "request body too large", http.StatusRequestEntityTooLarge)
		} else {
			http.Error(response, "failed to read request", http.StatusBadRequest)
		}
		return nil, err
	}
	if int64(len(body)) > limit {
		err := &http.MaxBytesError{Limit: limit}
		http.Error(response, "request body too large", http.StatusRequestEntityTooLarge)
		return nil, err
	}
	return body, nil
}

func nestedString(root map[string]any, path ...string) string {
	value := any(root)
	for _, key := range path {
		object, ok := value.(map[string]any)
		if !ok {
			return ""
		}
		value = object[key]
	}
	result, _ := value.(string)
	return result
}

func nestedBool(root map[string]any, path ...string) *bool {
	value := any(root)
	for _, key := range path {
		object, ok := value.(map[string]any)
		if !ok {
			return nil
		}
		value = object[key]
	}
	result, ok := value.(bool)
	if !ok {
		return nil
	}
	return &result
}

func attributeString(attributes map[string]any, key string) string {
	value, _ := attributes[key].(string)
	return value
}

func writeJSON(response http.ResponseWriter, status int, value any) {
	response.Header().Set("Content-Type", "application/json")
	response.WriteHeader(status)
	_ = json.NewEncoder(response).Encode(value)
}

func main() {
	token := os.Getenv("WEBAPP_INGEST_TOKEN")
	capacity := defaultCapacity
	if raw := os.Getenv("WEBAPP_EVENT_CAPACITY"); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil {
			log.Fatalf("invalid WEBAPP_EVENT_CAPACITY: %v", err)
		}
		capacity = parsed
	}
	server, err := newDashboardServer(token, capacity)
	if err != nil {
		log.Fatal(err)
	}
	address := os.Getenv("WEBAPP_LISTEN_ADDRESS")
	if address == "" {
		address = "0.0.0.0:8080"
	}
	types := []string{"OpenShell CloudEvents", "NeMo Relay OTLP traces"}
	sort.Strings(types)
	log.Printf("development conformance receiver listening on %s for %s", address, strings.Join(types, " and "))
	httpServer := &http.Server{
		Addr:              address,
		Handler:           server.routes(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
	certificateFile := os.Getenv("WEBAPP_TLS_CERT_FILE")
	keyFile := os.Getenv("WEBAPP_TLS_KEY_FILE")
	if (certificateFile == "") != (keyFile == "") {
		log.Fatal("WEBAPP_TLS_CERT_FILE and WEBAPP_TLS_KEY_FILE must be configured together")
	}
	if certificateFile != "" {
		log.Fatal(httpServer.ListenAndServeTLS(certificateFile, keyFile))
	}
	log.Fatal(httpServer.ListenAndServe())
}
