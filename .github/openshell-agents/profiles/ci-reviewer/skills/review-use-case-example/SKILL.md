---
name: review-use-case-example
description: Assess a new use case demonstration for a coherent, reproducible workflow that teaches its intended audience without unnecessary framework design.
---

# Review a use case example

Establish the intended user, concrete scenario, and useful outcome. Read the
complete documentation and trace its workflow from prerequisites through
configuration and execution to the expected output. Sample the main entry point,
configuration, and verification evidence to check that the major pieces plausibly
connect and that the demonstration includes its stated use of OpenShell. Do not
audit every supporting implementation file.

Assess whether a reader can distinguish required steps from optional variations,
understand inputs and outputs, and recognize a successful run. Cross-check key
commands and configuration against representative supplied implementation. Missing hardware,
paid services, or restricted data limit what can be verified; do not report a
broken workflow merely because those resources are unavailable in the sandbox.

Look for unsafe committed defaults, credentials, costly operations without
warning, and shortcuts presented as deployment-ready behavior. Keep the review
focused on this scenario. Do not demand reusable APIs, generalized abstractions,
multiple alternative implementations, or production infrastructure. A concise
working demonstration with honest boundaries can receive full marks.

## Rubric

Use these five criteria, in order:

1. `workflow_correctness`: the documented steps and sampled implementation
   connect coherently to deliver the stated outcome;
2. `reproducibility`: prerequisites, configuration, inputs, and commands support
   repeating the workflow;
3. `instructional_clarity`: the intended reader can understand and follow it;
4. `safe_configuration`: realistic security, side effects, and cost boundaries
   are handled and explained in documentation and representative configuration;
5. `scope_relevance`: the demonstration teaches a useful scenario without
   unnecessary complexity or unrelated features.

Apply the common score anchors to this demonstration, not an imagined product.
