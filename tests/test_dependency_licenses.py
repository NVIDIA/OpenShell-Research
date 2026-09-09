# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

if sys.version_info < (3, 12):
    pytest.skip(
        "Dependency-license tooling requires Python 3.12+", allow_module_level=True
    )

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "dependency_licenses", ROOT / "scripts/check_dependency_licenses.py"
)
assert SPEC and SPEC.loader
checker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)
POLICY = checker.load_policy((ROOT / checker.POLICY_PATH).read_text())
DEPENDENCY = checker.Dependency(
    "pypi", "example", "1.0", '{"registry":"https://pypi.org/simple"}'
)


def uv_lock(version="1.0", source='registry = "https://pypi.org/simple"'):
    return f'''[[package]]
name = "root"
version = "0.1"
source = {{ virtual = "." }}
dependencies = [{{ name = "example" }}]

[[package]]
name = "example"
version = "{version}"
source = {{ {source} }}
'''


def uv_script(*dependencies):
    dependency_list = json.dumps(list(dependencies))
    return f"# /// script\n# dependencies = {dependency_list}\n# ///\n"


def assert_equal(actual, expected):
    assert actual == expected


def assert_false(value):
    assert not value


def assert_true(value):
    assert value


def check_coverage(before, after, base="base"):
    changed = sorted(
        path
        for path in before.keys() | after.keys()
        if before.get(path) != after.get(path)
    )

    def git_text(root, *args):
        if args[0] == "ls-tree":
            files = before if args[-1] == "base" else after
            return "\0".join(files)
        revision, path = args[-1].split(":", 1)
        return (before if revision == "base" else after)[path]

    with mock.patch.object(checker, "git_text", side_effect=git_text):
        return checker.check_manifest_coverage(ROOT, base, "head", changed)


def test_native_check_targets_for_direct_projects():
    for manifest, lock, content, manager in [
        ("pyproject.toml", "uv.lock", '[project]\ndependencies=["example"]', "uv"),
        (
            "package.json",
            "package-lock.json",
            '{"dependencies":{"example":"1"}}',
            "npm",
        ),
        ("Cargo.toml", "Cargo.lock", '[dependencies]\nexample="1"', "cargo"),
    ]:
        targets = check_coverage(
            {}, {f"project/{manifest}": content, f"project/{lock}": "inventory"}
        )
        assert_equal(targets, [{"directory": "project", "manager": manager}])


def test_native_check_targets_for_changed_lock_only_and_no_changes():
    before = {
        "pyproject.toml": '[project]\ndependencies=["example"]',
        "uv.lock": uv_lock(),
    }
    assert_equal(check_coverage(before, before), [])
    assert_equal(
        check_coverage(before, {**before, "uv.lock": uv_lock("2.0")}),
        [{"directory": ".", "manager": "uv"}],
    )


def test_full_audit_validates_locked_projects_not_manifest_templates():
    after = {
        "project/pyproject.toml": '[project]\ndependencies=["example"]',
        "project/uv.lock": uv_lock(),
        "src/templates/pyproject.toml": '[project]\ndependencies=["template"]',
    }
    assert_equal(
        check_coverage({}, after, base=None),
        [{"directory": "project", "manager": "uv"}],
    )
    root = {
        "pyproject.toml": '[tool.uv.workspace]\nmembers=["packages/*"]',
        "uv.lock": uv_lock(),
    }
    assert_equal(
        check_coverage(root, {**root, "uv.lock": uv_lock("2.0")}),
        [{"directory": ".", "manager": "uv"}],
    )


def test_workspace_native_checks_are_deduplicated_at_root():
    after = {
        "pyproject.toml": '[tool.uv.workspace]\nmembers=["packages/*"]',
        "uv.lock": uv_lock(),
        "packages/one/pyproject.toml": '[project]\ndependencies=["example"]',
        "packages/two/pyproject.toml": '[project]\ndependencies=["example"]',
    }
    assert_equal(check_coverage({}, after), [{"directory": ".", "manager": "uv"}])


