"""Package-wide Privacy Guard constants and operational limits.

Keep this module dependency-free within the package: it must not import from
``privacy_guard``.
"""

from __future__ import annotations

import re
from importlib.metadata import version

# Middleware identity and stable response values.
SERVICE_NAME = "privacy-guard"
SERVICE_VERSION = version("privacy-guard")
BLOCK_REASON = "Privacy Guard blocked the request"
BLOCK_REASON_CODE = "privacy_guard_blocked"
LIMIT_REASON = "Privacy Guard denied a result that exceeded a safety limit"
LIMIT_REASON_CODE = "privacy_guard_limit_exceeded"
# Text input limits.
MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_SCANNED_CHARACTERS = 4 * 1024 * 1024

# Engine and result limits.
MAX_DETECTIONS_PER_STAGE = 256
MAX_DETECTIONS_PER_REQUEST = 4096
MAX_DIAGNOSTIC_TEXT_BYTES = 1024
MAX_FINDING_METADATA_ENTRIES = 32
MAX_PROTO_FINDING_GROUPS = 32
MAX_PROTO_FINDING_BYTES = 4 * 1024

# Engine configuration and regex execution limits.
MAX_REGEX_NAME_BYTES = 128
MAX_REGEX_ENTITIES_PER_CATALOG = 2_000
MAX_REGEX_PATTERNS_PER_CATALOG = 10_000
MAX_REGEX_PATTERN_BYTES = 16 * 1024
MAX_MATCHES_PER_PATTERN = 256
DEFAULT_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 30.0

# Service concurrency and transport limits.
MAX_CONCURRENT_PROCESSING = 4
MAX_CONCURRENT_RPCS = 16
PROTOBUF_ENVELOPE_ALLOWANCE_BYTES = 1024 * 1024
MAX_RECEIVE_MESSAGE_BYTES = MAX_BODY_BYTES + PROTOBUF_ENVELOPE_ALLOWANCE_BYTES

# Protocol validation values.
UINT32_MAX = 2**32 - 1
REASON_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
CONFIDENCE_RANK = {
    "low": 0,
    "medium": 1,
    "high": 2,
}
