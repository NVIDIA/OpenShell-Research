---
title: Dependency License Checks
description: Automated checks for added and changed locked dependencies.
---

# Dependency license checks

The `Dependency licenses` workflow runs on every pull request. It compares the
base and head lockfiles, looks up the exact versions of added or changed packages,
and checks their reported licenses against
`.github/dependency-license-policy.toml`. It uses no agent, inference service,
secrets, or write permissions.

The workflow returns a pass/fail check and uploads `dependency-licenses.json`.
Maintainers can make `Check dependency licenses` a required check in repository
rules. Adding this workflow does not change those rules automatically.

## Coverage

Supported lockfiles are:

- Python: `uv.lock` and uv script lockfiles (`*.py.lock`).
- JavaScript: npm v2/v3 `package-lock.json`.
- Rust: `Cargo.lock`.

Checks cover the external packages recorded in those files, including transitive,
development, optional, and platform-specific dependencies. A changed version or
source is checked again. Removing a dependency does not fail the check.
New dependency manifests need a corresponding lockfile or membership in a
declared locked workspace. Unchanged inventories are not a full-repository audit.

The workflow also validates affected project locks with their package managers:
`uv lock --check`, `npm ci --ignore-scripts --no-audit --no-fund`, or
`cargo metadata --locked`. The npm check installs locked packages without
lifecycle scripts; Cargo metadata does not build project code. A compatible
constraint edit need not rewrite a lockfile if the native check accepts it.

This is not a complete inventory of vendored code, datasets, model weights,
container/system packages, or unsupported package managers. Passing the check
does not constitute complete legal clearance.

## Policy and unresolved results

The approved identifiers are `Apache-2.0`, `BSD-2-Clause`, `BSD-3-Clause`, `ISC`,
`MIT`, and `MIT-CMU`. Bare `BSD` is ambiguous and is not an approved identifier.

SPDX `OR` expressions pass when at least one alternative is approved; `AND`
requires every term. A `WITH` combination must be approved explicitly as a whole.
Unknown, malformed, missing, or unapproved license metadata fails visibly.
Registry outages also fail as unresolved, not as successful checks.

Public metadata comes from PyPI, npm, or crates.io. Private registries, Git
dependencies, and other unsupported source forms require reviewed, exact-source
clarification. First-party Cargo workspace/path packages are not registry
dependencies.

If metadata is incomplete, a maintainer can add a `[[clarification]]` entry with
`ecosystem`, `name`, `version`, `source`, `license`, and an HTTPS `evidence` URL.
Use the exact identity reported by the checker and verify the authoritative
license evidence. A clarification must still satisfy the approved list; it is
not an exception permitting an unapproved license.

PR checks use the base branch's approved list when one exists. A proposed
allowlist expansion cannot approve its own dependencies. Workflow and checker
edits still require maintainer review; this is not tamper-proof against authors
who can rewrite the check itself.

## Rollout and full audits

When introducing this policy, the PR check covers additions and changes, not
unchanged dependencies across the repository. Later edits to the existing policy
trigger a full inventory audit. Manual workflow dispatch also audits the full
inventory. Existing dependencies may need separate remediation or explicit
policy decisions; do not add blanket approvals to hide those results.

## Local checks

From the repository root, run the isolated unit tests:

```sh
uv run --python 3.12 --with license-expression==30.4.4 --with boolean.py==5.0 \
  python -m unittest discover -s tests -p test_dependency_licenses.py
```

Compare committed revisions:

```sh
uv run --locked --script scripts/check_dependency_licenses.py \
  --base BASE_SHA --head HEAD_SHA --report /tmp/dependency-licenses.json
```

Omit `--base` for a full audit. To reproduce the base-branch policy constraint,
also pass `--approved-policy` with a copy of that branch's policy file.
The checker reads committed lockfiles, not uncommitted changes. Its own pinned
dependencies are recorded in `scripts/check_dependency_licenses.py.lock`.
