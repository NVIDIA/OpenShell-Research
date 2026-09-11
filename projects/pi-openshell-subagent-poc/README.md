# Pi subagent delegation through OpenShell

This POC demonstrates one behavior:

> A Pi agent running in an OpenShell parent sandbox delegates a task through
> `pi-subagents`; the worker runs in a newly created, policy-scoped OpenShell
> child sandbox and returns its final answer to the parent.

There is deliberately no parent/child or child/child messaging layer here. The
child is one-shot: create sandbox, run Pi, return the result, delete sandbox.

The parent authors the child policy. A host-side Tool Service retrieves the
live parent policy, runs an LLM-based permission review, creates the child with the
approved policy, executes Pi, captures its result, and deletes the child.

The model-based policy review demonstrates the desired gating flow; it is not a
formal subset proof. See [Architecture details](architecture_details.md) for
the complete request and trust-boundary explanation.

## Components

| Component | Location | Responsibility |
| --- | --- | --- |
| Parent Pi | `pi-parent` OpenShell sandbox | Decides to delegate and authors the child policy |
| `pi-subagents` adapter | Parent image | Maps external-job start/status/result calls to HTTP |
| Tool Service | Host terminal | Reviews policy and runs OpenShell CLI lifecycle commands |
| Child Pi | New `pi-child-*` sandbox | Executes the delegated task and returns one final answer |
| OpenShell | Gateway and sandbox runtime | Creates sandboxes and enforces their policies |

