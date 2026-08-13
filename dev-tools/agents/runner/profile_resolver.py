#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "jsonschema==4.26.0",
#   "pyyaml==6.0.3",
# ]
# ///
"""Resolve repository agent profiles into staged runtime configuration."""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from jsonschema import Draft202012Validator, SchemaError

MAX_INPUT_BYTES = 1_048_576
MAX_PROMPT_BYTES = 2_097_152
MAX_RESPONSE_BYTES = 1_048_576
MODEL_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")


class ProfileResolverError(ValueError):
    """Expected invalid input or configuration."""


def load_yaml(path: Path, label: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise ProfileResolverError(f"{label} is invalid: {error}") from error
    if not isinstance(value, dict):
        raise ProfileResolverError(f"{label} must be an object")
    return value


def resolve_inside(
    root: Path, relative: object, label: str, *, directory: bool
) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ProfileResolverError(f"{label} must be a non-empty path")
    candidate = root / relative
    try:
        lexical = candidate.relative_to(root)
    except ValueError as error:
        raise ProfileResolverError(
            f"{label} escapes its profile: {relative}"
        ) from error
    current = root
    for part in lexical.parts:
        current /= part
        if current.is_symlink():
            raise ProfileResolverError(f"{label} contains a symlink: {relative}")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ProfileResolverError(f"{label} does not exist: {relative}") from error
    if not resolved.is_relative_to(root) or resolved.is_symlink():
        raise ProfileResolverError(f"{label} escapes its profile: {relative}")
    if resolved.is_dir() != directory or resolved.is_file() == directory:
        raise ProfileResolverError(f"{label} has the wrong file type: {relative}")
    return resolved


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProfileResolverError(f"{label} is invalid: {error}") from error
    if not isinstance(value, dict):
        raise ProfileResolverError(f"{label} must be an object")
    return value


def validate_document(value: object, schema: dict[str, Any], label: str) -> None:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise ProfileResolverError(f"{label} schema is invalid: {error}") from error
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda error: list(error.path),
    )
    if errors:
        raise ProfileResolverError(f"{label} is invalid: {errors[0].message}")


def task_output_schema(schema: dict[str, Any], definition: object) -> dict[str, Any]:
    if definition is None:
        return schema
    definitions = schema.get("$defs")
    if not isinstance(definition, str) or not isinstance(definitions, dict):
        raise ProfileResolverError("task.output_schema_definition is invalid")
    if definition not in definitions:
        raise ProfileResolverError(f"unknown output schema definition: {definition}")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": f"#/$defs/{definition}",
        "$defs": definitions,
    }


def read_input() -> bytes:
    value = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(value) > MAX_INPUT_BYTES:
        raise ProfileResolverError("task input exceeds 1 MiB")
    return value


def resolve_profile(
    root: Path, profile_id: str, task_id: str
) -> tuple[Path, dict[str, Any], dict[str, Any], Path, dict[str, Any]]:
    profiles_root = (root.parent / "profiles").resolve(strict=True)
    try:
        profile_path = profiles_root / profile_id
        if profile_path.is_symlink():
            raise ProfileResolverError(f"unknown profile: {profile_id}")
        profile_root = profile_path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ProfileResolverError(f"unknown profile: {profile_id}") from error
    if not profile_root.is_dir() or not profile_root.is_relative_to(profiles_root):
        raise ProfileResolverError(f"unknown profile: {profile_id}")

    profile = load_yaml(profile_root / "profile.yaml", "profile")
    validate_document(
        profile,
        load_json(root / "profile.schema.json", "profile schema"),
        "profile",
    )
    if profile["id"] != profile_id:
        raise ProfileResolverError("profile id must match its directory")
    task = profile["tasks"].get(task_id)
    if not isinstance(task, dict):
        raise ProfileResolverError(
            f"unknown task '{task_id}' for profile '{profile_id}'"
        )
    if task.get("skills") and "read" not in task.get("tools", []):
        raise ProfileResolverError("tasks with skills must allow the read tool")
    inference = profile["inference"]
    if inference["max_tokens"] > inference["context_window"]:
        raise ProfileResolverError("max_tokens cannot exceed context_window")

    harness_name = profile["harness"]
    harness_root = root / "harnesses" / harness_name
    if not harness_root.is_dir():
        raise ProfileResolverError(f"unknown harness: {harness_name}")
    provider_id = inference["provider"]
    provider = load_yaml(root / f"providers/{provider_id}.yaml", "provider")
    validate_document(
        provider,
        load_json(root / "provider.schema.json", "provider schema"),
        "provider",
    )
    if provider["id"] != provider_id:
        raise ProfileResolverError("provider id must match its filename")
    return profile_root, profile, task, harness_root, provider


