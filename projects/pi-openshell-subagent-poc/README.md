# Generic Pi workers in OpenShell sandboxes

This POC demonstrates one reusable behavior:

> A Pi agent running in an OpenShell parent sandbox asks `pi-subagents` to run
> `openshell-worker`; each subagent runs as a second Pi process in a newly
> created OpenShell child sandbox and returns its final answer to the parent.

The worker accepts arbitrary task text. The parent Pi also authors the complete
OpenShell policy for each child and includes it in the worker task. The Tool
Service runs a fail-closed mock LLM attenuation review before applying that
policy to a new child. Sandbox-to-sandbox messaging and GitHub write access
remain out of scope.

## What runs where

```text
Parent OpenShell sandbox
  Pi + pi-subagents
          |
          | POST /v1/jobs, then poll GET /v1/jobs/{id}
          v
OpenShell Tool Service (trusted host process)
  Mock LLM policy reviewer
          |
          | openshell sandbox create / exec / delete
          v
Child OpenShell sandbox
  Pi -p <subagent prompt>  --->  final text
```

Pi's `openshell-worker` agent uses the `external-job` runner supplied by
`pi-subagents`. The local Pi extension in `pi-package/` translates that runner's
start, status, result, and reattach operations into HTTP requests.

The package installs two parent skills. `openshell-workers` tells Pi when and
how to delegate, and requires `generate-sandbox-policy` before every worker
launch. `generate-sandbox-policy` translates the child task into the complete,
least-privilege YAML policy placed in the worker task. Pi discovers both skills
from the package metadata when `pi install /opt/pi-openshell-poc` runs during
the parent image build.

Skill discovery alone does not guarantee that a model will select the
OpenShell-specific worker instead of a built-in Pi subagent. The parent launch
therefore appends `parent-system-prompt.md` to Pi's system prompt. That rule
requires every delegation to use `openshell-worker`, requires both policy and
orchestration skills to be read first, and forbids silently doing delegated
repository work in the parent sandbox.

The `openshell-worker` definition deliberately omits Pi Subagents' native
acceptance and agent-contract fields. The `external-job` runner does not support
those fields, including an explicit `acceptance.level: none`; adding one causes
the launch to fail before the Tool Service is called. The parent image sets
`intercomBridge.mode` to `off` because external OpenShell jobs use the Tool
Service lifecycle and do not expose Pi's native `contact_supervisor` channel
inside the child.

The community Pi image currently contains Pi 0.77.0. The derived parent image
pins Pi 0.81.0 because `pi-subagents` 0.58.0 requires `@earendil-works/pi-ai`
0.80.0 or newer. The child does not load `pi-subagents`, so it can use the
community image directly.

The OpenShell Tool Service is the only component allowed to invoke the
OpenShell CLI. Its generic create-job request contains an idempotency key, the
child prompt, and the parent-authored policy. The service loads the trusted
parent sandbox name from the request, loads that sandbox's active policy from
OpenShell, and asks the configured GPT-5.6
model whether the child adds authority. Only an explicit, schema-valid `allow`
continues to sandbox creation. The child image, provider, uploaded model
configuration, command, timeouts, and cleanup behavior still come from trusted
service configuration.

For every worker, the parent places YAML between `<openshell-policy>` and
`</openshell-policy>` in the task. The Pi extension extracts that block and
sends it as `resources.childPolicy`. The extension removes the block from the
prompt and discards Pi Subagents' `<System instructions>` wrapper before
submitting the request, so the child sees only the delegated task. The Tool
Service writes the exact submitted policy to a temporary file and passes it to
`openshell sandbox create --policy`. OpenShell performs schema and safety
validation.

For repository work, the parent authors the narrowest Git operations required
by the task. A read task can use repository-scoped `git-upload-pack`; a push
task can additionally propose repository-scoped `git-receive-pack`. The Tool
Service does not impose a read-only rule or otherwise interpret the task; the
reviewer compares the proposed policy with the live parent policy.

Network policy and credentials remain separate. A GitHub write also requires
the Tool Service to attach an explicitly allowlisted GitHub credential provider
to the child. The current POC attaches only its configured child provider, so
removing the policy-authoring restriction does not itself forward a GitHub
credential.

The mock LLM review is not a proof or a production security boundary. Models
can misunderstand policy semantics and produce false allows. The service calls
it asynchronously and fails closed on deny, timeout, provider failure,
malformed JSON, uncertainty, or internally inconsistent output. The model also
reports task alignment, but task alignment does not change the subset decision.
The `PolicyReviewer` interface is deliberately replaceable by a future formal
attenuation checker.

