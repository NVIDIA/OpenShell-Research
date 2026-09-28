# Architecture details

## What the POC proves

A Pi agent contained in one OpenShell sandbox can use Pi Subagents to delegate
work to a second Pi agent contained in a newly created OpenShell sandbox. The
child receives its own parent-authored OpenShell policy and is deleted after it
returns one final response.

The POC does not provide parent/child messaging, sibling messaging, shared
memory, long-lived workers, or follow-up turns.

## Component model

```mermaid
flowchart LR
    User --> Parent[Parent Pi\nOpenShell sandbox]
    Parent --> Adapter[Pi Subagents\nexternal-job adapter]
    Adapter -->|HTTP job API| Tool[OpenShell Tool Service\ntrusted host process]
    Tool -->|policy get| Gateway[OpenShell gateway]
    Tool -->|local policy files| Reviewer[openshell-prover\ncontainment check]
    Tool -->|sandbox create / exec / logs / delete| Gateway
    Gateway --> Child[Child Pi\nOpenShell sandbox]
    Child -->|final stdout| Tool
    Tool -->|job result| Adapter
    Adapter --> Parent
```

### Ownership

| Component | Owns |
| --- | --- |
| Parent Pi | Task decomposition and proposed child policy |
| Pi Subagents | Subagent workflow and external-job lifecycle |
| Local Pi adapter | Request translation and idempotency key |
| Tool Service | Trusted child configuration and OpenShell CLI operations |
| Policy prover | Containment result, modeled scope, counterexample or reason for no proof |
| Native composition helper | Reads live provider profiles and composes the effective child policy without creating a sandbox |
| OpenShell | Sandbox lifecycle and policy enforcement |
| Child Pi | Execution of one delegated prompt |

The Tool Service—not the sandbox request—selects the child image, workspace,
provider, model, command, timeouts, and cleanup behavior.

## Request flow

For this parent prompt:

```text
Use one openshell-worker subagent to run hostname.
```

the flow is:

1. The parent loads the `openshell-workers` and `generate-sandbox-policy`
   skills.
2. It authors a complete child policy and puts it at the beginning of the
   worker task:

   ```text
   <openshell-policy>
   version: 1
   ...
   </openshell-policy>
   Run hostname and return the result.
   ```

3. Pi Subagents selects the `openshell-worker` definition. That agent uses the
   `external-job` runner instead of creating an in-process Pi child.
4. The local adapter removes the policy block and any harness-generated
   acceptance appendix from the child prompt.
5. The adapter sends:

   ```json
   {
     "idempotencyKey": "stable hash of the Pi run and step",
     "caller": {"sandboxName": "pi-parent"},
     "worker": {
       "stepIndex": 0,
       "prompt": "Run hostname and return the result.",
       "resources": {"childPolicy": "version: 1\n..."}
     }
   }
   ```

6. The Tool Service persists a job and immediately returns its ID with state
   `queued`. Pi polls that job's individual status endpoint. Repeating the same
   request returns the same job.
7. In a background worker, the Tool Service retrieves `pi-parent`'s live policy
   using `openshell policy get`.
8. The service first checks the original child document so normalization cannot
   conceal unsupported fields. For a provider-backed job, the native helper
   verifies the provider is attached to the parent, reads its live profile, and
   calls OpenShell's `compose_effective_policy`. The reviewer checks those
   effective child and parent policies. It writes private per-call files and
   invokes `openshell-prover check child.yaml --boundary parent.yaml --output json`.
   The task is not passed to the prover. Actual exit code 0, `within_boundary`, and
   the expected schema/check/coverage domains are all required. Unsupported,
   inconclusive, timeout, missing binary, malformed output, or inconsistent
   status fails closed. Review files are removed after every outcome.
9. After an allow, the Tool Service writes the child policy to a temporary
   local file and runs `openshell sandbox create` for a deterministic
   `pi-child-<job-prefix>` name.
10. Creation attaches the configured provider and uploads the trusted model
    configuration. It submits the original source policy, not the composed
    `_provider_*` rules, which belong to the gateway.
11. After the child is Ready, the service compares its actual effective policy
    and the parent's current policy with the proved snapshots. On a mismatch it
    deletes the child without running Pi. Otherwise it runs `pi -p --no-session` inside
    it using `openshell sandbox exec`. Only the cleaned delegated prompt is
    passed on stdin.
12. The Tool Service captures the child's final stdout and a best-effort
    OpenShell log tail.
13. It always attempts idempotent child deletion, including after create or
    execution failures.
14. Pi Subagents polls job status, fetches the terminal result, and presents
    the child output to the parent.

## Why the Tool Service exists

The parent sandbox does not hold the user's OpenShell CLI credential. The Tool
Service runs on the trusted host where the authenticated CLI can operate on the
same gateway as the parent.

This keeps administrative sandbox lifecycle authority outside the untrusted
agent while still demonstrating delegated creation without changing OpenShell
core.

The parent policy allows the Pi/Node executable to call only the Tool Service's
job endpoints. Child sandboxes do not need access to the Tool Service because
they return their answer through `openshell sandbox exec` stdout.

## Policy path

The parent authors the policy required for the child's task. The Tool Service
does not infer or broaden it.

Each new job fetches the parent policy and runs its own local containment check. Neither the parent policy nor review decisions are cached. Repeated
submissions with the same idempotency key still return the existing job.
The adapter converts a validated proof into an allow decision and a validated
`exceeds_boundary` counterexample into a denial. Unsupported and inconclusive results
are separate failures, not claims of a permission increase. The task cannot
influence the comparison and is never sent to the CLI.

