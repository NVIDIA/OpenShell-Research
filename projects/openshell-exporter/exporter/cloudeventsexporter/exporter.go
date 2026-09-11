// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package cloudeventsexporter

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"time"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/config/confighttp"
	"go.opentelemetry.io/collector/consumer/consumererror"
	"go.opentelemetry.io/collector/exporter"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/plog"
	"go.uber.org/zap"
)

const maxResponseBody = 64 * 1024

type cloudEventsExporter struct {
	config   *Config
	settings exporter.Settings
	client   *http.Client
	metrics  *exporterMetrics
}

func (e *cloudEventsExporter) start(ctx context.Context, host component.Host) error {
	if err := e.config.Validate(); err != nil {
		return err
	}
	client, err := (*confighttp.ClientConfig)(&e.config.ClientConfig).ToClient(
		ctx,
		host.GetExtensions(),
		e.settings.TelemetrySettings,
	)
	if err != nil {
		return err
	}
	client.CheckRedirect = refuseRedirect
	e.client = client
	return nil
}

func (e *cloudEventsExporter) shutdown(context.Context) error {
	if e.client != nil {
		e.client.CloseIdleConnections()
	}
	return nil
}

func refuseRedirect(*http.Request, []*http.Request) error {
	return http.ErrUseLastResponse
}

func (e *cloudEventsExporter) pushLogs(ctx context.Context, logs plog.Logs) error {
	if e.client == nil {
		return errors.New("CloudEvents HTTP client is not started")
	}
	encodedEvents := make([][]byte, 0, logs.LogRecordCount())
	oversizedCount := 0
	for resourceIndex := 0; resourceIndex < logs.ResourceLogs().Len(); resourceIndex++ {
		resourceLogs := logs.ResourceLogs().At(resourceIndex)
		for scopeIndex := 0; scopeIndex < resourceLogs.ScopeLogs().Len(); scopeIndex++ {
			records := resourceLogs.ScopeLogs().At(scopeIndex).LogRecords()
			for recordIndex := 0; recordIndex < records.Len(); recordIndex++ {
				event := e.cloudEvent(
					resourceLogs.Resource().Attributes(),
					records.At(recordIndex),
				)
				encoded, err := json.Marshal(event)
				if err != nil {
					return consumererror.NewPermanent(
						fmt.Errorf("marshal CloudEvent: %w", err),
					)
				}
				if len(encoded) > e.config.MaxEventBytes ||
					len(encoded)+2 > e.config.MaxRequestBytes {
					oversizedCount++
					continue
				}
				encodedEvents = append(encodedEvents, encoded)
			}
		}
	}

	batch := make([][]byte, 0, e.config.MaxEvents)
	batchBytes := 2
	for _, event := range encodedEvents {
		nextBytes := batchBytes + len(event)
		if len(batch) > 0 {
			nextBytes++
		}
		if len(batch) >= e.config.MaxEvents ||
			(len(batch) > 0 && nextBytes > e.config.MaxRequestBytes) {
			if err := e.sendBatch(ctx, batch); err != nil {
				return err
			}
			batch = batch[:0]
			batchBytes = 2
			nextBytes = batchBytes + len(event)
		}
		batch = append(batch, event)
		batchBytes = nextBytes
	}
	if len(batch) > 0 {
		if err := e.sendBatch(ctx, batch); err != nil {
			return err
		}
	}

	if oversizedCount > 0 {
		e.metrics.recordOversized(ctx, oversizedCount)
		logger := e.settings.Logger
		if logger == nil {
			logger = zap.NewNop()
		}
		logger.Error(
			"CloudEvent permanently rejected from webhook delivery because it exceeds limits",
			zap.Int("max_event_bytes", e.config.MaxEventBytes),
			zap.Int("max_request_bytes", e.config.MaxRequestBytes),
		)
		return consumererror.NewPermanent(fmt.Errorf(
			"%d CloudEvents exceed webhook size limits",
			oversizedCount,
		))
	}
	return nil
}

