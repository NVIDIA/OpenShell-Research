"""Ordered gate execution over one mutable-current immutable request value."""

from __future__ import annotations

from collections.abc import Sequence
from time import monotonic

from pydantic import ValidationError

from egress_gate.config import DefaultDecision, EgressGateConfig
from egress_gate.constants import (
    DEFAULT_DENY_REASON_CODE,
    LIMIT_REASON_CODE,
    MAX_FINDING_COUNT,
    MAX_PROTO_FINDING_BYTES,
    MAX_PROTO_FINDING_GROUPS,
)
from egress_gate.errors import (
    EgressGateError,
    ErrorCode,
    GateConfigurationError,
    GateContractError,
    GateError,
    GateExecutionError,
    GateInputError,
    GateLimitExceededError,
    TimeoutExpiredError,
)
from egress_gate.gates.base import Gate, GateConfig, GateResources
from egress_gate.logging import get_logger
from egress_gate.request import (
    ExistingHeaderAction,
    HttpHeader,
    HttpRequest,
    RemoveHeaderMutation,
    RequestMutations,
    WriteHeaderMutation,
)
from egress_gate.result import (
    DecisionSource,
    DecisionSourceKind,
    EgressDecision,
    EgressResult,
    Finding,
    GateControl,
    GateDecisionSource,
    GateTrace,
    MutationKind,
    PipelineDefaultDecisionSource,
    RuntimeLimitDecisionSource,
    SourcedFinding,
)
from egress_gate.string_validators import validate_scalar_string
from egress_gate.timeout import Timeout


