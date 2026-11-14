from pydantic import ValidationError

from .models import (
    Diagnosis,
    Evidence,
    IncidentRequest,
    InsufficientEvidence,
    ModelDiagnosis,
    ProviderErrorCode,
    ProviderName,
    RunbookReference,
    Triaged,
    TriageResponse,
)
from .providers import DiagnosisProvider, ProviderError
from .runbooks import Runbook, select_runbook


class TriageService:
    def __init__(
        self,
        runbooks: tuple[Runbook, ...],
        provider: DiagnosisProvider,
        provider_name: ProviderName,
    ) -> None:
        self.runbooks = runbooks
        self.provider = provider
        self.provider_name = provider_name

    async def triage(self, incident: IncidentRequest) -> TriageResponse:
        lines = [
            Evidence(line_id=f"L{index}", message=message)
            for index, message in enumerate(incident.logs, start=1)
        ]
        selection = select_runbook(lines, self.runbooks)
        if isinstance(selection, str):
            return InsufficientEvidence(
                service=incident.service, provider=self.provider_name, reason=selection
            )

        runbook = selection.runbook
        matching_ids = set(selection.line_ids)
        matching_lines = {line.line_id: line for line in lines if line.line_id in matching_ids}
        checks = {check.id: check for check in runbook.checks}
        raw = await self.provider.diagnose(runbook, list(matching_lines.values()))

        try:
            model = ModelDiagnosis.model_validate_json(raw)
            evidence = [matching_lines[line_id] for line_id in model.evidence_line_ids]
            next_checks = [checks[check_id] for check_id in model.next_check_ids]
        except (KeyError, ValidationError) as exc:
            raise ProviderError(ProviderErrorCode.INVALID_RESPONSE) from exc

        if len(set(model.evidence_line_ids)) != len(evidence) or len(
            set(model.next_check_ids)
        ) != len(next_checks):
            raise ProviderError(ProviderErrorCode.INVALID_RESPONSE)

        return Triaged(
            service=incident.service,
            provider=self.provider_name,
            runbook=RunbookReference(id=runbook.id, title=runbook.title),
            diagnosis=Diagnosis(
                summary=model.summary,
                hypothesis=model.hypothesis,
                severity_hint=model.severity_hint,
                evidence=evidence,
                next_checks=next_checks,
            ),
        )