When the reviewer denies a network-only increase, the Tool Service creates no
child and returns `POLICY_ADVISOR_ACTION_REQUIRED`. The parent uses OpenShell's
in-sandbox Policy Advisor API to submit only the missing `addRule` operations.
OpenShell stores the proposal, runs its prover, and requires an external human
approval in manual mode. After the parent observes `policy_reloaded: true`, it
launches a fresh worker request; the Tool Service then reviews against the
updated live parent policy. The Tool Service never approves proposals and does
not need OpenShell `config:write` authority.

The parent name in `caller.sandboxName` is self-reported for this POC. Binding
that identity to an OpenShell-issued or Tool Service-issued credential is
intentionally deferred.

## Prerequisites

- A running OpenShell gateway and working `openshell` CLI.
- OpenShell 0.0.116 or newer (`--detach` is used for child creation).
- Docker available to the gateway's local compute driver.
- A workspace inference route backed by an OpenAI-compatible provider.
- `uv`, Node.js, and npm for local development checks.

Confirm the gateway and route:

```bash
openshell status
openshell provider list
openshell inference get --workspace default
```

This checkout's example configuration matches the NVIDIA Inference Hub route:
provider `nv-inference`, model `azure/openai/gpt-5.6-sol`, workspace `default`. If your
route is different, update all four matching values before building or running:

- `OPENSHELL_CHILD_PROVIDER` and `PI_MODEL` in `.env`
- the model `id` in `config/models.json`
- `--provider` and `--model` in the parent launch command below

For this provider and route, the OpenShell setup is:

```bash
export OPENAI_API_KEY="$NVIDIA_API_KEY"
openshell provider create --workspace default --name nv-inference --type openai \
  --credential OPENAI_API_KEY \
  --config OPENAI_BASE_URL=https://inference-api.nvidia.com/v1
unset OPENAI_API_KEY
openshell inference set --workspace default --provider nv-inference \
  --model azure/openai/gpt-5.6-sol --timeout 300
```

Do not put the API key in this repository or pass it to a sandbox with `--env`.
This POC attaches the provider to both sandboxes to satisfy the Step 1
requirement. Managed model traffic still resolves through the workspace's
inference route at `https://inference.local/v1`; provider attachment and
inference routing are separate OpenShell mechanisms.

## 1. Run the local checks

From this directory:

```bash
uv sync --locked
uv run ruff check src tests
uv run pytest -q
npm --prefix pi-package ci
npm --prefix pi-package test
npm --prefix pi-package run typecheck
```

The Python tests use a fake runtime and fake policy reviewers. They prove the
HTTP job lifecycle, parent-authored policy handoff, idempotent request
validation, fail-closed review gating, cleanup, and the exact create/exec/delete
command sequence without creating sandboxes or calling a model.

## 2. Start the OpenShell Tool Service

If the smoke-only version of the service is already running, stop it with
`Ctrl-C` before restarting. The existing `.state/jobs.sqlite3` is migrated in
place and retained.

If an existing `.env` still contains `OPENSHELL_CHILD_POLICY`, remove that line.
The Tool Service no longer selects the child policy from local configuration.
Remove the old `OPENSHELL_PARENT_POLICY` and `OPENSHELL_PARENT_SANDBOX`
settings. The parent now supplies its sandbox name with each job request.

Create local configuration and replace the example Tool Service token and
`NVIDIA_API_KEY`. The local `.env` is ignored by Git and is the single source
for Tool Service configuration:

```bash
cp .env.example .env
```

Load it and start the service from this project directory:

```bash
set -a
source .env
set +a
uv run openshell-tool-service
```

Keep this terminal open. The service listens on host port `8765`, stores job
state in `.state/jobs.sqlite3`, and logs the job ID and child sandbox name. The
service must run on a machine that has an authenticated OpenShell CLI for the
same gateway used by the parent. It fetches the request's parent sandbox policy,
then sends the task and both policies to the configured external
Responses-compatible model for the mock review.

In another terminal, confirm that the service is reachable from the host:

```bash
curl http://127.0.0.1:8765/healthz
```

The service prints a human-readable lifecycle at INFO level. For each
`POST /v1/jobs`, it also prints the complete parsed JSON request body, including
the prompt and parent-authored child policy. Uvicorn access logging is disabled
so Pi's repeated status polling does not flood the terminal. Set
`OPENSHELL_TOOL_SERVICE_LOG_LEVEL=DEBUG` to additionally include prompt and
policy sizes and hashes, queue time, exit code, output sizes, and cleanup
details. Authorization headers, Tool Service tokens, and provider credentials
are never logged.

Example:

