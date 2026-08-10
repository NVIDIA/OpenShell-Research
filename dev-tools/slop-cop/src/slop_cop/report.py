"""Render Slop Cop results without recalculating analysis or scoring."""

from __future__ import annotations

import html
import json
from base64 import b64encode
from collections.abc import Mapping, Sequence
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from slop_cop.findings import RunResult

JSON_SCHEMA_VERSION = 1
MAX_HTML_BYTES = 10 * 1024 * 1024
_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"


class ReportError(ValueError):
    """Raised when a result cannot be serialized safely."""


def result_data(result: RunResult | Mapping[str, Any]) -> dict[str, Any]:
    """Validate and serialize one canonical run result."""
    try:
        validated = (
            result
            if isinstance(result, RunResult)
            else RunResult.model_validate(result, strict=False)
        )
    except ValidationError as error:
        raise ReportError(f"Invalid run result: {error}") from error
    return validated.model_dump(mode="json")


def json_report(result: RunResult | Mapping[str, Any]) -> str:
    """Serialize a result with stable key ordering and a final newline."""
    data = result_data(result)
    data.setdefault("schema_version", JSON_SCHEMA_VERSION)
    try:
        return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    except (TypeError, ValueError) as error:
        raise ReportError(f"The run result is not JSON serializable: {error}") from error


def write_json_report(result: RunResult | Mapping[str, Any], destination: str | Path) -> Path:
    """Write the canonical machine report."""
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_report(result), encoding="utf-8")
    return path


def terminal_report(result: RunResult | Mapping[str, Any]) -> str:
    """Render a compact, stable terminal summary."""
    data = result_data(result)
    state = _upper(data.get("decision") or data.get("analysis_state") or "error")
    threshold = data.get("threshold")
    score = data.get("score")
    lines = [f"Slop Cop: {state}  score={_display(score)}  threshold={_display(threshold)}"]
    for file_result in _items(data.get("files")):
        path = _text(file_result.get("path"))
        file_score = file_result.get("score")
        file_state = _upper(
            file_result.get("decision") or file_result.get("analysis_state") or "complete"
        )
        base_score = _base_score(file_result)
        delta = _delta(file_score, base_score)
        comparison = (
            "" if base_score is None else f"  base={_display(base_score)}  delta={_signed(delta)}"
        )
        lines.append(f"{path}: {file_state}  score={_display(file_score)}{comparison}")
        for category in _category_rows(file_result):
            lines.append(f"  category {category[0]}: -{_display(category[1])} points")
        for rule in _items(file_result.get("rule_costs")):
            charged = rule.get("charged_cost", 0)
            density = rule.get("density")
            density_cost = density.get("cost", 0) if isinstance(density, Mapping) else 0
            if _number(charged) or _number(density_cost):
                lines.append(
                    f"  rule {_text(rule.get('rule_id') or 'unknown')}: "
                    f"units={_display(rule.get('deduplicated_units'))} "
                    f"allowance={_display(rule.get('allowance'))} "
                    f"base=-{_display(rule.get('base_cost'))} "
                    f"density=-{_display(density_cost)} "
                    f"charged=-{_display(charged)}"
                )
                if isinstance(density, Mapping) and density.get("peak_excess"):
                    density_unit = _text(density.get("unit") or "units")
                    lines.append(
                        f"    peak={_display(density.get('peak_units'))} in "
                        f"{_display(density.get('window'))} {density_unit}; "
                        f"excess={_display(density.get('peak_excess'))}"
                    )
        for finding in _findings(file_result):
            if finding.get("suppressed") or finding.get("advisory"):
                continue
            location = _finding_location(path, finding)
            rule_id = _text(finding.get("rule_id") or "unknown")
            excerpt = _bounded(_text(finding.get("excerpt")), 120)
            lines.append(f"  {location} [{rule_id}] {excerpt}".rstrip())
            advice = _text(finding.get("advice"))
            if advice:
                lines.append(f"    {advice}")
        for error in _items(file_result.get("errors")):
            lines.append(
                f"  analysis error [{_text(error.get('error_code') or 'unknown')}]: "
                f"{_bounded(_text(error.get('message') or ''), 300)}"
            )
    suppressed = _count_findings(data, "suppressed")
    advisory = _count_findings(data, "advisory")
    if suppressed or advisory:
        lines.append(f"Suppressed: {suppressed}  Advisory: {advisory}")
    for error in _items(data.get("rule_errors")):
        lines.append(f"Rule error: {_bounded(_text(error.get('message') or error), 300)}")
    override = data.get("override")
    if isinstance(override, Mapping):
        lines.append(
            f"Override: {_text(override.get('reviewer') or 'unknown')} - "
            f"{_bounded(_text(override.get('reason') or ''), 300)}"
        )
    for audit in _items(data.get("external_audits")):
        lines.append(
            f"External rule {_text(audit.get('rule_id') or 'unknown')} sent selected prose to "
            f"{_text(audit.get('endpoint_hostname') or audit.get('service') or 'unknown')}; "
            f"outcome={_text(audit.get('outcome') or 'unknown')}"
        )
    return "\n".join(lines) + "\n"


