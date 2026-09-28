# Pi subagent delegation through OpenShell

This POC demonstrates one behavior:

> A Pi agent running in an OpenShell parent sandbox delegates a task through
> `pi-subagents`; the worker runs in a newly created, policy-scoped OpenShell
> child sandbox and returns its final answer to the parent.

There is deliberately no parent/child or child/child messaging layer here. The
child is one-shot: create sandbox, run Pi, return the result, delete sandbox.

The parent authors the child policy. A host-side Tool Service retrieves the
live parent policy and runs the standalone `openshell-prover` CLI. It creates a
child only after a validated `within_boundary` result with exit code 0, then executes
Pi, captures its result, and deletes the child. Policy review makes no model calls.

The check establishes that the child grants no more permissions than the parent
within the prover's supported model; equal policies are allowed. It does not
prove that the child has the minimum permissions necessary for its task.
See [Architecture details](architecture_details.md) for the trust boundaries.

The prover is built from OpenShell PR #3533 at revision
`df10520d3828768189faa348ba9f6e6f807ab1cb` (updated September 28, 2026; not yet merged).
That revision includes `process`, `landlock`, and `allowed_ips` checks. The POC
no longer requires the custom `kirit93/prover-policy-fields` prover branch.

The pin also includes REST query containment and its wildcard correctness fixes.
PR #3533 is not yet merged; this is a pinned PR build, not a released OpenShell version.

Provider-backed jobs include the live provider rules before review. The configured
provider must already be attached to the parent. Filesystem subdirectory comparisons
can still return unsupported. Do not omit
required fields or providers, or use the former LLM reviewer, to bypass a failure.
Passing the policy-only tests is not a successful end-to-end delegation test.

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
  `azure/openai/gpt-5.6-sol`, for agent inference only. The policy reviewer no longer
  uses or requires `NVIDIA_API_KEY`.
- Rust (rustup; the pinned source selects 1.95.0) and Z3 to build the standalone
  prover. Alternatively, CMake 3.16+ can build bundled Z3.

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

## Build the policy prover

From the POC directory, build the pinned PR revision:

```bash
brew install z3
bash scripts/build-prover.sh
```

The script builds in a temporary checkout without changing your OpenShell repo.
It installs `.state/bin/openshell-prover` and records its source revision in
`.state/bin/openshell-prover.commit`. It pins the reviewed commit rather than
silently tracking future changes to main. System Z3 must remain installed; use
`bash scripts/build-prover.sh --features bundled-z3` to build bundled Z3 instead.

Set this in `.env`, replacing any path to the earlier custom prover:

```dotenv
OPENSHELL_PROVER_BIN=.state/bin/openshell-prover
```

Alternatively, build the same revision in your OpenShell checkout with
`cargo build --locked -p openshell-prover-cli --bin openshell-prover` and point
`OPENSHELL_PROVER_BIN` at its absolute `target/debug/openshell-prover` path.
On macOS, set `Z3_LIBRARY_PATH_OVERRIDE="$(brew --prefix z3)/lib"` and add
`-L native=$Z3_LIBRARY_PATH_OVERRIDE` to `RUSTFLAGS` before building manually.
The binary's `--version` may say `0.0.0`; it does not identify the source commit.

**Provider composition is still a separate POC helper.** Keep your existing
`OPENSHELL_POLICY_COMPOSER_BIN` setting. The upstream prover checks complete
policies; it does not fetch or attach provider rules. The helper uses the host's
mTLS connection and OpenShell's native composition library to prepare those
policies without changing the gateway.

For the existing local setup, its source is
`crates/openshell-cli/examples/poc-policy-compose.rs` in the
`openshell-prover-fields` worktree. Build it there with
`cargo build --locked -p openshell-cli --example poc-policy-compose` and set
`OPENSHELL_POLICY_COMPOSER_BIN` to its absolute `target/debug/examples/poc-policy-compose`
path. That helper is not shipped on OpenShell main or installed by the prover
build script. A fresh setup needs this POC helper as well as the upstream prover.

