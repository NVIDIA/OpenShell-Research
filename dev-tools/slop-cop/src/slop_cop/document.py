"""Source-preserving Markdown projection and prose segmentation."""

from __future__ import annotations

import bisect
import re
import unicodedata
from itertools import pairwise
from pathlib import Path

import regex
from pydantic import Field, model_validator

from slop_cop.config import MAX_SOURCE_BYTES, ContextConfig, RuleId, StrictModel

_FENCE_OPEN = re.compile(
    r"^(?P<quote>(?: {0,3}>[ \t]?)*)(?P<indent> {0,3})"
    r"(?P<fence>`{3,}|~{3,})[^\r\n]*(?:\r?\n|$)",
    re.MULTILINE,
)
_BLOCK_INTERRUPT = re.compile(
    r"^(?: {4}|\t| {0,3}(?:#{1,6}(?:\s|$)|[-+*]\s|\d+[.)]\s|`{3,}|~{3,}|"
    r"(?:\*\s*){3,}$|(?:-\s*){3,}$|(?:_\s*){3,}$|<[/!?A-Za-z]))"
)
_SUPPRESSION = re.compile(
    r"^[ \t]*<!--\s*slop-cop:\s*ignore-next=(?P<ids>[a-z0-9.,-]+)\s+"
    r'reason="(?P<reason>[^"\r\n]{1,500})"\s*-->[ \t]*(?:\r?\n|$)',
    re.MULTILINE,
)
_GENERATED_START = "<!-- dev-note:byline:start -->"
_GENERATED_END = "<!-- dev-note:byline:end -->"
_WORD = regex.compile(r"[\p{L}\p{N}](?:[\p{L}\p{N}\p{M}]|['’](?=[\p{L}\p{N}])|-(?=[\p{L}\p{N}]))*")
_ABBREVIATIONS = frozenset({"e.g.", "i.e.", "mr.", "mrs.", "ms.", "dr.", "vs.", "etc."})


class ProjectionError(ValueError):
    """Raised when Markdown cannot be projected without ambiguous source ranges."""

    def __init__(self, message: str, source: str, offset: int = 0) -> None:
        line_starts = _line_starts(source)
        line, column = line_column(line_starts, offset)
        super().__init__(f"{message} at line {line}, column {column}")
        self.offset = offset
        self.line = line
        self.column = column


class Span(StrictModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_order(self) -> Span:
        if self.end < self.start:
            raise ValueError("span end must not precede start")
        return self


class MaskedRange(Span):
    reasons: tuple[str, ...] = Field(min_length=1)


class ProseSegment(Span):
    text: str
    normalized: str


class SuppressionDirective(StrictModel):
    rule_ids: tuple[RuleId, ...] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=500)
    directive_span: Span
    target_span: Span


class DocumentMetrics(StrictModel):
    source_bytes: int = Field(ge=0, le=MAX_SOURCE_BYTES)
    source_code_points: int = Field(ge=0)
    analyzable_words: int = Field(ge=0)
    analyzable_sentences: int = Field(ge=0)
    analyzable_paragraphs: int = Field(ge=0)
    masked_code_points: int = Field(ge=0)


class Document(StrictModel):
    path: str = Field(min_length=1, max_length=4_096)
    source: str
    prose_projection: str
    line_starts: tuple[int, ...]
    front_matter: tuple[tuple[str, str], ...] = ()
    masked_ranges: tuple[MaskedRange, ...]
    suppressions: tuple[SuppressionDirective, ...]
    tokens: tuple[ProseSegment, ...]
    sentences: tuple[ProseSegment, ...]
    paragraphs: tuple[ProseSegment, ...]
    metrics: DocumentMetrics

    @model_validator(mode="after")
    def validate_projection(self) -> Document:
        if len(self.source) != len(self.prose_projection):
            raise ValueError("prose projection must have the same length as source")
        for index, character in enumerate(self.source):
            if character in "\r\n" and self.prose_projection[index] != character:
                raise ValueError("prose projection must preserve line endings")
        if self.line_starts != _line_starts(self.source):
            raise ValueError("line-start index does not match source")
        return self

    def line_column(self, offset: int) -> tuple[int, int]:
        if not 0 <= offset <= len(self.source):
            raise ValueError("offset lies outside source")
        return line_column(self.line_starts, offset)

    def source_span(self, span: Span) -> str:
        if span.end > len(self.source):
            raise ValueError("span lies outside source")
        return self.source[span.start : span.end]


