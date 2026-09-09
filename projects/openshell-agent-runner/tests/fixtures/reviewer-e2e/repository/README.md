# Totals

This internal OAR smoke fixture calculates an arithmetic mean for a non-empty list of
ordinary finite numbers. Callers provide at least one value; handling empty
lists, arbitrary object types, and extreme floating-point values is out of scope.

It is a tiny reusable function, not an installable package or command-line tool.
No third-party dependencies or installation are required. With Python 3.12 or
newer, run the example's checks from this directory:

```bash
python3 checks.py
```

The checks cover positive values and mixed negative/positive values. A successful
run prints `OK`. Import `arithmetic_mean` from `src.totals` to use the function.

The CI workflow uploads this fixture to an OpenShell review sandbox; it is not
itself an OpenShell integration or a published project. This standard-library-only
fixture deliberately omits packaging and a lockfile; the OAR CI job owns its
Python environment. No credentials, services, or paid operations are needed.
