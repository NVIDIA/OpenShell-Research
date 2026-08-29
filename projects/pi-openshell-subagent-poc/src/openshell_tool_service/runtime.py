"""OpenShell CLI-backed child sandbox runtime."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from openshell_tool_service.config import Settings
from openshell_tool_service.store import Job

MAX_OUTPUT_BYTES = 1024 * 1024
logger = logging.getLogger(__name__)


def _byte_length(value: str) -> int:
    return len(value.encode("utf-8"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _elapsed_ms(started_at: float) -> int:
    return round((time.monotonic() - started_at) * 1000)


def _job_ref(job: Job) -> str:
    return job.id[:8]


@dataclass(frozen=True)
class ExecutionResult:
    output: str
    stderr: str
    exit_code: int
    cleanup_error: str | None = None


class RuntimeExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        stderr: str = "",
        exit_code: int | None = None,
        cleanup_error: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.stderr = stderr
        self.exit_code = exit_code
        self.cleanup_error = cleanup_error


class ParentPolicyUnavailableError(RuntimeError):
    """The active parent sandbox policy could not be read from OpenShell."""


CommandRunner = Callable[[Sequence[str], str | None, int], subprocess.CompletedProcess[str]]


def _default_runner(
    command: Sequence[str], input_text: str | None, timeout_seconds: int
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["NO_COLOR"] = "1"
    return subprocess.run(
        command,
        input=input_text,
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout_seconds,
        env=environment,
    )


def _scope_args(settings: Settings) -> list[str]:
    arguments = ["--workspace", settings.workspace]
    if settings.gateway:
        arguments.extend(["--gateway", settings.gateway])
    if settings.gateway_endpoint:
        arguments.extend(["--gateway-endpoint", settings.gateway_endpoint])
    if settings.gateway_insecure:
        arguments.append("--gateway-insecure")
    return arguments


class OpenShellCliParentPolicySource:
    """Read a parent sandbox's active policy from OpenShell."""

    def __init__(self, settings: Settings, runner: CommandRunner = _default_runner) -> None:
        self.settings = settings
        self.runner = runner

    def get(self, sandbox_name: str) -> str:
        command = [
            self.settings.openshell_bin,
            "policy",
            "get",
            sandbox_name,
            *_scope_args(self.settings),
            "--full",
            "--output",
            "json",
        ]
        logger.info(
            "loading active parent policy from OpenShell sandbox %s",
            sandbox_name,
        )
        try:
            completed = self.runner(command, None, self.settings.create_timeout_seconds)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ParentPolicyUnavailableError(
                f"could not retrieve policy for parent sandbox {sandbox_name}"
            ) from error
        if completed.returncode != 0:
            diagnostic = completed.stderr.strip()[:4096]
            message = f"OpenShell could not retrieve policy for {sandbox_name}"
            if diagnostic:
                message = f"{message}: {diagnostic}"
            raise ParentPolicyUnavailableError(message)
        try:
            response = json.loads(completed.stdout)
            policy_value = response["policy"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise ParentPolicyUnavailableError(
                f"OpenShell returned an invalid policy for {sandbox_name}"
            ) from error
        if not isinstance(policy_value, dict) or not policy_value:
            raise ParentPolicyUnavailableError(
                f"OpenShell returned an empty policy for {sandbox_name}"
            )
        policy = json.dumps(policy_value, sort_keys=True, separators=(",", ":"))
        logger.info(
            "loaded active parent policy from %s "
            "(version=%s, config_revision=%s, bytes=%d, sha256=%s)",
            sandbox_name,
            response.get("version", "unknown"),
            response.get("config_revision", "unknown"),
            _byte_length(policy),
            _digest(policy),
        )
        return policy


class OpenShellCliRuntime:
    """Create one child, run Pi once, and always attempt cleanup."""

    def __init__(self, settings: Settings, runner: CommandRunner = _default_runner) -> None:
        self.settings = settings
        self.runner = runner

    def _scope_args(self) -> list[str]:
        return _scope_args(self.settings)

    def _create_command(self, job: Job, policy_path: Path | None) -> list[str]:
        command = [
            self.settings.openshell_bin,
            "sandbox",
            "create",
            *self._scope_args(),
            "--name",
            job.sandbox_name,
            "--from",
            self.settings.child_image,
            "--no-tty",
            "--detach",
            "--label",
            "poc-owner=openshell-tool-service",
            "--label",
            f"poc-job={job.id}",
            "--env",
            "PI_OFFLINE=1",
            "--env",
            "PI_SKIP_VERSION_CHECK=1",
            "--env",
            "PI_TELEMETRY=0",
            "--env",
            "PI_CODING_AGENT_DIR=/home/sandbox/.pi/agent",
        ]
        if self.settings.child_provider:
            command.extend(["--provider", self.settings.child_provider])
        if policy_path:
            command.extend(["--policy", str(policy_path.resolve())])
        if self.settings.child_models_file:
            source = self.settings.child_models_file.resolve()
            command.extend(["--upload", f"{source}:/home/sandbox/.pi/agent"])
        # OpenShell 0.0.116 does not allow --upload together with COMMAND.
        # Detach from the default main process, then run Pi with sandbox exec.
        return command

    def _materialize_policy(self, job: Job) -> tuple[Path | None, Path | None]:
        try:
            policy_directory = self.settings.database_path.parent / "policies"
            policy_directory.mkdir(parents=True, exist_ok=True)
            generated_path = policy_directory / f"{job.id}.yaml"
            generated_path.write_text(f"{job.child_policy.rstrip()}\n", encoding="utf-8")
            generated_path.chmod(0o600)
        except OSError as error:
            raise RuntimeExecutionError(
                "OpenShell Tool Service could not materialize the parent-authored child policy",
                code="policy-materialize",
                stderr=str(error),
            ) from error
        logger.debug(
            "job %s details: policy materialized policy_bytes=%d policy_sha256=%s",
            _job_ref(job),
            _byte_length(job.child_policy),
            _digest(job.child_policy),
        )
        return generated_path, generated_path

    def _exec_command(self, job: Job) -> list[str]:
        return [
            self.settings.openshell_bin,
            "sandbox",
            "exec",
            *self._scope_args(),
            "--name",
            job.sandbox_name,
            "--workdir",
            self.settings.child_workdir,
            "--timeout",
            str(self.settings.job_timeout_seconds),
            "--no-tty",
            "--",
            "pi",
            "-p",
            "--no-session",
            "--provider",
            self.settings.pi_provider,
            "--model",
            self.settings.pi_model,
        ]

    def _delete_command(self, job: Job) -> list[str]:
        return [
            self.settings.openshell_bin,
            "sandbox",
            "delete",
            *self._scope_args(),
            job.sandbox_name,
        ]

    def run(self, job: Job) -> ExecutionResult:
        runtime_started = time.monotonic()
        created = False
        pending_error: RuntimeExecutionError | None = None
        successful_result: ExecutionResult | None = None
        cleanup_error: str | None = None
        generated_policy: Path | None = None
        active_phase = "policy.materialize"
        phase_started = runtime_started

        logger.debug(
            "job %s details: runtime started prompt_bytes=%d prompt_sha256=%s "
            "policy_bytes=%d policy_sha256=%s",
            _job_ref(job),
            _byte_length(job.prompt),
            _digest(job.prompt),
            _byte_length(job.child_policy),
            _digest(job.child_policy),
        )

        try:
            policy_path, generated_policy = self._materialize_policy(job)
            active_phase = "sandbox.create"
            phase_started = time.monotonic()
            logger.info(
                "job %s creating sandbox %s",
                _job_ref(job),
                job.sandbox_name,
            )
            logger.debug(
                "job %s details: sandbox create timeout_seconds=%d",
                _job_ref(job),
                self.settings.create_timeout_seconds,
            )
            create = self.runner(
                self._create_command(job, policy_path), None, self.settings.create_timeout_seconds
            )
            if create.returncode != 0:
                logger.error(
                    "job %s sandbox creation failed after %dms "
                    "(exit_code=%d, stderr_bytes=%d)",
                    _job_ref(job),
                    _elapsed_ms(phase_started),
                    create.returncode,
                    _byte_length(create.stderr),
                )
                raise RuntimeExecutionError(
                    "OpenShell could not create the child sandbox",
                    code="sandbox-create",
                    stderr=create.stderr,
                    exit_code=create.returncode,
                )
            created = True
            logger.info(
                "job %s sandbox ready in %dms; inspect: %s",
                _job_ref(job),
                _elapsed_ms(phase_started),
                f"openshell logs {job.sandbox_name} --workspace {self.settings.workspace}",
            )

            active_phase = "child.exec"
            phase_started = time.monotonic()
            logger.info(
                "job %s running Pi (timeout=%ds)",
                _job_ref(job),
                self.settings.job_timeout_seconds,
            )
            executed = self.runner(
                self._exec_command(job), job.prompt, self.settings.job_timeout_seconds + 30
            )
            if executed.returncode != 0:
                logger.error(
                    "job %s Pi failed after %.1fs "
                    "(exit_code=%d, stdout_bytes=%d, stderr_bytes=%d)",
                    _job_ref(job),
                    _elapsed_ms(phase_started) / 1000,
                    executed.returncode,
                    _byte_length(executed.stdout),
                    _byte_length(executed.stderr),
                )
                raise RuntimeExecutionError(
                    "Pi failed inside the child sandbox",
                    code="child-exit",
                    stderr=executed.stderr,
                    exit_code=executed.returncode,
                )
            output = executed.stdout.strip()
            if not output:
                logger.error(
                    "job %s Pi returned no output after %.1fs (stderr_bytes=%d)",
                    _job_ref(job),
                    _elapsed_ms(phase_started) / 1000,
                    _byte_length(executed.stderr),
                )
                raise RuntimeExecutionError(
                    "Pi returned no final output",
                    code="empty-output",
                    stderr=executed.stderr,
                    exit_code=executed.returncode,
                )
            if len(output.encode()) > MAX_OUTPUT_BYTES:
                logger.error(
                    "job %s Pi output exceeded the limit after %.1fs "
                    "(output_bytes=%d, stderr_bytes=%d)",
                    _job_ref(job),
                    _elapsed_ms(phase_started) / 1000,
                    _byte_length(output),
                    _byte_length(executed.stderr),
                )
                raise RuntimeExecutionError(
                    "Pi output exceeded the 1 MiB POC limit",
                    code="output-too-large",
                    stderr=executed.stderr,
                    exit_code=executed.returncode,
                )
            successful_result = ExecutionResult(
                output=output,
                stderr=executed.stderr,
                exit_code=executed.returncode,
            )
            logger.info(
                "job %s Pi completed in %.1fs",
                _job_ref(job),
                _elapsed_ms(phase_started) / 1000,
            )
            logger.debug(
                "job %s details: Pi exit_code=%d output_bytes=%d stderr_bytes=%d",
                _job_ref(job),
                executed.returncode,
                _byte_length(output),
                _byte_length(executed.stderr),
            )
        except subprocess.TimeoutExpired as error:
            logger.error(
                "job %s timed out during %s after %.1fs",
                _job_ref(job),
                active_phase,
                _elapsed_ms(phase_started) / 1000,
            )
            pending_error = RuntimeExecutionError(
                "OpenShell child execution timed out",
                code="timeout",
                stderr=str(error),
            )
        except RuntimeExecutionError as error:
            pending_error = error
        finally:
            if created:
                delete_started = time.monotonic()
                logger.debug(
                    "job %s details: deleting sandbox timeout_seconds=%d",
                    _job_ref(job),
                    self.settings.delete_timeout_seconds,
                )
                try:
                    deleted = self.runner(
                        self._delete_command(job), None, self.settings.delete_timeout_seconds
                    )
                    if deleted.returncode != 0:
                        cleanup_error = deleted.stderr.strip() or (
                            f"openshell sandbox delete exited {deleted.returncode}"
                        )
                        logger.error(
                            "job %s sandbox deletion failed after %dms "
                            "(exit_code=%d, stderr_bytes=%d)",
                            _job_ref(job),
                            _elapsed_ms(delete_started),
                            deleted.returncode,
                            _byte_length(deleted.stderr),
                        )
                    else:
                        logger.info(
                            "job %s sandbox deleted in %dms",
                            _job_ref(job),
                            _elapsed_ms(delete_started),
                        )
                except subprocess.TimeoutExpired as error:
                    cleanup_error = f"sandbox deletion timed out: {error}"
                    logger.error(
                        "job %s sandbox deletion timed out after %.1fs",
                        _job_ref(job),
                        _elapsed_ms(delete_started) / 1000,
                    )
            if generated_policy is not None:
                try:
                    generated_policy.unlink(missing_ok=True)
                    logger.debug(
                        "job %s details: generated policy removed",
                        _job_ref(job),
                    )
                except OSError as error:
                    policy_cleanup_error = f"policy cleanup failed: {error}"
                    cleanup_error = (
                        f"{cleanup_error}; {policy_cleanup_error}"
                        if cleanup_error
                        else policy_cleanup_error
                    )
                    logger.error(
                        "job %s generated policy cleanup failed",
                        _job_ref(job),
                    )

        if pending_error is not None:
            pending_error.cleanup_error = cleanup_error
            raise pending_error
        assert successful_result is not None
        logger.debug(
            "job %s details: runtime completed duration_ms=%d",
            _job_ref(job),
            _elapsed_ms(runtime_started),
        )
        return ExecutionResult(
            output=successful_result.output,
            stderr=successful_result.stderr,
            exit_code=successful_result.exit_code,
            cleanup_error=cleanup_error,
        )
