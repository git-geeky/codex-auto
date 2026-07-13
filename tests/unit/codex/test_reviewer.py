from __future__ import annotations

import json

import pytest

from codex_auto.codex.reviewer import ReviewResultError, parse_review_result


def review_payload(decision: str) -> dict[str, object]:
    return {
        "decision": decision,
        "findings": [
            {
                "severity": "high",
                "confidence": "high",
                "file": "candidate.txt",
                "line": 1,
                "title": "candidate issue",
                "evidence": "concrete evidence",
                "recommended_action": "repair candidate",
            }
        ],
        "acceptance_criteria_checked": ["candidate validator passes"],
        "remaining_risks": [],
    }


def test_review_result_requires_concrete_schema() -> None:
    parsed = parse_review_result(json.dumps(review_payload("repair")))
    assert parsed.decision == "repair"
    assert parsed.findings[0].file == "candidate.txt"

    payload = review_payload("repair")
    payload["findings"] = [{"title": "missing evidence"}]
    with pytest.raises(ReviewResultError):
        parse_review_result(json.dumps(payload))


def test_review_accept_cannot_include_high_severity_finding() -> None:
    with pytest.raises(ReviewResultError, match="accept decision conflicts"):
        parse_review_result(json.dumps(review_payload("accept")))