def stage_sandbox(
    destination: Path,
    harness_root: Path,
    policy: Path,
    profile_root: Path,
    task: dict[str, Any],
    provider: dict[str, Any],
    inference: dict[str, Any],
    model_id: str,
) -> list[str]:
    sandbox = destination / "sandbox"
    payload = sandbox / "payload"
    skills_root = payload / "skills"
    skills_root.mkdir(parents=True)
    shutil.copy2(harness_root / "Dockerfile", sandbox / "Dockerfile")
    shutil.copy2(harness_root / "exec.sh", sandbox / "exec.sh")
    (sandbox / "exec.sh").chmod(0o755)
    shutil.copy2(policy, sandbox / "policy.yaml")

    skill_args: list[str] = []
    for index, relative in enumerate(task.get("skills", [])):
        skill = resolve_inside(
            profile_root, relative, f"task.skills[{index}]", directory=True
        )
        if (
            any(path.is_symlink() for path in skill.rglob("*"))
            or not (skill / "SKILL.md").is_file()
        ):
            raise ProfileResolverError(
                f"task.skills[{index}] contains a symlink or lacks SKILL.md"
            )
        name = f"{index:02d}-{skill.name}"
        shutil.copytree(skill, skills_root / name)
        skill_args.extend(("--skill", f"/etc/openshell/agent-payload/skills/{name}"))

    sandbox_provider = provider["sandbox"]
    models = {
        "providers": {
            "repository-agent": {
                "baseUrl": sandbox_provider["base_url"],
                "api": sandbox_provider["api"],
                "apiKey": sandbox_provider["api_key"],
                "authHeader": True,
                "compat": {
                    "supportsDeveloperRole": False,
                    "supportsReasoningEffort": False,
                },
                "models": [
                    {
                        "id": model_id,
                        "name": model_id,
                        "reasoning": False,
                        "input": ["text"],
                        "contextWindow": inference["context_window"],
                        "maxTokens": inference["max_tokens"],
                        "cost": {
                            "input": 0,
                            "output": 0,
                            "cacheRead": 0,
                            "cacheWrite": 0,
                        },
                    }
                ],
            }
        }
    }
    (payload / "models.json").write_text(json.dumps(models), encoding="utf-8")
    (payload / "settings.json").write_text(
        json.dumps({"enableInstallTelemetry": False, "defaultProjectTrust": "never"}),
        encoding="utf-8",
    )
    (sandbox / ".dockerignore").write_text(
        "*\n!Dockerfile\n!exec.sh\n!policy.yaml\n!payload/\n!payload/**\n",
        encoding="utf-8",
    )
    return skill_args


def assemble_prompt(
    workspace: Path,
    prompt: Path,
    response_schema: dict[str, Any],
    task: dict[str, Any],
    model_id: str,
    guidance: list[Path],
) -> None:
    workspace.mkdir(parents=True)

    try:
        parts = [
            prompt.read_text(encoding="utf-8"),
            f"\n\nRequired model identity: model_id `{model_id}`.\n",
            "\nTrusted response schema:\n",
            json.dumps(response_schema),
        ]
        if guidance:
            parts.append("\nTrusted guidance:\n")
        for index, guidance_file in enumerate(guidance):
            parts.extend(
                (
                    f"\n--- {index:02d}-{guidance_file.name} ---\n",
                    guidance_file.read_text(encoding="utf-8"),
                )
            )
        parts.extend((f"\n{task['input_label']}:\n", read_input().decode("utf-8")))
    except (OSError, UnicodeError) as error:
        raise ProfileResolverError(f"prompt input is invalid: {error}") from error
    rendered = "".join(parts).encode()
    if len(rendered) > MAX_PROMPT_BYTES:
        raise ProfileResolverError("assembled prompt exceeds 2 MiB")
    (workspace / "prompt.md").write_bytes(rendered)
    (workspace / "prompt.md").chmod(0o600)


