# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Package-specific errors with stable CLI exit classifications."""


class OarError(Exception):
    """Base expected runner error."""


class ConfigurationError(OarError):
    """Invalid configuration or invocation (exit code 2)."""


class ExecutionError(OarError):
    """OpenShell or agent execution failure (exit code 1)."""


class ArtifactError(OarError):
    """Missing or invalid required artifact (exit code 3)."""
