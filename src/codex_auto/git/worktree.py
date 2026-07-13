"""Idempotent owned detached worktrees and exact cleanup."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from codex_auto.git.repository import GitRepository, run_git


class WorktreeOwnershipError(RuntimeError):
    """An existing path or record does not belong to the requested run."""


class UnsafeCleanupError(RuntimeError):
    """Cleanup would remove active or unexported candidate work."""


@dataclass(frozen=True, slots=True)
class OwnedWorktree:
    run_id: str
    path: Path
    repository_root: Path
    common_dir: Path
    base_sha: str


class GitWorktreeManager:
    def __init__(self, state_root: Path) -> None:
        self.state_root = state_root.resolve()
        self.worktrees_root = self.state_root / "worktrees"
        self.ownership_root = self.state_root / "locks" / "worktree-ownership"

    def create(self, run_id: str, repository: GitRepository, base_sha: str) -> OwnedWorktree:
        path = (self.worktrees_root / run_id).resolve()
        ownership_path = self._ownership_path(run_id)
        if ownership_path.exists():
            owned = self._load(ownership_path)
            expected = OwnedWorktree(
                run_id,
                path,
                repository.root.resolve(),
                repository.common_dir.resolve(),
                base_sha,
            )
            if owned != expected:
                raise WorktreeOwnershipError(f"run {run_id} ownership record does not match")
            self._verify_registered(owned, require_base_sha=False)
            return owned
        if path.exists():
            raise WorktreeOwnershipError(f"unowned worktree path already exists: {path}")

        self.worktrees_root.mkdir(parents=True, exist_ok=True)
        self.ownership_root.mkdir(parents=True, exist_ok=True)
        run_git(repository.root, "worktree", "add", "--detach", str(path), base_sha)
        owned = OwnedWorktree(
            run_id=run_id,
            path=path,
            repository_root=repository.root.resolve(),
            common_dir=repository.common_dir.resolve(),
            base_sha=base_sha,
        )
        ownership_path.write_text(
            json.dumps(
                {
                    "run_id": owned.run_id,
                    "path": str(owned.path),
                    "repository_root": str(owned.repository_root),
                    "common_dir": str(owned.common_dir),
                    "base_sha": owned.base_sha,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self._verify_registered(owned, require_base_sha=True)
        return owned

    def cleanup(
        self,
        run_id: str,
        repository: GitRepository,
        *,
        active: bool,
        exported: bool,
        discard_unexported: bool = False,
    ) -> None:
        if active:
            raise UnsafeCleanupError("run is active")
        ownership_path = self._ownership_path(run_id)
        if not ownership_path.exists():
            raise WorktreeOwnershipError(f"no ownership record for run {run_id}")
        owned = self._load(ownership_path)
        if owned.repository_root != repository.root.resolve():
            raise WorktreeOwnershipError("ownership repository does not match")
        self._verify_registered(owned, require_base_sha=False)
        dirty = bool(str(run_git(owned.path, "status", "--porcelain", "--untracked-files=all")))
        if dirty and not exported and not discard_unexported:
            raise UnsafeCleanupError("worktree contains unexported changes")
        run_git(repository.root, "worktree", "remove", "--force", str(owned.path))
        ownership_path.unlink()

    def _verify_registered(self, owned: OwnedWorktree, *, require_base_sha: bool) -> None:
        if not owned.path.exists():
            raise WorktreeOwnershipError("recorded worktree path is missing")
        observed_repository = GitRepository.discover(owned.path)
        if observed_repository.common_dir != owned.common_dir:
            raise WorktreeOwnershipError("worktree common Git directory does not match")
        observed_head = str(run_git(owned.path, "rev-parse", "HEAD"))
        if require_base_sha and observed_head != owned.base_sha:
            raise WorktreeOwnershipError("worktree base SHA does not match")
        listing = str(run_git(owned.repository_root, "worktree", "list", "--porcelain"))
        registered = {
            Path(line.removeprefix("worktree ")).resolve()
            for line in listing.splitlines()
            if line.startswith("worktree ")
        }
        if owned.path not in registered:
            raise WorktreeOwnershipError("worktree is not registered with Git")

    def _ownership_path(self, run_id: str) -> Path:
        return self.ownership_root / f"{run_id}.json"

    @staticmethod
    def _load(path: Path) -> OwnedWorktree:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return OwnedWorktree(
            run_id=str(payload["run_id"]),
            path=Path(payload["path"]).resolve(),
            repository_root=Path(payload["repository_root"]).resolve(),
            common_dir=Path(payload["common_dir"]).resolve(),
            base_sha=str(payload["base_sha"]),
        )
