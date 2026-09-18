# Minimal Pi admission spike

This research spike shows why content policy must integrate at the agent
harness—not only at network egress. It uses unmodified Pi libraries for a
controlled coding session in OpenShell with two boundaries:

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

You need an existing HTTPS/mTLS OpenShell gateway registered in your CLI, access
to its configuration and restart procedure, Bash, Python 3.11+, uv 0.11+,
Rust 1.90+ with native build tools, and Docker. OpenShell `0.0.116` was tested;
other releases must support the same middleware contract. Node 22 is needed
only for local harness development; the demo image includes it.

Use your own model provider: it must support HTTPS, streaming OpenAI-compatible
Chat Completions, text input, tool calling, and API-key authentication. This
spike does not support OAuth, custom authentication headers, or every Pi API.
Model calls, including the verification, may incur provider charges.

### 1. Configure and prepare

Run these commands from `projects/research/pi-admission/`:

```sh
cp .env.example .env
cp models.json.example models.json
```

Edit `models.json` to describe your provider and model using Pi's native catalog
format. Set the provider name, `baseUrl`, model `id`, limits, and compatibility
settings for your endpoint; keep `api: "openai-completions"` and text-only input.
The supplied OpenRouter/GLM configuration is an example, not a requirement.
Do not put API keys in this file: it is copied into the sandbox image.

Set these values in `.env`:

| Variable | Your value |
| --- | --- |
| `OPENSHELL_GATEWAY` | The gateway name shown by `openshell gateway list` |
| `PI_ADMISSION_HOST` | DNS hostname or IPv4 address of the machine running `serve`, without a scheme or port |
| `PI_MODEL_API_KEY` | The API key for the selected model provider |
| `PI_MODEL` | Only when the catalog has multiple models: `provider/model-id` |

The gateway and sandbox supervisor must reach the service on TCP **50051**;
Pi inside the sandbox uses TCP **5443**. Choose a hostname/address reachable
from both gateway and sandbox; `localhost`
inside a sandbox points to the sandbox, not your host. Docker-specific hostnames
are suitable only if they also resolve from the gateway. Allow these connections
through the host firewall.

```sh
./demo.sh prepare
```

Preparation discovers the gateway identity using your CLI's existing mTLS
credentials, creates a 30-day service certificate, and builds the local
`pi-admission:local` image. Private generated state stays in `.workspaces/`.

### 2. Start and register the service

In one terminal, start the service and leave it running:

```sh
./demo.sh serve
```

In a second terminal, from the same project directory, print the registration:

```sh
./demo.sh registration
```

This command reads `.workspaces/middleware.toml`, created by `prepare`, and
prints it. It does **not** write to the gateway configuration, register the
middleware, or restart anything.

Find the TOML configuration actually loaded by your gateway process. Its path
depends on how the gateway was installed: check its service/startup configuration
or ask its operator. This is the **server's configuration**, not the CLI's local
gateway credentials. Do not assume a file named `gateway.toml` in the current
directory is the right one.

Back up that file, then add the printed `[[openshell.supervisor.middleware]]`
entry. For a gateway configuration accessible on this machine, the command is:

```sh
# Replace this placeholder with the existing, active gateway config path.
./demo.sh registration >> /path/to/gateway.toml
```

If the file requires administrator permissions, use this **instead**:

```sh
./demo.sh registration | sudo tee -a /path/to/gateway.toml >/dev/null
```

Both commands **append**. Run only one, and only when no middleware entry named
`pi-admission` already exists. On subsequent runs, edit the existing entry rather
than appending a duplicate. Do not use `>`: it would overwrite the gateway's
other settings.

For a remote or containerized gateway, install the entry in that deployment's
configuration instead of appending to an unrelated local file. Copy/mount the
**public** `.workspaces/tls/ca.crt` there and adjust `tls_ca_cert_path` in the entry
to a path readable by the gateway process. Do not copy the service's private key.

Restart the gateway using the procedure for your installation, while `serve`
is running. Both the gateway and each sandbox connect to the service at startup.
This restart may briefly affect other gateway users.

