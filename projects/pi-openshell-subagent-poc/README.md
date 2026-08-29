# Generic Pi workers in OpenShell sandboxes

This POC demonstrates one reusable behavior:

> A Pi agent running in an OpenShell parent sandbox asks `pi-subagents` to run
> `openshell-worker`; each subagent runs as a second Pi process in a newly
> created OpenShell child sandbox and returns its final answer to the parent.

The worker accepts arbitrary task text. The parent Pi also authors the complete
OpenShell policy for each child and includes it in the worker task. The Tool
Service applies that policy when it creates the child. Sandbox-to-sandbox
messaging and GitHub write access remain out of scope.

## What runs where

```text
Parent OpenShell sandbox
  Pi + pi-subagents
          |
          | POST /v1/jobs, then poll GET /v1/jobs/{id}
          v
OpenShell Tool Service (trusted host process)
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

The `openshell-worker` agent disables Pi Subagents' automatic acceptance
contract. The child returns the task result directly instead of receiving an
implementation-oriented checklist and required acceptance-report JSON. The
parent image also sets `intercomBridge.mode` to `off`; external OpenShell jobs
use the Tool Service lifecycle and do not expose Pi's native
`contact_supervisor` channel inside the child.

The community Pi image currently contains Pi 0.77.0. The derived parent image
pins Pi 0.81.0 because `pi-subagents` 0.58.0 requires `@earendil-works/pi-ai`
0.80.0 or newer. The child does not load `pi-subagents`, so it can use the
community image directly.

The OpenShell Tool Service is the only component allowed to invoke the
OpenShell CLI. It accepts one fixed `openshell-worker` execution envelope. The
task prompt and child policy come from the parent. The child image, provider,
uploaded model configuration, command, timeouts, and cleanup behavior still
come from trusted service configuration.

For every worker, the parent places YAML between `<openshell-policy>` and
`</openshell-policy>` in the task. The Pi extension extracts that block and
sends it as `resources.childPolicy`. The Tool Service writes the exact submitted
policy to a temporary file and passes it to `openshell sandbox create --policy`.
OpenShell performs schema and safety validation.

For repository work, the parent-authored policy permits Git's read-only
`info/refs` and `git-upload-pack` requests only for the assigned repository. The
extension also sends the normalized `OWNER/REPO` as job metadata, but the Tool
Service does not generate policy from it.

This POC intentionally does not yet prove that the submitted child policy is a
subset of the parent's policy. To preserve that governing rule for repository
tasks in the demo, the checked-in parent policy grants read-only Git clone/fetch
access to public `github.com/OWNER/REPO` paths. Do not treat the parent-authored
policy endpoint as safe for untrusted use until attenuation checks are added.

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

The Python tests use a fake runtime. They prove the HTTP job lifecycle,
parent-authored policy handoff, resource validation, cleanup, and the exact
create/exec/delete command sequence without creating sandboxes.

## 2. Start the OpenShell Tool Service

If the smoke-only version of the service is already running, stop it with
`Ctrl-C` before restarting. The existing `.state/jobs.sqlite3` is migrated in
place and retained.

If an existing `.env` still contains `OPENSHELL_CHILD_POLICY`, remove that line.
The Tool Service no longer selects the child policy from local configuration.

Create local configuration and replace the example token with a random POC
value:

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
same gateway used by the parent.

In another terminal, confirm that the service is reachable from the host:

```bash
curl http://127.0.0.1:8765/healthz
```

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
  --env PI_OFFLINE=1 \
  --env PI_SKIP_VERSION_CHECK=1 \
  --env PI_TELEMETRY=0 \
  --env PI_CODING_AGENT_DIR=/home/sandbox/.pi/agent \
  --no-credential-warnings \
  --tty \
  --detach
```

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
3. `pi-subagents` calls `POST /v1/jobs` with the task and policy and receives a
   queued job ID immediately.
4. The Tool Service materializes the submitted policy, creates detached
   `pi-child-<job-id-prefix>`, attaches the configured provider, uploads
   `models.json`, and runs
   Pi inside it with `sandbox exec`.
5. `pi-subagents` polls the status endpoint while the child works.
6. The Tool Service captures the child's final output and deletes the child.
7. `pi-subagents` fetches the result and gives it to the parent Pi agent.

While the child is waiting, verify the separate sandbox from a third terminal:

```bash
openshell sandbox list --workspace default
openshell sandbox get pi-child-<job-id-prefix> --workspace default
```

The Tool Service terminal supplies the exact job ID and child sandbox name.
After completion, `openshell sandbox list` should show `pi-parent` but no child.
The parent should display `OPEN_SHELL_CHILD_OK` and the hostname returned by the
child.

## 5. Validate a repository-scoped child

Ask the parent:

```text
Use one openshell-worker subagent to clone and review
https://github.com/nicobailon/pi-subagents. Return the child's concise summary.
```

The parent should author a repository-scoped policy. The Tool Service should
create that temporary policy under `.state/policies/`, use it to launch the
child, and remove it after the job. The child can run Git's read-only clone
protocol for `nicobailon/pi-subagents`; another GitHub repository is not
authorized by the submitted policy. Large-file storage, submodules, release
downloads, raw-content URLs, private repositories, and GitHub writes are not
included in this POC.

## 6. Validate two workers in parallel

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
Each child prompt should contain only its policy and concise repository-review
task. It should not contain an `Acceptance Contract`, `acceptance-report`, or
`Intercom orchestration channel` section.

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
- The Tool Service still fixes the child image, provider, command, workspace,
  timeouts, and cleanup behavior.
- The parent can fan out two independent repository jobs concurrently.
- The child Pi's final answer returns to the parent, and cleanup is automatic.

It does not prove policy attenuation, unrestricted general-purpose execution,
private repository access, GitHub writes, or cross-sandbox messaging. The
parent-authored policy is trusted temporarily for this POC; subset proof and
fail-closed rejection are the next security step.
