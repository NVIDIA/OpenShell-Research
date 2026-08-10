from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, Field

from slop_cop.config import RegexFlag, Severity, SlopCopConfig, load_config
from slop_cop.document import build_document
from slop_cop.rules.api import (
    FunctionRule,
    RuleContext,
    RuleEvaluation,
    RuleMetadata,
    RuleSignal,
)
from slop_cop.rules.registry import build_registry
from slop_cop.runtime import RuleRuntime, RuntimeManager

CONFIG_PATH = Path(__file__).parents[1] / "slop-cop.toml"


async def _evaluate(context: RuleContext, runtime: object) -> RuleEvaluation:
    start = context.projected_prose.find("repeated claim")
    if start < 0:
        return RuleEvaluation()
    return RuleEvaluation(signals=(RuleSignal(start=start, end=start + 14, key="repeated claim"),))


def test_python_custom_rule_uses_the_same_registry_contract() -> None:
    rule = FunctionRule(
        RuleMetadata(
            id="custom.repeated-claim",
            category="repetition",
            title="Repeated claim",
            rationale="The claim repeats without new evidence.",
            advice="Keep the instance supported by evidence.",
        ),
        _evaluate,
    )
    raw = load_config(CONFIG_PATH).model_dump(mode="python")
    raw["rules"][rule.metadata.id] = {
        "severity": Severity.WARNING,
        "max_signal_units": 1,
        "fixed_allowance": 0,
        "first_cost": 2.0,
        "repeat_cost": 1.0,
        "cap": 5.0,
    }
    config = SlopCopConfig.model_validate(raw)
    registry = build_registry(config, custom_rules=(rule,))
    document = build_document("note.md", "This is a repeated claim in visible prose.")
    evaluation = asyncio.run(
        registry.by_id(rule.metadata.id).rule.evaluate(RuleContext(document), object())
    )
    assert evaluation.signals[0].start == 10


def test_declarative_phrase_and_regex_rules_require_no_core_edits() -> None:
    raw = load_config(CONFIG_PATH).model_dump(mode="python")
    raw["custom_rules"] = {
        "phrase": (
            {
                "id": "custom.empty-intensifier",
                "version": 1,
                "category": "vocabulary",
                "severity": Severity.WARNING,
                "title": "Empty intensifier",
                "rationale": "The phrase asserts importance without a concrete effect.",
                "advice": "Name the concrete effect.",
                "phrases": ("deeply transformative",),
                "max_signal_units": 1,
                "fixed_allowance": 0,
                "first_cost": 2.0,
                "repeat_cost": 1.0,
                "cap": 5.0,
            },
        ),
        "regex": (
            {
                "id": "custom.generic-promise",
                "version": 1,
                "category": "rhetoric",
                "severity": Severity.WARNING,
                "title": "Generic promise",
                "rationale": "The sentence promises unspecified later detail.",
                "advice": "Name the follow-up topic or remove the promise.",
                "pattern": r"\bmore on (?:that|this) (?:later|soon)\b",
                "flags": (RegexFlag.IGNORECASE,),
                "max_signal_units": 1,
                "fixed_allowance": 0,
                "first_cost": 2.0,
                "repeat_cost": 1.0,
                "cap": 5.0,
            },
        ),
    }
    registry = build_registry(SlopCopConfig.model_validate(raw))

    async def evaluate(rule_id: str, source: str) -> RuleEvaluation:
        document = build_document("note.md", source)
        return await registry.by_id(rule_id).rule.evaluate(RuleContext(document), object())

    phrase = asyncio.run(
        evaluate("custom.empty-intensifier", "This is deeply transformative work.")
    )
    assert [(signal.start, signal.end) for signal in phrase.signals] == [(8, 29)]
    assert not asyncio.run(
        evaluate("custom.empty-intensifier", "This changes request routing.")
    ).signals

    regex = asyncio.run(
        evaluate("custom.generic-promise", "More on this later, after the benchmark.")
    )
    assert [(signal.start, signal.end) for signal in regex.signals] == [(0, 18)]
    assert not asyncio.run(
        evaluate("custom.generic-promise", "The benchmark follows in the next section.")
    ).signals


def test_declarative_duplicate_and_dangling_ids_fail_clearly() -> None:
    duplicate = load_config(CONFIG_PATH).model_dump(mode="python")
    duplicate["custom_rules"] = {
        "phrase": (
            {
                "id": "rhetoric.not-just",
                "version": 1,
                "category": "rhetoric",
                "severity": Severity.INFO,
                "title": "Duplicate",
                "rationale": "This ID already exists.",
                "advice": "Use a unique ID.",
                "phrases": ("duplicate phrase",),
                "max_signal_units": 1,
                "fixed_allowance": 0,
                "first_cost": 0.0,
                "repeat_cost": 0.0,
                "cap": 0.0,
            },
        )
    }
    with pytest.raises(ValueError, match="unique"):
        SlopCopConfig.model_validate(duplicate)

    dangling = load_config(CONFIG_PATH).model_dump(mode="python")
    dangling["rules"]["custom.missing-rule"] = {
        "severity": Severity.INFO,
        "max_signal_units": 1,
        "fixed_allowance": 0,
        "first_cost": 0.0,
        "repeat_cost": 0.0,
        "cap": 0.0,
    }
    with pytest.raises(ValueError, match="unknown Python rules"):
        build_registry(SlopCopConfig.model_validate(dangling))


class JudgeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int
    judge_revision: str
    label: str
    strength: int = Field(ge=1, le=5)
    explanation: str = Field(max_length=200)
    evidence: tuple[str, ...] = ()


def _external_config() -> SlopCopConfig:
    raw = load_config(CONFIG_PATH).model_dump(mode="python")
    raw["services"] = {
        "editorial_judge": {
            "url": "https://judge.example/v1/evaluate",
            "token_env": "SLOP_COP_JUDGE_TOKEN",
            "timeout_seconds": 5.0,
            "max_response_bytes": 4096,
            "max_attempts": 1,
        }
    }
    raw["rules"]["custom.editorial-judge"] = {
        "severity": Severity.WARNING,
        "service": "editorial_judge",
        "max_signal_units": 5,
        "fixed_allowance": 0,
        "first_cost": 4.0,
        "repeat_cost": 2.0,
        "cap": 12.0,
        "settings": {"required_judge_revision": "editorial-v1"},
    }
    return SlopCopConfig.model_validate(raw)


def _judge_rule() -> FunctionRule:
    metadata = RuleMetadata(
        id="custom.editorial-judge",
        category="rhetoric",
        title="Editorial judge",
        rationale="The configured judge found formulaic prose.",
        advice="Review the cited prose and state the claim directly.",
        execution_kind="external",
        services=("editorial_judge",),
    )

    async def evaluate(context: RuleContext, runtime: RuleRuntime) -> RuleEvaluation:
        response = await runtime.service().post_json(
            {"schema_version": 1, "prose": context.projected_prose}
        )
        result = JudgeResponse.model_validate(response.data)
        if result.schema_version != 1:
            raise ValueError("unsupported judge response schema")
        expected = runtime.settings["required_judge_revision"]
        if result.judge_revision != expected:
            raise ValueError("judge revision does not match configured revision")
        signal = RuleSignal.document(
            key=result.label,
            units=result.strength,
            detail=result.explanation,
            evidence=context.map_exact_quotes(result.evidence),
        )
        return RuleEvaluation(
            signals=(signal,), audit={**response.audit, "judge_revision": result.judge_revision}
        )

    return FunctionRule(metadata, evaluate)


def test_external_custom_rule_maps_evidence_and_bounded_strength() -> None:
    async def run() -> RuleEvaluation:
        response_body = {
            "schema_version": 1,
            "judge_revision": "editorial-v1",
            "label": "formulaic",
            "strength": 3,
            "explanation": "The conclusion repeats a generic promise.",
            "evidence": ["paving the way"],
        }
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response_body))
        )
        config = _external_config()
        rule = _judge_rule()
        registry = build_registry(config, custom_rules=(rule,))
        document = build_document("note.md", "This is paving the way for later work.")
        manager = RuntimeManager(
            config,
            client=client,
            environment={"SLOP_COP_JUDGE_TOKEN": "secret"},
        )
        evaluation = await registry.by_id(rule.metadata.id).rule.evaluate(
            RuleContext(document),
            manager.for_rule(rule.metadata, registry.by_id(rule.metadata.id).policy),
        )
        await client.aclose()
        return evaluation

    evaluation = asyncio.run(run())
    assert evaluation.signals[0].units == 3
    assert evaluation.signals[0].evidence[0].start == 8
    assert evaluation.audit["judge_revision"] == "editorial-v1"
    assert "response_digest" in evaluation.audit


@pytest.mark.parametrize(
    "response_body",
    [
        {
            "schema_version": 1,
            "judge_revision": "stale-v0",
            "label": "formulaic",
            "strength": 1,
            "explanation": "Stale judge response.",
        },
        {
            "schema_version": 1,
            "judge_revision": "editorial-v1",
            "label": "formulaic",
            "strength": 99,
            "explanation": "Invalid strength.",
            "unexpected": True,
        },
    ],
)
def test_external_custom_rule_rejects_stale_or_malformed_response(
    response_body: Mapping[str, Any],
) -> None:
    async def run() -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response_body))
        )
        config = _external_config()
        rule = _judge_rule()
        manager = RuntimeManager(
            config,
            client=client,
            environment={"SLOP_COP_JUDGE_TOKEN": "secret"},
        )
        try:
            await rule.evaluate(
                RuleContext(build_document("note.md", "Direct prose for review.")),
                manager.for_rule(
                    rule.metadata,
                    build_registry(config, custom_rules=(rule,)).by_id(rule.metadata.id).policy,
                ),
            )
        finally:
            await client.aclose()

    with pytest.raises((ValueError, RuntimeError)):
        asyncio.run(run())
