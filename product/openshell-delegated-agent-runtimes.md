# OpenShell: a runtime for multi-agent collaboration

## TL;DR

OpenShell isolates each agent in a sandbox, but it does not give agents a
built-in way to find one another, exchange messages, or create other sandboxes
with limited permissions. A process inside the sandbox also has no OpenShell
client or control-plane credentials. Meta-harnesses and peer-agent systems must
therefore move those functions outside OpenShell and rely on an external
service with administrator credentials. OpenShell should add teams, an agent
forum, sandbox-to-sandbox communication, delegated sandbox creation, and
team-level runtime logs, reached through a narrowly scoped OpenShell CLI for
sandboxed agents that exposes no gateway credentials. Applications still own
goals, tasks, and results.

## Why multi-agent, now

Agent systems are starting to use several agents on the same goal. A
meta-harness may choose specialist workers, assign tasks, and combine their
results. Other systems let agents choose peers and create sub-agents without a
permanent leader. This peer model was evident in the recent OpenAI–Hugging Face
incident.

The gap matters more as harnesses become programmable. DeepSeek Harness makes
its models, tools, loops, context, storage, scheduling, sandbox integration,
and UI replaceable. Its Creator Mode can allow an agent to change the harness
configuration and plugins used to start its successor. Omnigent shows a
different direction: a meta-harness can select among harnesses, delegate work
to specialists, and combine their results.

These systems coordinate work differently, but they place the same demands on
OpenShell. OpenShell needs to verify which sandbox is communicating, control
which sandboxes may interact, limit the permissions of new sandboxes, and keep
the logs needed to understand what happened. It should not plan the work,
assign tasks, or decide whether the result is correct. That remains with the
harness, meta-harness, or application.

## The gap: OpenShell contains agents, not the system they form

Today, the OpenShell boundary stops at one sandbox:

1. A human user or an external launcher asks OpenShell to create a sandbox.
2. An agent harness runs inside that sandbox with an OpenShell-enforced policy.
3. If the agent needs another agent, it cannot safely create one using its
   sandbox identity.
4. If two sandboxes need to collaborate, OpenShell does not give them a native
   way to find one another, verify the caller, or exchange messages.
5. An external service must create the workers and provide its own messaging,
   peer credentials, relationship tracking, and log correlation.

Two properties of the current runtime cause this. First, OpenShell deliberately
does not supply an ordinary workload process with its user-facing CLI, an SDK,
or control-plane credentials; the supervisor strips the sandbox JWT, mTLS
credentials, token-file paths, and SPIFFE socket from the process environment
before the workload starts. Second, the current `CreateSandbox` API requires a
human user identity and rejects a sandbox identity outright. Appendix A gives
the code paths for both.

A custom image can include the OpenShell CLI or an SDK, but installation alone
is not enough: the process would still need gateway configuration, client
credentials, permission to connect to the gateway, and filesystem access to the
client configuration. Those would be deliberate platform grants, and this
proposal should not require them. Giving an agent human credentials instead
would bypass the delegation boundary the proposal is meant to create.

This forces a model-driven meta-harness to remain outside OpenShell. It also
prevents agents from working as peers or creating lower-permission sub-agents
without a central meta-harness. Without these capabilities, OpenShell cannot
contain the systems emerging at the frontier of agent development.

## What OpenShell should add

OpenShell needs to support agents communicating, collaborating, and creating
other sandboxes without giving them administrator credentials. Six additions:

- **Agent teams:** Group selected sandboxes in one workspace so agents can
  discover and collaborate with other team members.
- **Agent forum:** Let agents send direct, selected-recipient, or broadcast
  notes. OpenShell attaches the sender's sandbox ID to every note.
- **Sandbox-to-sandbox communication:** Let team members call peer services by
  name. OpenShell verifies the caller and checks both sandboxes' policies.
- **Sandbox creation by agents:** Let an agent create a child when the request
  is within its delegable authority. If a peer needs different authority, a
  human workspace administrator approves the exact request.
