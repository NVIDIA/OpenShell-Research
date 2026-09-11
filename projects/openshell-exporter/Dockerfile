# syntax=docker/dockerfile:1.19@sha256:b6afd42430b15f2d2a4c5a02b919e98a525b785b1aaff16747d2f623364e39b6
FROM --platform=$BUILDPLATFORM docker.io/library/golang:1.26.8-bookworm@sha256:9fdc884aacc3bec89b20ffc69f4bb369c78210e3e4f600387b5128b12c199f81 AS builder

ARG TARGETOS
ARG TARGETARCH
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download && go mod verify
RUN go install go.opentelemetry.io/collector/cmd/builder@v0.160.0

COPY builder-config.yaml ./
COPY cmd/healthcheck ./cmd/healthcheck
COPY cmd/sandbox-injector ./cmd/sandbox-injector
COPY sandboxinjector ./sandboxinjector
COPY processor ./processor
COPY receiver ./receiver
COPY exporter ./exporter
COPY extension ./extension

RUN CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH} /go/bin/builder --config builder-config.yaml
RUN CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH} go build -trimpath -ldflags='-s -w' -o /out/healthcheck ./cmd/healthcheck
RUN CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH} go build -trimpath -ldflags='-s -w' -o /out/sandbox-injector ./cmd/sandbox-injector

FROM gcr.io/distroless/static-debian12:nonroot@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab

ARG VERSION=dev
ARG VCS_REF=unknown
ARG CREATED=unknown
LABEL org.opencontainers.image.title="OpenShell Event Exporter" \
      org.opencontainers.image.description="Vendor-neutral OpenShell event and OCSF exporter" \
      org.opencontainers.image.source="https://github.com/NVIDIA/OpenShell-Research" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${CREATED}"

COPY --from=builder /src/_build/openshell-event-exporter /usr/local/bin/openshell-event-exporter
COPY --from=builder /out/healthcheck /usr/local/bin/healthcheck
COPY --from=builder /out/sandbox-injector /usr/local/bin/sandbox-injector

USER nonroot:nonroot
EXPOSE 13133 8888 4317 4318 4319 4320
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD ["/usr/local/bin/healthcheck"]
ENTRYPOINT ["/usr/local/bin/openshell-event-exporter"]
CMD ["--config", "/etc/openshell-event-exporter/config.yaml"]
