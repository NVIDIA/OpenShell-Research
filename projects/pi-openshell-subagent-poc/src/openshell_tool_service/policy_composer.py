"""Read-only native provider composition before child creation."""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
import time
from pathlib import Path

from openshell_tool_service.config import Settings
from openshell_tool_service.policy_reviewer import PolicyReviewError, PolicyReviewRequest
from openshell_tool_service.runtime import CommandRunner, _default_runner, _scope_args
from openshell_tool_service.store import Job

logger = logging.getLogger(__name__)


class OpenShellPolicyComposer:
    def __init__(self, settings: Settings, runner: CommandRunner = _default_runner) -> None:
        self.settings = settings
        self.runner = runner

    def prepare(self, job: Job) -> PolicyReviewRequest:
        started = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="openshell-compose-") as directory:
                path = Path(directory) / "child.yaml"
                path.write_text(job.child_policy, encoding="utf-8")
                path.chmod(0o600)
                result = self.runner(
                    [self.settings.policy_composer_bin, "--parent", job.caller_id,
                     "--provider", self.settings.child_provider or "", "--policy", str(path),
                     *_scope_args(self.settings)],
                    None,
                    self.settings.create_timeout_seconds,
                )
            if result.returncode:
                raise ValueError(f"native composer failed: {result.stderr.strip()[:2048]}")
            if len(result.stdout.encode()) > 4 * 1024 * 1024:
                raise ValueError("composer output exceeds limit")
            data = json.loads(result.stdout)
            if (data.get("schema_version") != 1
                    or data.get("provider") != self.settings.child_provider
                    or not isinstance(data.get("parent_policy"), dict)
                    or not isinstance(data.get("child_policy"), dict)):
                raise ValueError("invalid native composition response")
            logger.debug(
                "job %s provider %s is attached to parent %s",
                job.id[:8], self.settings.child_provider, job.caller_id,
            )
            count = data.get("provider_rule_count")
            description = (
                "provider adds no network rules; child policy is unchanged"
                if count == 0 else
                f"provider contributes {count} network rule(s) to the effective child policy"
            )
            logger.debug(
                "job %s %s (%dms)", job.id[:8], description,
                round((time.monotonic() - started) * 1000),
            )
            return PolicyReviewRequest(
                parent_policy=json.dumps(data["parent_policy"], sort_keys=True),
                child_policy=json.dumps(data["child_policy"], sort_keys=True),
                task=job.prompt,
            )
        except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, AttributeError) as error:
            raise PolicyReviewError(
                f"Effective policy composition failed; no child was created: {error}",
                code="policy-composition-unavailable",
            ) from error
