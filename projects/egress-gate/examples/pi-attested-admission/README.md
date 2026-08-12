# Pi attested-admission example

This credential-free example exercises Egress Gate's public harness-admission
and attested-egress APIs across the state boundaries a managed Pi runtime must
enforce. It uses the real configured regex Gates, admission processor, Pi shape
adapter, Ed25519 receipt issuer, provider adapter, network Gate pass, and
receipt-header stripping. The deterministic provider recorder is local; no API
key or external service is needed.

From `projects/egress-gate/`, run:

```bash
uv run python examples/pi-attested-admission/run_example.py \
  --session-file /tmp/pi-egress-example/session.jsonl
```

The command prints JSON evidence for the intentionally small MVP:

- a safe idle, text-only rendered prompt and its first provider request;
- denial before the candidate changes the session or reaches the provider;
- candidate replacement before persistence and provider serialization;
- fail-closed denial of an unattested continuation; and
- removal of the internal receipt header before the provider recorder.

Inspect the resulting accepted history with:

```bash
python3 -m json.tool --json-lines /tmp/pi-egress-example/session.jsonl
```

The output reports receipt, canonicalization, provider-adapter, active key ID,
and policy versions, but never prints receipt bytes or denied content.

This hermetic executable is the Egress Gate component layer of the broader Pi
integration. `ManagedPiSession` deliberately models the required ordering:
rendered-prompt admission, optional candidate replacement, candidate commit, then
attested network egress. It is not presented as the pinned downstream Pi fork
or the full OpenShell sandbox layer; those runtime artifacts must use the same
public API and preserve this ordering.

## Run the managed forks

Use the matching integration branches:

- [Pi `openshell/pi-egress-admission`](https://github.com/johnnygreco/pi/tree/openshell/pi-egress-admission)
- [OpenShell `openshell/pi-egress-admission`](https://github.com/johnnygreco/OpenShell/tree/openshell/pi-egress-admission)

Register this service as an OpenShell supervisor middleware and start it without
`--no-require-pi-receipt`. Configure exactly one network middleware entry for
the OpenAI provider host. When OpenShell sees that the service advertises the
Pi admission binding, it exposes the loopback bridge and sets
`OPENSHELL_PI_CONVERSATION_URL` in the sandbox. Start the pinned Pi fork with
the standard extension option and this example's extension:

```shell
pi --extension ./openshell-input-admission.ts
```

Pi remains unaware of OpenShell; the deployment is responsible for loading the
extension. Receipt enforcement makes a missing or inactive extension fail
closed at provider egress. A normal Egress Gate deployment that does not use
managed Pi must start with `--no-require-pi-receipt`; it advertises and
evaluates only HTTP middleware.

The managed path currently supports direct OpenAI Chat Completions requests
from the pinned Pi serializer. It does not support images, steering or queued
follow-ups while streaming, compaction requests, provider retries, or automatic
continuations after tool calls. Those paths fail closed. The next increment is
a separate pre-provider-request admission boundary that issues one receipt for
each automatic call; it does not change the rendered-prompt hook or its
pre-persistence denial guarantee.

Version 1 supports the direct OpenAI Chat Completions subset emitted by the
pinned Pi serializer: text messages, function tools and calls/results,
`max_completion_tokens`, optional `temperature` and `reasoning_effort`, tool
choice, `stream: true`, `stream_options.include_usage: true`, `store: false`,
and optional `prompt_cache_key` and `prompt_cache_retention: "24h"` cache
fields. Compatibility-provider fields, custom sampling parameters, unknown
fields, unsupported content variants, and lossy multipart forms fail closed.
The provider adapter accepts either a string or one OpenAI text
block for message content because the pinned fixture treats those as the same
single text value. It otherwise requires one representation: `content` is
present, optional message metadata is omitted instead of `null`, and empty tool
call arrays are omitted. Integer, floating-point, and negative-zero spellings of
the same temperature are normalized because the pinned fixture treats them as
one numeric value. Provider requests require exactly one parameter-free
`Content-Type: application/json` header and no `Content-Encoding`.

Each receipt is short-lived and consumed by the first matching provider
request. It binds the admitted rendered prompt, sandbox, middleware policy, and
provider target. It does not prove which JavaScript extension called the
supervisor bridge, and it does not attest the complete conversation or provider
payload. OpenShell reruns the configured Gates on the actual HTTP request before
forwarding it and strips the internal receipt header.
