// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestStatusDistinguishesObservationAcknowledgementAndMissingMetrics(t *testing.T) {
	const scrape = `# HELP ignored arbitrary text
openshell_exporter_source_last_success_unixtime{source="ocsf",producer="openshellprocessor"} 1700000000
openshell_exporter_destination_last_success_unixtime{exporter="cloudevents/a"} 1700000010
openshell_exporter_delivery_events_total{exporter="cloudevents/a",status_code="202"} 10
openshell_exporter_delivery_permanent_rejections{exporter="cloudevents/b",status_code="400"} 2
otelcol_exporter_queue_size{exporter="cloudevents/b"} 7
otelcol_exporter_queue_capacity{exporter="cloudevents/b"} 100
openshell_exporter_storage_available{storage="cloudevents_queue"} 0
openshell_exporter_storage_free_bytes{storage="cloudevents_queue"} NaN
unrelated_metric{secret="not-to-be-printed"} 42
`
	var output bytes.Buffer
	if err := renderStatus(&output, scrape); err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{
		"observation is not destination delivery",
		"Last observation (UTC) {source=\"ocsf\",producer=\"openshellprocessor\"}: 2023-11-14T22:13:20Z",
		"Last acknowledgement (UTC) {exporter=\"cloudevents/a\"}: 2023-11-14T22:13:30Z",
		"Acknowledged events {exporter=\"cloudevents/a\",status_code=\"202\"}: 10",
		"Permanently rejected responses {exporter=\"cloudevents/b\",status_code=\"400\"}: 2",
		"Backlog {exporter=\"cloudevents/b\"}: 7",
		"Available (1=yes, 0=no) {storage=\"cloudevents_queue\"}: 0",
		"Free bytes {storage=\"cloudevents_queue\"}: unavailable",
		"Reconnects: unavailable",
	} {
		if !strings.Contains(output.String(), want) {
			t.Errorf("missing %q in status:\n%s", want, &output)
		}
	}
	if strings.Contains(output.String(), "not-to-be-printed") {
		t.Fatal("unrelated metrics reached output")
	}
}

func TestStatusRejectsFailedRedirectedOversizedAndMalformedScrapes(t *testing.T) {
	for _, tc := range []struct {
		name, body string
		code       int
	}{
		{"unavailable", "", 503},
		{"redirect", "", 302},
		{"oversized", strings.Repeat("x", (4<<20)+1), 200},
		{"bad value", "otelcol_exporter_queue_size nope", 200},
		{"bad labels", "otelcol_exporter_queue_size{ 1", 200},
	} {
		t.Run(tc.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Location", "/redirect")
				w.WriteHeader(tc.code)
				_, _ = w.Write([]byte(tc.body))
			}))
			defer server.Close()
			var output bytes.Buffer
			if err := deliveryStatus(t.Context(), &output, server.URL); err == nil {
				t.Fatal("bad scrape accepted")
			}
			if output.Len() != 0 {
				t.Fatal("partial status emitted after failed scrape")
			}
		})
	}
}

func TestStatusFetchAndCancellation(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte("otelcol_exporter_queue_size{exporter=\"cloudevents\"} 0\n"))
	}))
	defer server.Close()
	var output bytes.Buffer
	if err := deliveryStatus(t.Context(), &output, server.URL); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(output.String(), "Last acknowledgement (UTC): unavailable") {
		t.Fatal("missing acknowledgement treated as successful delivery")
	}
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	if err := deliveryStatus(ctx, &output, server.URL); err == nil {
		t.Fatal("cancellation ignored")
	}
	for _, endpoint := range []string{"file:///etc/passwd", "http://user:password@localhost/metrics", ":bad"} {
		if err := deliveryStatus(t.Context(), &output, endpoint); err == nil {
			t.Fatal("unsafe endpoint accepted")
		}
	}
}
