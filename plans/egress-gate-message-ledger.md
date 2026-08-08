# Egress Gate message fingerprinting and denied-history ledger plan

## Scope

The first release is intentionally process-local and in-memory. It does not
provide cross-process, cross-replica, or restart persistence. Losing state may
cause an old denied message to be denied again, but must never cause content to
be silently allowed or an unrelated message to be removed.

## Executive decision

Build two separate features on one reusable fingerprint-state primitive:

1. **Evaluation memoization** reuses immutable per-target gate results so a
   gate does not repeat expensive work for unchanged historical content.
2. **Denied-history sanitation** records a denied current message and removes
   that complete message when the harness later resubmits it as history.

Never implement `seen fingerprint -> skip`. A cache hit must replay the earlier
evaluation semantics. Detect findings still count, replacement edits are still
applied to the current body, and denied current input is still denied.

History sanitation is different from memoization. It requires a stable
conversation key, an explicit current-versus-history boundary, a stable message
position, and structural removal of a complete message object. Keep its state
and invalidation semantics separate even if both features use the same storage
implementation.

## Goals

1. Give built-in and custom gates a public, bounded, thread-safe fingerprint
   store that retains no raw request content.
2. Avoid repeated deterministic evaluation of unchanged message text within
   one prepared gate instance.
3. Deny disallowed current input the first time it appears.
4. If the harness later resubmits that denied input as history, remove the
   complete historical message before evaluating and forwarding the request.
5. If the same content is submitted again as current input, evaluate and deny
   it again.
6. Preserve source bytes outside structurally removed messages and explicit
   text replacements.
7. Preserve the current gate, pipeline, timeout, mutation, finding, and
   content-safe logging contracts.

## Non-goals

- Durable or distributed storage.
- Exactly-once evaluation across concurrent requests. Concurrent misses may
  compute the same result more than once; correctness must remain identical.
- Guessing conversation identity from `sandbox_id`.
- Inferring current input from “the last message” without harness metadata.
- Modifying the harness's local session store.
- Returning body mutations with a terminal deny. Denied evaluations remain
  mutation-free.
- Silently permitting content when state is missing, expired, or unavailable.
- Removing arbitrary JSON object members or general JSON tree surgery. The
  first structural edit is removal of message objects from a configured array.
- Solving provider-specific tool-call dependency repair generically.

## Required harness contract

History sanitation must be explicitly enabled and requires two bounded request
headers supplied by the harness:

- a stable conversation ID;
- the number of leading message-array entries that are history for this call.

Make both header names configurable in the message-history policy. Do not
assign implicit global header names in the domain model. When the feature is
enabled, each configured header must occur exactly once. The conversation value
must be a bounded scalar string. The history length must be a canonical
non-negative decimal integer no greater than the parsed message count.

Egress Gate must remove both coordination headers from every proceeding
request so they are not sent to the LLM provider. The regex gate must therefore
declare `MUTATE_HEADERS` when this feature is configured. A denied request is
not forwarded, so no header mutation is necessary in the terminal result.

Do not use `request_id`: it changes on every call. Do not use `sandbox_id`: one
sandbox may host multiple conversations. If either required header is missing,
duplicated, malformed, or inconsistent with the body, fail closed as invalid
input rather than guessing.

The harness remains the preferred owner of its own history. It should avoid
persisting denied input when it can observe the denial. The ledger is a
defensive recovery mechanism for harnesses that resubmit rejected messages.

## Message identity and classification

Add a normalized message envelope above `MessageBlock`:

```python
class MessageOccurrence(StrEnum):
    HISTORY = "history"
    CURRENT = "current"


class MessageEnvelope(StrictDomainModel):
    id: str
    node_id: str
    message_index: int
    occurrence: MessageOccurrence
    role: MessageRole
    blocks: tuple[MessageBlock, ...]
```

`JsonMessageMapParser` should continue deriving ordered blocks, but return them
grouped by their owning message envelope. The history length supplied by the
harness classifies indices below the boundary as `HISTORY` and all remaining
indices as `CURRENT`.

