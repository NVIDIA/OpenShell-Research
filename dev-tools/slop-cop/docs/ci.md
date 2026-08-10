# CI integration

The `Slop Cop` workflow analyzes Dev Notes and uploads `index.html` and
`report.json` as a 14-day artifact. The `Slop Cop report` workflow validates that
artifact and updates one sticky PR comment. Generated reports are not committed.

## Enforcement boundary

For a pull request, the required analysis runs Slop Cop code, configuration,
and dependencies from the PR base revision against the candidate Dev Notes.
Candidate Slop Cop changes are tested separately without credentials and scan
the complete candidate Dev Note corpus as a non-authoritative preview.

The `pull_request` event can use workflow orchestration changed by the pull
request. Base-revision analyzer selection does not protect that orchestration by
itself. Enforce `.github/workflows/slop-cop.yml` as an organization or
repository-ruleset required workflow using its default-branch definition. Add
both Slop Cop workflow files to the repository's protected workflow paths and
require designated review for changes to them. A branch-protection check name
without this ruleset protection does not establish the same boundary.

The analysis workflow has read-only permissions. It runs for every PR so the
required check is present even when no Dev Note changed. A non-applicable run
has a null score and creates no misleading clean score.

The trusted reporting workflow runs from the default branch after analysis. It
never executes PR-supplied code. It accepts exactly one artifact associated with
the completed run, checks the PR number and current head SHA, revalidates any
override through the GitHub API, rejects unknown JSON schemas and oversized or
malformed data, and treats every report string as untrusted before creating
Markdown. Stale runs do not update the comment.

## Artifact and comment

The artifact is named `slop-cop-pr-<number>-<head-sha>` and contains:

```text
slop-cop-report/
├── index.html
└── report.json
```

Download the artifact from the sticky PR comment or the analysis run. Open
`index.html` locally; it is self-contained and requires no network or
JavaScript. The PR comment shows the decision, threshold, per-file scores, base
deltas, top findings, override details, analyzed revision, and expiration note.

## Suppress one finding

Place a standalone directive immediately before the next scanned prose block:

```html
<!-- slop-cop: ignore-next=rhetoric.not-just reason="Contrasting two named API contracts" -->
```

Name stable rule IDs and supply a concrete reason. The directive suppresses the
first named finding in that block. Unknown IDs, missing reasons, unused
directives, and file-wide wildcards are errors. Suppressions remain visible in
terminal, JSON, and HTML output.

## Override one revision

An authorized maintainer can override a result by approving the exact current
head revision with this line in the review body:

```text
Slop-Cop-Override: <reason for accepting the findings>
```

The reviewer must currently have write, maintain, or admin permission. A new
commit makes the review stale. The override changes the CI decision to
`OVERRIDDEN`; it does not change the score, hide findings, or mark an incomplete
analysis complete. The report records the reviewer, reason, review, and head
revision.

## External rules

Built-in and declarative rules are offline. A configured custom rule can send
selected projected prose to its named service. Required external rules fail
closed when a credential or service is unavailable. Ordinary Actions secrets
are unavailable to fork PR workflows, so enabling a required external rule
must account for that behavior. The workflow never executes candidate rule code
with repository secrets.

## Required check rollout

The introducing PR has neither a base-revision analyzer nor a default-branch
required-workflow definition, so it cannot enforce itself. Validate it with the
complete local suite and designated workflow review. After merge, configure the
ruleset required workflow and protected workflow paths, require a successful
full-corpus push run, then exercise passing, failing, stale, override, rename,
deletion, and non-applicable PR revisions before treating the check as enforced.