def test_declared_dependency_edits_emit_native_check_without_forcing_lock_churn():
    before = {
        "Cargo.toml": '[dependencies]\nexample="1"',
        "Cargo.lock": "same resolution",
    }
    after = {**before, "Cargo.toml": '[dependencies]\nexample="^1.0"'}
    assert_equal(
        check_coverage(before, after), [{"directory": ".", "manager": "cargo"}]
    )


def test_uv_source_configuration_changes_emit_native_check():
    before = {
        "pyproject.toml": '[project]\ndependencies=["example"]',
        "uv.lock": uv_lock(),
    }
    after = {
        **before,
        "pyproject.toml": before["pyproject.toml"]
        + '\n[tool.uv.sources]\nexample={git="https://example.org/repo"}',
    }
    assert_equal(check_coverage(before, after), [{"directory": ".", "manager": "uv"}])


def test_added_dependency_manifest_requires_lockfile():
    manifests = {
        "new/pyproject.toml": '[project]\ndependencies=["example"]',
        "new/package.json": '{"dependencies":{"example":"1"}}',
        "new/Cargo.toml": '[dependencies]\nexample="1"',
    }
    for path, content in manifests.items():
        with pytest.raises(ValueError, match="no .* inventory"):
            check_coverage({}, {path: content})


def test_lockfile_or_declared_workspace_covers_manifest():
    check_coverage(
        {},
        {
            "new/pyproject.toml": '[project]\ndependencies=["example"]',
            "new/uv.lock": uv_lock(),
        },
    )
    check_coverage(
        {},
        {
            "pyproject.toml": '[tool.uv.workspace]\nmembers=["packages/*"]',
            "uv.lock": uv_lock(),
            "packages/member/pyproject.toml": '[project]\ndependencies=["example"]',
        },
    )


def test_unrelated_ancestor_lock_does_not_cover_new_manifest():
    with pytest.raises(ValueError):
        check_coverage(
            {},
            {
                "pyproject.toml": '[project]\nname="root"',
                "uv.lock": uv_lock(),
                "nested/pyproject.toml": '[project]\ndependencies=["example"]',
            },
        )


def test_non_dependency_manifest_changes_do_not_require_new_inventory():
    check_coverage(
        {"pyproject.toml": '[project]\nname="old"\ndependencies=["example"]'},
        {"pyproject.toml": '[project]\nname="new"\ndependencies=["example"]'},
    )
    check_coverage({}, {"pyproject.toml": '[project]\nname="stdlib-only"'})


def test_deleting_lockfile_with_remaining_dependencies_fails():
    manifest = '[project]\ndependencies=["example"]'
    with pytest.raises(ValueError):
        check_coverage(
            {"pyproject.toml": manifest, "uv.lock": uv_lock()},
            {"pyproject.toml": manifest},
        )


def test_compatible_cargo_constraint_edit_does_not_require_lockfile_churn():
    check_coverage(
        {
            "Cargo.toml": '[dependencies]\nexample="1"',
            "Cargo.lock": "unchanged resolution",
        },
        {
            "Cargo.toml": '[dependencies]\nexample="^1.0"',
            "Cargo.lock": "unchanged resolution",
        },
    )


def test_uv_includes_all_locked_packages_not_just_active_platform():
    content = uv_lock() + uv_lock("2.0") + uv_lock("0.1", 'editable = "."')
    dependencies = checker.inventory("projects/tool/uv.lock", content)
    assert_equal({item.version for item in dependencies}, {"1.0", "2.0"})


def test_non_root_python_sources_remain_visible():
    for source in (
        'git = "https://example.com/repo#abc"',
        'directory = "../vendored"',
        'editable = "../package"',
    ):
        assert_equal(len(checker.inventory("uv.lock", uv_lock(source=source))), 1)


def test_uv_script_lock_is_inventoried():
    dependencies = checker.inventory("scripts/check.py.lock", uv_lock())
    assert_equal(next(iter(dependencies)).ecosystem, "pypi")


