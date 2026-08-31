# Request enforcement

The gateway parses each request once and passes an immutable representation to
the configured gates. A denied request returns a structured error before any
upstream connection is opened. Tests cover malformed input, allowed traffic,
and denial responses.
