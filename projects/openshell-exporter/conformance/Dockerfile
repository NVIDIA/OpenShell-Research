# syntax=docker/dockerfile:1.19@sha256:b6afd42430b15f2d2a4c5a02b919e98a525b785b1aaff16747d2f623364e39b6
FROM --platform=$BUILDPLATFORM docker.io/library/golang:1.26.8-bookworm@sha256:9fdc884aacc3bec89b20ffc69f4bb369c78210e3e4f600387b5128b12c199f81 AS builder
ARG TARGETOS
ARG TARGETARCH
ARG VCS_REF
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download && go mod verify
COPY conformance ./conformance
RUN case "$VCS_REF" in *[!0-9a-f]*|'') exit 1;; esac \
    && test "${#VCS_REF}" -eq 40 \
    && CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH} go test -c \
      -ldflags "-X 'github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter/conformance.candidateCommit=${VCS_REF}'" \
      -o /out/destination-conformance ./conformance

FROM gcr.io/distroless/static-debian12:nonroot@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab
ARG VCS_REF
LABEL org.opencontainers.image.revision=$VCS_REF
COPY --from=builder /out/destination-conformance /usr/local/bin/destination-conformance
USER nonroot:nonroot
ENTRYPOINT ["/usr/local/bin/destination-conformance"]
CMD ["-test.v"]
