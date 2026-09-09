# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# /// script
# requires-python = ">=3.12"
# dependencies = ["license-expression==30.4.4", "boolean.py==5.0"]
# ///

"""Check added/changed locked dependencies without installing project packages."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

import tomllib

POLICY_PATH = ".github/dependency-license-policy.toml"
LOCK_NAMES = {"uv.lock", "package-lock.json", "Cargo.lock"}
MANIFEST_LOCKS = {
    "pyproject.toml": "uv.lock",
    "package.json": "package-lock.json",
    "Cargo.toml": "Cargo.lock",
}
MANIFEST_MANAGERS = {
    "pyproject.toml": "uv",
    "package.json": "npm",
    "Cargo.toml": "cargo",
}


@dataclass(frozen=True, order=True)
class Dependency:
    ecosystem: str
    name: str
    version: str
    source: str


def inventory(path: str, content: str) -> set[Dependency]:
    """Include every locked external package, irrespective of groups or platform."""
    result = set()
    if Path(path).name == "package-lock.json":
        lock = json.loads(content)
        if lock.get("lockfileVersion") not in (2, 3) or "packages" not in lock:
            raise ValueError(
                f"{path}: expected npm lockfileVersion 2 or 3 with packages"
            )
        workspace_patterns = lock["packages"].get("", {}).get("workspaces", [])
        workspace_targets = set()
        for package in lock["packages"].values():
            resolved = package.get("resolved")
            if not package.get("link") or not isinstance(resolved, str):
                continue
            target = resolved.removeprefix("./").rstrip("/")
            if any(Path(target).match(pattern) for pattern in workspace_patterns):
                workspace_targets.add(target)
        for location, package in lock["packages"].items():
            if (
                not location
                or package.get("link")
                or location.removeprefix("./").rstrip("/") in workspace_targets
            ):
                continue  # Root package, workspace link, or its first-party target.
            name = package.get("name") or location.rsplit("node_modules/", 1)[-1]
            source = package.get("resolved") or f"local:{location}"
            result.add(Dependency("npm", name, package.get("version", ""), source))
    else:
        lock = tomllib.loads(content)
        if "package" not in lock:
            raise ValueError(f"{path}: missing package inventory")
        workspace_members = set(lock.get("manifest", {}).get("members", []))
        for package in lock["package"]:
            source = package.get("source")
            if Path(path).name == "uv.lock" or path.endswith(".py.lock"):
                if package["name"] in workspace_members or source in (
                    {"editable": "."},
                    {"virtual": "."},
                ):
                    continue  # First-party project/workspace source.
                source = json.dumps(source, sort_keys=True, separators=(",", ":"))
                ecosystem = "pypi"
            else:
                if source is None:
                    continue  # Cargo workspace/path packages are repository source.
                ecosystem = "cargo"
            result.add(
                Dependency(
                    ecosystem, package["name"], package.get("version", ""), source
                )
            )
    return result


def allowed_expression(expression: str, approved: list[str]) -> bool:
    """Parse SPDX using its library; evaluate AND/OR over approved license atoms."""
    from license_expression import ExpressionError, get_spdx_licensing

    if not isinstance(expression, str) or not expression.strip():
        return False
    licensing = get_spdx_licensing()
    try:
        parsed = licensing.parse(expression, validate=True, strict=True)
    except ExpressionError:
        return False
    if parsed is None:
        return False
    # A WITH expression is one symbol, so only that exact combination can pass.
    values = {
        symbol: licensing.TRUE if str(symbol) in approved else licensing.FALSE
        for symbol in parsed.get_symbols()
    }
    return parsed.subs(values).simplify() == licensing.TRUE


def load_policy(content: str, approved_override: list[str] | None = None) -> dict:
    policy = tomllib.loads(content)
    if approved_override is not None:
        policy["approved"] = approved_override
    approved = policy["approved"]
    if not approved or any(
        not allowed_expression(value, [value]) for value in approved
    ):
        raise ValueError(
            "approved must contain explicit SPDX licenses or WITH combinations"
        )
    seen = set()
    for entry in policy.get("clarification", []):
        key = Dependency(
            *(entry[field] for field in ("ecosystem", "name", "version", "source"))
        )
        if key in seen or not all((key.name, key.version, key.source)):
            raise ValueError(
                "clarifications require unique exact package/version/source identities"
            )
        if not entry.get("evidence", "").startswith("https://"):
            raise ValueError("clarifications require an HTTPS evidence URL")
        if not allowed_expression(entry.get("license", ""), approved):
            raise ValueError(
                f"clarification for {key.name} must still satisfy approved licenses"
            )
        seen.add(key)
    return policy


@cache
def fetch_json(url: str) -> dict:
    request = Request(
        url,
        headers={
            "User-Agent": "OpenShell-Research-dependency-license-ci",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def package_license(dependency: Dependency) -> str:
    """Read exact-version metadata only from supported public registries."""
    name = quote(dependency.name, safe="")
    version = quote(dependency.version, safe="")
    if not name or not version:
        raise ValueError("missing package name/version")
    if dependency.ecosystem == "pypi":
        source = json.loads(dependency.source)
        if source not in (
            {"registry": "https://pypi.org/simple"},
            {"registry": "https://pypi.org/simple/"},
        ):
            raise ValueError(
                "unsupported Python source; provide an exact-source clarification"
            )
        info = fetch_json(f"https://pypi.org/pypi/{name}/{version}/json")["info"]
        return info.get("license_expression") or info.get("license") or ""
    if dependency.ecosystem == "npm":
        parsed = urlparse(dependency.source)
        if parsed.scheme != "https" or parsed.netloc != "registry.npmjs.org":
            raise ValueError(
                "unsupported npm source; provide an exact-source clarification"
            )
        info = fetch_json(f"https://registry.npmjs.org/{name}/{version}")
        if info.get("dist", {}).get("tarball") != dependency.source:
            raise ValueError("registry tarball does not match locked source")
        return info.get("license") or ""
    if dependency.source not in (
        "registry+https://github.com/rust-lang/crates.io-index",
        "sparse+https://index.crates.io/",
    ):
        raise ValueError(
            "unsupported Cargo source; provide an exact-source clarification"
        )
    return (
        fetch_json(f"https://crates.io/api/v1/crates/{name}/{version}")["version"].get(
            "license"
        )
        or ""
    )


def check_dependency(dependency: Dependency, policy: dict) -> dict:
    expression = ""
    evidence = ""
    try:
        for entry in policy.get("clarification", []):
            if all(
                entry[key] == getattr(dependency, key)
                for key in ("ecosystem", "name", "version", "source")
            ):
                expression, evidence = entry["license"], entry["evidence"]
                break
        else:
            expression = package_license(dependency)
        passed = allowed_expression(expression, policy["approved"])
        reason = (
            "approved"
            if passed
            else "missing, ambiguous, malformed, or unapproved license"
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        passed, reason = False, f"metadata unresolved: {error}"
    return {
        **dependency.__dict__,
        "license": expression,
        "evidence": evidence,
        "passed": passed,
        "reason": reason,
    }


def git_text(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True)


def revision_files(root: Path, revision: str) -> dict[str, str]:
    paths = git_text(root, "ls-tree", "-r", "--name-only", "-z", revision).split("\0")
    return {
        path: git_text(root, "show", f"{revision}:{path}")
        for path in paths
        if Path(path).name in LOCK_NAMES or path.endswith(".py.lock")
    }


def manifest_dependencies(name: str, data: dict) -> list:
    if name == "pyproject.toml":
        project = data.get("project", {})
        return [
            project.get("dependencies"),
            project.get("optional-dependencies"),
            data.get("dependency-groups"),
            data.get("build-system", {}).get("requires"),
            data.get("tool", {}).get("uv"),
        ]
    if name == "Cargo.toml":
        return [
            data.get(key)
            for key in (
                "dependencies",
                "dev-dependencies",
                "build-dependencies",
                "target",
            )
        ] + [data.get("workspace")]
    return [
        data.get(key)
        for key in (
            "dependencies",
            "devDependencies",
            "optionalDependencies",
            "peerDependencies",
            "workspaces",
        )
    ]


def script_metadata(content: str) -> dict | None:
    """Parse a PEP 723 script metadata block, if present."""
    lines = content.splitlines()
    for start, line in enumerate(lines):
        if line != "# /// script":
            continue
        metadata_lines = []
        for line in lines[start + 1 :]:
            if line == "# ///":
                return tomllib.loads(
                    "\n".join(
                        line[2:] if line.startswith("# ") else ""
                        for line in metadata_lines
                    )
                )
            if line != "#" and not line.startswith("# "):
                raise ValueError("invalid PEP 723 script metadata block")
            metadata_lines.append(line)
        raise ValueError("unterminated PEP 723 script metadata block")
    return None


def check_manifest_coverage(
    root: Path, base: str | None, head: str, changed: list[str]
) -> list[dict[str, str]]:
    """Reject missing inventories and identify native lock-freshness checks.

    CI runs these package managers directly; this function does not resolve packages.
    Workspace members may use their declared workspace's root lockfile.
    """
    from fnmatch import fnmatch

    paths = set(git_text(root, "ls-tree", "-r", "--name-only", "-z", head).split("\0"))
    base_paths = (
        set(git_text(root, "ls-tree", "-r", "--name-only", "-z", base).split("\0"))
        if base
        else set()
    )
    candidates = paths if base is None else set(changed)
    targets: set[tuple[str, str, str]] = set()
    for path in changed:
        if path.endswith(".py.lock"):
            script_path = path.removesuffix(".lock")
            if script_path not in paths:
                raise ValueError(f"{path}: uv script lockfile has no matching script")
            candidates.add(script_path)
        for manifest_name, lock_name in MANIFEST_LOCKS.items():
            if Path(path).name == lock_name:
                candidates.add((Path(path).parent / manifest_name).as_posix())
    for path in sorted(candidates & paths):
        manifest = Path(path)
        if manifest.suffix == ".py":
            metadata = script_metadata(git_text(root, "show", f"{head}:{path}"))
            previous_metadata = (
                script_metadata(git_text(root, "show", f"{base}:{path}"))
                if path in base_paths
                else None
            )
            lock_path = f"{path}.lock"
            has_dependencies = bool(
                (metadata or {}).get("dependencies")
                or (previous_metadata or {}).get("dependencies")
            )
            if not has_dependencies:
                continue
            if (
                path in base_paths
                and lock_path not in changed
                and previous_metadata == metadata
            ):
                continue
            if lock_path not in paths:
                raise ValueError(
                    f"{path}: dependencies have no {manifest.name}.lock inventory; commit the uv script lockfile"
                )
            targets.add((manifest.parent.as_posix(), "uv-script", manifest.name))
            continue
        if manifest.name not in MANIFEST_LOCKS:
            continue
        content = git_text(root, "show", f"{head}:{path}")
        parse = json.loads if manifest.name == "package.json" else tomllib.loads
        dependencies = manifest_dependencies(manifest.name, parse(content))
        lock_name = MANIFEST_LOCKS[manifest.name]
        direct_lock = (manifest.parent / lock_name).as_posix()
        previous_dependencies = (
            manifest_dependencies(
                manifest.name, parse(git_text(root, "show", f"{base}:{path}"))
            )
            if path in base_paths
            else []
        )
        if (
            not any(dependencies)
            and not any(previous_dependencies)
            and direct_lock not in changed
        ):
            continue
        if (
            path in base_paths
            and direct_lock not in changed
            and previous_dependencies == dependencies
        ):
            continue
        if direct_lock in paths:
            targets.add(
                (manifest.parent.as_posix(), MANIFEST_MANAGERS[manifest.name], "")
            )
            continue
        covered = False
        for parent in manifest.parent.parents:
            workspace_path = (parent / manifest.name).as_posix()
            if (
                workspace_path not in paths
                or (parent / lock_name).as_posix() not in paths
            ):
                continue
            workspace_content = git_text(root, "show", f"{head}:{workspace_path}")
            workspace = (
                json.loads(workspace_content)
                if manifest.name == "package.json"
                else tomllib.loads(workspace_content)
            )
            if manifest.name == "package.json":
                members = workspace.get("workspaces", [])
            elif manifest.name == "pyproject.toml":
                members = (
                    workspace.get("tool", {})
                    .get("uv", {})
                    .get("workspace", {})
                    .get("members", [])
                )
            else:
                members = workspace.get("workspace", {}).get("members", [])
            relative = manifest.parent.relative_to(parent).as_posix()
            if any(fnmatch(relative, member) for member in members):
                targets.add((parent.as_posix(), MANIFEST_MANAGERS[manifest.name], ""))
                covered = True
                break
        if not covered:
            raise ValueError(
                f"{path}: dependencies have no {lock_name} inventory; commit a lockfile or declare membership in a locked workspace"
            )
    return [
        {
            "directory": directory,
            "manager": manager,
            **({"script": script} if script else {}),
        }
        for directory, manager, script in sorted(targets)
    ]


def select_dependencies(
    before: dict[str, str], after: dict[str, str], full: bool = False
) -> dict[Dependency, list[str]]:
    selected: dict[Dependency, list[str]] = {}
    for path, content in after.items():
        previous = (
            inventory(path, before[path]) if path in before and not full else set()
        )
        for dependency in inventory(path, content) - previous:
            selected.setdefault(dependency, []).append(path)
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base", help="Compare with this Git revision; omit for a full audit"
    )
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--policy", type=Path, default=Path(POLICY_PATH))
    parser.add_argument(
        "--approved-policy",
        type=Path,
        help="Base-branch policy providing the approved list; head clarifications cannot expand it",
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        approved = (
            load_policy(args.approved_policy.read_text())["approved"]
            if args.approved_policy
            else None
        )
        policy = load_policy(args.policy.read_text(), approved_override=approved)
        before = revision_files(args.repo, args.base) if args.base else {}
        after = revision_files(args.repo, args.head)
        changed = (
            git_text(
                args.repo, "diff", "--name-only", args.base, args.head
            ).splitlines()
            if args.base
            else []
        )
        policy_existed = bool(
            args.base
            and POLICY_PATH in changed
            and git_text(
                args.repo, "ls-tree", "--name-only", args.base, "--", POLICY_PATH
            ).strip()
        )
        full = not args.base or policy_existed
        lock_checks = check_manifest_coverage(args.repo, args.base, args.head, changed)
        selected = select_dependencies(before, after, full=full)
        results = []
        for dependency in sorted(selected):
            result = check_dependency(dependency, policy)
            result["lockfiles"] = selected[dependency]
            results.append(result)
            license_label = str(result["license"] or "unknown").replace("\n", " ")[:120]
            print(
                f"{'PASS' if result['passed'] else 'FAIL'} {dependency.ecosystem}:{dependency.name}@{dependency.version}: {license_label} ({result['reason']})"
            )
        report = {
            "base": args.base,
            "head": args.head,
            "full_audit": full,
            "checked": len(results),
            "failed": sum(not result["passed"] for result in results),
            "results": results,
            "lock_checks": lock_checks,
        }
        if args.report:
            args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(
            f"Checked {report['checked']} added/changed dependencies; {report['failed']} require resolution."
        )
        return 1 if report["failed"] else 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"Dependency license check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
