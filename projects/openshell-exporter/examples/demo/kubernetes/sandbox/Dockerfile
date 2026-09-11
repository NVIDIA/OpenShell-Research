# syntax=docker/dockerfile:1.7
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: Apache-2.0

ARG BASE_IMAGE=ghcr.io/nvidia/nemoclaw/hermes-sandbox-base@sha256:57c091ab9b31c924eac0050e66c834c37df875154a254964302a31b119b50b96
FROM ${BASE_IMAGE}

USER root
ARG UTIL_LINUX_VERSION=2.41.5-0+deb13u1
ARG BSDUTILS_VERSION=1:2.41.5-0+deb13u1
ARG LOGIN_VERSION=1:4.16.0-2+really2.41.5-0+deb13u1
ARG LINUX_LIBC_DEV_VERSION=6.12.101-1
RUN set -eu \
    && apt-get update \
    && apt-get install --yes --no-install-recommends \
      "bsdutils=${BSDUTILS_VERSION}" \
      "libblkid1=${UTIL_LINUX_VERSION}" \
      "liblastlog2-2=${UTIL_LINUX_VERSION}" \
      "libmount1=${UTIL_LINUX_VERSION}" \
      "libsmartcols1=${UTIL_LINUX_VERSION}" \
      "libuuid1=${UTIL_LINUX_VERSION}" \
      "linux-libc-dev=${LINUX_LIBC_DEV_VERSION}" \
      "login=${LOGIN_VERSION}" \
      "mount=${UTIL_LINUX_VERSION}" \
      "util-linux=${UTIL_LINUX_VERSION}" \
    && rm -rf /var/lib/apt/lists/*
ARG HERMES_VERSION=v2026.8.16
ARG HERMES_TARBALL_SHA256=655639384767611feee5ef5d6871e1a1b2294f7b1fd80fb401e9b888a418f4f9
RUN --mount=type=secret,id=host_ca_bundle,required=false set -eu \
    && ca_bundle=/etc/ssl/certs/ca-certificates.crt \
    && if [ -s /run/secrets/host_ca_bundle ]; then ca_bundle=/run/secrets/host_ca_bundle; fi \
    && export CURL_CA_BUNDLE="$ca_bundle" SSL_CERT_FILE="$ca_bundle" REQUESTS_CA_BUNDLE="$ca_bundle" \
    && curl -L --proto '=https' --tlsv1.2 --retry 5 --retry-delay 2 --retry-all-errors -sSf \
      "https://codeload.github.com/NousResearch/hermes-agent/tar.gz/refs/tags/${HERMES_VERSION}" \
      -o /tmp/hermes.tar.gz \
    && printf '%s  %s\n' "$HERMES_TARBALL_SHA256" /tmp/hermes.tar.gz >/tmp/hermes.tar.gz.sha256 \
    && sha256sum -c /tmp/hermes.tar.gz.sha256 \
    && rm -rf /opt/hermes \
    && mkdir -p /opt/hermes \
    && tar -xzf /tmp/hermes.tar.gz -C /opt/hermes --strip-components=1 \
    && rm /tmp/hermes.tar.gz /tmp/hermes.tar.gz.sha256 \
    && cd /opt/hermes \
    && uv sync --python /usr/bin/python3.13 --frozen --no-dev --no-cache \
    && ln -sfn /opt/hermes/.venv/bin/hermes /usr/local/bin/hermes \
    && hermes --version

RUN --mount=type=secret,id=openshell_exporter_ca,required=true set -eu \
    && install -m 0644 /run/secrets/openshell_exporter_ca /usr/local/share/ca-certificates/openshell-exporter-demo.crt \
    && update-ca-certificates

RUN install -d -o sandbox -g sandbox -m 0700 \
      /sandbox/.hermes /sandbox/.hermes/nemo-relay /sandbox/telemetry/atof /sandbox/telemetry/atif
COPY --chown=sandbox:sandbox examples/demo/kubernetes/sandbox/config.yaml /sandbox/.hermes/config.yaml
COPY --chown=sandbox:sandbox examples/demo/kubernetes/sandbox/plugins.toml /sandbox/.hermes/nemo-relay/plugins.toml
COPY integrations/hermes/hermes-correlated.sh /usr/local/bin/hermes-correlated
RUN chmod 0755 /usr/local/bin/hermes-correlated \
    && chmod 0600 /sandbox/.hermes/config.yaml /sandbox/.hermes/nemo-relay/plugins.toml \
    && chown -R sandbox:sandbox /sandbox/.hermes /sandbox/telemetry
USER sandbox
RUN NEMO_RELAY_OTEL_AUTHORIZATION="Bearer build-validation-only" \
    /opt/hermes/.venv/bin/python -c 'import asyncio, tomllib; from pathlib import Path; import nemo_relay; config = tomllib.loads(Path("/sandbox/.hermes/nemo-relay/plugins.toml").read_text()); report = asyncio.run(nemo_relay.plugin.initialize(config)); assert not report.get("diagnostics"), report; nemo_relay.plugin.clear(); print("NeMo Relay configuration valid")' \
    && rm -f /sandbox/telemetry/atof/hermes-atof.jsonl

ARG OPENSHELL_DEMO_BUILD_REVISION=development
LABEL io.nvidia.openshell.demo.sandbox-revision="$OPENSHELL_DEMO_BUILD_REVISION"

ENV HERMES_HOME=/sandbox/.hermes \
    HERMES_DISABLE_LAZY_INSTALLS=1 \
    HERMES_NEMO_RELAY_PLUGINS_TOML=/sandbox/.hermes/nemo-relay/plugins.toml \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    PATH=/opt/hermes/.venv/bin:/usr/local/bin:/usr/bin:/bin
WORKDIR /sandbox
CMD ["sleep", "infinity"]