Before continuing, check that it is healthy (use your `.env` gateway name):

```sh
openshell --gateway YOUR_GATEWAY gateway info
```

### 3. Launch and try it

In the second terminal:

```sh
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

The email should appear as `[EMAIL]` in the admitted conversation. The synthetic
SSN-shaped input should be denied without entering live history or JSONL. Tool
edits should change the sample and its test. `/compact` may report insufficient
history in a short session; continue working or use the verification below,
which deliberately exercises compaction with a smaller retention threshold.

### 4. Verify and inspect egress

Every action has a side-effect-free form that neither sources `.env` nor reveals
secrets, for example `./demo.sh --print setup`. Run the paid live check with
`./demo.sh verify`; it checks denial, redaction, a real write tool, manual
compaction, saved JSONL, and rejection of a provider request without a receipt.
It requires the running gateway, sandbox, service, and model credential.

Inspect network decisions using OpenShell's existing logs (replace `YOUR_GATEWAY`
with the gateway from `.env`):

```sh
openshell --gateway YOUR_GATEWAY logs pi-admission --source sandbox --since 5m
```

The verification's bypass attempt should report `receipt_missing`. User input
denied before any model request stays inside the harness boundary; it is not a
network request and will not appear as an egress denial.

### 5. Clean up

After exiting Pi, run `./demo.sh cleanup`. It deletes the example sandbox and its
sessions, providers, and profiles; save any work you want to retain first. It
keeps host configuration, gateway registration, and the Docker image. Run cleanup
before repeating setup after a failed or completed demo. The scripts use fixed
`pi-admission` resource names; use a gateway where those names are available.

When finished permanently, remove the `pi-admission` middleware entry from the
gateway configuration and restart the gateway **before stopping `serve`**.
Otherwise a later gateway startup may fail while trying to contact the stopped
service. Finally, stop `serve` with Ctrl-C.

## Comparison: network redaction alone

**What this demonstrates:** a proxy can sanitize what the model receives without
sanitizing the agent's live conversation or saved session. Those are different
boundaries; successful network redaction is not evidence of clean local history.

Use a separate sandbox running ordinary Pi, without `pi-harness`, for this
comparison. Keep its normal model-provider and network configuration, but attach
OpenShell's built-in regex middleware to the model endpoint instead of the
`pi-admission` middleware:

```yaml
network_middlewares:
  network_redaction:
    middleware: openshell/regex
    config:
      mode: redact
    on_error: fail_closed
    endpoints:
      include: ["YOUR_MODEL_PROVIDER_HOST"]
```

This is a policy fragment, not a complete sandbox policy. The built-in middleware
needs no external service registration. In OpenShell `0.0.116`, it replaces
`sk-[A-Za-z0-9_-]{16,}` with `[REDACTED]`; its patterns are fixed, and it does not
recognize this spike's email/SSN patterns.

In ordinary Pi, send this **fake token**, never a real credential:

```text
Repeat this demonstration token exactly: sk-DEMO_ONLY_NOT_A_REAL_KEY_123456
```

Inspect OpenShell's sandbox logs for the regex transformation, then inspect Pi's
original user message and search its saved session JSONL for the fake token.
The original remains in local history even though the outgoing request was
redacted. The model's reply may show `[REDACTED]`, but do not rely on model
obedience alone as proof of what crossed the network.

Compare that with `alice@example.com` in our admission demo:

| Approach | Outgoing content | Pi history and JSONL |
| --- | --- | --- |
| Ordinary Pi + network-only regex | Fake token redacted | Original fake token remains |
| Admission harness | Email redacted | Only `[EMAIL]` is published |

The two examples deliberately use different fixed patterns. For an identical
input comparison, the Rust admission policy would need the same fake-token
pattern; it does not currently contain it. Do not layer this network replacement
onto the receipt-enforced demo: changing attested content can invalidate the
receipt and obscure the comparison. `./demo.sh launch` always starts the admission
harness, not the ordinary-Pi baseline.

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
npm run build
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
