# Robotics Policy-Prover Architecture

This demo is intentionally shaped like a future OpenShell runtime integration,
without depending on OpenShell internals yet.

## Runtime Boundary

The browser simulation plays the role of a physical actuator and operator view.
The agent proposes `ActionEnvelope` values. The policy-prover service accepts
those envelopes through `/api/decide`, runs bounded Z3 checks, and returns a
`ProverDecision` contract:

- `decision`: allow, deny, constrain, or require approval
- `violations`: invariant ids that failed
- `constraints`: narrower path, speed, or force bounds the executor must use
- `obligations`: runtime monitor rules the executor must enforce
- `counterexample`: proof-relevant coordinates for a denial
- `solver_ms`: decision latency

That boundary is the intended OpenShell transition point. In a later integration,
the planner agent can run inside an OpenShell sandbox, submit action envelopes to
an OpenShell-hosted policy-prover service, and receive the same decision contract before
any MCP tool, A2A delegation, sandbox capability, or physical actuator command is
allowed to execute.

## Cadence

Z3 is not a servo controller. The solver runs before each physical action or
short-horizon segment. The executor then enforces returned obligations every sim
tick. This mirrors an OpenShell runtime split:

- agent runtime: propose/replan
- policy prover: prove and return a contract
- executor/proxy: enforce contract and stream audit events

## Demo Surface

The transcript is the audit stream. Each `agent_plan`, `prover_decision`,
`execution_update`, and `world_event` is a future-compatible explanation surface
for OpenShell operators and agents.
