import os

from codex_auto.process.identity import process_identity_matches, process_start_identity


def test_process_identity_matches_creation_time_not_pid_alone() -> None:
    identity = process_start_identity()

    assert identity is not None
    assert process_identity_matches(os.getpid(), identity)
    assert not process_identity_matches(os.getpid(), f"{identity}-different")
