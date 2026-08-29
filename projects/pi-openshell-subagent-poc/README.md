# Pi subagents in OpenShell sandboxes

This POC demonstrates one behavior:

> A Pi agent running in an OpenShell parent sandbox delegates a task through
> `pi-subagents`; the worker runs as a second Pi process in a newly created,
> policy-scoped OpenShell sandbox and returns its final answer to the parent.

The parent authors the child policy. A host-side OpenShell Tool Service checks
that policy against the live parent policy, creates the child, runs Pi, returns
the result, and deletes the child. When a network rule exceeds the parent
policy, OpenShell Policy Advisor provides the human review and approval flow.

The mock LLM policy reviewer demonstrates the intended gating experience. It is
not a formal policy-subset proof or a production security boundary.

## Tested configuration

This runbook was validated with:

| Component | Tested value |
| --- | --- |
| OpenShell CLI and gateway | `0.0.116` |
| Parent Pi | `0.81.0` |
| `pi-subagents` | `0.58.0` |
| Node.js in the Pi image | `22.22.1` |
| Host Python | `3.12.13` |
| Compute | Local Docker driver |
| Workspace | `default` |
| Provider | `nv-inference` |
| Model | `azure/openai/gpt-5.6-sol` |

The default policy uses `host.openshell.internal` to reach the Tool Service.
The runbook therefore assumes that the Tool Service and the local Docker-backed
OpenShell gateway run on the same machine.

## Quick start

Run all commands from this project directory unless a section says otherwise:

```bash
cd projects/pi-openshell-subagent-poc
```

### 1. Check the prerequisites

You need:

- An authenticated OpenShell CLI connected to a running gateway.
- OpenShell `0.0.116` or newer.
- Docker available to the gateway's local compute driver.
- `uv` for the host-side Tool Service.
- An NVIDIA Inference Hub key that can call
  `azure/openai/gpt-5.6-sol` through the Responses API.

Check OpenShell:

```bash
openshell --version
openshell status
docker info
```

### 2. Create the local configuration

On the first run:

```bash
cp .env.example .env
openssl rand -hex 32
```

Copy the generated token and the NVIDIA key into `.env`:

```dotenv
OPENSHELL_TOOL_SERVICE_TOKEN=<generated-token>
NVIDIA_API_KEY=<nvidia-inference-hub-key>
```

Do not commit `.env`. Load it before running any remaining setup commands:

```bash
set -a
source .env
set +a
```

### 3. Configure inference

Create the tested provider and route:

```bash
export OPENAI_API_KEY="$NVIDIA_API_KEY"

openshell provider create \
  --workspace default \
  --name nv-inference \
  --type openai \
  --credential OPENAI_API_KEY \
  --config OPENAI_BASE_URL=https://inference-api.nvidia.com/v1

unset OPENAI_API_KEY

openshell inference set \
  --workspace default \
  --provider nv-inference \
  --model azure/openai/gpt-5.6-sol \
  --timeout 300
```

If `nv-inference` already exists and is correct, skip the create command. Verify
the provider and route:

```bash
openshell provider list
openshell inference get --workspace default
```

The API key remains in OpenShell's provider store and the host-side `.env`; it
is not passed into either sandbox. Both agents use the managed inference route
at `https://inference.local/v1`.

### 4. Enable Policy Advisor

Enable agent policy proposals globally and keep human approval mandatory:

```bash
openshell settings set --global \
  --workspace default \
  --key agent_policy_proposals_enabled \
  --value true \
  --yes

openshell settings set --global \
  --workspace default \
  --key proposal_approval_mode \
  --value manual \
  --yes
```

### 5. Terminal 1: open the operator view

```bash
openshell term
```

Keep this terminal open. Use it to:

- Watch `pi-parent` and `pi-child-*` sandboxes appear and change state.
- Inspect sandbox status and logs.
- Review and approve or reject Policy Advisor requests.
- Confirm that completed child sandboxes are removed.

