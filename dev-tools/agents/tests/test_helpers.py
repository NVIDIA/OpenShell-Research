"""Tests for the standalone repository-agent helper and launcher."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ["uv", "run", "--locked", "--script", str(ROOT / "helpers.py")]


class RepositoryAgentTests(unittest.TestCase):
    def test_prepare_generates_bounded_prompt_and_pi_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            destination = Path(raw) / "stage"
            subprocess.run(
                [
                    *HELPER,
                    "prepare",
                    "--root",
                    str(ROOT),
                    "--profile",
                    "dev-note-reviewer",
                    "--task",
                    "editorial",
                    "--destination",
                    str(destination),
                    "--model-id",
                    "vendor/model-v1",
                ],
                input=b'{"candidate":"untrusted"}',
                check=True,
            )
            models = json.loads(
                (destination / "sandbox/payload/models.json").read_text()
            )
            model = models["providers"]["repository-agent"]["models"][0]
            self.assertEqual(model["contextWindow"], 1_000_000)
            self.assertEqual(model["maxTokens"], 128_000)
            prompt = (destination / "workspace/prompt.md").read_text()
            self.assertIn('{"candidate":"untrusted"}', prompt)
            self.assertLess(
                prompt.index("Trusted response schema"), prompt.index("Untrusted")
            )

    def test_prepare_rejects_unknown_keys_and_path_escapes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            copied = Path(raw) / "agents"
            shutil.copytree(
                ROOT, copied, ignore=shutil.ignore_patterns(".venv", "__pycache__")
            )
            manifest = copied / "profiles/dev-note-reviewer/profile.yaml"
            manifest.write_text(manifest.read_text() + "unexpected: true\n")
            result = subprocess.run(
                [
                    *HELPER,
                    "prepare",
                    "--root",
                    str(copied),
                    "--profile",
                    "dev-note-reviewer",
                    "--task",
                    "editorial",
                    "--destination",
                    str(Path(raw) / "stage"),
                    "--model-id",
                    "model",
                ],
                input=b"",
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn(b"profile is invalid", result.stderr)

    def test_public_wrapper_prepares_complete_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            destination = Path(raw) / "prepared"
            subprocess.run(
                [
                    str(ROOT / "run.sh"),
                    "--profile",
                    "dev-note-reviewer",
                    "--task",
                    "technical",
                    "--prepare-only",
                    str(destination),
                ],
                input=b"candidate",
                check=True,
            )
            self.assertTrue((destination / "sandbox/Dockerfile").is_file())
            self.assertTrue((destination / "workspace/prompt.md").is_file())

    def test_existing_gateway_path_keeps_credentials_out_of_sandbox_command(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            temporary = Path(raw)
            log = temporary / "commands.log"
            executable = temporary / "openshell"
            executable.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$AGENT_TEST_LOG"
if [[ "${1:-}" == provider && "${2:-}" == get ]]; then exit 1; fi
if [[ "${1:-}" == sandbox && "${2:-}" == create ]]; then
  printf '{"decision":"pass"}\\n'
fi
"""
            )
            executable.chmod(0o755)
            output = temporary / "result.json"
            environment = os.environ.copy()
            environment.update(
                {
                    "AGENT_TEST_LOG": str(log),
                    "MODEL_BASE_URL": "https://models.example/v1",
                    "MODEL_API_KEY": "secret",
                    "MODEL_ID": "vendor/model",
                }
            )
            subprocess.run(
                [
                    str(ROOT / "run.sh"),
                    "--profile",
                    "dev-note-reviewer",
                    "--task",
                    "editorial",
                    "--gateway-endpoint",
                    "http://gateway",
                    "--openshell-bin",
                    str(executable),
                    "--output",
                    str(output),
                ],
                input=b"candidate",
                env=environment,
                check=True,
            )
            self.assertEqual(json.loads(output.read_text()), {"decision": "pass"})
            commands = log.read_text()
            self.assertIn("--no-auto-providers", commands)
            self.assertIn("/sandbox/task/prompt.md vendor/model --no-tools", commands)
            self.assertNotIn("secret", commands)


if __name__ == "__main__":
    unittest.main()
