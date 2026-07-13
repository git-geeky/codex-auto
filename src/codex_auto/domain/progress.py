"""Externally measurable progress evaluation."""

from codex_auto.domain.models import ValidationSummary


class ProgressEvaluator:
    def has_progress(self, before: ValidationSummary, after: ValidationSummary) -> bool:
        if after.failure_count < before.failure_count:
            return True
        if after.stage_index > before.stage_index:
            return True
        if before.failing_tests - after.failing_tests:
            return True
        if after.localized and not before.localized:
            return True
        if (
            before.metric is not None
            and after.metric is not None
            and before.metric_target is not None
            and after.metric_target == before.metric_target
        ):
            return abs(after.metric - after.metric_target) < abs(
                before.metric - before.metric_target
            )
        return False