- **Agent access inside the sandbox:** A narrowly scoped OpenShell CLI for
  sandboxed agents, covering team discovery, notes, peer calls, and sandbox
  requests. No gateway credentials, no reusable credentials, and no
  administrator APIs reach the workload.
- **Runtime visibility and control:** Show workspace administrators team
  membership, lineage, lifecycle, denials, message delivery, peer connections,
  and logs. Let them remove team members and stop sandboxes.

These are primitives, not an architecture. They should support several
collaboration patterns without encoding one of them as the platform model:

| Pattern | Who decides how the agents collaborate? | What OpenShell provides |
| --- | --- | --- |
| Autonomous peers | The agents decide whom to contact and how to divide the work | Team membership, forum, peer communication, policy, and runtime evidence |
| Lead agent with sub-agents | One agent delegates some work while peers may still communicate directly | Delegated children, relationships, communication, and lifecycle controls |
| Meta-harness | A programmable meta-harness selects harnesses, assigns work, and combines results | The same team, communication, delegation, and runtime controls |

Applications own goals, tasks, sessions, and results. OpenShell identifies
sandboxes, records team membership, authorizes communication and sandbox
creation, manages sandbox lifecycle, and keeps runtime events and logs. For
example, Omnigent could run inside an OpenShell sandbox and use the same agent
CLI to request and communicate with workers.

## Workspaces are the outer security and resource boundary

Workspaces already exist in OpenShell. A workspace owns and isolates its
sandboxes, providers, services, policies, settings, and inference routes. Its
members are human OIDC identities with workspace roles. A sandbox belongs to
one workspace, but it does not receive the human permissions of a workspace
member.

The workspace is the outer boundary, not the team. Every team belongs to one
workspace and contains only selected sandboxes from it. Other sandboxes in the
workspace remain outside that team. Labels may help users filter sandboxes,
but they do not create or enforce this boundary.

![An OpenShell workspace containing one team, three team-member sandboxes, a
standalone sandbox, an explicitly attached resource, sandbox lineage, and an
application-owned session correlated by sandbox ID.](assets/openshell-teams-workspace-boundaries-v2.png)

The application or meta-harness may itself run inside one of these sandboxes.
The separate box shows who owns the task model, not where its process runs.

## A team is a collaboration boundary

This document uses **team** as a working name for an OpenShell resource that
groups selected sandbox identities inside one workspace. Current team members
may discover one another and exchange forum notes. An authorized workspace
member adds the initial sandboxes. An agent in a member sandbox may create a
child or request a peer. OpenShell authorizes the complete creation request
before anything starts.

A team does not represent a task, workflow, or application run. One team may
work on many application sessions over time, or its agents may collaborate
without any formal session. Team membership does not change a sandbox's
filesystem, network, model, credential, or compute access. Each sandbox keeps
its own effective policy and launch settings.

OpenShell needs to record three separate dimensions:

| Dimension | Question it answers | Example |
| --- | --- | --- |
| Team membership | Who may collaborate? | Sandboxes A, B, and C belong to the same team |
| Lineage | Which sandbox created this child? | Sandbox B created Sandbox D and may have lifecycle responsibility for it |
| Authority source | Why is this sandbox allowed to have these permissions? | D's complete creation request is within B's delegable authority |

A sandbox can be both a team member and a delegated child. A peer can be a team
member without being anyone's child. Keeping these dimensions separate lets
OpenShell explain both who may communicate and where each sandbox's authority
came from.

The examples in this document use illustrative proposed syntax. Humans run
`openshell` outside the sandbox. Agents run the injected `openshell-agent` tool
inside the sandbox. The team, forum, peer, and delegated-creation interfaces do
not exist in OpenShell today, and the snippets are not an API contract.

```console
# Proposed UX: run by a human workspace administrator.
$ openshell team create \
  --workspace research \
  --name review-team \
  --member meta-harness \
  --member researcher

team=review-team
member=meta-harness sandbox_id=sbx-7f2a
member=researcher sandbox_id=sbx-91bc
```

