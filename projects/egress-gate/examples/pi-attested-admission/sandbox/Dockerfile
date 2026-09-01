# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

FROM ghcr.io/nvidia/openshell-community/sandboxes/pi:latest

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends fd-find ripgrep \
    && ln -s /usr/bin/fdfind /usr/local/bin/fd \
    && mkdir -p /sandbox/workspace \
    && chown sandbox:sandbox /sandbox/workspace \
    && rm -rf /var/lib/apt/lists/*
USER sandbox
