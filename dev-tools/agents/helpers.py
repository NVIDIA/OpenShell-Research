#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "jsonschema==4.26.0",
#   "pyyaml==6.0.3",
# ]
# ///
"""Small file-generation and validation helpers for run.sh."""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from jsonschema import Draft202012Validator

MAX_INPUT_BYTES = 1_048_576
MAX_PROMPT_BYTES = 2_097_152
MAX_RESPONSE_BYTES = 1_048_576
IDENTIFIER = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MODEL_ID = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")
ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


class HelperError(ValueError):
    """Expected invalid input or configuration."""


def load_yaml(path: Path, label: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise HelperError(f"{label} is invalid: {error}") from error
    if not isinstance(value, dict):
        raise HelperError(f"{label} must be an object")
    return value


def resolve_inside(
    root: Path, relative: object, label: str, *, directory: bool
) -> Path:
    if not isinstance(relative, str) or not relative:
        raise HelperError(f"{label} must be a non-empty path")
    candidate = root / relative
    try:
        lexical = candidate.relative_to(root)
    except ValueError as error:
        raise HelperError(f"{label} escapes its profile: {relative}") from error
    current = root
    for part in lexical.parts:
        current /= part
        if current.is_symlink():
            raise HelperError(f"{label} contains a symlink: {relative}")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise HelperError(f"{label} does not exist: {relative}") from error
    if not resolved.is_relative_to(root) or resolved.is_symlink():
        raise HelperError(f"{label} escapes its profile: {relative}")
    if resolved.is_dir() != directory or resolved.is_file() == directory:
        raise HelperError(f"{label} has the wrong file type: {relative}")
    return resolved


def require_keys(value: dict[str, Any], required: set[str], label: str) -> None:
    missing = required - value.keys()
    extra = value.keys() - required
    if missing or extra:
        raise HelperError(
            f"{label} keys are invalid; missing={sorted(missing)}, unknown={sorted(extra)}"
        )


def validate_provider(value: dict[str, Any], provider_id: str) -> None:
    require_keys(
        value, {"version", "id", "description", "openshell", "sandbox"}, "provider"
    )
    if value["version"] != 1 or value["id"] != provider_id:
        raise HelperError("provider version or id is invalid")
    openshell = value["openshell"]
    sandbox = value["sandbox"]
    if not isinstance(openshell, dict) or not isinstance(sandbox, dict):
        raise HelperError("provider openshell and sandbox fields must be objects")
    require_keys(
        openshell,
        {
            "type",
            "base_url_source_env",
            "api_key_source_env",
            "base_url_export_env",
            "api_key_export_env",
        },
        "provider.openshell",
    )
    require_keys(sandbox, {"api", "base_url", "api_key"}, "provider.sandbox")
    if not IDENTIFIER.fullmatch(str(openshell["type"])):
        raise HelperError("provider.openshell.type is invalid")
    for name in (
        "base_url_source_env",
        "api_key_source_env",
        "base_url_export_env",
        "api_key_export_env",
    ):
        if not ENVIRONMENT_NAME.fullmatch(str(openshell[name])):
            raise HelperError(f"provider.openshell.{name} is invalid")
    for name in ("api", "base_url", "api_key"):
        if not isinstance(sandbox[name], str) or not sandbox[name]:
            raise HelperError(f"provider.sandbox.{name} is invalid")


def read_input() -> bytes:
    value = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(value) > MAX_INPUT_BYTES:
        raise HelperError("task input exceeds 1 MiB")
    return value


def prepare(arguments: argparse.Namespace) -> None:
    root = arguments.root.resolve(strict=True)
    profiles_root = (root / "profiles").resolve(strict=True)
    try:
        profile_path = profiles_root / arguments.profile
        if profile_path.is_symlink():
            raise HelperError(f"unknown profile: {arguments.profile}")
        profile_root = profile_path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise HelperError(f"unknown profile: {arguments.profile}") from error
    if not profile_root.is_dir() or not profile_root.is_relative_to(profiles_root):
        raise HelperError(f"unknown profile: {arguments.profile}")

    profile = load_yaml(profile_root / "profile.yaml", "profile")
    schema = json.loads(
        (root / "schemas/profile.schema.json").read_text(encoding="utf-8")
    )
    errors = sorted(
        Draft202012Validator(schema).iter_errors(profile),
        key=lambda error: list(error.path),
    )
    if errors:
        raise HelperError(f"profile is invalid: {errors[0].message}")
    if profile["id"] != arguments.profile:
        raise HelperError("profile id must match its directory")
    task = profile["tasks"].get(arguments.task)
    if not isinstance(task, dict):
        raise HelperError(
            f"unknown task '{arguments.task}' for profile '{arguments.profile}'"
        )
    if task.get("skills") and "read" not in task.get("tools", []):
        raise HelperError("tasks with skills must allow the read tool")
    inference = profile["inference"]
    if inference["max_tokens"] > inference["context_window"]:
        raise HelperError("max_tokens cannot exceed context_window")

    harness_name = profile["harness"]["name"]
    harness_root = root / "runtime/harnesses" / harness_name
    if not harness_root.is_dir():
        raise HelperError(f"unknown harness: {harness_name}")
    provider_id = inference["provider"]
    provider = load_yaml(root / f"inference/providers/{provider_id}.yaml", "provider")
    validate_provider(provider, provider_id)
    prompt = resolve_inside(
        profile_root, task["prompt"], "task.prompt", directory=False
    )
    output_schema = resolve_inside(
        profile_root, task["output_schema"], "task.output_schema", directory=False
    )
    policy = resolve_inside(
        profile_root, profile["sandbox"]["policy"], "sandbox.policy", directory=False
    )

    if not MODEL_ID.fullmatch(arguments.model_id):
        raise HelperError("MODEL_ID is invalid")
    destination = arguments.destination
    if destination.exists() or destination.is_symlink():
        raise HelperError(f"staging destination already exists: {destination}")
    sandbox = destination / "sandbox"
    workspace = destination / "workspace"
    payload = sandbox / "payload"
    skills_root = payload / "skills"
    skills_root.mkdir(parents=True)
    workspace.mkdir(parents=True)
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
            raise HelperError(
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
                        "id": arguments.model_id,
                        "name": arguments.model_id,
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

    try:
        parts = [
            prompt.read_text(encoding="utf-8"),
            f"\n\nRequired model identity: model_id `{arguments.model_id}`.\n",
            "\nTrusted response schema:\n",
            output_schema.read_text(encoding="utf-8"),
        ]
        json.loads(parts[-1])
        if arguments.guidance:
            parts.append("\nTrusted guidance:\n")
        for index, guidance in enumerate(arguments.guidance):
            if guidance.is_symlink() or not guidance.is_file():
                raise HelperError(
                    f"guidance[{index}] must be a regular non-symlink file"
                )
            parts.extend(
                (f"\n--- {index:02d}-{guidance.name} ---\n", guidance.read_text())
            )
        parts.extend((f"\n{task['input_label']}:\n", read_input().decode("utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise HelperError(f"prompt input is invalid: {error}") from error
    rendered = "".join(parts).encode()
    if len(rendered) > MAX_PROMPT_BYTES:
        raise HelperError("assembled prompt exceeds 2 MiB")
    (workspace / "prompt.md").write_bytes(rendered)
    (workspace / "prompt.md").chmod(0o600)

    tools = task.get("tools", [])
    resource_args = ["--tools", ",".join(tools)] if tools else ["--no-tools"]
    summary = {
        "profile_id": arguments.profile,
        "task_id": arguments.task,
        "provider": {"id": provider_id, **provider["openshell"]},
        "resource_args": resource_args + skill_args,
    }
    (destination / "resolved.json").write_text(json.dumps(summary), encoding="utf-8")


def values(arguments: argparse.Namespace) -> None:
    value: object = json.loads(arguments.path.read_text(encoding="utf-8"))
    for dotted in arguments.fields:
        current = value
        for key in dotted.split("."):
            if not isinstance(current, dict) or key not in current:
                raise HelperError(f"missing resolved field: {dotted}")
            current = current[key]
        if not isinstance(current, (str, int)):
            raise HelperError(f"resolved field is not scalar: {dotted}")
        print(current)


def resource_args(arguments: argparse.Namespace) -> None:
    value = json.loads(arguments.path.read_text(encoding="utf-8"))
    resources = value.get("resource_args") if isinstance(value, dict) else None
    if not isinstance(resources, list) or not all(
        isinstance(item, str) for item in resources
    ):
        raise HelperError("resolved resource arguments are invalid")
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
        raise HelperError(
            "model base URL must be HTTPS without credentials, query, or fragment"
        )


def validate_response(arguments: argparse.Namespace) -> None:
    if (
        not arguments.path.is_file()
        or not 0 < arguments.path.stat().st_size <= MAX_RESPONSE_BYTES
    ):
        raise HelperError("agent response is missing or exceeds 1 MiB")
    try:
        value = json.loads(arguments.path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise HelperError(f"agent response is not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise HelperError("agent response must be one JSON object")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    commands = result.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--root", type=Path, required=True)
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
    response_parser.set_defaults(handler=validate_response)
    return result


def main() -> int:
    arguments = parser().parse_args()
    try:
        arguments.handler(arguments)
    except HelperError as error:
        print(f"repository agents: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
