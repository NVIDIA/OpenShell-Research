# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Typer command-line interface for OpenShell middleware projects."""

from __future__ import annotations

import shlex
from enum import Enum
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path
from typing import Annotated

import typer

from openshell_middleware_manager.generator import (
    ProjectError,
    create_project,
    update_project,
)


def version_callback(value: bool) -> None:
    """Print the installed OMM version when requested."""
    if value:
        typer.echo(f"omm {_installed_version()}")
        raise typer.Exit()


class Language(str, Enum):
    """Middleware implementation languages supported by the generator."""

    PYTHON = "python"
    RUST = "rust"


app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help="Create or update a version-matched OpenShell middleware project.",
)


@app.callback()
def root(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            callback=version_callback,
            is_eager=True,
            help="Show the installed OMM version and exit.",
        ),
    ] = None,
) -> None:
    """Create or update a version-matched OpenShell middleware project."""


@app.command()
def create(
    name: Annotated[
        str,
        typer.Argument(help="Project name, such as audit-headers."),
    ],
    language: Annotated[
        Language,
        typer.Option("--language", "-l", help="Implementation language."),
    ],
    openshell_version: Annotated[
        str,
        typer.Option(
            "--openshell-version",
            "--version",
            help="OpenShell release tag (for example v0.0.86), or latest.",
        ),
    ],
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Destination directory. Defaults to ./<name>.",
        ),
    ] = None,
    package_name: Annotated[
        str | None,
        typer.Option(
            "--package-name",
            help="Python import package override (Python projects only).",
        ),
    ] = None,
) -> None:
    """Create a new OpenShell supervisor middleware project."""
    destination = output if output is not None else Path.cwd() / name
    try:
        result = create_project(
            name=name,
            language=language.value,
            requested_version=openshell_version,
            destination=destination,
            package_name=package_name,
        )
    except ProjectError as error:
        _report_error(error)

    typer.echo(f"Created {result.language} middleware project at {result.destination}")
    typer.echo(f"OpenShell contract: {result.openshell_version}")
    typer.echo(f"Next: cd {result.destination} && {result.run_command}")


@app.command()
def update(
    project: Annotated[
        Path,
        typer.Argument(
            help="Existing generated middleware project. Defaults to the current directory."
        ),
    ] = Path("."),
    openshell_version: Annotated[
        str,
        typer.Option(
            "--openshell-version",
            "--version",
            help="OpenShell release tag (for example v0.0.86), or latest.",
        ),
    ] = "latest",
    check_command: Annotated[
        str | None,
        typer.Option(
            "--check-command",
            help="Python project validation command instead of pytest (no shell expansion).",
        ),
    ] = None,
) -> None:
    """Update an existing middleware project's OpenShell contract and generated files."""
    try:
        try:
            command = shlex.split(check_command) if check_command is not None else None
        except ValueError as error:
            raise ProjectError(f"invalid check command: {error}") from error
        result = update_project(
            project_dir=project,
            requested_version=openshell_version,
            check_command=command,
        )
    except ProjectError as error:
        _report_error(error)

    typer.echo(f"Updated {result.language} middleware project at {result.destination}")
    typer.echo(f"OpenShell contract: {result.openshell_version}")


def _report_error(error: ProjectError) -> None:
    typer.echo(f"omm: error: {error}", err=True)
    raise typer.Exit(code=1) from error


def _installed_version() -> str:
    try:
        return distribution_version("openshell-middleware-manager")
    except PackageNotFoundError:
        return "unknown"


def main() -> None:
    """Run the command-line application."""
    app()


if __name__ == "__main__":
    main()
