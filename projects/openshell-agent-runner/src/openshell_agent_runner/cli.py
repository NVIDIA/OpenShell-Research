# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Typer command-line interface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, NoReturn

import typer

from openshell_agent_runner.config import load_profile
from openshell_agent_runner.errors import ArtifactError, ConfigurationError, OarError
from openshell_agent_runner.openshell import NativeTarget
from openshell_agent_runner.openshell import doctor as run_doctor
from openshell_agent_runner.runner import RunRequest, render_dry_run, run_agent

app = typer.Typer(
    help="Validate and run agent profiles in OpenShell sandboxes.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)


@app.command()
def validate(
    profile: Annotated[
        Path, typer.Argument(help="Path to a profile YAML file.", metavar="PROFILE")
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


@app.command()
def run(
    profile: Annotated[
        Path, typer.Argument(help="Path to a profile YAML file.", metavar="PROFILE")
    ],
    task: Annotated[str, typer.Option("--task", help="Task identifier to run.")],
    output: Annotated[
        Path, typer.Option("--output", help="Host path for the validated output.")
    ],
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
    """Run or preview one profile task and its validated output."""
    request = RunRequest(
        profile_path=profile,
        task_id=task,
        output=output,
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
    for name, result in checks:
        typer.echo(f"[{name}]\n{result}")


def _fail(error: OarError) -> NoReturn:
    typer.echo(f"oar: {error}", err=True)
    if isinstance(error, ArtifactError):
        raise typer.Exit(3)
    if isinstance(error, ConfigurationError):
        raise typer.Exit(2)
    raise typer.Exit(1)


if __name__ == "__main__":
    app()
