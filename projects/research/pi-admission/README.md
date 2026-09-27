# Pi redaction experiment

This research experiment tests whether an agent can recover an email from
saved session history despite redaction on outgoing model requests.

- [Experiment specification](docs/index.md): admission harness and middleware
  construction, setup, redaction conditions, prompts, and evidence requirements.
- [Dev Note](../../../docs/dev-notes/posts/2026-09-26-network-redaction-is-not-enough.md):
  findings and the eight recorded chat transcripts.

## Maintenance

Edit `docs/index.md` here. The build stages it into `docs/research/pi-admission/`,
linked from the Dev Note rather than the tools Documentation navigation.

The harness, middleware, setup code, and runtime tests live in
`openshell-research-internal/spikes/pi-admission/implementation/`. Native sessions,
logs, provenance records, and evidence bundles stay in that internal project.
The public build works without the internal checkout.

The eight recorded chats are published through
`docs/assets/pi-admission/email-traces/viewer.json`. The viewer ends network
redacted chats at the first assistant reply containing the full original address;
later follow-ups remain in the generated data and internal originals. Counts and
navigation reflect the displayed excerpt. Edit `docs/javascripts/pi-traces.js`
and `docs/stylesheets/pi-traces.css` for presentation changes. Never hand-edit
recorded messages, tool results, or the Dev Note's quoted code. Verify or
regenerate the viewer with the internal project's `tools/render-public-traces.py`
and its README instructions; use `--check` to compare it with the originals.
Python excerpts in the post omit shell wrappers; the viewer preserves complete
tool calls.

From the public repository root:

```sh
python3 tests/test_render_dev_notes.py
node --check docs/javascripts/pi-traces.js
scripts/build-docs.sh
```

Serve and inspect the built site as described in the
[site development guide](../../../docs/development/index.md#validate-and-preview).
After presentation changes, check the model and redaction tabs, collapse
controls, keyboard navigation, and mobile layout in both color schemes.
