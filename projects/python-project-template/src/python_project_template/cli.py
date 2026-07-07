# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Command-line interface for the example project."""

import click


def greeting(name: str) -> str:
    """Build a greeting for ``name``."""
    return f"Hello, {name}!"


@click.command()
@click.option("--name", default="world", show_default=True, help="Name to greet.")
def main(name: str) -> None:
    """Print a friendly greeting."""
    click.echo(greeting(name))