Each `TextTarget` exposed by `MessageBlocksParser` must carry the document-local
ID of its owning removable content unit:

```python
@dataclass(frozen=True, slots=True)
class TextTarget:
    id: str
    text: str
    unit_id: str | None = None
```

Whole-body and arbitrary JSON-field targets may leave `unit_id` as `None`.
Message-block targets use their `MessageEnvelope.id`. Do not place conversation
IDs, fingerprints, or persistent state keys on `TextTarget`.

For the in-memory first release, identify a denied message by:

```text
conversation fingerprint
+ message_index at first denial
+ semantic message fingerprint
```

The semantic message fingerprint must include the normalized role and the
ordered `(block kind, block text)` sequence. This avoids storing raw JSON and
matches equivalent JSON string escaping. Including `message_index` prevents a
different identical message elsewhere in the same conversation from being
removed.

If a harness truncates leading history and changes message indices, the record
will not match and the message may be denied again. That conservative false
negative is acceptable in the in-memory release. Never fall back to removing
every historical message with the same text.

## General fingerprint-state API

Create a focused public module, tentatively `egress_gate.fingerprints`, with:

- an opaque immutable `ContentFingerprint` value;
- a `ContentFingerprinter` that creates keyed BLAKE2b digests with explicit
  domain separation;
- a generic `FingerprintStore[ValueT]` protocol;
- a bounded `InMemoryFingerprintStore[ValueT]` implementation.

Use a process-random key generated when a fingerprinter is constructed. Never
log or serialize the key or resulting fingerprints. Callers provide a short,
developer-authored domain such as `regex-target-v1`, `conversation-v1`, or
`message-v1`; the domain must be included in the digest input.

The store API should be small:

```python
class FingerprintStore(Protocol, Generic[ValueT]):
    def get(
        self,
        scope: ContentFingerprint,
        key: ContentFingerprint,
        *,
        timeout: Timeout,
    ) -> ValueT | None: ...

    def put(
        self,
        scope: ContentFingerprint,
        key: ContentFingerprint,
        value: ValueT,
        *,
        timeout: Timeout,
    ) -> None: ...
```

The in-memory implementation must:

- use monotonic time;
- provide bounded entry count and TTL;
- use LRU eviction among live entries;
- refresh recency, but not necessarily TTL, on reads;
- be safe for prepared gates shared by worker threads;
- honor the shared `Timeout` while waiting for its lock and before returning;
- perform hashing outside the lock;
- never store raw request text, headers, bodies, or conversation IDs;
- accept immutable caller-owned values and never mutate them;
- expose content-free hit, miss, insertion, expiration, and eviction counters
  only if the project has an appropriate metrics surface by implementation
  time. Do not add request fingerprints to logs or traces.

Use separate store instances or namespaces for evaluation cache records and
denied-message records. Evaluation records are scoped to one prepared gate and
therefore one rule/configuration generation. Denied-message records are scoped
to a conversation within that prepared instance. A policy replacement may lose
both forms of in-memory state; the resulting behavior is conservative
re-evaluation.

Make the protocol and implementation usable from custom `GateResources`.
Document composition rather than adding a hidden global state service or
changing the `Gate._evaluate()` signature.

## Parsed-content mutation contract

Generalize parsed-content rendering beyond text replacement without teaching
gates about JSON source spans:

```python
@dataclass(frozen=True, slots=True)
class ContentRemoval:
    unit_id: str


ContentMutation: TypeAlias = TextReplacement | ContentRemoval


class ParsedRequestContent(Protocol):
    @property
    def targets(self) -> tuple[TextTarget, ...]: ...

    def render(
        self,
        mutations: tuple[ContentMutation, ...],
        *,
        timeout: Timeout,
    ) -> bytes: ...
```

Replace the current `replace_text()` protocol method rather than maintaining
parallel mutation APIs. `Utf8TextParser` supports its one text replacement and
rejects removals. `JsonFieldsParser` supports selected text replacements and
rejects removals. The parsed result owned by `MessageBlocksParser` supports
both selected text replacements and removal of complete message envelopes.

