from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ProtocolVersion(_message.Message):
    __slots__ = ("major", "minor")
    MAJOR_FIELD_NUMBER: _ClassVar[int]
    MINOR_FIELD_NUMBER: _ClassVar[int]
    major: int
    minor: int
    def __init__(self, major: _Optional[int] = ..., minor: _Optional[int] = ...) -> None: ...

class PeerMetadata(_message.Message):
    __slots__ = ("protocol_version", "implementation_name", "implementation_version", "supported_capabilities", "required_capabilities")
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    IMPLEMENTATION_NAME_FIELD_NUMBER: _ClassVar[int]
    IMPLEMENTATION_VERSION_FIELD_NUMBER: _ClassVar[int]
    SUPPORTED_CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    REQUIRED_CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    protocol_version: ProtocolVersion
    implementation_name: str
    implementation_version: str
    supported_capabilities: _containers.RepeatedScalarFieldContainer[str]
    required_capabilities: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, protocol_version: _Optional[_Union[ProtocolVersion, _Mapping]] = ..., implementation_name: _Optional[str] = ..., implementation_version: _Optional[str] = ..., supported_capabilities: _Optional[_Iterable[str]] = ..., required_capabilities: _Optional[_Iterable[str]] = ...) -> None: ...
