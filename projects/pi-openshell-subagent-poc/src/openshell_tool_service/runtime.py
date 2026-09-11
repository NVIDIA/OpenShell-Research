"""OpenShell CLI-backed child sandbox runtime."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from openshell_tool_service.config import Settings
from openshell_tool_service.store import Job

MAX_OUTPUT_BYTES = 1024 * 1024
TRANSPORT_RETRY_ATTEMPTS = 3
TRANSPORT_RETRY_BASE_SECONDS = 0.25
TRANSIENT_CREATE_TRANSPORT_MARKERS = (
    "transport error",
    "tls handshake eof",
    "ssh tar extract exited with status",
)
TRANSIENT_EXEC_TRANSPORT_MARKERS = (
    "failed to establish ssh transport",
    "failed to start relay proxy",
)
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
    sandbox_logs: str | None = None
    sandbox_log_error: str | None = None


class RuntimeExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        stderr: str = "",
        exit_code: int | None = None,
        cleanup_error: str | None = None,
        sandbox_logs: str | None = None,
        sandbox_log_error: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.stderr = stderr
        self.exit_code = exit_code
        self.cleanup_error = cleanup_error
        self.sandbox_logs = sandbox_logs
        self.sandbox_log_error = sandbox_log_error


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
        logger.debug(
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
        logger.debug(
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

    def __init__(
        self,
        settings: Settings,
        runner: CommandRunner = _default_runner,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.runner = runner
        self.sleep = sleep

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
            "--no-credential-warnings",
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
            "--env",
            "NODE_OPTIONS=--disable-warning=UNDICI-EHPA",
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

    def _materialize_policy(self, job: Job) -> Path:
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
        return generated_path

    def _exec_command(self, job: Job) -> list[str]:
        command = [
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
        return command

    def _delete_command(self, job: Job) -> list[str]:
        return [
            self.settings.openshell_bin,
            "sandbox",
            "delete",
            *self._scope_args(),
            job.sandbox_name,
        ]

    def _logs_command(self, job: Job) -> list[str]:
        return [
            self.settings.openshell_bin,
            "logs",
            job.sandbox_name,
            *self._scope_args(),
            "-n",
            "2000",
            "--source",
            "all",
        ]

    @staticmethod
    def _already_absent(completed: subprocess.CompletedProcess[str], sandbox_name: str) -> bool:
        diagnostic = f"{completed.stdout}\n{completed.stderr}".lower()
        return bool(re.search(
            rf"sandbox\s+['\"]?{re.escape(sandbox_name.lower())}['\"]?\s+"
            r"(?:was\s+|is\s+)?(?:not found|does not exist)",
            diagnostic,
        ))

    @staticmethod
    def _transient_transport_failure(
        completed: subprocess.CompletedProcess[str], markers: tuple[str, ...]
    ) -> bool:
        diagnostic = f"{completed.stdout}\n{completed.stderr}".lower()
        return any(marker in diagnostic for marker in markers)

    def _run_with_transport_retries(
        self,
        *,
        job: Job,
        phase: str,
        command: Sequence[str],
        input_text: str | None,
        timeout_seconds: int,
        transient_markers: tuple[str, ...],
        clean_partial_create: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        for attempt in range(1, TRANSPORT_RETRY_ATTEMPTS + 1):
            completed = self.runner(command, input_text, timeout_seconds)
            if (
                completed.returncode == 0
                # Output means execution may have begun. Never replay the task.
                or (not clean_partial_create and bool(completed.stdout.strip()))
                or not self._transient_transport_failure(completed, transient_markers)
                or attempt == TRANSPORT_RETRY_ATTEMPTS
            ):
                return completed
            logger.warning(
                "job %s transient %s transport failure; retrying (attempt=%d/%d)",
                _job_ref(job),
                phase,
                attempt,
                TRANSPORT_RETRY_ATTEMPTS,
            )
            if clean_partial_create:
                cleanup_error = self.cleanup(job)
                if cleanup_error:
                    logger.error(
                        "job %s cannot safely retry sandbox creation because "
                        "partial cleanup failed: %s",
                        _job_ref(job),
                        cleanup_error,
                    )
                    return completed
            self.sleep(TRANSPORT_RETRY_BASE_SECONDS * (2 ** (attempt - 1)))
        raise AssertionError("transport retry loop did not return")

    def cleanup(self, job: Job) -> str | None:
        """Best-effort idempotent cleanup used by execution and restart recovery."""

        last_error: str | None = None
        for attempt in range(1, 4):
            started = time.monotonic()
            try:
                deleted = self.runner(
                    self._delete_command(job), None, self.settings.delete_timeout_seconds
                )
                if deleted.returncode == 0 or self._already_absent(deleted, job.sandbox_name):
                    logger.info(
                        "job %s sandbox deleted in %dms (attempt=%d)",
                        _job_ref(job),
                        _elapsed_ms(started),
                        attempt,
                    )
                    return None
                last_error = deleted.stderr.strip() or (
                    f"openshell sandbox delete exited {deleted.returncode}"
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                last_error = str(error)
            logger.warning(
                "job %s sandbox deletion attempt %d failed: %s",
                _job_ref(job),
                attempt,
                last_error,
            )
            if attempt < 3:
                self.sleep(0.1 * attempt)
        return last_error or "sandbox deletion failed"

    def _checked_command(
        self,
        job: Job,
        *,
        phase: str,
        command: Sequence[str],
        timeout_seconds: int,
        failure_code: str,
        failure_message: str,
        transient_markers: tuple[str, ...],
        input_text: str | None = None,
        clean_partial_create: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Normalize command failures while preserving transport retry behavior."""
        started = time.monotonic()
        logger.info("job %s %s started (timeout=%ds)", _job_ref(job), phase, timeout_seconds)
        try:
            completed = self._run_with_transport_retries(
                job=job,
                phase=phase,
                command=command,
                input_text=input_text,
                timeout_seconds=timeout_seconds,
                transient_markers=transient_markers,
                clean_partial_create=clean_partial_create,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeExecutionError(
                f"OpenShell child timed out during {phase}", code="timeout", stderr=str(error)
            ) from error
        except OSError as error:
            raise RuntimeExecutionError(
                f"Tool Service operating-system failure during {phase}",
                code="runtime-os-error",
                stderr=str(error),
            ) from error
        if completed.returncode != 0:
            raise RuntimeExecutionError(
                failure_message,
                code=failure_code,
                stderr=completed.stderr,
                exit_code=completed.returncode,
            )
        logger.info("job %s %s completed in %dms", _job_ref(job), phase, _elapsed_ms(started))
        return completed

    def _execute(self, job: Job) -> ExecutionResult:
        executed = self._checked_command(
            job,
            phase="child.exec",
            command=self._exec_command(job),
            input_text=job.prompt,
            timeout_seconds=self.settings.job_timeout_seconds + 30,
            failure_code="child-exit",
            failure_message="Pi failed inside the child sandbox",
            transient_markers=TRANSIENT_EXEC_TRANSPORT_MARKERS,
        )
        output = executed.stdout.strip()
        if not output or _byte_length(output) > MAX_OUTPUT_BYTES:
            raise RuntimeExecutionError(
                "Pi returned no final output"
                if not output
                else "Pi output exceeded the 1 MiB POC limit",
                code="empty-output" if not output else "output-too-large",
                stderr=executed.stderr,
                exit_code=executed.returncode,
            )
        return ExecutionResult(output, executed.stderr, executed.returncode)

    def _capture_logs(self, job: Job) -> tuple[str | None, str | None]:
        """Log collection is best effort and must not prevent sandbox cleanup."""
        logger.info("job %s capturing child sandbox logs", _job_ref(job))
        try:
            captured = self.runner(
                self._logs_command(job), None, self.settings.delete_timeout_seconds
            )
            if captured.returncode == 0:
                logger.info(
                    "job %s captured child sandbox logs (bytes=%d)",
                    _job_ref(job),
                    _byte_length(captured.stdout),
                )
                return captured.stdout, None
            return None, captured.stderr.strip() or f"openshell logs exited {captured.returncode}"
        except (OSError, subprocess.TimeoutExpired) as error:
            return None, str(error)

    def run(self, job: Job) -> ExecutionResult:
        """Own the entire child lifecycle, including cleanup after failed creation."""
        policy_path: Path | None = None
        create_attempted = False
        execution_started = False
        result: ExecutionResult | None = None
        failure: RuntimeExecutionError | None = None
        sandbox_logs: str | None = None
        sandbox_log_error: str | None = None
        cleanup_errors: list[str] = []
        try:
            policy_path = self._materialize_policy(job)
            command = self._create_command(job, policy_path)
            create_attempted = True
            logger.info("job %s creating sandbox %s", _job_ref(job), job.sandbox_name)
            self._checked_command(
                job,
                phase="sandbox.create",
                command=command,
                timeout_seconds=self.settings.create_timeout_seconds,
                failure_code="sandbox-create",
                failure_message="OpenShell could not create the child sandbox",
                transient_markers=TRANSIENT_CREATE_TRANSPORT_MARKERS,
                clean_partial_create=True,
            )
            logger.info(
                "job %s sandbox ready; inspect: openshell logs %s --workspace %s",
                _job_ref(job),
                job.sandbox_name,
                self.settings.workspace,
            )
            execution_started = True
            result = self._execute(job)
        except RuntimeExecutionError as error:
            failure = error
        except Exception as error:
            logger.exception("job %s unexpected runtime failure", _job_ref(job))
            failure = RuntimeExecutionError(str(error), code="tool-service")
            failure.__cause__ = error
        finally:
            # Even an unexpected exception during log collection must not skip deletion.
            try:
                if execution_started:
                    sandbox_logs, sandbox_log_error = self._capture_logs(job)
            except Exception as error:
                sandbox_log_error = str(error)
                logger.exception("job %s unexpected log capture failure", _job_ref(job))
            finally:
                if create_attempted:
                    try:
                        cleanup_error = self.cleanup(job)
                    except Exception as error:
                        cleanup_error = str(error)
                        logger.exception("job %s unexpected cleanup failure", _job_ref(job))
                    if cleanup_error:
                        cleanup_errors.append(cleanup_error)
                if policy_path is not None:
                    try:
                        policy_path.unlink(missing_ok=True)
                    except OSError as error:
                        cleanup_errors.append(f"policy cleanup failed: {error}")
                        logger.warning("job %s policy cleanup failed: %s", _job_ref(job), error)

        diagnostics = {
            "cleanup_error": "; ".join(cleanup_errors) or None,
            "sandbox_logs": sandbox_logs,
            "sandbox_log_error": sandbox_log_error,
        }
        if failure is not None:
            failure.cleanup_error = diagnostics["cleanup_error"]
            failure.sandbox_logs = sandbox_logs
            failure.sandbox_log_error = sandbox_log_error
            raise failure
        assert result is not None
        return replace(result, **diagnostics)
