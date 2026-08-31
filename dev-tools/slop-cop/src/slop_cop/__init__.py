"""Editorial policy checks for OpenShell Dev Notes."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("slop-cop")
except PackageNotFoundError:
    __version__ = "0.1.0"

__all__ = ["__version__"]
