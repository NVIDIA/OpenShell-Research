# Repository agent profiles

This tool runs repository-owned agent profiles through OpenShell. A profile
selects a harness and an independent inference provider, then exposes one or
more related tasks with reviewed prompts, schemas, skills, and sandbox policy.

## Layout

```text
runner/              # owns launching, profile resolution, providers, and harnesses
profiles/            # owns agent identity, tasks, prompts, schemas, and policy
```

The first profile is `dev-note-reviewer`, with `editorial` and `technical`
tasks. Both use Pi, but neither the profile nor the inference provider is
coupled to GitHub Actions.

## Run with an ephemeral local gateway

Set the same model values used by CI:

```bash
export INFERENCE_BASE_URL=https://model-gateway.example/v1
export INFERENCE_API_KEY=replace-me
export MODEL_ID_TOP=model-id

dev-tools/agents/runner/run.sh \
  --profile dev-note-reviewer \
  --task editorial \
  --output /tmp/editorial-result.json \
  < /tmp/editorial-input.json
```

`runner/run.sh` keeps the OpenShell control flow visible. It calls the
standalone `runner/profile_resolver.py` to resolve and validate a profile,
stage its prompt and Pi configuration, and validate the response. The
resolver's two pinned dependencies are recorded in
`runner/profile_resolver.py.lock`; it is not a Python package. The default mode
downloads checksum-pinned OpenShell binaries, starts an ephemeral mTLS gateway,
attaches the selected provider, builds a disposable sandbox, and exits after
one Pi print-mode response.

For an existing gateway, pass `--gateway-endpoint URL` and optionally
`--openshell-bin PATH`. If model credentials are present, the launcher creates
or updates the selected provider. Without them, the named provider must already
exist on that gateway. `MODEL_ID_TOP` is always required.

Profiles may declare reviewed guidance paths relative to their own directory.
Use `--guidance PATH` to append a reviewed trusted guidance file for one run;
the option is repeatable. Standard input remains the untrusted task payload.

## Security model

- Runtime configuration, policy, and skills are path-checked and baked
  read-only into the image; the bounded assembled prompt is uploaded separately.
- Pi runs as an unprivileged user without sessions or automatic resources.
- Tools and skills are explicit task allowlists.
- The model URL and API key remain in the OpenShell gateway.
- The sandbox sees only `https://inference.local/v1`, a placeholder key, and
  the selected model ID.
- CI always uses `--no-keep`, a bounded prompt and response, and an explicit
  profile policy.

## Validate

```bash
cd dev-tools/agents/runner
uv lock --script profile_resolver.py --check
python3 -m unittest discover -s tests -v
uv run --with ruff==0.16.2 ruff check profile_resolver.py tests/test_profile_resolver.py
uv run --with ruff==0.16.2 ruff format --check profile_resolver.py tests/test_profile_resolver.py
python3 -m compileall -q profile_resolver.py tests
bash -n run.sh harnesses/pi/exec.sh
```

The tests are credential-free and do not launch a model or Docker sandbox.
