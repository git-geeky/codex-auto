"""Strict TOML configuration and deterministic precedence."""

from __future__ import annotations

import copy
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Configuration is malformed, unknown, or internally inconsistent."""


ALLOWED_TOP_LEVEL = {
    "version",
    "controller",
    "state",
    "validation",
    "models",
    "compatibility",
    "routing",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "controller": {
        "default_lane": "auto",
        "default_deep_mode": "serial",
        "require_clean_source": True,
        "preserve_worktree": True,
        "ignore_user_codex_config": True,
        "ignore_codex_rules": False,
        "retain_raw_codex_events": False,
        "max_transient_retries": 2,
        "max_same_tier_repairs": 1,
        "loop_repeat_limit": 3,
        "review_policy": "risk-based",
        "approval_policy": "never",
        "attempt_timeout_seconds": 1800,
        "startup_timeout_seconds": 60,
        "inactivity_timeout_seconds": 300,
        "deep_attempt_timeout_seconds": 3600,
        "reviewer_timeout_seconds": 1200,
        "graceful_shutdown_seconds": 15,
        "output_limit_bytes": 10485760,
    },
    "state": {"root": "", "database": "", "artifact_retention_days": 90},
    "validation": {
        "execution": "codex-sandbox",
        "require_safe_execution": True,
        "allow_host_only_with_explicit_trust": True,
        "steps": [],
    },
    "models": {
        "mechanical": {"model": "gpt-5.6-luna", "effort": "medium"},
        "mechanical_repair": {"model": "gpt-5.6-luna", "effort": "high"},
        "standard": {"model": "gpt-5.6-luna", "effort": "high"},
        "bounded_hard": {"model": "gpt-5.6-luna", "effort": "xhigh"},
        "latency": {"model": "gpt-5.6-terra", "effort": "medium"},
        "rescue": {"model": "gpt-5.6-sol", "effort": "high"},
        "deep_serial": {"model": "gpt-5.6-sol", "effort": "max"},
        "deep_parallel": {"model": "gpt-5.6-sol", "effort": "ultra"},
        "standard_reviewer": {"model": "gpt-5.6-sol", "effort": "medium"},
        "high_risk_reviewer": {"model": "gpt-5.6-sol", "effort": "high"},
    },
    "compatibility": {
        "allow_effort_fallback": True,
        "max_fallback_efforts": ["xhigh", "high"],
        "ultra_fallback_efforts": ["max", "xhigh", "high"],
    },
    "routing": {
        "high_risk_terms": [
            "authentication",
            "authorization",
            "credential",
            "secret",
            "cryptography",
            "encryption",
            "migration",
            "data loss",
            "concurrency",
            "race condition",
            "distributed state",
            "payment",
            "billing",
            "production outage",
        ],
        "mechanical_terms": ["rename", "replace exact", "generate repetitive", "format"],
        "high_risk_globs": [
            "**/auth/**",
            "**/security/**",
            "**/migrations/**",
            "**/payments/**",
            "**/*.tf",
            "**/terraform/**",
            "**/k8s/**",
        ],
        "mechanical_globs": [],
        "forbidden_globs": [".git/**"],
        "protected_test_globs": [
            "tests/**",
            "**/tests/**",
            "**/*_test.py",
            "**/*.spec.*",
            "**/*.test.*",
        ],
        "max_changed_files": 100,
        "max_insertions": 10000,
        "max_deletions": 10000,
    },
}

ALLOWED_SECTION_KEYS = {
    section: set(value) for section, value in DEFAULT_CONFIG.items() if isinstance(value, dict)
}
MODEL_KEYS = {"model", "effort"}
VALIDATION_STEP_KEYS = {
    "name",
    "stage",
    "command",
    "working_directory",
    "timeout_seconds",
    "policy",
    "expected_exit_codes",
    "platform",
    "environment_allowlist",
    "output_limit_bytes",
    "safe_to_rerun",
    "network_required",
    "sandbox_profile",
    "comparison_mode",
}

DEFAULT_ROUTER_TOML = """version = 1

[controller]
default_lane = "auto"
default_deep_mode = "serial"
require_clean_source = true
preserve_worktree = true
ignore_user_codex_config = true
ignore_codex_rules = false
retain_raw_codex_events = false
max_transient_retries = 2
max_same_tier_repairs = 1
loop_repeat_limit = 3
review_policy = "risk-based"
approval_policy = "never"
attempt_timeout_seconds = 1800
startup_timeout_seconds = 60
inactivity_timeout_seconds = 300
deep_attempt_timeout_seconds = 3600
reviewer_timeout_seconds = 1200
graceful_shutdown_seconds = 15
output_limit_bytes = 10485760

[state]
root = ""
database = ""
artifact_retention_days = 90

[validation]
execution = "codex-sandbox"
require_safe_execution = true
allow_host_only_with_explicit_trust = true

[models.mechanical]
model = "gpt-5.6-luna"
effort = "medium"
[models.mechanical_repair]
model = "gpt-5.6-luna"
effort = "high"
[models.standard]
model = "gpt-5.6-luna"
effort = "high"
[models.bounded_hard]
model = "gpt-5.6-luna"
effort = "xhigh"
[models.latency]
model = "gpt-5.6-terra"
effort = "medium"
[models.rescue]
model = "gpt-5.6-sol"
effort = "high"
[models.deep_serial]
model = "gpt-5.6-sol"
effort = "max"
[models.deep_parallel]
model = "gpt-5.6-sol"
effort = "ultra"
[models.standard_reviewer]
model = "gpt-5.6-sol"
effort = "medium"
[models.high_risk_reviewer]
model = "gpt-5.6-sol"
effort = "high"

