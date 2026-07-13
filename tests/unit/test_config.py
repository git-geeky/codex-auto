from __future__ import annotations

from pathlib import Path

import pytest

from codex_auto.config import (
    DEFAULT_CONFIG,
    DEFAULT_ROUTER_TOML,
    ConfigError,
    load_effective_config,
    parse_config,
)
from codex_auto.paths import state_root


def test_config_rejects_unknown_top_level_with_exact_path() -> None:
    with pytest.raises(ConfigError, match=r"unknown top-level key: unexpected"):
        parse_config("[unexpected]\nvalue = 1\n", source="test.toml")


def test_config_rejects_unknown_nested_key_with_exact_path() -> None:
    with pytest.raises(ConfigError, match=r"unknown key: controller\.retry_forever"):
        parse_config("[controller]\nretry_forever = true\n", source="test.toml")


def test_config_precedence_and_origins_are_reported(tmp_path: Path) -> None:
    user = tmp_path / "user.toml"
    repository = tmp_path / "repo.toml"
    user.write_text('[controller]\ndefault_lane = "latency"\n', encoding="utf-8")
    repository.write_text('[controller]\ndefault_lane = "mechanical"\n', encoding="utf-8")

    effective = load_effective_config(
        user_path=user,
        repository_path=repository,
        task_overrides={"controller": {"default_lane": "bounded-hard"}},
        cli_overrides={"controller": {"default_lane": "standard"}},
    )

    assert effective.data["controller"]["default_lane"] == "standard"
    assert effective.origins["controller.default_lane"] == "cli"


def test_state_root_honors_override(tmp_path: Path) -> None:
    assert state_root({"CODEX_AUTO_HOME": str(tmp_path)}) == tmp_path.resolve()


def test_starter_template_contains_all_default_routing_terms() -> None:
    starter = parse_config(DEFAULT_ROUTER_TOML, source="starter.toml")

    assert starter["routing"]["high_risk_terms"] == DEFAULT_CONFIG["routing"]["high_risk_terms"]
