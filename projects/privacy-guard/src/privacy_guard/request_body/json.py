"""JSON FormatHandler that addresses string leaves by JSON Pointer path."""

from __future__ import annotations

import json
import math
from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import ConfigDict, TypeAdapter, ValidationError
from typing_extensions import TypeAliasType, override

from privacy_guard.constants import (
    MAX_JSON_NESTING,
    MAX_SCANNED_CHARACTERS,
    MAX_TEXT_BLOCKS,
)
from privacy_guard.errors import ErrorCode, PrivacyGuardError
from privacy_guard.request_body.base import FormatHandler, RequestBody, TextBlock
from privacy_guard.validation import ScalarString, parse_scalar_string

if TYPE_CHECKING:
    from privacy_guard.config import PolicyConfig


JsonValue = TypeAliasType(
    "JsonValue",
    ScalarString
    | int
    | float
    | bool
    | None
    | dict[ScalarString, "JsonValue"]
    | list["JsonValue"],
)


class JsonHandler(FormatHandler):
    """Handle strict UTF-8 JSON bodies, with one TextBlock per string leaf.

    Text-block paths are JSON Pointers (RFC 6901), e.g. ``/items/0/text``, so
    ``reconstruct`` can locate each replacement. Rewritten JSON preserves
    untouched values semantically but may change serialization details.
    """

    def __init__(self) -> None:
        super().__init__(format_name="json")

    @override
    def _normalize(self, raw_body: bytes, policy_config: PolicyConfig) -> RequestBody:
        """Decode UTF-8, parse JSON, and collect every string leaf as a text block."""
        try:
            decoded_body = raw_body.decode("utf-8")
        except UnicodeDecodeError:
            raise PrivacyGuardError(ErrorCode.BODY_ENCODING_INVALID) from None

        try:
            parsed_value = self._parse_json_value(decoded_body)
        except _InvalidJsonError:
            raise PrivacyGuardError(ErrorCode.BODY_JSON_INVALID) from None
        except _InvalidJsonShapeError:
            raise PrivacyGuardError(ErrorCode.REQUEST_SHAPE_LIMIT_EXCEEDED) from None

        try:
            text_blocks = tuple(self._iter_text_blocks(parsed_value))
        except _InvalidJsonError:
            raise PrivacyGuardError(ErrorCode.REQUEST_SHAPE_LIMIT_EXCEEDED) from None
        return RequestBody(
            text_blocks=text_blocks,
            parsed_value=_JsonBodyState(parsed_value),
            original_bytes=raw_body,
        )

    @override
    def _reconstruct(
        self,
        request_body: RequestBody,
        replacements_by_path: Mapping[str, str],
    ) -> bytes:
        """Apply JSON Pointer replacements, or return exact bytes for a no-op."""
        parsed_state = request_body.parsed_value
        if not isinstance(parsed_state, _JsonBodyState):
            raise PrivacyGuardError(ErrorCode.BODY_RECONSTRUCTION_INVALID) from None
        if not replacements_by_path:
            return request_body.original_bytes

        try:
            reconstructed_value = deepcopy(parsed_state.value)
            for json_pointer, replacement_text in replacements_by_path.items():
                parse_scalar_string(replacement_text)

                path_tokens = self._decode_json_pointer(json_pointer)
                if not path_tokens:
                    if not isinstance(reconstructed_value, str):
                        raise _InvalidJsonError
                    reconstructed_value = replacement_text
                    continue

                parent_node = reconstructed_value
                for path_token in path_tokens[:-1]:
                    parent_node = self._resolve_child_node(parent_node, path_token)

                final_token = path_tokens[-1]
                target_value = self._resolve_child_node(parent_node, final_token)
                if not isinstance(target_value, str):
                    raise _InvalidJsonError

                if isinstance(parent_node, dict):
                    parent_node[final_token] = replacement_text
                elif isinstance(parent_node, list):
                    array_index = self._parse_array_index(final_token, len(parent_node))
                    parent_node[array_index] = replacement_text
                else:
                    raise _InvalidJsonError
        except Exception:
            raise PrivacyGuardError(ErrorCode.BODY_RECONSTRUCTION_INVALID) from None

        try:
            return json.dumps(
                reconstructed_value,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except Exception:
            raise PrivacyGuardError(ErrorCode.BODY_RECONSTRUCTION_INVALID) from None

    @staticmethod
    def _build_unique_object(
        object_pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        object_members: dict[str, object] = {}
        for member_name, member_value in object_pairs:
            if member_name in object_members:
                raise _InvalidJsonError
            object_members[member_name] = member_value
        return object_members

    @staticmethod
    def _reject_non_finite_constant(_: str) -> object:
        raise _InvalidJsonError

    @staticmethod
    def _parse_finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise _InvalidJsonError
        return parsed

    @classmethod
    def _parse_json_value(cls, decoded_body: str) -> JsonValue:
        """Parse duplicate-aware JSON and return its one validated typed tree."""
        try:
            raw_value = json.loads(
                decoded_body,
                object_pairs_hook=cls._build_unique_object,
                parse_constant=cls._reject_non_finite_constant,
                parse_float=cls._parse_finite_float,
            )
        except RecursionError:
            raise _InvalidJsonShapeError from None
        except (json.JSONDecodeError, _InvalidJsonError, ValueError):
            raise _InvalidJsonError from None

        try:
            return _JSON_VALUE_ADAPTER.validate_python(raw_value, strict=True)
        except ValidationError as validation_error:
            if any(
                error["type"] == "recursion_loop"
                for error in validation_error.errors(
                    include_url=False,
                    include_context=False,
                    include_input=False,
                )
            ):
                raise _InvalidJsonShapeError from None
            raise _InvalidJsonError from None

    @classmethod
    def _iter_text_blocks(cls, parsed_json: JsonValue) -> Iterator[TextBlock]:
        """Walk a parsed JSON value incrementally in stable depth-first order."""
        count = 0
        total_characters = 0
        for emitted in cls._walk_text_blocks(parsed_json, "", 0, True):
            # Bound built-in parsing before tuple/model materialization. The
            # processor repeats aggregates for arbitrary handler outputs.
            count += 1
            total_characters += len(emitted.text)
            if count > MAX_TEXT_BLOCKS or total_characters > MAX_SCANNED_CHARACTERS:
                raise _InvalidJsonError
            yield emitted

    @classmethod
    def _walk_text_blocks(
        cls,
        node: JsonValue,
        json_pointer: str,
        depth: int,
        replaceable: bool,
    ) -> Iterator[TextBlock]:
        """Yield one branch at a time so traversal state remains O(depth)."""
        if depth > MAX_JSON_NESTING:
            raise _InvalidJsonError
        if isinstance(node, str):
            yield TextBlock(path=json_pointer, text=node, replaceable=replaceable)
            return
        if isinstance(node, dict):
            for key, child_node in node.items():
                path_token = key.replace("~", "~0").replace("/", "~1")
                child_path = f"{json_pointer}/{path_token}"
                # Key paths are deliberately outside the JSON Pointer namespace.
                yield from cls._walk_text_blocks(
                    key, f"#key:{child_path}", depth, False
                )
                yield from cls._walk_text_blocks(
                    child_node, child_path, depth + 1, True
                )
            return
        if isinstance(node, list):
            for index, child_node in enumerate(node):
                yield from cls._walk_text_blocks(
                    child_node, f"{json_pointer}/{index}", depth + 1, True
                )

    @staticmethod
    def _decode_json_pointer(json_pointer: str) -> list[str]:
        if json_pointer == "":
            return []
        if not json_pointer.startswith("/"):
            raise _InvalidJsonError

        decoded_tokens: list[str] = []
        for encoded_token in json_pointer[1:].split("/"):
            decoded_characters: list[str] = []
            character_index = 0
            while character_index < len(encoded_token):
                character = encoded_token[character_index]
                if character != "~":
                    decoded_characters.append(character)
                    character_index += 1
                    continue
                if character_index + 1 >= len(encoded_token) or encoded_token[
                    character_index + 1
                ] not in {"0", "1"}:
                    raise _InvalidJsonError
                decoded_characters.append(
                    "~" if encoded_token[character_index + 1] == "0" else "/"
                )
                character_index += 2
            decoded_tokens.append("".join(decoded_characters))
        return decoded_tokens

    @classmethod
    def _resolve_child_node(cls, parent_node: JsonValue, path_token: str) -> JsonValue:
        if isinstance(parent_node, dict):
            if path_token not in parent_node:
                raise _InvalidJsonError
            return parent_node[path_token]
        if isinstance(parent_node, list):
            array_index = cls._parse_array_index(path_token, len(parent_node))
            return parent_node[array_index]
        raise _InvalidJsonError

    @staticmethod
    def _parse_array_index(path_token: str, array_length: int) -> int:
        if not path_token.isascii() or not path_token.isdecimal():
            raise _InvalidJsonError
        if len(path_token) > 1 and path_token.startswith("0"):
            raise _InvalidJsonError
        array_index = int(path_token)
        if array_index >= array_length:
            raise _InvalidJsonError
        return array_index


_JSON_VALUE_ADAPTER = TypeAdapter(
    JsonValue,
    config=ConfigDict(
        strict=True,
        allow_inf_nan=False,
        hide_input_in_errors=True,
    ),
)


@dataclass(frozen=True)
class _JsonBodyState:
    """Typed reconstruction state assembled from an already validated value."""

    value: JsonValue = field(repr=False)


class _InvalidJsonError(ValueError):
    """Signal a strict-JSON violation without retaining request content."""


class _InvalidJsonShapeError(ValueError):
    """Signal JSON nesting that exceeds a parser or validator safe limit."""
