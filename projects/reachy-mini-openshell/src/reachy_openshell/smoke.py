from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass(frozen=True)
class ReachyTarget:
    host: str
    port: int
    connection_mode: str
    timeout: float


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def _default_connection_mode(host: str) -> str:
    return "localhost_only" if host in LOCAL_HOSTS else "network"


def build_parser() -> argparse.ArgumentParser:
    default_host = os.getenv("REACHY_HOST", "localhost")
    parser = argparse.ArgumentParser(description="Run a small Reachy Mini SDK smoke motion.")
    parser.add_argument("--host", default=default_host)
    parser.add_argument("--port", type=int, default=_env_int("REACHY_PORT", 8000))
    parser.add_argument(
        "--connection-mode",
        choices=["auto", "localhost_only", "network"],
        default=os.getenv("REACHY_CONNECTION_MODE", _default_connection_mode(default_host)),
    )
    parser.add_argument("--timeout", type=float, default=_env_float("REACHY_TIMEOUT", 5.0))
    return parser


def run_motion(target: ReachyTarget) -> None:
    try:
        from reachy_mini import ReachyMini
        from reachy_mini.utils import create_head_pose
    except ImportError as exc:
        raise RuntimeError(
            "Reachy Mini SDK is not installed. Install with `uv pip install -e .` "
            "or `uv pip install -e '.[sim]'` for simulator work."
        ) from exc

    with ReachyMini(
        host=target.host,
        port=target.port,
        connection_mode=target.connection_mode,
        spawn_daemon=False,
        timeout=target.timeout,
    ) as mini:
        print(f"Connected to Reachy Mini daemon at {target.host}:{target.port}")
        print("Moving head and antennas...")
        mini.goto_target(
            head=create_head_pose(z=20, roll=10, mm=True, degrees=True),
            duration=1.0,
        )
        mini.goto_target(antennas=[0.6, -0.6], duration=0.3)
        mini.goto_target(antennas=[-0.6, 0.6], duration=0.3)
        mini.goto_target(
            head=create_head_pose(),
            antennas=[0.0, 0.0],
            duration=1.0,
        )
        print("Smoke motion complete.")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    target = ReachyTarget(
        host=args.host,
        port=args.port,
        connection_mode=args.connection_mode,
        timeout=args.timeout,
    )
    try:
        run_motion(target)
    except Exception as exc:
        print(f"Reachy smoke motion failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

