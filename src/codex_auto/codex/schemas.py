"""Maintained JSON Schemas supplied to Codex output files."""

from __future__ import annotations

from typing import Any

ATTEMPT_RESULT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "outcome",
        "diagnosis",
        "strategy",
        "files_changed",
        "checks_run",
        "observed_failures",
        "blockers",
        "risk_flags",
        "notes_for_next_attempt",
    ],
    "properties": {
        "outcome": {"enum": ["candidate", "blocked", "stalled"]},
        "diagnosis": {"type": "string"},
        "strategy": {"type": "string"},
        "files_changed": {"type": "array", "items": {"type": "string"}},
        "checks_run": {"type": "array", "items": {"type": "string"}},
        "observed_failures": {"type": "array", "items": {"type": "string"}},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "risk_flags": {"type": "array", "items": {"type": "string"}},
        "notes_for_next_attempt": {"type": "array", "items": {"type": "string"}},
    },
}

REVIEW_RESULT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "decision",
        "findings",
        "acceptance_criteria_checked",
        "remaining_risks",
    ],
    "properties": {
        "decision": {"enum": ["accept", "repair", "human_review"]},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "severity",
                    "confidence",
                    "file",
                    "line",
                    "title",
                    "evidence",
                    "recommended_action",
                ],
                "properties": {
                    "severity": {"enum": ["critical", "high", "medium", "low"]},
                    "confidence": {"enum": ["high", "medium", "low"]},
                    "file": {"type": "string"},
                    "line": {"type": "integer", "minimum": 0},
                    "title": {"type": "string"},
                    "evidence": {"type": "string"},
                    "recommended_action": {"type": "string"},
                },
            },
        },
        "acceptance_criteria_checked": {
            "type": "array",
            "items": {"type": "string"},
        },
        "remaining_risks": {"type": "array", "items": {"type": "string"}},
    },
}
