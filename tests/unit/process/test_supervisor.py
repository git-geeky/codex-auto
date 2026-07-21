from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from codex_auto.process.loop import CommandCycle, CommandLoopDetector
from codex_auto.process.output import BoundedTextBuffer
from codex_auto.process.posix import PosixProcessController
from codex_auto.process.supervisor import FileCancelSignal, ProcessRequest, ProcessSupervisor


def test_bounded_buffer_retains_beginning_and_end() -> None:
    buffer = BoundedTextBuffer(limit_bytes=10)
    buffer.append("abcdefgh")
    buffer.append("ijklmnop")

    value = buffer.finish()
    assert value.truncated
    assert value.text.startswith("abcde")
    assert value.text.endswith("lmnop")


def test_bounded_buffer_retains_tail_after_many_small_appends() -> None:
    buffer = BoundedTextBuffer(limit_bytes=64)
    buffer.append("beginning-marker|")
    for index in range(10_000):
        buffer.append(f"{index:05d}|")

    value = buffer.finish()

    assert value.truncated
    assert value.text.startswith("beginning-marker|")
    assert value.text.endswith("09999|")
    assert len(value.text.encode("utf-8")) <= 64


def test_command_loop_requires_equivalent_failure_and_no_progress() -> None:
    detector = CommandLoopDetector(threshold=3)
    cycle = CommandCycle("pytest -q", 1, "same-output", progress_token="git-a")
    assert not detector.observe(cycle)
    assert not detector.observe(cycle)
    assert detector.observe(cycle)

    detector = CommandLoopDetector(threshold=3)
    assert not detector.observe(cycle)
    assert not detector.observe(CommandCycle("pytest -q", 1, "same-output", progress_token="git-b"))


def test_supervisor_streams_separate_output_and_preserves_exit_code(tmp_path: Path) -> None:
    request = ProcessRequest(
        command=(
            sys.executable,
            "-c",
            "import sys; print('stdout-line'); print('stderr-line', file=sys.stderr)",
        ),
        cwd=tmp_path,
        stdin="",
        environment=dict(os.environ),
        total_timeout_seconds=5,
        inactivity_timeout_seconds=5,
        graceful_shutdown_seconds=0.2,
        output_limit_bytes=1024,
    )

    result = ProcessSupervisor().run(request)

    assert result.exit_code == 0
    assert result.stdout.text.strip() == "stdout-line"
    assert result.stderr.text.strip() == "stderr-line"
    assert result.termination_reason == "completed"


def test_supervisor_bounds_single_huge_line_without_waiting_for_newline(tmp_path: Path) -> None:
    result = ProcessSupervisor().run(
        ProcessRequest(
            command=(
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('x' * 200000); sys.stdout.flush()",
            ),
            cwd=tmp_path,
            stdin="",
            environment=dict(os.environ),
            total_timeout_seconds=5,
            inactivity_timeout_seconds=5,
            graceful_shutdown_seconds=0.2,
            output_limit_bytes=1024,
        )
    )

    assert result.exit_code == 0
    assert result.stdout.truncated
    assert result.stdout.total_bytes == 200000
    assert len(result.stdout.text.encode()) <= 1024


def test_supervisor_terminates_timeout_and_marks_reason(tmp_path: Path) -> None:
    request = ProcessRequest(
        command=(sys.executable, "-c", "import time; time.sleep(30)"),
        cwd=tmp_path,
        stdin="",
        environment=dict(os.environ),
        total_timeout_seconds=0.2,
        inactivity_timeout_seconds=5,
        graceful_shutdown_seconds=0.1,
        output_limit_bytes=1024,
    )

    result = ProcessSupervisor().run(request)

    assert result.timed_out
    assert result.termination_reason == "total_timeout"
    assert result.duration_seconds < 5


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group shutdown behavior")
def test_supervisor_skips_hard_kill_after_graceful_group_exit(tmp_path: Path) -> None:
    class CountingController(PosixProcessController):
        def __init__(self) -> None:
            self.hard_kill_calls = 0

        def kill(self, pid: int) -> None:
            self.hard_kill_calls += 1
            super().kill(pid)

    controller = CountingController()
    result = ProcessSupervisor(controller_factory=lambda: controller).run(
        ProcessRequest(
            command=(sys.executable, "-c", "import time; time.sleep(30)"),
            cwd=tmp_path,
            stdin="",
            environment=dict(os.environ),
            total_timeout_seconds=0.2,
            inactivity_timeout_seconds=5,
            graceful_shutdown_seconds=1,
            output_limit_bytes=1024,
        )
    )

    assert result.timed_out
    assert controller.hard_kill_calls == 0


def test_supervisor_enforces_distinct_startup_timeout(tmp_path: Path) -> None:
    result = ProcessSupervisor().run(
        ProcessRequest(
            command=(sys.executable, "-c", "import time; time.sleep(30)"),
            cwd=tmp_path,
            stdin="",
            environment=dict(os.environ),
            total_timeout_seconds=5,
            inactivity_timeout_seconds=5,
            graceful_shutdown_seconds=0.1,
            output_limit_bytes=1024,
            startup_timeout_seconds=0.2,
        )
    )

    assert result.timed_out
    assert result.termination_reason == "startup_timeout"


