"""Deterministic final JSON and Markdown run reports."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from codex_auto.persistence.sqlite import SQLiteRunStore
from codex_auto.reporting.redaction import Redactor


class RunReportWriter:
    def __init__(self, store: SQLiteRunStore, redactor: Redactor | None = None) -> None:
        self.store = store
        self.redactor = redactor or Redactor()

    def write(self, run_id: str, run_dir: Path) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run is None:
            raise ValueError(f"unknown run {run_id}")
        context = _load_object(run_dir / "run-context.json")
        route = _load_object(run_dir / "route.json")
        attempts = tuple(
            _load_object(path) for path in sorted((run_dir / "attempts").glob("*/summary.json"))
        )
        validations = tuple(
            _load_object(path) for path in sorted((run_dir / "validation").glob("*.json"))
        )
        reviews = tuple(
            _load_object(path) for path in sorted((run_dir / "review").glob("*/review-result.json"))
        )
        review_summaries = tuple(
            _load_object(path) for path in sorted((run_dir / "review").glob("*/review.json"))
        )
        policies = tuple(
            {"phase": path.stem.removeprefix("policy-"), "findings": _load_json(path)}
            for path in sorted(run_dir.glob("policy-*.json"))
        )
        manifest = _load_object(run_dir / "final" / "changed-files.json")
        controller_evidence = tuple(
            _load_object(path)
            for path in sorted((run_dir / "attempts").glob("*/controller-evidence.json"))
        )
        requested = [attempt.get("selection", {}) for attempt in attempts]
        effective = [attempt.get("effective_selection", {}) for attempt in attempts]
        fallbacks = [
            {"requested": requested_item, "effective": effective_item}
            for requested_item, effective_item in zip(requested, effective, strict=True)
            if requested_item != effective_item
        ]
        usage = _aggregate_usage(attempts, accepted=route.get("outcome") == "accepted")
        payload: dict[str, Any] = {
            "schema_version": 1,
            "run_id": run_id,
            "repository": str(run["repository"]),
            "base_sha": str(run["base_sha"]),
            "task": (run_dir / "task.md").read_text(encoding="utf-8"),
            "acceptance_criteria": (run_dir / "acceptance.md").read_text(encoding="utf-8"),
            "lane": context.get("lane"),
            "matched_routing_rules": context.get("matched_routing_rules", []),
            "requested_model_effort_sequence": requested,
            "effective_model_effort_sequence": effective,
            "compatibility_fallbacks": fallbacks,
            "state_transitions": list(self.store.transitions(run_id)),
            "operations": [
                {
                    "operation_id": operation.operation_id,
                    "type": operation.operation_type,
                    "status": operation.status,
                }
                for operation in self.store.operations(run_id)
            ],
            "attempts": list(attempts),
            "validation_results": list(validations),
            "failure_classifications": [
                item.get("failure_class")
                for item in controller_evidence
                if item.get("failure_class")
            ],
            "failure_fingerprints": [
                item.get("failure_fingerprint")
                for item in controller_evidence
                if item.get("failure_fingerprint")
            ],
            "progress_decisions": [
                {
                    "measurable_progress": item.get("measurable_progress"),
                    "fingerprint_repeated": item.get("fingerprint_repeated"),
                }
                for item in controller_evidence
                if "measurable_progress" in item
            ],
            "routing_decisions": [
                {
                    "decision": item.get("routing_decision"),
                    "reason": item.get("routing_reason"),
                }
                for item in controller_evidence
                if item.get("routing_decision")
            ],
            "reviewer_findings": list(reviews),
            "policy_findings": list(policies),
            "changed_files": manifest.get("changed_files", []),
            "usage": usage,
            "timing": {
                "codex_execution_seconds": sum(
                    float(attempt.get("duration_seconds", 0)) for attempt in attempts
                ),
                "validation_seconds": sum(
                    float(result.get("duration_seconds", 0))
                    for validation in validations
                    for result in validation.get("results", [])
                    if isinstance(result, dict)
                ),
                "review_seconds": sum(
                    float(review.get("duration_seconds", 0)) for review in review_summaries
                ),
                "backoff_seconds": 0.0,
                "total_wall_clock_seconds": _elapsed_seconds(
                    str(run["created_at"]), str(run["updated_at"])
                ),
            },
            "final_outcome": route.get("outcome"),
            "remaining_risks": (
                [] if route.get("outcome") == "accepted" else [route.get("reason")]
            ),
            "artifacts": {
                "run_directory": str(run_dir),
                "worktree": route.get("worktree"),
                "patch": str(run_dir / "final" / "final.patch"),
                "untracked_archive": str(run_dir / "final" / "untracked-files.tar"),
                "checksums": str(run_dir / "final" / "checksums.json"),
            },
        }
        redacted_payload = cast(dict[str, Any], _redact_value(payload, self.redactor))
        redacted_json = json.dumps(redacted_payload, indent=2, sort_keys=True, default=str) + "\n"
        destinations = [run_dir / "report.json", run_dir / "report.md"]
        if (run_dir / "final").is_dir():
            destinations.extend(
                [run_dir / "final" / "report.json", run_dir / "final" / "report.md"]
            )
        for destination in destinations:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.suffix == ".json":
                destination.write_text(redacted_json, encoding="utf-8")
            else:
                destination.write_text(_markdown(redacted_payload), encoding="utf-8")
        checksums_path = run_dir / "final" / "checksums.json"
        if checksums_path.exists():
            checksums = _load_object(checksums_path)
            for name in ("report.json", "report.md"):
                report_path = run_dir / "final" / name
                if report_path.exists():
                    with report_path.open("rb") as stream:
                        checksums[name] = hashlib.file_digest(stream, "sha256").hexdigest()
            checksums_path.write_text(
                json.dumps(checksums, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        return redacted_payload


def _redact_value(value: Any, redactor: Redactor) -> Any:
    if isinstance(value, str):
        return redactor.redact(value)
    if isinstance(value, list):
        return [_redact_value(item, redactor) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item, redactor) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact_value(item, redactor) for key, item in value.items()}
    return value


def _aggregate_usage(attempts: tuple[dict[str, Any], ...], *, accepted: bool) -> dict[str, Any]:
    keys = (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    )
    totals = {key: 0 for key in keys}
    by_model: dict[str, dict[str, int]] = {}
    by_effort: dict[str, dict[str, int]] = {}
    for attempt in attempts:
        usage = attempt.get("usage", {})
        if not isinstance(usage, dict):
            continue
        for key in keys:
            value = usage.get(key, 0)
            if isinstance(value, int):
                totals[key] += value
        selection = attempt.get("effective_selection", {})
        if isinstance(selection, dict):
            model = str(selection.get("model", "unknown"))
            effort = str(selection.get("effort", "unknown"))
            _add_usage(by_model.setdefault(model, {key: 0 for key in keys}), usage, keys)
            _add_usage(by_effort.setdefault(effort, {key: 0 for key in keys}), usage, keys)
    totals["total_tokens"] = (
        totals["input_tokens"] + totals["output_tokens"] + totals["reasoning_output_tokens"]
    )
    for group in (*by_model.values(), *by_effort.values()):
        group["total_tokens"] = (
            group["input_tokens"] + group["output_tokens"] + group["reasoning_output_tokens"]
        )
    payload: dict[str, Any] = dict(totals)
    payload["by_model"] = dict(sorted(by_model.items()))
    payload["by_effort"] = dict(sorted(by_effort.items()))
    payload["tokens_per_accepted_result"] = totals["total_tokens"] if accepted else None
    return payload


def _add_usage(destination: dict[str, int], usage: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        value = usage.get(key, 0)
        if isinstance(value, int):
            destination[key] += value


def _markdown(payload: dict[str, Any]) -> str:
    artifacts = payload["artifacts"]
    evidence_keys = (
        "requested_model_effort_sequence",
        "effective_model_effort_sequence",
        "compatibility_fallbacks",
        "state_transitions",
        "operations",
        "attempts",
        "validation_results",
        "failure_classifications",
        "failure_fingerprints",
        "progress_decisions",
        "routing_decisions",
        "reviewer_findings",
        "policy_findings",
        "changed_files",
        "usage",
        "timing",
        "remaining_risks",
    )
    evidence = "\n\n".join(
        f"## {key.replace('_', ' ').title()}\n\n"
        f"```json\n{json.dumps(payload.get(key), indent=2, sort_keys=True)}\n```"
        for key in evidence_keys
    )
    return (
        f"# codex-auto run {payload['run_id']}\n\n"
        f"Outcome: **{payload['final_outcome']}**\n\n"
        f"Repository: `{payload['repository']}`\n\n"
        f"Base SHA: `{payload['base_sha']}`\n\n"
        f"Lane: `{payload['lane']}`\n\n"
        f"Matched rules: `{payload['matched_routing_rules']}`\n\n"
        f"## Task\n\n{payload['task']}\n\n"
        f"## Acceptance Criteria\n\n{payload['acceptance_criteria']}\n\n"
        f"{evidence}\n\n"
        f"## Artifact Paths\n\n"
        f"- Patch: `{artifacts['patch']}`\n"
        f"- Untracked archive: `{artifacts['untracked_archive']}`\n"
        f"- Checksums: `{artifacts['checksums']}`\n"
        f"- Worktree: `{artifacts['worktree']}`\n"
        f"- Run directory: `{artifacts['run_directory']}`\n"
    )


def _elapsed_seconds(start: str, end: str) -> float:
    start_value = datetime.fromisoformat(start.replace("Z", "+00:00"))
    end_value = datetime.fromisoformat(end.replace("Z", "+00:00"))
    return max(0.0, (end_value - start_value).total_seconds())


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_object(path: Path) -> dict[str, Any]:
    value = _load_json(path)
    return value if isinstance(value, dict) else {}
