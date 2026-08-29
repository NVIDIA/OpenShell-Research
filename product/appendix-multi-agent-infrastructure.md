# Appendix: Infrastructure required for multi-agent collaboration

Multi-agent collaboration will only feel native if adding another agent feels
like assigning work, not provisioning another machine. An agent should be able
to add a specialist, exchange work with it, let it go idle, wake it again, and
stop the team without waiting on a full container or Kubernetes lifecycle each
time.

Today, each collaborator is still an individual sandbox lifecycle. OpenShell
creates a `Provisioning` resource, asks its compute driver to create the
runtime, and waits for it to become ready. It now supports retained
`Stop -> Start` behavior, but that restarts compute; it does not define portable
checkpointing of memory and process state. If every specialist needs a new pod,
image pull, supervisor startup, policy load, and readiness check, collaboration
will remain constrained by infrastructure provisioning.

The governing principle should be:

> A sandbox is a durable logical agent. A container, pod, or VM is temporary
> capacity used to run it.

## The collaboration workflow creates infrastructure requirements

| Multi-agent need | Required infrastructure behavior |
| --- | --- |
| Add a specialist | Authorize the request, create a durable sandbox identity, and assign it to compatible ready capacity. Enforce team, lineage, fan-out, and resource limits before it starts. |
| Let an agent go idle | Suspend it and return its worker to the pool while retaining the state promised by the lifecycle contract. |
| Contact an idle agent | Address it by stable sandbox identity, resolve its current location, resume it if necessary, check both sandboxes' policies, and deliver the request within a bounded wait. |
| Create a delegated child | Attach authority to the logical sandbox and install its effective policy before execution. The child must not receive administrator or supervisor credentials. |
| Contain a team | Revoke new communication, stop or suspend selected members and descendants, release capacity, and reconcile partial failures. |
| Investigate a failure | Correlate lifecycle events, policy decisions, messages, peer calls, logs, and resource use across workers by team, sandbox, and lineage. |

## OpenShell needs an agent-scale lifecycle

The runtime should maintain warm capacity for compatible classes of work and
assign logical sandboxes to those workers on demand. Kubernetes or another
infrastructure manager can continue to add and remove worker capacity, but
individual agent activation should not depend on a new pod reaching ready
state.

OpenShell also needs distinct lifecycle contracts. **Stop** ends execution but
retains the persistent workspace. **Suspend** checkpoints the supported runtime
state and releases the worker. **Resume** restores that state, installs the
current policy, refreshes identity and credentials, and makes the agent ready.
**Delete** removes the logical sandbox and garbage-collects its retained state.

These operations must be idempotent and recoverable after gateway, driver, or
worker failure. The runtime must prevent duplicate assignments, bound the
number and duration of queued activations, and keep new-agent bursts from
starving agents that are already running.

Capacity limits must follow the collaboration graph. OpenShell needs limits for
child count, delegation depth, creation rate, concurrent agents, CPU, memory,
storage, inference, network, and accelerators. Otherwise sandbox creation
becomes a programmable resource-exhaustion path.

## Identity and policy must follow the agent

Team membership, lineage, delegated authority, and policy must attach to the
logical sandbox rather than its pod, IP address, or gateway connection. Before
a resumed agent executes, the runtime must install its current policy and issue
fresh credentials for the new assignment. Credentials must not be captured in
snapshots.

Reusing workers also requires a strict reset between agents. The runtime must
remove the previous sandbox's processes, filesystem changes, environment,
credentials, network rules, and identity. Snapshots must be integrity-checked,
encrypted, versioned against the runtime that created them, and inaccessible to
the workload.

OpenShell should remain authoritative for sandbox identity, policy,
credentials, delegated authority, collaboration authorization, and audit. A
compute substrate may own worker pools, placement, isolation, checkpointing,
restoration, and state locality. Applications continue to own goals, tasks,
sessions, retries, and result quality.

## Agent Substrate is a reference, not the complete solution

Agent Substrate follows the same core principle. It maps durable actors onto a
smaller pool of ready workers, suspends idle actors, restores them when traffic
arrives, and routes requests without exposing physical location. Its separation
between high-frequency actor activation and lower-frequency Kubernetes worker
provisioning is a useful model for removing sandbox creation from the critical
path.

It does not yet solve OpenShell multi-agent collaboration. Agent Substrate is
in early development, and parts of its architecture are aspirational. User
authorization, actor-to-actor policy, worker autoscaling, actor grouping,
forking, and downscoped delegation remain incomplete or on its roadmap. Open
issues also cover the ability to deliver the OpenShell supervisor into
arbitrary images and give enforcement components privileges that the workload
does not receive.

Adopting it would therefore require an OpenShell compute-driver integration and
a clear lifecycle and security compatibility contract. It would not replace
the OpenShell identity, policy, credential, team, or audit layers.

## What the first release should prove

The proposed four-sandbox team should validate this infrastructure path. A
coordinator should create one permitted child, the runtime should assign it to
warm capacity, and a forum message or peer call should reach it by logical
identity. The child should be able to go idle and return without losing the
state promised by the lifecycle contract. Removing it from the team should end
its collaboration access, and stopping the team should release every member's
physical capacity without leaving an orphaned sandbox.

The prototype should measure create-to-ready time, resume-to-ready time, queue
time, warm-capacity misses, snapshot duration, orphan rate, and team-stop
completion. These results will tell us whether Agent Substrate can serve as
OpenShell's compute substrate and whether the compute-driver contract must
expand.

Without this infrastructure path, OpenShell can add collaboration APIs, but
every new collaborator remains a slow, heavyweight infrastructure event.

## References

- [OpenShell compute-driver lifecycle contract](https://github.com/NVIDIA/OpenShell/blob/701382d015c0faa3bc65b78213136ca1cf0466f0/proto/compute_driver.proto#L11-L53)
- [OpenShell stop and start semantics](https://github.com/NVIDIA/OpenShell/blob/701382d015c0faa3bc65b78213136ca1cf0466f0/architecture/compute-runtimes.md#L87-L111)
- [OpenShell sandbox creation path](https://github.com/NVIDIA/OpenShell/blob/701382d015c0faa3bc65b78213136ca1cf0466f0/crates/openshell-server/src/compute/mod.rs#L944-L1016)
- [Agent Substrate architecture](https://github.com/agent-substrate/substrate/blob/main/docs/architecture.md)
- [Agent Substrate request parking](https://github.com/agent-substrate/substrate/blob/main/docs/request-parking.md)
- [Agent Substrate roadmap](https://github.com/agent-substrate/substrate/blob/main/docs/roadmap.md)
- [Agent Substrate threat model](https://github.com/agent-substrate/substrate/blob/main/docs/threat-model.md)
- [Agent Substrate issue #553: support sandbox platforms](https://github.com/agent-substrate/substrate/issues/553)
- [Agent Substrate issue #783: image volume source](https://github.com/agent-substrate/substrate/issues/783)
