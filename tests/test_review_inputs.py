# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise trusted snapshot preparation against real, isolated Git repositories."""

import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1] / ".github/scripts/prepare-review-inputs.sh"
)


class ReviewInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="review-input-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.checkout = self.root / "checkout with spaces"
        self.checkout.mkdir()
        self.git("init", "-q")
        (self.checkout / "README.md").write_text("Original note.\n")
        self.base = self.commit()
        self.output = self.root / "review inputs"

    def git(self, *arguments):
        return subprocess.run(
            ["git", "-C", str(self.checkout), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def commit(self, stage=True):
        if stage:
            self.git("add", ".")
        self.git(
            "-c",
            "user.name=CI Test",
            "-c",
            "user.email=ci@example.invalid",
            "commit",
            "--no-gpg-sign",
            "-qm",
            "Fixture commit",
        )
        return self.git("rev-parse", "HEAD")

    def prepare(self, head):
        return subprocess.run(
            [
                "bash",
                str(SCRIPT),
                str(self.checkout),
                str(self.output),
                self.base,
                head,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_archive_attributes_cannot_omit_or_rewrite_input(self):
        (self.checkout / ".gitattributes").write_text(
            "hidden.md export-ignore\nversion.txt export-subst\nnested export-ignore\n"
        )
        (self.checkout / "hidden.md").write_text("Review this entire file.\n")
        original = "$Format:%H$\n"
        (self.checkout / "version.txt").write_text(original)
        nested = self.checkout / "nested"
        nested.mkdir()
        (nested / ".gitattributes").write_text("* export-ignore export-subst\n")
        (nested / "deep file.txt").write_text(original)
        result = self.prepare(self.commit())
        self.assertEqual(result.returncode, 0, result.stderr)
        source = self.output / "source"
        self.assertEqual(
            (source / "hidden.md").read_text(), "Review this entire file.\n"
        )
        self.assertEqual((source / "version.txt").read_text(), original)
        self.assertEqual((source / "nested/deep file.txt").read_text(), original)
        self.assertFalse((self.checkout / ".git/info/attributes").exists())

    def test_symlinks_do_not_copy_or_dereference_external_files(self):
        external = self.root / "outside"
        external.mkdir()
        (external / "secret.txt").write_text("Must not enter the snapshot.\n")
        (self.checkout / "file link.txt").symlink_to(external / "secret.txt")
        (self.checkout / "directory link").symlink_to(
            external, target_is_directory=True
        )
        result = self.prepare(self.commit())
        self.assertEqual(result.returncode, 0, result.stderr)
        source = self.output / "source"
        self.assertEqual([path.name for path in source.iterdir()], ["README.md"])
        omitted = (self.output / "review-context/omitted-symlinks.txt").read_text()
        self.assertIn("file link.txt ->", omitted)
        self.assertIn("directory link ->", omitted)
        self.assertEqual(
            (external / "secret.txt").read_text(), "Must not enter the snapshot.\n"
        )
        self.assertTrue((self.checkout / "file link.txt").is_symlink())

    def test_diff_matches_exact_commits_and_reports_submodule(self):
        (self.checkout / "README.md").write_text("Revised note.\n")
        (self.checkout / "new document.txt").write_text("New document.\n")
        self.git("add", ".")
        self.git(
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{self.base},external module",
        )
        head = self.commit(stage=False)
        result = self.prepare(head)
        self.assertEqual(result.returncode, 0, result.stderr)
        patch = (self.output / "review-context/changes.patch").read_text().strip()
        self.assertEqual(
            patch,
            self.git("diff", "--no-ext-diff", "--no-textconv", f"{self.base}...{head}"),
        )
        self.assertIn("+Revised note.", patch)
        self.assertEqual(
            (self.output / "source/new document.txt").read_text(), "New document.\n"
        )
        self.assertIn(
            "external module (git submodule",
            (self.output / "review-context/omitted-symlinks.txt").read_text(),
        )

    def test_existing_destination_or_attributes_are_not_modified(self):
        self.output.mkdir()
        preserved = self.output / "keep.txt"
        preserved.write_text("Keep me.\n")
        result = self.prepare(self.base)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(preserved.read_text(), "Keep me.\n")
        self.output = self.root / "another output"
        attributes = self.checkout / ".git/info/attributes"
        attributes.write_text("Existing configuration.\n")
        result = self.prepare(self.base)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(attributes.read_text(), "Existing configuration.\n")
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
