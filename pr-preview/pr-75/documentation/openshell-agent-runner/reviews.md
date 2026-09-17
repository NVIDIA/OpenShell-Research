---
title: Run reviews
description: Review code or technical writing, guide the reviewer, and interpret its result.
agent_markdown: true
---

# Run reviews

After [creating your profiles](index.md#2-create-your-profiles), choose the
reviewer that matches your input:

| Profile | Task | What it reviews |
| --- | --- | --- |
| `code-reviewer` | `review-repository` | A local project directory, including code, tests, and documentation |
| `technical-writing-reviewer` | `review-document` | A text document, such as a guide, design note, reference page, or blog post |

## Review a project

```bash
oar run ./profiles/code-reviewer \
  --task review-repository \
  --input ./my-project \
  --output ./code-review.json
```

The reviewer looks for concrete issues in correctness, security, complexity,
testing, and usability. Its instructions keep recommendations appropriate to
the project's purpose and guard against overengineering and scope creep.

`--input` accepts a directory; it does not require Git metadata. OAR uploads a
copy and makes it the agent's working directory. It does not select a PR diff
or synchronize changes back to your machine. Tests run only if the agent can
execute them with the tools, dependencies, and permissions available in the
sandbox; the review should state what it could verify.

## Review a document

```bash
oar run ./profiles/technical-writing-reviewer \
  --task review-document \
  --input ./design-notes.txt \
  --prompt-var context="Audience: engineers evaluating the proposed design." \
  --output ./writing-review.json
```

The reviewer assesses accuracy, clarity, completeness, organization, audience
fit, and usefulness. Use readable text such as Markdown or `.txt`. OAR uploads
the file as provided; it does not convert PDFs or Word documents to text.

## Set focus and context

Both reviewers accept two optional prompt variables:

| Variable | Use it to explain… |
| --- | --- |
| `focus` | Which files, sections, behaviors, or questions deserve attention. |
| `context` | The audience, purpose, maturity, constraints, or non-goals. |

Add either or both to a run:

```bash
--prompt-var focus="src/auth and tests/auth" \
--prompt-var context="A small internal tool; keep recommendations proportionate."
```

Without these options, the reviewer considers the whole input. Focus guides
attention; it does not restrict file access or reduce the upload. To limit what
you share, supply a smaller input directory.

To include supporting files, use `--upload` and tell the reviewer where to find
them in the sandbox. [Upload paths](reference.md#upload-supporting-files)
explains how destinations work.

## Understand the result

Both reviewers write JSON with this structure:

```text
review.json
├── verdict           pass | needs_changes | inconclusive
├── summary           Main conclusion
├── criterion_scores  A score and explanation for each criterion
├── overall_score     Rounded average of the criterion scores
├── findings          Specific issues and recommended fixes
├── strengths         What works well
└── limitations       What the reviewer could not establish
```

Each criterion is scored from **0 to 100**, where 100 is best:

| Score | Meaning |
| --- | --- |
| 90–100 | Excellent; no material weakness |
| 75–89 | Strong; localized weaknesses |
| 60–74 | Needs substantive revision |
| 40–59 | Major or repeated weaknesses |
| 0–39 | Fails its purpose or, for code, is fundamentally unsafe |

Code review uses five criteria: correctness, robustness and security,
maintainability and complexity, tests and verification, and usability and
integration. Writing review uses six: accuracy, clarity, completeness,
structure, audience fit, and actionability. The profile's `skills/` directory
contains the full rubric; `schemas/review.json` defines the JSON fields.

The packaged reviewers require a score of at least 90 and no findings for
`pass`. A reported finding means `needs_changes`. Missing context can lead to
`inconclusive`, with the uncertainty recorded in `limitations`.

Read the evidence and limitations alongside the score. OAR checks that the
result matches the schema; it does not establish that the review is correct.
The rubric tells the agent how to calculate the average; OAR's schema validation
does not recalculate it. [CI behavior](reference.md#use-oar-in-ci) explains why
a valid review with findings still returns exit code `0`.
