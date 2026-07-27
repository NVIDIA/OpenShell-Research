#!/usr/bin/env python3
"""Check the release details that the package builder cannot validate."""

from __future__ import annotations

import tarfile
import zipfile
from collections.abc import Iterable
from email import policy
from email.message import Message
from email.parser import BytesParser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPOSITORY_ROOT = PROJECT_ROOT.parent.parent
DIST_DIRECTORY = PROJECT_ROOT / "dist"
README_LINKS = {
    "https://github.com/NVIDIA/OpenShell-Research/blob/main/"
    "projects/privacy-guard/examples/regex-engine/README.md": (
        REPOSITORY_ROOT / "projects/privacy-guard/examples/regex-engine/README.md"
    ),
    "https://github.com/NVIDIA/OpenShell-Research/blob/main/"
    "projects/privacy-guard/examples/custom-engine/README.md": (
        REPOSITORY_ROOT / "projects/privacy-guard/examples/custom-engine/README.md"
    ),
    "https://github.com/NVIDIA/OpenShell-Research/blob/main/"
    "projects/openshell-middleware-kit/README.md": (
        REPOSITORY_ROOT / "projects/openshell-middleware-kit/README.md"
    ),
}


def main() -> None:
    """Inspect the one wheel and source distribution produced by ``uv build``."""
    wheel = _one(DIST_DIRECTORY.glob("privacy_guard-*.whl"), "wheel")
    source = _one(DIST_DIRECTORY.glob("privacy_guard-*.tar.gz"), "sdist")
    missing_targets = [
        str(path) for path in README_LINKS.values() if not path.is_file()
    ]
    if missing_targets:
        raise SystemExit(f"README targets do not exist: {', '.join(missing_targets)}")
    _check_wheel(wheel)
    _check_sdist(source)


def _one(paths: Iterable[Path], description: str) -> Path:
    matches = tuple(paths)
    if len(matches) != 1:
        raise SystemExit(f"expected one {description}, found {len(matches)}")
    return matches[0]


def _check_wheel(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        metadata_name = _member_ending(names, ".dist-info/METADATA")
        license_name = _member_ending(names, ".dist-info/licenses/LICENSE")
        _require_member(names, "privacy_guard/py.typed", path)
        _require_license(archive.read(license_name), path)
        _require_metadata(archive.read(metadata_name), path)


def _check_sdist(path: Path) -> None:
    with tarfile.open(path) as archive:
        names = archive.getnames()
        metadata_name = _member_ending(names, "/PKG-INFO")
        license_name = _member_ending(names, "/LICENSE")
        readme_name = _member_ending(names, "/README.md")
        _member_ending(names, "/src/privacy_guard/py.typed")
        _require_license(_read_tar(archive, license_name), path)
        _require_metadata(_read_tar(archive, metadata_name), path)
        _require_readme(_read_tar(archive, readme_name).decode(), path)


def _member_ending(names: list[str], suffix: str) -> str:
    matches = [name for name in names if name.endswith(suffix)]
    if len(matches) != 1:
        raise SystemExit(f"expected one archive member ending in {suffix!r}")
    return matches[0]


def _require_member(names: list[str], member: str, artifact: Path) -> None:
    if member not in names:
        raise SystemExit(f"{artifact.name} is missing {member}")


def _require_license(contents: bytes, artifact: Path) -> None:
    if contents != (PROJECT_ROOT / "LICENSE").read_bytes():
        raise SystemExit(f"{artifact.name} does not contain the exact LICENSE")


def _require_metadata(contents: bytes, artifact: Path) -> None:
    metadata: Message = BytesParser(policy=policy.default).parsebytes(contents)
    if metadata["License-Expression"] != "Apache-2.0":
        raise SystemExit(f"{artifact.name} has the wrong license expression")
    if "LICENSE" not in (metadata.get_all("License-File") or []):
        raise SystemExit(f"{artifact.name} lacks License-File: LICENSE")
    description = metadata.get_payload()
    if not isinstance(description, str):
        raise SystemExit(f"{artifact.name} has no text README")
    _require_readme(description, artifact)


def _require_readme(contents: str, artifact: Path) -> None:
    missing = [url for url in README_LINKS if url not in contents]
    if missing:
        raise SystemExit(
            f"{artifact.name} README is missing canonical links: {', '.join(missing)}"
        )


def _read_tar(archive: tarfile.TarFile, member: str) -> bytes:
    file = archive.extractfile(member)
    if file is None:
        raise SystemExit(f"could not read {member}")
    return file.read()


if __name__ == "__main__":
    main()
