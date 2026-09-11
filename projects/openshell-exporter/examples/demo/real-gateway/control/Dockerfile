# syntax=docker/dockerfile:1.7
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: Apache-2.0

FROM docker.io/library/debian:13-slim@sha256:3a39a0592364683e6bab97937b72cad5a8fa6dcbbee90edb3bb48c7f8e94f258
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends \
      bsdutils=1:2.41.5-0+deb13u1 \
      libblkid1=2.41.5-0+deb13u1 \
      liblastlog2-2=2.41.5-0+deb13u1 \
      libmount1=2.41.5-0+deb13u1 \
      libsmartcols1=2.41.5-0+deb13u1 \
      libuuid1=2.41.5-0+deb13u1 \
      login=1:4.16.0-2+really2.41.5-0+deb13u1 \
      mount=2.41.5-0+deb13u1 \
      openssh-client=1:10.0p1-7+deb13u4 \
      util-linux=2.41.5-0+deb13u1 \
    && rm -rf /var/lib/apt/lists/*
COPY examples/demo/real-gateway/control/openshell-artifacts.env /tmp/openshell-artifacts.env
COPY examples/demo/real-gateway/runtime/downloads/openshell-x86_64-unknown-linux-musl.tar.gz /tmp/
COPY examples/demo/real-gateway/runtime/downloads/openshell-aarch64-unknown-linux-musl.tar.gz /tmp/
ARG TARGETARCH
RUN set -eu; \
    . /tmp/openshell-artifacts.env; \
    printf '%s  %s\n' "$OPENSHELL_CONTROL_AMD64_SHA256" "/tmp/$OPENSHELL_CONTROL_AMD64_FILE" \
      | sha256sum --check --strict -; \
    printf '%s  %s\n' "$OPENSHELL_CONTROL_ARM64_SHA256" "/tmp/$OPENSHELL_CONTROL_ARM64_FILE" \
      | sha256sum --check --strict -; \
    case "$TARGETARCH" in \
      amd64) archive="/tmp/$OPENSHELL_CONTROL_AMD64_FILE" ;; \
      arm64) archive="/tmp/$OPENSHELL_CONTROL_ARM64_FILE" ;; \
      *) echo "unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    tar -xzf "$archive" -C /usr/local/bin; \
    rm -f /tmp/openshell-*.tar.gz /tmp/openshell-artifacts.env; \
    openshell --version
ENTRYPOINT ["/usr/local/bin/openshell"]
