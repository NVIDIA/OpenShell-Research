# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Typer command-line interface."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from typer._click import Context
from typer.core import TyperCommand

from openshell_agent_runner.config import ResolvedProfile, load_profile, resolve_task
from openshell_agent_runner.errors import ArtifactError, ConfigurationError, OarError
from openshell_agent_runner.openshell import NativeTarget
from openshell_agent_runner.openshell import doctor as run_doctor
from openshell_agent_runner.profile_init import ThinkingLevel, initialize_profiles
from openshell_agent_runner.runner import RunRequest, render_dry_run, run_agent

app = typer.Typer(
    help="Launch ephemeral agents for single tasks in OpenShell sandboxes.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)


class ProfileTaskHelpCommand(TyperCommand):
    """Show focused help when a profile task is selected."""

    def parse_args(self, ctx: Context, args: list[str]) -> list[str]:
        ctx.meta["oar_raw_args"] = list(args)
        return super().parse_args(ctx, args)

    def get_help(self, ctx: Context) -> str:
        selection = _profile_task_selection(ctx.meta.get("oar_raw_args", []))
        if selection is None:
            return super().get_help(ctx)
        profile_directory, task_id = selection
        try:
            resolved = resolve_task(profile_directory, task_id)
        except OarError as error:
            _fail(error)
        return _render_task_help(profile_directory, resolved, task_id)


@app.command()
def init(
    destination: Annotated[
        Path,
        typer.Argument(
            help="Directory that will contain the initialized profiles.",
            metavar="PROFILE_ROOT",
        ),
    ],
    model: Annotated[
        str,
        typer.Option("--model", help="Inference route model identifier."),
    ],
    profile: Annotated[
        list[str] | None,
        typer.Option(
            "--profile",
            help="Packaged profile to initialize. Repeat to select several; omit for all.",
        ),
    ] = None,
    thinking: Annotated[
        ThinkingLevel,
        typer.Option("--thinking", help="Pi thinking level."),
    ] = ThinkingLevel.HIGH,
) -> None:
    """Create editable profiles from resources packaged with OAR."""
    try:
        created = initialize_profiles(
            destination,
            profile or (),
            model,
            thinking,
        )
    except OarError as error:
        _fail(error)
    typer.echo("Created profiles:")
    for path in created:
        typer.echo(f"  {path}")


@app.command()
def validate(
    profile: Annotated[
        Path,
        typer.Argument(
            help="Profile directory containing profile.yaml.",
            metavar="PROFILE_DIRECTORY",
        ),
    ],
) -> None:
    """Validate a profile and all referenced local resources."""
    try:
        resolved = load_profile(profile)
    except OarError as error:
        _fail(error)
    typer.echo(
        f"Valid profile: {resolved.profile.id} ({len(resolved.profile.tasks)} task(s))"
    )


@app.command(cls=ProfileTaskHelpCommand)
def run(
    profile: Annotated[
        Path,
        typer.Argument(
            help="Profile directory containing profile.yaml.",
            metavar="PROFILE_DIRECTORY",
        ),
    ],
    task: Annotated[str, typer.Option("--task", help="Task identifier to run.")],
    output: Annotated[
        Path, typer.Option("--output", help="Host path for the agent result.")
    ],
    input_document: Annotated[
        Path | None,
        typer.Option("--input", help="Host document required by document tasks."),
    ] = None,
    upload: Annotated[
        list[str] | None,
        typer.Option("--upload", help="Native SOURCE:DESTINATION upload mapping."),
    ] = None,
    environment: Annotated[
        list[str] | None,
        typer.Option("--env", help="Non-secret KEY=VALUE sandbox environment."),
    ] = None,
    gateway: Annotated[
        str | None, typer.Option("--gateway", help="OpenShell gateway name.")
    ] = None,
    workspace: Annotated[
        str, typer.Option("--workspace", help="OpenShell workspace name.")
    ] = "default",
    timeout_seconds: Annotated[
        int,
        typer.Option("--timeout-seconds", min=1, help="Maximum agent runtime."),
    ] = 1200,
    keep_sandbox: Annotated[
        bool,
        typer.Option("--keep-sandbox", help="Retain the sandbox for debugging."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Print every command and host action without executing them.",
        ),
    ] = False,
) -> None:
    """Launch or preview an ephemeral agent for one profile task."""
    request = RunRequest(
        profile_directory=profile,
        task_id=task,
        output=output,
        input_document=input_document,
        uploads=upload or (),
        environments=environment or (),
        gateway=gateway,
        workspace=workspace,
        timeout_seconds=timeout_seconds,
        keep_sandbox=keep_sandbox,
    )
    try:
        if dry_run:
            typer.echo(render_dry_run(request), nl=False)
            return
        run_agent(request)
    except OarError as error:
        _fail(error)


@app.command()
def doctor(
    gateway: Annotated[
        str | None, typer.Option("--gateway", help="OpenShell gateway name.")
    ] = None,
    workspace: Annotated[
        str, typer.Option("--workspace", help="OpenShell workspace name.")
    ] = "default",
) -> None:
    """Check OpenShell readiness without changing its state."""
    try:
        checks = run_doctor(NativeTarget(gateway=gateway, workspace=workspace))
    except OarError as error:
        _fail(error)
    typer.echo("\n\n".join(result for _, result in checks))


def _fail(error: OarError) -> NoReturn:
    typer.echo(f"oar: {error}", err=True)
    if isinstance(error, ArtifactError):
        raise typer.Exit(3)
    if isinstance(error, ConfigurationError):
        raise typer.Exit(2)
    raise typer.Exit(1)


def _profile_task_selection(args: list[str]) -> tuple[Path, str] | None:
    if not args or args[0].startswith("-"):
        return None
    profile = Path(args[0])
    for index, argument in enumerate(args[1:], start=1):
        if argument == "--task" and index + 1 < len(args):
            return profile, args[index + 1]
        if argument.startswith("--task="):
            return profile, argument.partition("=")[2]
    return None


def _render_task_help(
    profile_directory: Path,
    resolved: ResolvedProfile,
    task_id: str,
) -> str:
    profile = resolved.profile
    task = profile.tasks[task_id]
    description = task.description or profile.description
    usage_lines = [
        _help_heading("Usage:"),
        _help_command(f"  oar run {shlex.quote(str(profile_directory))} \\"),
        _help_command(f"    --task {shlex.quote(task_id)} \\"),
    ]
    if task.required_input == "document":
        usage_lines.append(_help_command("    --input DOCUMENT \\"))
    usage_lines.append(_help_command("    --output OUTPUT"))

    upload_lines = [_help_heading("Additional configured uploads:")]
    if profile.sandbox.upload:
        upload_lines.extend(f"  {upload}" for upload in profile.sandbox.upload)
    else:
        upload_lines.append("  None.")

    environment_lines = [_help_heading("Configured environment:")]
    if profile.sandbox.env:
        environment_lines.extend(f"  {value}" for value in profile.sandbox.env)
    else:
        environment_lines.append("  None. Add values with --env KEY=VALUE.")

    input_lines = _required_input_help(task.required_input)

    output_description = (
        f"JSON validated against {task.output_schema}."
        if task.output_schema is not None
        else "The agent's final response."
    )
    return "\n".join(
        (
            typer.style(f"{profile.id}:{task_id}", fg=typer.colors.CYAN, bold=True),
            "",
            description,
            "",
            *usage_lines,
            "",
            *input_lines,
            "",
            *upload_lines,
            "",
            *environment_lines,
            "",
            _help_heading("Output:"),
            f"  {output_description}",
            "",
        )
    )


def _required_input_help(required_input: str | None) -> list[str]:
    if required_input is None:
        return [_help_heading("Required input:"), "  None."]
    return [
        _help_heading("Required argument:"),
        _help_command("  --input DOCUMENT"),
        "    Host document to review.",
    ]


def _help_heading(value: str) -> str:
    return typer.style(value, fg=typer.colors.YELLOW, bold=True)


def _help_command(value: str) -> str:
    return typer.style(value, fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
