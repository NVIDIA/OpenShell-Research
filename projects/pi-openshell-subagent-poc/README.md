# Pi subagents in OpenShell sandboxes

This POC demonstrates one behavior:

> A Pi agent running in an OpenShell parent sandbox delegates a task through
> `pi-subagents`; the worker runs as a second Pi process in a newly created,
> policy-scoped OpenShell sandbox and returns its final answer to the parent.

The POC also gives that parent and its children a small shared message store.
The parent can message any child, and active children can message their parent
or active siblings created by that same parent. Children of different parents
cannot address each other.

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

# Fail here instead of silently creating a parent with an empty service token.
: "${OPENSHELL_TOOL_SERVICE_TOKEN:?OPENSHELL_TOOL_SERVICE_TOKEN is not loaded from .env}"
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
job, collaboration, and captured child-log state in `.state/jobs.sqlite3`, and
uses the authenticated host OpenShell CLI to operate on the same gateway as the
parent sandbox.

Optionally verify it from another host terminal:

```bash
curl http://127.0.0.1:8765/healthz
```

Expected response:

```json
{"status":"ok"}
```

### 7. Terminal 3: watch the collaboration timeline

Open a third terminal and start the read-only timeline monitor:

```bash
cd projects/pi-openshell-subagent-poc

set -a
source .env
set +a

uv run openshell-collaboration-watch --parent pi-parent
```

Keep this terminal open during the collaboration test. Its default view prints
the useful operator timeline: worker reservation, policy review, sandbox ready,
messages, failures, and completion. It hides internal bookkeeping such as
delivery acknowledgements and full LLM review explanations. Add `--verbose` to
show every stored event and the full review reasons. The monitor does not
authenticate as an agent or modify the database.

Use `--history` to print retained events before following new ones,
`--snapshot` to print retained events and exit, `--metadata-only` to hide
message bodies, or `--verbose` for the diagnostic view.

For a visual near-real-time view, open:

```text
http://127.0.0.1:8765/watch?parent=pi-parent
```

Paste `OPENSHELL_TOOL_SERVICE_TOKEN` into the page and select **Connect**. The
token is kept only in that browser tab. The page refreshes every 500 ms and
automatically follows the newest run. Its default network-flow view is a shared
time-axis waterfall with one lane per worker and separate tracks for control
plane work, Pi execution, inference, and messages. It highlights the critical
worker, slowest measured operation, failures, running spans, and message points.
Runs above 16 workers collapse into expandable groups of 16. OpenShell
`API:INFERENCE` timings appear after a child finishes, when its captured sandbox
logs become available. A message's Tool Service to recipient time is measured
from storage to acknowledgement; sender to Tool Service is shown as unmeasured
because the POC sees only the arrival timestamp. Expand **Raw events** for the
underlying journal, switch to conversation or activity views when useful, or
select a bar or point to inspect its timing source and details. The browser API
is read-only and requires the Tool Service bearer token; the HTML shell itself
contains no credentials.

### 8. Terminal 4: create and start the parent Pi agent

Open a fourth terminal and load the same configuration:

