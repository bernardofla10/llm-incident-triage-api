# LLM Incident Triage API

A Python/FastAPI service that turns synthetic incident logs into a first-pass diagnosis. It selects a local runbook, asks OpenAI for a structured hypothesis, and returns the original evidence lines and recommended checks for an on-call engineer to review.

[![CI](https://github.com/bernardofla10/llm-incident-triage-api/actions/workflows/ci.yml/badge.svg)](https://github.com/bernardofla10/llm-incident-triage-api/actions/workflows/ci.yml)

[Try it locally](#try-it-locally) · [Use the OpenAI provider](#use-the-openai-provider) · [Run the tests](#run-the-tests)

## Example diagnosis

The synthetic checkout API reports a full database connection pool followed by an acquire timeout. The service selects the [database pool runbook](src/incident_triage/data/runbooks/db-pool-exhaustion.json) and returns the following result in demo mode.

| Field | Result |
| --- | --- |
| Hypothesis | Requests may be waiting for database connections because the pool has reached capacity. |
| Evidence | `L1` reports `active=20 max=20`. `L2` reports a 1,500 ms acquire timeout. |
| Severity hint | `unknown`. User impact is unverified in this example. |
| First check | Compare active, idle, and waiting connections with the configured pool limit. |

[Example request](src/incident_triage/data/example.json) · [Full JSON response](examples/checkout-diagnosis.json)

## Try it locally

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run these commands. The project uses Python 3.12.

```sh
git clone https://github.com/bernardofla10/llm-incident-triage-api.git
cd llm-incident-triage-api
uv sync --locked
uv run uvicorn incident_triage.api:app
```

Open [Swagger UI](http://127.0.0.1:8000/docs), expand `POST /v1/triage`, and select **Try it out** and **Execute** to send the prefilled example. Or send the same request from a second terminal.

```sh
curl -sS http://127.0.0.1:8000/v1/triage \
  -H 'Content-Type: application/json' \
  --data-binary @src/incident_triage/data/example.json
```

Demo mode uses a deterministic provider and needs no account or API key. The response identifies the provider as `demo`.

## Use the OpenAI provider

Create a local `.env` from [`.env.example`](.env.example). Set `TRIAGE_PROVIDER=openai` and set `OPENAI_API_KEY` to your own API key. Restart the server and send the same request. Live responses identify the provider as `openai`. The default model is `gpt-5.4-mini`. `OPENAI_MODEL` can select a different model that supports Structured Outputs on the Responses API.

Live requests send the selected runbook and matching synthetic log lines to OpenAI and may incur API charges. Keep the key in your local environment and use synthetic data. The request sets `store: false`.

[OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)

## How it works

1. **Validate the request.** Accept a service identifier and 1–40 log lines of up to 500 characters each. Assign each line a stable ID within the request.
2. **Retrieve a runbook.** Count distinct, case-insensitive signal phrases in the logs. Select the highest-scoring runbook from the bundled catalog. A tie or no match returns `insufficient_evidence` before the provider step.
3. **Draft a diagnosis.** Send the selected runbook and matching lines to the OpenAI Responses API with a strict JSON schema. In demo mode, the local provider produces a draft with the same schema.
4. **Validate references.** Reject invalid JSON, unknown IDs, and duplicate references. Copy evidence text from the request and check descriptions from the selected runbook. Treat the generated hypothesis and severity hint as suggestions for human review.

Phrase matching is easy to inspect for these three incident classes. It can miss paraphrases and returns at most one runbook, so the demo can abstain on an unfamiliar or ambiguous incident. The API provides read-only triage suggestions for human review.

### HTTP contract

| Status | Result |
| --- | --- |
| `200` | `triaged` with a diagnosis, or `insufficient_evidence` with a reason |
| `422` | The request failed validation |
| `502` | `invalid_model_response` for unusable output, including refusals and invalid references |
| `503` | `model_unavailable` for upstream HTTP or connection failures |
| `504` | `model_timeout` after the configured request deadline |

Upstream failures return stable error codes and generic messages. The service makes one provider request per matched incident, with a 20-second deadline by default. `GET /healthz` reports local service health.

## Run the tests

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

The tests exercise the FastAPI endpoint and intercept OpenAI requests at the HTTP transport boundary. They cover runbook selection, abstention, malformed and ungrounded output, provider failures, and timeouts. GitHub Actions runs the same checks without credentials.

| Path | Responsibility |
| --- | --- |
| [`api.py`](src/incident_triage/api.py) | HTTP routes, lifecycle, and error mapping |
| [`service.py`](src/incident_triage/service.py) | Triage workflow and reference validation |
| [`providers.py`](src/incident_triage/providers.py) | Local demo provider and OpenAI Responses adapter |
| [`data/runbooks/`](src/incident_triage/data/runbooks) | Editable incident signals and read-only checks |
| [`tests/`](tests) | Endpoint and provider contract tests |