class RequestProcessor:
    """Run configured gates in order over the current request revision."""

    def __init__(
        self,
        config: EgressGateConfig[GateConfig],
        configured_gates: Sequence[
            tuple[str, str, Gate[GateConfig, GateResources | None]]
        ],
        *,
        policy_fingerprint: str | None = None,
    ) -> None:
        gates = tuple(configured_gates)
        configured_names = tuple(name for name, _, _ in gates)
        configured_types = tuple(gate_type for _, gate_type, _ in gates)
        policy_names = tuple(item.name for item in config.gates)
        policy_types = tuple(getattr(item, "kind", None) for item in config.gates)
        if configured_names != policy_names or configured_types != policy_types:
            raise ValueError("configured gates do not match the policy")
        if not gates:
            raise ValueError("at least one configured gate is required")
        if any(not name for name in configured_names):
            raise ValueError("gate names must be non-empty")
        if len(configured_names) != len(set(configured_names)):
            raise ValueError("gate names must be unique")
        if policy_fingerprint is not None:
            try:
                policy_fingerprint = validate_scalar_string(policy_fingerprint)
            except ValueError:
                raise ValueError("policy fingerprint must be a string") from None
        self._config = config
        self._gates = gates
        self._policy_fingerprint = policy_fingerprint

    def process(self, request: HttpRequest, *, timeout: Timeout) -> EgressResult:
        """Evaluate one request and return an atomic final domain result."""
        if not isinstance(request, HttpRequest):
            raise EgressGateError(ErrorCode.REQUEST_ENVELOPE_INVALID)
        if not isinstance(timeout, Timeout):
            raise EgressGateError(ErrorCode.GATE_OUTPUT_INVALID)
        current_request = request
        accumulated_mutations = RequestMutations()
        sourced_findings: list[SourcedFinding] = []
        traces: list[GateTrace] = []

        try:
            for gate_name, gate_type, gate in self._gates:
                timeout.raise_if_expired()
                started = monotonic()
                evaluation = gate.evaluate(current_request, timeout=timeout)
                mutation_kinds = _mutation_kinds(evaluation.request_mutations)
                trace_finding_count = sum(
                    finding.count for finding in evaluation.findings
                )
                if trace_finding_count > MAX_FINDING_COUNT:
                    raise GateLimitExceededError(
                        "gate trace finding count exceeds the limit"
                    )
                traces.append(
                    GateTrace(
                        gate_name=gate_name,
                        gate_type=gate_type,
                        control=evaluation.control,
                        duration_ms=max(0.0, (monotonic() - started) * 1000),
                        finding_count=trace_finding_count,
                        mutation_kinds=mutation_kinds,
                    )
                )
                _append_findings(
                    sourced_findings,
                    gate_name=gate_name,
                    findings=evaluation.findings,
                )
                if len(sourced_findings) > MAX_PROTO_FINDING_GROUPS:
                    raise GateLimitExceededError(
                        "result finding groups exceed the limit"
                    )

                if evaluation.control is GateControl.DENY:
                    return _result(
                        decision=EgressDecision.DENY,
                        source=GateDecisionSource(
                            kind=DecisionSourceKind.GATE,
                            gate_name=gate_name,
                            gate_type=gate_type,
                        ),
                        findings=sourced_findings,
                        reason_code=evaluation.reason_code,
                        fingerprint=self._policy_fingerprint,
                        traces=traces,
                    )
                if evaluation.control is GateControl.ALLOW:
                    return _result(
                        decision=EgressDecision.ALLOW,
                        source=GateDecisionSource(
                            kind=DecisionSourceKind.GATE,
                            gate_name=gate_name,
                            gate_type=gate_type,
                        ),
                        request_mutations=accumulated_mutations,
                        findings=sourced_findings,
                        fingerprint=self._policy_fingerprint,
                        traces=traces,
                    )
                if not evaluation.request_mutations.is_empty:
                    current_request = apply_request_mutations(
                        current_request,
                        evaluation.request_mutations,
                    )
                    accumulated_mutations = _compose_request_mutations(
                        accumulated_mutations,
                        evaluation.request_mutations,
                    )
            timeout.raise_if_expired()
        except TimeoutExpiredError:
            _LOGGER.info("egress_gate_processing_limit kind=timeout")
            return _runtime_limit_result(self._policy_fingerprint)
        except GateLimitExceededError:
            _LOGGER.info("egress_gate_processing_limit kind=resource")
            return _runtime_limit_result(self._policy_fingerprint)
        except GateInputError:
            raise EgressGateError(ErrorCode.BODY_ENCODING_INVALID) from None
        except GateConfigurationError:
            raise EgressGateError(ErrorCode.CONFIG_INVALID) from None
        except GateContractError:
            raise EgressGateError(ErrorCode.GATE_OUTPUT_INVALID) from None
        except GateExecutionError:
            raise EgressGateError(ErrorCode.GATE_EXECUTION_FAILED) from None
        except EgressGateError:
            raise
        except (ValidationError, ValueError):
            raise EgressGateError(ErrorCode.GATE_OUTPUT_INVALID) from None
        except GateError:
            raise EgressGateError(ErrorCode.GATE_EXECUTION_FAILED) from None
        except Exception:
            raise EgressGateError(ErrorCode.GATE_EXECUTION_FAILED) from None

        if self._config.default_decision is DefaultDecision.ALLOW:
            result = _result(
                decision=EgressDecision.ALLOW,
                source=PipelineDefaultDecisionSource(
                    kind=DecisionSourceKind.PIPELINE_DEFAULT
                ),
                request_mutations=accumulated_mutations,
                findings=sourced_findings,
                fingerprint=self._policy_fingerprint,
                traces=traces,
            )
        else:
            result = _result(
                decision=EgressDecision.DENY,
                source=PipelineDefaultDecisionSource(
                    kind=DecisionSourceKind.PIPELINE_DEFAULT
                ),
                findings=sourced_findings,
                reason_code=DEFAULT_DENY_REASON_CODE,
                fingerprint=self._policy_fingerprint,
                traces=traces,
            )
        return result


def apply_request_mutations(
    request: HttpRequest,
    request_mutations: RequestMutations,
) -> HttpRequest:
    """Apply validated mutations to the current request in operation order."""
    if not isinstance(request, HttpRequest) or not isinstance(
        request_mutations, RequestMutations
    ):
        raise GateContractError("request mutation input is invalid")
    body = (
        request.body
        if request_mutations.replacement_body is None
        else request_mutations.replacement_body
    )
    headers = list(request.headers)
    for mutation in request_mutations.header_mutations:
        if isinstance(mutation, WriteHeaderMutation):
            _validate_write_mutation(mutation)
            matching = _header_indexes(headers, mutation.name)
            if mutation.on_existing is ExistingHeaderAction.OVERWRITE:
                headers = [
                    header
                    for index, header in enumerate(headers)
                    if index not in matching
                ]
                headers.append(HttpHeader(name=mutation.name, value=mutation.value))
            elif mutation.on_existing is ExistingHeaderAction.SKIP and matching:
                continue
            else:
                headers.append(HttpHeader(name=mutation.name, value=mutation.value))
        elif isinstance(mutation, RemoveHeaderMutation):
            _validate_remove_mutation(mutation)
            matching = _header_indexes(headers, mutation.name)
            headers = [
                header for index, header in enumerate(headers) if index not in matching
            ]
        else:
            raise GateContractError("request mutation is invalid")
    try:
        return HttpRequest(
            context=request.context,
            target=request.target,
            headers=tuple(headers),
            body=body,
        )
    except (TypeError, ValueError, ValidationError):
        raise GateLimitExceededError(
            "request mutation exceeds a domain limit"
        ) from None


