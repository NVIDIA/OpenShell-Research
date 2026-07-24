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

class SupervisorMiddlewarePhase(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SUPERVISOR_MIDDLEWARE_PHASE_UNSPECIFIED: _ClassVar[SupervisorMiddlewarePhase]
    SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS: _ClassVar[SupervisorMiddlewarePhase]

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
SUPERVISOR_MIDDLEWARE_PHASE_UNSPECIFIED: SupervisorMiddlewarePhase
SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS: SupervisorMiddlewarePhase
DECISION_UNSPECIFIED: Decision
DECISION_ALLOW: Decision
DECISION_DENY: Decision
EXISTING_HEADER_ACTION_UNSPECIFIED: ExistingHeaderAction
EXISTING_HEADER_ACTION_APPEND: ExistingHeaderAction
EXISTING_HEADER_ACTION_OVERWRITE: ExistingHeaderAction
EXISTING_HEADER_ACTION_SKIP: ExistingHeaderAction

class MiddlewareManifest(_message.Message):
    __slots__ = ("name", "service_version", "bindings")
    NAME_FIELD_NUMBER: _ClassVar[int]
    SERVICE_VERSION_FIELD_NUMBER: _ClassVar[int]
    BINDINGS_FIELD_NUMBER: _ClassVar[int]
    name: str
    service_version: str
    bindings: _containers.RepeatedCompositeFieldContainer[MiddlewareBinding]
    def __init__(self, name: _Optional[str] = ..., service_version: _Optional[str] = ..., bindings: _Optional[_Iterable[_Union[MiddlewareBinding, _Mapping]]] = ...) -> None: ...

class MiddlewareBinding(_message.Message):
    __slots__ = ("operation", "phase", "max_body_bytes", "timeout")
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    PHASE_FIELD_NUMBER: _ClassVar[int]
    MAX_BODY_BYTES_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_FIELD_NUMBER: _ClassVar[int]
    operation: SupervisorMiddlewareOperation
    phase: SupervisorMiddlewarePhase
    max_body_bytes: int
    timeout: str
    def __init__(self, operation: _Optional[_Union[SupervisorMiddlewareOperation, str]] = ..., phase: _Optional[_Union[SupervisorMiddlewarePhase, str]] = ..., max_body_bytes: _Optional[int] = ..., timeout: _Optional[str] = ...) -> None: ...

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
    __slots__ = ("phase", "context", "config", "target", "headers", "body", "middleware_name")
    PHASE_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    TARGET_FIELD_NUMBER: _ClassVar[int]
    HEADERS_FIELD_NUMBER: _ClassVar[int]
    BODY_FIELD_NUMBER: _ClassVar[int]
    MIDDLEWARE_NAME_FIELD_NUMBER: _ClassVar[int]
    phase: SupervisorMiddlewarePhase
    context: RequestContext
    config: _struct_pb2.Struct
    target: HttpRequestTarget
    headers: _containers.RepeatedCompositeFieldContainer[HttpHeader]
    body: bytes
    middleware_name: str
    def __init__(self, phase: _Optional[_Union[SupervisorMiddlewarePhase, str]] = ..., context: _Optional[_Union[RequestContext, _Mapping]] = ..., config: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., target: _Optional[_Union[HttpRequestTarget, _Mapping]] = ..., headers: _Optional[_Iterable[_Union[HttpHeader, _Mapping]]] = ..., body: _Optional[bytes] = ..., middleware_name: _Optional[str] = ...) -> None: ...

class HttpHeader(_message.Message):
    __slots__ = ("name", "value")
    NAME_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    name: str
    value: str
    def __init__(self, name: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

class RequestContext(_message.Message):
    __slots__ = ("request_id", "sandbox_id", "originating_process")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    ORIGINATING_PROCESS_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    sandbox_id: str
    originating_process: Process
    def __init__(self, request_id: _Optional[str] = ..., sandbox_id: _Optional[str] = ..., originating_process: _Optional[_Union[Process, _Mapping]] = ...) -> None: ...

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
