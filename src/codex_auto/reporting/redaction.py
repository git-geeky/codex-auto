"""Redact common credentials before persistence or display."""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_PATTERNS = (
    r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+",
    r"(?i)(\b(?:api[_-]?key|token|password|secret)\s*[=:]\s*)[^\s]+",
    r"\bsk-[A-Za-z0-9_-]{8,}\b",
    (
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
        r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    ),
    r"(?i)(https?://[^:/\s]+:)[^@/\s]+@",
)


@dataclass(frozen=True, slots=True)
class Redactor:
    secret_values: tuple[str, ...] = ()
    extra_patterns: tuple[str, ...] = ()

    def redact(self, text: str) -> str:
        redacted = text
        for pattern in (*DEFAULT_PATTERNS, *self.extra_patterns):
            compiled = re.compile(pattern, re.DOTALL)
            if compiled.groups:
                redacted = compiled.sub(r"\1<redacted>", redacted)
            else:
                redacted = compiled.sub("<redacted>", redacted)
        for value in sorted(self.secret_values, key=len, reverse=True):
            if value:
                redacted = redacted.replace(value, "<redacted>")
        return redacted
