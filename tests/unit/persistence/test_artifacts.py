from __future__ import annotations

from pathlib import Path

import pytest

from codex_auto.persistence.artifacts import FilesystemArtifactStore, UnsafeArtifactPathError


def test_filesystem_artifact_store_writes_atomically_inside_root(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")

    written = store.write_bytes("runs/run-1/evidence.json", b'{"ok":true}\n')

    assert written.read_bytes() == b'{"ok":true}\n'
    assert written.is_relative_to(store.root)
    assert not tuple(written.parent.glob("*.tmp"))


@pytest.mark.parametrize("path", ("../escape", "runs/../../escape", "/absolute"))
def test_filesystem_artifact_store_rejects_escape(path: str, tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")

    with pytest.raises(UnsafeArtifactPathError):
        store.write_bytes(path, b"unsafe")
