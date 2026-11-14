from dataclasses import dataclass
from importlib.resources import files
from typing import Annotated

from pydantic import Field, StringConstraints

from .models import AbstentionReason, DiagnosisText, Evidence, RunbookCheck, RunbookReference


class Runbook(RunbookReference):
    hypothesis: DiagnosisText
    signals: tuple[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)], ...] = (
        Field(min_length=1)
    )
    checks: tuple[RunbookCheck, ...] = Field(min_length=1)


@dataclass(frozen=True)
class RunbookMatch:
    runbook: Runbook
    line_ids: tuple[str, ...]
    score: int


def load_runbooks() -> tuple[Runbook, ...]:
    directory = files("incident_triage").joinpath("data", "runbooks")
    runbooks = tuple(
        Runbook.model_validate_json(entry.read_text(encoding="utf-8"))
        for entry in sorted(directory.iterdir(), key=lambda path: path.name)
        if entry.name.endswith(".json")
    )
    if not runbooks:
        raise RuntimeError("The bundled runbook catalog is empty.")
    return runbooks


def select_runbook(
    lines: list[Evidence], runbooks: tuple[Runbook, ...]
) -> RunbookMatch | AbstentionReason:
    normalized_lines = [(line.line_id, line.message.casefold()) for line in lines]
    candidates = []

    for runbook in runbooks:
        distinct_signals: set[str] = set()
        line_ids = []
        signals = [signal.casefold() for signal in runbook.signals]

        for line_id, message in normalized_lines:
            matches = {signal for signal in signals if signal in message}
            if matches:
                line_ids.append(line_id)
                distinct_signals.update(matches)

        if distinct_signals:
            candidates.append(RunbookMatch(runbook, tuple(line_ids), len(distinct_signals)))

    if not candidates:
        return "no_match"

    candidates.sort(key=lambda candidate: candidate.score, reverse=True)
    if len(candidates) > 1 and candidates[0].score == candidates[1].score:
        return "ambiguous_match"
    return candidates[0]
