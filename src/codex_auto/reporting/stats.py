"""Aggregate routing, outcome, usage, and timing telemetry from final reports."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def summarize_runs(records: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    accepted = 0
    by_lane: dict[str, dict[str, int]] = defaultdict(lambda: {"runs": 0, "accepted": 0})
    by_repository: dict[str, dict[str, int]] = defaultdict(lambda: {"runs": 0, "accepted": 0})
    by_risk_class: dict[str, dict[str, int]] = defaultdict(lambda: {"runs": 0, "accepted": 0})
    by_initial_tier: dict[str, dict[str, int]] = defaultdict(lambda: {"runs": 0, "accepted": 0})
    by_model_effort: dict[str, dict[str, int]] = defaultdict(
        lambda: {"attempts": 0, "accepted_runs": 0}
    )
    same_tier_repair = {"runs": 0, "accepted": 0}
    luna_to_sol = {"runs": 0, "accepted": 0}
    deep = {
        "max": {"runs": 0, "accepted": 0},
        "ultra": {"runs": 0, "accepted": 0},
    }
    reviewer_rejections = 0
    environment_blockers = 0
    sol_high_rescue = {"runs": 0, "accepted": 0}
    terra_latency_seconds: list[float] = []
    accepted_tokens = 0
    accepted_seconds = 0.0
    for run, report in records:
        is_accepted = str(run.get("state")) == "accepted"
        accepted += int(is_accepted)
        lane = str(report.get("lane") or "unknown")
        by_lane[lane]["runs"] += 1
        by_lane[lane]["accepted"] += int(is_accepted)
        repository = str(run.get("repository") or report.get("repository") or "unknown")
        by_repository[repository]["runs"] += 1
        by_repository[repository]["accepted"] += int(is_accepted)
        risk_class = _risk_class(lane, report)
        by_risk_class[risk_class]["runs"] += 1
        by_risk_class[risk_class]["accepted"] += int(is_accepted)
        route = report.get("effective_model_effort_sequence", [])
        if not isinstance(route, list):
            route = []
        if route and isinstance(route[0], dict):
            initial = f"{route[0].get('model', 'unknown')}/{route[0].get('effort', 'unknown')}"
            by_initial_tier[initial]["runs"] += 1
            by_initial_tier[initial]["accepted"] += int(is_accepted and len(route) == 1)
        routing_decisions = report.get("routing_decisions", [])
        repaired = isinstance(routing_decisions, list) and any(
            isinstance(decision, dict) and decision.get("decision") == "repair"
            for decision in routing_decisions
        )
        if repaired:
            same_tier_repair["runs"] += 1
            same_tier_repair["accepted"] += int(is_accepted)
        for item in route:
            if not isinstance(item, dict):
                continue
            tier = f"{item.get('model', 'unknown')}/{item.get('effort', 'unknown')}"
            by_model_effort[tier]["attempts"] += 1
        for tier in {
            f"{item.get('model', 'unknown')}/{item.get('effort', 'unknown')}"
            for item in route
            if isinstance(item, dict)
        }:
            by_model_effort[tier]["accepted_runs"] += int(is_accepted)
        models = [item.get("model") for item in route if isinstance(item, dict)]
        if any(model == "gpt-5.6-luna" for model in models) and any(
            model == "gpt-5.6-sol" for model in models
        ):
            luna_to_sol["runs"] += 1
            luna_to_sol["accepted"] += int(is_accepted)
        for effort in ("max", "ultra"):
            if any(
                isinstance(item, dict)
                and item.get("model") == "gpt-5.6-sol"
                and item.get("effort") == effort
                for item in route
            ):
                deep[effort]["runs"] += 1
                deep[effort]["accepted"] += int(is_accepted)
        if len(route) > 1 and any(
            isinstance(item, dict)
            and item.get("model") == "gpt-5.6-sol"
            and item.get("effort") == "high"
            for item in route[1:]
        ):
            sol_high_rescue["runs"] += 1
            sol_high_rescue["accepted"] += int(is_accepted)
        reviews = report.get("reviewer_findings", [])
        if isinstance(reviews, list):
            reviewer_rejections += sum(
                1
                for review in reviews
                if isinstance(review, dict) and review.get("decision") != "accept"
            )
        failures = report.get("failure_classifications", [])
        if isinstance(failures, list) and "environment" in failures:
            environment_blockers += 1
        if is_accepted:
            usage = report.get("usage", {})
            timing = report.get("timing", {})
            if isinstance(usage, dict):
                accepted_tokens += int(usage.get("total_tokens", 0))
            if isinstance(timing, dict):
                accepted_seconds += float(timing.get("total_wall_clock_seconds", 0))
        if route and isinstance(route[0], dict) and route[0].get("model") == "gpt-5.6-terra":
            timing = report.get("timing", {})
            if isinstance(timing, dict):
                terra_latency_seconds.append(float(timing.get("total_wall_clock_seconds", 0)))
    return {
        "runs": len(records),
        "accepted": accepted,
        "acceptance_rate": accepted / len(records) if records else 0.0,
        "by_lane": dict(sorted(by_lane.items())),
        "by_repository": dict(sorted(by_repository.items())),
        "by_risk_class": dict(sorted(by_risk_class.items())),
        "by_initial_tier": dict(sorted(by_initial_tier.items())),
        "by_model_effort": dict(sorted(by_model_effort.items())),
        "same_tier_repair": same_tier_repair,
        "luna_to_sol_rescue": luna_to_sol,
        "luna_to_sol_rescue_rate": _accepted_rate(luna_to_sol),
        "sol_high_rescue": sol_high_rescue,
        "sol_high_rescue_rate": _accepted_rate(sol_high_rescue),
        "terra_latency_seconds_average": (
            sum(terra_latency_seconds) / len(terra_latency_seconds)
            if terra_latency_seconds
            else None
        ),
        "deep_outcomes": deep,
        "reviewer_rejections": reviewer_rejections,
        "reviewer_rejection_rate": reviewer_rejections / len(records) if records else 0.0,
        "environment_blockers": environment_blockers,
        "environment_blocker_rate": environment_blockers / len(records) if records else 0.0,
        "tokens_per_accepted_result": accepted_tokens / accepted if accepted else None,
        "seconds_per_accepted_result": accepted_seconds / accepted if accepted else None,
    }


def _accepted_rate(metric: dict[str, int]) -> float | None:
    return metric["accepted"] / metric["runs"] if metric["runs"] else None


def _risk_class(lane: str, report: dict[str, Any]) -> str:
    if lane == "high-risk":
        return "high-risk"
    policies = report.get("policy_findings", [])
    if isinstance(policies, list):
        for phase in policies:
            if not isinstance(phase, dict):
                continue
            findings = phase.get("findings", [])
            if isinstance(findings, list) and any(
                isinstance(finding, dict) and finding.get("code") == "high_risk_path"
                for finding in findings
            ):
                return "high-risk"
    return "standard-risk"
