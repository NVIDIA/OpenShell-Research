# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Gate-agnostic parsers for text-bearing request content."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from egress_gate.constants import MAX_BODY_BYTES
from egress_gate.errors import GateInputError, GateLimitExceededError
from egress_gate.request_content.json import JsonDocument, JsonSelector
from egress_gate.request_content.messages import (
    MessageBlockExtractor,
    MessageBlockKind,
    MessageRole,
)
from egress_gate.request_content.text import TextReplacement, TextTarget
from egress_gate.timeout import Timeout


class ParsedRequestContent(Protocol):
    """A parsed text view that can render complete replacement body bytes."""

    @property
    def targets(self) -> tuple[TextTarget, ...]:
        """Return independently inspected text values in parser-defined order."""
        ...

    def replace_text(
        self,
        replacements: tuple[TextReplacement, ...],
        *,
        timeout: Timeout,
    ) -> bytes:
        """Render complete body bytes after replacing complete target strings."""
        ...


class RequestContentParser(Protocol):
    """A prepared stateless parser for one text-bearing request body format."""

    def parse(
        self,
        body: bytes,
        *,
        timeout: Timeout,
    ) -> ParsedRequestContent:
        """Parse current body bytes into independently inspectable text targets."""
        ...


class Utf8TextParser:
    """Expose one complete strict UTF-8 body as a text target."""

    def parse(
        self,
        body: bytes,
        *,
        timeout: Timeout,
    ) -> ParsedRequestContent:
        timeout.raise_if_expired()
        if len(body) > MAX_BODY_BYTES:
            raise GateLimitExceededError("UTF-8 request body exceeds the size limit")
        try:
            text = body.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise GateInputError("request body is not valid UTF-8") from None
        return _Utf8ParsedRequestContent(targets=(TextTarget(id="body", text=text),))


@dataclass(frozen=True)
class JsonFieldsParser:
    """Expose selected JSON string fields as independent text targets."""

    selectors: tuple[JsonSelector, ...]

    def parse(
        self,
        body: bytes,
        *,
        timeout: Timeout,
    ) -> ParsedRequestContent:
        document = JsonDocument.parse(body, timeout=timeout)
        nodes = document.select_text(self.selectors, timeout=timeout)
        return _JsonParsedRequestContent(
            targets=tuple(TextTarget(id=node.id, text=node.text) for node in nodes),
            document=document,
        )


class MessageBlocksParser:
    """Parse a JSON body into filtered normalized message-block targets."""

    __slots__ = ("_block_kinds", "_extractor", "_roles")

    def __init__(
        self,
        *,
        extractor: MessageBlockExtractor,
        roles: tuple[MessageRole, ...] | None = None,
        block_kinds: tuple[MessageBlockKind, ...] | None = None,
    ) -> None:
        self._extractor = extractor
        self._roles = roles
        self._block_kinds = block_kinds

    def parse(
        self,
        body: bytes,
        *,
        timeout: Timeout,
    ) -> ParsedRequestContent:
        document = JsonDocument.parse(body, timeout=timeout)
        message_document = self._extractor.extract(document, timeout=timeout)
        targets: list[TextTarget] = []
        seen_nodes: set[str] = set()
        for block in message_document.blocks:
            if self._roles is not None and block.role not in self._roles:
                continue
            if self._block_kinds is not None and block.kind not in self._block_kinds:
                continue
            if block.node_id in seen_nodes:
                continue
            seen_nodes.add(block.node_id)
            targets.append(TextTarget(id=block.node_id, text=block.text))
        return _JsonParsedRequestContent(
            targets=tuple(targets),
            document=document,
        )


@dataclass(frozen=True)
class _Utf8ParsedRequestContent:
    targets: tuple[TextTarget, ...]

    def replace_text(
        self,
        replacements: tuple[TextReplacement, ...],
        *,
        timeout: Timeout,
    ) -> bytes:
        timeout.raise_if_expired()
        if len(replacements) != 1 or replacements[0].target_id != self.targets[0].id:
            raise ValueError("UTF-8 body replacement target is invalid")
        replacement_text = replacements[0].text
        if len(replacement_text) > MAX_BODY_BYTES:
            raise GateLimitExceededError("UTF-8 replacement body exceeds the limit")
        try:
            rendered = replacement_text.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise ValueError("UTF-8 body replacement text is invalid") from None
        if len(rendered) > MAX_BODY_BYTES:
            raise GateLimitExceededError("UTF-8 replacement body exceeds the limit")
        timeout.raise_if_expired()
        return rendered


@dataclass(frozen=True)
class _JsonParsedRequestContent:
    targets: tuple[TextTarget, ...]
    document: JsonDocument

    def replace_text(
        self,
        replacements: tuple[TextReplacement, ...],
        *,
        timeout: Timeout,
    ) -> bytes:
        replacement_ids = tuple(item.target_id for item in replacements)
        if len(replacement_ids) != len(set(replacement_ids)):
            raise ValueError("request-content replacement target IDs must be unique")
        selected_ids = frozenset(target.id for target in self.targets)
        if any(node_id not in selected_ids for node_id in replacement_ids):
            raise ValueError("request-content replacement target was not selected")
        return self.document.replace_text(replacements, timeout=timeout)


__all__ = [
    "JsonFieldsParser",
    "MessageBlocksParser",
    "ParsedRequestContent",
    "RequestContentParser",
    "Utf8TextParser",
]
