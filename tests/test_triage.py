import asyncio
import json
from collections.abc import Awaitable, Callable
from importlib.resources import files

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from incident_triage.api import create_app
from incident_triage.config import Settings

SAMPLE = json.loads(files("incident_triage").joinpath("data", "example.json").read_text())
VALID_DRAFT = {
    "summary": "The checkout service reported two pool failures.",
    "hypothesis": "Requests may be waiting for saturated database connections.",
    "severity_hint": "unknown",
    "evidence_line_ids": ["L2", "L1"],
    "next_check_ids": ["check-pool-metrics"],
}


@pytest.fixture
def demo():
    with TestClient(create_app(Settings(_env_file=None, triage_provider="demo"))) as client:
        yield client


def live_client(
    handler: Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]],
    timeout: float = 2,
) -> TestClient:
    settings = Settings(
        _env_file=None,
        triage_provider="openai",
        openai_api_key="test-key",
        openai_model="test-model",
        openai_timeout_seconds=timeout,
    )
    return TestClient(create_app(settings, openai_transport=httpx.MockTransport(handler)))


def openai_response(part: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status": "completed",
            "output": [
                {"type": "reasoning"},
                {"type": "message", "role": "assistant", "content": [part]},
            ],
        },
    )


def test_demo_diagnosis_and_swagger_example(demo):
    response = demo.post("/v1/triage", json=SAMPLE)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "triaged"
    assert result["provider"] == "demo"
    assert result["service"] == SAMPLE["service"]
    assert result["runbook"]["id"] == "db-pool-exhaustion"
    assert result["diagnosis"]["evidence"] == [
        {"line_id": "L1", "message": SAMPLE["logs"][0]},
        {"line_id": "L2", "message": SAMPLE["logs"][1]},
    ]
    assert result["diagnosis"]["next_checks"][0]["id"] == "check-pool-metrics"
    assert demo.get("/healthz").json() == {"status": "ok"}
    operation = demo.get("/openapi.json").json()["paths"]["/v1/triage"]["post"]
    example = operation["requestBody"]["content"]["application/json"]["examples"]["database_pool"]
    assert example["value"] == SAMPLE


@pytest.mark.parametrize(
    ("logs", "runbook_id"),
    [
        (["ERROR UPSTREAM REQUEST TIMEOUT"], "upstream-timeout"),
        (["WARN queue backlog growing"], "queue-backlog"),
        (
            ["upstream request timeout"] * 10
            + ["connection pool exhausted", "pool acquire timeout"],
            "db-pool-exhaustion",
        ),
    ],
)
def test_retrieval_counts_distinct_case_insensitive_signals(demo, logs, runbook_id):
    result = demo.post("/v1/triage", json={"service": "checkout-api", "logs": logs}).json()
    assert result["runbook"]["id"] == runbook_id


@pytest.mark.parametrize(
    ("logs", "reason"),
    [
        (["INFO checkout succeeded"], "no_match"),
        (["connection pool exhausted"] * 3 + ["queue backlog growing"], "ambiguous_match"),
    ],
)
def test_abstention_skips_live_provider(logs, reason):
    def handler(_):
        pytest.fail("Abstention must happen before the model call.")

    with live_client(handler) as client:
        response = client.post("/v1/triage", json={"service": SAMPLE["service"], "logs": logs})
    assert response.status_code == 200
    assert response.json() == {
        "status": "insufficient_evidence",
        "service": SAMPLE["service"],
        "provider": "openai",
        "reason": reason,
    }


@pytest.mark.parametrize("logs", [[], [""], ["  "], [123], ["x" * 501], ["x"] * 41])
def test_rejects_invalid_log_batches(demo, logs):
    assert (
        demo.post("/v1/triage", json={"service": SAMPLE["service"], "logs": logs}).status_code
        == 422
    )


