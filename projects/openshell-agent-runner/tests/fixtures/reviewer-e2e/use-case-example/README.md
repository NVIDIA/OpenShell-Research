# Summarize a small review workload

This internal OAR smoke fixture teaches a contributor how recorded sandbox task
durations become a simple run summary. It is not an OpenShell integration or a
published project; the CI workflow uploads it to an OpenShell review sandbox.
Its three fixed records stand in for already collected measurements.

With Python 3.12 or newer, run `python3 summarize.py` from this directory. No
installation, credentials, network access, or external data is needed. This
standard-library-only fixture deliberately omits packaging and a lockfile; the
OAR CI job owns its Python environment.

The script reads the adjacent `durations.json`, computes the number of runs and
their total duration, and asserts these expected results before printing them:

```text
{"runs": 3, "total_seconds": 12}
```

Use the output to understand the aggregation step, not to estimate production
performance. The recorded numbers are synthetic; this does not measure actual
agent latency. It has no writes, paid operations, or deployment side effects.
