# Reviewer profile QA

This suite exercises the packaged reviewer profiles through the real OAR CLI.
Every live case gets a fresh host session directory and OAR creates a fresh
OpenShell sandbox. The suite initializes packaged profiles, renders runtime
prompt variables, uploads the declared input, runs Pi, validates the submitted
JSON, checks profile-specific semantics, verifies the host fixture was unchanged,
and checks for leaked OAR sandboxes.

The cases cover clean and defective inputs, `.md` and `.txt` documents, default
and multiple prompt variables, focused repository review, explicit non-goals,
genre calibration, intentional repetition, and prompt-injection attempts.

Run all live experiments against an existing gateway and inference route:

```bash
uv run python qa/reviewer_profiles/runner.py \
  --gateway openshell \
  --model provider/model \
  --report qa/reviewer_profiles/report.html
```

Use a specific compatible OpenShell CLI without changing the system install:

```bash
uv run python qa/reviewer_profiles/runner.py \
  --openshell-bin /path/to/openshell \
  --gateway openshell \
  --model provider/model
```

Run the CLI initialization, profile validation, and resolved-command checks
without creating sandboxes:

```bash
uv run python qa/reviewer_profiles/runner.py \
  --mode dry-run \
  --model qa/model
```

The command always writes an HTML report. Live preflight failures mark cases as
`blocked`, not failed, so an unavailable gateway or inference route cannot be
mistaken for a profile defect. Use `--case CASE_ID` repeatedly to run a subset.