def normalize_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def line_column(line_starts: tuple[int, ...], offset: int) -> tuple[int, int]:
    line_index = bisect.bisect_right(line_starts, offset) - 1
    return line_index + 1, offset - line_starts[line_index] + 1


def _line_starts(source: str) -> tuple[int, ...]:
    return (0, *(match.end() for match in re.finditer("\n", source)))


def _line_end(source: str, offset: int) -> int:
    newline = source.find("\n", offset)
    return len(source) if newline < 0 else newline + 1


def _covered(offset: int, ranges: list[tuple[int, int, str]]) -> bool:
    return any(start <= offset < end for start, end, _ in ranges)


def _strip_blockquote_markers(text: str) -> tuple[str, int]:
    depth = 0
    while (marker := re.match(r"^ {0,3}>[ \t]?", text)) is not None:
        text = text[marker.end() :]
        depth += 1
    return text, depth


def _add_front_matter(
    source: str, ranges: list[tuple[int, int, str]]
) -> tuple[tuple[str, str], ...]:
    first_end = _line_end(source, 0)
    if source[:first_end].rstrip("\r\n") != "---":
        return ()
    cursor = first_end
    end = None
    while cursor < len(source):
        next_end = _line_end(source, cursor)
        if source[cursor:next_end].rstrip("\r\n") in {"---", "..."}:
            end = next_end
            break
        cursor = next_end
    if end is None:
        raise ProjectionError("unterminated YAML front matter", source)
    ranges.append((0, end, "front-matter"))
    metadata: list[tuple[str, str]] = []
    for line in source[first_end:cursor].splitlines():
        match = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*?)\s*$", line)
        if match and match.group(1) in {"title", "description", "date", "author"}:
            metadata.append((match.group(1), match.group(2).strip("'\"")))
    return tuple(metadata)


def _add_generated_ranges(source: str, ranges: list[tuple[int, int, str]]) -> None:
    cursor = 0
    while True:
        start = source.find(_GENERATED_START, cursor)
        end_only = source.find(_GENERATED_END, cursor)
        if start < 0:
            if end_only >= 0:
                raise ProjectionError("generated byline end marker has no start", source, end_only)
            return
        if end_only >= 0 and end_only < start:
            raise ProjectionError("generated byline end marker has no start", source, end_only)
        end_marker = source.find(_GENERATED_END, start + len(_GENERATED_START))
        if end_marker < 0:
            raise ProjectionError("unterminated generated byline range", source, start)
        nested = source.find(_GENERATED_START, start + len(_GENERATED_START), end_marker)
        if nested >= 0:
            raise ProjectionError("nested generated byline start marker", source, nested)
        end = end_marker + len(_GENERATED_END)
        ranges.append((start, end, "generated-byline"))
        cursor = end


def _add_fenced_code(source: str, ranges: list[tuple[int, int, str]]) -> None:
    cursor = 0
    while cursor < len(source):
        match = _FENCE_OPEN.search(source, cursor)
        if match is None:
            return
        cursor = match.end()
        if _covered(match.start(), ranges):
            continue
        fence = match.group("fence")
        quote_depth = match.group("quote").count(">")
        closer = re.compile(
            rf"^(?: {{0,3}}>[ \t]?){{{quote_depth}}} {{0,3}}"
            rf"{re.escape(fence[0])}{{{len(fence)},}}[ \t]*(?:\r?\n|$)",
            re.MULTILINE,
        ).search(source, match.end())
        if closer is None:
            raise ProjectionError("unterminated fenced code block", source, match.start())
        ranges.append((match.start(), closer.end(), "fenced-code"))
        cursor = closer.end()


