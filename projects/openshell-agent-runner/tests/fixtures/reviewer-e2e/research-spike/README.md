# Floating-point cancellation spike

This is an internal OAR smoke fixture uploaded to an OpenShell review sandbox by
CI, not a published project or an OpenShell integration. This standard-library-only
fixture deliberately omits packaging and a lockfile; the OAR CI job owns its
Python environment. No credentials, services, or paid operations are needed.

Question: can a small term disappear when accumulated between two large opposite
terms in ordinary Python floating-point arithmetic?

Use Python 3.12 or newer. There are no dependencies, datasets, random seeds, or
installation steps. From this directory, run:

```bash
python3 experiment.py
```

The experiment uses the fixed input `[1e16, 1.0, -1e16]`, compares an explicit
left-to-right loop with `math.fsum`, and asserts the expected output:

```text
{"left_to_right": 0.0, "compensated": 1.0}
```

The exact arithmetic result is 1. This demonstrates cancellation for one
constructed input. It does not estimate prevalence, compare speed, or establish
accuracy for all numerical workloads. The spike deliberately has no generalized
API, CLI framework, or production packaging.
