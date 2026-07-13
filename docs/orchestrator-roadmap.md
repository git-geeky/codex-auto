# Orchestrator roadmap

## n8n

Future outer control plane for triggers, forms, schedules, notifications, approvals, and GitHub/Slack/email integration. It must call a stable API or queue and must not own Codex processes or routing decisions.

## Prefect

Possible wrapper for scheduling, workers, dashboards, and moderate concurrency. Prefect retries must not repeat substantive Codex attempts; the core router remains authoritative.

## Temporal

Possible durable multi-machine backend for long-running recovery, cancellation, and event history. One substantive execution maps to one routing decision; activity retry policy must not create unlimited model retries.

## LangGraph

Not a version-one dependency. If added later, graph edges call deterministic Python routing functions and never an LLM router.

## CrewAI and AutoGPT

Not appropriate as the core escalation controller. They may exist outside the core for unrelated collaboration but cannot own retry, validation, or model selection.
