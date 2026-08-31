"""Command-line interface for Slop Cop."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from slop_cop import __version__
from slop_cop.benchmarks import (
    evaluate_benchmarks,
    git_source_loader,
    load_benchmark_manifest,
)
from slop_cop.config import SlopCopConfig, load_config
from slop_cop.document import Document, build_document
from slop_cop.engine import EngineOutput, analyze_document
from slop_cop.findings import (
    AnalysisState,
    BaseComparison,
    Decision,
    ExternalAudit,
    Finding,
    FindingChange,
    OverrideRecord,
    RuleExecutionError,
    RunResult,
)
from slop_cop.report import (
    ReportError,
    terminal_report,
    write_json_report,
    write_report_directory,
)
from slop_cop.rules.registry import RuleKind, RuleRegistry, build_registry

EXIT_OK = 0
EXIT_POLICY = 1
EXIT_ERROR = 2
_REVISION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")


def _default_config() -> Path:
    return Path(__file__).resolve().parents[2] / "slop-cop.toml"


def _default_benchmarks() -> Path:
    return Path(__file__).resolve().parents[2] / "benchmarks" / "dev-note-history.toml"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="slop-cop",
        description="Review Dev Notes for configured editorial signals.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_config(command: argparse.ArgumentParser) -> None:
        command.add_argument("--config", type=Path, default=_default_config())

    check = subparsers.add_parser("check", help="Analyze one or more Dev Notes.")
    add_config(check)
    check.add_argument("paths", nargs="*", help="Markdown paths beneath the repository root.")
    check.add_argument("--repository-root", type=Path)
    baseline = check.add_mutually_exclusive_group()
    baseline.add_argument("--baseline-ref")
    baseline.add_argument("--baseline-root", type=Path)
    check.add_argument("--html-dir", type=Path)
    check.add_argument("--json", dest="json_path", type=Path)
    check.add_argument("--only-rule")
    check.add_argument("--repository")
    check.add_argument("--pull-request-number", type=int)
    check.add_argument("--base-sha")
    check.add_argument("--head-sha")
    check.add_argument("--override-json", type=Path)

    list_rules = subparsers.add_parser("list-rules", help="List configured rules.")
    add_config(list_rules)
    list_rules.add_argument("--kind", choices=("builtin", "declarative", "custom"))

    explain = subparsers.add_parser("explain", help="Explain one configured rule.")
    add_config(explain)
    explain.add_argument("rule_id")

    validate = subparsers.add_parser(
        "validate-rules", help="Validate configuration and rule registry."
    )
    add_config(validate)
    validate.add_argument("--cases", type=Path)

    benchmark = subparsers.add_parser(
        "benchmark", help="Check scores against historical calibration references."
    )
    add_config(benchmark)
    benchmark.add_argument("--manifest", type=Path, default=_default_benchmarks())
    benchmark.add_argument("--repository-root", type=Path)
    return parser


def _repository_root(value: Path | None) -> Path:
    if value is not None:
        root = value.resolve(strict=True)
        if not root.is_dir():
            raise ValueError(f"repository root is not a directory: {root}")
        return root
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def _resolve_input(root: Path, value: str) -> tuple[str, Path]:
    supplied = Path(value)
    unresolved = supplied if supplied.is_absolute() else Path.cwd() / supplied
    lexical = Path(os.path.abspath(unresolved))
    try:
        lexical_relative = lexical.relative_to(root)
    except ValueError as error:
        raise ValueError(f"input is outside the repository root: {value}") from error
    current = root
    for part in lexical_relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"input must not contain symlinks: {value}")
    physical = lexical.resolve(strict=True)
    try:
        relative = physical.relative_to(root)
    except ValueError as error:
        raise ValueError(f"input is outside the repository root: {value}") from error
    if physical.is_symlink() or not physical.is_file():
        raise ValueError(f"input must be a regular non-symlink file: {value}")
    if relative.suffix.casefold() != ".md":
        raise ValueError(f"input must be a Markdown file: {value}")
    return relative.as_posix(), physical


def _load_head_document(config: SlopCopConfig, logical: str, physical: Path) -> Document:
    return build_document(
        logical,
        physical.read_bytes(),
        contexts=config.contexts,
        max_source_bytes=config.source_max_bytes,
    )


def _load_base_document(
    config: SlopCopConfig,
    logical: str,
    *,
    root: Path,
    baseline_root: Path | None,
    baseline_ref: str | None,
) -> Document | None:
    content: bytes | None = None
    if baseline_root is not None:
        if baseline_root.is_symlink():
            raise ValueError(f"baseline root must not be a symlink: {baseline_root}")
        base_root = baseline_root.resolve(strict=True)
        unresolved_candidate = base_root / logical
        current = base_root
        for part in Path(logical).parts:
            current /= part
            if current.is_symlink():
                raise ValueError(f"baseline input must not contain symlinks: {logical}")
        candidate = unresolved_candidate.resolve(strict=False)
        try:
            candidate.relative_to(base_root)
        except ValueError as error:
            raise ValueError(f"baseline path escapes its root: {logical}") from error
        if candidate.exists():
            if candidate.is_symlink() or not candidate.is_file():
                raise ValueError(f"baseline input is not a regular non-symlink file: {logical}")
            content = candidate.read_bytes()
    elif baseline_ref is not None:
        if (
            not _REVISION.fullmatch(baseline_ref)
            or ".." in baseline_ref
            or baseline_ref.startswith("-")
        ):
            raise ValueError("baseline ref contains unsupported characters")
        completed = subprocess.run(
            ["git", "show", f"{baseline_ref}:{logical}"],
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if completed.returncode == 0:
            content = completed.stdout
    if content is None:
        return None
    return build_document(
        logical,
        content,
        contexts=config.contexts,
        max_source_bytes=config.source_max_bytes,
    )


def _finding_changes(base: tuple[Finding, ...], head: tuple[Finding, ...]) -> FindingChange:
    def key(finding: Finding) -> tuple[str, str, str]:
        return finding.rule_id, finding.normalized_key, finding.score_group

    base_by_key: dict[tuple[str, str, str], deque[Finding]] = defaultdict(deque)
    for finding in base:
        base_by_key[key(finding)].append(finding)
    added: list[Finding] = []
    persistent: list[Finding] = []
    for finding in head:
        matches = base_by_key[key(finding)]
        if matches:
            matches.popleft()
            persistent.append(finding)
        else:
            added.append(finding)
    removed = [finding for matches in base_by_key.values() for finding in matches]
    return FindingChange(added=tuple(added), removed=tuple(removed), persistent=tuple(persistent))


async def _analyze(
    document: Document,
    registry: RuleRegistry,
    config: SlopCopConfig,
    base_document: Document | None,
) -> tuple[EngineOutput, Document]:
    output = await analyze_document(document, registry, config)
    if base_document is None:
        return output, document
    try:
        base = await analyze_document(base_document, registry, config)
        comparison = BaseComparison(
            score=base.file_result.score,
            delta=(
                output.file_result.score - base.file_result.score
                if output.file_result.score is not None and base.file_result.score is not None
                else None
            ),
            analysis_state=base.file_result.analysis_state,
            findings=_finding_changes(base.file_result.findings, output.file_result.findings),
            errors=base.file_result.errors,
        )
    except Exception as error:
        comparison = BaseComparison(
            analysis_state=AnalysisState.INCOMPLETE,
            errors=(
                RuleExecutionError(
                    source_path=document.path,
                    error_code="base_analysis_failed",
                    message=f"base comparison could not complete: {type(error).__name__}",
                    fatal=False,
                ),
            ),
        )
    return EngineOutput(
        file_result=output.file_result.model_copy(update={"base": comparison}),
        external_audits=output.external_audits,
    ), document


def _external_audit(value: Mapping[str, Any]) -> ExternalAudit:
    return ExternalAudit(
        rule_id=str(value["rule_id"]),
        rule_version=int(value["rule_version"]),
        service=str(value.get("service", "unknown")),
        endpoint_hostname=str(value.get("endpoint_hostname") or value.get("hostname") or "unknown"),
        content_digest=str(value.get("content_digest") or value.get("request_content_hash")),
        request_schema_version=str(value.get("request_schema_version", "1")),
        response_schema_version=(
            str(value["response_schema_version"])
            if value.get("response_schema_version") is not None
            else None
        ),
        service_request_id=(
            str(value.get("service_request_id") or value.get("request_id"))
            if value.get("service_request_id") or value.get("request_id")
            else None
        ),
        judge_revision=(
            str(value["judge_revision"]) if value.get("judge_revision") is not None else None
        ),
        attempts=int(value.get("attempts", 1)),
        latency_ms=round(float(value.get("latency_ms", 0))),
        outcome=str(value.get("outcome", "unknown")),
        response_digest=(
            str(value["response_digest"]) if value.get("response_digest") is not None else None
        ),
    )


def _load_override(path: Path | None, head_sha: str | None) -> OverrideRecord | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        override = OverrideRecord.model_validate(value)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as error:
        raise ValueError(f"invalid override record: {error}") from error
    if not head_sha or override.head_sha != head_sha:
        raise ValueError("override review is not attached to the analyzed head revision")
    return override


async def _check(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if not args.paths:
        result = RunResult(
            analysis_state=AnalysisState.NOT_APPLICABLE,
            decision=Decision.NOT_APPLICABLE,
            score=None,
            threshold=config.threshold,
            repository=args.repository,
            pull_request_number=args.pull_request_number,
            base_sha=args.base_sha,
            head_sha=args.head_sha,
            tool_version=__version__,
            config_digest=config.digest,
        )
        sys.stdout.write(terminal_report(result))
        if args.html_dir:
            write_report_directory(result, args.html_dir)
        if args.json_path:
            write_json_report(result, args.json_path)
        return EXIT_OK
    root = _repository_root(args.repository_root)
    registry = build_registry(config)
    if args.only_rule:
        registry = RuleRegistry((registry.by_id(args.only_rule),))
    resolved = [_resolve_input(root, value) for value in args.paths]
    baseline_root = args.baseline_root.resolve(strict=True) if args.baseline_root else None
    tasks = []
    sources: dict[str, str] = {}
    projections: dict[str, str] = {}
    for logical, physical in resolved:
        document = _load_head_document(config, logical, physical)
        sources[logical] = document.source
        projections[logical] = document.prose_projection
        base = _load_base_document(
            config,
            logical,
            root=root,
            baseline_root=baseline_root,
            baseline_ref=args.baseline_ref,
        )
        tasks.append(_analyze(document, registry, config, base))
    analyzed = await asyncio.gather(*tasks)
    file_results = tuple(output.file_result for output, _ in analyzed)
    scores = [result.score for result in file_results if result.score is not None]
    score = min(scores) if scores else None
    if any(result.analysis_state is AnalysisState.ERROR for result in file_results):
        analysis_state = AnalysisState.ERROR
    elif any(result.analysis_state is AnalysisState.INCOMPLETE for result in file_results):
        analysis_state = AnalysisState.INCOMPLETE
    else:
        analysis_state = AnalysisState.COMPLETE
    decision = (
        Decision.PASS
        if all(result.decision is Decision.PASS for result in file_results)
        else Decision.FAIL
    )
    override = _load_override(args.override_json, args.head_sha)
    if (
        override is not None
        and analysis_state is AnalysisState.COMPLETE
        and decision is Decision.FAIL
    ):
        decision = Decision.OVERRIDDEN
    else:
        override = None
    audits = tuple(
        _external_audit(audit) for output, _ in analyzed for audit in output.external_audits
    )
    result = RunResult(
        analysis_state=analysis_state,
        decision=decision,
        score=score,
        threshold=config.threshold,
        repository=args.repository,
        pull_request_number=args.pull_request_number,
        base_sha=args.base_sha,
        head_sha=args.head_sha,
        tool_version=__version__,
        config_digest=config.digest,
        files=file_results,
        external_audits=audits,
        override=override,
    )
    sys.stdout.write(terminal_report(result))
    if args.html_dir:
        write_report_directory(
            result,
            args.html_dir,
            sources=sources,
            projections=projections,
        )
    if args.json_path:
        write_json_report(result, args.json_path)
    return EXIT_OK if result.decision in {Decision.PASS, Decision.OVERRIDDEN} else EXIT_POLICY


def _list_rules(config: SlopCopConfig, kind: RuleKind | None) -> int:
    registry = build_registry(config)
    for configured in registry.list(kind):
        metadata = configured.metadata
        state = "enabled" if configured.policy.enabled else "disabled"
        network = ""
        if metadata.execution_kind == "external":
            service = configured.policy.service or "unconfigured"
            service_config = config.services.get(service)
            host = service_config.url if service_config is not None else "unconfigured"
            network = f"; sends selected prose to {host}"
        print(f"{metadata.id}\t{configured.kind}\t{state}\t{metadata.title}{network}")
    return EXIT_OK


def _explain(config: SlopCopConfig, rule_id: str) -> int:
    configured = build_registry(config).by_id(rule_id)
    metadata = configured.metadata
    print(f"{metadata.id} (version {metadata.version})")
    print(f"Title: {metadata.title}")
    print(f"Category: {metadata.category}")
    print(f"Kind: {configured.kind}; execution: {metadata.execution_kind}")
    print(f"Rationale: {metadata.rationale}")
    print(f"Action: {metadata.advice}")
    print("Policy:")
    print(json.dumps(configured.policy.model_dump(mode="json"), indent=2, sort_keys=True))
    if metadata.execution_kind == "external":
        service = configured.policy.service
        endpoint = config.services[service].url if service else "unconfigured"
        print(f"Content transfer: sends selected prose to {endpoint}")
    return EXIT_OK


def _validate_cases(registry: RuleRegistry, path: Path) -> None:
    import tomllib

    if not path.exists():
        raise ValueError(f"rule case file does not exist: {path}")
    values = tomllib.loads(path.read_text(encoding="utf-8")).get("case", [])
    coverage: dict[str, set[str]] = defaultdict(set)
    for value in values:
        rule_id = str(value.get("rule_id", ""))
        registry.by_id(rule_id)
        coverage[rule_id].add(str(value.get("kind", "")))
    for configured in registry.enabled():
        if configured.policy.cap > 0 and coverage[configured.metadata.id] < {
            "positive",
            "counterexample",
        }:
            raise ValueError(
                f"scored rule {configured.metadata.id!r} lacks positive and counterexample cases"
            )


def _validate(config: SlopCopConfig, config_path: Path, cases: Path | None) -> int:
    registry = build_registry(config)
    effective_cases = cases
    if effective_cases is None:
        candidate = config_path.resolve().parent / "tests" / "rule_cases.toml"
        if candidate.exists():
            effective_cases = candidate
    if effective_cases is not None:
        _validate_cases(registry, effective_cases)
    print(f"Valid: {len(registry.rules)} rules; configuration {config.digest}")
    return EXIT_OK


async def _benchmark(config: SlopCopConfig, args: argparse.Namespace) -> int:
    root = _repository_root(args.repository_root)
    manifest = load_benchmark_manifest(args.manifest)
    results = await evaluate_benchmarks(
        manifest,
        config=config,
        registry=build_registry(config),
        source_loader=git_source_loader(root),
    )
    print("score  expected  decision  reference")
    for result in results:
        reference = result.reference
        status = "ok" if result.within_range else "DRIFT"
        expected = f"{reference.min_score}-{reference.max_score}"
        print(
            f"{result.score:>5}  {expected:>8}  {result.decision.value:<8}  "
            f"{status:<5}  {reference.name}"
        )
    return EXIT_OK if all(result.within_range for result in results) else EXIT_POLICY


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "check":
            return asyncio.run(_check(args))
        config = load_config(args.config)
        if args.command == "list-rules":
            return _list_rules(config, args.kind)
        if args.command == "explain":
            return _explain(config, args.rule_id)
        if args.command == "validate-rules":
            return _validate(config, args.config, args.cases)
        if args.command == "benchmark":
            return asyncio.run(_benchmark(config, args))
    except KeyError as error:
        print(f"slop-cop: unknown rule {error.args[0]!r}", file=sys.stderr)
        return EXIT_ERROR
    except (OSError, UnicodeError, ValueError, ValidationError, ReportError) as error:
        print(f"slop-cop: {error}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
