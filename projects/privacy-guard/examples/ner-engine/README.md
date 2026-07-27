# NER engine example

This example registers Privacy Guard's general `ner` engine with either the
explicit `POST /v1/extract` endpoint contract or an already-downloaded local
GLiNER model. Policy owns labels, threshold, overlap behavior, replacement,
and the final action. The registry factory owns the model and its execution
location.

The initial `nvidia/gliner-PII` checkpoint is optimized for English PII/PHI.
It is neither a required model nor a built-in entity catalog. Validate the
chosen model, labels, threshold, and representative inputs before production.

## Endpoint-backed registry

Set the exact endpoint and model identifier. Request text leaves the Privacy
Guard process, so use only an operator-approved endpoint and protected network.
The endpoint URL must be an `http` or `https` URL whose path is exactly
`/v1/extract`; embedded credentials, query strings, and fragments are rejected.

```bash
cd projects/privacy-guard
export PYTHONPATH="$PWD/examples/ner-engine"
export PRIVACY_GUARD_NER_ENDPOINT="http://spark-9107.local:8002/v1/extract"
export PRIVACY_GUARD_NER_MODEL="nvidia/gliner-PII"

uv run privacy-guard \
  --registry-factory endpoint_registry:create_registry engines
uv run privacy-guard \
  --registry-factory endpoint_registry:create_registry configuration-schema
uv run privacy-guard \
  --registry-factory endpoint_registry:create_registry configure-gateway \
  --host-ip YOUR_HOST_IPV4
uv run privacy-guard \
  --registry-factory endpoint_registry:create_registry serve \
  --listen 0.0.0.0:50051 \
  --timeout-seconds 30
```

The endpoint receives `text`, `labels`, `model`, `threshold`, `chunk_length`,
`overlap`, and `flat_ner`. Privacy Guard performs no retry, fallback, health
check, or protocol detection.

## Direct local registry

The direct example deliberately keeps GLiNER and PyTorch out of Privacy Guard's
base dependencies. Install the characterized library version in the deployment
environment and download the model through an operator-controlled workflow:

```bash
uv pip install "gliner==0.2.27"
export PYTHONPATH="$PWD/examples/ner-engine"
export PRIVACY_GUARD_NER_MODEL_PATH="/absolute/path/to/downloaded/model"

uv run privacy-guard \
  --registry-factory local_registry:create_registry engines
uv run privacy-guard \
  --registry-factory local_registry:create_registry serve \
  --listen 0.0.0.0:50051 \
  --timeout-seconds 30
```

`local_files_only=True` prevents an implicit model download. Local calls are
serialized because loaded-model thread safety and GPU memory behavior are not
assumed. Long input is processed in overlapping Unicode code-point windows,
with global offset rebasing and exact duplicate removal. A running model call
cannot be preempted by Privacy Guard's worker thread; timeout is checked after
the call returns.

`PRIVACY_GUARD_NER_CHUNK_LENGTH` and
`PRIVACY_GUARD_NER_CHUNK_OVERLAP` optionally override the defaults of 384 and
128. The endpoint interprets these according to its contract. The local facade
uses them as code-point window sizes and requires overlap to be smaller than
chunk length.

## Exercise policy actions

Use [`policy.yaml`](policy.yaml) as the OpenShell policy source. The extracted
[`privacy-guard-config.yaml`](privacy-guard-config.yaml) is convenient for
direct middleware tests. Its configured replacement may stay present for every
action:

- `detect` allows the original request and reports findings.
- `block` denies a request when a configured entity is found.
- `replace` sends the body after deterministic, non-overlapping template
  replacement.

Change only `on_detection.action` to exercise these modes. Nested detection
returns all valid model spans. Replacement keeps all findings but selects
non-overlapping winners by score, span length, start offset, and configured
label order.

Scores are not exposed as categorical confidence because model scores are not
universally calibrated. Tune the required `threshold` using use-case-specific
evaluation.

The Anonymizer self-hosted GLiNER `/v1/chat/completions` contract is not
accepted by this example. It remains a dedicated future adapter so Privacy
Guard never guesses a provider protocol from a URL.
