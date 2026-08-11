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
        charged_rule_ids = _charged_rule_ids(file_result)
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
            if finding.get("rule_id") not in charged_rule_ids and not finding.get("blocking"):
                continue
            location = _finding_location(path, finding)
            rule_id = _text(finding.get("rule_id") or "unknown")
            excerpt = _bounded(_text(finding.get("excerpt")), 120)
            lines.append(f"  {location} [{rule_id}] {excerpt}".rstrip())
            advice = _text(finding.get("advice"))
            if advice:
                lines.append(f"    {advice}")
        findings = _findings(file_result)
        within_allowance = sum(
            1
            for finding in findings
            if finding.get("chargeable")
            and not finding.get("suppressed")
            and not finding.get("blocking")
            and finding.get("rule_id") not in charged_rule_ids
        )
        advisory = sum(1 for finding in findings if finding.get("advisory"))
        suppressed = sum(1 for finding in findings if finding.get("suppressed"))
        if within_allowance or advisory or suppressed:
            lines.append(
                "  unscored signals: "
                f"within_allowance={within_allowance} advisory={advisory} "
                f"suppressed={suppressed}"
            )
        for error in _items(file_result.get("errors")):
            lines.append(
                f"  analysis error [{_text(error.get('error_code') or 'unknown')}]: "
                f"{_bounded(_text(error.get('message') or ''), 300)}"
            )
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
        '<dl class="summary run-summary">',
        _dtdd("Score", _display(score)),
        _dtdd("Threshold", _display(threshold)),
        _dtdd("Head revision", head_sha or "Not supplied"),
        _dtdd("Analysis", _text(data.get("analysis_state") or "complete")),
        "</dl></header>",
        '<p class="interpretation">The score measures configured editorial signals. '
        "It does not identify the author or determine whether a model wrote the text.</p>",
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
        if len(files) > 1:
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
        findings = _findings(item)
        charged_rule_ids = _charged_rule_ids(item)
        scored = sum(
            1
            for finding in findings
            if finding.get("rule_id") in charged_rule_ids
            and finding.get("chargeable")
            and not finding.get("suppressed")
        )
        unscored = sum(
            1
            for finding in findings
            if not finding.get("blocking")
            and not finding.get("suppressed")
            and finding.get("rule_id") not in charged_rule_ids
        )
        rows.append(
            "<tr>"
            f'<th scope="row">{_h(path)}</th><td>{_h(_display(score))}</td>'
            f"<td>{_h(_display(base))}</td><td>{_h(_signed(_delta(score, base)))}</td>"
            f"<td>{scored}</td><td>{unscored}</td><td>{_h(state)}</td>"
            "</tr>"
        )
    return (
        '<section aria-labelledby="files"><h2 id="files">Files</h2><div class="table-wrap"><table>'
        "<thead><tr><th>Path</th><th>Score</th><th>Base</th><th>Delta</th>"
        "<th>Contributing</th><th>Unscored</th><th>State</th></tr></thead>"
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
    sections = [
        f'<article aria-labelledby="file-{index}"><h2 class="file-heading" '
        f'id="file-{index}">{_h(path)}</h2>'
    ]
    raw_metrics = item.get("metrics")
    metrics: Mapping[str, Any] = raw_metrics if isinstance(raw_metrics, Mapping) else {}
    source = sources.get(path) if sources is not None else None
    charged_rule_ids = _charged_rule_ids(item)
    scored = sum(
        1
        for finding in findings
        if finding.get("rule_id") in charged_rule_ids
        and finding.get("chargeable")
        and not finding.get("suppressed")
    )
    unscored = sum(
        1
        for finding in findings
        if not finding.get("blocking")
        and not finding.get("suppressed")
        and finding.get("rule_id") not in charged_rule_ids
    )
    sections.append('<dl class="summary">')
    sections.append(_dtdd("Score", _display(item.get("score"))))
    sections.append(_dtdd("Contributing signals", scored))
    sections.append(_dtdd("Unscored signals", unscored))
    sections.append(_dtdd("Words analyzed", _display(metrics.get("analyzable_words"))))
    sections.append(_dtdd("Code points masked", _display(metrics.get("masked_code_points"))))
    sections.append("</dl>")
    sections.append(_render_costs(item))
    sections.append(_render_density(item, source=source if isinstance(source, str) else None))
    sections.append(
        _render_findings(
            findings,
            source=source if isinstance(source, str) else None,
            charged_rule_ids=charged_rule_ids,
        )
    )
    sections.append(_render_comparison(item))
    sections.append(_render_rule_errors(item))
    if isinstance(source, str):
        scored_findings = [
            finding
            for finding in findings
            if (finding.get("rule_id") in charged_rule_ids or finding.get("blocking"))
            and not finding.get("suppressed")
        ]
        source_label = (
            "Analyzed source with contributing signals highlighted"
            if scored_findings
            else "Analyzed source"
        )
        sections.append(
            f"<details><summary>{source_label}</summary>"
            '<pre class="source"><code>'
            f"{_highlight_source(source, scored_findings)}</code></pre></details>"
        )
    sections.append("</article>")
    return "".join(sections)


def _render_costs(item: Mapping[str, Any]) -> str:
    categories = _category_rows(item)
    rule_costs = [
        rule for rule in _items(item.get("rule_costs")) if _number(rule.get("charged_cost"))
    ]
    if not categories and not rule_costs:
        return '<section class="deductions"><h3>Score deductions</h3><p>None.</p></section>'
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
        '<section class="deductions"><h3>Score deductions</h3><div class="table-wrap">'
        '<p class="quiet">Category totals determine the score; rule rows explain those totals.</p>'
        "<table><tbody>" + "".join(rows) + "</tbody></table></div></section>"
    )


