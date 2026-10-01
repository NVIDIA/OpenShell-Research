# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Client-visible operating instructions for the two independent MCP services."""

ORDERED_WORKFLOW = """For the ordered policy-review workflow, first call check_policy_boundary
with complete candidate YAML text, not a filename or patch. Continue to review_delegation only
when prover status is complete AND within_boundary is true. Pass exactly the same YAML bytes
to both tools and compare candidate_sha256 before combining their reports. After a policy edit,
rerun the prover. The caller owns this ordering; neither server calls the other or enforces
the cross-server gate. Neither server edits, activates, or approves policies.
MCP isError indicates a tool/protocol failure, not a policy verdict. Read each report's status,
reason, and coverage even when isError is false. Malformed tool arguments can return an MCP
error instead of a report. These services use structuredContent with matching JSON text for
legacy clients. Boundary containment is not task fit; neither implies approval."""

PROVER_DESCRIPTION = """Check complete OpenShell candidate YAML against the configured
operator-owned boundary using the local openshell-prover executable. The caller cannot change
the boundary through this tool. Only status=complete AND within_boundary=true is a pass;
complete can also mean exceeds_boundary. Unresolved or adapter_error never passes. Inspect
counterexample, reason, and coverage. No TypeSafe API call is made. Temporary local policy
snapshots are used, but no installed policy is changed."""

JEV_DESCRIPTION = """Assess whether candidate permissions fit the exact delegated task and
documented runtime. Sends the complete parsed candidate/optional starting policy, task, and
context to the external TypeSafe JEV API, including unsupported policy fields. Requires operator
TYPESAFE_API_KEY configuration and may incur API charges. Do not supply secrets or sensitive
data without authorization. Assesses individual filesystem_policy.read_only/read_write entries
and supported api.github.com REST allow rules; consult coverage.unassessed for everything else.
Complete means assessed supported scope, not approval. Incomplete preserves uncertainty,
context gaps, or conflicts; individual findings may still have actionable_guidance=true.
Respect blocked_by and uncertainty; confidence is not policy safety. Starting policy is
comparison context, not the boundary. Optional custom questions add independent Choice answers
to the same API call. Diagnostic choices are hypotheses, not causal explanations or edit commands;
none_fit and insufficient_context are always server-added. This tool is not a containment proof."""
