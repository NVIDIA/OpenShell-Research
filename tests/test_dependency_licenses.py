# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

if sys.version_info < (3, 12):
    raise unittest.SkipTest("Dependency-license tooling requires Python 3.12+")

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "dependency_licenses", ROOT / "scripts/check_dependency_licenses.py"
)
assert SPEC and SPEC.loader
checker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)
HAS_SPDX = importlib.util.find_spec("license_expression") is not None


def uv_lock(version="1.0", source='registry = "https://pypi.org/simple"'):
    return f'[[package]]\nname = "example"\nversion = "{version}"\nsource = {{ {source} }}\n'


class InventoryTests(unittest.TestCase):
    def check_coverage(self, before, after):
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
            return checker.check_manifest_coverage(ROOT, "base", "head", changed)

    def test_native_check_targets_for_direct_projects(self):
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
            with self.subTest(manager=manager):
                targets = self.check_coverage(
                    {}, {f"project/{manifest}": content, f"project/{lock}": "inventory"}
                )
                self.assertEqual(
                    targets, [{"directory": "project", "manager": manager}]
                )

    def test_native_check_targets_for_changed_lock_only_and_no_changes(self):
        before = {
            "pyproject.toml": '[project]\ndependencies=["example"]',
            "uv.lock": uv_lock(),
        }
        self.assertEqual(self.check_coverage(before, before), [])
        self.assertEqual(
            self.check_coverage(before, {**before, "uv.lock": uv_lock("2.0")}),
            [{"directory": ".", "manager": "uv"}],
        )
        root = {
            "pyproject.toml": '[tool.uv.workspace]\nmembers=["packages/*"]',
            "uv.lock": uv_lock(),
        }
        self.assertEqual(
            self.check_coverage(root, {**root, "uv.lock": uv_lock("2.0")}),
            [{"directory": ".", "manager": "uv"}],
        )

    def test_workspace_native_checks_are_deduplicated_at_root(self):
        after = {
            "pyproject.toml": '[tool.uv.workspace]\nmembers=["packages/*"]',
            "uv.lock": uv_lock(),
            "packages/one/pyproject.toml": '[project]\ndependencies=["example"]',
            "packages/two/pyproject.toml": '[project]\ndependencies=["example"]',
        }
        self.assertEqual(
            self.check_coverage({}, after), [{"directory": ".", "manager": "uv"}]
        )

    def test_declared_dependency_edits_emit_native_check_without_forcing_lock_churn(
        self,
    ):
        before = {
            "Cargo.toml": '[dependencies]\nexample="1"',
            "Cargo.lock": "same resolution",
        }
        after = {**before, "Cargo.toml": '[dependencies]\nexample="^1.0"'}
        self.assertEqual(
            self.check_coverage(before, after), [{"directory": ".", "manager": "cargo"}]
        )

    def test_uv_source_configuration_changes_emit_native_check(self):
        before = {
            "pyproject.toml": '[project]\ndependencies=["example"]',
            "uv.lock": uv_lock(),
        }
        after = {
            **before,
            "pyproject.toml": before["pyproject.toml"]
            + '\n[tool.uv.sources]\nexample={git="https://example.org/repo"}',
        }
        self.assertEqual(
            self.check_coverage(before, after), [{"directory": ".", "manager": "uv"}]
        )

    def test_added_dependency_manifest_requires_lockfile(self):
        manifests = {
            "new/pyproject.toml": '[project]\ndependencies=["example"]',
            "new/package.json": '{"dependencies":{"example":"1"}}',
            "new/Cargo.toml": '[dependencies]\nexample="1"',
        }
        for path, content in manifests.items():
            with (
                self.subTest(path=path),
                self.assertRaisesRegex(ValueError, "no .* inventory"),
            ):
                self.check_coverage({}, {path: content})

    def test_lockfile_or_declared_workspace_covers_manifest(self):
        self.check_coverage(
            {},
            {
                "new/pyproject.toml": '[project]\ndependencies=["example"]',
                "new/uv.lock": uv_lock(),
            },
        )
        self.check_coverage(
            {},
            {
                "pyproject.toml": '[tool.uv.workspace]\nmembers=["packages/*"]',
                "uv.lock": uv_lock(),
                "packages/member/pyproject.toml": '[project]\ndependencies=["example"]',
            },
        )

    def test_unrelated_ancestor_lock_does_not_cover_new_manifest(self):
        with self.assertRaises(ValueError):
            self.check_coverage(
                {},
                {
                    "pyproject.toml": '[project]\nname="root"',
                    "uv.lock": uv_lock(),
                    "nested/pyproject.toml": '[project]\ndependencies=["example"]',
                },
            )

    def test_non_dependency_manifest_changes_do_not_require_new_inventory(self):
        self.check_coverage(
            {"pyproject.toml": '[project]\nname="old"\ndependencies=["example"]'},
            {"pyproject.toml": '[project]\nname="new"\ndependencies=["example"]'},
        )
        self.check_coverage({}, {"pyproject.toml": '[project]\nname="stdlib-only"'})

    def test_deleting_lockfile_with_remaining_dependencies_fails(self):
        manifest = '[project]\ndependencies=["example"]'
        with self.assertRaises(ValueError):
            self.check_coverage(
                {"pyproject.toml": manifest, "uv.lock": uv_lock()},
                {"pyproject.toml": manifest},
            )

    def test_compatible_cargo_constraint_edit_does_not_require_lockfile_churn(self):
        self.check_coverage(
            {
                "Cargo.toml": '[dependencies]\nexample="1"',
                "Cargo.lock": "unchanged resolution",
            },
            {
                "Cargo.toml": '[dependencies]\nexample="^1.0"',
                "Cargo.lock": "unchanged resolution",
            },
        )

    def test_uv_includes_all_locked_packages_not_just_active_platform(self):
        content = uv_lock() + uv_lock("2.0") + uv_lock("0.1", 'editable = "."')
        dependencies = checker.inventory("projects/tool/uv.lock", content)
        self.assertEqual({item.version for item in dependencies}, {"1.0", "2.0"})

    def test_non_root_python_sources_remain_visible(self):
        for source in (
            'git = "https://example.com/repo#abc"',
            'directory = "../vendored"',
            'editable = "../package"',
        ):
            with self.subTest(source=source):
                self.assertEqual(
                    len(checker.inventory("uv.lock", uv_lock(source=source))), 1
                )

    def test_uv_script_lock_is_inventoried(self):
        dependencies = checker.inventory("scripts/check.py.lock", uv_lock())
        self.assertEqual(next(iter(dependencies)).ecosystem, "pypi")

    def test_npm_includes_nested_optional_and_dev_packages(self):
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
        self.assertEqual(
            {item.name for item in checker.inventory("package-lock.json", content)},
            {"one", "@scope/two", "real-name"},
        )

    def test_npm_workspace_target_not_silently_dropped(self):
        content = json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {},
                    "node_modules/member": {
                        "link": True,
                        "resolved": "packages/member",
                    },
                    "packages/member": {"name": "member", "version": "1"},
                },
            }
        )
        self.assertEqual(len(checker.inventory("package-lock.json", content)), 1)

    def test_unsupported_or_missing_inventory_fails(self):
        for path, content in (
            ("package-lock.json", '{"lockfileVersion": 1}'),
            ("uv.lock", "version = 1"),
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                checker.inventory(path, content)

    def test_cargo_sources_and_versions(self):
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
        self.assertEqual(
            {item.version for item in checker.inventory("Cargo.lock", content)},
            {"1", "2"},
        )

    def test_changes_include_version_and_source_but_not_removals(self):
        path = "project/uv.lock"
        self.assertFalse(
            checker.select_dependencies({path: uv_lock()}, {path: uv_lock()})
        )
        self.assertFalse(checker.select_dependencies({path: uv_lock()}, {}))
        self.assertEqual(
            len(checker.select_dependencies({path: uv_lock()}, {path: uv_lock("2.0")})),
            1,
        )
        self.assertEqual(
            len(
                checker.select_dependencies(
                    {path: uv_lock()},
                    {path: uv_lock(source='git = "https://example.org#abc"')},
                )
            ),
            1,
        )

    def test_new_project_and_full_audit_check_every_dependency(self):
        after = {"new/uv.lock": uv_lock()}
        self.assertEqual(len(checker.select_dependencies({}, after)), 1)
        self.assertEqual(len(checker.select_dependencies(after, after, full=True)), 1)

    def test_same_dependency_reports_each_affected_lockfile(self):
        selected = checker.select_dependencies(
            {}, {"a/uv.lock": uv_lock(), "b/uv.lock": uv_lock()}
        )
        self.assertEqual(list(selected.values()), [["a/uv.lock", "b/uv.lock"]])


@unittest.skipUnless(
    HAS_SPDX,
    "SPDX tests run in the dependency-license workflow's pinned uv environment",
)
class LicenseTests(unittest.TestCase):
    def setUp(self):
        self.policy = checker.load_policy((ROOT / checker.POLICY_PATH).read_text())
        self.dependency = checker.Dependency(
            "pypi", "example", "1.0", '{"registry":"https://pypi.org/simple"}'
        )

    def test_approved_licenses_and_boolean_expressions(self):
        for expression in [
            *self.policy["approved"],
            "MIT OR GPL-3.0-only",
            "MIT AND Apache-2.0",
            "GPL-3.0-only OR (MIT AND BSD-3-Clause)",
        ]:
            with self.subTest(expression=expression):
                self.assertTrue(
                    checker.allowed_expression(expression, self.policy["approved"])
                )

    def test_unapproved_unknown_malformed_and_ambiguous(self):
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
            with self.subTest(expression=expression):
                self.assertFalse(
                    checker.allowed_expression(expression, self.policy["approved"])
                )

    def test_with_requires_approved_exact_combination(self):
        expression = "Apache-2.0 WITH LLVM-exception"
        self.assertTrue(checker.allowed_expression(expression, [expression]))
        self.assertFalse(
            checker.allowed_expression(expression, ["Apache-2.0", "LLVM-exception"])
        )

    def test_pypi_uses_exact_version_and_prefers_expression(self):
        with mock.patch.object(
            checker,
            "fetch_json",
            return_value={"info": {"license_expression": "MIT", "license": "BSD"}},
        ) as fetch:
            self.assertEqual(checker.package_license(self.dependency), "MIT")
        fetch.assert_called_once_with("https://pypi.org/pypi/example/1.0/json")

    def test_npm_checks_source_matches_registry_tarball(self):
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
            self.assertEqual(checker.package_license(dependency), "MIT")
        fetch.assert_called_once_with(
            "https://registry.npmjs.org/%40scope%2Fexample/1.0"
        )
        with mock.patch.object(
            checker,
            "fetch_json",
            return_value={"license": "MIT", "dist": {"tarball": "different"}},
        ):
            self.assertFalse(
                checker.check_dependency(dependency, self.policy)["passed"]
            )

    def test_cargo_reads_exact_version(self):
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
            self.assertTrue(checker.check_dependency(dependency, self.policy)["passed"])
        fetch.assert_called_once_with("https://crates.io/api/v1/crates/example/1.0")

    def test_unknown_sources_never_trigger_arbitrary_network_requests(self):
        for ecosystem, source in [
            ("pypi", '{"registry":"http://localhost/simple"}'),
            ("npm", "https://registry.npmjs.org.evil.test/pkg.tgz"),
            ("cargo", "git+https://example.org#abc"),
        ]:
            with (
                self.subTest(ecosystem=ecosystem),
                mock.patch.object(checker, "fetch_json") as fetch,
            ):
                result = checker.check_dependency(
                    checker.Dependency(ecosystem, "example", "1.0", source), self.policy
                )
                self.assertFalse(result["passed"])
                fetch.assert_not_called()

    def test_metadata_failure_is_not_a_pass(self):
        for response in ({"info": {}}, {"wrong": {}}, {"info": {"license": "BSD"}}):
            with (
                self.subTest(response=response),
                mock.patch.object(checker, "fetch_json", return_value=response),
            ):
                self.assertFalse(
                    checker.check_dependency(self.dependency, self.policy)["passed"]
                )
        with mock.patch.object(
            checker, "fetch_json", side_effect=OSError("unavailable")
        ):
            self.assertFalse(
                checker.check_dependency(self.dependency, self.policy)["passed"]
            )

    def test_clarification_requires_exact_identity_and_evidence(self):
        entry = {
            **self.dependency.__dict__,
            "license": "MIT",
            "evidence": "https://example.org/v1.0/LICENSE",
        }
        policy = {**self.policy, "clarification": [entry]}
        with mock.patch.object(
            checker, "package_license", side_effect=ValueError("unknown")
        ) as fetch:
            self.assertTrue(checker.check_dependency(self.dependency, policy)["passed"])
            fetch.assert_not_called()
            changed = checker.Dependency(
                "pypi", "example", "2.0", self.dependency.source
            )
            self.assertFalse(checker.check_dependency(changed, policy)["passed"])

    def test_policy_does_not_allow_clarifications_to_bypass_allowlist(self):
        base = (ROOT / checker.POLICY_PATH).read_text()
        entry = '\n[[clarification]]\necosystem="pypi"\nname="example"\nversion="1"\nsource="exact"\nlicense="GPL-3.0-only"\nevidence="https://example.org/LICENSE"\n'
        with self.assertRaises(ValueError):
            checker.load_policy(base + entry)
        with self.assertRaises(ValueError):
            checker.load_policy(
                base
                + entry.replace("GPL-3.0-only", "MIT").replace("https://", "http://")
            )

    def test_base_approved_list_cannot_be_expanded_by_head(self):
        policy = checker.load_policy(
            'approved = ["MIT", "GPL-3.0-only"]', approved_override=["MIT"]
        )
        self.assertEqual(policy["approved"], ["MIT"])

    def test_cli_uses_git_revisions_not_uncommitted_files(self):
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
            ]
            with (
                mock.patch.object(sys, "argv", args),
                mock.patch.object(checker, "package_license", return_value="MIT"),
            ):
                self.assertEqual(checker.main(), 0)
            self.assertEqual(
                json.loads(report.read_text())["results"][0]["version"], "2.0"
            )
            with (
                mock.patch.object(sys, "argv", args),
                mock.patch.object(
                    checker, "package_license", return_value="GPL-3.0-only"
                ),
            ):
                self.assertEqual(checker.main(), 1)
            self.assertEqual(
                json.loads(report.read_text())["lock_checks"],
                [{"directory": ".", "manager": "uv"}],
            )

    def test_first_policy_introduction_is_delta_then_policy_edits_are_full(self):
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
                    checker, "package_license", return_value="MIT"
                ) as fetch,
            ):
                self.assertEqual(checker.main(), 0)
                fetch.assert_not_called()
            self.assertEqual(json.loads(report.read_text())["checked"], 0)
            self.assertFalse(json.loads(report.read_text())["full_audit"])
            args[args.index("--base") + 1] = git("rev-parse", "HEAD")
            policy_path.write_text('approved = ["MIT", "Apache-2.0"]\n')
            git("add", str(policy_path))
            git("commit", "-qm", "edit existing policy")
            with (
                mock.patch.object(sys, "argv", args),
                mock.patch.object(
                    checker, "package_license", return_value="MIT"
                ) as fetch,
            ):
                self.assertEqual(checker.main(), 0)
                fetch.assert_called_once()
            self.assertTrue(json.loads(report.read_text())["full_audit"])


if __name__ == "__main__":
    unittest.main()
