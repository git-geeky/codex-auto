from __future__ import annotations

import subprocess
import sys


def test_module_help_exposes_required_command_families() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "codex_auto", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    for command in (
        "init",
        "config",
        "doctor",
        "dry-run",
        "run",
        "resume",
        "cancel",
        "status",
        "report",
        "export",
        "stats",
        "cleanup",
    ):
        assert command in completed.stdout
