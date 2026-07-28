# Privacy Guard latency analysis

This directory contains the source data, deterministic renderer, and generated
Privacy Guard latency-versus-prompt-size figure. It is the reusable starting
point for future documentation and a Dev Note about the proof-of-concept stress
test.

## Recreate the figure

Run from `projects/privacy-guard/`:

```sh
uv run python analysis/render_latency_plot.py
```

The command writes:

```text
analysis/privacy-guard-latency-vs-prompt-size.svg
```

Verify that the committed figure matches the data and renderer:

```sh
uv run python analysis/render_latency_plot.py --check
```

The renderer uses only the Python standard library and does not add a project
dependency.

## Data

`privacy-guard-latency.csv` contains 96 joined observations from the synthetic
Claude Code context-growth experiment run on July 27–28, 2026.

| Column | Meaning |
| --- | --- |
| `observed_at_utc` | Timestamp used to correlate the request across available logs |
| `prompt_tokens` | Claude Code session token accounting for the provider request |
| `privacy_guard_latency_ms` | `duration_ms` emitted by the `privacy_guard_evaluation` service log |
| `entity_count` | Aggregate Privacy Guard finding count |
| `phase` | Baseline, pre-compaction, compaction-trigger, or context-rebuild phase |
| `openshell_observed_ms` | OpenShell L7 request event to middleware-result event |
| `first_output_elapsed_ms` | OpenShell L7 request event to the first Claude response output |
| `turn_elapsed_ms` | OpenShell L7 request event to the last Claude response output |

The baseline observations have only the service-side measurement, token count,
and entity count. Thirteen large-context observations also have the
OpenShell-observed middleware interval. Twelve of those completed turns have
first- and last-output timing; the compaction-triggering request does not.

The figure's 0.56% annotation is the arithmetic mean of
`privacy_guard_latency_ms / turn_elapsed_ms` across those 12 completed turns.
It is not Privacy Guard's share of the short OpenShell middleware interval.
Point color uses a continuous scale for `entity_count`; each SVG point also
contains an accessible tooltip with its token, latency, and entity values. The
SVG adapts its text, grid, threshold, and point-outline colors to the viewer's
light or dark color scheme.

## Interpretation limits

This is a proof-of-concept observation set, not a general benchmark:

- the workload used synthetic, deliberately repeated text
- prompt size and entity count increased together
- the run used one host, sandbox, Privacy Guard configuration, RegexEngine
  policy, and Claude Code session
- baseline and large-context observations span a Privacy Guard process restart
- the linear fit is descriptive and should not be treated as a performance
  guarantee

The CSV contains operational timing and aggregate counts only. It contains no
request text or detected entity values.
