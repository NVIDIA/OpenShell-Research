"""Normalized agent message blocks derived from structured JSON content."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Protocol, Self

from pydantic import Field, field_validator, model_validator

from egress_gate.base import StrictDomainModel
from egress_gate.constants import MAX_JSON_SELECTORS, MAX_MESSAGE_BLOCKS
from egress_gate.errors import GateLimitExceededError
from egress_gate.request_content.json import (
    JsonDocument,
    JsonNode,
    JsonNodeKind,
    JsonSelector,
)
from egress_gate.string_validators import ScalarString
from egress_gate.timeout import Timeout


class MessageRole(StrEnum):
    """A normalized role for one model-visible message block."""

    SYSTEM = "system"
    DEVELOPER = "developer"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    UNKNOWN = "unknown"


class MessageBlockKind(StrEnum):
    """The normalized purpose of one text-bearing message block."""

    TEXT = "text"
    TOOL_INPUT = "tool_input"
    TOOL_OUTPUT = "tool_output"


class MessageBlock(StrictDomainModel):
    """One normalized text block backed by a JSON string node."""

    id: str
    node_id: str
    message_index: int | None = Field(ge=0)
    role: MessageRole
    kind: MessageBlockKind
    text: str = Field(repr=False)


class MessageDocument(StrictDomainModel):
    """The bounded normalized message view of one request body."""

    blocks: tuple[MessageBlock, ...] = Field(
        max_length=MAX_MESSAGE_BLOCKS,
        repr=False,
    )


class JsonMessageMapConfig(StrictDomainModel):
    """Map conventional JSON message objects to normalized text blocks."""

    kind: Literal["json-message-map"]
    messages: JsonSelector
    role_key: ScalarString = "role"
    text_selectors: tuple[JsonSelector, ...] = Field(
        default=(),
        max_length=MAX_JSON_SELECTORS,
    )
    tool_input_selectors: tuple[JsonSelector, ...] = Field(
        default=(),
        max_length=MAX_JSON_SELECTORS,
    )
    tool_output_selectors: tuple[JsonSelector, ...] = Field(
        default=(),
        max_length=MAX_JSON_SELECTORS,
    )

    @field_validator(
        "text_selectors",
        "tool_input_selectors",
        "tool_output_selectors",
        mode="before",
    )
    @classmethod
    def _text_selectors_are_a_tuple(cls, value: object) -> object:
        if isinstance(value, list | tuple):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def _selectors_are_non_empty_and_bounded(self) -> Self:
        selector_count = sum(
            len(selectors)
            for selectors in (
                self.text_selectors,
                self.tool_input_selectors,
                self.tool_output_selectors,
            )
        )
        if selector_count == 0:
            raise ValueError("message mapping requires at least one text selector")
        if selector_count > MAX_JSON_SELECTORS:
            raise ValueError("message mapping selector count exceeds the limit")
        return self


class MessageBlockExtractor(Protocol):
    """Extract normalized message blocks from one parsed JSON document."""

    def extract(
        self,
        document: JsonDocument,
        *,
        timeout: Timeout,
    ) -> MessageDocument:
        """Return normalized blocks backed by nodes in ``document``."""
        ...


class JsonMessageBlockExtractor:
    """Extract normalized blocks with one validated JSON message mapping."""

    def __init__(self, config: JsonMessageMapConfig) -> None:
        self._config = config

    def extract(
        self,
        document: JsonDocument,
        *,
        timeout: Timeout,
    ) -> MessageDocument:
        """Return normalized blocks in message and configured selector order."""
        containers = document.select_nodes(
            (self._config.messages,),
            timeout=timeout,
        )
        blocks: list[MessageBlock] = []
        seen_nodes: set[str] = set()
        message_index = 0
        for container in containers:
            if container.kind is not JsonNodeKind.ARRAY:
                continue
            for message in document.array_items(container, timeout=timeout):
                timeout.raise_if_expired()
                role = _message_role(document, message, self._config.role_key)
                default_kind = (
                    MessageBlockKind.TOOL_OUTPUT
                    if role is MessageRole.TOOL
                    else MessageBlockKind.TEXT
                )
                selector_groups = (
                    (self._config.text_selectors, default_kind),
                    (
                        self._config.tool_input_selectors,
                        MessageBlockKind.TOOL_INPUT,
                    ),
                    (
                        self._config.tool_output_selectors,
                        MessageBlockKind.TOOL_OUTPUT,
                    ),
                )
                for selectors, kind in selector_groups:
                    for text_node in document.select_text_from(
                        message,
                        selectors,
                        timeout=timeout,
                    ):
                        if text_node.id in seen_nodes:
                            continue
                        seen_nodes.add(text_node.id)
                        blocks.append(
                            MessageBlock(
                                id=f"message-block-{len(blocks) + 1}",
                                node_id=text_node.id,
                                message_index=message_index,
                                role=role,
                                kind=kind,
                                text=text_node.text,
                            )
                        )
                        if len(blocks) > MAX_MESSAGE_BLOCKS:
                            raise GateLimitExceededError(
                                "message block count exceeds the limit"
                            )
                message_index += 1
        timeout.raise_if_expired()
        return MessageDocument(blocks=tuple(blocks))


def _message_role(
    document: JsonDocument,
    message: JsonNode,
    role_key: str,
) -> MessageRole:
    role_node = document.object_member(message, role_key)
    if role_node is None:
        return MessageRole.UNKNOWN
    value = document.text_value(role_node)
    if value is None:
        return MessageRole.UNKNOWN
    try:
        return MessageRole(value)
    except ValueError:
        return MessageRole.UNKNOWN


__all__ = [
    "JsonMessageBlockExtractor",
    "JsonMessageMapConfig",
    "MessageBlock",
    "MessageBlockExtractor",
    "MessageBlockKind",
    "MessageDocument",
    "MessageRole",
]
