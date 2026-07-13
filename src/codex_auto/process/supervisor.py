"""Streaming bounded subprocess supervision."""

from __future__ import annotations

import codecs
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TextIO

from codex_auto.process.identity import process_start_identity
from codex_auto.process.output import BoundedOutput, BoundedTextBuffer
from codex_auto.process.posix import PosixProcessController
from codex_auto.process.windows import WindowsProcessController


class ProcessController(Protocol):
    fallback_reason: str | None

    def attach(self, pid: int) -> None: ...

    def terminate(self, pid: int) -> None: ...

    def kill(self, pid: int) -> None: ...

    def close(self) -> None: ...


class CancelSignal(Protocol):
    def is_set(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class FileCancelSignal:
    path: Path

    def is_set(self) -> bool:
        return self.path.is_file()


@dataclass(frozen=True, slots=True)
class ProcessRequest:
    command: tuple[str, ...]
    cwd: Path
    stdin: str
    environment: dict[str, str]
    total_timeout_seconds: float
    inactivity_timeout_seconds: float
    graceful_shutdown_seconds: float
    output_limit_bytes: int
    cancel_event: CancelSignal | None = None
    startup_timeout_seconds: float | None = None
    output_observer: Callable[[str], str | None] | None = None


@dataclass(frozen=True, slots=True)
class ProcessResult:
    exit_code: int | None
    stdout: BoundedOutput
    stderr: BoundedOutput
    timed_out: bool
    inactivity_timed_out: bool
    cancelled: bool
    termination_reason: str
    duration_seconds: float
    controller_fallback_reason: str | None


class ProcessSupervisor:
    def __init__(self, controller_factory: Callable[[], ProcessController] | None = None) -> None:
        self._controller_factory = controller_factory

    def run(self, request: ProcessRequest) -> ProcessResult:
        if not request.command:
            raise ValueError("process command cannot be empty")
        controller = self._new_controller()
        stdout_buffer = BoundedTextBuffer(request.output_limit_bytes)
        stderr_buffer = BoundedTextBuffer(request.output_limit_bytes)
        last_activity = [time.monotonic()]
        activity_seen = [False]
        observer_termination: list[str | None] = [None]
        start = time.monotonic()
        command = list(request.command)
        if os.name != "nt":
            parent_identity = process_start_identity()
            if parent_identity is None:
                raise RuntimeError("could not determine supervisor process identity")
            command = [
                sys.executable,
                "-m",
                "codex_auto.process.posix_watchdog",
                str(os.getpid()),
                parent_identity,
                json.dumps(command),
            ]
        process = subprocess.Popen(
            command,
            cwd=request.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            shell=False,
            env=request.environment,
            start_new_session=os.name != "nt",
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
        )
        controller.attach(process.pid)
        threads = [
            threading.Thread(
                target=_read_stream,
                args=(
                    process.stdout,
                    stdout_buffer,
                    last_activity,
                    activity_seen,
                    request.output_observer,
                    observer_termination,
                ),
                daemon=True,
            ),
            threading.Thread(
                target=_read_stream,
                args=(
                    process.stderr,
                    stderr_buffer,
                    last_activity,
                    activity_seen,
                    None,
                    observer_termination,
                ),
                daemon=True,
            ),
        ]
        if process.stdin is not None:
            threads.append(
                threading.Thread(
                    target=_write_stdin,
                    args=(process.stdin, request.stdin),
                    daemon=True,
                )
            )
        for thread in threads:
            thread.start()

        termination_reason = "completed"
        timed_out = False
        inactivity_timed_out = False
        cancelled = False
        next_cancel_check = start
        while process.poll() is None:
            now = time.monotonic()
            if observer_termination[0] is not None:
                termination_reason = observer_termination[0]
                break
            if (
                request.cancel_event is not None
                and now >= next_cancel_check
                and request.cancel_event.is_set()
            ):
                cancelled = True
                termination_reason = "cancelled"
                break
            if now >= next_cancel_check:
                next_cancel_check = now + 0.2
            if now - start >= request.total_timeout_seconds:
                timed_out = True
                termination_reason = "total_timeout"
                break
            if (
                request.startup_timeout_seconds is not None
                and not activity_seen[0]
                and now - start >= request.startup_timeout_seconds
            ):
                timed_out = True
                termination_reason = "startup_timeout"
                break
            if now - last_activity[0] >= request.inactivity_timeout_seconds:
                inactivity_timed_out = True
                termination_reason = "inactivity_timeout"
                break
            time.sleep(0.02)

        if process.poll() is None:
            controller.terminate(process.pid)
            deadline = time.monotonic() + request.graceful_shutdown_seconds
            while process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            controller.kill(process.pid)
            try:
                process.wait(timeout=max(1.0, request.graceful_shutdown_seconds))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)
        for thread in threads:
            thread.join(timeout=1.0)
        controller.close()
        return ProcessResult(
            exit_code=process.returncode,
            stdout=stdout_buffer.finish(),
            stderr=stderr_buffer.finish(),
            timed_out=timed_out,
            inactivity_timed_out=inactivity_timed_out,
            cancelled=cancelled,
            termination_reason=termination_reason,
            duration_seconds=time.monotonic() - start,
            controller_fallback_reason=controller.fallback_reason,
        )

    def _new_controller(self) -> ProcessController:
        if self._controller_factory is not None:
            return self._controller_factory()
        if os.name == "nt":
            return WindowsProcessController()
        return PosixProcessController()


def _read_stream(
    stream: TextIO | None,
    buffer: BoundedTextBuffer,
    last_activity: list[float],
    activity_seen: list[bool],
    observer: Callable[[str], str | None] | None,
    observer_termination: list[str | None],
) -> None:
    if stream is None:
        return
    try:
        raw = getattr(stream, "buffer", None)
        if raw is None or not hasattr(raw, "read1"):
            for chunk in iter(lambda: stream.read(4096), ""):
                buffer.append(chunk)
                last_activity[0] = time.monotonic()
                activity_seen[0] = True
                if observer is not None and observer_termination[0] is None:
                    observer_termination[0] = observer(chunk)
            return
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        for raw_chunk in iter(lambda: raw.read1(4096), b""):
            chunk = decoder.decode(raw_chunk)
            if chunk:
                buffer.append(chunk)
                last_activity[0] = time.monotonic()
                activity_seen[0] = True
                if observer is not None and observer_termination[0] is None:
                    observer_termination[0] = observer(chunk)
        remainder = decoder.decode(b"", final=True)
        if remainder:
            buffer.append(remainder)
            last_activity[0] = time.monotonic()
            activity_seen[0] = True
            if observer is not None and observer_termination[0] is None:
                observer_termination[0] = observer(remainder)
    finally:
        stream.close()


def _write_stdin(stream: TextIO, content: str) -> None:
    try:
        stream.write(content)
        stream.flush()
    except (BrokenPipeError, OSError, ValueError):
        pass
    finally:
        with suppress(OSError):
            stream.close()
