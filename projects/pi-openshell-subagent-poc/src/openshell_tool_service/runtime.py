"""OpenShell CLI-backed child sandbox runtime."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Protocol

from openshell_tool_service.collaboration import ChildCollaboration
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
    "tls handshake eof",
    "connection reset by peer",
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


class ParentPolicyGetter(Protocol):
    def get(self, sandbox_name: str) -> str: ...


class CachingParentPolicySource:
    """Short-lived single-flight cache for bursty worker fan-out."""

    def __init__(self, delegate: ParentPolicyGetter, ttl_seconds: float = 2) -> None:
        self.delegate = delegate
        self.ttl_seconds = ttl_seconds
        self._lock = Lock()
        self._cache: dict[str, tuple[float, str]] = {}
        self._in_flight: dict[str, Future[str]] = {}

    def get(self, sandbox_name: str) -> str:
        owner = False
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(sandbox_name)
            if cached is not None and cached[0] > now:
                return cached[1]
            future = self._in_flight.get(sandbox_name)
            if future is None:
                future = Future()
                self._in_flight[sandbox_name] = future
                owner = True
        if not owner:
            return future.result()
        try:
            policy = self.delegate.get(sandbox_name)
        except Exception as error:
            with self._lock:
                self._in_flight.pop(sandbox_name, None)
                future.set_exception(error)
            raise
        with self._lock:
            self._cache[sandbox_name] = (time.monotonic() + self.ttl_seconds, policy)
            self._in_flight.pop(sandbox_name, None)
            future.set_result(policy)
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

    def _create_command(
        self, job: Job, policy_path: Path | None, collaboration: ChildCollaboration
    ) -> list[str]:
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
            "--label",
            f"poc-role={job.participant_alias}",
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
            "--env",
            f"POC_COLLABORATION_URL={collaboration.service_url}",
            "--env",
            f"POC_COLLABORATION_TOKEN={collaboration.token}",
            "--env",
            f"POC_COLLABORATION_PARTICIPANT_ID={collaboration.participant_id}",
            "--env",
            f"POC_COLLABORATION_ROLE={collaboration.participant_alias}",
            "--env",
            f"POC_COLLABORATION_GROUP_ID={collaboration.group_id}",
            "--env",
            f"POC_COLLABORATION_RUN_ID={collaboration.run_id}",
        ]
        if self.settings.child_provider:
            command.extend(["--provider", self.settings.child_provider])
        if policy_path:
            command.extend(["--policy", str(policy_path.resolve())])
        if self.settings.child_models_file:
            source = self.settings.child_models_file.resolve()
            command.extend(["--upload", f"{source}:/home/sandbox/.pi/agent"])
        if self.settings.child_collaboration_extension:
            source = self.settings.child_collaboration_extension.resolve()
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
        if self.settings.child_collaboration_extension:
            command.extend(
                [
                    "--extension",
                    "/home/sandbox/.pi/agent/collaboration.ts",
                ]
            )
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
    def _already_absent(completed: subprocess.CompletedProcess[str]) -> bool:
        diagnostic = f"{completed.stdout}\n{completed.stderr}".lower()
        return "not found" in diagnostic or "does not exist" in diagnostic

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
                if deleted.returncode == 0 or self._already_absent(deleted):
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

    def prepare(
        self,
        job: Job,
        collaboration: ChildCollaboration,
        on_ready: Callable[[], None] | None = None,
    ) -> None:
        """Create one idle Ready sandbox without starting Pi."""

        started = time.monotonic()
        create_attempted = False
        pending_error: RuntimeExecutionError | None = None
        cleanup_error: str | None = None
        generated_policy: Path | None = None
        active_phase = "policy.materialize"
        phase_started = started

        logger.debug(
            "job %s details: sandbox preparation started prompt_bytes=%d prompt_sha256=%s "
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
            create_attempted = True
            create = self._run_with_transport_retries(
                job=job,
                phase="sandbox create/upload",
                command=self._create_command(job, policy_path, collaboration),
                input_text=None,
                timeout_seconds=self.settings.create_timeout_seconds,
                transient_markers=TRANSIENT_CREATE_TRANSPORT_MARKERS,
                clean_partial_create=True,
            )
            if create.returncode != 0:
                logger.error(
                    "job %s sandbox creation failed after %dms (exit_code=%d, stderr_bytes=%d)",
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
            if on_ready is not None:
                on_ready()
            logger.info(
                "job %s sandbox ready in %dms; inspect: %s",
                _job_ref(job),
                _elapsed_ms(phase_started),
                f"openshell logs {job.sandbox_name} --workspace {self.settings.workspace}",
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
        except OSError as error:
            logger.error(
                "job %s operating-system failure during %s: %s",
                _job_ref(job),
                active_phase,
                error,
            )
            pending_error = RuntimeExecutionError(
                f"Tool Service operating-system failure during {active_phase}",
                code="runtime-os-error",
                stderr=str(error),
            )
        except RuntimeExecutionError as error:
            pending_error = error
        finally:
            if pending_error is not None and create_attempted:
                cleanup_error = self.cleanup(job)
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
        logger.debug(
            "job %s details: sandbox preparation completed duration_ms=%d",
            _job_ref(job),
            _elapsed_ms(started),
        )

    def execute(self, job: Job) -> ExecutionResult:
        """Run Pi once in an already Ready sandbox, capture logs, and delete it."""

        started = time.monotonic()
        pending_error: RuntimeExecutionError | None = None
        successful_result: ExecutionResult | None = None
        sandbox_logs: str | None = None
        sandbox_log_error: str | None = None
        logger.info(
            "job %s running Pi (timeout=%ds)",
            _job_ref(job),
            self.settings.job_timeout_seconds,
        )
        try:
            executed = self._run_with_transport_retries(
                job=job,
                phase="sandbox SSH",
                command=self._exec_command(job),
                input_text=job.prompt,
                timeout_seconds=self.settings.job_timeout_seconds + 30,
                transient_markers=TRANSIENT_EXEC_TRANSPORT_MARKERS,
            )
            if executed.returncode != 0:
                raise RuntimeExecutionError(
                    "Pi failed inside the child sandbox",
                    code="child-exit",
                    stderr=executed.stderr,
                    exit_code=executed.returncode,
                )
            output = executed.stdout.strip()
            if not output:
                raise RuntimeExecutionError(
                    "Pi returned no final output",
                    code="empty-output",
                    stderr=executed.stderr,
                    exit_code=executed.returncode,
                )
            if len(output.encode()) > MAX_OUTPUT_BYTES:
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
            logger.info("job %s Pi completed in %.1fs", _job_ref(job), time.monotonic() - started)
        except subprocess.TimeoutExpired as error:
            pending_error = RuntimeExecutionError(
                "OpenShell child execution timed out", code="timeout", stderr=str(error)
            )
        except OSError as error:
            pending_error = RuntimeExecutionError(
                "Tool Service operating-system failure during child.exec",
                code="runtime-os-error",
                stderr=str(error),
            )
        except RuntimeExecutionError as error:
            pending_error = error
        finally:
            logger.info("job %s capturing child sandbox logs", _job_ref(job))
            try:
                captured = self.runner(
                    self._logs_command(job), None, self.settings.delete_timeout_seconds
                )
                if captured.returncode == 0:
                    sandbox_logs = captured.stdout
                    logger.info(
                        "job %s captured child sandbox logs (bytes=%d)",
                        _job_ref(job),
                        _byte_length(sandbox_logs),
                    )
                else:
                    sandbox_log_error = captured.stderr.strip() or (
                        f"openshell logs exited {captured.returncode}"
                    )
            except (OSError, subprocess.TimeoutExpired) as error:
                sandbox_log_error = str(error)
            cleanup_error = self.cleanup(job)

        if pending_error is not None:
            pending_error.cleanup_error = cleanup_error
            pending_error.sandbox_logs = sandbox_logs
            pending_error.sandbox_log_error = sandbox_log_error
            raise pending_error
        assert successful_result is not None
        return ExecutionResult(
            output=successful_result.output,
            stderr=successful_result.stderr,
            exit_code=successful_result.exit_code,
            cleanup_error=cleanup_error,
            sandbox_logs=sandbox_logs,
            sandbox_log_error=sandbox_log_error,
        )

    def run(
        self,
        job: Job,
        collaboration: ChildCollaboration,
        on_ready: Callable[[], None] | None = None,
    ) -> ExecutionResult:
        """Compatibility helper for callers that do not need an all-ready barrier."""

        self.prepare(job, collaboration, on_ready)
        return self.execute(job)
