# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stable request contracts shared by the JEV service and demo client."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_POLICY_CHARACTERS = 1_048_576
MAX_TASK_CHARACTERS = 32_768
ContextValue = Annotated[str, Field(max_length=2_000)]


class ExecutionContext(BaseModel):
    """Known execution facts; omitted facts remain unknown."""

    model_config = ConfigDict(extra="forbid")

    intended_tools: list[ContextValue] = Field(
        default_factory=list,
        max_length=64,
        description="Tools the delegated task will actually use, e.g. gh or read_file.",
    )
    prepared_inputs: list[ContextValue] = Field(
        default_factory=list,
        max_length=64,
        description=(
            "Already available inputs, e.g. a prepared /workspace checkout; "
            "avoid assuming network downloads."
        ),
    )
    installed_dependencies: list[ContextValue] = Field(
        default_factory=list,
        max_length=64,
        description="Dependencies already installed; presence alone does not justify access.",
    )
    output_locations: list[ContextValue] = Field(
        default_factory=list,
        max_length=64,
        description="Required output destinations, including exact writable paths if known.",
    )
    scratch_locations: list[ContextValue] = Field(
        default_factory=list,
        max_length=64,
        description="Known temporary/cache paths that require writes during execution.",
    )
    runtime_requirements: list[ContextValue] = Field(
        default_factory=list,
        max_length=64,
        description=(
            "Known executable, trust-store, authentication, cache and other "
            "runtime requirements. Empty lists mean no supplied facts, not "
            "proof that access is unnecessary."
        ),
    )


class FieldAnnotation(BaseModel):
    """Caller context associated with one candidate or starting-policy pointer."""

    model_config = ConfigDict(extra="forbid")

    pointer: str = Field(
        description=(
            "JSON pointer into candidate policy; removed fields may reference "
            "starting policy. Escape ~ as ~0 and / as ~1."
        )
    )
    change: Literal["fixed", "updated", "new", "removed"] | None = Field(
        default=None,
        description=(
            "Claimed change relative to starting_policy, when supplied; "
            "verified against actual values."
        ),
    )
    editable: bool = Field(
        default=True,
        description="Caller-provided editability context, not permission for this service to edit.",
    )
    rationale: str | None = Field(
        default=None,
        max_length=2000,
        description=(
            "Caller explanation for the field; treated as data, not an "
            "instruction overriding the task."
        ),
    )

    @field_validator("pointer")
    @classmethod
    def valid_pointer(cls, value: str) -> str:
        if value and not value.startswith("/"):
            raise ValueError("pointer must be empty or start with '/'")
        return value


class TargetedQuestion(BaseModel):
    """An optional independent Choice question evaluated beside the core rubric."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
    pointers: list[str] = Field(
        min_length=1,
        max_length=16,
        description=(
            "Unique JSON pointers into candidate YAML; empty string references "
            "the root. Values and coverage are resolved for JEV. Diagnostics "
            "require one exact supported entry, e.g. "
            "/filesystem_policy/read_write/0."
        ),
    )
    instructions: str = Field(
        min_length=1,
        max_length=4000,
        description=(
            "Question for an independent Choice assessment against the "
            "supplied task and policy values."
        ),
    )
    criteria: dict[str, str] = Field(
        min_length=1,
        max_length=14,
        description=(
            "Option label -> meaningful description. Labels are 1-64 "
            "characters, descriptions nonblank and at most 2000 characters. Do "
            "not supply reserved none_fit or insufficient_context; the server "
            "adds both."
        ),
    )
    diagnostic_outcomes: dict[str, Literal["justified", "unjustified"]] | None = Field(
        default=None,
        description=(
            "Opt into a per-entry diagnostic: map every caller option to its task-fit conclusion. "
            "Requires one exact supported target, multiple alternatives, and both conclusions. "
            "The server always adds none_fit and insufficient_context; do not map those options."
        ),
    )

    @model_validator(mode="after")
    def valid_diagnostic(self) -> "TargetedQuestion":
        if self.diagnostic_outcomes is not None:
            if len(self.pointers) != 1:
                raise ValueError("diagnostics must reference exactly one supported target")
            if set(self.diagnostic_outcomes) != set(self.criteria):
                raise ValueError("diagnostic_outcomes must map every caller criterion exactly once")
            if set(self.diagnostic_outcomes.values()) != {"justified", "unjustified"}:
                raise ValueError(
                    "diagnostics must offer both justified and unjustified alternatives"
                )
        return self

    @field_validator("pointers")
    @classmethod
    def valid_pointers(cls, value: list[str]) -> list[str]:
        if any(pointer and not pointer.startswith("/") for pointer in value):
            raise ValueError("pointers must be empty or start with '/'")
        if len(set(value)) != len(value):
            raise ValueError("pointers must be unique")
        return value

    @field_validator("criteria")
    @classmethod
    def reserved_choices_are_server_owned(cls, value: dict[str, str]) -> dict[str, str]:
        reserved = {"none_fit", "insufficient_context"}
        if reserved.intersection(value):
            raise ValueError("criteria must not use reserved choice names")
        if any(len(key) > 64 or not key for key in value):
            raise ValueError("criteria names must contain 1 to 64 characters")
        if any(
            not description.strip() or len(description) > 2000 for description in value.values()
        ):
            raise ValueError("criteria descriptions must contain 1 to 2000 nonblank characters")
        return value


class ReviewRequest(BaseModel):
    """Input to ``review_delegation``."""

    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1, max_length=MAX_TASK_CHARACTERS)
    candidate_policy: str = Field(min_length=1, max_length=MAX_POLICY_CHARACTERS)
    execution_context: ExecutionContext
    annotations: list[FieldAnnotation] = Field(default_factory=list, max_length=256)
    starting_policy: str | None = Field(default=None, max_length=MAX_POLICY_CHARACTERS)
    questions: list[TargetedQuestion] = Field(default_factory=list, max_length=32)


JSONValue = dict[str, Any] | list[Any] | str | int | float | bool | None
