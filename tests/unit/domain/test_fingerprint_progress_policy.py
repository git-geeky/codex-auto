from codex_auto.domain.fingerprint import FingerprintEngine
from codex_auto.domain.models import PolicyInput, ValidationSummary
from codex_auto.domain.policy import PolicyEvaluator
from codex_auto.domain.progress import ProgressEvaluator


def test_fingerprint_normalizes_volatile_paths_uuid_time_port_and_line_numbers() -> None:
    first = (
        "C:\\state\\worktrees\\abc\\src\\app.py:41 error at 2026-07-13T00:01:02Z "
        "id=123e4567-e89b-12d3-a456-426614174000 port 51234"
    )
    second = (
        "/tmp/worktrees/xyz/src/app.py:99 error at 2026-07-14T09:11:12Z "
        "id=aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee port 61234"
    )
    engine = FingerprintEngine(worktree_roots=("C:/state/worktrees/abc", "/tmp/worktrees/xyz"))

    assert engine.fingerprint({"stderr": first}) == engine.fingerprint({"stderr": second})


def test_progress_requires_external_improvement() -> None:
    before = ValidationSummary(
        stage_index=1,
        failing_tests=frozenset({"test_a", "test_b"}),
        failure_count=2,
        localized=False,
    )
    fewer = ValidationSummary(
        stage_index=1,
        failing_tests=frozenset({"test_b"}),
        failure_count=1,
        localized=False,
    )
    prose_only = ValidationSummary(
        stage_index=1,
        failing_tests=frozenset({"test_a", "test_b"}),
        failure_count=2,
        localized=False,
    )
    assert ProgressEvaluator().has_progress(before, fewer)
    assert not ProgressEvaluator().has_progress(before, prose_only)


def test_policy_detects_forbidden_out_of_scope_and_size_violations() -> None:
    findings = PolicyEvaluator().evaluate(
        PolicyInput(
            changed_paths=("src/app.py", "secrets/token.txt"),
            allowed_globs=("src/**",),
            forbidden_globs=("secrets/**",),
            high_risk_globs=("**/auth/**",),
            protected_test_globs=("tests/**",),
            insertions=101,
            deletions=0,
            max_changed_files=1,
            max_insertions=100,
            max_deletions=100,
        )
    )
    codes = {finding.code for finding in findings}
    assert codes == {
        "forbidden_path",
        "outside_allowed_paths",
        "too_many_files",
        "too_many_insertions",
    }


def test_policy_marks_high_risk_path_for_stronger_review() -> None:
    findings = PolicyEvaluator().evaluate(
        PolicyInput(
            changed_paths=("src/auth/session.py",),
            high_risk_globs=("**/auth/**",),
        )
    )
    assert [(finding.code, finding.blocking) for finding in findings] == [("high_risk_path", False)]


def test_policy_recursive_globs_match_nested_and_root_paths() -> None:
    findings = PolicyEvaluator().evaluate(
        PolicyInput(
            changed_paths=("tests/unit/deep/test_app.py", "main.tf", "auth/session.py"),
            forbidden_globs=("tests/**",),
            high_risk_globs=("**/*.tf", "**/auth/**"),
        )
    )

    assert [(finding.code, finding.path) for finding in findings] == [
        ("forbidden_path", "tests/unit/deep/test_app.py"),
        ("high_risk_path", "main.tf"),
        ("high_risk_path", "auth/session.py"),
    ]
