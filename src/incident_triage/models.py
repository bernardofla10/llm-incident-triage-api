from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ProviderName = Literal["demo", "openai"]
Severity = Literal["low", "medium", "high", "unknown"]
AbstentionReason = Literal["no_match", "ambiguous_match"]
LogMessage = Annotated[str, StringConstraints(min_length=1, max_length=500, pattern=r"\S")]
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,63}$")]
LineID = Annotated[str, StringConstraints(pattern=r"^L[1-9][0-9]?$")]
DiagnosisText = Annotated[str, StringConstraints(min_length=1, max_length=500, pattern=r"\S")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class IncidentRequest(Contract):
    service: Identifier
    logs: list[LogMessage] = Field(min_length=1, max_length=40)


class Evidence(Contract):
    line_id: LineID
    message: LogMessage


class RunbookReference(Contract):
    id: Identifier
    title: str


class RunbookCheck(Contract):
    id: Identifier
    description: str


class Diagnosis(Contract):
    summary: DiagnosisText
    hypothesis: DiagnosisText
    severity_hint: Severity
    evidence: list[Evidence]
    next_checks: list[RunbookCheck]


class Triaged(Contract):
    status: Literal["triaged"] = "triaged"
    service: Identifier
    provider: ProviderName
    runbook: RunbookReference
    diagnosis: Diagnosis


class InsufficientEvidence(Contract):
    status: Literal["insufficient_evidence"] = "insufficient_evidence"
    service: Identifier
    provider: ProviderName
    reason: AbstentionReason


TriageResponse = Annotated[Triaged | InsufficientEvidence, Field(discriminator="status")]


class ModelDiagnosis(Contract):
    summary: DiagnosisText
    hypothesis: DiagnosisText
    severity_hint: Severity
    evidence_line_ids: list[LineID] = Field(min_length=1, max_length=5)
    next_check_ids: list[Identifier] = Field(min_length=1, max_length=3)


class ProviderErrorCode(StrEnum):
    INVALID_RESPONSE = "invalid_model_response"
    UNAVAILABLE = "model_unavailable"
    TIMEOUT = "model_timeout"


class ProviderFailure(Contract):
    code: ProviderErrorCode
    message: str
