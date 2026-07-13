"""Cross-platform external state paths."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path


def state_root(environment: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    override = env.get("CODEX_AUTO_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        local = env.get("LOCALAPPDATA")
        if local:
            return (Path(local) / "codex-auto").resolve()
    if sys.platform == "darwin":
        return (Path.home() / "Library" / "Application Support" / "codex-auto").resolve()
    xdg = env.get("XDG_STATE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "state"
    return (base / "codex-auto").resolve()


def is_wsl() -> bool:
    if sys.platform != "linux":
        return False
    release = Path("/proc/sys/kernel/osrelease")
    try:
        return "microsoft" in release.read_text(encoding="utf-8").lower()
    except OSError:
        return False