def prepare(arguments: argparse.Namespace) -> None:
    root = arguments.runner_root.resolve(strict=True)
    profile_root, profile, task, harness_root, provider = resolve_profile(
        root, arguments.profile, arguments.task
    )
    if not MODEL_IDENTIFIER.fullmatch(arguments.model_id):
        raise ProfileResolverError("MODEL_ID_TOP is invalid")
    destination = arguments.destination
    if destination.exists() or destination.is_symlink():
        raise ProfileResolverError(f"staging destination already exists: {destination}")

    prompt = resolve_inside(
        profile_root, task["prompt"], "task.prompt", directory=False
    )
    schema_path = resolve_inside(
        profile_root, task["output_schema"], "task.output_schema", directory=False
    )
    response_schema = task_output_schema(
        load_json(schema_path, "response schema"), task.get("output_schema_definition")
    )
    model_constraint = {
        "type": "object",
        "required": ["model_id"],
        "properties": {"model_id": {"const": arguments.model_id}},
    }
    existing_constraints = response_schema.get("allOf")
    if existing_constraints is None:
        response_schema["allOf"] = [model_constraint]
    elif isinstance(existing_constraints, list):
        existing_constraints.append(model_constraint)
    else:
        raise ProfileResolverError("response schema allOf must be an array")
    try:
        Draft202012Validator.check_schema(response_schema)
    except SchemaError as error:
        raise ProfileResolverError(f"response schema is invalid: {error}") from error
    policy = resolve_inside(profile_root, profile["policy"], "policy", directory=False)
    profile_guidance = [
        resolve_inside(
            profile_root, relative, f"task.guidance[{index}]", directory=False
        )
        for index, relative in enumerate(task.get("guidance", []))
    ]
    for index, guidance in enumerate(arguments.guidance):
        if guidance.is_symlink() or not guidance.is_file():
            raise ProfileResolverError(
                f"guidance[{index}] must be a regular non-symlink file"
            )

    skill_args = stage_sandbox(
        destination,
        harness_root,
        policy,
        profile_root,
        task,
        provider,
        profile["inference"],
        arguments.model_id,
    )
    assemble_prompt(
        destination / "workspace",
        prompt,
        response_schema,
        task,
        arguments.model_id,
        profile_guidance + arguments.guidance,
    )
    (destination / "response.schema.json").write_text(
        json.dumps(response_schema), encoding="utf-8"
    )

    tools = task.get("tools", [])
    resource_args = ["--tools", ",".join(tools)] if tools else ["--no-tools"]
    summary = {
        "profile_id": arguments.profile,
        "task_id": arguments.task,
        "provider": {"id": profile["inference"]["provider"], **provider["openshell"]},
        "resource_args": resource_args + skill_args,
    }
    (destination / "resolved.json").write_text(json.dumps(summary), encoding="utf-8")


def values(arguments: argparse.Namespace) -> None:
    value: object = json.loads(arguments.path.read_text(encoding="utf-8"))
    for dotted in arguments.fields:
        current = value
        for key in dotted.split("."):
            if not isinstance(current, dict) or key not in current:
                raise ProfileResolverError(f"missing resolved field: {dotted}")
            current = current[key]
        if not isinstance(current, (str, int)):
            raise ProfileResolverError(f"resolved field is not scalar: {dotted}")
        print(current)


def resource_args(arguments: argparse.Namespace) -> None:
    value = json.loads(arguments.path.read_text(encoding="utf-8"))
    resources = value.get("resource_args") if isinstance(value, dict) else None
    if not isinstance(resources, list) or not all(
        isinstance(item, str) for item in resources
    ):
        raise ProfileResolverError("resolved resource arguments are invalid")
    for item in resources:
        sys.stdout.buffer.write(item.encode() + b"\0")


def validate_url(arguments: argparse.Namespace) -> None:
    parsed = urlsplit(arguments.url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ProfileResolverError(
            "model base URL must be HTTPS without credentials, query, or fragment"
        )


def validate_response(arguments: argparse.Namespace) -> None:
    if (
        not arguments.path.is_file()
        or not 0 < arguments.path.stat().st_size <= MAX_RESPONSE_BYTES
    ):
        raise ProfileResolverError("agent response is missing or exceeds 1 MiB")
    try:
        value = json.loads(arguments.path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProfileResolverError(
            f"agent response is not valid JSON: {error}"
        ) from error
    if not isinstance(value, dict):
        raise ProfileResolverError("agent response must be one JSON object")
    validate_document(
        value, load_json(arguments.schema, "response schema"), "agent response"
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    commands = result.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--runner-root", type=Path, required=True)
    prepare_parser.add_argument("--profile", required=True)
    prepare_parser.add_argument("--task", required=True)
    prepare_parser.add_argument("--destination", type=Path, required=True)
    prepare_parser.add_argument("--model-id", required=True)
    prepare_parser.add_argument("--guidance", action="append", type=Path, default=[])
    prepare_parser.set_defaults(handler=prepare)
    values_parser = commands.add_parser("values")
    values_parser.add_argument("path", type=Path)
    values_parser.add_argument("fields", nargs="+")
    values_parser.set_defaults(handler=values)
    resources_parser = commands.add_parser("resource-args")
    resources_parser.add_argument("path", type=Path)
    resources_parser.set_defaults(handler=resource_args)
    url_parser = commands.add_parser("validate-url")
    url_parser.add_argument("url")
    url_parser.set_defaults(handler=validate_url)
    response_parser = commands.add_parser("validate-response")
    response_parser.add_argument("path", type=Path)
    response_parser.add_argument("schema", type=Path)
    response_parser.set_defaults(handler=validate_response)
    return result


def main() -> int:
    arguments = parser().parse_args()
    try:
        arguments.handler(arguments)
    except ProfileResolverError as error:
        print(f"repository agents: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
