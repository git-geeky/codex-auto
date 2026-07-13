"""Strict structured model-result parsing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class ModelResultError(ValueError):
    """A model result is missing, malformed, or violates the external schema."""


REQUIRED_FIELDS = {
    "outcome",
    "diagnosis",
    "strategy",
    "files_changed",
    "checks_run",
    "observed_failures",
    "blockers",
    "risk_flags",
    "notes_for_next_attempt",
}


@dataclass(frozen=True, slots=True)
class ModelResult:
    outcome: str
    diagnosis: str
    strategy: str
    files_changed: tuple[str, ...]
    checks_run: tuple[str, ...]
    observed_failures: tuple[str, ...]
    blockers: tuple[str, ...]
    risk_flags: tuple[str, ...]
    notes_for_next_attempt: tuple[str, ...]


def parse_model_result(raw: str) -> ModelResult:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ModelResultError(f"malformed model result: {error.msg}") from error
    if not isinstance(payload, dict):
        raise ModelResultError("model result must be a JSON object")
    fields = set(payload)
    missing = REQUIRED_FIELDS - fields
    unexpected = fields - REQUIRED_FIELDS
    if missing:
        raise ModelResultError(f"missing result fields: {', '.join(sorted(missing))}")
    if unexpected:
        raise ModelResultError(f"unexpected result fields: {', '.join(sorted(unexpected))}")
    outcome = _string(payload, "outcome")
    if outcome not in {"candidate", "blocked", "stalled"}:
        raise ModelResultError(f"invalid outcome {outcome}")
    return ModelResult(
        outcome=outcome,
        diagnosis=_string(payload, "diagnosis"),
        strategy=_string(payload, "strategy"),
        files_changed=_strings(payload, "files_changed"),
        checks_run=_strings(payload, "checks_run"),
        observed_failures=_strings(payload, "observed_failures"),
        blockers=_strings(payload, "blockers"),
        risk_flags=_strings(payload, "risk_flags"),
        notes_for_next_attempt=_strings(payload, "notes_for_next_attempt"),
    )


def _string(payload: dict[str, Any], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str):
        raise ModelResultError(f"{key} must be a string")
    return value


def _strings(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    value = payload[key]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ModelResultError(f"{key} must be an array of strings")
    return tuple(value)
