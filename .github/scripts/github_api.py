# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Small JSON adapter around the authenticated GitHub CLI used by CI."""

import json
import os
import re
import subprocess


class GitHubError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class GitHub:
    def __init__(self, repository: str | None = None):
        self.repository = repository or os.environ["GITHUB_REPOSITORY"]

    def request(self, method: str, path: str, data=None):
        arguments = ["--method", method]
        if data is not None:
            arguments.extend(["--input", "-"])
        return self._run(path, arguments, data)

    def paginate(self, path: str, key: str | None = None) -> list:
        pages = self._run(path, ["--method", "GET", "--paginate", "--slurp"])
        results = []
        for page in pages:
            results.extend(page[key] if key else page)
        return results

    def _run(self, path: str, arguments: list[str], data=None):
        command = ["gh", "api", f"repos/{self.repository}/{path}", *arguments]
        result = subprocess.run(
            command,
            input=json.dumps(data) if data is not None else None,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise GitHubError(
                result.stderr.strip() or f"GitHub API request failed: {path}",
                _error_status(result.stdout, result.stderr),
            )
        if not result.stdout.strip():
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise GitHubError(f"GitHub API returned invalid JSON: {path}") from error


def _error_status(stdout: str, stderr: str) -> int | None:
    try:
        response = json.loads(stdout)
    except json.JSONDecodeError:
        response = None
    if isinstance(response, dict):
        status = response.get("status")
        if isinstance(status, int) or isinstance(status, str) and status.isdigit():
            return int(status)
    match = re.search(r"\bHTTP (\d{3})\b", stderr)
    return int(match.group(1)) if match else None
