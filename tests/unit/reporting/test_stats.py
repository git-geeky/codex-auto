from codex_auto.reporting.stats import summarize_runs


def test_stats_distinguish_lane_tier_repair_rescue_deep_usage_and_time() -> None:
    records = [
        (
            {"state": "accepted", "repository": "C:/repo-a"},
            {
                "lane": "standard",
                "effective_model_effort_sequence": [
                    {"model": "gpt-5.6-luna", "effort": "high"},
                    {"model": "gpt-5.6-luna", "effort": "high"},
                    {"model": "gpt-5.6-sol", "effort": "max"},
                ],
                "reviewer_findings": [{"decision": "accept"}],
                "routing_decisions": [{"decision": "repair"}],
                "failure_classifications": ["substantive"],
                "policy_findings": [{"findings": [{"code": "high_risk_path", "blocking": False}]}],
                "usage": {"total_tokens": 120},
                "timing": {"total_wall_clock_seconds": 3.5},
            },
        )
    ]

    summary = summarize_runs(records)

    assert summary["runs"] == 1
    assert summary["accepted"] == 1
    assert summary["by_lane"]["standard"] == {"runs": 1, "accepted": 1}
    assert summary["by_repository"]["C:/repo-a"] == {"runs": 1, "accepted": 1}
    assert summary["by_risk_class"]["high-risk"] == {"runs": 1, "accepted": 1}
    assert summary["same_tier_repair"] == {"runs": 1, "accepted": 1}
    assert summary["luna_to_sol_rescue"] == {"runs": 1, "accepted": 1}
    assert summary["luna_to_sol_rescue_rate"] == 1.0
    assert summary["deep_outcomes"]["max"] == {"runs": 1, "accepted": 1}
    assert summary["tokens_per_accepted_result"] == 120
    assert summary["seconds_per_accepted_result"] == 3.5
    assert summary["reviewer_rejection_rate"] == 0.0
    assert summary["environment_blocker_rate"] == 0.0