Validation must reject:

- duplicate mutations for one target or unit;
- replacement of an unselected target;
- removal of an unknown or non-removable unit;
- replacement of a target inside a unit removed by the same render call;
- overlapping structural edits;
- output beyond `MAX_BODY_BYTES`;
- unsupported mutation kinds for a parser.

Keep `TextReplacement` and `ContentRemoval` named and immutable. Do not return
to raw `(id, value)` tuples.

## Source-preserving JSON array removal

Extend the private source-aware JSON engine and `JsonDocument` with one narrow
structural operation: removal of selected immediate items from a known array.

The document must compute non-overlapping source spans that correctly handle:

- the only array item;
- first, middle, and last items;
- adjacent and non-adjacent removals;
- whitespace before or after commas;
- multiple removals in one linear render pass;
- simultaneous text replacements outside removed items.

Preserve every source byte outside the removed array-item spans and explicitly
replaced string tokens. Assemble the output once in source order, reusing the
existing linear-edit discipline. Check the shared deadline during span
planning, output sizing, and assembly.

Do not expose raw token offsets publicly. `MessageBlocksParser` retains the
document-bound message-node handles needed to request removal.

Provider message validity remains the harness parser's responsibility. The
configurable JSON message-map implementation removes exactly the selected
message object. A future harness-specific parser may expand a removal to a
dependency group such as an assistant tool call and its tool outputs. Do not
guess those relationships in the generic JSON document.

## Regex-gate integration

Add an optional history configuration only to `RegexMessageBlocksScan` in the
first release. Raw body, JSON-field, path, query, and header scans do not have a
message-history contract.

During gate preparation:

1. Construct one process-random `ContentFingerprinter`.
2. Construct bounded evaluation and denied-history stores.
3. Validate the conversation and history-boundary header names.
4. Retain the prepared regex rule identity implicitly through the lifetime of
   the gate instance; no policy fingerprint needs to enter an evaluation-cache
   key owned exclusively by that instance.

During evaluation:

1. Parse conversation metadata and the message document.
2. Fingerprint the conversation ID without retaining the raw value.
3. For each historical message, compute its message fingerprint and look up
   `(conversation scope, message index, message fingerprint)` in the denied
   ledger.
4. Remove matching historical envelopes and reparse the sanitized body. Favor
   the simple two-parse implementation initially; optimize only with evidence.
5. Remove the coordination headers from a proceeding request.
6. For each remaining target, look up its immutable regex detections in the
   evaluation cache. On miss, run `_match_text()` and insert the result.
7. Aggregate detections and findings per occurrence exactly as today. Cache
   reuse must not collapse finding counts for repeated message occurrences.
8. Apply detect, replace, or deny behavior normally.
9. Before returning a deny, record every message envelope containing a detected
   target in the denied ledger. This normally records current input. After
   eviction, expiration, or restart, it may rediscover and deny a historical
   message once; recording that historical occurrence ensures the next request
   can remove it. Never record an envelope without a detection.

Cache immutable detection spans and rule identities, not final
`GateEvaluation` objects. On a hit, reconstruct findings and replacements
against the current target occurrence. This preserves current mutation and
counting behavior.

If the same semantic content appears as both history and current input, remove
only a historical occurrence whose full ledger key matches. Always evaluate
the current occurrence.

## Failure and safety behavior

- Missing or malformed required history metadata is invalid input.
- An expired shared deadline follows the existing runtime-limit behavior.
- Store construction or internal contract failures are configuration or gate
  execution failures, not allow decisions.
- A cache miss, expiration, eviction, or new process performs normal
  evaluation.
- A denied-ledger miss performs normal evaluation and may deny again.
- Hash collisions are treated as computationally infeasible through keyed
  256-bit BLAKE2b output. Do not add raw-content collision verification that
  defeats data minimization.