def test_uv_script_dependency_edits_require_and_validate_lockfile():
    before = {
        "scripts/check.py": uv_script("example==1"),
        "scripts/check.py.lock": uv_lock(),
    }
    after = {**before, "scripts/check.py": uv_script("example==2")}
    assert_equal(
        check_coverage(before, after),
        [
            {
                "directory": "scripts",
                "manager": "uv-script",
                "script": "check.py",
            }
        ],
    )
    with pytest.raises(ValueError, match="uv script lockfile"):
        check_coverage({}, {"scripts/check.py": uv_script("example")})


def test_uv_workspace_members_are_not_external_dependencies():
    content = """
[manifest]
members = ["member"]

[[package]]
name = "member"
version = "0.1.0"
source = { editable = "packages/member" }

[[package]]
name = "external"
version = "1.0"
source = { registry = "https://pypi.org/simple" }
"""
    dependencies = checker.inventory("uv.lock", content)
    assert_equal({dependency.name for dependency in dependencies}, {"external"})


def test_npm_includes_nested_optional_and_dev_packages():
    content = json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "root"},
                "node_modules/one": {"version": "1", "dev": True},
                "node_modules/one/node_modules/@scope/two": {
                    "version": "2",
                    "optional": True,
                    "os": ["darwin"],
                },
                "node_modules/alias": {"name": "real-name", "version": "3"},
            },
        }
    )
    assert_equal(
        {item.name for item in checker.inventory("package-lock.json", content)},
        {"one", "@scope/two", "real-name"},
    )


def test_npm_workspace_target_is_not_an_external_dependency():
    content = json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"workspaces": ["packages/*"]},
                "node_modules/member": {
                    "link": True,
                    "resolved": "packages/member",
                },
                "packages/member": {"name": "member", "version": "1"},
            },
        }
    )
    assert_false(checker.inventory("package-lock.json", content))


def test_npm_non_workspace_links_remain_external_dependencies():
    content = json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {
                "": {},
                "node_modules/vendored": {
                    "link": True,
                    "resolved": "vendor/vendored",
                },
                "vendor/vendored": {"name": "vendored", "version": "1"},
            },
        }
    )
    dependencies = checker.inventory("package-lock.json", content)
    assert_equal({dependency.name for dependency in dependencies}, {"vendored"})


def test_unsupported_or_missing_inventory_fails():
    for path, content in (
        ("package-lock.json", '{"lockfileVersion": 1}'),
        ("uv.lock", "version = 1"),
    ):
        with pytest.raises(ValueError):
            checker.inventory(path, content)


def test_cargo_sources_and_versions():
    content = """[[package]]
name = "root"
version = "0.1"
[[package]]
name = "dep"
version = "1"
source = "registry+https://github.com/rust-lang/crates.io-index"
[[package]]
name = "dep"
version = "2"
source = "git+https://example.org/repo#abc"
"""
    assert_equal(
        {item.version for item in checker.inventory("Cargo.lock", content)},
        {"1", "2"},
    )


def test_direct_inventory_excludes_transitive_dependencies():
    uv_content = (
        uv_lock()
        + """[[package]]
name = "transitive"
version = "1"
source = { registry = "https://pypi.org/simple" }
"""
    )
    assert_equal(
        {item.name for item in checker.direct_inventory("uv.lock", uv_content)},
        {"example"},
    )

    npm_content = json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"dependencies": {"direct": "1"}},
                "node_modules/direct": {"version": "1"},
                "node_modules/transitive": {"version": "1"},
            },
        }
    )
    assert_equal(
        {
            item.name
            for item in checker.direct_inventory("package-lock.json", npm_content)
        },
        {"direct"},
    )

    cargo_content = """[[package]]
name = "root"
version = "0.1"
dependencies = ["direct"]
[[package]]
name = "direct"
version = "1"
source = "registry+https://github.com/rust-lang/crates.io-index"
[[package]]
name = "transitive"
version = "1"
source = "registry+https://github.com/rust-lang/crates.io-index"
"""
    assert_equal(
        {item.name for item in checker.direct_inventory("Cargo.lock", cargo_content)},
        {"direct"},
    )