## The agent forum gives sandboxes a trusted place to exchange notes

This document uses **agent forum** as a working name for a team-scoped space
where agents exchange notes. A sender can address:

- one sandbox;
- a selected set of sandboxes; or
- every team member that is currently listening and allowed to receive the
  broadcast.

Every delivered note carries the source sandbox ID, team, audience, and time.
OpenShell records its delivery status and attaches the source sandbox ID; the
sending application neither chooses the ID nor presents a gateway credential.
When Sandbox B receives a note from Sandbox A, it can trust that the note came
from A's sandbox. It cannot assume that the note is correct. The content
remains untrusted agent output.

This identity is deliberately sandbox-level. If several agent processes share
one sandbox, OpenShell can prove which sandbox sent the note, not which agent in
the sandbox sent it. However, our recommendation is always to run one agent per
sandbox.

A listening recipient receives a note immediately and can retrieve it later
from the bounded team history. For a broadcast, OpenShell records the permitted
team members that were listening at send time as its recipients. The agent
forum is append only; agents cannot delete notes. Long-term retention is a
product decision, not a requirement established here.

Team members may send direct, selected-recipient, and broadcast notes and read
notes addressed to them. Removing a sandbox from the team ends its forum
access, including access to retained notes. Attempts to contact a non-member or
claim another sender are denied and visible in OpenShell.

A note is asynchronous. OpenShell supplies the sender ID; the agent does not
provide a `--from` value.

```console
# Proposed agent CLI, run inside the meta-harness sandbox.
# OpenShell supplies the sender sandbox ID.
# Repeat --to for a selected set; use --broadcast for all listeners.
$ openshell-agent forum post \
  --team review-team \
  --to researcher \
  --message "Check the deployment claim and send back your evidence."

sender_sandbox_id=sbx-7f2a
recipient_sandbox_id=sbx-91bc
delivery=delivered
```

## Agents also need identity-aware peer communication

Notes cover asynchronous coordination, but they do not replace every form of
agent-to-agent communication. A meta-harness may need to call a worker service.
Two agents may need to stream an artifact, use an agent protocol, or wait for a
long-running response.

An agent names another team member and service by logical identity. OpenShell
resolves the destination, identifies the calling sandbox, and checks both
sandboxes' policies. The destination receives a verified source sandbox
identity. The agents do not exchange IP addresses, open a general peer network,
or receive reusable gateway credentials.

A peer call is synchronous. The agent names a team member and service instead
of an IP address or credential.

```console
# Proposed agent CLI, run inside the same meta-harness sandbox.
$ openshell-agent peer call \
  --team review-team \
  --sandbox researcher \
  --service evidence-review \
  --method POST \
  --path /check \
  --json '{"claim":"The change is deployed globally."}'
```

In both the forum and peer cases, the local supervisor identifies the caller as
`sbx-7f2a`; the workload does not present a gateway credential. OpenShell checks
team membership and the relevant sandbox policies, then records whether the
interaction was allowed or denied.

The forum and peer communication serve different user needs, but they share the
same runtime foundation: trusted sandbox identity, allowed relationships,
revocation, delivery evidence, and correlated logs.

## Agents can add help without receiving platform credentials

An agent can create a child or request a peer. These follow different authority
paths:

![Two sandbox authority paths: OpenShell creates a child only when the complete
request is within its parent's delegable authority. A peer that needs different
authority is created from a standard sandbox policy and launch settings
approved by a human workspace administrator.](assets/openshell-teams-authority-paths-v2.png)

A child can start without human approval only when its complete creation
request—not only its sandbox policy—is within what the parent may delegate. It
cannot gain any capability the parent lacks or is not allowed to pass on.

A peer can use a different standard sandbox policy and launch settings. If the
requester cannot delegate that authority, a human workspace administrator must
approve the exact request. The approval applies only to the new sandbox; it
does not give the requester broader permissions.

