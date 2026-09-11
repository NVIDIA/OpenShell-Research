# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: Apache-2.0

id: openshell-relay-otlp
display_name: OpenShell Relay OTLP
description: Endpoint-bound NeMo Relay OTLP/HTTP delivery to the OpenShell Event Exporter
category: other
credentials:
  - name: ingest_token
    description: Exporter Relay ingestion bearer token
    env_vars: [NEMO_RELAY_OTLP_TOKEN]
    required: true
    auth_style: bearer
    header_name: authorization
discovery:
  credentials: [ingest_token]
endpoints:
  - host: __OPENSHELL_EXPORTER_HOST__
    port: 4318
    path: /v1/traces
    protocol: rest
    enforcement: enforce
    rules:
      - allow:
          method: POST
          path: /v1/traces
binaries:
  - /opt/hermes/.venv/bin/python
  - /opt/hermes/.venv/bin/python3
  - /opt/hermes/.venv/bin/python3.13
  - /usr/bin/python3
  - /usr/bin/python3.13