Restart the Tool Service after changing its code or environment. The old
`--maximum` / `within_max` contract is no longer accepted. The service validates
the new JSON contract, including coverage of all five policy domains.
The solver timeout defaults to 10 seconds
(`OPENSHELL_POLICY_REVIEW_TIMEOUT_SECONDS`); the host allows five additional
seconds for the CLI to report a timeout, then terminates a hung process.

You can run the same check directly, without a gateway:

```bash
"$OPENSHELL_PROVER_BIN" check child.yaml --boundary parent.yaml --output json
```

Supply full policies. Expect `result: within_boundary` and exit code 0 for a
contained policy. Do not remove unsupported fields to obtain a successful check.

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
workspace used to create the parent. Existing `OPENSHELL_POLICY_REVIEW_BASE_URL`
and `OPENSHELL_POLICY_REVIEW_MODEL` settings are no longer used.

Keep `OPENSHELL_CHILD_PROVIDER=nv-inference`. The native helper verifies that it
is attached to the parent, reads its current profile, and composes its rules with
the requested child policy. The prover checks that effective policy against the
parent's effective policy. After creation, the service verifies both policies
still match the checked snapshots before starting Pi. A mismatch deletes the
child without running the task. A different provider not attached to the parent,
global policy, or a non-mTLS gateway is not supported by this helper.

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

## 7. Validate the launch gate

Enter this at the parent Pi prompt:

```text
Use one openshell-worker subagent to run hostname in a dedicated OpenShell
sandbox. Return exactly OPEN_SHELL_CHILD_OK followed by the hostname.
```

The pinned PR build can check the supplied baseline's process, Landlock, and IP fields,
including the provider-composed policy from the helper. Unsupported policy
features still stop creation. An unsupported check does not mean the parent lacks
a permission; do not submit a Policy Advisor request for it.

The successful lifecycle to validate is:

1. Pi invokes the `openshell-worker` external-job agent.
2. The Tool Service accepts the job and obtains a `within_boundary` proof for the full policy.
3. A new `pi-child-*` sandbox appears in `openshell term`.
4. The child reaches Ready, its effective policy matches the proof input, and
   the service runs a separate Pi process.
5. The parent receives `OPEN_SHELL_CHILD_OK` and the child's hostname.
6. The Tool Service deletes the child after its final response.

At `INFO` level, a successful job produces three main lines, each with its short job ID:

- `VERIFIED`: policy checks passed, with total review time including policy lookup and composition.
- `RUNNING`: Pi is starting, with the sandbox name, provider, and sandbox setup time.
- `DONE`: total elapsed time, exit code, and cleanup outcome.

Denials (`DENIED`), missing proofs (`NOT VERIFIED`), failures (`FAILED`), retries,
and cleanup problems remain visible. `DONE` with a warning means execution finished
but cleanup or log collection needs attention. Concurrent jobs can interleave;
use the job ID to follow one job. Task and policy contents are not printed at INFO.

Set `OPENSHELL_TOOL_SERVICE_LOG_LEVEL=DEBUG` in `.env` and restart the service for
individual proof stages, provider composition, command timings, and cleanup details.
Git discovery denials show `service=git-upload-pack (clone/fetch)` or
`service=git-receive-pack (push)` when applicable. Other query values are not
printed in the INFO/WARNING summary. Complete counterexamples remain in the
authenticated job result and DEBUG diagnostics; these can contain sensitive values.

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
If the prover establishes `exceeds_boundary`, the job fails before child creation with
`POLICY_ADVISOR_ACTION_REQUIRED`. An unsupported or inconclusive comparison does
not establish a permission increase and does not offer the approval path. The parent can then submit only the missing
network rule to Policy Advisor. After you approve it in `openshell term` and
the parent policy reloads, the parent launches a new worker job.

