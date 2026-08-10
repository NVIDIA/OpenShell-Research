from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import regex

from slop_cop.rules.api import RuleContext, RuleEvaluation, RuleMetadata, RuleSignal

_ALLOWED_FLAGS = {
    "IGNORECASE": regex.IGNORECASE,
    "MULTILINE": regex.MULTILINE,
    "DOTALL": regex.DOTALL,
    "VERBOSE": regex.VERBOSE,
}
_UNSAFE_PATTERN_PARTS = (
    "(?<=",
    "(?<!",
    "(?(",
    "(?R",
    "(?0",
    "(?P>",
    "(?&",
    "\\g<",
)


def validate_pattern(pattern: str, flags: tuple[str, ...] = ()) -> regex.Pattern[str]:
    if not pattern or len(pattern) > 500:
        raise ValueError("regex patterns must contain 1 to 500 characters")
    unknown = set(flags) - _ALLOWED_FLAGS.keys()
    if unknown:
        raise ValueError(f"unsupported regex flags: {', '.join(sorted(unknown))}")
    if any(part in pattern for part in _UNSAFE_PATTERN_PARTS):
        raise ValueError("regex pattern uses an unsupported construct")
    if regex.search(r"\\[1-9]", pattern):
        raise ValueError("regex backreferences are not supported")
    if regex.search(r"\(\?[aiLmsuxw-]+[:)]", pattern):
        raise ValueError("inline regex flags are not supported")
    value = regex.VERSION1
    for name in flags:
        value |= _ALLOWED_FLAGS[name]
    try:
        return regex.compile(pattern, value)
    except regex.error as error:
        raise ValueError(f"invalid regex pattern: {error}") from error


@dataclass(frozen=True, slots=True)
class RegexRule:
    metadata: RuleMetadata
    pattern: str
    flags: tuple[str, ...] = ()
    timeout_seconds: float = 0.05
    _compiled: regex.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_compiled", validate_pattern(self.pattern, self.flags))

    async def evaluate(self, context: RuleContext, runtime: Any) -> RuleEvaluation:
        signals: list[RuleSignal] = []
        try:
            for match in self._compiled.finditer(
                context.projected_prose, timeout=self.timeout_seconds
            ):
                start, end = match.span()
                signals.append(
                    RuleSignal(
                        start=start,
                        end=end,
                        key=" ".join(match.group(0).casefold().split()),
                    )
                )
        except TimeoutError as error:
            raise RuntimeError(f"{self.metadata.id} exceeded its regex timeout") from error
        return RuleEvaluation(signals=tuple(signals))


@dataclass(frozen=True, slots=True)
class PhraseRule:
    metadata: RuleMetadata
    phrases: tuple[str, ...]
    _delegate: RegexRule = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.phrases or any(not phrase.strip() for phrase in self.phrases):
            raise ValueError("phrase rules require nonempty phrases")
        alternatives = []
        for phrase in sorted(set(self.phrases), key=len, reverse=True):
            words = regex.findall(r"[\p{L}\p{N}]+", phrase)
            if not words:
                raise ValueError(f"phrase contains no words: {phrase!r}")
            alternatives.append(r"(?:[\s\p{P}]+)".join(regex.escape(word) for word in words))
        pattern = rf"\b(?:{'|'.join(alternatives)})\b"
        object.__setattr__(
            self,
            "_delegate",
            RegexRule(self.metadata, pattern, ("IGNORECASE",)),
        )

    async def evaluate(self, context: RuleContext, runtime: Any) -> RuleEvaluation:
        return await self._delegate.evaluate(context, runtime)
