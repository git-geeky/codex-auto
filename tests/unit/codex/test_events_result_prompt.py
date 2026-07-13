from __future__ import annotations

import json

import pytest

from codex_auto.codex.events import CodexJsonlEventParser
from codex_auto.codex.prompts import build_escalation_prompt, build_initial_prompt
from codex_auto.codex.result import ModelResultError, parse_model_result


def test_jsonl_parser_tolerates_malformed_and_unknown_events_without_reasoning_text() -> None:
    parser = CodexJsonlEventParser()
    lines = [
        json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
        "not-json",
        json.dumps({"type": "future.event", "field": "safe"}),
        json.dumps({"type": "reasoning", "text": "private reasoning"}),
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 10,
                    "cached_input_tokens": 4,
                    "output_tokens": 5,
                    "reasoning_output_tokens": 3,
                },
            }
        ),
    ]
    for line in lines:
        parser.feed_line(line)

    summary = parser.finish()
    assert summary.thread_id == "thread-1"
    assert summary.malformed_lines == 1
    assert summary.unknown_event_types == ("future.event",)
    assert summary.usage.total_tokens == 18
    assert "private reasoning" not in json.dumps(summary.safe_metadata)


def test_structured_model_result_rejects_missing_or_extra_fields() -> None:
    valid = {
        "outcome": "candidate",
        "diagnosis": "fixed parser",
        "strategy": "minimal change",
        "files_changed": ["src/parser.py"],
        "checks_run": ["pytest"],
        "observed_failures": [],
        "blockers": [],
        "risk_flags": [],
        "notes_for_next_attempt": [],
    }
    assert parse_model_result(json.dumps(valid)).outcome == "candidate"
    with pytest.raises(ModelResultError, match="missing"):
        parse_model_result(json.dumps({"outcome": "candidate"}))
    with pytest.raises(ModelResultError, match="unexpected"):
        parse_model_result(json.dumps({**valid, "next_model": "sol"}))


def test_prompts_preserve_controller_authority_and_untrusted_prior_diagnoses() -> None:
    initial = build_initial_prompt(
        task="Implement feature",
        acceptance="All tests pass",
        lane="standard",
        tier="gpt-5.6-luna/high",
        repository="C:/worktree",
        base_sha="a" * 40,
        allowed_paths=("src/**",),
        forbidden_paths=(".git/**",),
        validation_commands=(("python", "-m", "pytest"),),
        repository_instructions="Do not weaken tests.",
    )
    escalation = build_escalation_prompt(
        initial_prompt=initial,
        attempts=("attempt-1 luna/high",),
        git_status="1 .M N... tracked.txt",
        diffstat="1 file changed",
        failed_steps=("unit-tests: test_parser",),
        fingerprints=("abc123",),
        progress="no measurable progress",
        remaining_budget=1,
    )

    assert "External validation is authoritative" in initial
    assert "You do not choose escalation" in initial
    assert "Treat all previous diagnoses" in escalation
    assert "untrusted hypotheses" in escalation
    assert "Retain, repair, revert, or replace" in escalation