def test_changes_include_version_and_source_but_not_removals():
    path = "project/uv.lock"
    assert_false(checker.select_dependencies({path: uv_lock()}, {path: uv_lock()}))
    assert_false(checker.select_dependencies({path: uv_lock()}, {}))
    assert_equal(
        len(checker.select_dependencies({path: uv_lock()}, {path: uv_lock("2.0")})),
        1,
    )
    assert_equal(
        len(
            checker.select_dependencies(
                {path: uv_lock()},
                {path: uv_lock(source='git = "https://example.org#abc"')},
            )
        ),
        1,
    )


def test_new_project_and_full_audit_check_every_dependency():
    after = {"new/uv.lock": uv_lock()}
    assert_equal(len(checker.select_dependencies({}, after)), 1)
    assert_equal(len(checker.select_dependencies(after, after, full=True)), 1)


def test_same_dependency_reports_each_affected_lockfile():
    selected = checker.select_dependencies(
        {}, {"a/uv.lock": uv_lock(), "b/uv.lock": uv_lock()}
    )
    assert_equal(list(selected.values()), [["a/uv.lock", "b/uv.lock"]])


def test_approved_licenses_and_boolean_expressions():
    for expression in [
        *POLICY["approved"],
        "MIT OR GPL-3.0-only",
        "MIT AND Apache-2.0",
        "GPL-3.0-only OR (MIT AND BSD-3-Clause)",
    ]:
        assert_true(checker.allowed_expression(expression, POLICY["approved"]))


def test_unapproved_unknown_malformed_and_ambiguous():
    for expression in [
        "BSD",
        "",
        "UNKNOWN",
        "MIT AND",
        "MIT AND GPL-3.0-only",
        "LicenseRef-Private",
        "MIT OR nonsense",
        "MIT WITH LLVM-exception",
        None,
        {"type": "MIT"},
    ]:
        assert_false(checker.allowed_expression(expression, POLICY["approved"]))


def test_with_requires_approved_exact_combination():
    expression = "Apache-2.0 WITH LLVM-exception"
    assert_true(checker.allowed_expression(expression, [expression]))
    assert_false(
        checker.allowed_expression(expression, ["Apache-2.0", "LLVM-exception"])
    )


def test_report_summarizes_scanned_targets_and_repository_licenses():
    npm_dependency = checker.Dependency(
        "npm",
        "web-example",
        "2.0",
        "https://registry.npmjs.org/web-example/-/web-example-2.0.tgz",
    )
    dependencies = {
        DEPENDENCY: ["python/uv.lock"],
        npm_dependency: ["web/package-lock.json"],
    }
    checked = {
        DEPENDENCY: {
            **DEPENDENCY.__dict__,
            "license": "MIT OR Apache-2.0",
            "evidence": "",
            "passed": True,
            "reason": "approved",
        },
        npm_dependency: {
            **npm_dependency.__dict__,
            "license": "MIT",
            "evidence": "",
            "passed": False,
            "reason": "not approved",
        },
    }
    lockfiles = {
        "python/uv.lock": uv_lock(),
        "web/package-lock.json": json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {},
                    "node_modules/example": {
                        "version": "1.0",
                        "resolved": "https://registry.npmjs.org/example/-/example-1.0.tgz",
                    },
                },
            }
        ),
    }
    report = checker.report_summary(lockfiles, dependencies, checked, dependencies)
    assert_equal(
        report["folders"],
        {
            "python": {
                "files": ["python/uv.lock"],
                "licenses": ["MIT OR Apache-2.0"],
            },
            "web": {
                "files": ["web/package-lock.json"],
                "licenses": ["MIT"],
            },
        },
    )
    assert_equal(
        report["licenses"],
        {"MIT": ["web"], "MIT OR Apache-2.0": ["python"]},
    )
    assert_equal(report["failures"], {"web": ["MIT"]})


def test_reported_license_is_compact_and_preserves_registry_wording():
    assert_equal(checker.reported_license(None), "unknown")
    assert_equal(checker.reported_license("MIT\nLicense"), "MIT License")
    assert_equal(checker.reported_license("x" * 121), "x" * 119 + "…")


