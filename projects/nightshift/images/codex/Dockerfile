ARG BASE_IMAGE=ghcr.io/nvidia/openshell-community/sandboxes/base@sha256:aeef1c63f00e2913ea002ccb3aaf925f338b5c5d70e63576f0d95c16a138044e
FROM ${BASE_IMAGE}

ARG CODEX_VERSION=0.147.0

USER root
RUN apt-get update \
    && apt-get install --yes --no-install-recommends jq ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global --no-audit --no-fund "@openai/codex@${CODEX_VERSION}" \
    && test "$(codex --version)" = "codex-cli ${CODEX_VERSION}"

LABEL org.opencontainers.image.title="Nightshift codex sandbox" \
      org.opencontainers.image.version="${CODEX_VERSION}"

USER sandbox
