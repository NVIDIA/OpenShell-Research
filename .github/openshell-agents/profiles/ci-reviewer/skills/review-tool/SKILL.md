---
name: review-tool
description: Assess a new reusable tool or library for correctness, usability, verification, and proportionate engineering.
---

# Review a new tool or library

Identify the advertised behavior, public entry points, and actual callers.
Trace behavior through its relevant contracts and tests. Inspect the documented
minimal path from installation to useful output. For a library, assess the
consumer's import/API path; a CLI or hosted service is not required.

Consider security, failure handling, performance, and dependency behavior where
the change makes them relevant. A missing test is a finding when an important
behavior lacks credible verification, not merely because a branch exists.
Do not demand production architecture from a small utility, or treat every
exception as grounds for another fallback. Prefer a direct fix at the owning
boundary over new layers or generalized frameworks.

## Rubric

Use these five criteria, in order:

1. `correctness`: behavior satisfies its contracts and intended use;
2. `robustness_security`: realistic failure modes and trust boundaries are
   handled proportionately;
3. `maintainability_complexity`: ownership is clear and complexity earns its cost;
4. `tests_verification`: critical behavior has credible verification;
5. `usability_integration`: installation, interfaces, documentation, and callers
   work coherently where applicable.

Apply the common score anchors to the new project at the tool's stated
maturity. Do not infer requirements for hypothetical consumers.
