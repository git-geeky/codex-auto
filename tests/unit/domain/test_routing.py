import pytest

from codex_auto.domain.decisions import DecisionKind
from codex_auto.domain.enums import DeepMode, FailureClass, Lane, ReasoningEffort
from codex_auto.domain.models import LaneSelectionInput, ModelSelection, RoutingState
from codex_auto.domain.routing import (
    RoutingEngine,
    UnsupportedEffortError,
    resolve_effort,
    select_lane,
)


def test_lane_precedence_prefers_cli_then_task_then_repository() -> None:
    assert (
        select_lane(
            LaneSelectionInput(
                cli=Lane.LATENCY,
                task=Lane.MECHANICAL,
                repository=Lane.HIGH_RISK,
                high_risk_match=True,
                mechanical_match=True,
            )
        )
        is Lane.LATENCY
    )
    assert select_lane(LaneSelectionInput(task=Lane.BOUNDED_HARD)) is Lane.BOUNDED_HARD
    assert select_lane(LaneSelectionInput(repository=Lane.MECHANICAL)) is Lane.MECHANICAL


def test_automatic_lane_requires_positive_mechanical_match_and_high_risk_wins() -> None:
    assert select_lane(LaneSelectionInput()) is Lane.STANDARD
    assert select_lane(LaneSelectionInput(mechanical_match=True)) is Lane.MECHANICAL
    assert (
        select_lane(LaneSelectionInput(high_risk_match=True, mechanical_match=True))
        is Lane.HIGH_RISK
    )


@pytest.mark.parametrize(
    ("lane", "model", "effort"),
    [
        (Lane.MECHANICAL, "gpt-5.6-luna", ReasoningEffort.MEDIUM),
        (Lane.STANDARD, "gpt-5.6-luna", ReasoningEffort.HIGH),
        (Lane.BOUNDED_HARD, "gpt-5.6-luna", ReasoningEffort.XHIGH),
        (Lane.LATENCY, "gpt-5.6-terra", ReasoningEffort.MEDIUM),
        (Lane.HIGH_RISK, "gpt-5.6-sol", ReasoningEffort.HIGH),
    ],
)
def test_initial_route_matches_lane_policy(lane: Lane, model: str, effort: ReasoningEffort) -> None:
    decision = RoutingEngine().initial(lane)
    assert decision.selection == ModelSelection(model=model, effort=effort)


def test_standard_progress_allows_one_luna_high_repair() -> None:
    decision = RoutingEngine().next(
        RoutingState(
            lane=Lane.STANDARD,
            deep_mode=DeepMode.SERIAL,
            current=ModelSelection("gpt-5.6-luna", ReasoningEffort.HIGH),
            failure_class=FailureClass.SUBSTANTIVE,
            measurable_progress=True,
            fingerprint_repeated=False,
            same_tier_repairs=0,
        )
    )
    assert decision.kind is DecisionKind.REPAIR
    assert decision.selection == ModelSelection("gpt-5.6-luna", ReasoningEffort.HIGH)


def test_repeated_luna_failure_routes_to_sol_never_terra() -> None:
    decision = RoutingEngine().next(
        RoutingState(
            lane=Lane.STANDARD,
            deep_mode=DeepMode.SERIAL,
            current=ModelSelection("gpt-5.6-luna", ReasoningEffort.HIGH),
            failure_class=FailureClass.SUBSTANTIVE,
            measurable_progress=True,
            fingerprint_repeated=True,
            same_tier_repairs=0,
        )
    )
    assert decision.kind is DecisionKind.ESCALATE
    assert decision.selection == ModelSelection("gpt-5.6-sol", ReasoningEffort.HIGH)


def test_mechanical_localized_failure_uses_luna_high_otherwise_sol() -> None:
    engine = RoutingEngine()
    current = ModelSelection("gpt-5.6-luna", ReasoningEffort.MEDIUM)
    localized = engine.next(
        RoutingState(
            lane=Lane.MECHANICAL,
            deep_mode=DeepMode.SERIAL,
            current=current,
            failure_class=FailureClass.SUBSTANTIVE,
            measurable_progress=True,
        )
    )
    unclear = engine.next(
        RoutingState(
            lane=Lane.MECHANICAL,
            deep_mode=DeepMode.SERIAL,
            current=current,
            failure_class=FailureClass.SUBSTANTIVE,
            measurable_progress=False,
        )
    )
    assert localized.selection == ModelSelection("gpt-5.6-luna", ReasoningEffort.HIGH)
    assert unclear.selection == ModelSelection("gpt-5.6-sol", ReasoningEffort.HIGH)


