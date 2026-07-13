"""Windows Job Object process-tree control with a narrow taskkill fallback."""

from __future__ import annotations

import ctypes
import subprocess
from collections.abc import Callable
from ctypes import wintypes
from typing import Any, Protocol, cast

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100


def load_kernel32() -> Any:
    """Load kernel32 without exposing platform-specific ctypes names to type checkers."""
    win_dll = cast(Callable[..., Any], vars(ctypes)["WinDLL"])
    return win_dll("kernel32", use_last_error=True)


def last_windows_error() -> OSError:
    """Return the current Win32 error through a platform-isolated ctypes boundary."""
    get_last_error = cast(Callable[[], int], vars(ctypes)["get_last_error"])
    win_error = cast(Callable[[int], OSError], vars(ctypes)["WinError"])
    return win_error(get_last_error())


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class JobObject(Protocol):
    def attach(self, pid: int) -> None: ...

    def terminate(self) -> None: ...

    def close(self) -> None: ...


class WindowsJobObject:
    def __init__(self) -> None:
        kernel32 = load_kernel32()
        self._kernel32 = kernel32
        self._handle = kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise last_windows_error()
        information = _EXTENDED_LIMIT_INFORMATION()
        information.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        configured = kernel32.SetInformationJobObject(
            self._handle,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(information),
            ctypes.sizeof(information),
        )
        if not configured:
            error = last_windows_error()
            kernel32.CloseHandle(self._handle)
            self._handle = None
            raise error

    def attach(self, pid: int) -> None:
        process = self._kernel32.OpenProcess(PROCESS_TERMINATE | PROCESS_SET_QUOTA, False, pid)
        if not process:
            raise last_windows_error()
        try:
            if not self._kernel32.AssignProcessToJobObject(self._handle, process):
                raise last_windows_error()
        finally:
            self._kernel32.CloseHandle(process)

    def terminate(self) -> None:
        if self._handle and not self._kernel32.TerminateJobObject(self._handle, 1):
            raise last_windows_error()

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def _taskkill(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )


class WindowsProcessController:
    def __init__(
        self,
        *,
        job_factory: Callable[[], JobObject] = WindowsJobObject,
        fallback_kill: Callable[[int], None] = _taskkill,
    ) -> None:
        self._fallback_kill = fallback_kill
        self._job: JobObject | None
        self.fallback_reason: str | None = None
        try:
            self._job = job_factory()
        except OSError as error:
            self._job = None
            self.fallback_reason = str(error)

    def attach(self, pid: int) -> None:
        if self._job is None:
            return
        try:
            self._job.attach(pid)
        except OSError as error:
            self.fallback_reason = str(error)
            self._job.close()
            self._job = None

    def terminate(self, pid: int) -> None:
        if self._job is not None:
            self._job.terminate()
        else:
            self._fallback_kill(pid)

    def kill(self, pid: int) -> None:
        self.terminate(pid)

    def close(self) -> None:
        if self._job is not None:
            self._job.close()
