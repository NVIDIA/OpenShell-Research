"""OpenShell CLI-backed child sandbox runtime."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from openshell_tool_service.config import Settings
from openshell_tool_service.store import Job

MAX_OUTPUT_BYTES = 1024 * 1024


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


class OpenShellCliRuntime:
    """Create one child, run Pi once, and always attempt cleanup."""

    def __init__(self, settings: Settings, runner: CommandRunner = _default_runner) -> None:
        self.settings = settings
        self.runner = runner

    def _scope_args(self) -> list[str]:
        arguments = ["--workspace", self.settings.workspace]
        if self.settings.gateway:
            arguments.extend(["--gateway", self.settings.gateway])
        if self.settings.gateway_endpoint:
            arguments.extend(["--gateway-endpoint", self.settings.gateway_endpoint])
        if self.settings.gateway_insecure:
            arguments.append("--gateway-insecure")
        return arguments

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
        created = False
        pending_error: RuntimeExecutionError | None = None
        successful_result: ExecutionResult | None = None
        cleanup_error: str | None = None
        generated_policy: Path | None = None

        try:
            policy_path, generated_policy = self._materialize_policy(job)
            create = self.runner(
                self._create_command(job, policy_path), None, self.settings.create_timeout_seconds
            )
            if create.returncode != 0:
                raise RuntimeExecutionError(
                    "OpenShell could not create the child sandbox",
                    code="sandbox-create",
                    stderr=create.stderr,
                    exit_code=create.returncode,
                )
            created = True

            executed = self.runner(
                self._exec_command(job), job.prompt, self.settings.job_timeout_seconds + 30
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
        except subprocess.TimeoutExpired as error:
            pending_error = RuntimeExecutionError(
                "OpenShell child execution timed out",
                code="timeout",
                stderr=str(error),
            )
        except RuntimeExecutionError as error:
            pending_error = error
        finally:
            if created:
                try:
                    deleted = self.runner(
                        self._delete_command(job), None, self.settings.delete_timeout_seconds
                    )
                    if deleted.returncode != 0:
                        cleanup_error = deleted.stderr.strip() or (
                            f"openshell sandbox delete exited {deleted.returncode}"
                        )
                except subprocess.TimeoutExpired as error:
                    cleanup_error = f"sandbox deletion timed out: {error}"
            if generated_policy is not None:
                try:
                    generated_policy.unlink(missing_ok=True)
                except OSError as error:
                    policy_cleanup_error = f"policy cleanup failed: {error}"
                    cleanup_error = (
                        f"{cleanup_error}; {policy_cleanup_error}"
                        if cleanup_error
                        else policy_cleanup_error
                    )

        if pending_error is not None:
            pending_error.cleanup_error = cleanup_error
            raise pending_error
        assert successful_result is not None
        return ExecutionResult(
            output=successful_result.output,
            stderr=successful_result.stderr,
            exit_code=successful_result.exit_code,
            cleanup_error=cleanup_error,
        )