def _render_density(item: Mapping[str, Any], *, source: str | None = None) -> str:
    records: list[dict[str, Any]] = []
    for owner_key, collection_key in (("rule_id", "rule_costs"), ("category", "category_costs")):
        for owner in _items(item.get(collection_key)):
            density = owner.get("density")
            if isinstance(density, Mapping) and (
                _number(density.get("cost")) or _number(density.get("peak_excess"))
            ):
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


def _render_findings(
    findings: list[dict[str, Any]],
    *,
    source: str | None,
    charged_rule_ids: set[str],
) -> str:
    if not findings:
        return "<section><h3>Findings</h3><p>No editorial signals detected.</p></section>"
    blocking: list[dict[str, Any]] = []
    scored: list[dict[str, Any]] = []
    unscored: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for finding in findings:
        if finding.get("suppressed"):
            suppressed.append(finding)
        elif finding.get("blocking"):
            blocking.append(finding)
        elif finding.get("rule_id") in charged_rule_ids and finding.get("chargeable"):
            scored.append(finding)
        else:
            unscored.append(finding)

    output = ['<section aria-labelledby="findings"><h3 id="findings">Findings</h3>']
    important = blocking + scored
    if important:
        output.append('<div class="finding-list">')
        output.extend(_render_finding(finding, source=source) for finding in important)
        output.append("</div>")
    else:
        output.append('<p class="quiet">No findings affect the score.</p>')
    if unscored:
        output.append(_render_unscored_summary(unscored, source=source))
    if suppressed:
        output.append(
            f"<details><summary>Suppressed findings ({len(suppressed)})</summary>"
            '<div class="finding-list">'
        )
        output.extend(_render_finding(finding, source=source) for finding in suppressed)
        output.append("</div></details>")
    output.append("</section>")
    return "".join(output)


def _render_finding(finding: Mapping[str, Any], *, source: str | None) -> str:
    rule_id = _text(finding.get("rule_id") or "unknown")
    line = _display(finding.get("line"))
    column = _display(finding.get("column"))
    rationale = _text(finding.get("explanation"))
    advice = _text(finding.get("advice"))
    label = "Blocking" if finding.get("blocking") else "Contributing"
    return (
        '<div class="finding">'
        '<div class="finding-heading">'
        f'<code class="rule-id">{_h(rule_id)}</code>'
        f'<span class="location">line {line}, column {column}</span>'
        f'<span class="badge">{label}</span></div>'
        + _render_finding_context(finding, source=source)
        + (f'<p class="rationale">{_h(rationale)}</p>' if rationale else "")
        + (f'<p class="action"><strong>Suggested edit:</strong> {_h(advice)}</p>' if advice else "")
        + _suppression_detail(finding)
        + "</div>"
    )