def _add_indented_code(source: str, ranges: list[tuple[int, int, str]]) -> None:
    lines = list(re.finditer(r"^.*(?:\r?\n|$)", source, re.MULTILINE))
    in_block = False
    block_start = 0
    block_end = 0
    previous_blank = True
    for line in lines:
        text = line.group(0).rstrip("\r\n")
        content, _ = _strip_blockquote_markers(text)
        blank = not content.strip()
        indented = content.startswith("    ") or content.startswith("\t")
        if in_block:
            if indented or blank:
                block_end = line.end()
            else:
                ranges.append((block_start, block_end, "indented-code"))
                in_block = False
        if not in_block and indented and previous_blank and not _covered(line.start(), ranges):
            in_block = True
            block_start = line.start()
            block_end = line.end()
        previous_blank = blank
    if in_block:
        ranges.append((block_start, block_end, "indented-code"))


def _add_suppressions(
    source: str, ranges: list[tuple[int, int, str]]
) -> list[tuple[tuple[RuleId, ...], str, Span]]:
    parsed: list[tuple[tuple[RuleId, ...], str, Span]] = []
    matches = list(_SUPPRESSION.finditer(source))
    valid_starts = {match.start() for match in matches}
    for occurrence in re.finditer(r"<!--\s*slop-cop:", source):
        line_start = source.rfind("\n", 0, occurrence.start()) + 1
        if line_start not in valid_starts and not _covered(occurrence.start(), ranges):
            raise ProjectionError(
                "invalid Slop Cop suppression directive", source, occurrence.start()
            )
    for match in matches:
        if _covered(match.start(), ranges):
            continue
        ids = tuple(part.strip() for part in match.group("ids").split(","))
        if len(set(ids)) != len(ids):
            raise ProjectionError("suppression rule IDs must be unique", source, match.start())
        reason = match.group("reason").strip()
        if not reason:
            raise ProjectionError("suppression reason must not be blank", source, match.start())
        span = Span(start=match.start(), end=match.end())
        ranges.append((span.start, span.end, "suppression-directive"))
        parsed.append((ids, reason, span))
    return parsed


def _add_html_comments(source: str, ranges: list[tuple[int, int, str]]) -> None:
    cursor = 0
    while True:
        start = source.find("<!--", cursor)
        if start < 0:
            return
        end = source.find("-->", start + 4)
        if end < 0:
            if not _covered(start, ranges):
                raise ProjectionError("unterminated HTML comment", source, start)
            return
        end += 3
        if not _covered(start, ranges):
            ranges.append((start, end, "html-comment"))
        cursor = end


def _add_inline_code(source: str, ranges: list[tuple[int, int, str]]) -> None:
    cursor = 0
    while cursor < len(source):
        start = source.find("`", cursor)
        if start < 0:
            return
        if _covered(start, ranges):
            cursor = start + 1
            continue
        run_end = start + 1
        while run_end < len(source) and source[run_end] == "`":
            run_end += 1
        delimiter = source[start:run_end]
        end = source.find(delimiter, run_end)
        if end < 0:
            raise ProjectionError("unterminated inline code span", source, start)
        ranges.append((start, end + len(delimiter), "inline-code"))
        cursor = end + len(delimiter)


def _add_html_tags(source: str, ranges: list[tuple[int, int, str]]) -> None:
    cursor = 0
    while cursor < len(source):
        start = source.find("<", cursor)
        if start < 0:
            return
        if _covered(start, ranges):
            cursor = start + 1
            continue
        if (
            start + 1 >= len(source)
            or source[start + 1] not in "/!?ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
        ):
            cursor = start + 1
            continue
        quote: str | None = None
        end = start + 1
        while end < len(source):
            char = source[end]
            if quote is not None:
                if char == quote:
                    quote = None
            elif char in "'\"":
                quote = char
            elif char == ">":
                ranges.append((start, end + 1, "html-tag"))
                cursor = end + 1
                break
            elif char in "\r\n":
                cursor = start + 1
                break
            end += 1
        else:
            cursor = start + 1


