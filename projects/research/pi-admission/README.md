# Minimal Pi admission spike

This research spike shows why content policy must integrate at the agent
harness—not only at network egress. It runs an unmodified Pi coding session in
OpenShell with two boundaries:

```text
draft -> admission HTTPS -> Pi history and JSONL -> provider request
                    receipt ^                    |
                            +-- OpenShell middleware
```

The launcher admits a complete user, assistant, or tool-result candidate before
publishing it. The external Rust process also signs the user/tool projection for
each model call; pre-credentials middleware checks that receipt against the
actual request before OpenShell supplies the provider credential.

The intentionally synthetic fixed policy is:

| Match | Result |
| --- | --- |
| address ending in `@example.com` | replace with `[EMAIL]` |
| `NNN-NN-NNNN` | deny |

These regexes are teaching aids, not production DLP.

## Scope

The launcher keeps Pi's native TUI, JSONL sessions, reasoning controls, and
native `read`, `bash`, `edit`, and `write` tools. Tool calls run sequentially.
The complete assistant/tool-result batch is admitted before it is published.
Tool side effects are not transactional and may exist even when a result is
denied.

Edit results show admitted text rather than Pi's file-derived diff preview,
which would read content outside the admission boundary.

Only explicit `/compact` is supported. Automatic compaction, retries, queued
prompts, project instructions, skills, resume/import/branching, model switching,
images, extensions, and direct `!` shell commands are disabled. A prompt entered
while Pi is working is rejected rather than retained. At context exhaustion,
run `/compact` yourself.

The sample workspace contains `counter.js` and one Node test so a model can
inspect, edit, and test real code without extra project scaffolding.

## Run

Prerequisites are an existing HTTPS/mTLS OpenShell gateway, OpenShell `0.0.116`
or a compatible release, Bash, Python 3.11+, uv 0.11+, Rust 1.90+, Docker, Node
22 for local development, and one OpenAI-compatible Chat Completions key.

```sh
cp .env.example .env
cp models.json.example models.json
# Fill in the three values documented in .env.
./demo.sh prepare
```

`PI_ADMISSION_HOST` must be reachable from the gateway and sandbox. Preparation
discovers the selected gateway identity over its existing mTLS connection,
creates a 30-day local service certificate, and writes private state under
`.workspaces/`.

Start the service:

```sh
./demo.sh serve
```

Then print and install the middleware registration in the gateway's
operator-owned configuration before creating the sandbox:

```sh
./demo.sh registration
./demo.sh setup
./demo.sh launch
```

Useful prompts are:

```text
Read the sample and run its test.
Change the counter to add two, update the test, and run it.
Repeat alice@example.com.
123-45-6789
/compact
/quit
```

Every action has a side-effect-free form that neither sources `.env` nor reveals
secrets, for example `./demo.sh --print setup`. Run the paid live check with
`./demo.sh verify`; it checks denial, redaction, a real write tool, manual
compaction, saved JSONL, and rejection of a provider request without a receipt.
It requires the running gateway, sandbox, service, and model credential.

Finish with `./demo.sh cleanup`. It removes only the example sandbox, sessions,
providers, and profiles. It retains host configuration, gateway registration,
and the Docker image.

## Development

```sh
uv run ruff format --check .
uv run ruff check .

cd middleware
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --locked

cd ../pi-harness
npm ci
npm run check
npm test
```

The suite is deliberately bounded: two launcher end-to-end flows and focused
admission transport, egress parsing, and receipt-binding checks. The protobuf,
manifest, and lockfile are generated or managed by
`openshell-middleware-manager`; do not edit the protocol by hand.

## Limits

The supported request is uncompressed, streaming, text-only Chat Completions.
Unknown shapes fail closed. Receipts cover ordered user/tool text, destination,
sandbox, middleware, policy, and expiry—not the full transcript or every HTTP
byte. Assistant/reasoning text is locally admitted and scanned at egress but is
not receipt-bound. The guarantee applies to this controlled launcher, not
compromised same-authority code, filesystem contents, or reversible tool effects.