def _render_finding_context(finding: Mapping[str, Any], *, source: str | None) -> str:
    span = finding.get("span")
    if source is not None and isinstance(span, Mapping):
        start, end = span.get("start"), span.get("end")
        if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(source):
            left, right = _context_bounds(source, start, end)
            prefix = "…" if left else ""
            suffix = "…" if right < len(source) else ""
            before = _compact(source[left:start])
            match = _compact(source[start:end])
            after = _compact(source[end:right])
            return (
                '<blockquote class="context">'
                f"{_h(prefix + before)}<mark>{_h(match)}</mark>{_h(after + suffix)}"
                "</blockquote>"
            )
    excerpt = _text(finding.get("excerpt"))
    return f'<blockquote class="context"><mark>{_h(excerpt)}</mark></blockquote>'


def _context_bounds(source: str, start: int, end: int, radius: int = 180) -> tuple[int, int]:
    floor = max(0, start - radius)
    left = floor
    for marker in (". ", "! ", "? ", "\n"):
        position = source.rfind(marker, floor, start)
        if position >= left:
            left = position + len(marker)
    ceiling = min(len(source), end + radius)
    right = ceiling
    endings = [
        position + 1
        for marker in (".", "!", "?", "\n")
        if (position := source.find(marker, end, ceiling)) >= 0
    ]
    if endings:
        right = min(endings)
    if left == floor and left > 0:
        whitespace = source.find(" ", left, start)
        if whitespace >= 0:
            left = whitespace + 1
    if right == ceiling and right < len(source):
        whitespace = source.rfind(" ", end, right)
        if whitespace >= end:
            right = whitespace
    return left, right


def _compact(value: str) -> str:
    compact = " ".join(value.split())
    if compact and value[:1].isspace():
        compact = " " + compact
    if compact and value[-1:].isspace():
        compact += " "
    return compact


def _render_unscored_summary(findings: list[dict[str, Any]], *, source: str | None) -> str:
    groups: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        groups.setdefault(_text(finding.get("rule_id") or "unknown"), []).append(finding)
    sections = []
    for rule_id, values in sorted(groups.items()):
        advice = _text(values[0].get("advice"))
        effect = "Within allowance" if values[0].get("chargeable") else "Advisory"
        matches = []
        for finding in values:
            line = _display(finding.get("line"))
            matches.append(
                f'<li><span class="location">line {line}</span>'
                f"{_render_finding_context(finding, source=source)}</li>"
            )
        review = (
            f"<details><summary>Review {len(values)} match"
            f"{'es' if len(values) != 1 else ''}</summary>"
            f'<ol class="compact-matches">{"".join(matches)}</ol></details>'
        )
        sections.append(
            '<section class="signal-group"><div class="signal-heading">'
            f'<code class="rule-id">{_h(rule_id)}</code>'
            f'<span class="signal-count">{len(values)} match'
            f"{'es' if len(values) != 1 else ''}</span>"
            f'<span class="signal-effect">{_h(effect)}</span></div>'
            + (f'<p class="signal-advice">{_h(advice)}</p>' if advice else "")
            + review
            + "</section>"
        )
    return (
        f'<details class="advisories"><summary>Unscored signals ({len(findings)} across '
        f'{len(groups)} rules; no score effect)</summary><div class="signal-groups">'
        f"{''.join(sections)}</div></details>"
    )


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


def _charged_rule_ids(item: Mapping[str, Any]) -> set[str]:
    return {
        _text(rule.get("rule_id"))
        for rule in _items(item.get("rule_costs"))
        if _number(rule.get("charged_cost"))
    }


def _category_rows(item: Mapping[str, Any]) -> list[tuple[str, Any]]:
    rows = _items(item.get("category_costs"))
    return [
        (
            _text(row.get("category")),
            row.get("charged_cost", 0),
        )
        for row in rows
        if _number(row.get("charged_cost"))
    ]


def _base_score(item: Mapping[str, Any]) -> Any:
    base = item.get("base")
    return base.get("score") if isinstance(base, Mapping) else None