def test_live_adapter_sends_matched_evidence_and_resolves_model_references():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == "https://api.openai.com/v1/responses"
        assert request.headers["authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["model"] == "test-model"
        assert payload["store"] is False
        assert "untrusted data" in payload["instructions"]
        output_format = payload["text"]["format"]
        assert output_format["strict"] is True
        assert output_format["schema"]["additionalProperties"] is False
        assert set(output_format["schema"]["required"]) == set(VALID_DRAFT)
        context = json.loads(payload["input"])
        assert context["runbook"]["id"] == "db-pool-exhaustion"
        assert [line["line_id"] for line in context["evidence"]] == ["L1", "L2"]
        return openai_response({"type": "output_text", "text": json.dumps(VALID_DRAFT)})

    with live_client(handler) as client:
        response = client.post("/v1/triage", json=SAMPLE)
    assert response.status_code == 200
    result = response.json()
    assert result["provider"] == "openai"
    diagnosis = result["diagnosis"]
    assert diagnosis["hypothesis"] == VALID_DRAFT["hypothesis"]
    assert diagnosis["evidence"][0] == {"line_id": "L2", "message": SAMPLE["logs"][1]}
    assert diagnosis["next_checks"] == [
        {
            "id": "check-pool-metrics",
            "description": "Compare active, idle, and waiting connections with the configured pool limit.",
        }
    ]


@pytest.mark.parametrize(
    "draft",
    [
        "not JSON",
        "{}",
        json.dumps(VALID_DRAFT | {"severity_hint": "critical"}),
        json.dumps(VALID_DRAFT | {"evidence_line_ids": ["L3"]}),
        json.dumps(VALID_DRAFT | {"evidence_line_ids": ["L1", "L1"]}),
        json.dumps(VALID_DRAFT | {"next_check_ids": ["invented-check"]}),
        json.dumps(VALID_DRAFT | {"next_check_ids": ["check-pool-metrics"] * 2}),
        json.dumps(VALID_DRAFT | {"command": "example of an unexpected field"}),
    ],
)
def test_invalid_model_drafts_return_502(draft):
    with live_client(lambda _: openai_response({"type": "output_text", "text": draft})) as client:
        response = client.post("/v1/triage", json=SAMPLE)
    assert response.status_code == 502
    assert response.json()["code"] == "invalid_model_response"


@pytest.mark.parametrize(
    "handler",
    [
        lambda _: httpx.Response(200, text="<html>invalid provider response</html>"),
        lambda _: httpx.Response(200, json={"status": "incomplete", "output": []}),
        lambda _: httpx.Response(200, json={"status": "completed", "output": []}),
        lambda _: openai_response({"type": "refusal", "refusal": "example provider refusal"}),
    ],
)
def test_invalid_provider_responses_return_502(handler):
    with live_client(handler) as client:
        response = client.post("/v1/triage", json=SAMPLE)
    assert response.status_code == 502
    assert response.json()["code"] == "invalid_model_response"


@pytest.mark.parametrize("status_code", [401, 429, 500])
def test_provider_http_failures_return_sanitized_503(status_code, caplog):
    with live_client(
        lambda _: httpx.Response(status_code, json={"error": "upstream-only details"})
    ) as client:
        response = client.post("/v1/triage", json=SAMPLE)
    assert response.status_code == 503
    assert response.json()["code"] == "model_unavailable"
    assert "upstream-only details" not in response.text
    assert "upstream-only details" not in caplog.text
    assert "test-key" not in caplog.text


def test_provider_connection_failure_returns_503():
    def handler(request):
        raise httpx.ConnectError("connection failed", request=request)

    with live_client(handler) as client:
        response = client.post("/v1/triage", json=SAMPLE)
    assert response.status_code == 503
    assert response.json()["code"] == "model_unavailable"


def test_slow_provider_is_cancelled_and_returns_504():
    async def handler(_):
        await asyncio.sleep(1)
        pytest.fail("The deadline should cancel this request.")

    with live_client(handler, timeout=0.01) as client:
        response = client.post("/v1/triage", json=SAMPLE)
    assert response.status_code == 504
    assert response.json()["code"] == "model_timeout"


def test_live_mode_requires_a_key():
    with pytest.raises(ValidationError, match="Set OPENAI_API_KEY"):
        Settings(_env_file=None, triage_provider="openai", openai_api_key=" ")
