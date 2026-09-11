// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package cloudeventsexporter

import (
	"context"
	"errors"
	"io"
	"net/http"
	"testing"

	"go.opentelemetry.io/collector/consumer/consumererror"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/metric/metricdata"
)

func TestAcknowledgementFailuresAreRetryableAndNotCountedDelivered(t *testing.T) {
	tests := []struct {
		name string
		body io.ReadCloser
	}{
		{name: "read failure", body: &faultyAcknowledgement{readErr: errors.New("truncated acknowledgement")}},
		{name: "close failure", body: &faultyAcknowledgement{closeErr: errors.New("close acknowledgement")}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			reader := sdkmetric.NewManualReader()
			provider := sdkmetric.NewMeterProvider(sdkmetric.WithReader(reader))
			t.Cleanup(func() { _ = provider.Shutdown(context.Background()) })
			client := &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
				return &http.Response{
					StatusCode: http.StatusAccepted,
					Body:       test.body,
					Header:     make(http.Header),
				}, nil
			})}
			implementation := testExporter(client)
			implementation.metrics = newExporterMetrics(provider)
			err := implementation.pushLogs(context.Background(), oneLog(t, "evidence"))
			if err == nil || consumererror.IsPermanent(err) {
				t.Fatalf("acknowledgement error=%v, want retryable", err)
			}

			var resource metricdata.ResourceMetrics
			if err := reader.Collect(context.Background(), &resource); err != nil {
				t.Fatal(err)
			}
			retryable := int64(0)
			delivered := int64(0)
			for _, scope := range resource.ScopeMetrics {
				for _, current := range scope.Metrics {
					sum, ok := current.Data.(metricdata.Sum[int64])
					if !ok {
						continue
					}
					for _, point := range sum.DataPoints {
						switch current.Name {
						case "openshell.exporter.delivery.retryable_failures":
							retryable += point.Value
						case "openshell.exporter.delivery.events":
							delivered += point.Value
						}
					}
				}
			}
			if retryable != 1 {
				t.Fatalf("retryable failure metric=%d, want 1", retryable)
			}
			if delivered != 0 {
				t.Fatalf("delivered event metric=%d after uncertain acknowledgement, want 0", delivered)
			}
		})
	}
}

func TestNetworkFailureIsRetryable(t *testing.T) {
	client := &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
		return nil, errors.New("destination unavailable")
	})}
	err := testExporter(client).pushLogs(context.Background(), oneLog(t, "evidence"))
	if err == nil || consumererror.IsPermanent(err) {
		t.Fatalf("network error=%v, want retryable", err)
	}
}

type faultyAcknowledgement struct {
	readErr  error
	closeErr error
}

func (body *faultyAcknowledgement) Read([]byte) (int, error) {
	if body.readErr != nil {
		return 0, body.readErr
	}
	return 0, io.EOF
}

func (body *faultyAcknowledgement) Close() error {
	return body.closeErr
}