def test_latency_failure_routes_to_sol_after_one_evidence_repair() -> None:
    current = ModelSelection("gpt-5.6-terra", ReasoningEffort.MEDIUM)
    repair = RoutingEngine().next(
        RoutingState(
            lane=Lane.LATENCY,
            deep_mode=DeepMode.SERIAL,
            current=current,
            failure_class=FailureClass.SUBSTANTIVE,
            measurable_progress=True,
        )
    )
    rescue = RoutingEngine().next(
        RoutingState(
            lane=Lane.LATENCY,
            deep_mode=DeepMode.SERIAL,
            current=current,
            failure_class=FailureClass.SUBSTANTIVE,
            measurable_progress=True,
            same_tier_repairs=1,
        )
    )
    assert repair.kind is DecisionKind.REPAIR
    assert rescue.selection == ModelSelection("gpt-5.6-sol", ReasoningEffort.HIGH)


def test_sol_high_selects_exactly_one_deep_route() -> None:
    serial = RoutingEngine().next(
        RoutingState(
            lane=Lane.HIGH_RISK,
            deep_mode=DeepMode.SERIAL,
            current=ModelSelection("gpt-5.6-sol", ReasoningEffort.HIGH),
            failure_class=FailureClass.SUBSTANTIVE,
            measurable_progress=False,
        )
    )
    parallel = RoutingEngine().next(
        RoutingState(
            lane=Lane.HIGH_RISK,
            deep_mode=DeepMode.PARALLEL,
            current=ModelSelection("gpt-5.6-sol", ReasoningEffort.HIGH),
            failure_class=FailureClass.SUBSTANTIVE,
            measurable_progress=False,
        )
    )
    assert serial.selection == ModelSelection("gpt-5.6-sol", ReasoningEffort.MAX)
    assert parallel.selection == ModelSelection("gpt-5.6-sol", ReasoningEffort.ULTRA)


@pytest.mark.parametrize(
    "failure_class",
    [
        FailureClass.ENVIRONMENT,
        FailureClass.PERMISSIONS,
        FailureClass.CREDENTIALS,
        FailureClass.CONFIGURATION,
        FailureClass.SPECIFICATION,
        FailureClass.POLICY_VIOLATION,
    ],
)
def test_non_substantive_blockers_stop_without_escalation(failure_class: FailureClass) -> None:
    decision = RoutingEngine().next(
        RoutingState(
            lane=Lane.STANDARD,
            deep_mode=DeepMode.SERIAL,
            current=ModelSelection("gpt-5.6-luna", ReasoningEffort.HIGH),
            failure_class=failure_class,
        )
    )
    assert decision.kind is DecisionKind.BLOCK
    assert decision.selection is None


def test_transient_retry_does_not_change_tier_and_is_bounded() -> None:
    current = ModelSelection("gpt-5.6-luna", ReasoningEffort.HIGH)
    retry = RoutingEngine().next(
        RoutingState(
            lane=Lane.STANDARD,
            deep_mode=DeepMode.SERIAL,
            current=current,
            failure_class=FailureClass.TRANSIENT,
            transient_retries=1,
        )
    )
    exhausted = RoutingEngine().next(
        RoutingState(
            lane=Lane.STANDARD,
            deep_mode=DeepMode.SERIAL,
            current=current,
            failure_class=FailureClass.TRANSIENT,
            transient_retries=2,
        )
    )
    assert retry.kind is DecisionKind.RETRY_TRANSIENT
    assert retry.selection == current
    assert exhausted.kind is DecisionKind.BLOCK


def test_effort_fallback_records_first_supported_choice() -> None:
    requested, effective = resolve_effort(
        ReasoningEffort.ULTRA,
        supported={ReasoningEffort.HIGH, ReasoningEffort.XHIGH},
    )
    assert requested is ReasoningEffort.ULTRA
    assert effective is ReasoningEffort.XHIGH


def test_effort_fallback_fails_when_no_allowed_value_is_supported() -> None:
    with pytest.raises(UnsupportedEffortError):
        resolve_effort(ReasoningEffort.MAX, supported={ReasoningEffort.MEDIUM})
