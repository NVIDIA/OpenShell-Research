"""Regression tests for the two explicit camera policy modes."""

# ruff: noqa: D103

from typing import Any
from pathlib import Path

import yaml


POLICY_DIRECTORY = Path(__file__).parents[1] / "openshell"


def _load_policy(filename: str) -> dict[str, Any]:
    with (POLICY_DIRECTORY / filename).open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def _allowed_requests(policy: dict[str, Any]) -> set[tuple[str, int, str, str]]:
    requests: set[tuple[str, int, str, str]] = set()
    for network_policy in policy["network_policies"].values():
        for endpoint in network_policy["endpoints"]:
            for rule in endpoint.get("rules", []):
                allowed = rule["allow"]
                requests.add(
                    (
                        endpoint["host"],
                        endpoint["port"],
                        allowed["method"],
                        allowed["path"],
                    )
                )
    return requests


def test_motion_disabled_policy_blocks_camera_and_motion_start() -> None:
    requests = _allowed_requests(_load_policy("policy-motion-disabled.yaml"))

    assert not any(port == 8042 for _, port, _, _ in requests)
    assert not any(path == "/api/move/goto" for _, _, _, path in requests)
    assert ("host.openshell.internal", 8000, "POST", "/api/move/stop") in requests


def test_camera_enabled_policy_allows_only_fixed_capture_and_still_blocks_motion_start() -> None:
    requests = _allowed_requests(_load_policy("policy-camera-enabled-motion-disabled.yaml"))
    camera_requests = {request for request in requests if request[1] == 8042}

    assert camera_requests == {
        ("host.openshell.internal", 8042, "POST", "/camera/capture")
    }
    assert not any(path == "/api/move/goto" for _, _, _, path in requests)