def _delta(head: Any, base: Any) -> int | float | None:
    if _number(head) is not None and _number(base) is not None:
        return _number(head) - _number(base)  # type: ignore[operator]
    return None


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
main { max-width: 72rem; margin: auto; padding: 1.5rem 2rem 3rem; }
header, section, article, footer { margin-block: 1.5rem; }
.report-heading { display: flex; align-items: center; gap: 1rem; }
.report-heading h1 { margin-block: .25rem; }
.report-logo { width: 6rem; height: 6rem; object-fit: contain; }
article {
  border-top: 2px solid color-mix(in srgb, CanvasText 25%, transparent);
  padding-top: 1rem;
}
.file-heading { font-size: clamp(1.2rem, 2.4vw, 1.65rem); overflow-wrap: anywhere; }
.interpretation {
  max-width: 52rem; margin-block: 1rem; padding: .75rem 1rem;
  border-left: .25rem solid #5272b8;
  background: color-mix(in srgb, #5272b8 10%, Canvas);
}
.eyebrow { font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
.status { display: inline-block; padding: .35rem .65rem; border-radius: .25rem; font-weight: 800; }
.pass { background: #176b3a; color: white; } .fail { background: #a12d2d; color: white; }
.override { background: #6d4d00; color: white; } section.override { padding: 1rem; }
.summary { display: flex; flex-wrap: wrap; gap: .65rem; }
.summary div {
  min-width: 8rem; padding: .65rem .8rem; border-radius: .4rem;
  background: color-mix(in srgb, CanvasText 6%, Canvas);
}
dt { font-size: .85rem; opacity: .75; }
dd { margin: .2rem 0 0; font-weight: 650; }
.table-wrap { overflow-x: auto; } table { border-collapse: collapse; width: 100%; }
th, td {
  border-bottom: 1px solid color-mix(in srgb, CanvasText 20%, transparent);
  padding: .5rem .6rem; text-align: left; vertical-align: top;
}
thead th { font-size: .8rem; opacity: .75; text-transform: uppercase; letter-spacing: .04em; }
.deductions { margin-block: 1.25rem; }
.finding-list { display: grid; gap: .8rem; }
.finding {
  border: 1px solid color-mix(in srgb, CanvasText 18%, transparent);
  border-left: .3rem solid #a12d2d; border-radius: .35rem; padding: .8rem 1rem;
}
.finding p { margin-block: .5rem; }
.finding-heading { display: flex; flex-wrap: wrap; align-items: center; gap: .45rem .75rem; }
.rule-id { font-weight: 750; }
.location { font-size: .9rem; opacity: .72; }
.badge {
  margin-left: auto; padding: .15rem .45rem; border-radius: 999px;
  background: #a12d2d; color: white; font-size: .75rem; font-weight: 800;
  text-transform: uppercase; letter-spacing: .04em;
}
.context {
  margin: .7rem 0; padding: .7rem .85rem; border-left: .2rem solid #d0a000;
  background: color-mix(in srgb, #d0a000 9%, Canvas); line-height: 1.55;
}
.compact-matches { margin: .6rem 0; padding-left: 1.25rem; }
.compact-matches li + li { margin-top: .65rem; }
.compact-matches .context { margin: .25rem 0; }
.signal-groups { display: grid; gap: .65rem; margin-top: .75rem; }
.signal-group {
  margin: 0; padding: .7rem .85rem; border-radius: .35rem;
  border: 1px solid color-mix(in srgb, CanvasText 16%, transparent);
}
.signal-heading { display: flex; flex-wrap: wrap; align-items: center; gap: .4rem .75rem; }
.signal-count { font-size: .85rem; opacity: .72; }
.signal-effect {
  margin-left: auto; font-size: .75rem; font-weight: 750;
  text-transform: uppercase; letter-spacing: .04em;
}
.signal-advice { margin: .45rem 0; opacity: .8; }
.rationale { opacity: .82; }
.quiet { opacity: .75; }
.advisories summary { color: color-mix(in srgb, CanvasText 80%, #5272b8); }
pre {
  overflow-x: auto; padding: .75rem;
  background: color-mix(in srgb, CanvasText 7%, Canvas); white-space: pre-wrap;
}
.source { white-space: pre; }
mark { background: #ffe66d; color: #171717; }
details { margin-block: 1rem; }
summary { cursor: pointer; font-weight: 700; }
.errors { border-left: .35rem solid #a12d2d; padding-left: 1rem; }
code { overflow-wrap: anywhere; }
@media (max-width: 42rem) {
  main { padding: 1rem; }
  .report-logo { width: 4.5rem; height: 4.5rem; }
  .badge { margin-left: 0; }
}
"""
