# syntax=docker/dockerfile:1.7
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: Apache-2.0

FROM docker.io/library/golang:1.26.8-bookworm@sha256:9fdc884aacc3bec89b20ffc69f4bb369c78210e3e4f600387b5128b12c199f81 AS builder
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY examples/demo/real-gateway/webapp/ ./examples/demo/real-gateway/webapp/
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/openshell-conformance-receiver ./examples/demo/real-gateway/webapp

FROM gcr.io/distroless/static-debian12:nonroot@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab
COPY --from=builder /out/openshell-conformance-receiver /usr/local/bin/openshell-conformance-receiver
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/openshell-conformance-receiver"]
