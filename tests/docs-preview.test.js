// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

for (const workflow of ["docs-preview.yml", "docs-preview-deploy.yml"]) {
  test(`${workflow} recognizes canonical documentation inputs`, () => {
    const source = fs.readFileSync(
      path.join(__dirname, "..", ".github", "workflows", workflow), "utf8",
    );
    // Exercise the actual workflow predicate, including the trusted deploy check.
    const predicate = source.match(
      /const exactInputs = new Set\([\s\S]*?const docsChanged = files\.some\([\s\S]*?\n\s*\);/,
    );
    assert.ok(predicate, "documentation input predicate is present");
    const changed = (filenames) => vm.runInNewContext(`${predicate[0]}\ndocsChanged`, {
      files: filenames.map((filename) => ({ filename })),
    });
    for (const filename of [
      "projects/research/pi-admission/docs/index.md",
      "projects/tools/openshell-agent-runner/docs/index.md",
      "projects/tools/openshell-agent-runner/assets/logo.svg",
      "projects/use-case-examples/example/docs/index.md",
      "docs/dev-notes/posts/example.md",
      "overrides/main.html",
      "scripts/stage-project-docs.py",
      "tests/docs-preview.test.js",
      "tests/test_dev_note_headers.py",
      "tests/test_dev_notes_layout.py",
      "tests/test_dev_notes_layout.py.lock",
      "zensical.toml",
    ]) {
      assert.equal(changed([filename]), true, filename);
    }
    assert.equal(changed([]), false);
    assert.equal(changed([
      "README.md", "projects/research/pi-admission/README.md",
      "projects/tools/example/src/main.py", "projects/research/docs/source.py",
    ]), false);
    assert.equal(changed([
      "README.md", "projects/research/pi-admission/docs/index.md",
    ]), true);
  });
}