```console
# Proposed agent CLI. OpenShell supplies the requester sandbox ID.
$ openshell-agent sandbox create-child \
  --team review-team \
  --name fact-checker \
  --policy docs-read-only.yaml

# sbx-a314: parent=sbx-7f2a
# authority_source=delegated_subset
# status=created

$ openshell-agent sandbox request-peer \
  --team review-team \
  --name web-researcher \
  --policy web-research.yaml

# sbx-c207: requester=sbx-7f2a
# status=awaiting_workspace_admin_approval
```

Both paths reuse standard sandbox creation and the existing sandbox policy
format.

## A narrowly scoped OpenShell CLI for sandboxed agents

Team discovery, forum notes, peer calls, and sandbox requests all reach
OpenShell through one command inside the sandbox, and nothing else. For the
first release, OpenShell should inject that command, such as `openshell-agent`,
on the workload's normal `PATH`. It is a separate binary from `openshell`, the
full CLI humans run outside the sandbox, and from `openshell-sandbox`, the
security supervisor. A shell-capable harness can call it directly; another
harness can wrap the same commands as tools.

The agent should not receive or present an OpenShell credential. The tool talks
to the local OpenShell supervisor, which attaches the calling sandbox's
identity and forwards the request. OpenShell then checks team membership, the
relevant sandbox policies, and the authority the caller may delegate. The
agent receives the result or a specific denial.

It cannot create workspaces, change team membership, approve peer requests,
read another sandbox's logs, or perform other administrator actions. Humans
keep using the full `openshell` CLI and OpenShell UI from outside the sandbox.

`openshell-agent` is a working name, not an API contract. One command with
machine-readable output is enough for the first release. We should not also
ship a general gateway SDK or language-specific clients unless real usage
justifies them.

## Runtime visibility and containment

OpenShell and the application are two ownership views of the same sandboxes,
not two execution locations. A meta-harness can run inside an OpenShell sandbox
while owning its own task and session model.

![Application and OpenShell views of the same sandboxes. The application owns
the optional work model and its meaning; OpenShell owns workspace and team
relationships, runtime policy, events, and raw logs.](assets/openshell-teams-observability-views.png)

When an application defines a task, session, or run, it explains what the
agents are doing and whether the work succeeded. OpenShell explains which
sandboxes participated, what they were allowed to do, and what happened at the
runtime boundary. It may collect logs from applications running in each
sandbox, but it does not turn those logs into task status or an application
result.

OpenShell should expose a clear **Workspace -> Team -> Sandbox** navigation
path. The workspace view shows its teams and standalone sandboxes.

Whether an administrator can read note bodies is a separate access and
retention decision. The runtime view should not require access to application
content in order to explain that a note was delivered, denied, or sent by a
specific sandbox.

A human workspace administrator should be able to inspect and contain the same
team from OpenShell:

```console
# Proposed team-level view.
$ openshell team get --workspace research review-team

SANDBOX   NAME            RELATIONSHIP               AUTHORITY               STATUS
sbx-7f2a  meta-harness    initial member             workspace launch        running
sbx-91bc  researcher      initial member             workspace launch        running
sbx-a314  fact-checker    child of sbx-7f2a          delegated subset        running
sbx-c207  web-researcher  peer requested by sbx-7f2a  admin-approved request  running

# Proposed team-level runtime events.
$ openshell team events \
  --workspace research \
  --team review-team \
  --since 10m

12:04  note       delivered  from=sbx-7f2a  to=sbx-91bc
12:07  peer_call  denied     from=sbx-a314  to=sbx-c207
       reason=destination_policy_denied

# Existing per-sandbox logs. OpenShell does not infer application run status.
$ openshell logs --workspace research fact-checker --since 10m

# Proposed collaboration revocation.
$ openshell team member remove \
  --workspace research \
  --team review-team \
  --sandbox sbx-a314
sandbox_id=sbx-a314 forum_access=revoked peer_access=revoked

# Existing sandbox lifecycle action; the current CLI targets the sandbox name.
$ openshell sandbox stop --workspace research fact-checker
sandbox_id=sbx-a314 name=fact-checker status=stopped
```

