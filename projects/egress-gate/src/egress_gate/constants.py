"""Package-wide Egress Gate constants and operational limits.

Keep this module dependency-free within the package: it must not import from
``egress_gate``.
"""

from __future__ import annotations

import re
from importlib.metadata import version

# Middleware processing timeout and the gateway's upper bound for it.
DEFAULT_TIMEOUT_MIDDLEWARE_PROCESSING = 1.0
TIMEOUT_GATEWAY_CEILING = 30.0

# Middleware identity and stable response values.
SERVICE_NAME = "egress-gate"
SERVICE_VERSION = version("egress-gate")
BLOCK_REASON = "Egress Gate blocked the request"
DEFAULT_DENY_REASON_CODE = "egress_gate_default_deny"
LIMIT_REASON = (
    "Egress Gate exceeded a processing safety limit. Check Egress Gate logs "
    "for the limit kind. Reduce the request or replacement size, simplify the "
    "configured gates and rules, or increase the timeout passed to "
    "egress-gate serve --timeout, then retry."
)
LIMIT_REASON_CODE = "egress_gate_limit_exceeded"
# Text input limits.
MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_EVALUATION_FILE_BYTES = 16 * 1024 * 1024
MAX_EVALUATION_CASES = 256
MAX_EVALUATION_CASE_NAME_BYTES = 128
MAX_EVALUATION_TAGS = 16

# Gate and result limits.
MAX_DETECTIONS_PER_GATE = 256
MAX_DIAGNOSTIC_TEXT_BYTES = 1024
MAX_PROTO_FINDING_GROUPS = 32
MAX_PROTO_FINDING_BYTES = 4 * 1024
MAX_FINDING_COUNT = 2**32 - 1
MAX_RESULT_METADATA_ENTRIES = 64
MAX_RESULT_METADATA_BYTES = 32 * 1024
MAX_GATE_TRACES = 10
MAX_TRACE_MUTATION_KINDS = 2

# Domain request-mutation limits mirrored from the OpenShell middleware
# contract. Encoded protobuf size remains a service-boundary concern.
MAX_HEADER_MUTATIONS = 64
MAX_HEADER_MUTATION_DATA_BYTES = 32 * 1024

# Pipeline configuration and regex execution limits.
MAX_PIPELINE_GATES = 10
MAX_REGEX_NAME_BYTES = 128
MAX_REGEX_ENTITIES_PER_CATALOG = 2_000
MAX_REGEX_RULES_PER_CATALOG = 10_000
MAX_REGEX_PATTERN_BYTES = 16 * 1024
MAX_REGEX_CATALOG_FILE_BYTES = 16 * 1024 * 1024
MAX_REGEX_CATALOG_PATH_BYTES = 1024

# Service concurrency and transport limits.
MAX_CONCURRENT_PROCESSING = 4
MAX_CONCURRENT_RPCS = 16
# Mirrored from the encoded OpenShell middleware contract.
MAX_PROTO_CONTEXT_BYTES = 4 * 1024
MAX_PROTO_CONFIG_BYTES = 64 * 1024
MAX_PROTO_TARGET_BYTES = 32 * 1024
MAX_PROTO_HEADERS = 128
MAX_PROTO_HEADERS_BYTES = 64 * 1024
PROTOBUF_ENVELOPE_ALLOWANCE_BYTES = 1024 * 1024
MAX_RECEIVE_MESSAGE_BYTES = MAX_BODY_BYTES + PROTOBUF_ENVELOPE_ALLOWANCE_BYTES

# Protocol validation values.
REASON_CODE_PATTERN = re.compile(r"\A[a-z][a-z0-9_]{0,63}\Z")