def _find_closing_paren(source: str, start: int) -> int | None:
    depth = 1
    escaped = False
    for index in range(start, len(source)):
        char = source[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
        elif char in "\r\n" and depth == 1:
            return None
    return None


def _add_links_and_images(source: str, ranges: list[tuple[int, int, str]]) -> None:
    reference = re.compile(r"^ {0,3}\[[^\]\r\n]+\]:[^\r\n]*(?:\r?\n|$)", re.MULTILINE)
    for match in reference.finditer(source):
        if not _covered(match.start(), ranges):
            ranges.append((match.start(), match.end(), "link-destination"))

    opener = re.compile(r"!?\[")
    cursor = 0
    while (opener_match := opener.search(source, cursor)) is not None:
        start = opener_match.start()
        cursor = opener_match.end()
        if _covered(start, ranges):
            continue
        label_end = source.find("]", opener_match.end())
        if label_end < 0:
            continue
        is_image = source[start] == "!"
        if label_end + 1 < len(source) and source[label_end + 1] == "(":
            close = _find_closing_paren(source, label_end + 2)
            if close is None:
                raise ProjectionError(
                    "unterminated Markdown link destination", source, label_end + 1
                )
            if is_image:
                ranges.append((start, close + 1, "image"))
            else:
                ranges.extend(
                    (
                        (start, opener_match.end(), "link-markup"),
                        (label_end, close + 1, "link-destination"),
                    )
                )
            cursor = close + 1
        elif label_end + 1 < len(source) and source[label_end + 1] == "[":
            ref_end = source.find("]", label_end + 2)
            if ref_end >= 0:
                if is_image:
                    ranges.append((start, ref_end + 1, "image"))
                else:
                    ranges.extend(
                        (
                            (start, opener_match.end(), "link-markup"),
                            (label_end, ref_end + 1, "link-destination"),
                        )
                    )
                cursor = ref_end + 1


def _add_blockquotes(source: str, ranges: list[tuple[int, int, str]]) -> None:
    active = False
    lazy_continuation = False
    start = end = 0
    for line in re.finditer(r"^.*(?:\r?\n|$)", source, re.MULTILINE):
        text = line.group(0).rstrip("\r\n")
        marker = re.match(r"^ {0,3}>[ \t]?", text)
        blank = not text.strip()
        if marker is not None and not _covered(line.start(), ranges):
            if not active:
                start = line.start()
                active = True
            end = line.end()
            content, _ = _strip_blockquote_markers(text)
            lazy_continuation = bool(content.strip()) and not _BLOCK_INTERRUPT.match(content)
        elif active and lazy_continuation and not blank and not _BLOCK_INTERRUPT.match(text):
            end = line.end()
        elif active:
            ranges.append((start, end, "blockquote"))
            active = False
            lazy_continuation = False
    if active:
        ranges.append((start, end, "blockquote"))


def _add_disabled_visible_contexts(
    source: str,
    ranges: list[tuple[int, int, str]],
    contexts: ContextConfig,
) -> None:
    if not contexts.scan_headings:
        for match in re.finditer(r"^ {0,3}#{1,6}\s+[^\r\n]*(?:\r?\n|$)", source, re.MULTILINE):
            if not _covered(match.start(), ranges):
                ranges.append((match.start(), match.end(), "heading"))
    if not contexts.scan_captions:
        for match in re.finditer(
            r"<figcaption\b[^>]*>.*?</figcaption\s*>",
            source,
            re.IGNORECASE | re.DOTALL,
        ):
            if not _covered(match.start(), ranges):
                ranges.append((match.start(), match.end(), "caption"))


def _merge_ranges(source: str, ranges: list[tuple[int, int, str]]) -> tuple[MaskedRange, ...]:
    boundaries: list[tuple[int, int, str]] = []
    for start, end, reason in ranges:
        if not 0 <= start <= end <= len(source):
            raise ProjectionError("invalid masked range", source, max(0, start))
        if start != end:
            boundaries.append((start, end, reason))
    if not boundaries:
        return ()
    points = sorted({point for start, end, _ in boundaries for point in (start, end)})
    pieces: list[MaskedRange] = []
    for start, end in pairwise(points):
        reasons = tuple(
            sorted({reason for left, right, reason in boundaries if left < end and right > start})
        )
        if not reasons:
            continue
        if pieces and pieces[-1].end == start and pieces[-1].reasons == reasons:
            previous = pieces.pop()
            pieces.append(MaskedRange(start=previous.start, end=end, reasons=reasons))
        else:
            pieces.append(MaskedRange(start=start, end=end, reasons=reasons))
    return tuple(pieces)


def _project(source: str, masked: tuple[MaskedRange, ...]) -> str:
    characters = list(source)
    for item in masked:
        for index in range(item.start, item.end):
            if characters[index] not in "\r\n":
                characters[index] = " "
    return "".join(characters)


def _segment_tokens(projection: str) -> tuple[ProseSegment, ...]:
    return tuple(
        ProseSegment(
            start=match.start(),
            end=match.end(),
            text=match.group(),
            normalized=normalize_key(match.group()),
        )
        for match in _WORD.finditer(projection)
    )


def _paragraph_spans(projection: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    current_start: int | None = None
    cursor = 0
    for line in projection.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        stripped = content.strip()
        is_boundary = bool(re.match(r" {0,3}(?:#{1,6}\s|[-+*]\s|\d+[.)]\s)", content))
        if not stripped:
            if current_start is not None:
                spans.append((current_start, cursor))
                current_start = None
        elif is_boundary:
            if current_start is not None:
                spans.append((current_start, cursor))
            spans.append((cursor, cursor + len(content)))
            current_start = None
        elif current_start is None:
            current_start = cursor
        cursor += len(line)
    if current_start is not None:
        spans.append((current_start, len(projection)))
    return [(start, end) for start, end in spans if projection[start:end].strip()]


def _segment_paragraphs(projection: str) -> tuple[ProseSegment, ...]:
    return tuple(
        ProseSegment(
            start=start,
            end=end,
            text=projection[start:end],
            normalized=normalize_key(projection[start:end].strip()),
        )
        for start, end in _paragraph_spans(projection)
    )


def _segment_sentences(
    projection: str, paragraphs: tuple[ProseSegment, ...]
) -> tuple[ProseSegment, ...]:
    sentences: list[ProseSegment] = []
    for paragraph in paragraphs:
        start = paragraph.start
        cursor = start
        while cursor < paragraph.end:
            while cursor < paragraph.end and projection[cursor].isspace():
                cursor += 1
            if cursor >= paragraph.end:
                break
            end = paragraph.end
            for match in re.finditer(r"[.!?]+(?:[\"'”’)]*)", projection[cursor : paragraph.end]):
                candidate_end = cursor + match.end()
                token_start = projection.rfind(" ", cursor, candidate_end) + 1
                candidate = projection[token_start:candidate_end].casefold()
                if candidate in _ABBREVIATIONS:
                    continue
                if candidate_end == paragraph.end or projection[candidate_end].isspace():
                    end = candidate_end
                    break
            text = projection[cursor:end]
            if text.strip():
                sentences.append(
                    ProseSegment(
                        start=cursor, end=end, text=text, normalized=normalize_key(text.strip())
                    )
                )
            cursor = end
    return tuple(sentences)


def _bind_suppressions(
    source: str,
    projection: str,
    parsed: list[tuple[tuple[RuleId, ...], str, Span]],
    paragraphs: tuple[ProseSegment, ...],
) -> tuple[SuppressionDirective, ...]:
    directives: list[SuppressionDirective] = []
    for ids, reason, span in parsed:
        target = next((paragraph for paragraph in paragraphs if paragraph.start >= span.end), None)
        if target is None:
            raise ProjectionError("suppression has no following prose block", source, span.start)
        if projection[span.end : target.start].strip():
            raise ProjectionError(
                "suppression is not immediately before a prose block", source, span.start
            )
        directives.append(
            SuppressionDirective(
                rule_ids=ids,
                reason=reason,
                directive_span=span,
                target_span=Span(start=target.start, end=target.end),
            )
        )
    return tuple(directives)


def build_document(
    path: str | Path,
    content: bytes | str,
    *,
    contexts: ContextConfig | None = None,
    max_source_bytes: int = MAX_SOURCE_BYTES,
) -> Document:
    """Build an immutable source-mapped prose view from one Markdown document."""

    if not 1 <= max_source_bytes <= MAX_SOURCE_BYTES:
        raise ValueError(f"max_source_bytes must be between 1 and {MAX_SOURCE_BYTES}")
    if isinstance(content, bytes):
        raw = content
        if len(raw) > max_source_bytes:
            raise ProjectionError(f"source exceeds {max_source_bytes} bytes", "")
        try:
            source = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ProjectionError("source is not valid UTF-8", "", error.start) from error
    else:
        source = content
        raw = source.encode("utf-8")
        if len(raw) > max_source_bytes:
            raise ProjectionError(f"source exceeds {max_source_bytes} bytes", source)
    nul = source.find("\0")
    if nul >= 0:
        raise ProjectionError("source contains a NUL character", source, nul)

    effective_contexts = contexts or ContextConfig()
    ranges: list[tuple[int, int, str]] = []
    front_matter = _add_front_matter(source, ranges)
    _add_generated_ranges(source, ranges)
    if not effective_contexts.scan_blockquotes:
        _add_blockquotes(source, ranges)
    _add_fenced_code(source, ranges)
    _add_indented_code(source, ranges)
    parsed_suppressions = _add_suppressions(source, ranges)
    _add_html_comments(source, ranges)
    _add_inline_code(source, ranges)
    _add_html_tags(source, ranges)
    _add_links_and_images(source, ranges)
    _add_disabled_visible_contexts(source, ranges, effective_contexts)

    masked = _merge_ranges(source, ranges)
    projection = _project(source, masked)
    tokens = _segment_tokens(projection)
    paragraphs = _segment_paragraphs(projection)
    sentences = _segment_sentences(projection, paragraphs)
    suppressions = _bind_suppressions(source, projection, parsed_suppressions, paragraphs)
    masked_count = sum(
        1
        for item in masked
        for character in source[item.start : item.end]
        if character not in "\r\n"
    )
    metrics = DocumentMetrics(
        source_bytes=len(raw),
        source_code_points=len(source),
        analyzable_words=len(tokens),
        analyzable_sentences=len(sentences),
        analyzable_paragraphs=len(paragraphs),
        masked_code_points=masked_count,
    )
    return Document(
        path=Path(path).as_posix(),
        source=source,
        prose_projection=projection,
        line_starts=_line_starts(source),
        front_matter=front_matter,
        masked_ranges=masked,
        suppressions=suppressions,
        tokens=tokens,
        sentences=sentences,
        paragraphs=paragraphs,
        metrics=metrics,
    )


def load_document(
    path: str | Path,
    *,
    contexts: ContextConfig | None = None,
    max_source_bytes: int = MAX_SOURCE_BYTES,
) -> Document:
    """Read a regular Markdown file and build its source-mapped prose view."""

    source_path = Path(path)
    if source_path.is_symlink() or not source_path.is_file():
        raise ValueError(f"input must be a regular non-symlink file: {source_path}")
    return build_document(
        source_path,
        source_path.read_bytes(),
        contexts=contexts,
        max_source_bytes=max_source_bytes,
    )


__all__ = [
    "Document",
    "DocumentMetrics",
    "MaskedRange",
    "ProjectionError",
    "ProseSegment",
    "Span",
    "SuppressionDirective",
    "build_document",
    "line_column",
    "load_document",
    "normalize_key",
]
