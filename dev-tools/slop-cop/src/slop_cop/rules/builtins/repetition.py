from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

import regex

from slop_cop.rules.api import FunctionRule, RuleContext, RuleEvaluation, RuleMetadata, RuleSignal

_WORD = regex.compile(r"[\p{L}\p{N}]+(?:['’-][\p{L}\p{N}]+)*", regex.VERSION1)
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "with",
    ]
)


def _ngram(context: RuleContext) -> list[RuleSignal]:
    prose = context.projected_prose
    occurrences: dict[tuple[str, ...], list[tuple[int, int, int]]] = defaultdict(list)
    for paragraph_index, paragraph in enumerate(context.paragraphs):
        words = list(_WORD.finditer(prose, paragraph.start, paragraph.end))
        normalized = [word.group(0).casefold() for word in words]
        for size in range(4, 6):
            for index in range(len(words) - size + 1):
                key = tuple(normalized[index : index + size])
                if sum(item not in _STOPWORDS for item in key) < 3:
                    continue
                if " ".join(key) in context.repository_terms:
                    continue
                occurrences[key].append(
                    (words[index].start(), words[index + size - 1].end(), paragraph_index)
                )
    candidates: list[tuple[int, int, str]] = []
    for key, spans in occurrences.items():
        if len({span[2] for span in spans}) < 3:
            continue
        candidates.extend((start, end, " ".join(key)) for start, end, _ in spans)
    candidates.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    accepted: list[tuple[int, int, str]] = []
    occupied_starts: set[int] = set()
    for candidate in candidates:
        if candidate[0] not in occupied_starts:
            occupied_starts.add(candidate[0])
            accepted.append(candidate)
    return [RuleSignal(start=start, end=end, key=key) for start, end, key in accepted]


def _sentence_opener(context: RuleContext) -> list[RuleSignal]:
    ignored = _STOPWORDS | {"also", "however", "therefore", "then", "instead"}
    values: list[tuple[int, int, str]] = []
    for sentence in context.sentences:
        words = list(_WORD.finditer(context.projected_prose, sentence.start, sentence.end))
        while words and words[0].group(0).casefold() in ignored:
            words.pop(0)
        if len(words) < 3:
            continue
        chosen = words[:3]
        values.append(
            (
                chosen[0].start(),
                chosen[-1].end(),
                " ".join(word.group(0).casefold() for word in chosen),
            )
        )
    counts: dict[str, int] = defaultdict(int)
    for _, _, key in values:
        counts[key] += 1
    return [
        RuleSignal(start=start, end=end, key=key) for start, end, key in values if counts[key] >= 3
    ]


def _template_shape(context: RuleContext) -> list[RuleSignal]:
    matches: list[tuple[int, int, str]] = []
    patterns = {
        "not-but": regex.compile(r"\bnot\b[^.!?\n]{1,80}\bbut\b", regex.I),
        "from-to": regex.compile(r"\bfrom\b[^.!?\n]{1,60}\bto\b", regex.I),
    }
    for key, pattern in patterns.items():
        matches.extend((m.start(), m.end(), key) for m in pattern.finditer(context.projected_prose))
    counts: dict[str, int] = defaultdict(int)
    for _, _, key in matches:
        counts[key] += 1
    return [
        RuleSignal(start=start, end=end, key=key)
        for start, end, key in sorted(matches)
        if counts[key] >= 3
    ]


def _emphatic_fragments(context: RuleContext) -> list[RuleSignal]:
    values: list[tuple[int, int, str]] = []
    prose = context.projected_prose
    for paragraph in context.paragraphs:
        paragraph_values: list[tuple[int, int, str]] = []
        for sentence in context.sentences:
            start, end = sentence.start, sentence.end
            if not paragraph.start <= start < paragraph.end:
                continue
            text = prose[start:end].strip()
            line_start = prose.rfind("\n", 0, start) + 1
            prefix = prose[line_start:start]
            if regex.fullmatch(r"\s*(?:[-+*]|\d+[.)])\s*", prefix):
                continue
            words = _WORD.findall(text)
            if regex.fullmatch(r"(?:\d+|[ivxlcdm]+)", text.rstrip(".!?"), regex.I):
                continue
            if 1 <= len(words) <= 4 and end < len(prose):
                paragraph_values.append((start, end, "short-fragment"))
        if len(paragraph_values) >= 3:
            values.extend(paragraph_values)
    return [RuleSignal(start=start, end=end, key=key) for start, end, key in values]


def _hedge_stack(context: RuleContext) -> list[RuleSignal]:
    hedge = r"(?:arguably|perhaps|possibly|potentially|generally|typically|often|may|might|could)"
    pattern = regex.compile(rf"\b{hedge}\b(?:[^.!?\n]{{0,60}}\b{hedge}\b){{2,}}", regex.I)
    return [
        RuleSignal(start=match.start(), end=match.end(), key="hedge-stack")
        for match in pattern.finditer(context.projected_prose)
    ]


def _question_answer(context: RuleContext) -> list[RuleSignal]:
    pattern = regex.compile(
        r"(?:^|(?<=[.!?])\s+)([^?\n]{2,100}\?\s+"
        r"(?:the answer|the key|the result|simple|yes|no)\b)",
        regex.I,
    )
    return [
        RuleSignal(start=match.start(1), end=match.end(1), key="question-answer")
        for match in pattern.finditer(context.projected_prose)
    ]


Detector = Callable[[RuleContext], list[RuleSignal]]


def _rule(
    rule_id: str,
    title: str,
    rationale: str,
    advice: str,
    detector: Detector,
    *,
    overlap_priority: int = 0,
) -> FunctionRule:
    def evaluate(context: RuleContext, runtime: object) -> RuleEvaluation:
        return RuleEvaluation(signals=tuple(detector(context)))

    return FunctionRule(
        RuleMetadata(
            id=rule_id,
            category="repetition",
            title=title,
            rationale=rationale,
            advice=advice,
            score_group=detector.__name__.removeprefix("_"),
            overlap_priority=overlap_priority,
        ),
        evaluate,
    )


RULES = (
    _rule(
        "repetition.ngram",
        "Repeated phrase",
        "A phrase repeats across several paragraphs.",
        "Remove redundant instances or use the precise term only where needed.",
        _ngram,
    ),
    _rule(
        "repetition.sentence-opener",
        "Repeated sentence opener",
        "Several sentences begin with the same three meaningful words.",
        "Vary the sentence structure or combine related claims.",
        _sentence_opener,
    ),
    _rule(
        "repetition.template-shape",
        "Repeated rhetorical shape",
        "The same rhetorical construction recurs several times.",
        "State the claims with structures suited to their content.",
        _template_shape,
        overlap_priority=20,
    ),
    _rule(
        "repetition.emphatic-fragments",
        "Emphatic fragment cluster",
        "Several short sentence fragments create a repetitive cadence.",
        "Combine fragments with the claims they qualify.",
        _emphatic_fragments,
    ),
    _rule(
        "repetition.hedge-stack",
        "Stacked hedges",
        "Several hedges weaken one bounded claim.",
        "Choose the uncertainty qualifier that accurately describes the evidence.",
        _hedge_stack,
    ),
    _rule(
        "repetition.question-answer",
        "Question-answer setup",
        "The prose uses a canned question followed by its own answer.",
        "State the answer directly unless the question is needed for navigation.",
        _question_answer,
    ),
)
