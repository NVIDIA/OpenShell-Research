from __future__ import annotations

import argparse
import asyncio
import os
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any

from . import __version__

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

try:
    from fastapi import FastAPI, HTTPException
except ImportError:  # pragma: no cover - exercised only when optional deps are absent.
    FastAPI = None  # type: ignore[assignment]
    HTTPException = None  # type: ignore[assignment]


@dataclass(frozen=True)
class Settings:
    reachy_host: str
    reachy_port: int
    connection_mode: str
    media_backend: str
    timeout: float

    @classmethod
    def from_env(cls) -> "Settings":
        host = os.getenv("REACHY_HOST", "localhost")
        return cls(
            reachy_host=host,
            reachy_port=_env_int("REACHY_PORT", 8000),
            connection_mode=os.getenv("REACHY_CONNECTION_MODE", _default_connection_mode(host)),
            media_backend=os.getenv("REACHY_MEDIA_BACKEND", "no_media"),
            timeout=_env_float("REACHY_TIMEOUT", 5.0),
        )

    @property
    def daemon_base_url(self) -> str:
        return f"http://{self.reachy_host}:{self.reachy_port}"


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


def daemon_status(settings: Settings) -> dict[str, Any]:
    url = f"{settings.daemon_base_url}/api/daemon/status"
    try:
        with urllib.request.urlopen(url, timeout=settings.timeout) as response:
            body = response.read(4096).decode("utf-8", errors="replace")
            return {
                "ok": 200 <= response.status < 500,
                "status_code": response.status,
                "url": url,
                "body": body,
            }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "ok": False,
            "url": url,
            "error": str(exc),
        }


def run_smoke_motion(settings: Settings) -> dict[str, Any]:
    try:
        from reachy_mini import ReachyMini
        from reachy_mini.utils import create_head_pose
    except ImportError as exc:
        raise RuntimeError(
            "Reachy Mini SDK is not installed. Install with `uv pip install -e '.[sdk]'` "
            "or include the `sim` extra for local simulator work."
        ) from exc

    with ReachyMini(
        host=settings.reachy_host,
        port=settings.reachy_port,
        connection_mode=settings.connection_mode,
        spawn_daemon=False,
        media_backend=settings.media_backend,
        timeout=settings.timeout,
    ) as mini:
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

    return {
        "ok": True,
        "reachy": asdict(settings),
        "motion": "smoke",
    }


def _create_app() -> Any:
    if FastAPI is None:
        return None

    api = FastAPI(
        title="Reachy Mini OpenShell Backend",
        version=__version__,
    )

    @api.get("/health")
    def health() -> dict[str, Any]:
        settings = Settings.from_env()
        status = daemon_status(settings)
        return {
            "ok": status["ok"],
            "reachy": asdict(settings),
            "daemon": status,
        }

    @api.post("/moves/smoke")
    async def smoke_move() -> dict[str, Any]:
        settings = Settings.from_env()
        try:
            return await asyncio.to_thread(run_smoke_motion, settings)
        except Exception as exc:  # pragma: no cover - depends on live daemon.
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return api


app = _create_app()


def main(argv: list[str] | None = None) -> int:
    if FastAPI is None:
        print(
            "FastAPI is not installed. Install backend dependencies with "
            '`uv pip install -e ".[backend]"`.',
            file=sys.stderr,
        )
        return 2

    parser = argparse.ArgumentParser(description="Run the Reachy Mini OpenShell backend.")
    parser.add_argument("--host", default=os.getenv("BACKEND_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=_env_int("BACKEND_PORT", 8080))
    parser.add_argument("--reachy-host", default=None)
    parser.add_argument("--reachy-port", type=int, default=None)
    parser.add_argument("--connection-mode", default=None)
    parser.add_argument("--media-backend", default=None)
    parser.add_argument("--log-level", default=os.getenv("UVICORN_LOG_LEVEL", "info"))
    args = parser.parse_args(argv)

    if args.reachy_host is not None:
        os.environ["REACHY_HOST"] = args.reachy_host
    if args.reachy_port is not None:
        os.environ["REACHY_PORT"] = str(args.reachy_port)
    if args.connection_mode is not None:
        os.environ["REACHY_CONNECTION_MODE"] = args.connection_mode
    if args.media_backend is not None:
        os.environ["REACHY_MEDIA_BACKEND"] = args.media_backend

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