### 6. Terminal 2: start the OpenShell Tool Service

Open a second terminal:

```bash
cd projects/pi-openshell-subagent-poc

set -a
source .env
set +a

uv sync --locked
uv run openshell-tool-service
```

Keep this terminal open. The service listens on host port `8765`, stores local
job state in `.state/jobs.sqlite3`, and uses the authenticated host OpenShell
CLI to operate on the same gateway as the parent sandbox.

Optionally verify it from another host terminal:

```bash
curl http://127.0.0.1:8765/healthz
```

Expected response:

```json
{"status":"ok"}
```

### 7. Terminal 3: create and start the parent Pi agent

Open a third terminal and load the same configuration:

```bash
cd projects/pi-openshell-subagent-poc

set -a
source .env
set +a
```

If `pi-parent` already exists, delete it so the sandbox is rebuilt with the
current image, extension, skills, and policy:

```bash
openshell sandbox delete --workspace default pi-parent
```

Ignore a not-found error on the first run. Create the parent:

```bash
openshell sandbox create \
  --workspace default \
  --name pi-parent \
  --from Dockerfile.parent \
  --policy policies/parent-smoke.yaml \
  --provider nv-inference \
  --env POC_TOOL_SERVICE_URL=http://host.openshell.internal:8765 \
  --env POC_TOOL_SERVICE_TOKEN="$OPENSHELL_TOOL_SERVICE_TOKEN" \
  --env POC_CALLER_SANDBOX_NAME=pi-parent \
  --env NODE_OPTIONS=--disable-warning=UNDICI-EHPA \
  --env PI_OFFLINE=1 \
  --env PI_SKIP_VERSION_CHECK=1 \
  --env PI_TELEMETRY=0 \
  --env PI_CODING_AGENT_DIR=/home/sandbox/.pi/agent \
  --no-credential-warnings \
  --tty \
  --detach
```

Wait until `pi-parent` is `Ready` in `openshell term`, then start Pi:

```bash
openshell sandbox exec \
  --workspace default \
  --name pi-parent \
  --tty \
  --timeout 0 \
  -- pi \
    --provider openshell-inference \
    --model azure/openai/gpt-5.6-sol \
    --append-system-prompt /opt/pi-openshell-poc/parent-system-prompt.md
```

## Test 1: create one child sandbox

This is the minimum successful POC. Enter this at the Pi prompt:

```text
Use an openshell-worker subagent to complete this task in a dedicated OpenShell
sandbox. Run hostname, wait 30 seconds, and then reply with
OPEN_SHELL_CHILD_OK followed by the hostname.
```

Watch `openshell term` and the Tool Service terminal. Verify that:

1. The Tool Service accepts the job and runs the policy review.
2. A `pi-child-*` sandbox appears and reaches `Ready`.
3. Pi runs inside the child.
4. The parent receives `OPEN_SHELL_CHILD_OK` and the child hostname.
5. The child sandbox is deleted after completion.

The Tool Service log shows the complete job request, the parent policy lookup,
the review decision, child creation, Pi execution, and cleanup.

## Test 2: require human policy approval

Enter this at the same parent Pi prompt:

```text
Use one openshell-worker subagent to fetch https://example.com/ and return the
HTTP status and page title. If child creation is denied because the child policy
exceeds the parent policy, use OpenShell Policy Advisor to propose only the
missing network rule, wait for human approval, and then launch a new worker for
the same task.
```

Verify that:

1. The Tool Service rejects the initial child policy and creates no child.
2. A pending network rule appears in `openshell term` for `pi-parent`.
3. The operator inspects the host, port, binary, method, path, and prover result.
4. The operator approves the proposal in `openshell term`.
5. The parent observes `policy_reloaded: true` and launches a new worker job.
6. The Tool Service reads the updated parent policy and allows the new child.
7. The child returns the HTTP status and page title and is then deleted.

