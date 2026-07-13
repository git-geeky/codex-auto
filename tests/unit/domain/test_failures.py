from codex_auto.domain.enums import FailureClass
from codex_auto.domain.failures import FailureClassifier
from codex_auto.domain.models import FailureEvidence


def test_credentials_and_permissions_stop_classes_take_precedence() -> None:
    classifier = FailureClassifier()
    assert classifier.classify(FailureEvidence(credentials_error=True)) is FailureClass.CREDENTIALS
    assert classifier.classify(FailureEvidence(permission_error=True)) is FailureClass.PERMISSIONS


def test_validation_failure_after_model_work_is_substantive() -> None:
    evidence = FailureEvidence(
        process_started=True,
        candidate_changed=True,
        validation_failed=True,
        exit_code=0,
    )
    assert FailureClassifier().classify(evidence) is FailureClass.SUBSTANTIVE


def test_unknown_before_process_start_biases_to_environment() -> None:
    assert FailureClassifier().classify(FailureEvidence()) is FailureClass.ENVIRONMENT


def test_success_requires_clean_process_and_validation() -> None:
    evidence = FailureEvidence(process_started=True, exit_code=0)
    assert FailureClassifier().classify(evidence) is FailureClass.SUCCESS
