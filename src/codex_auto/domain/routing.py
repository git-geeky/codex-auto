"""Pure deterministic lane selection and retry/escalation policy."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from codex_auto.domain.decisions import DecisionKind, RoutingDecision
from codex_auto.domain.enums import DeepMode, FailureClass, Lane, ReasoningEffort
from codex_auto.domain.models import LaneSelectionInput, ModelSelection, RoutingState


class UnsupportedEffortError(ValueError):
    """Raised when an effort and all configured compatibility fallbacks are unsupported."""


INITIAL_SELECTIONS: Mapping[Lane, ModelSelection] = {
    Lane.MECHANICAL: ModelSelection("gpt-5.6-luna", ReasoningEffort.MEDIUM),
    Lane.STANDARD: ModelSelection("gpt-5.6-luna", ReasoningEffort.HIGH),
    Lane.BOUNDED_HARD: ModelSelection("gpt-5.6-luna", ReasoningEffort.XHIGH),
    Lane.LATENCY: ModelSelection("gpt-5.6-terra", ReasoningEffort.MEDIUM),
    Lane.HIGH_RISK: ModelSelection("gpt-5.6-sol", ReasoningEffort.HIGH),
}

DEFAULT_MECHANICAL_REPAIR = ModelSelection("gpt-5.6-luna", ReasoningEffort.HIGH)
DEFAULT_RESCUE = ModelSelection("gpt-5.6-sol", ReasoningEffort.HIGH)
DEFAULT_DEEP_SERIAL = ModelSelection("gpt-5.6-sol", ReasoningEffort.MAX)
DEFAULT_DEEP_PARALLEL = ModelSelection("gpt-5.6-sol", ReasoningEffort.ULTRA)

EFFORT_FALLBACKS: Mapping[ReasoningEffort, tuple[ReasoningEffort, ...]] = {
    ReasoningEffort.MAX: (ReasoningEffort.XHIGH, ReasoningEffort.HIGH),
    ReasoningEffort.ULTRA: (
        ReasoningEffort.MAX,
        ReasoningEffort.XHIGH,
        ReasoningEffort.HIGH,
    ),
}

STOP_CLASSES = {
    FailureClass.ENVIRONMENT,
    FailureClass.PERMISSIONS,
    FailureClass.CREDENTIALS,
    FailureClass.CONFIGURATION,
    FailureClass.SPECIFICATION,
    FailureClass.POLICY_VIOLATION,
    FailureClass.CANCELLED,
}


def select_lane(selection: LaneSelectionInput) -> Lane:
    for explicit in (selection.cli, selection.task, selection.repository):
        if explicit is not None and explicit is not Lane.AUTO:
            return explicit
    if selection.high_risk_match:
        return Lane.HIGH_RISK
    if selection.mechanical_match:
        return Lane.MECHANICAL
    return Lane.STANDARD


def resolve_effort(
    requested: ReasoningEffort,
    *,
    supported: Iterable[ReasoningEffort],
    fallbacks: Mapping[ReasoningEffort, tuple[ReasoningEffort, ...]] = EFFORT_FALLBACKS,
) -> tuple[ReasoningEffort, ReasoningEffort]:
    supported_set = frozenset(supported)
    if requested in supported_set:
        return requested, requested
    for candidate in fallbacks.get(requested, ()):
        if candidate in supported_set:
            return requested, candidate
    raise UnsupportedEffortError(f"no supported compatibility fallback for {requested.value}")


class RoutingEngine:
    def __init__(
        self,
        *,
        initial_selections: Mapping[Lane, ModelSelection] = INITIAL_SELECTIONS,
        mechanical_repair: ModelSelection = DEFAULT_MECHANICAL_REPAIR,
        rescue: ModelSelection = DEFAULT_RESCUE,
        deep_serial: ModelSelection = DEFAULT_DEEP_SERIAL,
        deep_parallel: ModelSelection = DEFAULT_DEEP_PARALLEL,
    ) -> None:
        self.initial_selections = dict(initial_selections)
        self.mechanical_repair = mechanical_repair
        self.rescue = rescue
        self.deep_serial = deep_serial
        self.deep_parallel = deep_parallel

    @classmethod
    def from_config(cls, models: Mapping[str, object]) -> RoutingEngine:
        def selection(name: str) -> ModelSelection:
            raw = models.get(name)
            if not isinstance(raw, Mapping):
                raise ValueError(f"models.{name} must be a table")
            return ModelSelection(
                str(raw.get("model")),
                ReasoningEffort(str(raw.get("effort"))),
            )

        initial = {
            Lane.MECHANICAL: selection("mechanical"),
            Lane.STANDARD: selection("standard"),
            Lane.BOUNDED_HARD: selection("bounded_hard"),
            Lane.LATENCY: selection("latency"),
            Lane.HIGH_RISK: selection("rescue"),
        }
        return cls(
            initial_selections=initial,
            mechanical_repair=selection("mechanical_repair"),
            rescue=selection("rescue"),
            deep_serial=selection("deep_serial"),
            deep_parallel=selection("deep_parallel"),
        )

    def initial(self, lane: Lane) -> RoutingDecision:
        effective_lane = Lane.STANDARD if lane is Lane.AUTO else lane
        selection = self.initial_selections[effective_lane]
        return RoutingDecision(
            DecisionKind.START, f"initial {effective_lane.value} lane", selection
        )

    def next(self, state: RoutingState) -> RoutingDecision:
        failure = state.failure_class
        if failure is FailureClass.SUCCESS:
            return RoutingDecision(DecisionKind.ACCEPT, "external validation succeeded")
        if failure in STOP_CLASSES:
            return RoutingDecision(DecisionKind.BLOCK, f"{failure.value} failures do not escalate")
        if failure is FailureClass.TRANSIENT:
            if state.transient_retries < state.max_transient_retries:
                return RoutingDecision(
                    DecisionKind.RETRY_TRANSIENT,
                    "bounded transient retry at the same tier",
                    state.current,
                )
            return RoutingDecision(DecisionKind.BLOCK, "transient retry budget exhausted")

        current = state.current
        repair_allowed = (
            state.measurable_progress
            and not state.fingerprint_repeated
            and state.same_tier_repairs < state.max_same_tier_repairs
        )

        if current in {
            self.initial_selections[Lane.MECHANICAL],
            self.initial_selections[Lane.STANDARD],
            self.initial_selections[Lane.BOUNDED_HARD],
            self.mechanical_repair,
        }:
            if (
                current == self.initial_selections[Lane.MECHANICAL]
                and state.lane is Lane.MECHANICAL
            ):
                if repair_allowed:
                    return RoutingDecision(
                        DecisionKind.ESCALATE,
                        "localized mechanical evidence permits Luna High",
                        self.mechanical_repair,
                    )
                return self._sol_rescue("unclear mechanical failure skips Luna repair")
            if (
                current
                in {
                    self.initial_selections[Lane.STANDARD],
                    self.mechanical_repair,
                }
                and repair_allowed
            ):
                return RoutingDecision(
                    DecisionKind.REPAIR,
                    "new external evidence permits one Luna High repair",
                    current,
                )
            return self._sol_rescue("Luna failure changes model family")

        if current == self.initial_selections[Lane.LATENCY]:
            if repair_allowed:
                return RoutingDecision(
                    DecisionKind.REPAIR,
                    "new external evidence permits one Terra repair",
                    current,
                )
            return self._sol_rescue("Terra failure routes to Sol High")

        if current == self.rescue:
            if repair_allowed:
                return RoutingDecision(
                    DecisionKind.REPAIR,
                    "new external evidence permits one Sol High repair",
                    current,
                )
            deep = self.deep_parallel if state.deep_mode is DeepMode.PARALLEL else self.deep_serial
            return RoutingDecision(
                DecisionKind.ESCALATE,
                f"select one {state.deep_mode.value} deep route",
                deep,
            )

        if current in {self.deep_serial, self.deep_parallel}:
            return RoutingDecision(
                DecisionKind.HUMAN_REVIEW,
                "deep route exhausted; do not cycle model combinations",
            )

        return RoutingDecision(DecisionKind.BLOCK, "unrecognized model tier")

    def _sol_rescue(self, reason: str) -> RoutingDecision:
        return RoutingDecision(
            DecisionKind.ESCALATE,
            reason,
            self.rescue,
        )
