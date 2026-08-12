# Pi deny-or-redact example

This example demonstrates two outcomes for a rendered Pi prompt:

- **deny:** the prompt is not appended to chat history and no provider request
  is made;
- **redact:** the replacement is appended to history and the provider receives
  that same replacement.

Run the credential-free demonstration from `projects/egress-gate/`:

```shell
uv run python examples/pi-attested-admission/run_example.py
```

Its complete output is intentionally small:

```json
{
  "deny": {
    "decision": "deny",
    "history_unchanged": true,
    "provider_unchanged": true
  },
  "redact": {
    "decision": "replace",
    "history": ["please [REDACTED]"],
    "provider_prompts": ["please [REDACTED]"]
  }
}
```

The example uses the real regex policy, admission processor, signed receipt,
provider-request validation, and egress processor. The receipt is internal
plumbing: it proves that the redacted prompt admitted before history append is
the prompt authorized at provider egress.

## Managed Pi setup

Use the matching branches:

- [Pi user-message append hook PR](https://github.com/johnnygreco/pi/pull/1)
- [OpenShell integration branch](https://github.com/johnnygreco/OpenShell/tree/openshell/pi-egress-admission)

Register Egress Gate as an OpenShell supervisor middleware with Pi receipt
enforcement enabled. OpenShell exposes the admission bridge through
`OPENSHELL_PI_CONVERSATION_URL`. Load this directory's extension using Pi's
existing extension option:

```shell
pi --extension ./openshell-input-admission.ts
```

Pi remains unaware of OpenShell. The extension calls the bridge from
`before_user_message_append`: a denial returns `cancel`, while a replacement
returns `transform`. It attaches the resulting receipt to the first provider
request. Missing receipts and currently unsupported continuations fail closed.

This initial integration supports idle, text-only, direct OpenAI Chat
Completions submissions. Images, queued input, retries, compaction, and
automatic continuations after tool calls are deferred.
