"""Example-owned provider-neutral semantic gate and deterministic judge."""

from __future__ import annotations

import json
from collections.abc import Mapping
from types import MappingProxyType
from typing import ClassVar, Literal, Protocol, runtime_checkable

from pydantic import Field, field_validator, model_validator

from egress_gate.base import StrictDomainModel
from egress_gate.constants import MAX_BODY_BYTES
from egress_gate.errors import GateConfigurationError
from egress_gate.gates import (
    FindingTypeDefinition,
    Gate,
    GateCapabilities,
    GateConfig,
    GateRegistry,
    GateResources,
)
from egress_gate.request import HttpRequest
from egress_gate.result import Finding, GateEvaluation, ReasonCode
from egress_gate.string_validators import BoundedMetadataString, ScalarString
from egress_gate.timeout import Timeout

MAX_SELECTED_HEADERS = 16
MAX_POLICY_BYTES = 16 * 1024
MAX_SERIALIZED_REQUEST_BYTES = 16 * 1024
SelectedHeader = BoundedMetadataString
SemanticDecision = Literal["allow", "deny"]


class SemanticInclude(StrictDomainModel):
    """Strict request-field selection and per-field body bound."""

    method: bool = False
    target: bool = False
    headers: tuple[SelectedHeader, ...] = ()
    body_max_bytes: int = Field(default=0, ge=0, le=MAX_BODY_BYTES)

    @field_validator("headers", mode="before")
    @classmethod
    def _headers_are_a_tuple(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def _selected_headers_are_bounded_and_unique(self) -> SemanticInclude:
        if len(self.headers) > MAX_SELECTED_HEADERS:
            raise ValueError("too many selected headers")
        lowered = tuple(header.lower() for header in self.headers)
        if len(lowered) != len(set(lowered)):
            raise ValueError("selected headers must be unique")
        if not (self.method or self.target or self.headers or self.body_max_bytes):
            raise ValueError("semantic gate must select at least one field")
        return self


class SemanticGateConfig(GateConfig):
    """Strict policy controls for the example semantic judgment."""

    gate: Literal["semantic-judge"] = "semantic-judge"
    profile: BoundedMetadataString
    policy: ScalarString
    include: SemanticInclude
    mode: Literal["enforce", "observe"]
    on_allow: Literal["allow"] = "allow"
    allow_label: BoundedMetadataString
    deny_label: BoundedMetadataString
    deny_reason_code: ReasonCode

    @field_validator("policy")
    @classmethod
    def _policy_is_bounded(cls, value: str) -> str:
        if not value or len(value.encode("utf-8")) > MAX_POLICY_BYTES:
            raise ValueError("semantic policy is outside the size limit")
        return value


class JudgeResult(StrictDomainModel):
    """The only provider-neutral result the example gate accepts."""

    decision: SemanticDecision


@runtime_checkable
class JudgeClient(Protocol):
    """Provider-neutral client contract owned by the example application."""

    def judge(
        self,
        serialized_request: str,
        *,
        profile: str,
        policy: str,
        timeout: Timeout,
    ) -> JudgeResult:
        """Return one strict semantic decision for deterministic JSON input."""


class SemanticGateResources(GateResources):
    """Typed application-owned resources borrowed by the semantic gate."""

    __slots__ = ("_profiles",)

    def __init__(self, profiles: Mapping[str, JudgeClient]) -> None:
        if not profiles or any(
            not isinstance(name, str) or not name or not isinstance(client, JudgeClient)
            for name, client in profiles.items()
        ):
            raise TypeError("judge profiles are invalid")
        self._profiles = MappingProxyType(dict(profiles))

    def has_profile(self, profile: str) -> bool:
        """Return whether one operator-approved profile is installed."""
        return profile in self._profiles

    def resolve(self, profile: str) -> JudgeClient:
        """Resolve one already-validated operator-approved profile."""
        return self._profiles[profile]


class FakeJudgeClient:
    """Stateless deterministic judge used by the example and offline corpus."""

    __slots__ = ("_deny_markers",)

    def __init__(self, *, deny_markers: tuple[str, ...]) -> None:
        self._deny_markers = tuple(deny_markers)

    def judge(
        self,
        serialized_request: str,
        *,
        profile: str,
        policy: str,
        timeout: Timeout,
    ) -> JudgeResult:
        del profile, policy
        timeout.raise_if_expired()
        try:
            payload = json.loads(serialized_request)
        except (TypeError, ValueError):
            raise ValueError("semantic request serialization is invalid") from None
        body = payload.get("body") if isinstance(payload, dict) else None
        if not isinstance(body, str):
            body = ""
        decision: SemanticDecision = (
            "deny" if any(marker in body for marker in self._deny_markers) else "allow"
        )
        return JudgeResult(decision=decision)


class SemanticGate(Gate[SemanticGateConfig, SemanticGateResources]):
    """Run one provider-neutral semantic judgment in enforce or observe mode."""

    capabilities = GateCapabilities(
        reads_target=True,
        reads_context=True,
        reads_headers=True,
        reads_body=True,
        produces_findings=True,
        may_allow=True,
        may_deny=True,
        uses_resources=True,
    )
    finding_types: ClassVar[tuple[FindingTypeDefinition, ...]] = (
        FindingTypeDefinition(type="semantic_assessment"),
    )

    @classmethod
    def _validate_config(
        cls,
        config: SemanticGateConfig,
        resources: SemanticGateResources,
    ) -> None:
        if not resources.has_profile(config.profile):
            raise GateConfigurationError("semantic profile is not installed")

    def _evaluate(
        self,
        request: HttpRequest,
        *,
        timeout: Timeout,
    ) -> GateEvaluation:
        serialized_request = _serialize_selected_request(request, self.config.include)
        result = self.resources.resolve(self.config.profile).judge(
            serialized_request,
            profile=self.config.profile,
            policy=self.config.policy,
            timeout=timeout,
        )
        if not isinstance(result, JudgeResult):
            raise TypeError("judge client returned an invalid result")
        result = JudgeResult.model_validate(result.model_dump())
        finding = Finding(
            type="semantic_assessment",
            label=(
                self.config.allow_label
                if result.decision == "allow"
                else self.config.deny_label
            ),
        )
        if self.config.mode == "observe":
            return GateEvaluation.proceed(findings=(finding,))
        if result.decision == "allow":
            return GateEvaluation.allow(findings=(finding,))
        return GateEvaluation.deny(
            self.config.deny_reason_code,
            findings=(finding,),
        )


def _serialize_selected_request(
    request: HttpRequest,
    include: SemanticInclude,
) -> str:
    values: dict[str, object] = {}
    if include.method:
        values["method"] = request.target.method
    if include.target:
        values["target"] = {
            "host": request.target.host,
            "path": request.target.path,
            "port": request.target.port,
            "query": request.target.query,
            "scheme": request.target.scheme,
        }
    if include.headers:
        selected_headers = {header.lower() for header in include.headers}
        values["headers"] = [
            {"name": header.name.lower(), "value": header.value}
            for header in request.headers
            if header.name.lower() in selected_headers
        ]
    if include.body_max_bytes:
        try:
            body = request.body.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise ValueError("semantic gate requires a UTF-8 request body") from None
        body_bytes = body.encode("utf-8")
        truncated = len(body_bytes) > include.body_max_bytes
        if truncated:
            body = body_bytes[: include.body_max_bytes].decode(
                "utf-8",
                errors="ignore",
            )
        values["body"] = body
        values["body_truncated"] = truncated
    serialized = json.dumps(
        values,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(serialized.encode("utf-8")) > MAX_SERIALIZED_REQUEST_BYTES:
        raise ValueError("serialized semantic request exceeds the size limit")
    return serialized


def create_registry() -> GateRegistry:
    """Build the finalized registry used by both example compositions."""
    registry = GateRegistry(include_builtin_gates=True)
    registry.register(
        SemanticGate,
        resources=SemanticGateResources(
            {
                "organization-default": FakeJudgeClient(
                    deny_markers=("[semantic-deny]",)
                )
            },
        ),
    )
    return registry.finalize()


__all__ = [
    "FakeJudgeClient",
    "JudgeClient",
    "JudgeResult",
    "SemanticGate",
    "SemanticGateConfig",
    "SemanticGateResources",
    "SemanticInclude",
    "create_registry",
]
