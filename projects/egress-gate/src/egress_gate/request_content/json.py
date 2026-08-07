"""Strict source-aware JSON request-content parsing and selection."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, field_validator

from egress_gate.base import StrictDomainModel
from egress_gate.constants import (
    MAX_BODY_BYTES,
    MAX_JSON_DEPTH,
    MAX_JSON_NODES,
    MAX_JSON_SELECTED_NODES,
    MAX_JSON_SELECTED_TEXT_BYTES,
    MAX_JSON_SELECTOR_SEGMENTS,
    MAX_JSON_SELECTORS,
)
from egress_gate.errors import BodyFormatError, GateInputError, GateLimitExceededError
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


class JsonNodeKind(StrEnum):
    """The JSON value kind at one immutable document node."""

    OBJECT = "object"
    ARRAY = "array"
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    NULL = "null"


class JsonNode(StrictDomainModel):
    """Opaque immutable reference to one node in a ``JsonDocument``."""

    id: str
    path: tuple[str | int, ...] = Field(repr=False)
    kind: JsonNodeKind


class JsonTextNode(StrictDomainModel):
    """One selected JSON string with a stable document-local identity."""

    id: str
    path: tuple[str | int, ...] = Field(repr=False)
    text: str = Field(repr=False)


class JsonDocument:
    """One strict JSON body with bounded traversal and source-preserving edits."""

    __slots__ = ("_nodes", "_root", "_source")

    def __init__(self, source: str, root: _JsonNode, nodes: dict[str, _JsonNode]):
        self._source = source
        self._root = root
        self._nodes = nodes

    @classmethod
    def parse(cls, body: bytes, *, timeout: Timeout) -> JsonDocument:
        """Parse one complete strict UTF-8 JSON body under the shared deadline."""
        timeout.raise_if_expired()
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
        return self._text_nodes(self.select_nodes(selectors, timeout=timeout))

    def select_text_from(
        self,
        node: JsonNode,
        selectors: tuple[JsonSelector, ...],
        *,
        timeout: Timeout,
    ) -> tuple[JsonTextNode, ...]:
        """Select unique string nodes relative to one document node."""
        return self._text_nodes(self.select_from(node, selectors, timeout=timeout))

    def array_items(self, node: JsonNode) -> tuple[JsonNode, ...]:
        """Return the ordered immediate items of an array node."""
        internal = self._resolve_node(node)
        if internal.kind is not JsonNodeKind.ARRAY:
            return ()
        return tuple(_public_node(item) for item in internal.array_items)

    def object_member(self, node: JsonNode, key: str) -> JsonNode | None:
        """Return one exact object member without exposing mutable JSON values."""
        internal = self._resolve_node(node)
        if internal.kind is not JsonNodeKind.OBJECT:
            return None
        for member_key, value in internal.object_members:
            if member_key == key:
                return _public_node(value)
        return None

    def text_value(self, node: JsonNode) -> str | None:
        """Return the decoded value when a node is a JSON string."""
        internal = self._resolve_node(node)
        return internal.text if internal.kind is JsonNodeKind.STRING else None

    def replace_text(
        self,
        replacements: tuple[tuple[str, str], ...],
        *,
        timeout: Timeout,
    ) -> bytes:
        """Replace complete JSON string tokens and preserve all unrelated source."""
        timeout.raise_if_expired()
        replacement_ids = tuple(node_id for node_id, _ in replacements)
        if len(replacement_ids) != len(set(replacement_ids)):
            raise ValueError("JSON replacement node IDs must be unique")
        edits: list[tuple[int, int, str]] = []
        for node_id, text in replacements:
            timeout.raise_if_expired()
            node = self._nodes.get(node_id)
            if node is None or node.kind is not JsonNodeKind.STRING:
                raise ValueError("JSON replacement node ID is unknown")
            try:
                text.encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                raise ValueError("JSON replacement text is invalid") from None
            rendered = json.dumps(text, ensure_ascii=False)
            edits.append((node.token_start, node.token_end, rendered))
        output = self._source
        for start, end, rendered in sorted(edits, reverse=True):
            output = output[:start] + rendered + output[end:]
        encoded = output.encode("utf-8")
        if len(encoded) > MAX_BODY_BYTES:
            raise GateLimitExceededError("JSON replacement body exceeds the limit")
        timeout.raise_if_expired()
        return encoded

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
        return tuple(_public_node(node) for node in selected)

    def _text_nodes(self, nodes: tuple[JsonNode, ...]) -> tuple[JsonTextNode, ...]:
        selected: list[JsonTextNode] = []
        encoded_size = 0
        for node in nodes:
            internal = self._resolve_node(node)
            if internal.kind is not JsonNodeKind.STRING or internal.text is None:
                continue
            encoded_size += len(internal.text.encode("utf-8"))
            if encoded_size > MAX_JSON_SELECTED_TEXT_BYTES:
                raise GateLimitExceededError("JSON selected text exceeds the limit")
            selected.append(
                JsonTextNode(id=internal.id, path=internal.path, text=internal.text)
            )
        return tuple(selected)

    def _resolve_node(self, node: JsonNode) -> _JsonNode:
        internal = self._nodes.get(node.id)
        if (
            internal is None
            or internal.path != node.path
            or internal.kind is not node.kind
        ):
            raise ValueError("JSON node does not belong to this document")
        return internal


@dataclass(frozen=True)
class _JsonNode:
    id: str
    path: tuple[str | int, ...]
    kind: JsonNodeKind
    token_start: int
    token_end: int
    text: str | None = None
    object_members: tuple[tuple[str, _JsonNode], ...] = ()
    array_items: tuple[_JsonNode, ...] = ()


class _JsonParser:
    def __init__(self, source: str, timeout: Timeout) -> None:
        self.source = source
        self.timeout = timeout
        self.index = 0
        self.node_count = 0
        self.nodes: dict[str, _JsonNode] = {}

    def parse(self) -> _JsonNode:
        self._skip_whitespace()
        root = self._parse_value((), 0)
        self._skip_whitespace()
        if self.index != len(self.source):
            raise ValueError("trailing JSON data")
        return root

    def _parse_value(self, path: tuple[str | int, ...], depth: int) -> _JsonNode:
        self.timeout.raise_if_expired()
        if self.index >= len(self.source):
            raise ValueError("JSON value is missing")
        character = self.source[self.index]
        if character == "{":
            if depth >= MAX_JSON_DEPTH:
                raise GateLimitExceededError("JSON nesting depth exceeds the limit")
            return self._parse_object(path, depth)
        if character == "[":
            if depth >= MAX_JSON_DEPTH:
                raise GateLimitExceededError("JSON nesting depth exceeds the limit")
            return self._parse_array(path, depth)
        if character == '"':
            start = self.index
            text = self._parse_string()
            return self._new_node(
                path,
                JsonNodeKind.STRING,
                start,
                self.index,
                text=text,
            )
        if character in "-0123456789":
            start = self.index
            match = _NUMBER_PATTERN.match(self.source, self.index)
            if match is None:
                raise ValueError("JSON number is invalid")
            self.index = match.end()
            return self._new_node(path, JsonNodeKind.NUMBER, start, self.index)
        for literal, kind in (
            ("true", JsonNodeKind.BOOLEAN),
            ("false", JsonNodeKind.BOOLEAN),
            ("null", JsonNodeKind.NULL),
        ):
            if self.source.startswith(literal, self.index):
                start = self.index
                self.index += len(literal)
                return self._new_node(path, kind, start, self.index)
        raise ValueError("JSON value is invalid")

    def _parse_object(self, path: tuple[str | int, ...], depth: int) -> _JsonNode:
        start = self.index
        self.index += 1
        self._skip_whitespace()
        members: list[tuple[str, _JsonNode]] = []
        keys: set[str] = set()
        if self._consume("}"):
            return self._new_node(
                path, JsonNodeKind.OBJECT, start, self.index, object_members=()
            )
        while True:
            if self.index >= len(self.source) or self.source[self.index] != '"':
                raise ValueError("JSON object key is invalid")
            key = self._parse_string()
            if key in keys:
                raise ValueError("JSON object keys must be unique")
            keys.add(key)
            self._skip_whitespace()
            if not self._consume(":"):
                raise ValueError("JSON object separator is missing")
            self._skip_whitespace()
            value = self._parse_value((*path, key), depth + 1)
            members.append((key, value))
            self._skip_whitespace()
            if self._consume("}"):
                break
            if not self._consume(","):
                raise ValueError("JSON object delimiter is missing")
            self._skip_whitespace()
        return self._new_node(
            path,
            JsonNodeKind.OBJECT,
            start,
            self.index,
            object_members=tuple(members),
        )

    def _parse_array(self, path: tuple[str | int, ...], depth: int) -> _JsonNode:
        start = self.index
        self.index += 1
        self._skip_whitespace()
        items: list[_JsonNode] = []
        if self._consume("]"):
            return self._new_node(
                path, JsonNodeKind.ARRAY, start, self.index, array_items=()
            )
        while True:
            item = self._parse_value((*path, len(items)), depth + 1)
            items.append(item)
            self._skip_whitespace()
            if self._consume("]"):
                break
            if not self._consume(","):
                raise ValueError("JSON array delimiter is missing")
            self._skip_whitespace()
        return self._new_node(
            path,
            JsonNodeKind.ARRAY,
            start,
            self.index,
            array_items=tuple(items),
        )

    def _parse_string(self) -> str:
        start = self.index
        self.index += 1
        while self.index < len(self.source):
            character = self.source[self.index]
            if character == '"':
                self.index += 1
                token = self.source[start : self.index]
                value = json.loads(token)
                value.encode("utf-8", errors="strict")
                return value
            if character == "\\":
                self.index += 1
                if self.index >= len(self.source):
                    raise ValueError("JSON string escape is incomplete")
                escape = self.source[self.index]
                if escape == "u":
                    digits = self.source[self.index + 1 : self.index + 5]
                    if len(digits) != 4 or _HEX_PATTERN.fullmatch(digits) is None:
                        raise ValueError("JSON Unicode escape is invalid")
                    self.index += 5
                    continue
                if escape not in '"\\/bfnrt':
                    raise ValueError("JSON string escape is invalid")
            elif ord(character) < 0x20:
                raise ValueError("JSON string contains a control character")
            self.index += 1
        raise ValueError("JSON string is unterminated")

    def _new_node(
        self,
        path: tuple[str | int, ...],
        kind: JsonNodeKind,
        token_start: int,
        token_end: int,
        *,
        text: str | None = None,
        object_members: tuple[tuple[str, _JsonNode], ...] = (),
        array_items: tuple[_JsonNode, ...] = (),
    ) -> _JsonNode:
        self.node_count += 1
        if self.node_count > MAX_JSON_NODES:
            raise GateLimitExceededError("JSON node count exceeds the limit")
        node = _JsonNode(
            id=f"json-node-{self.node_count}",
            path=path,
            kind=kind,
            token_start=token_start,
            token_end=token_end,
            text=text,
            object_members=object_members,
            array_items=array_items,
        )
        self.nodes[node.id] = node
        return node

    def _skip_whitespace(self) -> None:
        while self.index < len(self.source) and self.source[self.index] in " \t\r\n":
            self.index += 1

    def _consume(self, token: str) -> bool:
        if self.source.startswith(token, self.index):
            self.index += len(token)
            return True
        return False


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


def _public_node(node: _JsonNode) -> JsonNode:
    return JsonNode(id=node.id, path=node.path, kind=node.kind)


_NUMBER_PATTERN = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_HEX_PATTERN = re.compile(r"[0-9a-fA-F]{4}")


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
