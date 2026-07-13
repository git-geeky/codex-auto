"""Baseline-to-candidate validation comparison."""

from __future__ import annotations

from dataclasses import dataclass

from codex_auto.domain.enums import ValidationPolicy
from codex_auto.validation.result import ValidationResult


@dataclass(frozen=True, slots=True)
class ValidationComparison:
    accepted: bool
    new_failures: tuple[str, ...]
    resolved_failures: tuple[str, ...]
    reason: str


def compare_validation(
    baseline: ValidationResult, candidate: ValidationResult
) -> ValidationComparison:
    if baseline.name != candidate.name:
        raise ValueError("baseline and candidate step names differ")
    if candidate.policy is ValidationPolicy.NO_REGRESSION:
        if not candidate.command_succeeded and not candidate.failure_ids:
            return ValidationComparison(
                False,
                ("<unclassified-command-failure>",),
                (),
                "candidate command failed without stable failure identifiers",
            )
        baseline_failures = set(baseline.failure_ids)
        candidate_failures = set(candidate.failure_ids)
        new = tuple(sorted(candidate_failures - baseline_failures))
        resolved = tuple(sorted(baseline_failures - candidate_failures))
        worsened_count = len(candidate_failures) > len(baseline_failures)
        accepted = not new and not worsened_count
        reason = "no new failures" if accepted else "candidate introduced regressions"
        return ValidationComparison(accepted, new, resolved, reason)
    if candidate.policy is ValidationPolicy.MUST_PASS:
        return ValidationComparison(
            candidate.command_succeeded,
            candidate.failure_ids if not candidate.command_succeeded else (),
            (),
            "must-pass command succeeded" if candidate.command_succeeded else "must-pass failed",
        )
    if candidate.policy is ValidationPolicy.ADVISORY:
        return ValidationComparison(True, (), (), "advisory result recorded")
    return ValidationComparison(False, (), (), "manual disposition required")
