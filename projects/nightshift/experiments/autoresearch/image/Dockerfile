# The autoresearch sandbox image: the OpenShell base image plus PyTorch and the
# fixed experiment code and data, baked read-only under /opt/autoresearch.
#
# Laptop (CPU, Docker Desktop on a Mac):
#   npm run image:autoresearch
# Station (GPU):
#   docker build --build-arg PROFILE=station --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu130 \
#     --tag nightshift/autoresearch:station --file experiments/autoresearch/image/Dockerfile experiments/autoresearch/image
ARG BASE_IMAGE=ghcr.io/nvidia/openshell-community/sandboxes/base@sha256:aeef1c63f00e2913ea002ccb3aaf925f338b5c5d70e63576f0d95c16a138044e
FROM ${BASE_IMAGE}

ARG PROFILE=laptop
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
ARG TORCH_VERSION=2.14.0

# Python packages go into the base image's venv, owned by the sandbox user.
USER sandbox
RUN uv pip install --python /sandbox/.venv/bin/python \
      --index-url "${TORCH_INDEX}" --extra-index-url https://pypi.org/simple --index-strategy unsafe-best-match \
      "torch==${TORCH_VERSION}" "rustbpe>=0.1.0" "tiktoken>=0.11.0" "pyarrow>=21.0.0" "requests>=2.32.0" "numpy>=2.2.0"

# Fixed code and data: root-owned and world-readable, so the agent (user
# `sandbox`, with /opt read-only in the sandbox policy) can import but not alter them.
USER root
COPY prepare.py score.py /opt/autoresearch/
ENV AUTORESEARCH_PROFILE=${PROFILE} \
    AUTORESEARCH_CACHE=/opt/autoresearch/cache \
    PYTHONPATH=/opt/autoresearch
RUN /sandbox/.venv/bin/python /opt/autoresearch/prepare.py \
    && chmod -R a+rX /opt/autoresearch

LABEL org.opencontainers.image.title="Nightshift autoresearch sandbox" \
      org.opencontainers.image.description="OpenShell base image with PyTorch and the fixed autoresearch data, tokenizer, evaluator, and scorer (profile ${PROFILE})."

USER sandbox
WORKDIR /sandbox
