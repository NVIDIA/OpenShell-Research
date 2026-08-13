# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Private bounded parser for source-aware strict JSON documents."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from egress_gate.constants import MAX_JSON_DEPTH, MAX_JSON_NODES
from egress_gate.errors import GateLimitExceededError
from egress_gate.request_content.json_values import JsonNodeKind
from egress_gate.timeout import Timeout


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
        next_timeout_check = self.index + _LEXICAL_TIMEOUT_CHECK_INTERVAL
        while self.index < len(self.source):
            if self.index >= next_timeout_check:
                self.timeout.raise_if_expired()
                next_timeout_check = self.index + _LEXICAL_TIMEOUT_CHECK_INTERVAL
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
        next_timeout_check = self.index + _LEXICAL_TIMEOUT_CHECK_INTERVAL
        while self.index < len(self.source) and self.source[self.index] in " \t\r\n":
            self.index += 1
            if self.index >= next_timeout_check:
                self.timeout.raise_if_expired()
                next_timeout_check = self.index + _LEXICAL_TIMEOUT_CHECK_INTERVAL

    def _consume(self, token: str) -> bool:
        if self.source.startswith(token, self.index):
            self.index += len(token)
            return True
        return False


_NUMBER_PATTERN = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_HEX_PATTERN = re.compile(r"[0-9a-fA-F]{4}")
_LEXICAL_TIMEOUT_CHECK_INTERVAL = 256


__all__ = ["_JsonNode", "_JsonParser"]
