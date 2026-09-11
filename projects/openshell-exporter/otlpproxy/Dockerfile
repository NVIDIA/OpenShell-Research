# syntax=docker/dockerfile:1.19@sha256:b6afd42430b15f2d2a4c5a02b919e98a525b785b1aaff16747d2f623364e39b6
FROM --platform=$BUILDPLATFORM docker.io/library/golang:1.26.8-bookworm@sha256:9fdc884aacc3bec89b20ffc69f4bb369c78210e3e4f600387b5128b12c199f81 AS builder

ARG TARGETOS
ARG TARGETARCH
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download && go mod verify
RUN go install go.opentelemetry.io/collector/cmd/builder@v0.160.0
COPY otlpproxy/builder-config.yaml ./
COPY extension ./extension
RUN mkdir -p _build && CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH} /go/bin/builder --config builder-config.yaml

FROM gcr.io/distroless/static-debian12:nonroot@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab

ARG VERSION=dev
ARG VCS_REF=unknown
ARG CREATED=unknown
LABEL org.opencontainers.image.title="OpenShell OTLP Proxy" \
      org.opencontainers.image.description="Minimal workload-local NeMo Relay OTLP proxy" \
      org.opencontainers.image.source="https://github.com/NVIDIA/OpenShell-Research" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${CREATED}"

COPY --from=builder /src/_build/otlp-proxy/openshell-otlp-proxy /usr/local/bin/openshell-otlp-proxy

USER nonroot:nonroot
EXPOSE 13134 8889 4318
ENTRYPOINT ["/usr/local/bin/openshell-otlp-proxy"]
CMD ["--config", "/etc/openshell-otlp-proxy/config.yaml"]
