FROM node:24-bookworm-slim@sha256:3638d9a6fe4030bd716be989438248074489337ba3275657f93595428be4fc03

ARG PI_VERSION=0.82.1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git iproute2 python3 ripgrep \
    && rm -rf /var/lib/apt/lists/*

RUN npm install --global --ignore-scripts "@earendil-works/pi-coding-agent@${PI_VERSION}" \
    && npm cache clean --force >/dev/null 2>&1 \
    && test "$(pi --version)" = "${PI_VERSION}"

RUN mkdir -p /opt/oar/pi /sandbox/artifacts /sandbox/tmp /workspace \
    && chown -R node:node /sandbox /workspace

COPY exec.sh /opt/oar/pi/exec.sh
RUN chmod 0755 /opt/oar/pi/exec.sh \
    && chmod -R a+rX,a-w /opt/oar

WORKDIR /sandbox
USER node
