from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from openshell_tool_service.policy_reviewer import (
    CachingPolicyReviewer,
    LlmPolicyReviewer,
    PolicyReviewError,
    PolicyReviewRequest,
)


def request() -> PolicyReviewRequest:
    return PolicyReviewRequest(
        parent_policy="parent-policy",
        child_policy="child-policy",
        task="clone repository x",
    )


def response_body(result: dict[str, object]) -> dict[str, object]:
    return {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(result),
                    }
                ],
            }
        ]
    }


def test_llm_reviewer_sends_structured_untrusted_inputs() -> None:
    sent: list[dict[str, object]] = []

    def requester(payload: dict[str, object]) -> dict[str, object]:
        sent.append(payload)
        return response_body(
            {
                "decision": "allow",
                "reason": "Child authority is contained by parent authority.",
                "violations": [],
                "taskAlignment": "aligned",
                "taskAlignmentReason": "The task only needs the child authority.",
            }
        )

    reviewer = LlmPolicyReviewer(
        base_url="https://example.test/v1",
        api_key="secret",
        model="test-model",
        timeout_seconds=10,
        requester=requester,
    )
    result = reviewer.review(request())
    assert result.decision == "allow"
    assert result.reviewer == "mock-llm:test-model"
    assert sent[0]["model"] == "test-model"
    assert json.loads(str(sent[0]["input"])) == {
        "TASK": "clone repository x",
        "PARENT_POLICY": "parent-policy",
        "CHILD_POLICY": "child-policy",
    }
    assert sent[0]["store"] is False


def test_llm_reviewer_uses_http2(monkeypatch: pytest.MonkeyPatch) -> None:
    client_options: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"output_text": "ignored"}

    class FakeClient:
        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def post(self, *_args: object, **_kwargs: object) -> FakeResponse:
            return FakeResponse()

    def client_factory(**kwargs: object) -> FakeClient:
        client_options.update(kwargs)
        return FakeClient()

    monkeypatch.setattr(
        "openshell_tool_service.policy_reviewer.httpx.Client",
        client_factory,
    )
    reviewer = LlmPolicyReviewer(
        base_url="https://example.test/v1",
        api_key="secret",
        model="test-model",
        timeout_seconds=10,
    )

    reviewer._request({"model": "test-model"})

    assert client_options["http2"] is True


@pytest.mark.parametrize(
    "response",
    [
        {"output_text": "not-json"},
        response_body(
            {
                "decision": "allow",
                "reason": "contradictory",
                "violations": ["added email access"],
                "taskAlignment": "warning",
                "taskAlignmentReason": "not needed",
            }
        ),
        response_body(
            {
                "decision": "maybe",
                "reason": "uncertain",
                "violations": [],
                "taskAlignment": "unknown",
                "taskAlignmentReason": "unknown",
            }
        ),
    ],
)
def test_llm_reviewer_rejects_malformed_or_ambiguous_results(
    response: dict[str, object],
) -> None:
    reviewer = LlmPolicyReviewer(
        base_url="https://example.test/v1",
        api_key="secret",
        model="test-model",
        timeout_seconds=10,
        requester=lambda _payload: response,
    )
    with pytest.raises(PolicyReviewError):
        reviewer.review(request())


def test_policy_cache_coalesces_identical_concurrent_reviews() -> None:
    calls = 0

    class Delegate:
        def review(self, _request: PolicyReviewRequest):
            nonlocal calls
            calls += 1
            time.sleep(0.05)
            return LlmPolicyReviewer(
                base_url="https://example.test/v1",
                api_key="secret",
                model="test-model",
                timeout_seconds=10,
                requester=lambda _payload: response_body(
                    {
                        "decision": "allow",
                        "reason": "contained",
                        "violations": [],
                        "taskAlignment": "aligned",
                        "taskAlignmentReason": "aligned",
                    }
                ),
            ).review(request())

    cached = CachingPolicyReviewer(Delegate())
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _index: cached.review(request()), range(8)))

    assert calls == 1
    assert all(result.decision == "allow" for result in results)
    assert sum(result.task_alignment == "aligned" for result in results) == 1