```text
11:58:31 INFO job c6f5f6f2 accepted (sandbox=pi-child-c6f5f6f26b)
11:58:31 INFO job c6f5f6f2 request body:
{
  "idempotencyKey": "...",
  "caller": {
    "sandboxName": "pi-parent"
  },
  "prompt": "...",
  "resources": {
    "childPolicy": "..."
  }
}
11:58:31 INFO job c6f5f6f2 running mock LLM policy review
11:58:36 INFO job c6f5f6f2 mock LLM policy review allowed: child authority is contained by parent authority
11:58:36 INFO job c6f5f6f2 creating sandbox pi-child-c6f5f6f26b
11:58:37 INFO job c6f5f6f2 sandbox ready in 407ms; inspect: openshell logs ...
11:58:37 INFO job c6f5f6f2 running Pi (timeout=300s)
11:59:18 INFO job c6f5f6f2 Pi completed in 41.3s
11:59:18 INFO job c6f5f6f2 sandbox deleted in 142ms
11:59:18 INFO job c6f5f6f2 completed successfully in 46.9s
```

Pi output is still captured and returned only when the child exits; this change
adds lifecycle visibility but does not stream the child's intermediate text.

## 3. Launch the parent Pi sandbox

Load the same `.env` in the second terminal, then run:

Delete and recreate an existing `pi-parent` so both its image and its static
policy contain the parent-authored-policy flow:

```bash
openshell sandbox delete --workspace default pi-parent
```

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
  --env PI_OFFLINE=1 \
  --env PI_SKIP_VERSION_CHECK=1 \
  --env PI_TELEMETRY=0 \
  --env PI_CODING_AGENT_DIR=/home/sandbox/.pi/agent \
  --no-credential-warnings \
  --tty \
  --detach
```

Enable Policy Advisor on the parent and explicitly keep human approval in
manual mode:

```bash
openshell settings set pi-parent \
  --workspace default \
  --key agent_policy_proposals_enabled \
  --value true

openshell settings set pi-parent \
  --workspace default \
  --key proposal_approval_mode \
  --value manual

openshell settings get pi-parent --workspace default
```

The effective settings must show Policy Advisor enabled and approval mode
`manual`. A gateway-global setting overrides the sandbox setting.

Then start Pi as a secondary process inside the retained parent sandbox:

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

The parent policy permits Pi/Node to call the Tool Service job endpoints and
permits Git read-only clone/fetch access to public repositories under
`github.com/OWNER/REPO`. It does not permit Git pushes or give the parent access
to the gateway API or OpenShell credentials.

## 4. Validate one generic child

At the Pi prompt, enter:

```text
Use the openshell-worker subagent to complete this task in a dedicated OpenShell
sandbox. In the child, run hostname, wait 30 seconds, and then reply with exactly
OPEN_SHELL_CHILD_OK followed by the hostname. Start it in the background, wait
for it to complete, and report its final output.
```

Expected flow:

1. Pi calls the `subagent` tool with agent `openshell-worker`.
2. The installed `openshell-workers` skill requires the parent to load
   `generate-sandbox-policy`, author the child policy, and place it inside an
   `<openshell-policy>` block.
3. The Pi extension derives an idempotency key from Pi's internal run identity,
   keeps only Pi Subagents' `<Task>` section, removes the policy block, and calls
   `POST /v1/jobs` with the clean delegated task and policy. It receives a queued
   job ID immediately.
4. The adapter sends `caller.sandboxName=pi-parent`. The Tool Service runs
   `openshell policy get pi-parent --full --output json` against the configured
   gateway and uses that active effective policy for the mock LLM subset
   review while the job remains queued. A missing parent, lookup failure, deny,
   or review failure marks the job failed and creates no sandbox.
5. After an explicit allow, the Tool Service materializes the submitted policy, creates detached
   `pi-child-<job-id-prefix>`, attaches the configured provider, uploads
   `models.json`, and runs
   Pi inside it with `sandbox exec`.
6. `pi-subagents` polls the status endpoint while review and child work proceed.
7. The Tool Service captures the child's final output and deletes the child.
8. `pi-subagents` fetches the result and gives it to the parent Pi agent.

While the child is waiting, verify the separate sandbox from a third terminal:

```bash
openshell sandbox list --workspace default
openshell sandbox get pi-child-<job-id-prefix> --workspace default
```

The Tool Service terminal supplies the exact job ID and child sandbox name.
After completion, `openshell sandbox list` should show `pi-parent` but no child.
The parent should display `OPEN_SHELL_CHILD_OK` and the hostname returned by the
child.

## 5. Validate Policy Advisor escalation

Use a destination absent from the default parent policy so the first child
request is denied by the reviewer:

```text
Use one openshell-worker subagent to fetch https://example.com/ and return the
HTTP status and page title. If child creation is denied because the child policy
exceeds the parent policy, use OpenShell Policy Advisor to propose only the
missing network rule, wait for human approval, and then launch a new worker for
the same task.
```

The Tool Service must log `policy-review-denied` and must not create a child.
The parent should submit a narrow `example.com:443` proposal through
`policy.local` and wait. From a third terminal, inspect the pending proposal:

```bash
openshell rule get pi-parent --workspace default --status pending
```

Approve the displayed chunk only after checking its host, port, binary, method,
path, prover result, and candidate hash:

```bash
openshell rule approve pi-parent \
  --workspace default \
  --chunk-id <chunk-id>
