# External destination conformance

This harness verifies a customer-operated CloudEvents and OTLP destination. It produces candidate-bound, credential-free JSON evidence. A local demo does not satisfy this gate.

## What it tests

- CloudEvents HTTPS, OTLP/HTTP, and OTLP/gRPC acceptance;
- invalid-authentication rejection;
- stable IDs and duplicate delivery;
- count, event-size, and request-size limits;
- retryable and permanent response semantics;
- bearer and client-certificate rotation;
- expired credential rejection;
- independent backend persistence;
- exact correlated OpenShell and Relay projections;
- guarded canary and multi-gateway reconciliation.

## Build

Build from a clean candidate checkout and bake the exact commit into the image:

~~~sh
docker build \
  --build-arg VCS_REF="$(git rev-parse HEAD)" \
  -f conformance/Dockerfile \
  -t openshell-destination-conformance .
~~~

## Core transport run

Prepare a unique run ID, protected evidence directory, current bearer file, destination URLs, and optional CA/client-certificate files.

~~~sh
export CONFORMANCE_RUN_ID=v0-0-4-rc2-20260831-a
export CONFORMANCE_CLOUDEVENTS_URL=https://events.example/v1/events
export CONFORMANCE_OTLP_HTTP_URL=https://otel.example/v1/traces
export CONFORMANCE_OTLP_GRPC_ENDPOINT=otel.example:4317
export CONFORMANCE_REPORT_DIR="$PWD/conformance-evidence"
mkdir -m 700 "$CONFORMANCE_REPORT_DIR"
~~~

Run the three transport tests with read-only secret mounts:

~~~sh
docker run --rm \
  -e CONFORMANCE_RUN_ID \
  -e CONFORMANCE_CLOUDEVENTS_URL \
  -e CONFORMANCE_OTLP_HTTP_URL \
  -e CONFORMANCE_OTLP_GRPC_ENDPOINT \
  -e CONFORMANCE_TOKEN_FILE=/secrets/token \
  -e CONFORMANCE_REPORT_DIR=/evidence \
  -v "$CONFORMANCE_TOKEN_FILE:/secrets/token:ro" \
  -v "$CONFORMANCE_REPORT_DIR:/evidence:rw" \
  openshell-destination-conformance \
  -test.run "^TestExternal(CloudEventsHTTPS|OTLPHTTP|OTLPGRPC)$" -test.v
~~~

Add `CONFORMANCE_CA_FILE`, `CONFORMANCE_CLIENT_CERT_FILE`, and `CONFORMANCE_CLIENT_KEY_FILE` with read-only mounts when the destination requires private trust or mTLS.

The harness refuses HTTP redirects and verifies invalid credentials are rejected. Probe IDs are deterministic within one run and distinct across run IDs and transports.

## Credential rotation

Bearer and client-certificate rotation are two-phase tests:

1. run the matching test with `CONFORMANCE_*_ROTATION_PHASE=before` and the currently accepted credential;
2. rotate the destination through its approved operator process;
3. run `after` with both current and retired credential files;
4. require the current identity to succeed and the still-valid retired identity to fail on all transports.

Tests:

~~~text
TestExternalBearerTokenRotation
TestExternalClientCertificateRotation
TestExternalExpiredCredentialRejection
~~~

Use the same run ID and protected report directory across the two phases. Evidence files are mode 0600 and never overwritten.

## Delivery semantics and limits

The checked tests validate evidence from isolated fault receivers:

~~~text
TestValidateCloudEventsResponseSemanticsEvidence
TestValidateCloudEventsLimitsEvidence
TestValidateOTLPGRPCResponseSemanticsEvidence
~~~

They cover 408, 425, 429, 5xx retries, permanent 3xx/4xx handling, count and byte splitting, oversized recovery, and gRPC retry classifications.

## Independent backend proof

An independently operated backend must export its stored records so the harness can prove:

- exact candidate commit;
- expected event, trace, and span IDs;
- `(source,id)` deduplication;
- unknown-field retention;
- separate OTLP HTTP and gRPC identity;
- authenticated rejection evidence;
- response, limit, and rotation evidence.

Set the documented backend evidence variables and run:

~~~text
TestVerifyExternalBackendEvidence
TestVerifyExternalCorrelatedTimeline
~~~

## Canary and pilot

The guarded long-running verifier consumes identity-only source, recovery,
destination, and monitoring projections. It does not collect raw content. Its
input contract is defined in [pilot_reconciliation_test.go](pilot_reconciliation_test.go).

## Evidence handling

- Use a new report directory for every candidate run.
- Keep secret files read-only and outside Git.
- Store reports in an approved evidence location.
- Do not edit or overwrite generated reports.
- Publish the report hashes with the release review.
- Keep unexecuted gates explicitly marked as unexecuted.