def html_report(
    result: RunResult | Mapping[str, Any], *, sources: Mapping[str, str] | None = None
) -> str:
    """Render a self-contained HTML report with all result text escaped."""
    data = result_data(result)
    state = _upper(data.get("decision") or data.get("analysis_state") or "error")
    score = data.get("score")
    threshold = data.get("threshold")
    head_sha = _text(data.get("head_sha") or "")
    files = _items(data.get("files"))
    body: list[str] = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        f'<meta http-equiv="Content-Security-Policy" content="{_h(_CSP)}">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>Slop Cop report</title>",
        f"<style>{_STYLE}</style></head><body><main>",
        '<header><div class="report-heading">',
        f'<img class="report-logo" src="{_logo_data_uri()}" alt="" width="160" height="160">',
        '<div><p class="eyebrow">Slop Cop</p><h1>Dev Notes report</h1></div></div>',
        f'<p class="status {_status_class(state)}">{_h(state)}</p>',
        '<dl class="summary">',
        _dtdd("Score", _display(score)),
        _dtdd("Threshold", _display(threshold)),
        _dtdd("Head revision", head_sha or "Not supplied"),
        _dtdd("Analysis", _text(data.get("analysis_state") or "complete")),
        "</dl></header>",
        '<section aria-labelledby="meaning"><h2 id="meaning">Interpretation</h2>',
        "<p>The score summarizes configured editorial signals. It does not identify "
        "the author or determine whether a model wrote the text.</p></section>",
    ]
    override = data.get("override")
    if isinstance(override, Mapping):
        body.append(_render_override(override))
    if not files:
        message = (
            "No changed Dev Note required analysis."
            if state == "NOT APPLICABLE"
            else "No file results were produced."
        )
        body.append(f"<section><h2>Files</h2><p>{_h(message)}</p></section>")
    else:
        body.append(_render_file_table(files))
        for index, file_result in enumerate(files, 1):
            body.append(_render_file(file_result, index, sources=sources))
    body.append(_render_rule_errors(data))
    body.append(_render_external_audits(data))
    body.append(_render_provenance(data))
    body.append("</main></body></html>\n")
    rendered = "".join(body)
    if len(rendered.encode("utf-8")) > MAX_HTML_BYTES:
        raise ReportError(f"HTML report exceeds the {MAX_HTML_BYTES}-byte limit.")
    return rendered


def write_html_report(
    result: RunResult | Mapping[str, Any],
    destination: str | Path,
    *,
    sources: Mapping[str, str] | None = None,
) -> Path:
    """Write the self-contained HTML report."""
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_report(result, sources=sources), encoding="utf-8")
    return path


