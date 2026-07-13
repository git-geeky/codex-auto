"""Typed routing decisions."""

from dataclasses import dataclass
from enum import StrEnum

from codex_auto.domain.models import ModelSelection


class DecisionKind(StrEnum):
    START = "start"
    RETRY_TRANSIENT = "retry_transient"
    REPAIR = "repair"
    ESCALATE = "escalate"
    ACCEPT = "accept"
    REVIEW = "review"
    HUMAN_REVIEW = "human_review"
    BLOCK = "block"


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    kind: DecisionKind
    reason: str
    selection: ModelSelection | None = None
