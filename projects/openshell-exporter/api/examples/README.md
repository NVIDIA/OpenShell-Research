# Contract examples

These files are checked examples of CloudEvents envelope v1. Tests validate them against [event-envelope-v1.schema.json](../schemas/event-envelope-v1.schema.json).

| Example | Evidence type |
|---|---|
| `ocsf-network-denial.json` | OCSF network denial |
| `ocsf-ai-inference.json` | OCSF AI inference evidence |
| `sandbox-lifecycle.json` | sandbox lifecycle |
| `nemo-relay-log.json` | Relay operational file log |
| `policy-draft-snapshot.json` | read-only draft snapshot |
| `policy-draft-chunk.json` | read-only draft chunk |
| `policy-draft-history.json` | read-only draft history |
| `policy-status.json` | sandbox policy status |
| `policy-revision.json` | policy revision metadata |
| `policy-reconciliation-warning.json` | incomplete or degraded reconciliation |

Use these examples to build receiver tests. Do not copy placeholder IDs, timestamps, or source paths into production configuration.

Run the contract tests from the repository root:

~~~sh
go test ./api
~~~