def write_report_directory(
    result: RunResult | Mapping[str, Any],
    destination: str | Path,
    *,
    sources: Mapping[str, str] | None = None,
) -> tuple[Path, Path]:
    """Write ``index.html`` and ``report.json`` into one artifact directory."""
    directory = Path(destination)
    directory.mkdir(parents=True, exist_ok=True)
    return (
        write_html_report(result, directory / "index.html", sources=sources),
        write_json_report(result, directory / "report.json"),
    )


def _render_file_table(files: list[dict[str, Any]]) -> str:
    rows = []
    for item in files:
        path = _text(item.get("path"))
        score = item.get("score")
        base = _base_score(item)
        state = _upper(item.get("decision") or item.get("analysis_state") or "complete")
        rows.append(
            "<tr>"
            f'<th scope="row">{_h(path)}</th><td>{_h(_display(score))}</td>'
            f"<td>{_h(_display(base))}</td><td>{_h(_signed(_delta(score, base)))}</td>"
            f"<td>{_h(state)}</td>"
            "</tr>"
        )
    return (
        '<section aria-labelledby="files"><h2 id="files">Files</h2><div class="table-wrap"><table>'
        "<thead><tr><th>Path</th><th>Head</th><th>Base</th><th>Delta</th><th>State</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div></section>"
    )


def _render_file(
    item: dict[str, Any],
    index: int,
    *,
    sources: Mapping[str, str] | None,
) -> str:
    path = _text(item.get("path"))
    findings = _findings(item)
    sections = [f'<article aria-labelledby="file-{index}"><h2 id="file-{index}">{_h(path)}</h2>']
    raw_metrics = item.get("metrics")
    metrics: Mapping[str, Any] = raw_metrics if isinstance(raw_metrics, Mapping) else {}
    source = sources.get(path) if sources is not None else None
    sections.append('<dl class="summary">')
    sections.append(_dtdd("Score", _display(item.get("score"))))
    sections.append(_dtdd("Words analyzed", _display(metrics.get("analyzable_words"))))
    sections.append(_dtdd("Code points masked", _display(metrics.get("masked_code_points"))))
    sections.append(_dtdd("Findings", str(len(findings))))
    sections.append("</dl>")
    sections.append(_render_costs(item))
    sections.append(_render_density(item, source=source if isinstance(source, str) else None))
    sections.append(_render_findings(findings))
    sections.append(_render_comparison(item))
    sections.append(_render_rule_errors(item))
    if isinstance(source, str):
        sections.append(
            '<details><summary>Escaped source view</summary><pre class="source"><code>'
            f"{_highlight_source(source, findings)}</code></pre></details>"
        )
    sections.append("</article>")
    return "".join(sections)


def _render_costs(item: Mapping[str, Any]) -> str:
    categories = _category_rows(item)
    rule_costs = _items(item.get("rule_costs"))
    if not categories and not rule_costs:
        return ""
    rows = [
        f"<tr><th>{_h(name)}</th><td>{_h(_display(cost))}</td></tr>" for name, cost in categories
    ]
    for rule in rule_costs:
        name = _text(rule.get("rule_id"))
        base = rule.get("base_cost", 0)
        density_value = rule.get("density")
        density = density_value.get("cost", 0) if isinstance(density_value, Mapping) else 0
        cost = rule.get("charged_cost", 0)
        allowance = rule.get("allowance", 0)
        rows.append(
            f"<tr><th>{_h(name)}</th><td>{_h(_display(cost))} "
            f"(base {_h(_display(base))}, density {_h(_display(density))}, "
            f"allowance {_h(_display(allowance))})</td></tr>"
        )
    return (
        "<details open><summary>Score arithmetic</summary><table><tbody>"
        + "".join(rows)
        + "</tbody></table></details>"
    )


