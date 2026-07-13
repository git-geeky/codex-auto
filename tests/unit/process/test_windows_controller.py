from __future__ import annotations

from dataclasses import dataclass

from codex_auto.process.windows import WindowsProcessController


@dataclass
class FakeJob:
    attach_error: OSError | None = None
    attached_pid: int | None = None
    terminated: bool = False
    closed: bool = False

    def attach(self, pid: int) -> None:
        if self.attach_error:
            raise self.attach_error
        self.attached_pid = pid

    def terminate(self) -> None:
        self.terminated = True

    def close(self) -> None:
        self.closed = True


def test_windows_controller_prefers_job_object() -> None:
    job = FakeJob()
    fallbacks: list[int] = []
    controller = WindowsProcessController(job_factory=lambda: job, fallback_kill=fallbacks.append)

    controller.attach(123)
    controller.terminate(123)
    controller.close()

    assert job.attached_pid == 123
    assert job.terminated
    assert job.closed
    assert fallbacks == []


def test_windows_controller_records_narrow_fallback_when_job_attach_fails() -> None:
    job = FakeJob(attach_error=OSError("nested job denied"))
    fallbacks: list[int] = []
    controller = WindowsProcessController(job_factory=lambda: job, fallback_kill=fallbacks.append)

    controller.attach(456)
    controller.terminate(456)

    assert controller.fallback_reason == "nested job denied"
    assert fallbacks == [456]