[compatibility]
allow_effort_fallback = true
max_fallback_efforts = ["xhigh", "high"]
ultra_fallback_efforts = ["max", "xhigh", "high"]

[routing]
high_risk_terms = [
  "authentication", "authorization", "credential", "secret", "cryptography",
  "encryption", "migration", "data loss", "concurrency", "race condition",
  "distributed state", "payment", "billing", "production outage"
]
mechanical_terms = ["rename", "replace exact", "generate repetitive", "format"]
high_risk_globs = [
  "**/auth/**", "**/security/**", "**/migrations/**", "**/payments/**",
  "**/*.tf", "**/terraform/**", "**/k8s/**"
]
forbidden_globs = [".git/**"]
protected_test_globs = [
  "tests/**", "**/tests/**", "**/*_test.py", "**/*.spec.*", "**/*.test.*"
]
max_changed_files = 100
max_insertions = 10000
max_deletions = 10000

[[validation.steps]]
name = "compile"
stage = "smoke"
command = ["python", "-m", "compileall", "-q", "src"]
working_directory = "."
timeout_seconds = 120
policy = "must_pass"
platform = "all"
environment_allowlist = ["PATH", "SYSTEMROOT", "WINDIR", "HOME", "USERPROFILE"]
output_limit_bytes = 1048576
safe_to_rerun = true
network_required = false
sandbox_profile = ":workspace"
comparison_mode = "failure_ids"
expected_exit_codes = [0]

[[validation.steps]]
name = "unit-tests"
stage = "targeted"
command = ["python", "-m", "pytest", "-q", "tests/unit"]
working_directory = "."
timeout_seconds = 600
policy = "must_pass"
platform = "all"
environment_allowlist = ["PATH", "SYSTEMROOT", "WINDIR", "HOME", "USERPROFILE"]
output_limit_bytes = 10485760
safe_to_rerun = true
network_required = false
sandbox_profile = ":workspace"
comparison_mode = "failure_ids"
expected_exit_codes = [0]

[[validation.steps]]
name = "full-tests"
stage = "full"
command = ["python", "-m", "pytest", "-q"]
working_directory = "."
timeout_seconds = 1800
policy = "must_pass"
platform = "all"
environment_allowlist = ["PATH", "SYSTEMROOT", "WINDIR", "HOME", "USERPROFILE"]
output_limit_bytes = 10485760
safe_to_rerun = true
network_required = false
sandbox_profile = ":workspace"
comparison_mode = "failure_ids"
expected_exit_codes = [0]
"""


@dataclass(frozen=True, slots=True)
class EffectiveConfig:
    data: dict[str, Any]
    origins: dict[str, str]


def parse_config(text: str, *, source: str, compatibility: bool = False) -> dict[str, Any]:
    try:
        payload = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{source}: {error}") from error
    unknown = sorted(set(payload) - ALLOWED_TOP_LEVEL)
    if unknown and not compatibility:
        raise ConfigError(f"unknown top-level key: {unknown[0]}")
    if not compatibility:
        for section, allowed in ALLOWED_SECTION_KEYS.items():
            value = payload.get(section)
            if isinstance(value, dict):
                nested_unknown = sorted(set(value) - allowed)
                if nested_unknown:
                    raise ConfigError(f"unknown key: {section}.{nested_unknown[0]}")
        models = payload.get("models")
        if isinstance(models, dict):
            for name, value in models.items():
                if isinstance(value, dict):
                    unknown_model_keys = sorted(set(value) - MODEL_KEYS)
                    if unknown_model_keys:
                        raise ConfigError(f"unknown key: models.{name}.{unknown_model_keys[0]}")
        validation = payload.get("validation")
        if isinstance(validation, dict):
            steps = validation.get("steps", [])
            if isinstance(steps, list):
                for index, step in enumerate(steps):
                    if isinstance(step, dict):
                        unknown_step_keys = sorted(set(step) - VALIDATION_STEP_KEYS)
                        if unknown_step_keys:
                            raise ConfigError(
                                f"unknown key: validation.steps[{index}].{unknown_step_keys[0]}"
                            )
    if payload.get("version", 1) != 1:
        raise ConfigError("version must be 1")
    return payload


def load_effective_config(
    *,
    user_path: Path | None = None,
    repository_path: Path | None = None,
    task_overrides: Mapping[str, Any] | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
) -> EffectiveConfig:
    data = copy.deepcopy(DEFAULT_CONFIG)
    origins = {path: "default" for path in _leaf_paths(data)}
    layers: list[tuple[str, Mapping[str, Any]]] = []
    for name, path in (("user", user_path), ("repository", repository_path)):
        if path is not None and path.exists():
            layers.append((name, parse_config(path.read_text(encoding="utf-8"), source=str(path))))
    if task_overrides:
        layers.append(("task", task_overrides))
    if cli_overrides:
        layers.append(("cli", cli_overrides))
    for origin, layer in layers:
        _merge(data, layer, origin, origins)
    return EffectiveConfig(data, origins)


def _merge(
    target: dict[str, Any],
    layer: Mapping[str, Any],
    origin: str,
    origins: dict[str, str],
    prefix: str = "",
) -> None:
    for key, value in layer.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            _merge(target[key], value, origin, origins, path)
        else:
            target[key] = copy.deepcopy(value)
            for leaf in _leaf_paths(value, path):
                origins[leaf] = origin


def _leaf_paths(value: Any, prefix: str = "") -> list[str]:
    if isinstance(value, Mapping):
        paths: list[str] = []
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(_leaf_paths(item, path))
        return paths
    return [prefix]
