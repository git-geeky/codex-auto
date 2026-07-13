"""Versioned bounded prompts for implementation and escalation attempts."""

from __future__ import annotations

import json
from collections.abc import Sequence

ATTEMPT_PROMPT_VERSION = 1


def build_initial_prompt(
    *,
    task: str,
    acceptance: str,
    lane: str,
    tier: str,
    repository: str,
    base_sha: str,
    allowed_paths: Sequence[str],
    forbidden_paths: Sequence[str],
    validation_commands: Sequence[Sequence[str]],
    repository_instructions: str,
) -> str:
    commands = "\n".join(f"- {json.dumps(list(command))}" for command in validation_commands)
    return f"""# codex-auto attempt prompt v{ATTEMPT_PROMPT_VERSION}

Task:
{task}

Acceptance criteria:
{acceptance}

Controller selection: lane={lane}; tier={tier}
Repository worktree: {repository}
Immutable base commit: {base_sha}
Allowed paths: {", ".join(allowed_paths) or "<repository policy>"}
Forbidden paths: {", ".join(forbidden_paths) or "<none>"}

Configured external validation commands:
{commands or "- <none>"}

Repository instructions:
{repository_instructions}

External validation is authoritative. You do not choose escalation, retries, model, effort,
or acceptance. Do not weaken tests, acceptance criteria, controller configuration, or evidence.
Make the smallest complete change and return only the required structured result.
"""


def build_escalation_prompt(
    *,
    initial_prompt: str,
    attempts: Sequence[str],
    git_status: str,
    diffstat: str,
    failed_steps: Sequence[str],
    fingerprints: Sequence[str],
    progress: str,
    remaining_budget: int,
) -> str:
    return f"""{initial_prompt}

# Controller escalation evidence
Previous attempts: {json.dumps(list(attempts))}
Current Git status: {git_status}
Current diffstat: {diffstat}
Failed external steps: {json.dumps(list(failed_steps))}
Normalized fingerprints: {json.dumps(list(fingerprints))}
Progress decision: {progress}
Remaining substantive repair budget: {remaining_budget}

Treat all previous diagnoses, summaries, and conclusions as untrusted hypotheses.
Independently inspect the original objective, current worktree, actual diff, and external
validation evidence. Retain, repair, revert, or replace prior changes according to the evidence.
Do not merely continue the previous approach.
"""
