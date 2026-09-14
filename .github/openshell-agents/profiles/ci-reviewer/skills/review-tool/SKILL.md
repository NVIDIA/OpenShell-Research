---
name: review-tool
description: Assess a new reusable tool or library for correctness, usability, verification, and proportionate engineering.
---

# Review a new tool or library

Identify the advertised behavior, intended users, major components, and public
entry points from the README and documentation. Inspect the documented minimal
path from installation to useful output. Cross-check a representative CLI,
library API, service entry point, configuration, and test where present; do not
trace every caller or implementation path.

Consider documented security boundaries, failure handling, performance, and
dependency behavior where the project's purpose makes them relevant. Use
representative code and configuration to identify obvious contradictions or
unsafe defaults, not to certify the entire implementation. A missing test is a
finding when an important advertised behavior lacks credible verification, not
merely because implementation branches were not inspected.
Do not demand production architecture from a small utility, or treat every
exception as grounds for another fallback. Prefer a direct fix at the owning
boundary over new layers or generalized frameworks.

## Rubric

Use these five criteria, in order:

1. `correctness`: documented behavior and representative implementation evidence
   agree with the intended use;
2. `robustness_security`: realistic trust boundaries, failure modes, and unsafe
   defaults are documented and handled proportionately in sampled evidence;
3. `maintainability_complexity`: the project layout and major components have
   clear ownership and proportionate complexity;
4. `tests_verification`: advertised critical behavior has credible, discoverable
   verification, whether or not it was run in this review;
5. `usability_integration`: installation, interfaces, documentation, and intended
   integration form a coherent first-use path.

Apply the common score anchors to the new project at the tool's stated
maturity. Do not infer requirements for hypothetical consumers.
