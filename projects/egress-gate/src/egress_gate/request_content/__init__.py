"""Public structured request-content parsing surface."""

from egress_gate.request_content.json import (
    JsonDocument,
    JsonEachSegment,
    JsonIndexSegment,
    JsonKeySegment,
    JsonNode,
    JsonNodeKind,
    JsonPathSegment,
    JsonSelector,
    JsonTextNode,
)
from egress_gate.request_content.messages import (
    JsonMessageBlockExtractor,
    JsonMessageMapConfig,
    MessageBlock,
    MessageBlockExtractor,
    MessageBlockKind,
    MessageDocument,
    MessageRole,
)
from egress_gate.request_content.parsers import (
    JsonFieldsParser,
    MessageBlocksParser,
    ParsedRequestContent,
    RequestContentParser,
    Utf8TextParser,
)
from egress_gate.request_content.text import TextReplacement, TextTarget

__all__ = [
    "JsonDocument",
    "JsonEachSegment",
    "JsonFieldsParser",
    "JsonIndexSegment",
    "JsonKeySegment",
    "JsonMessageBlockExtractor",
    "JsonMessageMapConfig",
    "JsonNode",
    "JsonNodeKind",
    "JsonPathSegment",
    "JsonSelector",
    "JsonTextNode",
    "MessageBlock",
    "MessageBlockExtractor",
    "MessageBlockKind",
    "MessageBlocksParser",
    "MessageDocument",
    "MessageRole",
    "ParsedRequestContent",
    "RequestContentParser",
    "TextReplacement",
    "TextTarget",
    "Utf8TextParser",
]