func (e *cloudEventsExporter) sendBatch(
	ctx context.Context,
	events [][]byte,
) error {
	payload := bytes.NewBuffer(make([]byte, 0, encodedBatchSize(events)))
	payload.WriteByte('[')
	for index, event := range events {
		if index > 0 {
			payload.WriteByte(',')
		}
		payload.Write(event)
	}
	payload.WriteByte(']')

	request, err := http.NewRequestWithContext(
		ctx,
		http.MethodPost,
		e.config.Endpoint,
		payload,
	)
	if err != nil {
		return consumererror.NewPermanent(
			fmt.Errorf("create CloudEvents request: %w", err),
		)
	}
	request.Header.Set("Content-Type", "application/cloudevents-batch+json")
	request.Header.Set("User-Agent", "openshell-event-exporter")

	started := time.Now()
	response, err := e.client.Do(request)
	if err != nil {
		e.metrics.recordNetworkFailure(ctx, time.Since(started))
		return fmt.Errorf("send CloudEvents batch: %w", err)
	}
	_, readErr := io.Copy(
		io.Discard,
		io.LimitReader(response.Body, maxResponseBody),
	)
	closeErr := response.Body.Close()
	if readErr != nil {
		e.metrics.recordNetworkFailure(ctx, time.Since(started))
		return fmt.Errorf("read CloudEvents acknowledgement: %w", readErr)
	}
	if closeErr != nil {
		e.metrics.recordNetworkFailure(ctx, time.Since(started))
		return fmt.Errorf("close CloudEvents acknowledgement: %w", closeErr)
	}
	e.metrics.recordResponse(ctx, response.StatusCode, len(events), time.Since(started))
	if response.StatusCode >= 200 && response.StatusCode < 300 {
		return nil
	}
	statusError := fmt.Errorf(
		"CloudEvents receiver returned HTTP %d",
		response.StatusCode,
	)
	if retryableStatus(response.StatusCode) {
		return statusError
	}
	return consumererror.NewPermanent(statusError)
}

func retryableStatus(status int) bool {
	return status == http.StatusRequestTimeout ||
		status == http.StatusTooEarly ||
		status == http.StatusTooManyRequests ||
		status >= 500
}

func encodedBatchSize(events [][]byte) int {
	size := 2
	for index, event := range events {
		size += len(event)
		if index > 0 {
			size++
		}
	}
	return size
}

func (e *cloudEventsExporter) cloudEvent(
	resource pcommon.Map,
	record plog.LogRecord,
) map[string]any {
	attributes := record.Attributes()
	eventID := stringAttribute(attributes, "cloudevents.id", "")
	if eventID == "" {
		encoded, _ := json.Marshal(record.Body().AsRaw())
		sum := sha256.Sum256(encoded)
		eventID = "sha256:" + hex.EncodeToString(sum[:])
	}
	event := map[string]any{
		"specversion": "1.0",
		"id":          eventID,
		"source": stringAttribute(
			attributes,
			"cloudevents.source",
			e.config.DefaultSource,
		),
		"type": stringAttribute(
			attributes,
			"cloudevents.type",
			"com.nvidia.openshell.stream.warning.v1",
		),
		"datacontenttype": "application/json",
		"data":            record.Body().AsRaw(),
	}
	if schema := stringAttribute(
		attributes,
		"cloudevents.dataschema",
		"",
	); schema != "" {
		event["dataschema"] = schema
	}
	if subject := stringAttribute(
		attributes,
		"cloudevents.subject",
		"",
	); subject != "" {
		event["subject"] = subject
	}
	if timestamp := record.Timestamp(); timestamp != 0 {
		event["time"] = timestamp.AsTime().UTC().Format(time.RFC3339Nano)
	}
	if correlation := stringAttribute(
		attributes,
		"openshell.correlation.id",
		"",
	); correlation != "" {
		event["openshellcorrelation"] = correlation
	}
	if serviceName := stringAttribute(resource, "service.name", ""); serviceName != "" {
		event["service"] = serviceName
	}
	if valid, ok := attributes.Get("openshell.ocsf.valid"); ok &&
		valid.Type() == pcommon.ValueTypeBool {
		event["openshellocsfvalid"] = valid.Bool()
	}
	return event
}

func stringAttribute(attributes pcommon.Map, key string, fallback string) string {
	value, ok := attributes.Get(key)
	if !ok || value.Type() != pcommon.ValueTypeStr {
		return fallback
	}
	return value.Str()
}
