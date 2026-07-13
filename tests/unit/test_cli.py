from __future__ import annotations

from codex_auto.cli import _default_codex_prefix


def test_default_codex_prefix_prefers_runnable_cmd_shim_on_windows() -> None:
    observed: list[str] = []

    def which(name: str) -> str | None:
        observed.append(name)
        return "C:/npm/codex.cmd" if name == "codex.cmd" else "C:/apps/codex"

    assert _default_codex_prefix(platform_name="nt", which=which) == ("C:/npm/codex.cmd",)
    assert observed == ["codex.cmd"]


def test_default_codex_prefix_resolves_normal_executable_on_posix() -> None:
    assert _default_codex_prefix(
        platform_name="posix", which=lambda name: "/usr/bin/codex" if name == "codex" else None
    ) == ("/usr/bin/codex",)