def test_pypi_uses_exact_version_and_prefers_expression():
    with mock.patch.object(
        checker,
        "fetch_json",
        return_value={"info": {"license_expression": "MIT", "license": "BSD"}},
    ) as fetch:
        assert_equal(checker.package_license(DEPENDENCY), "MIT")
    fetch.assert_called_once_with("https://pypi.org/pypi/example/1.0/json")


def test_npm_checks_source_matches_registry_tarball():
    dependency = checker.Dependency(
        "npm",
        "@scope/example",
        "1.0",
        "https://registry.npmjs.org/@scope/example/-/example-1.0.tgz",
    )
    with mock.patch.object(
        checker,
        "fetch_json",
        return_value={"license": "MIT", "dist": {"tarball": dependency.source}},
    ) as fetch:
        assert_equal(checker.package_license(dependency), "MIT")
    fetch.assert_called_once_with("https://registry.npmjs.org/%40scope%2Fexample/1.0")
    with mock.patch.object(
        checker,
        "fetch_json",
        return_value={"license": "MIT", "dist": {"tarball": "different"}},
    ):
        assert_false(checker.check_dependency(dependency, POLICY)["passed"])


def test_cargo_reads_exact_version():
    dependency = checker.Dependency(
        "cargo",
        "example",
        "1.0",
        "registry+https://github.com/rust-lang/crates.io-index",
    )
    with mock.patch.object(
        checker,
        "fetch_json",
        return_value={"version": {"license": "MIT OR Apache-2.0"}},
    ) as fetch:
        assert_true(checker.check_dependency(dependency, POLICY)["passed"])
    fetch.assert_called_once_with("https://crates.io/api/v1/crates/example/1.0")


def test_unknown_sources_never_trigger_arbitrary_network_requests():
    for ecosystem, source in [
        ("pypi", '{"registry":"http://localhost/simple"}'),
        ("npm", "https://registry.npmjs.org.evil.test/pkg.tgz"),
        ("cargo", "git+https://example.org#abc"),
    ]:
        with (
            mock.patch.object(checker, "fetch_json") as fetch,
        ):
            result = checker.check_dependency(
                checker.Dependency(ecosystem, "example", "1.0", source), POLICY
            )
            assert_false(result["passed"])
            fetch.assert_not_called()


def test_metadata_failure_is_not_a_pass():
    for response in ({"info": {}}, {"wrong": {}}, {"info": {"license": "BSD"}}):
        with (
            mock.patch.object(checker, "fetch_json", return_value=response),
        ):
            assert_false(checker.check_dependency(DEPENDENCY, POLICY)["passed"])
    with mock.patch.object(checker, "fetch_json", side_effect=OSError("unavailable")):
        assert_false(checker.check_dependency(DEPENDENCY, POLICY)["passed"])


def test_clarification_requires_exact_identity_and_evidence():
    entry = {
        **DEPENDENCY.__dict__,
        "license": "MIT",
        "evidence": "https://example.org/v1.0/LICENSE",
    }
    policy = {**POLICY, "clarification": [entry]}
    with mock.patch.object(
        checker, "package_license", side_effect=ValueError("unknown")
    ) as fetch:
        assert_true(checker.check_dependency(DEPENDENCY, policy)["passed"])
        fetch.assert_not_called()
        changed = checker.Dependency("pypi", "example", "2.0", DEPENDENCY.source)
        assert_false(checker.check_dependency(changed, policy)["passed"])


def test_policy_does_not_allow_clarifications_to_bypass_allowlist():
    base = (ROOT / checker.POLICY_PATH).read_text()
    entry = '\n[[clarification]]\necosystem="pypi"\nname="example"\nversion="1"\nsource="exact"\nlicense="GPL-3.0-only"\nevidence="https://example.org/LICENSE"\n'
    with pytest.raises(ValueError):
        checker.load_policy(base + entry)
    with pytest.raises(ValueError):
        checker.load_policy(
            base + entry.replace("GPL-3.0-only", "MIT").replace("https://", "http://")
        )


