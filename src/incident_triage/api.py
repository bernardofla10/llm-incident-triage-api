import json
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from importlib.resources import files
from typing import Annotated

import httpx
from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse

from .config import Settings
from .models import IncidentRequest, ProviderErrorCode, ProviderFailure, TriageResponse
from .providers import DemoProvider, DiagnosisProvider, OpenAIProvider, ProviderError
from .runbooks import load_runbooks
from .service import TriageService

logger = logging.getLogger(__name__)

PROVIDER_ERRORS = {
    ProviderErrorCode.INVALID_RESPONSE: (502, "The model returned an unusable diagnosis."),
    ProviderErrorCode.UNAVAILABLE: (503, "The model provider is unavailable."),
    ProviderErrorCode.TIMEOUT: (504, "The model provider timed out."),
}


def create_app(
    settings: Settings | None = None, *, openai_transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configuration = settings if settings is not None else Settings()
        async with AsyncExitStack() as stack:
            provider: DiagnosisProvider = DemoProvider()
            if configuration.triage_provider == "openai":
                client = await stack.enter_async_context(
                    httpx.AsyncClient(
                        headers={
                            "Authorization": f"Bearer {configuration.openai_api_key.get_secret_value()}"
                        },
                        timeout=configuration.openai_timeout_seconds,
                        transport=openai_transport,
                        follow_redirects=False,
                    )
                )
                provider = OpenAIProvider(
                    client, configuration.openai_model, configuration.openai_timeout_seconds
                )

            app.state.triage = TriageService(
                load_runbooks(), provider, configuration.triage_provider
            )
            yield

    app = FastAPI(
        title="LLM Incident Triage API",
        version="0.1.0",
        description="Turn synthetic logs into a first-pass diagnosis grounded in a local runbook.",
        lifespan=lifespan,
    )

    @app.get("/healthz", summary="Check service health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.exception_handler(ProviderError)
    async def handle_provider_error(_: Request, exc: ProviderError) -> JSONResponse:
        status, message = PROVIDER_ERRORS[exc.code]
        logger.warning("Triage provider failed: %s", exc.code)
        failure = ProviderFailure(code=exc.code, message=message)
        return JSONResponse(status_code=status, content=failure.model_dump(mode="json"))

    sample_path = files("incident_triage").joinpath("data", "example.json")
    sample_request = json.loads(sample_path.read_text(encoding="utf-8"))

    @app.post(
        "/v1/triage",
        summary="Triage synthetic incident logs",
        response_model=TriageResponse,
        responses={
            status: {"model": ProviderFailure, "description": message}
            for status, message in PROVIDER_ERRORS.values()
        },
    )
    async def triage(
        incident: Annotated[
            IncidentRequest,
            Body(
                openapi_examples={
                    "database_pool": {
                        "summary": "Database connection pool exhaustion",
                        "value": sample_request,
                    }
                }
            ),
        ],
        request: Request,
    ) -> TriageResponse:
        return await request.app.state.triage.triage(incident)

    return app


app = create_app()
