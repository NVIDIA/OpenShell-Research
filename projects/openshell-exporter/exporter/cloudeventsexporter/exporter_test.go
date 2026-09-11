// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package cloudeventsexporter

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"go.opentelemetry.io/collector/consumer/consumererror"
	"go.opentelemetry.io/collector/exporter"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/plog"
	"go.uber.org/zap"
)

func TestCloudEventsBatchContract(t *testing.T) {
	var received []map[string]any
	client := &http.Client{Transport: roundTripFunc(
		func(request *http.Request) (*http.Response, error) {
			if got := request.Header.Get("Content-Type"); got !=
				"application/cloudevents-batch+json" {
				t.Errorf("content type=%q", got)
			}
			if err := json.NewDecoder(request.Body).Decode(&received); err != nil {
				t.Error(err)
			}
			return response(http.StatusAccepted), nil
		},
	)}
	logs := oneLog(t, map[string]any{"schema_version": "1.0"})
	record := onlyLogRecord(logs)
	record.Attributes().PutStr("cloudevents.id", "sha256:event-1")
	record.Attributes().PutStr("cloudevents.source", "openshell://test")
	record.Attributes().PutStr(
		"cloudevents.type",
		"com.nvidia.openshell.ocsf.4001.v1",
	)
	record.Attributes().PutStr(
		"cloudevents.dataschema",
		"urn:openshell:event-envelope:1",
	)
	record.SetObservedTimestamp(pcommon.NewTimestampFromTime(time.Now()))

	implementation := testExporter(client)
	if err := implementation.pushLogs(context.Background(), logs); err != nil {
		t.Fatal(err)
	}
	if len(received) != 1 {
		t.Fatalf("received %d events, want 1", len(received))
	}
	if received[0]["id"] != "sha256:event-1" {
		t.Fatalf("id=%#v", received[0]["id"])
	}
	if received[0]["dataschema"] != "urn:openshell:event-envelope:1" {
		t.Fatalf("dataschema=%#v", received[0]["dataschema"])
	}
	if _, exists := received[0]["time"]; exists {
		t.Fatal("observation time was incorrectly substituted for source time")
	}
}

func TestSplitsByCountAndEncodedRequestBytes(t *testing.T) {
	var mu sync.Mutex
	requestSizes := make([]int, 0)
	eventCounts := make([]int, 0)
	client := &http.Client{Transport: roundTripFunc(
		func(request *http.Request) (*http.Response, error) {
			body, err := io.ReadAll(request.Body)
			if err != nil {
				t.Error(err)
			}
			var events []map[string]any
			if err := json.Unmarshal(body, &events); err != nil {
				t.Error(err)
			}
			mu.Lock()
			requestSizes = append(requestSizes, len(body))
			eventCounts = append(eventCounts, len(events))
			mu.Unlock()
			return response(http.StatusNoContent), nil
		},
	)}
	implementation := testExporter(client)
	implementation.config.MaxEvents = 2
	implementation.config.MaxRequestBytes = 750
	logs := plog.NewLogs()
	records := logs.ResourceLogs().
		AppendEmpty().
		ScopeLogs().
		AppendEmpty().
		LogRecords()
	for index := 0; index < 5; index++ {
		record := records.AppendEmpty()
		record.Body().SetStr(strings.Repeat("x", 250))
		record.Attributes().PutStr(
			"cloudevents.id",
			"sha256:"+strings.Repeat(string(rune('a'+index)), 64),
		)
	}
	if err := implementation.pushLogs(context.Background(), logs); err != nil {
		t.Fatal(err)
	}
	if len(requestSizes) < 3 {
		t.Fatalf("requests=%d, want at least 3", len(requestSizes))
	}
	for index, size := range requestSizes {
		if size > implementation.config.MaxRequestBytes {
			t.Fatalf("request %d size=%d", index, size)
		}
		if eventCounts[index] > implementation.config.MaxEvents {
			t.Fatalf("request %d events=%d", index, eventCounts[index])
		}
	}
}

