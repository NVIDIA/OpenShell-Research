# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Strict source-aware JSON request-content parsing and selection."""

from __future__ import annotations

import json
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, field_validator

from egress_gate.base import StrictDomainModel
from egress_gate.constants import (
    MAX_BODY_BYTES,
    MAX_JSON_SELECTED_NODES,
    MAX_JSON_SELECTED_TEXT_BYTES,
    MAX_JSON_SELECTOR_SEGMENTS,
    MAX_JSON_SELECTORS,
)
from egress_gate.errors import BodyFormatError, GateInputError, GateLimitExceededError
from egress_gate.request_content._json_parser import _JsonNode, _JsonParser
from egress_gate.request_content.json_values import (
    JsonNode,
    JsonNodeKind,
    JsonTextNode,
)
from egress_gate.request_content.text import TextReplacement
from egress_gate.string_validators import ScalarString
from egress_gate.timeout import Timeout


class JsonKeySegment(StrictDomainModel):
    """Select one object member by its exact decoded key."""

    kind: Literal["key"]
    value: ScalarString


class JsonIndexSegment(StrictDomainModel):
    """Select one array item by its zero-based index."""

    kind: Literal["index"]
    value: int = Field(ge=0)


class JsonEachSegment(StrictDomainModel):
    """Select every immediate array item or object member value."""

    kind: Literal["each"]


JsonPathSegment: TypeAlias = Annotated[
    JsonKeySegment | JsonIndexSegment | JsonEachSegment,
    Field(discriminator="kind"),
]


class JsonSelector(StrictDomainModel):
    """One bounded path from a selected JSON node."""

    segments: tuple[JsonPathSegment, ...] = Field(
        min_length=1,
        max_length=MAX_JSON_SELECTOR_SEGMENTS,
    )

    @field_validator("segments", mode="before")
    @classmethod
    def _segments_are_a_tuple(cls, value: object) -> object:
        if isinstance(value, list | tuple):
            return tuple(value)
        return value


