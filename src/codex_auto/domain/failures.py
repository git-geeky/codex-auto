"""Deterministic failure classification."""

from codex_auto.domain.enums import FailureClass
from codex_auto.domain.models import FailureEvidence


class FailureClassifier:
    def classify(self, evidence: FailureEvidence) -> FailureClass:
        if evidence.cancelled:
            return FailureClass.CANCELLED
        if evidence.credentials_error:
            return FailureClass.CREDENTIALS
        if evidence.permission_error:
            return FailureClass.PERMISSIONS
        if evidence.configuration_error:
            return FailureClass.CONFIGURATION
        if evidence.specification_error:
            return FailureClass.SPECIFICATION
        if evidence.environment_error or evidence.sandbox_unavailable:
            return FailureClass.ENVIRONMENT
        if evidence.transient_error:
            return FailureClass.TRANSIENT
        if evidence.policy_violation:
            return FailureClass.POLICY_VIOLATION
        if evidence.stalled:
            return FailureClass.STALLED
        if evidence.validation_failed:
            return FailureClass.SUBSTANTIVE
        if evidence.process_started and evidence.exit_code == 0:
            return FailureClass.SUCCESS
        if not evidence.process_started:
            return FailureClass.ENVIRONMENT
        if evidence.candidate_changed:
            return FailureClass.SUBSTANTIVE
        return FailureClass.UNKNOWN
