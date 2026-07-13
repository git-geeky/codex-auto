"""Typed domain enumerations."""

from enum import StrEnum


class RunState(StrEnum):
    CREATED = "created"
    PREFLIGHT = "preflight"
    SOURCE_SNAPSHOTTED = "source_snapshotted"
    WORKTREE_CREATING = "worktree_creating"
    WORKTREE_READY = "worktree_ready"
    BASELINE_RUNNING = "baseline_running"
    BASELINE_COMPLETE = "baseline_complete"
    ATTEMPT_PREPARING = "attempt_preparing"
    ATTEMPT_RUNNING = "attempt_running"
    ATTEMPT_INTERRUPTED = "attempt_interrupted"
    ATTEMPT_COMPLETE = "attempt_complete"
    VALIDATION_RUNNING = "validation_running"
    VALIDATION_COMPLETE = "validation_complete"
    ROUTING = "routing"
    REVIEW_PREPARING = "review_preparing"
    REVIEW_RUNNING = "review_running"
    REVIEW_COMPLETE = "review_complete"
    FINAL_VALIDATION = "final_validation"
    ACCEPTED = "accepted"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    FAILED = "failed"


class FailureClass(StrEnum):
    SUCCESS = "success"
    SUBSTANTIVE = "substantive"
    TRANSIENT = "transient"
    ENVIRONMENT = "environment"
    PERMISSIONS = "permissions"
    CREDENTIALS = "credentials"
    CONFIGURATION = "configuration"
    SPECIFICATION = "specification"
    POLICY_VIOLATION = "policy_violation"
    STALLED = "stalled"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class Lane(StrEnum):
    AUTO = "auto"
    MECHANICAL = "mechanical"
    STANDARD = "standard"
    BOUNDED_HARD = "bounded-hard"
    LATENCY = "latency"
    HIGH_RISK = "high-risk"


class DeepMode(StrEnum):
    SERIAL = "serial"
    PARALLEL = "parallel"


class ReasoningEffort(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"
    ULTRA = "ultra"


class ValidationPolicy(StrEnum):
    MUST_PASS = "must_pass"
    NO_REGRESSION = "no_regression"
    ADVISORY = "advisory"
    MANUAL = "manual"