Pi and the published `pi-subagents` library are not modified. The parent image
installs a local extension (`pi-package/index.ts`), a worker definition, and
policy-authoring skills. The startup command appends routing instructions that
tell the parent to select `openshell-worker`. Selection is model guidance;
the selected worker's external-job configuration routes execution through the
Tool Service. See [the request flow](architecture_details.md#request-flow).

## Prerequisites

- OpenShell CLI authenticated to a local running gateway. The commands below
  target the 0.0.116 CLI interface; newer interfaces have not been validated here.
- Docker available to the gateway's local compute driver.
- Python 3.11+ and `uv` 0.8+ on the host. Node.js 22.6+ and npm are needed only
  for host-side adapter tests; the parent image installs its own Pi runtime.
- An NVIDIA Inference Hub credential allowed to call
  `azure/openai/gpt-5.6-sol`.

From the repository root, enter this project directory. In each new terminal,
repeat this step from the repository root; do not nest this path inside itself:

```bash
cd projects/pi-openshell-subagent-poc
```

Verify the local services:

```bash
openshell --version
openshell status
docker info
```

## 1. Configure the POC

Create a local environment file and a random Tool Service token:

```bash
test -f .env || cp .env.example .env
openssl rand -hex 32
```

Put the generated token and your NVIDIA key in `.env`:

```dotenv
OPENSHELL_TOOL_SERVICE_TOKEN=<generated-token>
NVIDIA_API_KEY=<nvidia-inference-hub-key>
```

Load it in every host terminal used below:

```bash
set -a
source .env
set +a
```

Never commit `.env`. The service reads exported environment variables; it does
not automatically load `.env`. Restart it after changing the configuration.
`OPENSHELL_GATEWAY` and `OPENSHELL_WORKSPACE` must identify the same gateway and
workspace used to create the parent.

The shared service token is deliberately passed into the parent for this local
POC. It does not verify the caller's claimed sandbox name. Run the service only
on a trusted machine/network: its default HTTP listener binds to all interfaces,
and the token grants access to all job endpoints.

## 2. Configure inference

Create the OpenShell provider once:

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

If the provider already exists, do not recreate it. Verify the route:

```bash
openshell provider list
openshell inference get --workspace default
```

## 3. Enable Policy Advisor

This lets the parent propose a narrow missing network rule for human approval
when a required child policy exceeds the parent policy:

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

## 4. Start the operator view

In Terminal 1:

```bash
openshell term
```

Keep it open. It shows the parent and child sandboxes, logs, and any pending
Policy Advisor request.

## 5. Start the Tool Service

In Terminal 2:

```bash
cd projects/pi-openshell-subagent-poc
set -a
source .env
set +a

uv sync --locked
uv run openshell-tool-service
```

Leave this terminal running. In Terminal 3, verify the service before creating
the parent. The Tool Service listens on host port `8765` and uses the authenticated host
OpenShell CLI, so it operates on the same gateway as the sandboxes. Verify it:

```bash
curl --fail --silent --show-error http://127.0.0.1:8765/healthz
```

Expected response:

```json
{"status":"ok"}
```

## 6. Create and start the parent

In Terminal 3, load `.env` and check for an existing parent:

```bash
cd projects/pi-openshell-subagent-poc
set -a
source .env
set +a

openshell sandbox list --workspace default
```

If `pi-parent` already exists, first save anything needed from it. Only replace
it if it belongs to this POC:

```bash
openshell sandbox delete --workspace default pi-parent
```

Then create the parent. Creating from `Dockerfile.parent` builds and installs
the Pi packages, so the first launch requires registry/network access and may
take longer than subsequent launches:

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

Wait until `pi-parent` is Ready, then start Pi:

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

## 7. Run the minimal validation

Enter this at the parent Pi prompt:

```text
Use one openshell-worker subagent to run hostname in a dedicated OpenShell
sandbox. Return exactly OPEN_SHELL_CHILD_OK followed by the hostname.
```

Verify:

1. Pi invokes the `openshell-worker` external-job agent.
2. The Tool Service accepts the job and reviews the parent-authored policy.
3. A new `pi-child-*` sandbox appears in `openshell term`.
4. The child reaches Ready and runs a separate Pi process.
5. The parent receives `OPEN_SHELL_CHILD_OK` and the child's hostname.
6. The Tool Service deletes the child after its final response.

At `INFO` level, the service logs the lifecycle without printing task or policy
contents. Use `OPENSHELL_TOOL_SERVICE_LOG_LEVEL=DEBUG` for local debugging.

## Optional: repository-scoped delegation

```text
Use one openshell-worker subagent to clone and review
https://github.com/nicobailon/pi-subagents. Return the child's concise review.
```

The parent should author a repository-scoped GitHub read policy. The worker
should clone and inspect the repository inside its child sandbox and then be
deleted. The supplied parent policy has no GitHub access, so this test also
requires the human approval path below. A denial before approval is expected.

## Optional: human approval path

Ask a worker to access a network endpoint not currently allowed by the parent.
The first job should fail before child creation with
`POLICY_ADVISOR_ACTION_REQUIRED`. The parent can then submit only the missing
network rule to Policy Advisor. After you approve it in `openshell term` and
the parent policy reloads, the parent launches a new worker job.

Policy Advisor currently covers supported network-rule additions. Other policy
changes require a manual parent-policy update.

## Cleanup

Exit Pi and delete the persistent parent:

```bash
openshell sandbox delete --workspace default pi-parent
```

Stop the Tool Service with `Ctrl-C` after jobs finish. Shutdown waits for accepted
jobs; `POC_GRACEFUL_SHUTDOWN_SECONDS` bounds Uvicorn HTTP shutdown, not the entire
worker lifecycle. Its job database remains at
`.state/jobs.sqlite3` for local debugging.

On restart, unfinished jobs are marked failed and their sandboxes are passed to
cleanup. Run only one service process per database. Reattachment retrieves an
existing job; it does not resume a child process after service restart.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| README link works locally but returns 404 on GitHub | The entire project directory must be committed and pushed alongside the repository README; untracked files are not shared. |
| Port 8765 is in use | Stop the previous POC service before starting this one. Do not run both POC variants against the same port/database. |
| Missing service configuration in Pi | Recreate the parent with all three `POC_TOOL_SERVICE_*` / `POC_CALLER_SANDBOX_NAME` variables from the create command. |
| Reviewer returns HTTP 401 | Check `NVIDIA_API_KEY` in `.env`, export it, and restart the service. The host reviewer needs its own valid credential even when sandbox inference works. |
| No child appears | Read the service log for parent-policy lookup or review denial. A healthy `/healthz` response only proves HTTP service availability. |
| Pi times out but a job is still running | The worker's 360-second harness timeout does not cancel the service job. Check the service log and `openshell term` before retrying. |
| Child remains after completion | Check the job result's `cleanupError` and delete only the named POC child after inspection. |

For authenticated diagnostics, substitute the job ID shown by the service:

```bash
curl --fail --silent --show-error \
  -H "Authorization: Bearer $OPENSHELL_TOOL_SERVICE_TOKEN" \
  http://127.0.0.1:8765/v1/jobs/JOB_ID/result
```

## Development checks

These tests use fake OpenShell and reviewer implementations. They do not create
real sandboxes or call a model:

```bash
uv sync --locked
uv run ruff check src tests
uv run pytest -q
npm --prefix pi-package ci
npm --prefix pi-package test
npm --prefix pi-package run typecheck
git diff --check
```

## Known limitations

- The Tool Service token and parent sandbox name are POC identity, not verified
  OpenShell workload identity.
- The LLM policy reviewer is not a formal parent-child subset prover.
- Policy review and sandbox creation are separate, non-atomic operations.
- The trusted Tool Service fixes the child image, provider, model, workspace,
  timeouts, and command; the parent cannot override them in a job request.
- Workers are one-shot and cannot receive follow-up prompts.
- There is no agent messaging or shared memory in this stripped-down version.
- `POC_MAX_WORKERS` limits whole worker jobs, not only sandbox creation. This is
  a small delegation demo, not a validated 64-agent concurrent execution system.
- The child image defaults to the moving community alias `pi`; pin
  `OPENSHELL_CHILD_IMAGE` to a verified image digest for a repeatable team run.
- A completed task can have a cleanup warning. Inspect `cleanupError` in status
  or result responses rather than assuming successful output guarantees deletion.
