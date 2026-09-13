# syntax=docker/dockerfile:1.19@sha256:b6afd42430b15f2d2a4c5a02b919e98a525b785b1aaff16747d2f623364e39b6
FROM --platform=$BUILDPLATFORM docker.io/library/golang:1.26.8-bookworm@sha256:9fdc884aacc3bec89b20ffc69f4bb369c78210e3e4f600387b5128b12c199f81 AS builder
ARG TARGETOS
ARG TARGETARCH
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download && go mod verify
COPY . .
RUN CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH} go build -trimpath -ldflags='-s -w' -o /out/ ./cmd/openshell-event-exporter ./cmd/healthcheck
RUN go install github.com/google/go-licenses/v2@v2.0.1 && \
    CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH} /go/bin/go-licenses save ./cmd/openshell-event-exporter ./cmd/healthcheck \
    --ignore=github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter --save_path=/out/licenses

FROM gcr.io/distroless/static-debian12:nonroot@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab

ARG VERSION=dev
ARG VCS_REF=unknown
ARG CREATED=unknown
ARG IMAGE_SOURCE=https://github.com/NVIDIA/OpenShell-Research
LABEL org.opencontainers.image.title="OpenShell Event Exporter" \
      org.opencontainers.image.description="Vendor-neutral OpenShell event and OCSF exporter" \
      org.opencontainers.image.source="${IMAGE_SOURCE}" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${CREATED}"

COPY --from=builder /out/openshell-event-exporter /usr/local/bin/openshell-event-exporter
COPY --from=builder /out/healthcheck /usr/local/bin/healthcheck

COPY LICENSE NOTICE /usr/share/licenses/openshell-exporter/
COPY --from=builder /out/licenses /usr/share/licenses/third-party/
COPY --from=builder /usr/local/go/LICENSE /usr/share/licenses/go/LICENSE

USER nonroot:nonroot
EXPOSE 13133 8888 4317 4318 4319 4320
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD ["/usr/local/bin/healthcheck"]
ENTRYPOINT ["/usr/local/bin/openshell-event-exporter"]
CMD ["--config", "/etc/openshell-event-exporter/config.yaml"]