The POC instructs Pi to wait once for up to 30 seconds using `wait?timeout=30`.
If approval and reload are not confirmed, Pi should report the proposal ID and
return control to you instead of repeatedly waiting. A timeout is not approval;
after reviewing the proposal, ask Pi to continue. These are agent instructions,
not a change to OpenShell's server-side timeout limit.

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
| `policy-review-unavailable` | Verify `OPENSHELL_PROVER_BIN`, execute its `--version`, and inspect the failure message for an invalid schema, exit status, input error, or host timeout. There is no LLM fallback. |
| `policy-review-unsupported` | Inspect the reason code. Unrelated process identities, filesystem subdirectory narrowing, and advanced network surfaces can remain unsupported. Preserve the full policy and report the missing support. |
| `policy-composition-unavailable` | Check `OPENSHELL_POLICY_COMPOSER_BIN`, gateway authentication, and that the configured child provider is attached to the parent. No child is created. |
| `policy-snapshot-changed` | The created child's effective policy or the parent's current policy differs from what was proved. Pi did not start. Inspect changes, then retry with a new job. |
| `policy-review-inconclusive` | The prover could not establish containment, for example because of a solver timeout or cancellation. No child is launched and no parent-policy approval is suggested. |
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

The default suite uses fake OpenShell and prover results to exercise failures
and the launch gate. It creates no real sandboxes and calls no model:

```bash
uv sync --locked
uv run ruff check src tests
uv run pytest -q
npm --prefix pi-package ci
npm --prefix pi-package test
npm --prefix pi-package run typecheck
git diff --check
```

To exercise the real upstream CLI through the service, load `.env`, then run:

```bash
set -a
source .env
set +a
OPENSHELL_TEST_PROVER_BIN="$OPENSHELL_PROVER_BIN" uv run pytest -v tests/test_prover_integration.py
OPENSHELL_TEST_PROVER_BIN="$OPENSHELL_PROVER_BIN" uv run pytest -q
```

These tests still use a fake sandbox runtime. They verify that only a successful
proof reaches child execution, and that unproved requests never reach creation.
They include the checked-in GitHub parent policy: clone/fetch reaches the fake
runtime, while push discovery is denied with a query-specific counterexample
and Policy Advisor guidance. No GitHub writes occur in these tests.
They cover the supplied Pi baseline, process and Landlock weakening, IP-range
narrowing and expansion, unresolved paths, and provider-composed allow/deny cases.

For a real service-to-sandbox smoke test, with `pi-parent` already Ready and
`.env` loaded:

```bash
OPENSHELL_TEST_LIVE_DELEGATION=1 uv run pytest -q -s tests/test_live_provider_delegation.py
```

This creates one real child with the configured provider, runs Pi to obtain its
hostname, and deletes the child. It uses a temporary job database and does not
restart your running service. Expected output includes `OPEN_SHELL_CHILD_OK`, a
hostname, and `1 passed`. It tests the service lifecycle; use the Pi prompt above
to also test the parent harness and external-job adapter.

## Known limitations

- The Tool Service token and parent sandbox name are POC identity, not verified
  OpenShell workload identity.
- The CLI checks its declared filesystem, process, Landlock, and network model,
  including supported destination IP and REST restrictions. Process comparison
  assumes consistent identity resolution; Landlock comparison is about requested
  compatibility, not proof of installed kernel enforcement.
- Provider composition uses current gateway profiles. The helper supports local
  mTLS gateways, sandbox-owned policies, and a provider already attached to the
  parent; it does not authorize delegation of a different credential.
- This check establishes no additional authority, not a strictly smaller
  permission set or task-specific minimum permissions.
- Policy review and sandbox creation are separate, non-atomic operations.
  The pre-execution snapshot check detects intervening policy changes, but cannot
  prevent changes after that check. Keep policies/providers stable during a demo.
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
