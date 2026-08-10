from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from slop_cop.config import SlopCopConfig, load_config
from slop_cop.rules.api import RuleMetadata
from slop_cop.runtime import RuleRuntimeError, RuntimeManager, ServiceResponse

CONFIG_PATH = Path(__file__).parents[1] / "slop-cop.toml"


def _config(**service_overrides: Any) -> SlopCopConfig:
    raw = load_config(CONFIG_PATH).model_dump(mode="python")
    raw["services"] = {
        "judge": {
            "url": "https://judge.example/v1/evaluate",
            "token_env": "SLOP_COP_JUDGE_TOKEN",
            "timeout_seconds": 5.0,
            "max_response_bytes": 1024,
            "max_attempts": 1,
            "required_judge_revision": "editorial-v1",
            **service_overrides,
        }
    }
    return SlopCopConfig.model_validate(raw)


def _metadata() -> RuleMetadata:
    return RuleMetadata(
        id="custom.judge",
        category="rhetoric",
        title="Editorial judge",
        rationale="The configured judge found formulaic prose.",
        advice="Review the evidence and state the claim directly.",
        execution_kind="external",
        services=("judge",),
    )


async def _post(
    handler: httpx.AsyncBaseTransport,
    *,
    config: SlopCopConfig | None = None,
    environment: dict[str, str] | None = None,
    deadline: float | None = None,
    payload: dict[str, object] | None = None,
) -> ServiceResponse:
    client = httpx.AsyncClient(transport=handler)
    manager = RuntimeManager(
        config or _config(),
        client=client,
        environment=environment or {"SLOP_COP_JUDGE_TOKEN": "secret"},
        deadline=deadline,
    )
    try:
        return (
            await manager.for_rule(_metadata())
            .service("judge")
            .post_json(payload or {"prose": "private prose"})
        )
    finally:
        await client.aclose()


def test_named_service_applies_auth_and_returns_bounded_json() -> None:
    async def run() -> ServiceResponse:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url == "https://judge.example/v1/evaluate"
            assert request.headers["authorization"] == "Bearer secret"
            assert request.headers["idempotency-key"]
            return httpx.Response(
                200,
                json={"schema_version": 1, "strength": 2},
                headers={"x-request-id": "request-1"},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        manager = RuntimeManager(
            _config(), client=client, environment={"SLOP_COP_JUDGE_TOKEN": "secret"}
        )
        response = await manager.for_rule(_metadata()).service("judge").post_json({"prose": "text"})
        await client.aclose()
        return response

    response = asyncio.run(run())
    assert response.data["strength"] == 2
    assert response.audit["request_id"] == "request-1"


def test_missing_service_credential_is_content_safe() -> None:
    async def run() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
        manager = RuntimeManager(_config(), client=client, environment={})
        try:
            await (
                manager.for_rule(_metadata()).service("judge").post_json({"prose": "secret prose"})
            )
        finally:
            await client.aclose()

    try:
        asyncio.run(run())
    except RuleRuntimeError as error:
        assert "secret prose" not in str(error)
    else:
        raise AssertionError("missing credential should fail")


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(200, content=b"x" * 32), "size limit"),
        (
            httpx.Response(200, content=b"plain", headers={"content-type": "text/plain"}),
            "non-JSON",
        ),
        (
            httpx.Response(200, content=b"{broken", headers={"content-type": "application/json"}),
            "invalid JSON",
        ),
    ],
)
def test_response_bounds_and_json_validation(response: httpx.Response, message: str) -> None:
    config = _config(max_response_bytes=16)
    with pytest.raises(RuleRuntimeError, match=message):
        asyncio.run(_post(httpx.MockTransport(lambda _: response), config=config))


def test_transport_error_is_content_safe() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private prose and secret", request=request)

    with pytest.raises(RuleRuntimeError) as caught:
        asyncio.run(
            _post(
                httpx.MockTransport(handler),
                payload={"prose": "private prose"},
                environment={"SLOP_COP_JUDGE_TOKEN": "secret"},
            )
        )
    assert "private prose" not in str(caught.value)
    assert "secret" not in str(caught.value)


def test_per_request_timeout_is_content_safe() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={"ok": True})

    with pytest.raises(RuleRuntimeError, match="request failed") as caught:
        asyncio.run(
            _post(
                httpx.MockTransport(handler),
                config=_config(timeout_seconds=0.01),
                payload={"prose": "private prose"},
            )
        )
    assert "private prose" not in str(caught.value)


def test_expired_file_deadline_prevents_request() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"ok": True})

    with pytest.raises(RuleRuntimeError, match="deadline expired"):
        asyncio.run(
            _post(
                httpx.MockTransport(handler),
                deadline=time.monotonic() - 0.001,
            )
        )
    assert calls == 0


def test_transient_http_status_retries_with_one_idempotency_key() -> None:
    attempts = 0
    keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        keys.append(request.headers["idempotency-key"])
        if attempts < 3:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json={"ok": True})

    response = asyncio.run(_post(httpx.MockTransport(handler), config=_config(max_attempts=3)))
    assert attempts == 3
    assert len(set(keys)) == 1
    assert response.audit["attempts"] == 3


def test_nontransient_http_status_does_not_retry() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(400, json={"error": "invalid"})

    with pytest.raises(RuleRuntimeError, match="HTTP 400"):
        asyncio.run(_post(httpx.MockTransport(handler), config=_config(max_attempts=3)))
    assert attempts == 1