To test rejection, reject the pending rule in `openshell term`. The parent
should report the human guidance and create no child.

These CLI commands are available as troubleshooting alternatives:

```bash
openshell rule get pi-parent --workspace default --status pending

openshell rule approve pi-parent \
  --workspace default \
  --chunk-id <chunk-id>

openshell rule reject pi-parent \
  --workspace default \
  --chunk-id <chunk-id> \
  --reason "Narrow this rule before retrying."
```

Policy Advisor currently covers network `addRule` proposals. Filesystem,
process, provider attachment, credential binding, and unsupported advanced
network changes still require a separate manual policy update.

## Optional: validate a repository-scoped worker

Enter this at the parent Pi prompt:

```text
Use one openshell-worker subagent to clone and review
https://github.com/nicobailon/pi-subagents. Return the child's concise summary.
```

The parent should author a repository-scoped read policy. The child should clone
only that repository, return a concise review, and be deleted after completion.

## Cleanup

Exit Pi, then remove the retained parent sandbox:

```bash
openshell sandbox delete --workspace default pi-parent
```

Stop the Tool Service with `Ctrl-C`. Its SQLite file is retained so previous job
records remain available. To intentionally clear only that local POC history:

```bash
rm -f .state/jobs.sqlite3
```

## Troubleshooting

### `pi-parent` enters `Error`

Inspect it in `openshell term`, then delete and recreate it with the complete
command from the quick start:

```bash
openshell sandbox get pi-parent --workspace default
openshell sandbox delete --workspace default pi-parent
```

Recreation is also required after changing `Dockerfile.parent`, the Pi package,
the installed skills, the parent system prompt, or the parent policy.

### The Tool Service is unreachable

Confirm that it is running and listening on the configured port:

```bash
curl http://127.0.0.1:8765/healthz
```

The URL, port, and token must match across `.env`, `policies/parent-smoke.yaml`,
and the parent create command. The Tool Service must use an authenticated
OpenShell CLI connected to the same gateway as the sandbox.

### NVIDIA Inference Hub returns `401`

Reload `.env` and test the exact Responses endpoint used by this POC:

```bash
set -a
source .env
set +a

curl -sS -o /tmp/nvidia-response.json -w "HTTP %{http_code}\n" \
  https://inference-api.nvidia.com/v1/responses \
  -H "Authorization: Bearer $NVIDIA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "azure/openai/gpt-5.6-sol",
    "input": "Reply with exactly: OK"
  }'
```

An expired, copied incorrectly, or differently entitled key must be replaced in
`.env` before restarting the Tool Service.

### The Tool Service token is missing or rejected

Load the same `.env` before starting both the service and creating `pi-parent`.
If the token changes, restart the service and recreate the parent; sandbox
environment variables do not change after creation.

### Child creation is denied

Check the Tool Service output for `policy-review-denied`. No child should exist
at this point. If it also reports `POLICY_ADVISOR_ACTION_REQUIRED`, inspect the
pending proposal in `openshell term`. Unsupported non-network authority needs a
manual parent-policy update.

### A policy proposal remains pending

Policy approval mode is deliberately `manual`. Approve or reject the request in
`openshell term`, or use the CLI alternatives in Test 2. Pi waits for that human
decision.

### The `UNDICI-EHPA` warning appears

The warning comes from Node.js using OpenShell's environment-aware HTTP proxy.
It is harmless. The create command suppresses only that warning with:

```text
NODE_OPTIONS=--disable-warning=UNDICI-EHPA
```

Recreate an older parent sandbox to apply this environment variable.

### The provider already exists

Do not recreate a correctly configured provider. Verify the existing provider
and inference route:

```bash
openshell provider list
openshell inference get --workspace default
```

If a different provider or model is required, update all matching values in
`.env`, `config/models.json`, and the parent create and Pi launch commands.

## Architecture and ownership

