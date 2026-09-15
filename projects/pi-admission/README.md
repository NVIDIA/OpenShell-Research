# Standalone Pi admission

This use-case example runs unmodified Pi in an unmodified OpenShell sandbox and
keeps policy-denied content out of Pi's live conversation and saved JSONL. A
small Rust service makes both decisions:

1. Pi sends each candidate to authenticated HTTPS admission before publishing
   or saving it.
2. Admission allows it unchanged, replaces an `example.com` email with
   `[EMAIL]`, or denies an SSN-shaped value.
3. After the full provider context is approved, the service signs a short-lived
   receipt over its ordered user/tool text projection, destination, sandbox,
   and fixed policy identity.
4. OpenShell calls the same service as pre-credentials middleware. It verifies
   the receipt against the intercepted request, applies the policy again, and
   removes the private receipt header before provider credentials are attached.

Blocking only at step 4 would be too late: the denied text could already be in
the local transcript. The two checks protect different boundaries.

## Demonstration policy

The fixed policy is compiled once in
[`middleware/src/policy.rs`](middleware/src/policy.rs):

| Entity | Decision | Synthetic example |
| --- | --- | --- |
| Address ending in `@example.com` | Replace with `[EMAIL]` | `alice@example.com` |
| `NNN-NN-NNNN` digits | Deny | `123-45-6789` |

Denial is evaluated first. Patterns inspect decoded JSON strings, so JSON
escaping does not bypass them. Text block boundaries are preserved. Content in
executable tool arguments or protected reasoning metadata is denied instead of
rewritten. Egress never rewrites the provider body: it denies any remaining
matching entity.

These regexes are intentionally incomplete. They do not validate real email
addresses or SSNs and will have both false positives and misses. Use only
synthetic data with this example.

## Prerequisites

You need:

- an existing HTTPS/mTLS OpenShell gateway registered in the `openshell` CLI;
- OpenShell `0.0.116` (the pinned middleware contract) or a compatible release;
- Bash, Python 3.11+, uv 0.11+, Rust 1.90+, Docker, and Node 22 only for local
  harness development;
- a provider key with quota for a tool-capable OpenAI-compatible Chat
  Completions model.

The sample catalog uses OpenRouter. Provider use can incur normal model costs.
No keys are copied into the image or committed to this repository.

## Run it

From `projects/pi-admission/`:

```sh
cp .env.example .env
cp models.json.example models.json
# Set OPENSHELL_GATEWAY, PI_ADMISSION_HOST, and PI_MODEL_API_KEY in .env.
./demo.sh prepare
```

`PI_ADMISSION_HOST` must be a DNS name or IPv4 address reachable from both the
gateway and sandbox. Do not use `localhost` for container callers. Preparation
discovers the selected gateway's issuer and public Ed25519 key over verified
mTLS, generates a 30-day local service certificate, stages one selected native
Pi model, and builds `pi-admission:local`. Host-owned state is written to
`.workspaces/` with mode `0700`/`0600` defaults.

Run the service in one terminal:

```sh
./demo.sh serve
```

In another terminal:

```sh
./demo.sh register
./demo.sh setup
./demo.sh launch
```

`register` supports Homebrew and the DEB/RPM user service. It edits only the
`pi-admission` registration, records ownership, restarts the local gateway, and
refuses to overwrite or remove a registration that drifted. For remote or
custom gateway deployments, use `./demo.sh registration` and have the operator
merge the printed TOML and restart the gateway.

All actions have a side-effect-free print form which does not load `.env`:

```sh
./demo.sh --print prepare
./demo.sh --print serve
./demo.sh --print register
./demo.sh --print setup
./demo.sh --print launch
./demo.sh --print verify
./demo.sh --print cleanup
```

Try these inputs in Pi:

```text
Hello. Briefly describe what you can do.
Please repeat alice@example.com.
123-45-6789
/skill:review
/compact
/new
/quit
```

The email becomes `[EMAIL]` before it appears or is saved. The fictitious SSN
shape is rejected and never enters live history or JSONL. The skill reads the
synthetic `project/notes.txt`, exercising real tool continuation. Use `/session`
to locate the append-only JSONL under `/sandbox/sessions`.

Run the separate real-model acceptance workflow with:

```sh
./demo.sh verify
```

It exercises allow, replace, deny, a real bash tool result, explicit skill,
manual and automatic compaction, and a missing-receipt request. It requires a
running service, gateway, sandbox, and paid provider access; fixture tests do
not establish live provider compatibility.

When finished:

```sh
./demo.sh cleanup
```

Cleanup deletes the demo sandbox (including its sessions), provider instances
and profiles, and its owned gateway registration. It restarts the gateway and
retains generated host configuration and the Docker image. Stop `serve`
separately with Ctrl-C.

## Development checks

```sh
uv run ruff format --check .
uv run ruff check .
uv run pytest

cd middleware
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --locked

cd ../pi-harness
npm ci
npm run check
npm test
```

The middleware scaffold, protocol, and lockfile are managed by
`openshell-middleware-manager`; do not edit its protocol by hand.

## Supported scope and limitations

The harness keeps Pi's native TUI, sequential tools, tool continuations,
reasoning, project instructions, explicit skills, manual/automatic compaction,
model serialization, prompt-cache fields, and native session ownership. Allowed
native messages remain unchanged. Candidate buffers and queues are not history.

This POC is text-only and supports one prepared OpenAI-compatible Chat
Completions model. It blocks shell shortcuts, resume/import, branching,
renaming, live model switching, resource reload, and arbitrary extensions.
Unsupported request structures fail closed. It does not inspect across split
content blocks or decode arbitrary encodings. Opaque provider metadata is
preserved but is not claimed to be fully understood.

Receipts bind the ordered user/tool projection, not every byte or all
assistant/system history, and do not prove that the harness itself ran.
Tool-result admission cannot undo tool side effects. The history guarantee is
for this controlled harness, not compromised same-authority code. This is a
readable security example, not production DLP or identity validation.
