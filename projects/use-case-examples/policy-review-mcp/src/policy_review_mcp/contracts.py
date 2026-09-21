# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stable request contracts shared by the JEV service and demo client."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_POLICY_CHARACTERS = 1_048_576
MAX_TASK_CHARACTERS = 32_768
ContextValue = Annotated[str, Field(max_length=2_000)]


class ExecutionContext(BaseModel):
    """Known execution facts; omitted facts remain unknown."""

    model_config = ConfigDict(extra="forbid")

    intended_tools: list[ContextValue] = Field(default_factory=list, max_length=64)
    prepared_inputs: list[ContextValue] = Field(default_factory=list, max_length=64)
    installed_dependencies: list[ContextValue] = Field(default_factory=list, max_length=64)
    output_locations: list[ContextValue] = Field(default_factory=list, max_length=64)
    scratch_locations: list[ContextValue] = Field(default_factory=list, max_length=64)
    runtime_requirements: list[ContextValue] = Field(default_factory=list, max_length=64)


class FieldAnnotation(BaseModel):
    """Caller context associated with one candidate or starting-policy pointer."""

    model_config = ConfigDict(extra="forbid")

    pointer: str
    change: Literal["fixed", "updated", "new", "removed"] | None = None
    editable: bool = True
    rationale: str | None = Field(default=None, max_length=2000)

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
    pointers: list[str] = Field(min_length=1, max_length=16)
    instructions: str = Field(min_length=1, max_length=4000)
    criteria: dict[str, str] = Field(min_length=1, max_length=14)

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
        if any(len(description) > 2000 for description in value.values()):
            raise ValueError("criteria descriptions must not exceed 2000 characters")
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
