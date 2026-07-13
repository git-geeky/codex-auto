"""Stable normalized failure fingerprints."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.I
)
TIMESTAMP_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\b")
PORT_RE = re.compile(r"\bport\s+\d{2,5}\b", re.I)
LINE_RE = re.compile(r"(\.[A-Za-z0-9]{1,8}):\d+\b")
PERCENT_RE = re.compile(r"\b\d+(?:\.\d+)?%")


@dataclass(frozen=True, slots=True)
class FingerprintEngine:
    worktree_roots: tuple[str, ...] = ()

    def normalize_text(self, value: str) -> str:
        normalized = ANSI_RE.sub("", value).replace("\\", "/")
        for root in self.worktree_roots:
            normalized = re.sub(
                re.escape(root.replace("\\", "/")),
                "<WORKTREE>",
                normalized,
                flags=re.IGNORECASE,
            )
        normalized = UUID_RE.sub("<UUID>", normalized)
        normalized = TIMESTAMP_RE.sub("<TIMESTAMP>", normalized)
        normalized = PORT_RE.sub("port <PORT>", normalized)
        normalized = LINE_RE.sub(r"\1:<LINE>", normalized)
        normalized = PERCENT_RE.sub("<PERCENT>", normalized)
        return " ".join(normalized.split())

    def canonicalize(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.normalize_text(value)
        if isinstance(value, dict):
            return {str(key): self.canonicalize(value[key]) for key in sorted(value)}
        if isinstance(value, (list, tuple, set, frozenset)):
            canonical = [self.canonicalize(item) for item in value]
            return sorted(canonical, key=lambda item: json.dumps(item, sort_keys=True))
        return value

    def fingerprint(self, value: Any) -> str:
        canonical = json.dumps(
            self.canonicalize(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()
