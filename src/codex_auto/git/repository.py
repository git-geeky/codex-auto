"""Git discovery and immutable checkout snapshots."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from codex_auto.domain.policy import path_matches


class GitError(RuntimeError):
    """A bounded Git command failed."""


def run_git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    if completed.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def run_git_bytes(cwd: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=False,
        shell=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace")
        raise GitError(f"git {' '.join(args)} failed ({completed.returncode}): {stderr.strip()}")
    return completed.stdout


@dataclass(frozen=True, slots=True)
class GitRepository:
    root: Path
    common_dir: Path

    @classmethod
    def discover(cls, start: Path) -> GitRepository:
        cwd = start.parent if start.is_file() else start
        root = Path(str(run_git(cwd, "rev-parse", "--show-toplevel"))).resolve()
        common_text = str(run_git(cwd, "rev-parse", "--git-common-dir"))
        common = Path(common_text)
        if not common.is_absolute():
            common = root / common
        return cls(root=root, common_dir=common.resolve())

    def resolve_revision(self, revision: str) -> str:
        return str(run_git(self.root, "rev-parse", "--verify", f"{revision}^{{commit}}"))


@dataclass(frozen=True, slots=True)
class UntrackedFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class GitSnapshot:
    head: str
    branch: str | None
    index_tree: str
    porcelain_v2: str
    tracked_diff: str
    staged_diff: str
    binary_diff: str
    diffstat: str
    changed_files: tuple[str, ...]
    deleted_files: tuple[str, ...]
    insertions: int
    deletions: int
    untracked_files: tuple[UntrackedFile, ...]
    tracked_hashes: tuple[tuple[str, str], ...]

    def checkout_invariant(self) -> tuple[object, ...]:
        return (
            self.head,
            self.branch,
            self.index_tree,
            self.porcelain_v2,
            self.untracked_files,
            self.tracked_hashes,
        )


class GitInspector:
    def snapshot(self, checkout: Path, base_ref: str = "HEAD") -> GitSnapshot:
        root = checkout.resolve()
        head = str(run_git(root, "rev-parse", "HEAD"))
        branch_value = str(run_git(root, "branch", "--show-current"))
        branch = branch_value or None
        index_tree = str(run_git(root, "write-tree"))
        porcelain = str(run_git(root, "status", "--porcelain=v2", "--untracked-files=all"))
        tracked_diff = str(run_git(root, "diff"))
        staged_diff = str(run_git(root, "diff", "--cached"))
        binary_diff = str(run_git(root, "diff", "--binary", base_ref))
        diffstat = str(run_git(root, "diff", "--stat", base_ref))

        tracked_changed = set(
            filter(None, str(run_git(root, "diff", "--name-only", base_ref)).splitlines())
        )
        untracked_output = run_git(root, "ls-files", "--others", "--exclude-standard")
        untracked_paths = tuple(sorted(filter(None, untracked_output.splitlines())))
        untracked = tuple(self._untracked_file(root, path) for path in untracked_paths)
        changed_files = tuple(sorted(tracked_changed | set(untracked_paths)))
        deleted_files = tuple(
            sorted(
                filter(
                    None,
                    str(
                        run_git(root, "diff", "--name-only", "--diff-filter=D", base_ref)
                    ).splitlines(),
                )
            )
        )
        insertions, deletions = _numstat(root, base_ref)
        insertions += sum(_line_count(root / Path(path)) for path in untracked_paths)

        tracked_paths = tuple(sorted(filter(None, str(run_git(root, "ls-files")).splitlines())))
        tracked_hashes = tuple(
            (path, _sha256_file(root / Path(path)))
            for path in tracked_paths
            if (root / Path(path)).is_file()
        )
        return GitSnapshot(
            head=head,
            branch=branch,
            index_tree=index_tree,
            porcelain_v2=porcelain,
            tracked_diff=tracked_diff,
            staged_diff=staged_diff,
            binary_diff=binary_diff,
            diffstat=diffstat,
            changed_files=changed_files,
            deleted_files=deleted_files,
            insertions=insertions,
            deletions=deletions,
            untracked_files=untracked,
            tracked_hashes=tracked_hashes,
        )

    @staticmethod
    def _untracked_file(root: Path, relative: str) -> UntrackedFile:
        path = root / Path(relative)
        if path.is_symlink() or not path.is_file():
            raise GitError(f"unsafe or unsupported untracked path: {relative}")
        return UntrackedFile(relative.replace("\\", "/"), path.stat().st_size, _sha256_file(path))


def detect_weakened_tests(
    candidate: GitSnapshot,
    protected_patterns: tuple[str, ...],
    base_sha: str,
    worktree: Path,
) -> tuple[str, ...]:
    weakened: list[str] = []
    assertion_markers = ("assert", "expect(", "pytest.raises", "should(", "@test")
    skip_markers = ("pytest.mark.skip", ".skip(", "@disabled", "@skip")
    for path in candidate.changed_files:
        if not path_matches(path, protected_patterns):
            continue
        diff = str(run_git(worktree, "diff", "--unified=0", base_sha, "--", path))
        removed_assertions = sum(
            1
            for line in diff.splitlines()
            if line.startswith("-")
            and not line.startswith("---")
            and any(marker in line.lower() for marker in assertion_markers)
        )
        added_assertions = sum(
            1
            for line in diff.splitlines()
            if line.startswith("+")
            and not line.startswith("+++")
            and any(marker in line.lower() for marker in assertion_markers)
        )
        added_skip = any(
            line.startswith("+") and any(marker in line.lower() for marker in skip_markers)
            for line in diff.splitlines()
        )
        if removed_assertions > added_assertions or added_skip:
            weakened.append(path)
    return tuple(weakened)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _numstat(root: Path, base_ref: str) -> tuple[int, int]:
    insertions = 0
    deletions = 0
    for line in str(run_git(root, "diff", "--numstat", base_ref)).splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        added, removed, _ = parts
        if added.isdecimal():
            insertions += int(added)
        if removed.isdecimal():
            deletions += int(removed)
    return insertions, deletions


def _line_count(path: Path) -> int:
    count = 0
    last = b""
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            count += chunk.count(b"\n")
            last = chunk[-1:]
    if path.stat().st_size and last != b"\n":
        count += 1
    return count