class JsonDocument:
    """One strict JSON body with bounded traversal and source-preserving edits."""

    __slots__ = ("_nodes", "_public_nodes", "_root", "_source")

    def __init__(self, source: str, root: _JsonNode, nodes: dict[str, _JsonNode]):
        self._source = source
        self._root = root
        self._nodes = nodes
        self._public_nodes: dict[str, JsonNode] = {}

    @classmethod
    def parse(cls, body: bytes, *, timeout: Timeout) -> JsonDocument:
        """Parse one complete strict UTF-8 JSON body under the shared deadline."""
        timeout.raise_if_expired()
        if len(body) > MAX_BODY_BYTES:
            raise GateLimitExceededError("JSON request body exceeds the size limit")
        try:
            source = body.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise GateInputError("request body is not valid UTF-8") from None
        try:
            parser = _JsonParser(source, timeout)
            root = parser.parse()
        except GateLimitExceededError:
            raise
        except (RecursionError, UnicodeError, ValueError):
            raise BodyFormatError("request body is not strict JSON") from None
        timeout.raise_if_expired()
        return cls(source, root, parser.nodes)

    def select_nodes(
        self,
        selectors: tuple[JsonSelector, ...],
        *,
        timeout: Timeout,
    ) -> tuple[JsonNode, ...]:
        """Select unique nodes in selector order and then document order."""
        return self._select_from(self._root, selectors, timeout=timeout)

    def select_from(
        self,
        node: JsonNode,
        selectors: tuple[JsonSelector, ...],
        *,
        timeout: Timeout,
    ) -> tuple[JsonNode, ...]:
        """Select unique nodes relative to one node from this document."""
        return self._select_from(self._resolve_node(node), selectors, timeout=timeout)

    def select_text(
        self,
        selectors: tuple[JsonSelector, ...],
        *,
        timeout: Timeout,
    ) -> tuple[JsonTextNode, ...]:
        """Select unique string nodes from the document root."""
        return self._text_nodes(
            self.select_nodes(selectors, timeout=timeout),
            timeout=timeout,
        )

    def select_text_from(
        self,
        node: JsonNode,
        selectors: tuple[JsonSelector, ...],
        *,
        timeout: Timeout,
    ) -> tuple[JsonTextNode, ...]:
        """Select unique string nodes relative to one document node."""
        return self._text_nodes(
            self.select_from(node, selectors, timeout=timeout),
            timeout=timeout,
        )

    def array_items(
        self,
        node: JsonNode,
        *,
        timeout: Timeout,
    ) -> tuple[JsonNode, ...]:
        """Return the ordered immediate items of an array node."""
        internal = self._resolve_node(node)
        if internal.kind is not JsonNodeKind.ARRAY:
            return ()
        items: list[JsonNode] = []
        for index, item in enumerate(internal.array_items):
            if index % _TIMEOUT_CHECK_INTERVAL == 0:
                timeout.raise_if_expired()
            items.append(self._public_node(item))
        timeout.raise_if_expired()
        return tuple(items)

    def object_member(self, node: JsonNode, key: str) -> JsonNode | None:
        """Return one exact object member without exposing mutable JSON values."""
        internal = self._resolve_node(node)
        if internal.kind is not JsonNodeKind.OBJECT:
            return None
        for member_key, value in internal.object_members:
            if member_key == key:
                return self._public_node(value)
        return None

    def text_value(self, node: JsonNode) -> str | None:
        """Return the decoded value when a node is a JSON string."""
        internal = self._resolve_node(node)
        return internal.text if internal.kind is JsonNodeKind.STRING else None

    def replace_text(
        self,
        replacements: tuple[TextReplacement, ...],
        *,
        timeout: Timeout,
    ) -> bytes:
        """Replace complete JSON string tokens and preserve all unrelated source."""
        timeout.raise_if_expired()
        replacement_ids = tuple(item.target_id for item in replacements)
        if len(replacement_ids) != len(set(replacement_ids)):
            raise ValueError("JSON replacement node IDs must be unique")
        edits: list[tuple[int, int, bytes]] = []
        for replacement in replacements:
            timeout.raise_if_expired()
            node = self._nodes.get(replacement.target_id)
            if node is None or node.kind is not JsonNodeKind.STRING:
                raise ValueError("JSON replacement node ID is unknown")
            try:
                rendered = json.dumps(replacement.text, ensure_ascii=False).encode(
                    "utf-8", errors="strict"
                )
            except UnicodeEncodeError:
                raise ValueError("JSON replacement text is invalid") from None
            edits.append((node.token_start, node.token_end, rendered))

        output_parts: list[bytes] = []
        output_size = 0
        source_cursor = 0
        for start, end, rendered in sorted(edits):
            timeout.raise_if_expired()
            unchanged = self._source[source_cursor:start].encode("utf-8")
            output_size += len(unchanged) + len(rendered)
            if output_size > MAX_BODY_BYTES:
                raise GateLimitExceededError("JSON replacement body exceeds the limit")
            output_parts.extend((unchanged, rendered))
            source_cursor = end
        tail = self._source[source_cursor:].encode("utf-8")
        output_size += len(tail)
        if output_size > MAX_BODY_BYTES:
            raise GateLimitExceededError("JSON replacement body exceeds the limit")
        output_parts.append(tail)
        timeout.raise_if_expired()
        return b"".join(output_parts)

    def _select_from(
        self,
        start: _JsonNode,
        selectors: tuple[JsonSelector, ...],
        *,
        timeout: Timeout,
    ) -> tuple[JsonNode, ...]:
        if len(selectors) > MAX_JSON_SELECTORS:
            raise GateLimitExceededError("JSON selector count exceeds the limit")
        selected: list[_JsonNode] = []
        seen: set[str] = set()
        for selector in selectors:
            current = (start,)
            for segment in selector.segments:
                timeout.raise_if_expired()
                next_nodes: list[_JsonNode] = []
                for node in current:
                    next_nodes.extend(_select_segment(node, segment))
                    if len(next_nodes) > MAX_JSON_SELECTED_NODES:
                        raise GateLimitExceededError(
                            "JSON selected node count exceeds the limit"
                        )
                current = tuple(next_nodes)
            for node in current:
                if node.id not in seen:
                    seen.add(node.id)
                    selected.append(node)
                    if len(selected) > MAX_JSON_SELECTED_NODES:
                        raise GateLimitExceededError(
                            "JSON selected node count exceeds the limit"
                        )
        timeout.raise_if_expired()
        public_nodes: list[JsonNode] = []
        for index, node in enumerate(selected):
            if index % _TIMEOUT_CHECK_INTERVAL == 0:
                timeout.raise_if_expired()
            public_nodes.append(self._public_node(node))
        timeout.raise_if_expired()
        return tuple(public_nodes)

    def _text_nodes(
        self,
        nodes: tuple[JsonNode, ...],
        *,
        timeout: Timeout,
    ) -> tuple[JsonTextNode, ...]:
        selected: list[JsonTextNode] = []
        encoded_size = 0
        for index, node in enumerate(nodes):
            if index % _TIMEOUT_CHECK_INTERVAL == 0:
                timeout.raise_if_expired()
            internal = self._resolve_node(node)
            if internal.kind is not JsonNodeKind.STRING or internal.text is None:
                continue
            encoded_size += len(internal.text.encode("utf-8"))
            if encoded_size > MAX_JSON_SELECTED_TEXT_BYTES:
                raise GateLimitExceededError("JSON selected text exceeds the limit")
            selected.append(
                JsonTextNode(id=internal.id, path=internal.path, text=internal.text)
            )
        timeout.raise_if_expired()
        return tuple(selected)

    def _resolve_node(self, node: JsonNode) -> _JsonNode:
        internal = self._nodes.get(node.id)
        if internal is None or self._public_nodes.get(node.id) is not node:
            raise ValueError("JSON node does not belong to this document")
        return internal

    def _public_node(self, node: _JsonNode) -> JsonNode:
        public = self._public_nodes.get(node.id)
        if public is None:
            public = JsonNode(id=node.id, path=node.path, kind=node.kind)
            self._public_nodes[node.id] = public
        return public


def _select_segment(
    node: _JsonNode,
    segment: JsonPathSegment,
) -> tuple[_JsonNode, ...]:
    if isinstance(segment, JsonKeySegment):
        if node.kind is not JsonNodeKind.OBJECT:
            return ()
        return tuple(
            value for key, value in node.object_members if key == segment.value
        )
    if isinstance(segment, JsonIndexSegment):
        if node.kind is not JsonNodeKind.ARRAY or segment.value >= len(
            node.array_items
        ):
            return ()
        return (node.array_items[segment.value],)
    if node.kind is JsonNodeKind.ARRAY:
        return node.array_items
    if node.kind is JsonNodeKind.OBJECT:
        return tuple(value for _, value in node.object_members)
    return ()


_TIMEOUT_CHECK_INTERVAL = 256


__all__ = [
    "JsonDocument",
    "JsonEachSegment",
    "JsonIndexSegment",
    "JsonKeySegment",
    "JsonNode",
    "JsonNodeKind",
    "JsonPathSegment",
    "JsonSelector",
    "JsonTextNode",
]
