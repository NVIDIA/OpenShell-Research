# Demo workflow

1. Start the Prover MCP with `prover.toml`. It receives no TypeSafe key.
2. Start the JEV MCP with `jev.toml` and `TYPESAFE_API_KEY` in only that process.
3. The demo client submits the exact candidate bytes to `check_policy_boundary`.
4. It calls `review_delegation` only for `status: complete` and
   `within_boundary: true`.
5. It combines reports only when `candidate_sha256` matches.

An exceeding, unsupported, inconclusive, malformed, or unavailable prover result
is displayed with `jev.status: not_assessed`. Neither service edits or activates
the policy, and the runner does not spawn an agent.