```

To validate rejection instead:

```bash
openshell rule reject pi-parent \
  --workspace default \
  --chunk-id <chunk-id> \
  --reason "Narrow this rule before retrying."
```

On approval, the parent should observe `policy_reloaded: true`, launch a new
worker job, and receive a successful result. The Tool Service should fetch the
updated effective parent policy and allow the second request. On rejection, the
parent should report the human guidance and create no child.

This flow covers network `addRule` proposals only. Filesystem, process, provider
attachment, credential binding, and unsupported advanced network fields still
require a separate manual update path.

## 6. Validate a repository-scoped child

Ask the parent:

```text
Use one openshell-worker subagent to clone and review
https://github.com/nicobailon/pi-subagents. Return the child's concise summary.
```

The parent should author a repository-scoped read policy. The Tool Service should
create that temporary policy under `.state/policies/`, use it to launch the
child, and remove it after the job. The child can run Git's read-only clone
protocol for `nicobailon/pi-subagents`; another GitHub repository is not
authorized by the submitted policy. This validation task does not require
large-file storage, submodules, release downloads, raw-content URLs, private
repositories, or GitHub writes.

## 7. Validate two workers in parallel

Ask the parent with two full public repository URLs:

```text
Clone and review these two repositories. Use one openshell-worker subagent per
repository, run both in parallel, and combine their reviews:

- https://github.com/NVIDIA/OpenShell
- https://github.com/nicobailon/pi-subagents
```

Then repeat with the natural prompt that does not name the OpenShell agent:

```text
Clone and review these repositories. Use one subagent per repository and run
them in parallel:

- https://github.com/NVIDIA/OpenShell
- https://github.com/nicobailon/pi-subagents
```

The parent system instruction should still route both lanes to
`openshell-worker`. The parent must not clone the repositories itself. The Tool
Service should receive two jobs and `openshell sandbox list --workspace
default` should show two `pi-child-*` sandboxes while the reviews are running.
Each child prompt should contain only the concise repository-review task
authored by the parent. It should not contain the `<System instructions>`
section, the `<openshell-policy>` block, or an `Intercom orchestration channel`
section. Pi Subagents may still infer its own acceptance instructions when no
explicit runner override is present; the external-job definition must not set
an acceptance or agent-contract field.

Expected behavior:

1. The parent uses one asynchronous `workflowScript` with two worker tasks.
2. The Tool Service accepts two jobs and runs up to two child sandboxes at once.
3. The parent authors a separate policy for each child containing only its
   assigned repository.
4. Both child results return through `pi-subagents`, and the parent combines
   them.
5. Both child sandboxes and their generated policy files are removed.

## What this proves

- Pi can select a normal `pi-subagents` agent definition.
- One agent definition can execute different task prompts without creating a
  task-specific profile.
- Its external-job provider can asynchronously submit and poll OpenShell work.
- A parent Pi can author a child policy and pass it through `pi-subagents` to a
  trusted service that applies it to a dedicated child sandbox.
- A replaceable mock LLM reviewer can fail closed before sandbox creation when
  it detects policy expansion or cannot return a valid decision.
- A denied network expansion can become an OpenShell Policy Advisor proposal,
  remain pending for external human review, hot-reload into the parent after
  approval, and allow a fresh child request without giving approval authority
  to the Tool Service.
- The Tool Service still fixes the child image, provider, command, workspace,
  timeouts, and cleanup behavior.
- The parent can fan out two independent repository jobs concurrently.
- The child Pi's final answer returns to the parent, and cleanup is automatic.

It does not prove policy attenuation, unrestricted general-purpose execution,
private repository access, GitHub writes, or cross-sandbox messaging. The LLM
review only demonstrates the intended gating experience; a false `allow`
remains possible until a sound attenuation implementation replaces it.