```bash
cd projects/pi-openshell-subagent-poc

set -a
source .env
set +a

: "${OPENSHELL_TOOL_SERVICE_TOKEN:?OPENSHELL_TOOL_SERVICE_TOKEN is not loaded from .env}"
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

At the default `INFO` level, the Tool Service log shows a compact job summary,
review timing, child creation, Pi execution, and cleanup. Set
`OPENSHELL_TOOL_SERVICE_LOG_LEVEL=DEBUG` to inspect the complete request body,
parent-policy lookup, and full review explanation.

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

## Test 3: parent and sibling communication

Enter this at the parent Pi prompt:

```text
Launch two openshell-worker subagents in parallel with stable roles `worker-a`
and `worker-b`. Worker A must send `worker-b` the message "HELLO_FROM_A", wait
for its reply, then send the parent a result containing both messages. Worker B
must wait for Worker A's message, reply "HELLO_FROM_B", and send the parent a
progress message. Use blocking collaboration_wait only inside a worker that
must remain alive while waiting. The parent's incoming messages should arrive
automatically. Return the complete message transcript.
```

Verify that:

1. Two `pi-child-*` sandboxes are visible at the same time in `openshell term`.
2. The timeline reserves `worker-a` and `worker-b`, prepares both sandboxes, and
   starts both Pi processes only after the all-ready barrier opens.
3. Worker B receives `HELLO_FROM_A` and Worker A receives `HELLO_FROM_B`.
4. The parent receives messages from both children.
5. The timeline shows reservation, startup, messages, and completion in order.
6. Both child sandboxes are deleted and their participant tokens stop working.

The Pi extension starts an automatic mailbox for the parent Pi session. It
long-polls the Tool Service and acknowledges every delivery. Ordinary messages,
progress, and results remain visible in the collaboration watcher but are not
injected into the Pi chat; final worker answers return through Pi Subagents.
Questions and messages with an `actionRequired: true` envelope payload are
injected with `triggerTurn: true`. The parent model does not subscribe or manage
sequence numbers. One-shot children do not
start an automatic mailbox. When a child must stay alive for a message or reply,
it uses `collaboration_wait` as its only receiver; a message queued before the
wait remains in SQLite and is returned immediately.

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
  Pi + pi-subagents + local extension
    | POST /v1/jobs; batch status polls; fetch result
    | send messages; automatic persisted mailbox
    v
OpenShell Tool Service on the trusted host
  SQLite: jobs + role roster + mailboxes + messages + timeline + child logs
    | read-only terminal lifecycle/message timeline
    | openshell policy get
    | openshell sandbox create / exec / delete
    v
Child OpenShell sandboxes
  Pi -p <task> + collaboration extension
    | send messages; explicit collaboration_wait receiver
    +---- parent or sibling role in the same workflow
```

### Pi and `pi-subagents`

The `openshell-worker` definition uses the `external-job` runner supplied by
`pi-subagents`. The local extension in `pi-package/` translates start, status,
result, and reattach operations into Tool Service HTTP requests.

