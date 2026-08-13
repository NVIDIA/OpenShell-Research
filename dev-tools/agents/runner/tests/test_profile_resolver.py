"""Tests for the repository-agent profile resolver and launcher."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

RUNNER_ROOT = Path(__file__).resolve().parents[1]
AGENTS_ROOT = RUNNER_ROOT.parent
PROFILE_RESOLVER = [
    "uv",
    "run",
    "--locked",
    "--script",
    str(RUNNER_ROOT / "profile_resolver.py"),
]


def editorial_response() -> dict[str, object]:
    criteria = [
        "formulaic_language",
        "empty_emphasis",
        "repetitive_cadence",
        "unnecessary_summary",
        "inflated_claims",
        "vague_attribution",
        "directness",
    ]
    return {
        "schema_version": 1,
        "judge_id": "editorial",
        "rubric_revision": "editorial-v1",
        "model_id": "vendor/model",
        "analyzed_head_sha": "a" * 40,
        "source_content_digest": "b" * 64,
        "rubric_subscores": [
            {"criterion": criterion, "score": 4, "explanation": "Clear."}
            for criterion in criteria
        ],
        "overall_score": 100,
        "verdict": "pass",
        "confidence": "high",
        "findings": [],
        "overall_assessment": "Ready.",
    }


class RepositoryAgentTests(unittest.TestCase):
    def test_prepare_generates_bounded_prompt_and_pi_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            destination = Path(raw) / "stage"
            subprocess.run(
                [
                    *PROFILE_RESOLVER,
                    "prepare",
                    "--runner-root",
                    str(RUNNER_ROOT),
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

    def test_prepare_rejects_unknown_keys(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            copied = Path(raw) / "agents"
            shutil.copytree(
                AGENTS_ROOT,
                copied,
                ignore=shutil.ignore_patterns(".venv", "__pycache__"),
            )
            manifest = copied / "profiles/dev-note-reviewer/profile.yaml"
            manifest.write_text(manifest.read_text() + "unexpected: true\n")
            result = subprocess.run(
                [
                    *PROFILE_RESOLVER,
                    "prepare",
                    "--runner-root",
                    str(copied / "runner"),
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

    def test_prepare_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            copied = Path(raw) / "agents"
            shutil.copytree(AGENTS_ROOT, copied)
            manifest = copied / "profiles/dev-note-reviewer/profile.yaml"
            manifest.write_text(
                manifest.read_text().replace(
                    "prompt: prompts/editorial.md", "prompt: ../../README.md"
                )
            )
            result = subprocess.run(
                [
                    *PROFILE_RESOLVER,
                    "prepare",
                    "--runner-root",
                    str(copied / "runner"),
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
            self.assertIn(b"task.prompt escapes its profile", result.stderr)

    def test_prepare_includes_profile_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            copied = Path(raw) / "agents"
            shutil.copytree(AGENTS_ROOT, copied)
            profile_root = copied / "profiles/dev-note-reviewer"
            (profile_root / "guidance.md").write_text("Repository guidance")
            manifest = profile_root / "profile.yaml"
            manifest.write_text(
                manifest.read_text().replace(
                    "    input_label: Untrusted projected prose data",
                    "    input_label: Untrusted projected prose data\n"
                    "    guidance: [guidance.md]",
                    1,
                )
            )
            destination = Path(raw) / "stage"
            subprocess.run(
                [
                    *PROFILE_RESOLVER,
                    "prepare",
                    "--runner-root",
                    str(copied / "runner"),
                    "--profile",
                    "dev-note-reviewer",
                    "--task",
                    "editorial",
                    "--destination",
                    str(destination),
                    "--model-id",
                    "model",
                ],
                input=b"",
                check=True,
            )
            self.assertIn(
                "Repository guidance",
                (destination / "workspace/prompt.md").read_text(),
            )

    def test_prepare_preserves_response_schema_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            copied = Path(raw) / "agents"
            shutil.copytree(AGENTS_ROOT, copied)
            profile_root = copied / "profiles/dev-note-reviewer"
            schema = profile_root / "simple-response.schema.json"
            schema.write_text(
                json.dumps(
                    {
                        "$schema": "https://json-schema.org/draft/2020-12/schema",
                        "type": "object",
                        "allOf": [{"required": ["verdict"]}],
                        "properties": {
                            "model_id": {"type": "string"},
                            "verdict": {"const": "pass"},
                        },
                    }
                )
            )
            manifest = profile_root / "profile.yaml"
            manifest.write_text(
                manifest.read_text()
                .replace(
                    "output_schema: review-response.schema.json",
                    "output_schema: simple-response.schema.json",
                    1,
                )
                .replace("    output_schema_definition: editorial\n", "", 1)
            )
            destination = Path(raw) / "stage"
            subprocess.run(
                [
                    *PROFILE_RESOLVER,
                    "prepare",
                    "--runner-root",
                    str(copied / "runner"),
                    "--profile",
                    "dev-note-reviewer",
                    "--task",
                    "editorial",
                    "--destination",
                    str(destination),
                    "--model-id",
                    "model",
                ],
                input=b"",
                check=True,
            )
            staged_schema = json.loads(
                (destination / "response.schema.json").read_text()
            )
            self.assertEqual(len(staged_schema["allOf"]), 2)

    def test_public_wrapper_prepares_complete_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            destination = Path(raw) / "prepared"
            subprocess.run(
                [
                    str(RUNNER_ROOT / "run.sh"),
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
            response_fixture = temporary / "response.json"
            response_fixture.write_text(json.dumps(editorial_response()))
            executable.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$AGENT_TEST_LOG"
if [[ "${1:-}" == provider && "${2:-}" == get ]]; then exit 1; fi
if [[ "${1:-}" == sandbox && "${2:-}" == create ]]; then
  cat "$AGENT_TEST_RESPONSE"
fi
"""
            )
            executable.chmod(0o755)
            output = temporary / "result.json"
            environment = os.environ.copy()
            environment.update(
                {
                    "AGENT_TEST_LOG": str(log),
                    "AGENT_TEST_RESPONSE": str(response_fixture),
                    "INFERENCE_BASE_URL": "https://models.example/v1",
                    "INFERENCE_API_KEY": "secret",
                    "MODEL_ID_TOP": "vendor/model",
                }
            )
            subprocess.run(
                [
                    str(RUNNER_ROOT / "run.sh"),
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
            self.assertEqual(json.loads(output.read_text()), editorial_response())
            commands = log.read_text()
            self.assertIn("--no-auto-providers", commands)
            self.assertIn("/sandbox/task/prompt.md vendor/model --no-tools", commands)
            self.assertIn("--credential INFERENCE_API_KEY", commands)
            self.assertNotIn("secret", commands)

    def test_response_schema_rejects_contract_violations(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            destination = Path(raw) / "stage"
            subprocess.run(
                [
                    *PROFILE_RESOLVER,
                    "prepare",
                    "--runner-root",
                    str(RUNNER_ROOT),
                    "--profile",
                    "dev-note-reviewer",
                    "--task",
                    "editorial",
                    "--destination",
                    str(destination),
                    "--model-id",
                    "vendor/model",
                ],
                input=b"candidate",
                check=True,
            )
            response_path = Path(raw) / "response.json"
            duplicate = editorial_response()
            subscores = duplicate["rubric_subscores"]
            assert isinstance(subscores, list)
            subscores[1]["criterion"] = "formulaic_language"
            wrong_model = editorial_response()
            wrong_model["model_id"] = "different/model"
            for response in ({"decision": "pass"}, duplicate, wrong_model):
                response_path.write_text(json.dumps(response))
                result = subprocess.run(
                    [
                        *PROFILE_RESOLVER,
                        "validate-response",
                        str(response_path),
                        str(destination / "response.schema.json"),
                    ],
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn(b"agent response is invalid", result.stderr)


if __name__ == "__main__":
    unittest.main()