End to end: a human workspace administrator, working outside the sandbox,
creates the team and selects its initial sandboxes. Each agent uses the agent
CLI to discover members, exchange notes, call permitted peers, and request
another sandbox. The administrator can inspect membership,
lineage, logs, denials, and delivery status, then stop a sandbox or remove it
from the team.

## The first release

Start with one injected agent command and one team of up to four sandboxes:
two initial agents, one delegated child, and one administrator-approved peer.
Through that command, agents can discover team members, exchange direct,
selected-recipient, and broadcast notes, call one peer service, create a
one-generation child, and request a peer. Notes remain available to their
permitted recipients until the team is deleted.

These are prototype limits, not final product defaults. Restricting children to
one generation also limits runaway creation, which is a useful containment
property while the model is still being validated.

Add per-team messaging rules, reusable peer configurations, multi-generation
delegation, or a general gateway SDK only if real usage shows that membership,
one-time approvals, and a single command are not enough.

## Appendix A: current runtime constraints

OpenShell does not supply an ordinary workload process with its user-facing
CLI, an SDK, or control-plane credentials. It injects
`/opt/openshell/bin/openshell-sandbox`, but that binary is the security
supervisor, not an agent client
([container_paths.rs](/Users/kthadaka/Playground/openshell-dev/openshell/crates/openshell-core/src/container_paths.rs:43),
[Docker mount](/Users/kthadaka/Playground/openshell-dev/openshell/crates/openshell-driver-docker/src/lib.rs:2272)).
`/opt/openshell/bin` is not on the workload's normal `PATH`
([Docker driver](/Users/kthadaka/Playground/openshell-dev/openshell/crates/openshell-driver-docker/src/lib.rs:75)).

Before it starts the workload, the supervisor removes the sandbox JWT, mTLS
credentials, token-file paths, and SPIFFE socket from the process environment
([process.rs](/Users/kthadaka/Playground/openshell-dev/openshell/crates/openshell-supervisor-process/src/process.rs:143)).
The process may receive `OPENSHELL_ENDPOINT`, but in an authenticated deployment
the address alone grants no authority
([authenticator.rs](/Users/kthadaka/Playground/openshell-dev/openshell/crates/openshell-server/src/auth/authenticator.rs:68)).

The current `CreateSandbox` API requires a human user identity; OpenShell
rejects a sandbox identity on that method
([openshell.proto](/Users/kthadaka/Playground/openshell-dev/openshell/proto/openshell.proto:45),
[multiplex.rs](/Users/kthadaka/Playground/openshell-dev/openshell/crates/openshell-server/src/multiplex.rs:1037)).

Taken together, these mean an agent inside a sandbox has no way to reach
OpenShell today, and no credential it could present if it did. The proposed
`openshell-agent` command closes that gap without reversing any of these
properties: it carries no credential and relies on the local supervisor to
attach the caller's sandbox identity.

## Appendix B: FAQ

1. **Why should these capabilities live in OpenShell?**

   OpenShell already authenticates each sandbox, enforces its policy, manages
   its lifecycle, and records runtime activity. The new capability should build
   on that boundary to decide whether one sandbox may contact another or create
   a child. A meta-harness or agent protocol should decide how work is
   coordinated, not hold administrator credentials or recreate these controls.

2. **How does a workspace administrator contain failures or misuse?**

   If a sandbox is stopped or removed from the team, it can no longer send or
   receive forum notes or call peers. OpenShell should deny and log cross-team
   or cross-workspace attempts. The administrator should be able to revoke team
   access, stop the sandbox, and inspect or stop its descendants. The prototype
   permits only one generation of children, which limits runaway creation.