Pi Subagents 0.58 infers an acceptance appendix even though external-job
runners do not implement native acceptance contracts. The adapter removes that
harness-generated appendix before launching child Pi so coordination-only tasks
do not waste a model turn producing file-and-test evidence.

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
  "workflow": {
    "id": "pi-workflow-run-id",
    "startMode": "immediate"
  },
  "worker": {
    "stepIndex": 0,
    "role": "reviewer-1",
    "prompt": "clean delegated task",
    "resources": {"childPolicy": "version: 1\n..."}
  }
}
```

Workers that must communicate use `startMode: "all-ready"` and include the
exact `expectedWorkers` count. The parent expresses this using an
`<openshell-coordination>` block in every worker task. The adapter validates
and removes that block before the child sees its prompt. Independent workers
need no coordination block and default to `immediate`.

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
4. Starts Pi immediately for an independent worker, or holds the Ready sandbox
   at the explicit `all-ready` workflow barrier.
5. Opens an `all-ready` barrier only when the declared number of workers exists
   and every one is Ready, then starts Pi in all of them.
6. Captures each Pi process's final output and a best-effort OpenShell log tail.
7. Deletes each child and its temporary policy after that child finishes.
8. Returns the output through `pi-subagents` to the parent while retaining the
   log snapshot in SQLite.

### Shared collaboration memory

Pi gives each external job its own child run ID, while its local run record also
contains the shared `parentWorkflowRunId` for entries launched by one
`runs.all` workflow. The current external-job provider callback does not expose
that parent field, so this POC's adapter reads it from Pi's local `status.json`
record. The Tool Service uses the resolved parent workflow ID for both lifecycle
coordination and messaging; there is no separate batch identifier. A coordinated
launch fails immediately if that shared identity cannot be resolved, instead of
leaving isolated workers waiting at separate barriers. The parent assigns each
worker a stable role in an `<openshell-role>` block. The Pi adapter sends that
role with the job request, and the Tool Service reserves it before policy review
so messages can be queued before the sandbox starts. Role names must be unique
inside the active workflow and can be reused after it closes. The Tool Service
gives an allowed child a random participant token when creating its sandbox and
stores only the token's SHA-256 hash. The child receives the standalone Pi
collaboration extension with its model configuration.

The parent and child Pi processes receive three explicit tools:

- `collaboration_list_participants` lists active participants by default and can
  include finished children for debugging. Entries include stable role names.
- `collaboration_send` appends a direct message to the group log.
- `collaboration_wait` is the receive path for a one-shot child that must remain
  alive until another agent sends a message or replies. It reads the child's
  persisted mailbox and acknowledges returned deliveries. A worker waiting on
  a specific sibling passes that sibling's stable role as `sender`. If the
  sibling fails, or finishes without leaving a message, the wait returns a
  terminal error immediately instead of polling until the task timeout.

Parent message receipt is automatic rather than a model-facing tool. The parent
extension continuously reads its persisted mailbox and acknowledges each
delivery by ID. Informational messages stay out of the Pi chat and remain
visible in the collaboration watcher. A question, or another message whose
envelope payload sets `actionRequired: true`, is injected into Pi and triggers a
new turn before acknowledgement. Children use the model-facing
`collaboration_wait` tool instead, which
prevents an automatic mailbox and a blocking wait from competing for the same
delivery. A message that was not individually acknowledged is delivered again
after a transient failure; acknowledging one message cannot accidentally skip
another message.

Every stored message has a versioned envelope containing a type, correlation
ID, machine-readable payload, and optional human-readable text. The simple Pi
tool still accepts a normal message body and supplies sensible defaults.

The HTTP API derives the sender from authentication and resolves recipients
only inside the sender's workflow. SQLite foreign keys also bind both message
participants to that workflow. Messages are immutable and persist after a
child finishes; the finished child's token is disabled. Queued messages for a
finished child become `undeliverable`. The Tool Service does not manufacture
failure messages. A worker explicitly waiting on a failed role instead gets a
terminal error directly from `collaboration_wait`. A run closes after every child is
terminal and the parent's mailbox has been drained. A one-hour maximum TTL
closes abandoned state. The collaboration store does not impose a separate
child-count limit and limits a run to 8,192 messages of 64 KiB each. That is
enough for all 4,032 directed worker-to-worker pairs in a 64-worker workflow,
plus parent messages and retry headroom.

The parent chooses one of two start modes. `immediate` workers start Pi as soon
as their own sandbox is Ready; they do not wait for unrelated siblings.
`all-ready` workers remain idle until the exact declared worker count has been
registered and every sandbox is Ready. Mismatched workflow contracts are
rejected instead of guessed from a quiet period. If one coordinated worker
fails before release, every prepared peer is failed and cleaned up. A workflow
that never reaches its barrier expires after the configured readiness timeout.

`POC_CREATE_CONCURRENCY=8` bounds policy-review and sandbox-create pressure.
`POC_MAX_ACTIVE_WORKERS=64` bounds all admitted non-terminal workers and the Pi
execution pool. `POC_WORKFLOW_READY_TIMEOUT_SECONDS=300` bounds how long a
workflow may wait for readiness. Excess submissions receive HTTP `429` with
`Retry-After: 5`. A 64-worker workflow still depends on the gateway and host
having enough CPU, memory, file descriptors, and inference capacity.

The Pi adapter coalesces status checks made in the same 25 ms window into one
`POST /v1/jobs/status` call. The Tool Service also reuses one HTTP/2 connection
pool for policy review, briefly caches the live parent policy, and performs only
one LLM attenuation review for identical parent/child policy pairs. These keep a
large fan-out from multiplying file descriptors and identical external calls.
Job creation uses a 30-second response timeout. If the response is lost or
times out, the adapter retries up to three times with 250 ms and 500 ms backoff,
reusing the identical request body and idempotency key. The Tool Service then
returns the job created by the original request instead of launching a duplicate.
Definitive HTTP errors such as admission rejection are not retried.

Sandbox upload and initial SSH transport failures that match known transient
OpenShell diagnostics are retried up to three times with bounded exponential
backoff. Before retrying a failed create/upload, the service deletes the named
partial sandbox. It also performs an idempotent delete after every create
attempt—even when `openshell sandbox create` itself returned an error—so a
failed upload cannot silently leave an orphan behind.

On restart, workers that had reached sandbox preparation or execution are
failed closed because child Pi is one-shot; the service attempts to delete
their named sandboxes. Workers that had not started preparation may resume.
Startup-only recovery failures remain visible in job status and the operator
timeline without generating synthetic mailbox messages.

Set `POC_COLLABORATION_RUN_TTL_SECONDS` to change the collaboration-state TTL.

The service allows two seconds for active HTTP requests, including mailbox long
polls, to finish after `Ctrl-C`. Set `POC_GRACEFUL_SHUTDOWN_SECONDS` to change
that grace period. An actively executing child job may still take longer while
the Tool Service waits for its one-shot execution and sandbox cleanup.

The terminal watcher's default view is the concise operator journal. It shows
the shared Pi session correlation ID, role reservation, policy review, sandbox
readiness, messages, failures, worker completion, and group closure. Its
`--verbose` view also shows internal job, creation, cleanup, event ID, delivery
acknowledgement, and full policy-review details.

This is shared communication memory, not a shared filesystem. Agents exchange
text, progress, questions, and results through the Tool Service; they never
open the SQLite database directly.

### Retained child logs

OpenShell removes its in-memory sandbox log buffer when a sandbox is deleted.
Before cleanup, the Tool Service captures the child's available OpenShell log
tail and stores it with the job:

```bash
curl -H "Authorization: Bearer $OPENSHELL_TOOL_SERVICE_TOKEN" \
  http://127.0.0.1:8765/v1/jobs/<job-id>/logs
