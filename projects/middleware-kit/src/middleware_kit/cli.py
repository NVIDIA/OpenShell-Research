"""Typer command-line interface for OpenShell middleware projects."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from middleware_kit.generator import (
    InitializationError,
    initialize_project,
    update_project,
)


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
        result = initialize_project(
            name=name,
            language=language.value,
            requested_version=openshell_version,
            destination=destination,
            package_name=package_name,
        )
    except InitializationError as error:
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
) -> None:
    """Update an existing middleware project's OpenShell contract and generated files."""
    try:
        result = update_project(
            project_dir=project,
            requested_version=openshell_version,
        )
    except InitializationError as error:
        _report_error(error)

    typer.echo(f"Updated {result.language} middleware project at {result.destination}")
    typer.echo(f"OpenShell contract: {result.openshell_version}")


def _report_error(error: InitializationError) -> None:
    typer.echo(f"mkit: error: {error}", err=True)
    raise typer.Exit(code=1) from error


def main() -> None:
    """Run the command-line application."""
    app()


if __name__ == "__main__":
    main()
