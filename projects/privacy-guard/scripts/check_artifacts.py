#!/usr/bin/env python3
"""Verify release semantics that generic packaging checks do not cover."""

from __future__ import annotations

import re
import tarfile
import tomllib
import zipfile
from collections.abc import Iterable
from email import policy
from email.message import Message
from email.parser import BytesParser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPOSITORY_ROOT = PROJECT_ROOT.parent.parent
DIST_DIRECTORY = PROJECT_ROOT / "dist"

CANONICAL_README_TARGETS = {
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
REPOSITORY_RELATIVE_README_TARGETS = (
    "(examples/regex-engine/README.md)",
    "(examples/custom-engine/README.md)",
    "(../openshell-middleware-kit/README.md)",
)


def main() -> None:
    """Inspect the wheel and source distribution produced for this project."""
    project = _load_project_metadata()
    distribution_name = re.sub(r"[-_.]+", "_", project["name"])
    version = project["version"]
    archive_stem = f"{distribution_name}-{version}"

    wheel = _find_one(DIST_DIRECTORY.glob(f"{archive_stem}-*.whl"), "wheel")
    source_distribution = _find_one(
        DIST_DIRECTORY.glob(f"{archive_stem}.tar.gz"),
        "source distribution",
    )

    _check_readme_targets()
    _check_wheel(wheel, archive_stem)
    _check_source_distribution(source_distribution, archive_stem)


def _load_project_metadata() -> dict[str, str]:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)["project"]
    return {"name": project["name"], "version": project["version"]}


def _find_one(paths: Iterable[Path], description: str) -> Path:
    matches = sorted(paths)
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one {description}, found {len(matches)}")
    return matches[0]


def _check_readme_targets() -> None:
    missing = [
        str(target.relative_to(REPOSITORY_ROOT))
        for target in CANONICAL_README_TARGETS.values()
        if not target.is_file()
    ]
    if missing:
        raise SystemExit(
            "canonical README link targets are missing: " + ", ".join(missing)
        )


def _check_wheel(wheel: Path, archive_stem: str) -> None:
    metadata_member = f"{archive_stem}.dist-info/METADATA"
    license_member = f"{archive_stem}.dist-info/licenses/LICENSE"
    typed_marker = "privacy_guard/py.typed"

    with zipfile.ZipFile(wheel) as archive:
        members = set(archive.namelist())
        _require_members(
            wheel.name,
            members,
            (metadata_member, license_member, typed_marker),
        )
        _require_exact_license(wheel.name, archive.read(license_member))
        metadata = _parse_metadata(archive.read(metadata_member))
        _require_license_metadata(wheel.name, metadata)
        _require_readme_links(wheel.name, _metadata_description(metadata))

    print(f"{wheel.name}: {license_member}, {typed_marker}, License-File: LICENSE")


def _check_source_distribution(
    source_distribution: Path,
    archive_stem: str,
) -> None:
    metadata_member = f"{archive_stem}/PKG-INFO"
    license_member = f"{archive_stem}/LICENSE"
    readme_member = f"{archive_stem}/README.md"
    typed_marker = f"{archive_stem}/src/privacy_guard/py.typed"

    with tarfile.open(source_distribution, "r:gz") as archive:
        members = set(archive.getnames())
        _require_members(
            source_distribution.name,
            members,
            (metadata_member, license_member, readme_member, typed_marker),
        )
        _require_exact_license(
            source_distribution.name,
            _read_tar_member(archive, license_member),
        )
        metadata = _parse_metadata(_read_tar_member(archive, metadata_member))
        _require_license_metadata(source_distribution.name, metadata)
        _require_readme_links(
            f"{source_distribution.name}:{readme_member}",
            _read_tar_member(archive, readme_member).decode("utf-8"),
        )
        _require_readme_links(
            f"{source_distribution.name}:{metadata_member}",
            _metadata_description(metadata),
        )

    print(
        f"{source_distribution.name}: {license_member}, {typed_marker}, "
        "License-File: LICENSE"
    )


def _require_members(
    artifact: str,
    members: set[str],
    required: tuple[str, ...],
) -> None:
    missing = [member for member in required if member not in members]
    if missing:
        raise SystemExit(f"{artifact} is missing: {', '.join(missing)}")


def _require_exact_license(artifact: str, packaged_license: bytes) -> None:
    expected_license = (PROJECT_ROOT / "LICENSE").read_bytes()
    if packaged_license != expected_license:
        raise SystemExit(f"{artifact} does not contain the exact project LICENSE")


def _parse_metadata(contents: bytes) -> Message:
    return BytesParser(policy=policy.default).parsebytes(contents)


def _require_license_metadata(artifact: str, metadata: Message) -> None:
    if metadata["License-Expression"] != "Apache-2.0":
        raise SystemExit(
            f"{artifact} metadata does not declare License-Expression: Apache-2.0"
        )
    license_files = metadata.get_all("License-File") or []
    if "LICENSE" not in license_files:
        raise SystemExit(f"{artifact} metadata is missing License-File: LICENSE")


def _metadata_description(metadata: Message) -> str:
    description = metadata.get_payload()
    if not isinstance(description, str):
        raise SystemExit("package metadata description is not text")
    return description


def _require_readme_links(artifact: str, readme: str) -> None:
    missing = [url for url in CANONICAL_README_TARGETS if url not in readme]
    if missing:
        raise SystemExit(
            f"{artifact} README is missing canonical links: {', '.join(missing)}"
        )
    retained = [
        relative
        for relative in REPOSITORY_RELATIVE_README_TARGETS
        if relative in readme
    ]
    if retained:
        raise SystemExit(
            f"{artifact} README retains repository-relative links: "
            + ", ".join(retained)
        )


def _read_tar_member(archive: tarfile.TarFile, member: str) -> bytes:
    extracted = archive.extractfile(member)
    if extracted is None:
        raise SystemExit(f"could not read source distribution member {member}")
    return extracted.read()


if __name__ == "__main__":
    main()