def _append_findings(
    output: list[SourcedFinding],
    *,
    gate_name: str,
    findings: tuple[Finding, ...],
) -> None:
    for finding in findings:
        for index, sourced in enumerate(output):
            if sourced.source_gate != gate_name or sourced.finding.type != finding.type:
                continue
            if (
                sourced.finding.label != finding.label
                or sourced.finding.confidence != finding.confidence
                or sourced.finding.severity != finding.severity
            ):
                continue
            total = sourced.finding.count + finding.count
            if total > MAX_FINDING_COUNT:
                raise GateLimitExceededError("finding count exceeds the limit")
            combined_finding = sourced.finding.model_copy(update={"count": total})
            if combined_finding.encoded_size_bytes > MAX_PROTO_FINDING_BYTES:
                raise GateLimitExceededError(
                    "aggregated finding exceeds the encoded size limit"
                )
            output[index] = SourcedFinding(
                source_gate=gate_name,
                finding=combined_finding,
            )
            break
        else:
            output.append(SourcedFinding(source_gate=gate_name, finding=finding))


def _compose_request_mutations(
    first: RequestMutations,
    second: RequestMutations,
) -> RequestMutations:
    try:
        return RequestMutations(
            replacement_body=(
                second.replacement_body
                if second.replacement_body is not None
                else first.replacement_body
            ),
            header_mutations=first.header_mutations + second.header_mutations,
        )
    except (TypeError, ValueError, ValidationError):
        raise GateLimitExceededError(
            "composed request mutations exceed a pipeline processor limit"
        ) from None


def _mutation_kinds(request_mutations: RequestMutations) -> tuple[MutationKind, ...]:
    kinds: list[MutationKind] = []
    if request_mutations.replacement_body is not None:
        kinds.append(MutationKind.BODY)
    if request_mutations.header_mutations:
        kinds.append(MutationKind.HEADERS)
    return tuple(kinds)


def _result(
    *,
    decision: EgressDecision,
    source: DecisionSource,
    request_mutations: RequestMutations | None = None,
    findings: Sequence[SourcedFinding] = (),
    reason_code: str | None = None,
    fingerprint: str | None,
    traces: Sequence[GateTrace] = (),
) -> EgressResult:
    return EgressResult(
        decision=decision,
        decision_source=source,
        request_mutations=(
            RequestMutations() if request_mutations is None else request_mutations
        ),
        findings=tuple(findings),
        reason_code=reason_code,
        policy_fingerprint=fingerprint,
        traces=tuple(traces),
    )


def _runtime_limit_result(fingerprint: str | None) -> EgressResult:
    return _result(
        decision=EgressDecision.DENY,
        source=RuntimeLimitDecisionSource(kind=DecisionSourceKind.RUNTIME_LIMIT),
        reason_code=LIMIT_REASON_CODE,
        fingerprint=fingerprint,
    )


def _header_indexes(headers: Sequence[HttpHeader], name: str) -> set[int]:
    lowered = name.lower()
    return {
        index for index, header in enumerate(headers) if header.name.lower() == lowered
    }


def _validate_write_mutation(mutation: WriteHeaderMutation) -> None:
    if not mutation.name.lower().startswith("x-openshell-middleware-"):
        raise GateContractError("header writes require the middleware namespace")


def _validate_remove_mutation(mutation: RemoveHeaderMutation) -> None:
    if mutation.name.lower() in _PROTECTED_HEADER_NAMES:
        raise GateContractError("protected headers cannot be removed")


_PROTECTED_HEADER_NAMES = frozenset(
    {
        "authorization",
        "connection",
        "content-length",
        "host",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_LOGGER = get_logger(__name__)


__all__ = [
    "RequestProcessor",
    "apply_request_mutations",
]
