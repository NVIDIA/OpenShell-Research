"""Reachy Mini conversation app fork for OpenShell demos."""

from importlib.metadata import PackageNotFoundError, version


try:
    __version__ = version("reachy_mini_conversation_app")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = ["__version__"]
