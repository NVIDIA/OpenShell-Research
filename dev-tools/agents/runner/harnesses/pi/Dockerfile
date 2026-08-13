FROM node:24-bookworm-slim@sha256:3638d9a6fe4030bd716be989438248074489337ba3275657f93595428be4fc03

ARG PI_VERSION=0.82.1

RUN npm install --global --ignore-scripts "@earendil-works/pi-coding-agent@${PI_VERSION}" \
    && npm cache clean --force >/dev/null 2>&1 \
    && test "$(pi --version)" = "${PI_VERSION}"

RUN mkdir -p /etc/openshell/agent-payload /sandbox \
    && chown node:node /sandbox

COPY policy.yaml /etc/openshell/policy.yaml
COPY exec.sh /etc/openshell/agent-payload/runtime/exec.sh
COPY payload/ /etc/openshell/agent-payload/

RUN chmod 0755 /etc/openshell/agent-payload/runtime/exec.sh \
    && chmod -R a+rX,a-w /etc/openshell

WORKDIR /sandbox

USER node
