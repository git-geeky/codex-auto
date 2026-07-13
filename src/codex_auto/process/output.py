"""Bounded output retention that preserves both beginning and end."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BoundedOutput:
    text: str
    truncated: bool
    total_bytes: int


class BoundedTextBuffer:
    def __init__(self, limit_bytes: int) -> None:
        if limit_bytes < 2:
            raise ValueError("output limit must be at least two bytes")
        self.limit_bytes = limit_bytes
        self._all = bytearray()
        self._head = bytearray()
        self._tail_chunks: deque[bytes] = deque()
        self._tail_bytes = 0
        self._total = 0
        self._truncated = False
        self._lock = threading.Lock()

    def append(self, text: str) -> None:
        encoded = text.encode("utf-8", errors="replace")
        with self._lock:
            self._total += len(encoded)
            if not self._truncated and len(self._all) + len(encoded) <= self.limit_bytes:
                self._all.extend(encoded)
                return
            if not self._truncated:
                combined = bytes(self._all) + encoded
                head_size = self.limit_bytes // 2
                tail_size = self.limit_bytes - head_size
                self._head = bytearray(combined[:head_size])
                tail = combined[-tail_size:]
                self._tail_chunks.append(tail)
                self._tail_bytes = len(tail)
                self._all.clear()
                self._truncated = True
                return
            tail_size = self.limit_bytes - len(self._head)
            self._append_tail(encoded, tail_size)

    def _append_tail(self, encoded: bytes, tail_size: int) -> None:
        if len(encoded) >= tail_size:
            self._tail_chunks.clear()
            retained = encoded[-tail_size:]
            self._tail_chunks.append(retained)
            self._tail_bytes = len(retained)
            return
        self._tail_chunks.append(encoded)
        self._tail_bytes += len(encoded)
        overflow = self._tail_bytes - tail_size
        while overflow > 0:
            first = self._tail_chunks[0]
            if len(first) <= overflow:
                self._tail_chunks.popleft()
                self._tail_bytes -= len(first)
                overflow -= len(first)
                continue
            self._tail_chunks[0] = first[overflow:]
            self._tail_bytes -= overflow
            overflow = 0

    def finish(self) -> BoundedOutput:
        with self._lock:
            retained = (
                bytes(self._head) + b"".join(self._tail_chunks)
                if self._truncated
                else bytes(self._all)
            )
            return BoundedOutput(
                text=retained.decode("utf-8", errors="replace"),
                truncated=self._truncated,
                total_bytes=self._total,
            )