def test_base_approved_list_cannot_be_expanded_by_head():
    policy = checker.load_policy(
        'approved = ["MIT", "GPL-3.0-only"]', approved_override=["MIT"]
    )
    assert_equal(policy["approved"], ["MIT"])


def test_cli_uses_git_revisions_not_uncommitted_files():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)

        def git(*args):
            return subprocess.check_output(
                ["git", "-C", str(root), *args], text=True
            ).strip()

        git("init", "-q")
        git("config", "user.name", "Test")
        git("config", "user.email", "test@example.org")
        (root / "uv.lock").write_text(uv_lock())
        (root / "pyproject.toml").write_text('[project]\ndependencies=["example"]')
        git("add", ".")
        git("commit", "-qm", "base")
        base = git("rev-parse", "HEAD")
        (root / "uv.lock").write_text(uv_lock("2.0"))
        git("add", ".")
        git("commit", "-qm", "head")
        (root / "uv.lock").write_text("uncommitted invalid file")
        report = root / "report.json"
        lock_checks_report = root / "lock-checks.json"
        args = [
            "check",
            "--repo",
            str(root),
            "--base",
            base,
            "--policy",
            str(ROOT / checker.POLICY_PATH),
            "--report",
            str(report),
            "--lock-checks-report",
            str(lock_checks_report),
        ]
        with (
            mock.patch.object(sys, "argv", args),
            mock.patch.object(checker, "package_license", return_value="MIT"),
        ):
            assert_equal(checker.main(), 0)
        report_data = json.loads(report.read_text())
        assert_equal(
            report_data,
            {
                "folders": {".": {"files": ["uv.lock"], "licenses": ["MIT"]}},
                "licenses": {"MIT": ["."]},
                "failures": {},
            },
        )
        assert_equal(
            json.loads(lock_checks_report.read_text()),
            [{"directory": ".", "manager": "uv"}],
        )
        with (
            mock.patch.object(sys, "argv", args),
            mock.patch.object(checker, "package_license", return_value="GPL-3.0-only"),
        ):
            assert_equal(checker.main(), 1)
        failed_report = json.loads(report.read_text())
        assert_equal(failed_report["failures"], {".": ["GPL-3.0-only"]})


def test_first_policy_introduction_is_delta_then_policy_edits_are_full():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)

        def git(*args):
            return subprocess.check_output(
                ["git", "-C", str(root), *args], text=True
            ).strip()

        git("init", "-q")
        git("config", "user.name", "Test")
        git("config", "user.email", "test@example.org")
        (root / "uv.lock").write_text(uv_lock())
        git("add", ".")
        git("commit", "-qm", "base")
        base = git("rev-parse", "HEAD")
        policy_path = root / checker.POLICY_PATH
        policy_path.parent.mkdir()
        policy_path.write_text('approved = ["MIT"]\n')
        git("add", ".")
        git("commit", "-qm", "introduce policy")
        report = root / "report.json"
        args = [
            "check",
            "--repo",
            str(root),
            "--base",
            base,
            "--policy",
            str(policy_path),
            "--report",
            str(report),
        ]
        with (
            mock.patch.object(sys, "argv", args),
            mock.patch.object(
                checker, "package_license", return_value="GPL-3.0-only"
            ) as fetch,
        ):
            assert_equal(checker.main(), 0)
            fetch.assert_called_once()
        report_data = json.loads(report.read_text())
        assert_equal(report_data["failures"], {})
        args[args.index("--base") + 1] = git("rev-parse", "HEAD")
        policy_path.write_text('approved = ["MIT", "Apache-2.0"]\n')
        git("add", str(policy_path))
        git("commit", "-qm", "edit existing policy")
        with (
            mock.patch.object(sys, "argv", args),
            mock.patch.object(checker, "package_license", return_value="MIT") as fetch,
        ):
            assert_equal(checker.main(), 0)
            fetch.assert_called_once()
        assert_equal(json.loads(report.read_text())["failures"], {})