- Do not include raw conversation IDs, message text, fingerprints, cache keys,
  or stored detection spans in logs, public findings, traces, or errors.
- Bound hashing input by existing request and selected-text limits. Check the
  shared deadline before and after encoding large text for hashing.

## TDD implementation sequence

Implement in contract-first slices. Keep each slice green before proceeding.

### Slice 1: fingerprint primitives

Write tests for deterministic domain-separated fingerprints within one
fingerprinter, different results across domains and keys, frozen opaque values,
no content in representations, bounded LRU/TTL behavior, lock timeout, and
concurrent access. Then implement the public protocol and in-memory store.

### Slice 2: message envelopes and harness metadata

Write parser tests for exact history/current classification, zero history,
multiple current messages, missing/duplicate headers, malformed and excessive
history lengths, and header stripping. Then add message envelopes and the
history configuration models.

### Slice 3: structural message removal

Write exact-byte tests for only/first/middle/last/adjacent removals with unusual
whitespace and escaped strings. Add overlap, unknown-unit, output-bound, and
forced-timeout tests. Then generalize `ParsedRequestContent.render()` and
implement source-preserving array-item removal.

### Slice 4: denied-history ledger

Write end-to-end regex tests proving:

1. a disallowed current message is denied and recorded;
2. the same message at the same historical position is removed on the next
   request;
3. the next request proceeds if nothing else is disallowed;
4. the same text submitted as current input is denied again;
5. an identical historical message at a different index is not removed;
6. eviction or expiration causes conservative re-evaluation;
7. a new prepared gate starts with empty state;
8. no denied result carries mutations.

Then implement ledger lookup, sanitation, and recording.

### Slice 5: evaluation memoization

Use a recording matcher in tests to prove cache hits avoid repeated matching
while preserving detection findings, occurrence counts, deny behavior, and
source-preserving replacement. Test negative-result caching because clean
history is expected to dominate. Accept concurrent duplicate computation; do
not add single-flight coordination.

### Slice 6: custom-gate adoption and documentation

Add a focused custom-gate test or example resource that composes
`InMemoryFingerprintStore`. Document thread safety, data minimization,
expiration, process-local limitations, and conservative fallback behavior.
Update architecture, configuration, regex-gate, custom-gate, limits, and
failure documentation. Regenerate project documentation through the canonical
staging script.

## Validation

Run from `projects/egress-gate/`:

```bash
make check
```

Because the plan requires documentation changes, also run from the repository
root:

```bash
python3 tests/test_render_dev_notes.py
scripts/build-docs.sh
```

Run the offline evaluator against at least these multi-request fixtures:

- deny current, then sanitize the same historical message;
- replace current, then replay cached detections for history;
- duplicate text at different message indices;
- multiple current messages after one history boundary;
- missing and malformed coordination headers;
- TTL/eviction fallback to normal evaluation.

Before handoff, independently review concurrency, deadline enforcement,
source-span deletion, conversation isolation, duplicate-message behavior,
content-safe diagnostics, and custom-gate usability.

## Acceptance criteria

- Custom gates can use the public in-memory fingerprint store through ordinary
  typed resources without changes to the gate evaluation signature.
- Repeated allowed or replaceable message text reuses cached gate evaluation
  while preserving observable findings and mutations.
- A denied current message is denied on first submission.
- The same ledger-keyed message is removed when explicitly classified as
  history in the same conversation.
- The same content submitted as current input is evaluated and denied again.
- Identical text at another historical index is not removed.
- Coordination headers never reach a proceeding upstream request.
- Missing state, eviction, expiration, restart, and concurrent misses preserve
  fail-closed evaluation semantics.
- Memory, entry count, TTL, selected text, structural edits, and output bytes
  are bounded and deadline-aware.
- No raw request content, conversation identity, or fingerprint appears in
  logs, findings, traces, errors, or object representations.
- Existing whole-body, JSON-field, message-block, detect, replace, deny, and
  custom-gate behavior remains available when history handling is disabled.
- Project checks, documentation checks, offline fixtures, and independent
  review are clean.