func TestHTTPDisposition(t *testing.T) {
	tests := []struct {
		status    int
		permanent bool
	}{
		{status: http.StatusRequestTimeout},
		{status: http.StatusTooEarly},
		{status: http.StatusTooManyRequests},
		{status: http.StatusInternalServerError},
		{status: http.StatusMultipleChoices, permanent: true},
		{status: http.StatusBadRequest, permanent: true},
		{status: http.StatusUnauthorized, permanent: true},
		{status: http.StatusRequestEntityTooLarge, permanent: true},
	}
	for _, test := range tests {
		t.Run(http.StatusText(test.status), func(t *testing.T) {
			client := &http.Client{Transport: roundTripFunc(
				func(*http.Request) (*http.Response, error) {
					return response(test.status), nil
				},
			)}
			err := testExporter(client).pushLogs(
				context.Background(),
				oneLog(t, "event"),
			)
			if err == nil {
				t.Fatal("expected error")
			}
			if consumererror.IsPermanent(err) != test.permanent {
				t.Fatalf(
					"permanent=%v, want %v: %v",
					consumererror.IsPermanent(err),
					test.permanent,
					err,
				)
			}
		})
	}
}

func TestHTTPDispositionExhaustive(t *testing.T) {
	for statusCode := 300; statusCode <= 599; statusCode++ {
		expectedRetryable := statusCode == http.StatusRequestTimeout ||
			statusCode == http.StatusTooEarly ||
			statusCode == http.StatusTooManyRequests ||
			statusCode >= 500
		if retryableStatus(statusCode) != expectedRetryable {
			t.Fatalf("status %d retryable=%v, want %v", statusCode, retryableStatus(statusCode), expectedRetryable)
		}
		client := &http.Client{Transport: roundTripFunc(
			func(*http.Request) (*http.Response, error) {
				return response(statusCode), nil
			},
		)}
		err := testExporter(client).pushLogs(context.Background(), oneLog(t, "event"))
		if err == nil {
			t.Fatalf("status %d returned no error", statusCode)
		}
		if consumererror.IsPermanent(err) == expectedRetryable {
			t.Fatalf(
				"status %d permanent=%v, want %v: %v",
				statusCode,
				consumererror.IsPermanent(err),
				!expectedRetryable,
				err,
			)
		}
	}
}

func TestRedirectIsPermanentAndNotFollowed(t *testing.T) {
	followed := false
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path == "/redirected" {
			followed = true
			writer.WriteHeader(http.StatusAccepted)
			return
		}
		http.Redirect(writer, request, "/redirected", http.StatusFound)
	}))
	defer server.Close()

	client := server.Client()
	client.CheckRedirect = refuseRedirect
	implementation := testExporter(client)
	implementation.config.Endpoint = server.URL
	err := implementation.pushLogs(context.Background(), oneLog(t, "event"))
	if err == nil || !consumererror.IsPermanent(err) {
		t.Fatalf("redirect error=%v, want permanent", err)
	}
	if followed {
		t.Fatal("CloudEvents exporter followed a redirect")
	}
}

func TestRefuseRedirectUsesLastResponse(t *testing.T) {
	if err := refuseRedirect(nil, nil); !errors.Is(err, http.ErrUseLastResponse) {
		t.Fatalf("redirect policy error=%v", err)
	}
}

func TestOversizedSingleEventIsPermanentAndNotSent(t *testing.T) {
	called := false
	client := &http.Client{Transport: roundTripFunc(
		func(*http.Request) (*http.Response, error) {
			called = true
			return response(http.StatusAccepted), nil
		},
	)}
	implementation := testExporter(client)
	implementation.config.MaxEventBytes = 256
	err := implementation.pushLogs(
		context.Background(),
		oneLog(t, strings.Repeat("secret-redacted-recovery-evidence", 50)),
	)
	if !consumererror.IsPermanent(err) {
		t.Fatalf("error=%v, want permanent", err)
	}
	if called {
		t.Fatal("oversized event was sent")
	}
}

