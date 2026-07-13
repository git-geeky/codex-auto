from __future__ import annotations

import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from codex_auto.git.patch import (
    PatchExporter,
    UnsafePathError,
    export_is_complete,
    safe_candidate_path,
)
from codex_auto.git.repository import GitInspector, GitRepository
from codex_auto.git.worktree import (
    GitWorktreeManager,
    UnsafeCleanupError,
    WorktreeOwnershipError,
)


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def create_repository(tmp_path: Path, name: str = "source") -> Path:
    repository = tmp_path / name
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.name", "codex-auto tests")
    git(repository, "config", "user.email", "codex-auto@example.invalid")
    (repository / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    git(repository, "add", "tracked.txt")
    git(repository, "commit", "-m", "baseline")
    return repository


def test_discovery_and_worktree_creation_are_idempotent_and_detached(tmp_path: Path) -> None:
    source = create_repository(tmp_path)
    repository = GitRepository.discover(source / "tracked.txt")
    base_sha = repository.resolve_revision("HEAD")
    manager = GitWorktreeManager(tmp_path / "state")

    first = manager.create("run-1", repository, base_sha)
    repeated = manager.create("run-1", repository, base_sha)

    assert first.path == repeated.path
    assert first.base_sha == base_sha
    assert git(first.path, "branch", "--show-current") == ""
    assert git(first.path, "rev-parse", "HEAD") == base_sha


def test_existing_run_id_cannot_be_repurposed_for_another_repository(tmp_path: Path) -> None:
    first_source = create_repository(tmp_path, "first")
    second_source = create_repository(tmp_path, "second")
    first = GitRepository.discover(first_source)
    second = GitRepository.discover(second_source)
    manager = GitWorktreeManager(tmp_path / "state")
    manager.create("run-1", first, first.resolve_revision("HEAD"))

    with pytest.raises(WorktreeOwnershipError):
        manager.create("run-1", second, second.resolve_revision("HEAD"))


def test_snapshots_exports_and_cleanup_preserve_original_checkout(tmp_path: Path) -> None:
    source = create_repository(tmp_path)
    repository = GitRepository.discover(source)
    inspector = GitInspector()
    original_before = inspector.snapshot(source)
    manager = GitWorktreeManager(tmp_path / "state")
    owned = manager.create("run-1", repository, repository.resolve_revision("HEAD"))

    (owned.path / "tracked.txt").write_text("candidate\n", encoding="utf-8")
    (owned.path / "untracked.txt").write_text("new file\n", encoding="utf-8")
    (owned.path / "binary.bin").write_bytes(b"\x00\x01\xff")
    candidate = inspector.snapshot(owned.path)

    assert set(candidate.changed_files) == {"binary.bin", "tracked.txt", "untracked.txt"}
    assert {item.path for item in candidate.untracked_files} == {"binary.bin", "untracked.txt"}
    assert "tracked.txt" in candidate.binary_diff

    final_dir = tmp_path / "state" / "runs" / "run-1" / "final"
    exported = PatchExporter().export(owned.path, final_dir, candidate)
    assert exported.patch_path.read_bytes().startswith(b"diff --git")
    manifest = json.loads(exported.manifest_path.read_text(encoding="utf-8"))
    assert set(manifest["changed_files"]) == {"binary.bin", "tracked.txt", "untracked.txt"}
    with tarfile.open(exported.untracked_archive, "r") as archive:
        assert set(archive.getnames()) == {"binary.bin", "untracked.txt"}
    assert export_is_complete(final_dir)

    with pytest.raises(UnsafeCleanupError):
        manager.cleanup("run-1", repository, active=True, exported=True)
    manager.cleanup("run-1", repository, active=False, exported=True)
    assert not owned.path.exists()

    original_after = inspector.snapshot(source)
    assert original_after.checkout_invariant() == original_before.checkout_invariant()


def test_cleanup_refuses_unexported_changes(tmp_path: Path) -> None:
    source = create_repository(tmp_path)
    repository = GitRepository.discover(source)
    manager = GitWorktreeManager(tmp_path / "state")
    owned = manager.create("run-1", repository, repository.resolve_revision("HEAD"))
    (owned.path / "untracked.txt").write_text("not exported\n", encoding="utf-8")

    with pytest.raises(UnsafeCleanupError, match="unexported"):
        manager.cleanup("run-1", repository, active=False, exported=False)


def test_partial_export_is_not_complete(tmp_path: Path) -> None:
    final_dir = tmp_path / "final"
    final_dir.mkdir()
    (final_dir / "final.patch").write_text("partial\n", encoding="utf-8")

    assert not export_is_complete(final_dir)


def test_candidate_path_rejects_absolute_and_parent_traversal(tmp_path: Path) -> None:
    root = tmp_path / "worktree"
    root.mkdir()
    with pytest.raises(UnsafePathError):
        safe_candidate_path(root, "../escape.txt")
    with pytest.raises(UnsafePathError):
        safe_candidate_path(root, str((tmp_path / "absolute.txt").resolve()))
