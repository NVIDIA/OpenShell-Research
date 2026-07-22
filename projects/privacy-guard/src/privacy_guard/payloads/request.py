"""The provider-bound request captured pre-credentials, free of proto types."""

from __future__ import annotations

from pydantic import Field, field_validator

from privacy_guard.config import PolicyConfig
from privacy_guard.validation import StrictSensitiveModel


class InterceptedRequest(StrictSensitiveModel):
    """A captured HTTP request, decoupled from the proto ``HttpRequestEvaluation``.

    The servicer builds this from the proto message so that nothing below the
    service layer depends on ``bindings/``. ``raw_body`` is kept out of ``repr``
    so routine object representations do not include the sensitive payload.
    """

    raw_body: bytes = Field(repr=False)
    # Retained as normalized request context for future format negotiation; the
    # JSON handler is selected explicitly by policy and does not inspect it.
    content_type: str = "application/json"
    policy_config: PolicyConfig = Field(default_factory=PolicyConfig, repr=False)
    request_id: str = ""

    @field_validator("policy_config", mode="before")
    @classmethod
    def require_parsed_policy_config(cls, value: object) -> object:
        """Require policy input to cross its content-safe parser first."""
        if not isinstance(value, PolicyConfig):
            raise ValueError("policy config must already be parsed")
        return value
