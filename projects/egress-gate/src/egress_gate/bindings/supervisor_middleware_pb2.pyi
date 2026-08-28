from google.protobuf import empty_pb2 as _empty_pb2
from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SupervisorMiddlewareOperation(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SUPERVISOR_MIDDLEWARE_OPERATION_UNSPECIFIED: _ClassVar[SupervisorMiddlewareOperation]
    SUPERVISOR_MIDDLEWARE_OPERATION_HTTP_REQUEST: _ClassVar[SupervisorMiddlewareOperation]
    SUPERVISOR_MIDDLEWARE_OPERATION_WEBSOCKET_MESSAGE: _ClassVar[SupervisorMiddlewareOperation]
    SUPERVISOR_MIDDLEWARE_OPERATION_AGENT_CONVERSATION: _ClassVar[SupervisorMiddlewareOperation]

class SupervisorMiddlewarePhase(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SUPERVISOR_MIDDLEWARE_PHASE_UNSPECIFIED: _ClassVar[SupervisorMiddlewarePhase]
    SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS: _ClassVar[SupervisorMiddlewarePhase]
    SUPERVISOR_MIDDLEWARE_PHASE_PRE_RETURN: _ClassVar[SupervisorMiddlewarePhase]
    SUPERVISOR_MIDDLEWARE_PHASE_AGENT_CONTEXT: _ClassVar[SupervisorMiddlewarePhase]

class WebSocketSessionEndReason(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    WEB_SOCKET_SESSION_END_REASON_UNSPECIFIED: _ClassVar[WebSocketSessionEndReason]
    WEB_SOCKET_SESSION_END_REASON_NORMAL_CLOSE: _ClassVar[WebSocketSessionEndReason]
    WEB_SOCKET_SESSION_END_REASON_PEER_DISCONNECT: _ClassVar[WebSocketSessionEndReason]
    WEB_SOCKET_SESSION_END_REASON_POLICY_RELOAD: _ClassVar[WebSocketSessionEndReason]
    WEB_SOCKET_SESSION_END_REASON_MIDDLEWARE_DENIAL: _ClassVar[WebSocketSessionEndReason]
    WEB_SOCKET_SESSION_END_REASON_MIDDLEWARE_FAILURE: _ClassVar[WebSocketSessionEndReason]
    WEB_SOCKET_SESSION_END_REASON_PROTOCOL_ERROR: _ClassVar[WebSocketSessionEndReason]
    WEB_SOCKET_SESSION_END_REASON_CANCELLATION: _ClassVar[WebSocketSessionEndReason]
    WEB_SOCKET_SESSION_END_REASON_UPSTREAM_REJECTED: _ClassVar[WebSocketSessionEndReason]
    WEB_SOCKET_SESSION_END_REASON_POLICY_DENIAL: _ClassVar[WebSocketSessionEndReason]
    WEB_SOCKET_SESSION_END_REASON_STAGE_SKIPPED: _ClassVar[WebSocketSessionEndReason]

class WebSocketPreflightAction(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    WEB_SOCKET_PREFLIGHT_ACTION_UNSPECIFIED: _ClassVar[WebSocketPreflightAction]
    WEB_SOCKET_PREFLIGHT_ACTION_INSPECT: _ClassVar[WebSocketPreflightAction]
    WEB_SOCKET_PREFLIGHT_ACTION_SKIP: _ClassVar[WebSocketPreflightAction]
    WEB_SOCKET_PREFLIGHT_ACTION_DENY: _ClassVar[WebSocketPreflightAction]

class Decision(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    DECISION_UNSPECIFIED: _ClassVar[Decision]
    DECISION_ALLOW: _ClassVar[Decision]
    DECISION_DENY: _ClassVar[Decision]

class ExistingHeaderAction(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    EXISTING_HEADER_ACTION_UNSPECIFIED: _ClassVar[ExistingHeaderAction]
    EXISTING_HEADER_ACTION_APPEND: _ClassVar[ExistingHeaderAction]
    EXISTING_HEADER_ACTION_OVERWRITE: _ClassVar[ExistingHeaderAction]
    EXISTING_HEADER_ACTION_SKIP: _ClassVar[ExistingHeaderAction]
SUPERVISOR_MIDDLEWARE_OPERATION_UNSPECIFIED: SupervisorMiddlewareOperation
SUPERVISOR_MIDDLEWARE_OPERATION_HTTP_REQUEST: SupervisorMiddlewareOperation
SUPERVISOR_MIDDLEWARE_OPERATION_WEBSOCKET_MESSAGE: SupervisorMiddlewareOperation
SUPERVISOR_MIDDLEWARE_OPERATION_AGENT_CONVERSATION: SupervisorMiddlewareOperation
SUPERVISOR_MIDDLEWARE_PHASE_UNSPECIFIED: SupervisorMiddlewarePhase
SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS: SupervisorMiddlewarePhase
SUPERVISOR_MIDDLEWARE_PHASE_PRE_RETURN: SupervisorMiddlewarePhase
SUPERVISOR_MIDDLEWARE_PHASE_AGENT_CONTEXT: SupervisorMiddlewarePhase
WEB_SOCKET_SESSION_END_REASON_UNSPECIFIED: WebSocketSessionEndReason
WEB_SOCKET_SESSION_END_REASON_NORMAL_CLOSE: WebSocketSessionEndReason
WEB_SOCKET_SESSION_END_REASON_PEER_DISCONNECT: WebSocketSessionEndReason
WEB_SOCKET_SESSION_END_REASON_POLICY_RELOAD: WebSocketSessionEndReason
WEB_SOCKET_SESSION_END_REASON_MIDDLEWARE_DENIAL: WebSocketSessionEndReason
WEB_SOCKET_SESSION_END_REASON_MIDDLEWARE_FAILURE: WebSocketSessionEndReason
WEB_SOCKET_SESSION_END_REASON_PROTOCOL_ERROR: WebSocketSessionEndReason
WEB_SOCKET_SESSION_END_REASON_CANCELLATION: WebSocketSessionEndReason
WEB_SOCKET_SESSION_END_REASON_UPSTREAM_REJECTED: WebSocketSessionEndReason
WEB_SOCKET_SESSION_END_REASON_POLICY_DENIAL: WebSocketSessionEndReason
WEB_SOCKET_SESSION_END_REASON_STAGE_SKIPPED: WebSocketSessionEndReason
WEB_SOCKET_PREFLIGHT_ACTION_UNSPECIFIED: WebSocketPreflightAction
WEB_SOCKET_PREFLIGHT_ACTION_INSPECT: WebSocketPreflightAction
WEB_SOCKET_PREFLIGHT_ACTION_SKIP: WebSocketPreflightAction
WEB_SOCKET_PREFLIGHT_ACTION_DENY: WebSocketPreflightAction
DECISION_UNSPECIFIED: Decision
DECISION_ALLOW: Decision
DECISION_DENY: Decision
EXISTING_HEADER_ACTION_UNSPECIFIED: ExistingHeaderAction
EXISTING_HEADER_ACTION_APPEND: ExistingHeaderAction
EXISTING_HEADER_ACTION_OVERWRITE: ExistingHeaderAction
EXISTING_HEADER_ACTION_SKIP: ExistingHeaderAction

class MiddlewareManifest(_message.Message):
    __slots__ = ("name", "service_version", "bindings", "expected_audience")
    NAME_FIELD_NUMBER: _ClassVar[int]
    SERVICE_VERSION_FIELD_NUMBER: _ClassVar[int]
    BINDINGS_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_AUDIENCE_FIELD_NUMBER: _ClassVar[int]
    name: str
    service_version: str
    bindings: _containers.RepeatedCompositeFieldContainer[MiddlewareBinding]
    expected_audience: str
    def __init__(self, name: _Optional[str] = ..., service_version: _Optional[str] = ..., bindings: _Optional[_Iterable[_Union[MiddlewareBinding, _Mapping]]] = ..., expected_audience: _Optional[str] = ...) -> None: ...

class MiddlewareBinding(_message.Message):
    __slots__ = ("operation", "phase", "max_payload_bytes", "timeout", "harness", "hook", "schema_version")
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    PHASE_FIELD_NUMBER: _ClassVar[int]
    MAX_PAYLOAD_BYTES_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_FIELD_NUMBER: _ClassVar[int]
    HARNESS_FIELD_NUMBER: _ClassVar[int]
    HOOK_FIELD_NUMBER: _ClassVar[int]
    SCHEMA_VERSION_FIELD_NUMBER: _ClassVar[int]
    operation: SupervisorMiddlewareOperation
    phase: SupervisorMiddlewarePhase
    max_payload_bytes: int
    timeout: str
    harness: str
    hook: str
    schema_version: str
    def __init__(self, operation: _Optional[_Union[SupervisorMiddlewareOperation, str]] = ..., phase: _Optional[_Union[SupervisorMiddlewarePhase, str]] = ..., max_payload_bytes: _Optional[int] = ..., timeout: _Optional[str] = ..., harness: _Optional[str] = ..., hook: _Optional[str] = ..., schema_version: _Optional[str] = ...) -> None: ...

class ValidateConfigRequest(_message.Message):
    __slots__ = ("config", "middleware_name")
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    MIDDLEWARE_NAME_FIELD_NUMBER: _ClassVar[int]
    config: _struct_pb2.Struct
    middleware_name: str
    def __init__(self, config: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., middleware_name: _Optional[str] = ...) -> None: ...

class ValidateConfigResponse(_message.Message):
    __slots__ = ("valid", "reason")
    VALID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    valid: bool
    reason: str
    def __init__(self, valid: _Optional[bool] = ..., reason: _Optional[str] = ...) -> None: ...

class HttpRequestEvaluation(_message.Message):
    __slots__ = ("phase", "context", "config", "target", "headers", "body", "middleware_name", "agent_attestation")
    PHASE_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    TARGET_FIELD_NUMBER: _ClassVar[int]
    HEADERS_FIELD_NUMBER: _ClassVar[int]
    BODY_FIELD_NUMBER: _ClassVar[int]
    MIDDLEWARE_NAME_FIELD_NUMBER: _ClassVar[int]
    AGENT_ATTESTATION_FIELD_NUMBER: _ClassVar[int]
    phase: SupervisorMiddlewarePhase
    context: RequestContext
    config: _struct_pb2.Struct
    target: HttpRequestTarget
    headers: _containers.RepeatedCompositeFieldContainer[HttpHeader]
    body: bytes
    middleware_name: str
    agent_attestation: bytes
    def __init__(self, phase: _Optional[_Union[SupervisorMiddlewarePhase, str]] = ..., context: _Optional[_Union[RequestContext, _Mapping]] = ..., config: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., target: _Optional[_Union[HttpRequestTarget, _Mapping]] = ..., headers: _Optional[_Iterable[_Union[HttpHeader, _Mapping]]] = ..., body: _Optional[bytes] = ..., middleware_name: _Optional[str] = ..., agent_attestation: _Optional[bytes] = ...) -> None: ...

class HttpHeader(_message.Message):
    __slots__ = ("name", "value")
    NAME_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    name: str
    value: str
    def __init__(self, name: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

class WebSocketSessionEvent(_message.Message):
    __slots__ = ("preflight", "session_start", "message", "session_end")
    PREFLIGHT_FIELD_NUMBER: _ClassVar[int]
    SESSION_START_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    SESSION_END_FIELD_NUMBER: _ClassVar[int]
    preflight: WebSocketPreflight
    session_start: WebSocketSessionStart
    message: WebSocketMessage
    session_end: WebSocketSessionEnd
    def __init__(self, preflight: _Optional[_Union[WebSocketPreflight, _Mapping]] = ..., session_start: _Optional[_Union[WebSocketSessionStart, _Mapping]] = ..., message: _Optional[_Union[WebSocketMessage, _Mapping]] = ..., session_end: _Optional[_Union[WebSocketSessionEnd, _Mapping]] = ...) -> None: ...

class WebSocketPreflight(_message.Message):
    __slots__ = ("session_id", "phase", "context", "target", "requested_subprotocols", "middleware_name", "config")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PHASE_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    TARGET_FIELD_NUMBER: _ClassVar[int]
    REQUESTED_SUBPROTOCOLS_FIELD_NUMBER: _ClassVar[int]
    MIDDLEWARE_NAME_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    phase: SupervisorMiddlewarePhase
    context: RequestContext
    target: HttpRequestTarget
    requested_subprotocols: _containers.RepeatedScalarFieldContainer[str]
    middleware_name: str
    config: _struct_pb2.Struct
    def __init__(self, session_id: _Optional[str] = ..., phase: _Optional[_Union[SupervisorMiddlewarePhase, str]] = ..., context: _Optional[_Union[RequestContext, _Mapping]] = ..., target: _Optional[_Union[HttpRequestTarget, _Mapping]] = ..., requested_subprotocols: _Optional[_Iterable[str]] = ..., middleware_name: _Optional[str] = ..., config: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class WebSocketSessionStart(_message.Message):
    __slots__ = ("selected_subprotocol",)
    SELECTED_SUBPROTOCOL_FIELD_NUMBER: _ClassVar[int]
    selected_subprotocol: str
    def __init__(self, selected_subprotocol: _Optional[str] = ...) -> None: ...

class WebSocketMessage(_message.Message):
    __slots__ = ("sequence", "text", "binary")
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    BINARY_FIELD_NUMBER: _ClassVar[int]
    sequence: int
    text: str
    binary: bytes
    def __init__(self, sequence: _Optional[int] = ..., text: _Optional[str] = ..., binary: _Optional[bytes] = ...) -> None: ...

class WebSocketSessionEnd(_message.Message):
    __slots__ = ("reason",)
    REASON_FIELD_NUMBER: _ClassVar[int]
    reason: WebSocketSessionEndReason
    def __init__(self, reason: _Optional[_Union[WebSocketSessionEndReason, str]] = ...) -> None: ...

class WebSocketPreflightDecision(_message.Message):
    __slots__ = ("action", "reason", "reason_code", "findings", "metadata")
    class MetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    ACTION_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    REASON_CODE_FIELD_NUMBER: _ClassVar[int]
    FINDINGS_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    action: WebSocketPreflightAction
    reason: str
    reason_code: str
    findings: _containers.RepeatedCompositeFieldContainer[Finding]
    metadata: _containers.ScalarMap[str, str]
    def __init__(self, action: _Optional[_Union[WebSocketPreflightAction, str]] = ..., reason: _Optional[str] = ..., reason_code: _Optional[str] = ..., findings: _Optional[_Iterable[_Union[Finding, _Mapping]]] = ..., metadata: _Optional[_Mapping[str, str]] = ...) -> None: ...

class WebSocketMessageResult(_message.Message):
    __slots__ = ("sequence", "decision", "text", "binary", "reason", "reason_code", "findings", "metadata")
    class MetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    DECISION_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    BINARY_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    REASON_CODE_FIELD_NUMBER: _ClassVar[int]
    FINDINGS_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    sequence: int
    decision: Decision
    text: str
    binary: bytes
    reason: str
    reason_code: str
    findings: _containers.RepeatedCompositeFieldContainer[Finding]
    metadata: _containers.ScalarMap[str, str]
    def __init__(self, sequence: _Optional[int] = ..., decision: _Optional[_Union[Decision, str]] = ..., text: _Optional[str] = ..., binary: _Optional[bytes] = ..., reason: _Optional[str] = ..., reason_code: _Optional[str] = ..., findings: _Optional[_Iterable[_Union[Finding, _Mapping]]] = ..., metadata: _Optional[_Mapping[str, str]] = ...) -> None: ...

class WebSocketSessionEventResult(_message.Message):
    __slots__ = ("preflight_decision", "message_result")
    PREFLIGHT_DECISION_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_RESULT_FIELD_NUMBER: _ClassVar[int]
    preflight_decision: WebSocketPreflightDecision
    message_result: WebSocketMessageResult
    def __init__(self, preflight_decision: _Optional[_Union[WebSocketPreflightDecision, _Mapping]] = ..., message_result: _Optional[_Union[WebSocketMessageResult, _Mapping]] = ...) -> None: ...

class RequestContext(_message.Message):
    __slots__ = ("request_id", "sandbox_id", "originating_process", "sandbox_name", "workspace")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    ORIGINATING_PROCESS_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_NAME_FIELD_NUMBER: _ClassVar[int]
    WORKSPACE_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    sandbox_id: str
    originating_process: Process
    sandbox_name: str
    workspace: str
    def __init__(self, request_id: _Optional[str] = ..., sandbox_id: _Optional[str] = ..., originating_process: _Optional[_Union[Process, _Mapping]] = ..., sandbox_name: _Optional[str] = ..., workspace: _Optional[str] = ...) -> None: ...

class HttpRequestTarget(_message.Message):
    __slots__ = ("scheme", "host", "port", "method", "path", "query")
    SCHEME_FIELD_NUMBER: _ClassVar[int]
    HOST_FIELD_NUMBER: _ClassVar[int]
    PORT_FIELD_NUMBER: _ClassVar[int]
    METHOD_FIELD_NUMBER: _ClassVar[int]
    PATH_FIELD_NUMBER: _ClassVar[int]
    QUERY_FIELD_NUMBER: _ClassVar[int]
    scheme: str
    host: str
    port: int
    method: str
    path: str
    query: str
    def __init__(self, scheme: _Optional[str] = ..., host: _Optional[str] = ..., port: _Optional[int] = ..., method: _Optional[str] = ..., path: _Optional[str] = ..., query: _Optional[str] = ...) -> None: ...

class Process(_message.Message):
    __slots__ = ("binary", "pid", "ancestors")
    BINARY_FIELD_NUMBER: _ClassVar[int]
    PID_FIELD_NUMBER: _ClassVar[int]
    ANCESTORS_FIELD_NUMBER: _ClassVar[int]
    binary: str
    pid: int
    ancestors: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, binary: _Optional[str] = ..., pid: _Optional[int] = ..., ancestors: _Optional[_Iterable[str]] = ...) -> None: ...

class AgentConversationTarget(_message.Message):
    __slots__ = ("harness", "harness_version", "hook", "schema_version", "scheme", "host", "port", "path")
    HARNESS_FIELD_NUMBER: _ClassVar[int]
    HARNESS_VERSION_FIELD_NUMBER: _ClassVar[int]
    HOOK_FIELD_NUMBER: _ClassVar[int]
    SCHEMA_VERSION_FIELD_NUMBER: _ClassVar[int]
    SCHEME_FIELD_NUMBER: _ClassVar[int]
    HOST_FIELD_NUMBER: _ClassVar[int]
    PORT_FIELD_NUMBER: _ClassVar[int]
    PATH_FIELD_NUMBER: _ClassVar[int]
    harness: str
    harness_version: str
    hook: str
    schema_version: str
    scheme: str
    host: str
    port: int
    path: str
    def __init__(self, harness: _Optional[str] = ..., harness_version: _Optional[str] = ..., hook: _Optional[str] = ..., schema_version: _Optional[str] = ..., scheme: _Optional[str] = ..., host: _Optional[str] = ..., port: _Optional[int] = ..., path: _Optional[str] = ...) -> None: ...

class AgentConversationEvaluation(_message.Message):
    __slots__ = ("phase", "context", "config", "target", "middleware_name", "session_id", "turn_id", "request_body")
    PHASE_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    TARGET_FIELD_NUMBER: _ClassVar[int]
    MIDDLEWARE_NAME_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    TURN_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_BODY_FIELD_NUMBER: _ClassVar[int]
    phase: SupervisorMiddlewarePhase
    context: RequestContext
    config: _struct_pb2.Struct
    target: AgentConversationTarget
    middleware_name: str
    session_id: str
    turn_id: str
    request_body: bytes
    def __init__(self, phase: _Optional[_Union[SupervisorMiddlewarePhase, str]] = ..., context: _Optional[_Union[RequestContext, _Mapping]] = ..., config: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., target: _Optional[_Union[AgentConversationTarget, _Mapping]] = ..., middleware_name: _Optional[str] = ..., session_id: _Optional[str] = ..., turn_id: _Optional[str] = ..., request_body: _Optional[bytes] = ...) -> None: ...

class AgentConversationResult(_message.Message):
    __slots__ = ("decision", "reason", "attestation", "findings", "metadata", "reason_code", "replacement_body", "has_replacement_body")
    class MetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    DECISION_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    ATTESTATION_FIELD_NUMBER: _ClassVar[int]
    FINDINGS_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    REASON_CODE_FIELD_NUMBER: _ClassVar[int]
    REPLACEMENT_BODY_FIELD_NUMBER: _ClassVar[int]
    HAS_REPLACEMENT_BODY_FIELD_NUMBER: _ClassVar[int]
    decision: Decision
    reason: str
    attestation: bytes
    findings: _containers.RepeatedCompositeFieldContainer[Finding]
    metadata: _containers.ScalarMap[str, str]
    reason_code: str
    replacement_body: bytes
    has_replacement_body: bool
    def __init__(self, decision: _Optional[_Union[Decision, str]] = ..., reason: _Optional[str] = ..., attestation: _Optional[bytes] = ..., findings: _Optional[_Iterable[_Union[Finding, _Mapping]]] = ..., metadata: _Optional[_Mapping[str, str]] = ..., reason_code: _Optional[str] = ..., replacement_body: _Optional[bytes] = ..., has_replacement_body: _Optional[bool] = ...) -> None: ...

class Finding(_message.Message):
    __slots__ = ("type", "label", "count", "confidence", "severity")
    TYPE_FIELD_NUMBER: _ClassVar[int]
    LABEL_FIELD_NUMBER: _ClassVar[int]
    COUNT_FIELD_NUMBER: _ClassVar[int]
    CONFIDENCE_FIELD_NUMBER: _ClassVar[int]
    SEVERITY_FIELD_NUMBER: _ClassVar[int]
    type: str
    label: str
    count: int
    confidence: str
    severity: str
    def __init__(self, type: _Optional[str] = ..., label: _Optional[str] = ..., count: _Optional[int] = ..., confidence: _Optional[str] = ..., severity: _Optional[str] = ...) -> None: ...

class WriteHeader(_message.Message):
    __slots__ = ("name", "value", "on_existing")
    NAME_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    ON_EXISTING_FIELD_NUMBER: _ClassVar[int]
    name: str
    value: str
    on_existing: ExistingHeaderAction
    def __init__(self, name: _Optional[str] = ..., value: _Optional[str] = ..., on_existing: _Optional[_Union[ExistingHeaderAction, str]] = ...) -> None: ...

class RemoveHeader(_message.Message):
    __slots__ = ("name",)
    NAME_FIELD_NUMBER: _ClassVar[int]
    name: str
    def __init__(self, name: _Optional[str] = ...) -> None: ...

class HeaderMutation(_message.Message):
    __slots__ = ("write", "remove")
    WRITE_FIELD_NUMBER: _ClassVar[int]
    REMOVE_FIELD_NUMBER: _ClassVar[int]
    write: WriteHeader
    remove: RemoveHeader
    def __init__(self, write: _Optional[_Union[WriteHeader, _Mapping]] = ..., remove: _Optional[_Union[RemoveHeader, _Mapping]] = ...) -> None: ...

class HttpRequestResult(_message.Message):
    __slots__ = ("decision", "reason", "body", "has_body", "header_mutations", "findings", "metadata", "reason_code")
    class MetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    DECISION_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    BODY_FIELD_NUMBER: _ClassVar[int]
    HAS_BODY_FIELD_NUMBER: _ClassVar[int]
    HEADER_MUTATIONS_FIELD_NUMBER: _ClassVar[int]
    FINDINGS_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    REASON_CODE_FIELD_NUMBER: _ClassVar[int]
    decision: Decision
    reason: str
    body: bytes
    has_body: bool
    header_mutations: _containers.RepeatedCompositeFieldContainer[HeaderMutation]
    findings: _containers.RepeatedCompositeFieldContainer[Finding]
    metadata: _containers.ScalarMap[str, str]
    reason_code: str
    def __init__(self, decision: _Optional[_Union[Decision, str]] = ..., reason: _Optional[str] = ..., body: _Optional[bytes] = ..., has_body: _Optional[bool] = ..., header_mutations: _Optional[_Iterable[_Union[HeaderMutation, _Mapping]]] = ..., findings: _Optional[_Iterable[_Union[Finding, _Mapping]]] = ..., metadata: _Optional[_Mapping[str, str]] = ..., reason_code: _Optional[str] = ...) -> None: ...
