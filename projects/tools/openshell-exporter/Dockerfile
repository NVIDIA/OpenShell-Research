# syntax=docker/dockerfile:1.28.0@sha256:bb22d9815c728170f72750f4e5b0d672e06176142e1d602c7e66c050100b7e5b
FROM --platform=$BUILDPLATFORM docker.io/library/golang:1.27.2-bookworm@sha256:5cf287a799e6b94384bad13d16b14904c531f51ba65792237e122ce42b392f61 AS builder
ARG TARGETOS
ARG TARGETARCH
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download && go mod verify
COPY . .
RUN CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH} go build -trimpath -ldflags='-s -w' -o /out/ ./cmd/openshell-event-exporter ./cmd/healthcheck
RUN go install github.com/google/go-licenses/v2@v2.0.1 && \
    CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH} /go/bin/go-licenses save ./cmd/openshell-event-exporter ./cmd/healthcheck \
    --ignore=github.com/NVIDIA/OpenShell-Research/projects/tools/openshell-exporter --save_path=/out/licenses

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