```text
Parent OpenShell sandbox
  Pi + pi-subagents + local external-job extension
          |
          | POST /v1/jobs, then poll status/result
          v
OpenShell Tool Service on the trusted host
  SQLite job state + mock LLM policy reviewer
          |
          | openshell policy get
          | openshell sandbox create / exec / delete
          v
Child OpenShell sandbox
  Pi -p <delegated task>  --->  final text
```

### Pi and `pi-subagents`

The `openshell-worker` definition uses the `external-job` runner supplied by
`pi-subagents`. The local extension in `pi-package/` translates start, status,
result, and reattach operations into Tool Service HTTP requests.

The package also installs two parent skills:

- `openshell-workers` tells Pi when and how to delegate.
- `generate-sandbox-policy` tells Pi how to author a complete child policy.

`parent-system-prompt.md` requires delegation through `openshell-worker`, so Pi
does not silently use an in-process worker or do the delegated work itself.

### Parent-authored child policy

The parent places raw YAML between `<openshell-policy>` and
`</openshell-policy>` in each worker task. The extension extracts the policy,
removes it and Pi Subagents' system wrapper from the child prompt, and sends:

```json
{
  "idempotencyKey": "...",
  "caller": {"sandboxName": "pi-parent"},
  "prompt": "clean delegated task",
  "resources": {"childPolicy": "version: 1\n..."}
}
```

The Tool Service fixes the child image, provider, workspace, command, model
configuration, timeouts, and cleanup behavior. A sandbox request cannot replace
those trusted values.

### Policy review and Policy Advisor

The Tool Service reads the live parent policy from OpenShell and asks the
configured model whether the child policy adds authority. It continues only on
an explicit, schema-valid `allow`; deny, timeout, provider failure, malformed
output, or uncertainty fails closed.

The mock review is replaceable through the `PolicyReviewer` interface. A model
can still produce a false allow, so this does not prove attenuation.

On a denied network increase, the parent uses OpenShell Policy Advisor. The
Tool Service never approves proposals and does not need OpenShell
`config:write` authority. After human approval and policy reload, the parent
submits a new worker job rather than reattaching to the failed job.

### Child lifecycle

For every allowed job, the Tool Service:

1. Writes the submitted child policy to a temporary local file.
2. Creates a detached `pi-child-<job-id-prefix>` sandbox.
3. Attaches the configured inference provider and model configuration.
4. Runs Pi once with the delegated task.
5. Captures Pi's final output.
6. Deletes the child and temporary policy.
7. Returns the output through `pi-subagents` to the parent.

## Local development checks

These checks use fake OpenShell and policy-review implementations; they do not
create real sandboxes or call a model:

```bash
uv sync --locked
uv run ruff check src tests
uv run pytest -q
npm --prefix pi-package ci
npm --prefix pi-package test
npm --prefix pi-package run typecheck
```

## Known limitations

- `caller.sandboxName` is self-reported and is not bound to the Tool Service
  bearer token.
- The shared Tool Service token is a POC credential, not sandbox workload
  identity.
- The mock LLM reviewer is not a formal parent-child policy-subset prover.
- Review and sandbox creation are separate operations rather than one atomic
  authorization transaction.
- Policy Advisor proposals cover supported network rules, not every OpenShell
  policy or provider field.
- Child inference provider attachment is fixed by trusted Tool Service
  configuration; credentials are not delegated from the parent.
- Workers are one-shot and are deleted after their final response.
- Sandbox-to-sandbox messaging, follow-up sessions, artifact transfer, private
  repositories, and GitHub writes are out of scope.

## Project layout

```text
Dockerfile.parent              Derived parent Pi image
.env.example                   Host-side Tool Service configuration
config/models.json             Child Pi model configuration
policies/parent-smoke.yaml     Parent sandbox policy
pi-package/                    Pi extension, worker definition, skills, prompt
src/openshell_tool_service/    Host-side job API and OpenShell CLI runtime
tests/                         Python service and runtime tests
```
