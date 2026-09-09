---
title: Dependency License Checks
description: Automated checks for added and changed locked dependencies.
---

# Dependency license checks

The `Dependency licenses` workflow enforces the repository's dependency-license
policy on every pull request. It compares committed lockfiles, looks up license
metadata for direct dependencies, and identifies additions and changes that
must satisfy the policy. It uses no agent, inference service, secrets, or write
permissions.

The policy source of truth is
`.github/dependency-license-policy.toml`. It contains the currently approved
open-source licenses and any reviewed metadata clarifications. Keep policy
details there rather than duplicating them in documentation.

## Repository coverage

The checker searches the complete tracked Git tree, regardless of folder. It
reads files from the committed base and head revisions, so untracked, ignored,
and uncommitted files are outside the check.

Supported lockfiles are:

- Python: `uv.lock` and uv script lockfiles (`*.py.lock`).
- JavaScript: npm v2/v3 `package-lock.json`.
- Rust: `Cargo.lock`.

The inventory includes external packages declared directly by each project,
including development, optional, and platform-specific direct dependencies.
Transitive packages and first-party project or workspace packages are excluded.

For a pull request, a package is checked when its locked name, version, source,
or lockfile location is added or changed. Removed packages do not fail the
check. The report still includes unchanged current direct dependencies so that
every run provides a repository-wide view. Only additions and changes affect
the status of an ordinary pull request.

Changed `pyproject.toml`, `package.json`, `Cargo.toml`, and PEP 723 inline-script
metadata must have a corresponding lockfile or belong to a declared locked
workspace. The workflow asks the native package manager to confirm that each
affected lock is current:

- uv projects: `uv lock --check`
- uv inline scripts: `uv lock --script SCRIPT --check`
- npm projects: `npm ci --ignore-scripts --no-audit --no-fund`
- Cargo projects: `cargo metadata --locked`

The npm command installs locked packages without lifecycle scripts. Cargo
metadata does not build project code. A compatible constraint edit does not
need to rewrite a lockfile when the native check accepts it.

The checker does not inventory vendored code, datasets, model weights,
container or system packages, unsupported package managers, or dependencies
that are absent from a supported lockfile. A passing check is therefore not a
complete legal clearance.

## Policy and unresolved results

The policy approves a maintained set of open-source licenses. Consult
`.github/dependency-license-policy.toml` for the current policy. License metadata
that is missing, ambiguous, malformed, or not approved fails the check. Registry
outages are unresolved rather than being treated as successful lookups. An
unresolved addition or change fails the pull request; an unresolved unchanged
dependency contributes no license to the report.

Public metadata comes from PyPI, npm, or crates.io. Private registries, Git
dependencies, and other unsupported source forms require reviewed, exact-source
clarification.

If metadata is incomplete, a maintainer can add a `[[clarification]]` entry with
`ecosystem`, `name`, `version`, `source`, `license`, and an HTTPS `evidence` URL.
Use the exact identity reported by the checker and verify the authoritative
license evidence. A clarification must still satisfy the approved policy; it
cannot grant an exception for an unapproved license.

PR checks use the base branch's approved list when one exists. A proposed
allowlist expansion cannot approve its own dependencies. Workflow and checker
changes still require maintainer review because the workflow cannot protect
itself from changes made in the same pull request.

## Results and remediation

The workflow publishes the `Check dependency licenses` status and uploads a
compact `dependency-licenses.json` artifact with three fields:

- `folders` maps every scanned project folder to the lockfiles inspected there
  and its direct-dependency licenses.
- `licenses` maps every unique reported license value to the project folders
  that depend on it.
- `failures` maps affected project folders to the licenses that fail the check.
  Missing or unusable license metadata is reported as `unknown`.

The report contains no package-level records and does not list transitive
dependency licenses.

The lockfiles identify each direct dependency and its exact resolved version.
The reported license value comes from that version's package-registry metadata
or a reviewed policy clarification, not from the lockfile itself.

For a failure:

1. Confirm that the manifest and lockfile contain the intended dependency and
   source.
2. Check the package registry's exact-version metadata.
3. If that metadata is incomplete, add a narrowly scoped clarification backed
   by an authoritative HTTPS source.
4. Treat any policy change as a separate maintainer decision. Do not broaden the
   policy merely to make a check pass.

## Rollout and full audits

An ordinary pull request enforces policy only for direct dependency additions
and changes, while still reporting all current direct-dependency licenses.
Editing an existing policy triggers full enforcement, as does manually
dispatching the workflow without a base revision.

## Local checks

From the repository root, run the pinned command in the workflow's `Test checker`
step. Keeping the executable test command in the workflow provides one source of
truth for its dependencies and versions.

Compare committed revisions:

```sh
uv run --locked --script scripts/check_dependency_licenses.py \
  --base BASE_SHA --head HEAD_SHA --report /tmp/dependency-licenses.json
```

Omit `--base` for a full audit. To reproduce the base-branch policy constraint,
also pass `--approved-policy` with a copy of that branch's policy file.
The checker reads committed lockfiles, not uncommitted changes. Its own pinned
dependencies are recorded in `scripts/check_dependency_licenses.py.lock`.