def _render_density(item: Mapping[str, Any], *, source: str | None = None) -> str:
    records: list[dict[str, Any]] = []
    for owner_key, collection_key in (("rule_id", "rule_costs"), ("category", "category_costs")):
        for owner in _items(item.get(collection_key)):
            density = owner.get("density")
            if isinstance(density, Mapping):
                records.append({owner_key: owner.get(owner_key), **dict(density)})
    if not records:
        return ""
    rows = []
    for record in records:
        label = _text(record.get("rule_id") or record.get("category") or "density")
        window = f"{_display(record.get('window'))} {_text(record.get('unit') or 'units')}"
        passage = ""
        span = record.get("window_span")
        if not passage and source is not None and isinstance(span, Mapping):
            start, end = span.get("start"), span.get("end")
            if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(source):
                passage = source[start:end]
        rows.append(
            "<tr>"
            f"<th>{_h(label)}</th><td>{_h(window)}</td>"
            f"<td>{_h(_display(record.get('peak_excess')))}</td>"
            f"<td>{_h(_display(record.get('cost')))}</td>"
            f"<td><code>{_h(_bounded(passage, 240))}</code></td></tr>"
        )
    return (
        '<details><summary>Passage density</summary><div class="table-wrap"><table><thead><tr>'
        "<th>Rule or category</th><th>Window</th><th>Peak excess</th>"
        "<th>Points</th><th>Densest passage</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div></details>"
    )


