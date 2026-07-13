"""Deterministic candidate policy checks."""

from __future__ import annotations

import re

from codex_auto.domain.models import PolicyFinding, PolicyInput


def path_matches(path: str, patterns: tuple[str, ...]) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    return any(re.fullmatch(_glob_regex(pattern), normalized) is not None for pattern in patterns)


def _glob_regex(pattern: str) -> str:
    normalized = pattern.replace("\\", "/").lstrip("./")
    parts: list[str] = []
    index = 0
    while index < len(normalized):
        character = normalized[index]
        if character == "*":
            if index + 1 < len(normalized) and normalized[index + 1] == "*":
                index += 2
                if index < len(normalized) and normalized[index] == "/":
                    parts.append("(?:.*/)?")
                    index += 1
                else:
                    parts.append(".*")
                continue
            parts.append("[^/]*")
        elif character == "?":
            parts.append("[^/]")
        else:
            parts.append(re.escape(character))
        index += 1
    return "".join(parts)


class PolicyEvaluator:
    def evaluate(self, policy: PolicyInput) -> tuple[PolicyFinding, ...]:
        findings: list[PolicyFinding] = []
        for path in policy.changed_paths:
            if path_matches(path, policy.forbidden_globs):
                findings.append(
                    PolicyFinding("forbidden_path", f"forbidden path changed: {path}", path=path)
                )
            if policy.allowed_globs and not path_matches(path, policy.allowed_globs):
                findings.append(
                    PolicyFinding(
                        "outside_allowed_paths",
                        f"path is outside configured scope: {path}",
                        path=path,
                    )
                )
            if path_matches(path, policy.high_risk_globs):
                findings.append(
                    PolicyFinding(
                        "high_risk_path",
                        f"high-risk path requires stronger review: {path}",
                        blocking=False,
                        path=path,
                    )
                )
        for path in policy.deleted_paths:
            if path_matches(path, policy.protected_test_globs):
                findings.append(
                    PolicyFinding(
                        "protected_test_deleted", f"protected test deleted: {path}", path=path
                    )
                )
        for path in policy.weakened_tests:
            findings.append(
                PolicyFinding(
                    "test_weakening", f"test assertions may be weakened: {path}", path=path
                )
            )
        if len(policy.changed_paths) > policy.max_changed_files:
            findings.append(PolicyFinding("too_many_files", "changed-file limit exceeded"))
        if policy.insertions > policy.max_insertions:
            findings.append(PolicyFinding("too_many_insertions", "insertion limit exceeded"))
        if policy.deletions > policy.max_deletions:
            findings.append(PolicyFinding("too_many_deletions", "deletion limit exceeded"))
        if policy.head_changed:
            findings.append(PolicyFinding("head_changed", "candidate changed Git HEAD"))
        if policy.branch_changed:
            findings.append(PolicyFinding("branch_changed", "candidate changed Git branch"))
        if policy.new_commit:
            findings.append(PolicyFinding("new_commit", "candidate created a commit"))
        if policy.unrestricted_codex_flags:
            findings.append(
                PolicyFinding("unrestricted_codex_flags", "unrestricted Codex flags were used")
            )
        return tuple(findings)
