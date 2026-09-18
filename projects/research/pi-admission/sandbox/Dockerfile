# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

FROM node:22.22.2-bookworm-slim@sha256:9f6d5975c7dca860947d3915877f85607946403fc55349f39b4bc3688448bb6e

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates fd-find ripgrep iproute2 nftables \
    && ln -s /usr/bin/fdfind /usr/local/bin/fd \
    && useradd --create-home --uid 1001 sandbox \
    && rm -rf /var/lib/apt/lists/*
COPY admission-ca.crt /usr/local/share/ca-certificates/admission-ca.crt
RUN update-ca-certificates
WORKDIR /app
COPY pi-harness/package.json pi-harness/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY pi-harness/ ./
RUN npm run build && mkdir /app/agent
COPY models.json /app/models.json
COPY model-selection.json /app/model-selection.json
COPY workspace/ /sandbox/workspace/
RUN mkdir -p /sandbox/workspace /sandbox/sessions \
    && chown -R sandbox:sandbox /sandbox \
    && chmod -R a+rX /app
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt
WORKDIR /sandbox/workspace
USER sandbox