def _render_findings(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "<section><h3>Findings</h3><p>No findings.</p></section>"
    groups: dict[str, list[dict[str, Any]]] = {
        "Blocking": [],
        "Chargeable": [],
        "Advisory": [],
        "Suppressed": [],
    }
    for finding in findings:
        if finding.get("suppressed"):
            groups["Suppressed"].append(finding)
        elif finding.get("blocking"):
            groups["Blocking"].append(finding)
        elif finding.get("advisory") or finding.get("chargeable") is False:
            groups["Advisory"].append(finding)
        else:
            groups["Chargeable"].append(finding)
    output = ['<section aria-labelledby="findings"><h3 id="findings">Findings</h3>']
    for name, values in groups.items():
        if not values:
            continue
        output.append(
            f'<details open><summary>{_h(name)} ({len(values)})</summary><ol class="findings">'
        )
        for finding in values:
            rule_id = _text(finding.get("rule_id") or "unknown")
            line = _display(finding.get("line"))
            column = _display(finding.get("column"))
            excerpt = _text(finding.get("excerpt"))
            rationale = _text(finding.get("explanation"))
            advice = _text(finding.get("advice"))
            charge = ' <span class="points">chargeable signal</span>'
            output.append(
                "<li>"
                f"<p><strong>{_h(rule_id)}</strong> at {line}:{column} "
                f"{charge}</p>"
                f"<pre><code>{_h(_bounded(excerpt, 1000))}</code></pre>"
                + (f"<p>{_h(rationale)}</p>" if rationale else "")
                + (f"<p><strong>Action:</strong> {_h(advice)}</p>" if advice else "")
                + _suppression_detail(finding)
                + "</li>"
            )
        output.append("</ol></details>")
    output.append("</section>")
    return "".join(output)


def _render_comparison(item: Mapping[str, Any]) -> str:
    base = item.get("base")
    if not isinstance(base, Mapping):
        return ""
    changes = base.get("findings")
    if not isinstance(changes, Mapping):
        return ""
    output = ["<details><summary>Base comparison</summary>"]
    for label, key in (("Added", "added"), ("Removed", "removed"), ("Persistent", "persistent")):
        findings = _items(changes.get(key))
        output.append(f"<h4>{label} ({len(findings)})</h4>")
        if findings:
            output.append("<ul>")
            for finding in findings:
                rule_id = _text(finding.get("rule_id") or "unknown")
                excerpt = _bounded(_text(finding.get("excerpt") or ""), 300)
                output.append(f"<li><code>{_h(rule_id)}</code>: {_h(excerpt)}</li>")
            output.append("</ul>")
    output.append("</details>")
    return "".join(output)


def _render_override(override: Mapping[str, Any]) -> str:
    actor = _text(override.get("reviewer"))
    reason = _text(override.get("reason") or "")
    review = _text(override.get("review_url"))
    sha = _text(override.get("head_sha") or "")
    return (
        '<section class="override"><h2>Manual override</h2><dl class="summary">'
        + _dtdd("Reviewer", actor)
        + _dtdd("Reason", reason)
        + _dtdd("Review", review)
        + _dtdd("Head revision", sha)
        + "</dl></section>"
    )


def _suppression_detail(finding: Mapping[str, Any]) -> str:
    reason = finding.get("suppression_reason")
    if not reason:
        return ""
    return f"<p><strong>Suppression:</strong> {_h(_text(reason))}</p>"


def _render_rule_errors(data: Mapping[str, Any]) -> str:
    errors = _items(data.get("rule_errors") or data.get("errors"))
    if not errors:
        return ""
    rows = []
    for error in errors:
        message = error.get("message") or "Rule execution failed."
        rows.append(
            "<li>"
            f"<strong>{_h(_text(error.get('rule_id') or 'analysis'))}</strong>: "
            f"{_h(_bounded(_text(message), 1000))}"
            f" ({'fatal' if error.get('fatal') else 'advisory'})</li>"
        )
    return f'<section class="errors"><h2>Analysis errors</h2><ul>{"".join(rows)}</ul></section>'


def _render_external_audits(data: Mapping[str, Any]) -> str:
    audits = _items(data.get("external_audits"))
    if not audits:
        return ""
    rows = []
    for audit in audits:
        service = audit.get("service")
        revision = audit.get("judge_revision") or "not reported"
        rows.append(
            "<tr>"
            f"<th>{_h(_text(audit.get('rule_id') or 'unknown'))}</th>"
            f"<td>{_h(_text(service))}</td>"
            f"<td>{_h(_text(revision))}</td>"
            f"<td>{_h(_display(audit.get('latency_ms')))}</td>"
            f"<td>{_h(_text(audit.get('outcome') or 'unknown'))}</td>"
            f"<td><code>{_h(_text(audit.get('response_digest') or ''))}</code></td>"
            "</tr>"
        )
    return (
        "<section><h2>External rule audit</h2>"
        "<p>Listed rules sent selected prose to the named service.</p>"
        '<div class="table-wrap"><table><thead><tr>'
        "<th>Rule</th><th>Service</th><th>Judge revision</th>"
        "<th>Latency (ms)</th><th>Outcome</th><th>Response digest</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>"
    )


def _render_provenance(data: Mapping[str, Any]) -> str:
    return (
        '<footer><h2>Run identity</h2><dl class="summary">'
        + _dtdd("Tool version", _text(data.get("tool_version") or "unknown"))
        + _dtdd("Configuration digest", _text(data.get("config_digest") or "unknown"))
        + _dtdd("JSON schema", _display(data.get("schema_version", JSON_SCHEMA_VERSION)))
        + _dtdd("Base revision", _text(data.get("base_sha") or "Not supplied"))
        + _dtdd("Head revision", _text(data.get("head_sha") or "Not supplied"))
        + "</dl></footer>"
    )


def _highlight_source(source: str, findings: list[dict[str, Any]]) -> str:
    spans: list[tuple[int, int, str]] = []
    for finding in findings:
        if finding.get("suppressed"):
            continue
        span = finding.get("span")
        start = span.get("start") if isinstance(span, Mapping) else None
        end = span.get("end") if isinstance(span, Mapping) else None
        if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(source):
            spans.append((start, end, _text(finding.get("rule_id") or "finding")))
    spans.sort(key=lambda value: (value[0], -(value[1] - value[0]), value[2]))
    output: list[str] = []
    cursor = 0
    for start, end, rule_id in spans:
        if start < cursor:
            continue
        output.append(_h(source[cursor:start]))
        output.append(f'<mark title="{_h(rule_id)}">{_h(source[start:end])}</mark>')
        cursor = end
    output.append(_h(source[cursor:]))
    return "".join(output)


def _findings(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _items(item.get("findings"))


def _category_rows(item: Mapping[str, Any]) -> list[tuple[str, Any]]:
    rows = _items(item.get("category_costs"))
    return [
        (
            _text(row.get("category")),
            row.get("charged_cost", 0),
        )
        for row in rows
    ]


def _base_score(item: Mapping[str, Any]) -> Any:
    base = item.get("base")
    return base.get("score") if isinstance(base, Mapping) else None


def _delta(head: Any, base: Any) -> int | float | None:
    if _number(head) is not None and _number(base) is not None:
        return _number(head) - _number(base)  # type: ignore[operator]
    return None


def _count_findings(data: Mapping[str, Any], field: str) -> int:
    return sum(
        1 for item in _items(data.get("files")) for finding in _findings(item) if finding.get(field)
    )


def _finding_location(path: str, finding: Mapping[str, Any]) -> str:
    line = finding.get("line")
    column = finding.get("column")
    return f"{path}:{_display(line)}:{_display(column)}"


def _items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _text(value: object) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        value = cast(Any, value).value
    return str(value)


def _h(value: object) -> str:
    return html.escape(_text(value), quote=True)


def _upper(value: object) -> str:
    return _text(value).upper().replace("_", " ")


def _display(value: object) -> str:
    if value is None or value == "":
        return "—"
    number = _number(value)
    return f"{number:g}" if number is not None else _text(value)


def _number(value: object) -> int | float | None:
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None


def _signed(value: object) -> str:
    number = _number(value)
    return "—" if number is None else f"{number:+g}"


def _bounded(value: str, maximum: int) -> str:
    return value if len(value) <= maximum else value[: maximum - 1] + "…"


def _dtdd(term: str, value: object) -> str:
    return f"<div><dt>{_h(term)}</dt><dd>{_h(value)}</dd></div>"


def _status_class(state: str) -> str:
    if state in {"PASS", "NOT APPLICABLE"}:
        return "pass"
    if state == "OVERRIDDEN":
        return "override"
    return "fail"


@cache
def _logo_data_uri() -> str:
    try:
        logo = files("slop_cop").joinpath("assets", "slop-cop.png").read_bytes()
    except OSError as error:
        raise ReportError("The Slop Cop report logo is unavailable.") from error
    return "data:image/png;base64," + b64encode(logo).decode("ascii")


_STYLE = """
:root { color-scheme: light dark; font-family: ui-sans-serif, system-ui, sans-serif; }
body { margin: 0; background: Canvas; color: CanvasText; }
main { max-width: 76rem; margin: auto; padding: 2rem; }
header, section, article, footer { margin-block: 1.5rem; }
.report-heading { display: flex; align-items: center; gap: 1rem; }
.report-heading h1 { margin-block: .25rem; }
.report-logo { width: 6rem; height: 6rem; object-fit: contain; }
article {
  border-top: 2px solid color-mix(in srgb, CanvasText 25%, transparent);
  padding-top: 1rem;
}
.eyebrow { font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
.status { display: inline-block; padding: .35rem .65rem; border-radius: .25rem; font-weight: 800; }
.pass { background: #176b3a; color: white; } .fail { background: #a12d2d; color: white; }
.override { background: #6d4d00; color: white; } section.override { padding: 1rem; }
.summary { display: flex; flex-wrap: wrap; gap: .75rem 2rem; }
.summary div { min-width: 10rem; }
dt { font-size: .85rem; opacity: .75; }
dd { margin: .2rem 0 0; font-weight: 650; }
.table-wrap { overflow-x: auto; } table { border-collapse: collapse; width: 100%; }
th, td {
  border-bottom: 1px solid color-mix(in srgb, CanvasText 20%, transparent);
  padding: .55rem; text-align: left; vertical-align: top;
}
pre {
  overflow-x: auto; padding: .75rem;
  background: color-mix(in srgb, CanvasText 7%, Canvas); white-space: pre-wrap;
}
.source { white-space: pre; }
mark { background: #ffe66d; color: #171717; }
.points { white-space: nowrap; }
.findings > li { margin-block: 1rem; }
details { margin-block: 1rem; }
summary { cursor: pointer; font-weight: 700; }
.errors { border-left: .35rem solid #a12d2d; padding-left: 1rem; }
code { overflow-wrap: anywhere; }
"""