```text
Parent authors policy
        ↓
Tool Service fetches live parent policy and composes child provider rules
        ↓
openshell-prover returns a containment result
        ↓
within_boundary + supported launch config: create child
every other outcome: no child is created
```

For a denied network increase, the parent may use OpenShell Policy Advisor to
request the narrow missing rule. A human approves or rejects it. Approval
updates the parent; the parent must then launch a new child job.

The `PolicyReviewer` interface is implemented by `ProverPolicyReviewer`; there
is no model-review fallback. The integration pins schema version 1, the
`boundary` check, and coverage of the filesystem/network L4/network REST/process/Landlock
domains. The reported input paths and exit
code must match the invocation. Equal policies may pass: this is a no-escalation
check, not a proof of task-specific least privilege.

The prover build script pins OpenShell PR #3533 revision
`df10520d3828768189faa348ba9f6e6f807ab1cb`, which includes checks for explicit
`process` and `landlock` blocks and destination `allowed_ips`. Main reports
`coverage.domains`, not the old `scope.model_version`; the source pin records
which implementation was reviewed. Coverage describes the comparison model,
not an attestation of runtime enforcement. Older CLI contracts are not accepted.
Process comparisons assume consistent identity
resolution; Landlock comparisons check compatibility requirements, not actual
kernel enforcement. Filesystem narrowing such as `/tmp` to `/tmp/worker` remains
unsupported. The service passes the complete policy through unchanged and exposes
the CLI reason; it never projects unsupported fields out of the proof.

The host-side `poc-policy-compose` helper uses the existing CLI mTLS transport
and native composition library. It reads the configured provider and profile
through the gateway API. If the gateway has no profile, no layer is added, matching
the gateway's behavior. Built-in OpenAI/Anthropic profiles with alternate base
URLs also do not add their default public-vendor endpoints. The helper never
substitutes a local profile for a missing live one or prints credential values.

CLI errors map to `policy-review-unavailable`; unsupported and inconclusive
results map to `policy-review-unsupported` and `policy-review-inconclusive`.
Only a demonstrated expansion maps to `policy-review-denied` and includes the
conditional Policy Advisor guidance. A missing proof cannot be repaired by
automatically asking for more parent permissions.

## Job lifecycle

```text
queued → running → completed
                 ↘ failed
```

SQLite stores the adapter job ID, idempotency key, caller name, child prompt,
child policy, sandbox name, state, output, diagnostics, and captured log tail.
It is local persistence for job polling and restart cleanup—not shared agent
memory.

On Tool Service restart, any previously queued or running job is marked failed
and its deterministic sandbox name is passed to idempotent cleanup.

Creation/upload transport failures have bounded retries, with deletion of any
partial sandbox before another create attempt. Execution retries are limited to
recognized connection-setup errors with no stdout. An ambiguous connection reset
is reported as a failure, not replayed: the child may already have performed work.
These CLI diagnostics are a heuristic, not an exactly-once execution guarantee.

Cleanup runs after execution. If deletion fails, status and result responses
include `cleanupError` and `sandboxName`, even when the task itself completed.
Successful task output does not prove that the sandbox was deleted.

## Where the pieces are configured

| File | What to look for |
| --- | --- |
| [Dockerfile.parent](Dockerfile.parent) | Installs Pi, the published Pi Subagents package, and our local extension |
| [package.json](pi-package/package.json) | Declares the extension, skills, and agent directory Pi discovers |
| [openshell-worker.md](pi-package/agents/openshell-worker.md) | Selects the external-job provider and sets the harness timeout |
| [index.ts](pi-package/index.ts) | Registers start, status, result, and reattach HTTP handlers; this is custom code |
| [resources.ts](pi-package/resources.ts) | Separates the task from policy metadata and derives the idempotency key |
| [parent-system-prompt.md](pi-package/parent-system-prompt.md) | Guides the parent to choose the worker and author its policy |
| [.env.example](.env.example) | Host service, model, provider, timeout, and concurrency settings |
| [parent-smoke.yaml](policies/parent-smoke.yaml) | Parent filesystem baseline and permission to call the job API |

These are local additions; no upstream Pi or Pi Subagents source is patched.
The policy-authoring skill bundled here is POC guidance, not a policy validator.
Worker selection still depends on parent instructions. Once that worker is
selected, its runner configuration routes the job to the Tool Service.

## Trust and limitations

- The shared bearer token authenticates access to the Tool Service but does not
  cryptographically bind the caller's self-reported sandbox name.
- OpenShell enforces each sandbox policy, but it does not currently record a
  native parent-child delegation relationship for this POC.
- A proof covers only the supported model. Unsupported policy fields are
  rejected; the supplied Pi baseline is supported by the pinned PR build.
- The process exit code and complete JSON contract are checked before launch;
  no model is consulted if the prover fails.
- Review and create are separate operations, so they are not bound to one
  atomic parent-policy revision.
- The helper requires the child provider to be attached to the parent. A policy
  proof alone is not permission to delegate a different credential. This local
  helper supports mTLS and sandbox-owned policy, not global policy.
- The snapshot check happens before Pi starts, not atomically with creation or
  execution. It detects intervening policy drift but does not enforce future
  parent/provider revocation. Keep these settings stable while running the POC.
- Children are independent one-shot jobs. They cannot communicate or receive a
  follow-up prompt.

The native product direction would move authenticated parent identity,
parent-child lineage, policy attenuation, provider delegation, quotas, and
atomic creation into OpenShell. Pi should continue to own task decomposition
and the decision to use a subagent.