def test_supervisor_timeout_is_enforced_when_child_does_not_read_large_stdin(
    tmp_path: Path,
) -> None:
    request = ProcessRequest(
        command=(sys.executable, "-c", "import time; time.sleep(30)"),
        cwd=tmp_path,
        stdin="x" * (2 * 1024 * 1024),
        environment=dict(os.environ),
        total_timeout_seconds=0.2,
        inactivity_timeout_seconds=5,
        graceful_shutdown_seconds=0.1,
        output_limit_bytes=1024,
    )

    result = ProcessSupervisor().run(request)

    assert result.timed_out
    assert result.termination_reason == "total_timeout"
    assert result.duration_seconds < 5


def test_supervisor_file_cancel_signal_terminates_owned_process(tmp_path: Path) -> None:
    cancel = tmp_path / "cancel.requested"
    cancel.write_text("cancel\n", encoding="utf-8")
    result = ProcessSupervisor().run(
        ProcessRequest(
            command=(sys.executable, "-c", "import time; time.sleep(30)"),
            cwd=tmp_path,
            stdin="",
            environment=dict(os.environ),
            total_timeout_seconds=30,
            inactivity_timeout_seconds=30,
            graceful_shutdown_seconds=0.1,
            output_limit_bytes=1024,
            cancel_event=FileCancelSignal(cancel),
        )
    )
    assert result.cancelled
    assert result.termination_reason == "cancelled"


def test_supervisor_timeout_terminates_fake_codex_descendant(tmp_path: Path) -> None:
    fake = Path(__file__).parents[2] / "fake_codex" / "fake_codex.py"
    scenario = tmp_path / "scenario.json"
    child_pid_file = tmp_path / "child.pid"
    scenario.write_text(
        json.dumps(
            {
                "spawn_child_seconds": 30,
                "child_pid_file": str(child_pid_file),
                "sleep_seconds": 30,
            }
        ),
        encoding="utf-8",
    )
    environment = {**os.environ, "FAKE_CODEX_SCENARIO": str(scenario)}
    request = ProcessRequest(
        command=(sys.executable, str(fake), "exec", "-"),
        cwd=tmp_path,
        stdin="task",
        environment=environment,
        total_timeout_seconds=2,
        inactivity_timeout_seconds=5,
        graceful_shutdown_seconds=0.1,
        output_limit_bytes=1024,
    )

    result = ProcessSupervisor().run(request)

    assert result.timed_out
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 3
    while _process_exists(child_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _process_exists(child_pid)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group hard-kill behavior")
def test_posix_timeout_hard_kills_term_ignoring_descendant(tmp_path: Path) -> None:
    fake = Path(__file__).parents[2] / "fake_codex" / "fake_codex.py"
    scenario = tmp_path / "scenario.json"
    child_pid_file = tmp_path / "child.pid"
    scenario.write_text(
        json.dumps(
            {
                "spawn_child_seconds": 30,
                "child_ignore_sigterm": True,
                "child_pid_file": str(child_pid_file),
                "sleep_seconds": 30,
            }
        ),
        encoding="utf-8",
    )
    environment = {**os.environ, "FAKE_CODEX_SCENARIO": str(scenario)}

    result = ProcessSupervisor().run(
        ProcessRequest(
            command=(sys.executable, str(fake), "exec", "-"),
            cwd=tmp_path,
            stdin="task",
            environment=environment,
            total_timeout_seconds=2,
            inactivity_timeout_seconds=5,
            graceful_shutdown_seconds=0.1,
            output_limit_bytes=1024,
        )
    )

    assert result.timed_out
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 3
    while _process_exists(child_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _process_exists(child_pid)


@pytest.mark.skipif(os.name == "nt", reason="POSIX controller-death watchdog behavior")
def test_posix_controller_death_kills_active_command(tmp_path: Path) -> None:
    target_pid_file = tmp_path / "target.pid"
    target_code = (
        "import os,time; from pathlib import Path; "
        f"Path({str(target_pid_file)!r}).write_text(str(os.getpid())); time.sleep(30)"
    )
    controller_code = (
        "import os,sys; from pathlib import Path; "
        "from codex_auto.process.supervisor import ProcessRequest,ProcessSupervisor; "
        f"ProcessSupervisor().run(ProcessRequest((sys.executable,'-c',{target_code!r}),"
        f"Path({str(tmp_path)!r}),'',dict(os.environ),30,30,1,1024))"
    )
    controller = subprocess.Popen([sys.executable, "-c", controller_code], cwd=tmp_path)
    deadline = time.monotonic() + 5
    while not target_pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert target_pid_file.exists()
    target_pid = int(target_pid_file.read_text(encoding="utf-8"))

    controller.kill()
    controller.wait(timeout=3)

    deadline = time.monotonic() + 5
    while _process_exists(target_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _process_exists(target_pid)


def _process_exists(pid: int) -> bool:
    if os.name == "nt":
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            text=True,
        )
        return f'"{pid}"' in completed.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
