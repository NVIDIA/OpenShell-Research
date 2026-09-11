# Security

This is an experimental research example, provided as-is with no support
commitment. Use at your own risk.

Report suspected vulnerabilities through NVIDIA's product-security reporting
channel, not a public issue. Include the version, impact, and a minimal synthetic
reproduction; never include credentials or customer evidence.

Use authorized read-only sources, separate destination credentials, verified TLS,
read-only source mounts, and private monitoring endpoints. Protect checkpoint,
queue, proxy, and recovery storage with access controls and encryption. Redacted
data remains sensitive; redaction does not guarantee removal of every secret.

Relay is opt-in. Its proxy queues input before privacy filtering. Keep real
credentials outside agent containers and never collect arbitrary host logs.
Operators own authorization, retention, and downstream handling. See
[configuration](docs/configuration.md) and [limitations](docs/compatibility.md).
