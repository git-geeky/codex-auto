import pytest

from codex_auto.domain.enums import RunState
from codex_auto.domain.state_machine import IllegalTransitionError, StateMachine


def test_state_machine_accepts_declared_transition() -> None:
    transition = StateMachine().transition(
        run_id="run-1",
        sequence=1,
        current=RunState.CREATED,
        target=RunState.PREFLIGHT,
        reason_code="preflight_started",
        reason="preflight checks started",
    )

    assert transition.previous is RunState.CREATED
    assert transition.next is RunState.PREFLIGHT
    assert transition.sequence == 1


def test_state_machine_rejects_undeclared_transition() -> None:
    with pytest.raises(IllegalTransitionError, match="CREATED -> ACCEPTED"):
        StateMachine().transition(
            run_id="run-1",
            sequence=1,
            current=RunState.CREATED,
            target=RunState.ACCEPTED,
            reason_code="invalid",
            reason="cannot accept before execution",
        )


def test_allows_created_to_blocked_for_preflight_resource_rejection() -> None:
    transition = StateMachine().transition(
        run_id="run-1",
        sequence=1,
        current=RunState.CREATED,
        target=RunState.BLOCKED,
        reason_code="repository_lock_unavailable",
        reason="repository is owned by another live run",
    )

    assert transition.next is RunState.BLOCKED
