# OpenShell Robotics Prover Demo

This is a small robotics demo for showing how an AI agent and a formal policy prover can work together.

For the experiment, architecture, and DGX Spark latency results, read the
[Dev Note](../../docs/dev-notes/posts/2026-08-07-formal-methods-ai-generated-robot-actions.md).

The scene is simple on purpose: an agent proposes a tool-head path to pick up the green block and move it into the blue tray. Before the robot can move, the policy-prover service checks the proposed path against hard rules:

- the path must stay inside the workspace
- the path must not enter the red restricted zone
- nearby humans clamp the allowed speed
- approved moves emit runtime obligations

Rust derives concrete geometry and state facts, and Z3 selects exactly one
policy outcome: allow, deny, allow with constraints, or require approval. If the
path violates a rule, the service returns invariant ids, counterexample
coordinates, constraints, obligations, and solver latency. The agent can use
that packet to replan, but only an allowed outcome can reach the executor.

The executor applies returned speed and force limits to the effective action and
checks the human-distance pause obligation again immediately before motion.
Malformed envelopes, solver failures, denials, and approval-required outcomes
fail closed.

The point of the experiment is to make the OpenShell runtime idea visible: an autonomous agent may plan freely, but every tool, delegation, or physical action can be checked against a policy-prover contract before execution.

## Run It

Install dependencies:

```shell
npm install
```

Start the policy-prover service:

```shell
npm run dev:prover
```

In another terminal, start the web app:

```shell
npm run dev -- --port 5173
```

Open:

```shell
http://localhost:5173/
```

Click **Run Experiment**.

## Agent Modes

The app can run in two modes:

- **Fixture**: deterministic and repeatable; good for presenting the demo.
- **OpenAI-compatible**: uses `OPENAI_API_KEY` and the configured model endpoint
  from `.env`; good for showing real model variability. The event stream records
  the exact model setting and request latency for each successful plan.

Copy the sample env file if you want to use the OpenAI-compatible mode:

```shell
cp .env.sample .env
```

Then fill in your local values. Do not commit `.env`.

## Test

```shell
cargo test --manifest-path policy-prover-service/Cargo.toml
npm run build
npm run verify:visual
```

## Benchmark the Policy Decision

The benchmark calls the in-process policy decision directly in a release build.
It covers allow, deny, and constrained outcomes at 3, 6, 12, 24, and 48
waypoints. It intentionally excludes model inference, HTTP transport, rendering,
and robot execution. This is an action-admission benchmark for agent-generated
plans, not a benchmark for servo, balance, torque, or other low-level robot
control loops.

```shell
POLICY_PROVER_BENCHMARK=1 \
POLICY_PROVER_BENCHMARK_SAMPLES=5000 \
POLICY_PROVER_BENCHMARK_WARMUP=1000 \
POLICY_PROVER_BENCHMARK_OUTPUT=benchmarks/policy-latency.json \
cargo run --release --manifest-path policy-prover-service/Cargo.toml
```

The command writes p50, p95, p99, mean, minimum, maximum, and throughput for
each case. Add `POLICY_PROVER_BENCHMARK_PLATFORM` and
`POLICY_PROVER_BENCHMARK_REVISION` when publishing results so the run has useful
provenance.
