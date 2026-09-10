# Assess the new project

Task: `{{ review_skill }}`. Load and follow `review-common` and
`{{ review_skill }}`. Use only the selected domain rubric.

Project: `{{ oar.input_path }}` (original name: `{{ oar.input_name }}`).
Trusted project guidelines: `{{ guidelines_path }}`.

Review focus: {{ focus }}

Operator context: {{ context }}

Read the trusted guidelines first, then assess the complete project: README,
implementation, configuration, tests, and relevant documentation. Determine
whether it delivers its claims, follows every applicable guideline, and uses
an appropriate level of engineering for its purpose. A diff supplies context;
it does not limit this assessment to particular files or lines. Read surrounding
repository evidence only where needed to assess this project, not to audit
existing projects.

Report guideline compliance explicitly in `guidelines_assessment`. Use
`pass` when all applicable requirements are supported, `needs_changes` for
demonstrated gaps, or `inconclusive` when material requirements cannot be
verified. Summarize the evidence and any inapplicable requirements; put specific
violations in findings, referring to guideline headings. Reflect a violation in
the relevant rubric criterion, without adding a sixth criterion or a duplicate
deduction. Unreadable or missing guidelines must yield an inconclusive guideline
assessment, never an assertion of compliance.

Use original repository-relative paths when supplied, not sandbox upload paths.
Disclose unavailable evidence and incomplete coverage. Do not edit source files
or publish comments. Finish by calling `submit_result` with the configured
result schema and `task` set to `{{ review_skill }}`. Correct rejected
submissions and submit again.