```

This is a best-effort debugging snapshot, not an audit log. A capture failure
is returned separately as `captureError` and does not fail an otherwise
successful worker job.

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

### Mock concurrency benchmark

Measure one explicit worker wave through the Tool Service's SQLite state,
all-ready barrier, and thread pools without creating OpenShell sandboxes or
calling a model:

```bash
uv run python scripts/benchmark_concurrency.py
```

The benchmark sweeps worker counts `1,2,4,8,16,32,64`. A one-worker test starts
immediately; larger waves use the same explicit all-ready contract as a real
coordinated workflow. It prints peak concurrency, throughput, submit and status
p95 latency, errors, and a recommended mock ceiling. Customize the sweep when
needed:

```bash
uv run python scripts/benchmark_concurrency.py \
  --levels 8,16,32,64 \
  --job-duration-ms 100 \
  --api-p95-limit-ms 500
```

This recommendation covers only the host-side Tool Service. Run a smaller,
staged test with real children to measure OpenShell gateway, provider, and
inference capacity.

## Known limitations

- Pi Subagents' external-job start input does not currently expose
  `parentWorkflowRunId`; the POC adapter reads Pi's local `status.json` artifact
  as a compatibility shim. A first-class field in the upstream adapter contract
  would remove this coupling.
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
- The run TTL is checked when the Tool Service handles collaboration activity;
  it is a state-safety timeout, not a background process supervisor.
- Collaboration storage and mailboxes are asynchronous and durable, but parent
  delivery uses a background long poll rather than server push. Automatic
  parent delivery still requires the Pi process to remain active. A one-shot
  child waiting for a reply must keep itself alive with `collaboration_wait`.
- The parent uses the shared POC service token plus its self-reported sandbox
  name; this is not production workload identity.
- Collaboration carries text messages only. Follow-up sessions, artifact
  transfer, private repositories, and credential delegation remain out of
  scope.

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
