from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from slop_cop.config import DeclarativeRuleBase, RulePolicy, SlopCopConfig
from slop_cop.rules.api import Rule, RuleMetadata
from slop_cop.rules.builtins import BUILTIN_RULES
from slop_cop.rules.custom import CUSTOM_RULES
from slop_cop.rules.declarative import PhraseRule, RegexRule

RuleKind = Literal["builtin", "declarative", "custom"]


@dataclass(frozen=True, slots=True)
class ConfiguredRule:
    rule: Rule
    policy: RulePolicy
    kind: RuleKind
    order: int

    @property
    def metadata(self) -> RuleMetadata:
        return self.rule.metadata


@dataclass(frozen=True, slots=True)
class RuleRegistry:
    rules: tuple[ConfiguredRule, ...]

    def __iter__(self) -> Iterator[ConfiguredRule]:
        return iter(self.rules)

    def by_id(self, rule_id: str) -> ConfiguredRule:
        for configured in self.rules:
            if configured.metadata.id == rule_id:
                return configured
        raise KeyError(rule_id)

    def enabled(self) -> tuple[ConfiguredRule, ...]:
        return tuple(rule for rule in self.rules if rule.policy.enabled)

    def list(self, kind: RuleKind | None = None) -> tuple[ConfiguredRule, ...]:
        return tuple(rule for rule in self.rules if kind is None or rule.kind == kind)


def _declarative_policy(definition: DeclarativeRuleBase) -> RulePolicy:
    return RulePolicy.model_validate(
        {
            "enabled": definition.enabled,
            "severity": definition.severity,
            "blocking": definition.blocking,
            "on_error": definition.on_error,
            "max_signal_units": definition.max_signal_units,
            "fixed_allowance": definition.fixed_allowance,
            "first_cost": definition.first_cost,
            "repeat_cost": definition.repeat_cost,
            "cap": definition.cap,
            "document_density": definition.document_density,
            "density": definition.density,
        }
    )


def build_registry(
    config: SlopCopConfig,
    *,
    builtins: tuple[Rule, ...] = BUILTIN_RULES,
    custom_rules: tuple[Rule, ...] = CUSTOM_RULES,
) -> RuleRegistry:
    rows: list[tuple[Rule, RulePolicy, RuleKind]] = []
    python_ids = {rule.metadata.id for rule in (*builtins, *custom_rules)}
    if len(python_ids) != len((*builtins, *custom_rules)):
        raise ValueError("Python rule IDs must be unique")

    configured_ids = set(config.rules)
    missing = python_ids - configured_ids
    dangling = configured_ids - python_ids
    if missing:
        raise ValueError(f"rules lack policy blocks: {', '.join(sorted(missing))}")
    if dangling:
        raise ValueError(
            f"policy blocks reference unknown Python rules: {', '.join(sorted(dangling))}"
        )

    for rule in builtins:
        rows.append((rule, config.rules[rule.metadata.id], "builtin"))

    declarative_ids: set[str] = set()
    for phrase_definition in config.custom_rules.phrase:
        metadata = RuleMetadata(
            id=phrase_definition.id,
            version=phrase_definition.version,
            category=phrase_definition.category,
            title=phrase_definition.title,
            rationale=phrase_definition.rationale,
            advice=phrase_definition.advice,
            score_group=phrase_definition.score_group,
        )
        phrase_rule: Rule = PhraseRule(metadata, phrase_definition.phrases)
        rows.append(
            (
                phrase_rule,
                _declarative_policy(phrase_definition),
                "declarative",
            )
        )
        declarative_ids.add(phrase_definition.id)
    for regex_definition in config.custom_rules.regex:
        metadata = RuleMetadata(
            id=regex_definition.id,
            version=regex_definition.version,
            category=regex_definition.category,
            title=regex_definition.title,
            rationale=regex_definition.rationale,
            advice=regex_definition.advice,
            score_group=regex_definition.score_group,
        )
        regex_rule: Rule = RegexRule(
            metadata,
            regex_definition.pattern,
            tuple(flag.value for flag in regex_definition.flags),
        )
        rows.append(
            (
                regex_rule,
                _declarative_policy(regex_definition),
                "declarative",
            )
        )
        declarative_ids.add(regex_definition.id)
    for rule in custom_rules:
        rows.append((rule, config.rules[rule.metadata.id], "custom"))

    all_ids = [rule.metadata.id for rule, _, _ in rows]
    if len(set(all_ids)) != len(all_ids):
        raise ValueError("rule IDs must be unique across all registry sources")

    configured: list[ConfiguredRule] = []
    for order, (rule, policy, kind) in enumerate(rows):
        metadata = rule.metadata
        if metadata.category not in config.categories:
            raise ValueError(
                f"rule {metadata.id!r} references unknown category {metadata.category!r}"
            )
        declared = set(metadata.services)
        selected = {policy.service} if policy.service is not None else set()
        if metadata.execution_kind == "external":
            if not selected or not selected.issubset(declared):
                raise ValueError(
                    f"external rule {metadata.id!r} must select one of its declared services"
                )
        elif selected:
            raise ValueError(f"local rule {metadata.id!r} cannot select a service")
        configured.append(ConfiguredRule(rule, policy, kind, order))
    return RuleRegistry(tuple(configured))
