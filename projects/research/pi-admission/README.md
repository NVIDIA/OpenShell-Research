# Pi local-admission comparison

This research example demonstrates one point: redaction at network egress is
not enough when the original content remains in the agent's saved session.

```text
Admission off:
  content ------------------> history -> built-in regex -> model
                                raw

Admission on:
  content -> local redaction -> history -> built-in regex -> model
                               redacted
```

Both modes use one sandbox, the same controlled Pi launcher, model, native
tools, and OpenShell policy. The baseline is **not** a separate stock Pi CLI;
it is this launcher's controlled publication loop with local admission disabled.
Every launch starts a fresh JSONL session and prints its mode and transcript
path.

The only synthetic policy is:

```text
match:       sk-[A-Za-z0-9_-]{16,}
replacement: [REDACTED]
```

The local rule intentionally matches OpenShell's built-in fake API-key rule.
This is a focused example, not a configurable policy framework or a claim of
general DLP coverage. Use only invented values.

| Mode | Saved history | Outgoing request |
| --- | --- | --- |
| Admission off | Original fake key remains | Built-in regex redacts it |
| Admission on | `[REDACTED]` replaces it | Already redacted locally |

## Preserved Pi behavior and boundaries

The launcher retains Pi's native TUI and JSONL sessions, native `read`, `bash`,
`edit`, and `write` tools, supported reasoning/replay metadata, thinking
controls, usage accounting, cancellation, and explicit `/compact`.

Its controlled loop admits complete user messages, assistant output, tool
results, and manual compaction summaries before publication when admission is
on. It preserves text-block boundaries and native metadata. A transformation
that would change signed text/reasoning or tool-call semantics is rejected
instead of corrupting replay metadata or executing a modified call. Partial
streaming output and incomplete tool batches are not published. Native tool
execution is unchanged, but tool side effects are nontransactional and may
remain after a later rejection or cancellation. Unchecked tool details and edit
previews remain excluded.

Automatic compaction, retries, queued prompts, project instructions, skills,
resume/import/branching, model switching, images, extensions, and direct `!`
commands are disabled because those history-writing paths do not have the same
pre-publication boundary. At context exhaustion, run `/compact` explicitly.

## Prerequisites and configuration

You need an existing HTTPS/mTLS OpenShell gateway registered in the CLI, Bash,
Python 3.11+, uv 0.11+, Docker, and a provider compatible with streaming,
text-only OpenAI Chat Completions and API-key authentication. OpenShell
`0.0.116` was tested. Node 22 is needed only for host-side harness development;
the image includes it. Model calls can incur provider charges.

From `projects/research/pi-admission/`:

```sh
cp .env.example .env
cp models.json.example models.json
```

Edit `models.json` using Pi's native catalog format. Set the provider `baseUrl`,
model `id`, limits, and compatibility values; retain
`api: "openai-completions"` and text-only input. Do not put credentials there.

Set `OPENSHELL_GATEWAY` and `PI_MODEL_API_KEY` in `.env`. Set `PI_MODEL` only
when the catalog contains multiple models.

```sh
./demo.sh prepare
./demo.sh setup
```

Preparation selects the model, renders the model provider and policy, and
builds `pi-admission:local`. Setup creates one model provider and one sandbox.
There is no admission service, certificate, token, identity binding, custom
middleware registration, or gateway restart.

## Run the comparison

Use a different fresh fake key in each run. Reuse can let a later conversation
recover a value from an earlier transcript in the shared sandbox and obscure
what the comparison measures. Do not place either value in workspace files.

### Admission off

```sh
./demo.sh launch --admission off
```

1. Note the printed transcript path.
2. Enter a new value matching the synthetic pattern, for example in a prompt
   asking only for acknowledgement.
3. Inspect the stored user message in the printed JSONL file and confirm the
   original remains.
4. Confirm a built-in regex finding in OpenShell's sandbox logs:

   ```sh
   openshell --gateway YOUR_GATEWAY logs pi-admission --source sandbox --since 5m
   ```

5. Copy the exact transcript path printed by the launcher, substitute it for
   `PASTE_ACTUAL_TRANSCRIPT_PATH` below, and send the resulting prompt to the
   agent:

   ```text
   Use the bash tool exactly once to run the following Python command verbatim.
   Do not use the read tool, do not read any other file, and do not guess.

   python3 -c 'import json,re,sys; rows=(json.loads(line) for line in open(sys.argv[1])); texts=("\n".join(block["text"] for block in row["message"]["content"] if block["type"] == "text") for row in rows if row.get("type") == "message" and row["message"]["role"] == "user"); match=next(re.search(r"sk-[A-Za-z0-9_-]{16,}|\[REDACTED\]", text) for text in texts if re.search(r"sk-[A-Za-z0-9_-]{16,}|\[REDACTED\]", text)); print(" ".join(match.group(0)))' 'PASTE_ACTUAL_TRANSCRIPT_PATH'
   ```

The Python process inserts spaces before returning tool output, so the original
contiguous value never reaches the network layer on this recovery turn. This
shows that network-only redaction left recoverable source content in session
history.

### Admission on

```sh
./demo.sh launch --admission on
```

Repeat the steps with a different fresh fake key. The JSONL user message should
contain `[REDACTED]`, not the original. A network regex finding is not expected
for content already replaced locally. Asking the agent to inspect only this
transcript should recover the marker rather than the fake key.

Saved JSONL and OpenShell middleware logs are the evidence. A model's success,
failure, or claim that it saw a marker is not sufficient by itself. The
built-in regex accepts bodies only up to 256 KiB; `fail_closed` blocks larger
requests, so keep the experiment short or compact it.

## Optional live check

The paid verification uses the same shared setup and one fresh session per run:

```sh
./demo.sh verify --admission off
./demo.sh verify --admission on
```

It checks the mode-specific saved user value, a real native write tool call,
and manual compaction. It does not replace inspection of the middleware logs or
assert that a model successfully performs the recovery prompt.

Every action has a side-effect-free print form that does not source `.env`, for
example `./demo.sh --print launch --admission on`.

## Cleanup and migration

```sh
./demo.sh cleanup
```

Cleanup deletes the example sandbox, its sessions, and the model provider. Save
workspace changes or transcripts first. The generated host state and local
Docker image remain.

For an installation of the former service-backed version:

1. Save sandbox work and transcripts that must survive recreation.
2. Remove the old custom middleware registration and restart the gateway before
   stopping the old admission service.
3. Clean up obsolete demo resources, then prepare and set up this version to
   rebuild the image, provider, sandbox, and policy.

Ordinary preparation never edits or restarts an operator's gateway. This
version intentionally provides no network proof that local admission ran.
Built-in egress redaction remains active but does not authenticate local policy
decisions or verify receipts.

## Development

```sh
uv sync --frozen
uv run ruff format --check .
uv run ruff check .

cd pi-harness
npm ci
npm run check
npm run build
npm test

cd ..
bash -n demo.sh
```

The deterministic tests keep pre-network request capture separate from paid
gateway/model verification. They cover both modes across live history, saved
JSONL, assistant output, tool results, and compaction; native tool execution and
metadata; rejection of unsafe signed transformations; cancellation; and guards
on unsupported session replacement paths.

## Scope and limits

Local admission protects supported conversation history created by this
controlled launcher when enabled. It does not erase secrets from workspace or
other files, earlier sessions, editor recall, tool side effects, or reversible
encodings. The request format is HTTPS, streaming, text-only Chat Completions;
unknown content shapes fail closed. Same-authority code and filesystem contents
are outside this example's guarantee.
