from __future__ import annotations

import os
import sys
import types
import unittest
from contextlib import contextmanager, redirect_stdout
from io import StringIO
from unittest.mock import patch

from reachy_openshell import backend, smoke


class FakeReachyMini:
    calls: list[dict[str, object]] = []

    def __init__(self, **kwargs: object) -> None:
        self.calls.append(kwargs)

    def __enter__(self) -> "FakeReachyMini":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def goto_target(self, **_kwargs: object) -> None:
        return None


@contextmanager
def fake_reachy_modules():
    original = {
        name: sys.modules.get(name)
        for name in ("reachy_mini", "reachy_mini.utils")
    }

    reachy_module = types.ModuleType("reachy_mini")
    reachy_module.ReachyMini = FakeReachyMini
    utils_module = types.ModuleType("reachy_mini.utils")
    utils_module.create_head_pose = lambda **kwargs: {"head_pose": kwargs}

    sys.modules["reachy_mini"] = reachy_module
    sys.modules["reachy_mini.utils"] = utils_module
    FakeReachyMini.calls = []

    try:
        yield
    finally:
        for name, module in original.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


class ReachyConstructorTests(unittest.TestCase):
    def test_backend_smoke_motion_disables_media_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = backend.Settings.from_env()

        with fake_reachy_modules():
            backend.run_smoke_motion(settings)

        self.assertEqual(FakeReachyMini.calls[0]["media_backend"], "no_media")

    def test_smoke_cli_disables_media_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            target = smoke.target_from_args(smoke.build_parser().parse_args([]))

        with fake_reachy_modules(), redirect_stdout(StringIO()):
            smoke.run_motion(target)

        self.assertEqual(FakeReachyMini.calls[0]["media_backend"], "no_media")

    def test_smoke_cli_uses_network_mode_for_non_local_cli_host(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            target = smoke.target_from_args(
                smoke.build_parser().parse_args(["--host", "host.openshell.internal"])
            )

        self.assertEqual(target.connection_mode, "network")


if __name__ == "__main__":
    unittest.main()