func FuzzBatchLimitsAreNeverExceeded(f *testing.F) {
	f.Add(uint8(5), uint16(250), uint8(2), uint16(750))
	f.Fuzz(func(t *testing.T, rawEvents uint8, rawPayload uint16, rawMaxEvents uint8, rawMaxRequest uint16) {
		eventCount := int(rawEvents%32) + 1
		payloadBytes := int(rawPayload%1024) + 1
		maxEvents := int(rawMaxEvents%16) + 1
		maxRequestBytes := int(rawMaxRequest%14336) + 2048
		delivered := 0
		client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
			body, err := io.ReadAll(request.Body)
			if err != nil {
				return nil, err
			}
			if len(body) > maxRequestBytes {
				t.Fatalf("request size=%d, maximum=%d", len(body), maxRequestBytes)
			}
			var events []json.RawMessage
			if err := json.Unmarshal(body, &events); err != nil {
				t.Fatalf("invalid CloudEvents batch: %v", err)
			}
			if len(events) == 0 || len(events) > maxEvents {
				t.Fatalf("batch events=%d, maximum=%d", len(events), maxEvents)
			}
			delivered += len(events)
			return response(http.StatusNoContent), nil
		})}

		implementation := testExporter(client)
		implementation.config.MaxEvents = maxEvents
		implementation.config.MaxEventBytes = 1024 * 1024
		implementation.config.MaxRequestBytes = maxRequestBytes
		logs := plog.NewLogs()
		records := logs.ResourceLogs().AppendEmpty().ScopeLogs().AppendEmpty().LogRecords()
		for index := 0; index < eventCount; index++ {
			record := records.AppendEmpty()
			record.Body().SetStr(strings.Repeat("x", payloadBytes))
			record.Attributes().PutStr("cloudevents.id", fmt.Sprintf("sha256:%064x", index+1))
		}
		if err := implementation.pushLogs(context.Background(), logs); err != nil {
			t.Fatal(err)
		}
		if delivered != eventCount {
			t.Fatalf("delivered=%d, want=%d", delivered, eventCount)
		}
	})
}

func BenchmarkCloudEventsBatchDelivery(b *testing.B) {
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if _, err := io.Copy(io.Discard, request.Body); err != nil {
			return nil, err
		}
		return response(http.StatusNoContent), nil
	})}
	implementation := testExporter(client)
	logs := plog.NewLogs()
	records := logs.ResourceLogs().AppendEmpty().ScopeLogs().AppendEmpty().LogRecords()
	for index := 0; index < 100; index++ {
		record := records.AppendEmpty()
		record.Body().SetStr(strings.Repeat("x", 2048))
		record.Attributes().PutStr("cloudevents.id", fmt.Sprintf("sha256:%064x", index+1))
	}
	b.ReportAllocs()
	b.SetBytes(int64(100 * 2048))
	for b.Loop() {
		if err := implementation.pushLogs(context.Background(), logs); err != nil {
			b.Fatal(err)
		}
	}
}

func testExporter(client *http.Client) *cloudEventsExporter {
	config := createDefaultConfig().(*Config)
	config.Endpoint = "http://receiver.example.test/v1/events"
	settings := exporter.Settings{}
	settings.Logger = zap.NewNop()
	config.AllowInsecureHTTP = true
	return &cloudEventsExporter{
		config:   config,
		settings: settings,
		client:   client,
		metrics:  newExporterMetrics(nil),
	}
}

func oneLog(t *testing.T, body any) plog.Logs {
	t.Helper()
	logs := plog.NewLogs()
	record := onlyLogRecord(logs)
	if err := record.Body().FromRaw(body); err != nil {
		t.Fatal(err)
	}
	return logs
}

func onlyLogRecord(logs plog.Logs) plog.LogRecord {
	if logs.ResourceLogs().Len() == 0 {
		logs.ResourceLogs().
			AppendEmpty().
			ScopeLogs().
			AppendEmpty().
			LogRecords().
			AppendEmpty()
	}
	return logs.ResourceLogs().At(0).ScopeLogs().At(0).LogRecords().At(0)
}

func response(status int) *http.Response {
	return &http.Response{
		StatusCode: status,
		Body:       io.NopCloser(strings.NewReader("")),
		Header:     make(http.Header),
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (fn roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return fn(request)
}
