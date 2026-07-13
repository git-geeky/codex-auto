"""Bounded filesystem artifact storage outside candidate repositories."""

from __future__ import annotations

import os
import uuid
from pathlib import Path, PurePath


class UnsafeArtifactPathError(ValueError):
    """Raised when an artifact path could escape or traverse the store root."""


class FilesystemArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            self.root.chmod(0o700)

    def write_bytes(self, relative_path: str, content: bytes) -> Path:
        destination = self._safe_path(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_bytes(content)
            if os.name != "nt":
                temporary.chmod(0o600)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    def _safe_path(self, relative_path: str) -> Path:
        pure = PurePath(relative_path)
        if not pure.parts or pure.is_absolute() or ".." in pure.parts:
            raise UnsafeArtifactPathError(f"unsafe artifact path: {relative_path}")
        current = self.root
        for part in pure.parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise UnsafeArtifactPathError(f"symlink artifact path: {relative_path}")
        try:
            current.resolve(strict=False).relative_to(self.root)
        except ValueError as error:
            raise UnsafeArtifactPathError(
                f"artifact path escapes store: {relative_path}"
            ) from error
        return current
