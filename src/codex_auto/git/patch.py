"""Safe patch and untracked-file export."""

from __future__ import annotations

import hashlib
import json
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePath

from codex_auto.git.repository import GitSnapshot


class UnsafePathError(ValueError):
    """A candidate path escapes the owned worktree or traverses a symlink."""


@dataclass(frozen=True, slots=True)
class ExportResult:
    patch_path: Path
    untracked_archive: Path
    manifest_path: Path
    checksums_path: Path


def safe_candidate_path(root: Path, relative: str) -> Path:
    pure = PurePath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        raise UnsafePathError(f"unsafe candidate path: {relative}")
    resolved_root = root.resolve()
    candidate = resolved_root.joinpath(*pure.parts)
    current = resolved_root
    for part in pure.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise UnsafePathError(f"symlink candidate path: {relative}")
    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as error:
        raise UnsafePathError(f"candidate path escapes worktree: {relative}") from error
    return resolved_candidate


class PatchExporter:
    def export(self, worktree: Path, final_dir: Path, snapshot: GitSnapshot) -> ExportResult:
        final_dir.mkdir(parents=True, exist_ok=True)
        patch_path = final_dir / "final.patch"
        archive_path = final_dir / "untracked-files.tar"
        manifest_path = final_dir / "changed-files.json"
        checksums_path = final_dir / "checksums.json"

        patch = snapshot.binary_diff.encode("utf-8")
        if patch and not patch.endswith(b"\n"):
            patch += b"\n"
        patch_path.write_bytes(patch)

        with tarfile.open(archive_path, "w") as archive:
            for item in snapshot.untracked_files:
                path = safe_candidate_path(worktree, item.path)
                if not path.is_file() or path.is_symlink():
                    raise UnsafePathError(f"untracked path is not a regular file: {item.path}")
                archive.add(path, arcname=item.path, recursive=False)

        manifest_path.write_text(
            json.dumps(
                {
                    "changed_files": list(snapshot.changed_files),
                    "untracked_files": [
                        {"path": item.path, "size": item.size, "sha256": item.sha256}
                        for item in snapshot.untracked_files
                    ],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        checksums = {path.name: _sha256(path) for path in (patch_path, archive_path, manifest_path)}
        checksums_path.write_text(
            json.dumps(checksums, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return ExportResult(patch_path, archive_path, manifest_path, checksums_path)


def export_is_complete(final_dir: Path) -> bool:
    checksums_path = final_dir / "checksums.json"
    if not checksums_path.is_file():
        return False
    try:
        payload = json.loads(checksums_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(payload, dict):
        return False
    required = {"final.patch", "untracked-files.tar", "changed-files.json"}
    if not required.issubset(payload):
        return False
    for name, expected in payload.items():
        if not isinstance(name, str) or not isinstance(expected, str):
            return False
        path = final_dir / name
        if not path.is_file() or _sha256(path) != expected:
            return False
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
