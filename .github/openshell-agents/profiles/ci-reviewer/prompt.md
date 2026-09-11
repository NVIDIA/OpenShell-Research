# Assess the new project

Task: `{{ review_skill }}`. Load and follow `review-common` and
`{{ review_skill }}`. Use only the selected domain rubric.

Project: `{{ oar.input_path }}` (original name: `{{ oar.input_name }}`).
Trusted project guidelines: `{{ guidelines_path }}`.

Review focus: {{ focus }}

Operator context: {{ context }}

This is a bounded project-overview review, not an exhaustive code review. Read
the trusted guidelines first. Inventory the project tree, then read the complete
root README and every human-authored text document that serves as project
documentation: all README files, the project's `docs/` tree, and root or nested
guides such as contributing, security, architecture, deployment, and example
instructions. Inventory non-text documentation assets. Do not skim or sample
the required text documentation.

Use the inventory and documentation to explain what is being contributed, who
it serves, how its major pieces fit together, how a user starts, what evidence
supports it, and which limitations are disclosed. Inspect manifests, lockfiles,
licenses, example environment files, primary entry points, representative
configuration, and a small sample of implementation and tests only as needed to
check that documented claims and project structure are credible. Do not read
every source file, trace every branch, perform a line-by-line audit, or run a
broad test suite. A compact change summary supplies context; it does not require
reviewing every changed line. Do not audit existing projects.

Judge project-level coherence, documentation, integration readiness, evidence,
and applicable guidelines. A pass means the documented project overview and
representative evidence have no demonstrated material gap; it does not certify
all implementation details. Put unexamined implementation and unrun checks in
`limitations`. Use `inconclusive` when the required documentation or a material
project-level claim cannot be responsibly assessed within this bounded scope.

Report guideline compliance explicitly in `guidelines_assessment`. Use
`pass` when all applicable requirements are supported, `needs_changes` for
demonstrated gaps, or `inconclusive` when material requirements cannot be
verified. Summarize the evidence and any inapplicable requirements; put specific
violations in findings, referring to guideline headings. Reflect a violation in
the relevant rubric criterion, without adding a sixth criterion or a duplicate
deduction. Unreadable or missing guidelines must yield an inconclusive guideline
assessment, never an assertion of compliance.

Use original repository-relative paths when supplied, not sandbox upload paths.
Disclose unavailable evidence, representative sampling, and unrun checks. Keep
the summary concise and lead with the contribution's big picture. Do not edit
source files or publish comments. Finish by calling `submit_result` with the
configured result schema and `task` set to `{{ review_skill }}`. Correct
rejected submissions and submit again.
