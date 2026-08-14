from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
import git_service

from contracts import (
    AgentRole,
    ContractResult,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingStatus,
    ReviewEvidence,
    ValidationAttestation,
    ValidationRecord,
    ValidationStatus,
)
from git_service import (
    CommitAuthorization,
    GitTransactionError,
    SliceGitBoundary,
    begin_slice,
    commit_managed_audit_report,
    commit_slice,
    inspect_repository,
    prepare_new_watch_task_branch,
    require_committed_file_at_head,
    resume_slice,
)
from repo_changes import collect_repository_changes


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _new_repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "master")
    _git(repository, "config", "user.name", "Slice Tests")
    _git(repository, "config", "user.email", "slice-tests@example.invalid")
    (repository / "base.txt").write_text("base\n", encoding="utf-8")
    (repository / ".gitignore").write_text("*.lock\n", encoding="utf-8")
    _git(repository, "add", "base.txt", ".gitignore")
    _git(repository, "commit", "-m", "base")
    _git(repository, "switch", "-c", "feature/transaction")
    return repository, _git(repository, "rev-parse", "HEAD")


def _authorization(
    repository: Path,
    start_commit: str,
    *,
    claude_approval: bool = True,
) -> CommitAuthorization:
    fingerprint = collect_repository_changes(repository, start_commit).fingerprint
    command = "python3 -m pytest tests/ -v"
    attestation = ValidationAttestation(
        attestation_id="slice-09-validation",
        diff_fingerprint=fingerprint,
        expected_commands=(command,),
        records=(ValidationRecord(ValidationStatus.PASS, command, 0),),
        output_digest="a" * 64,
        summary="green",
    )

    def review(role: AgentRole, approval: bool) -> ContractResult:
        return ContractResult(
            reviewer=role,
            approval=approval,
            stopped=False,
            stop_request=None,
            validation=attestation,
            test_files=(),
            pre_mortem="a future status shape is not handled",
            evidence=ReviewEvidence("scope and commit", "TOCTOU", "concurrent edit"),
            findings=(),
            anchors=(),
        )

    return CommitAuthorization(
        slice_id=9,
        diff_fingerprint=fingerprint,
        attestation=attestation,
        claude_review=review(AgentRole.CLAUDE, claude_approval),
        antigravity_review=review(AgentRole.ANTIGRAVITY, True),
    )


def test_new_watch_task_creates_missing_target_branch(tmp_path: Path) -> None:
    repository, base = _new_repository(tmp_path)
    _git(repository, "switch", "master")

    prepared = prepare_new_watch_task_branch(
        repository,
        target_branch="feature/inbox-created",
    )

    assert prepared.action == "created"
    assert prepared.previous_branch == "master"
    assert prepared.identity.branch == "feature/inbox-created"
    assert prepared.identity.head == base
    assert _git(repository, "branch", "--show-current") == "feature/inbox-created"


def test_new_watch_task_switches_to_existing_target_and_extends_its_head(
    tmp_path: Path,
) -> None:
    repository, _ = _new_repository(tmp_path)
    (repository / "prior.txt").write_text("prior branch work\n", encoding="utf-8")
    _git(repository, "add", "prior.txt")
    _git(repository, "commit", "-m", "prior target work")
    target_head = _git(repository, "rev-parse", "HEAD")
    _git(repository, "switch", "master")

    prepared = prepare_new_watch_task_branch(
        repository,
        target_branch="feature/transaction",
    )

    assert prepared.action == "switched"
    assert prepared.previous_branch == "master"
    assert prepared.identity.branch == "feature/transaction"
    assert prepared.identity.head == target_head


def test_new_watch_task_keeps_active_target_with_existing_changes(
    tmp_path: Path,
) -> None:
    repository, _ = _new_repository(tmp_path)
    (repository / "prior.txt").write_text("prior branch work\n", encoding="utf-8")
    _git(repository, "add", "prior.txt")
    _git(repository, "commit", "-m", "prior target work")
    target_head = _git(repository, "rev-parse", "HEAD")

    prepared = prepare_new_watch_task_branch(
        repository,
        target_branch="feature/transaction",
    )

    assert prepared.action == "already-active"
    assert prepared.previous_branch == "feature/transaction"
    assert prepared.identity.head == target_head


def test_new_watch_task_refuses_switch_with_foreign_worktree_changes(
    tmp_path: Path,
) -> None:
    repository, _ = _new_repository(tmp_path)
    _git(repository, "switch", "master")
    (repository / "foreign.txt").write_text("do not move\n", encoding="utf-8")

    with pytest.raises(GitTransactionError, match="requires a clean"):
        prepare_new_watch_task_branch(
            repository,
            target_branch="feature/transaction",
        )

    assert _git(repository, "branch", "--show-current") == "master"
    assert (repository / "foreign.txt").read_text(encoding="utf-8") == "do not move\n"


def test_new_watch_task_preserves_only_untracked_task_control_paths(
    tmp_path: Path,
) -> None:
    repository, _ = _new_repository(tmp_path)
    _git(repository, "switch", "master")
    inbox = repository / "CustomInbox"
    inbox.mkdir()
    task = inbox / "bug.md"
    identity = inbox / ".bug.md.watch.json"
    task.write_text("task\n", encoding="utf-8")
    identity.write_text("identity\n", encoding="utf-8")

    prepared = prepare_new_watch_task_branch(
        repository,
        target_branch="feature/transaction",
        excluded_control_paths=(
            "CustomInbox/.bug.md.watch.json",
            "CustomInbox/bug.md",
        ),
    )

    assert prepared.action == "switched"
    assert task.read_text(encoding="utf-8") == "task\n"
    assert identity.read_text(encoding="utf-8") == "identity\n"


def test_new_watch_task_refuses_to_exclude_tracked_task_control_path(
    tmp_path: Path,
) -> None:
    repository, _ = _new_repository(tmp_path)
    _git(repository, "switch", "master")
    task = repository / "tracked-task.md"
    task.write_text("tracked task\n", encoding="utf-8")
    _git(repository, "add", "tracked-task.md")
    _git(repository, "commit", "-m", "track task")

    with pytest.raises(GitTransactionError, match="to be untracked"):
        prepare_new_watch_task_branch(
            repository,
            target_branch="feature/transaction",
            excluded_control_paths=("tracked-task.md",),
        )

    assert _git(repository, "branch", "--show-current") == "master"


def test_new_watch_task_reports_target_branch_control_path_collision(
    tmp_path: Path,
) -> None:
    repository, _ = _new_repository(tmp_path)
    target_task = repository / "CustomInbox" / "bug.md"
    target_task.parent.mkdir()
    target_task.write_text("target version\n", encoding="utf-8")
    _git(repository, "add", "CustomInbox/bug.md")
    _git(repository, "commit", "-m", "track task on target")
    _git(repository, "switch", "master")
    target_task.parent.mkdir(exist_ok=True)
    target_task.write_text("new inbox task\n", encoding="utf-8")

    with pytest.raises(GitTransactionError, match="git switch.*failed"):
        prepare_new_watch_task_branch(
            repository,
            target_branch="feature/transaction",
            excluded_control_paths=("CustomInbox/bug.md",),
        )

    assert _git(repository, "branch", "--show-current") == "master"
    assert target_task.read_text(encoding="utf-8") == "new inbox task\n"


def test_new_watch_task_reports_target_checked_out_in_another_worktree(
    tmp_path: Path,
) -> None:
    repository, _ = _new_repository(tmp_path)
    _git(repository, "switch", "master")
    other_worktree = tmp_path / "other-worktree"
    _git(
        repository,
        "worktree",
        "add",
        str(other_worktree),
        "feature/transaction",
    )

    with pytest.raises(GitTransactionError, match="git switch.*failed"):
        prepare_new_watch_task_branch(
            repository,
            target_branch="feature/transaction",
        )

    assert _git(repository, "branch", "--show-current") == "master"


def test_approved_plan_handoff_allows_unrelated_descendant_commit(tmp_path: Path) -> None:
    repository, _ = _new_repository(tmp_path)
    plan = repository / "docs" / "internal" / "plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("approved plan\n", encoding="utf-8")
    _git(repository, "add", "docs/internal/plan.md")
    _git(repository, "commit", "-m", "approve plan")
    approved_commit = _git(repository, "rev-parse", "HEAD")
    (repository / "tests.txt").write_text("platform fix\n", encoding="utf-8")
    _git(repository, "add", "tests.txt")
    _git(repository, "commit", "-m", "platform fix")

    require_committed_file_at_head(
        repository,
        expected_commit=approved_commit,
        relative_path="docs/internal/plan.md",
    )


def test_approved_plan_handoff_rejects_changed_plan_or_foreign_history(
    tmp_path: Path,
) -> None:
    repository, base = _new_repository(tmp_path)
    plan = repository / "docs" / "internal" / "plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("approved plan\n", encoding="utf-8")
    _git(repository, "add", "docs/internal/plan.md")
    _git(repository, "commit", "-m", "approve plan")
    approved_commit = _git(repository, "rev-parse", "HEAD")

    plan.write_text("changed plan\n", encoding="utf-8")
    with pytest.raises(GitTransactionError, match="working tree"):
        require_committed_file_at_head(
            repository,
            expected_commit=approved_commit,
            relative_path="docs/internal/plan.md",
        )

    _git(repository, "add", "docs/internal/plan.md")
    plan.write_text("approved plan\n", encoding="utf-8")
    with pytest.raises(GitTransactionError, match="index"):
        require_committed_file_at_head(
            repository,
            expected_commit=approved_commit,
            relative_path="docs/internal/plan.md",
        )

    _git(repository, "commit", "-m", "change plan")
    plan.write_text("changed plan\n", encoding="utf-8")
    with pytest.raises(GitTransactionError, match="HEAD"):
        require_committed_file_at_head(
            repository,
            expected_commit=approved_commit,
            relative_path="docs/internal/plan.md",
        )

    plan.write_text("approved plan\n", encoding="utf-8")
    _git(repository, "add", "docs/internal/plan.md")
    _git(repository, "commit", "-m", "restore plan")
    _git(repository, "switch", "-c", "feature/sibling", base)
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("approved plan\n", encoding="utf-8")
    _git(repository, "add", "docs/internal/plan.md")
    _git(repository, "commit", "-m", "sibling plan")
    with pytest.raises(GitTransactionError, match="ancestor"):
        require_committed_file_at_head(
            repository,
            expected_commit=approved_commit,
            relative_path="docs/internal/plan.md",
        )


def test_begin_slice_requires_expected_feature_branch_and_clean_tree(tmp_path: Path) -> None:
    repository, head = _new_repository(tmp_path)
    (repository / "ignored.lock").write_text("runtime\n", encoding="utf-8")

    boundary, identity = begin_slice(
        repository_root=repository,
        slice_id=9,
        expected_branch="feature/transaction",
        scope_paths=("src/new.py", "docs/slice.md"),
    )

    assert identity.head == head
    assert identity.remote_status == "local-only"
    assert identity.ahead is None
    assert identity.behind is None
    assert boundary.scope_paths == ("docs/slice.md", "src/new.py")
    assert boundary.start_commit == head
    assert len(boundary.start_fingerprint) == 64

    with pytest.raises(GitTransactionError, match="branch mismatch"):
        begin_slice(
            repository_root=repository,
            slice_id=9,
            expected_branch="feature/other",
            scope_paths=("src/new.py",),
        )

    (repository / "unexpected.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(GitTransactionError, match="clean"):
        begin_slice(
            repository_root=repository,
            slice_id=9,
            expected_branch="feature/transaction",
            scope_paths=("src/new.py",),
        )

    with pytest.raises(GitTransactionError, match="relative POSIX"):
        begin_slice(
            repository_root=repository,
            slice_id=9,
            expected_branch="feature/transaction",
            scope_paths=("../outside",),
        )


def test_resume_allows_only_persisted_scope_and_optional_pause_fingerprint(
    tmp_path: Path,
) -> None:
    repository, _ = _new_repository(tmp_path)
    boundary, _ = begin_slice(
        repository_root=repository,
        slice_id=9,
        expected_branch="feature/transaction",
        scope_paths=("allowed.txt",),
    )
    (repository / "allowed.txt").write_text("work\n", encoding="utf-8")
    paused = resume_slice(repository_root=repository, boundary=boundary)

    resumed = resume_slice(
        repository_root=repository,
        boundary=boundary,
        paused_fingerprint=paused.fingerprint,
    )
    assert resumed.paths == ("allowed.txt",)

    (repository / "allowed.txt").write_text("changed after pause\n", encoding="utf-8")
    with pytest.raises(GitTransactionError, match="persisted pause"):
        resume_slice(
            repository_root=repository,
            boundary=boundary,
            paused_fingerprint=paused.fingerprint,
        )
    (repository / "foreign.txt").write_text("foreign\n", encoding="utf-8")
    with pytest.raises(GitTransactionError, match="outside"):
        resume_slice(repository_root=repository, boundary=boundary)


def test_commit_blocks_stale_or_negative_review_without_mutating_index(
    tmp_path: Path,
) -> None:
    repository, head = _new_repository(tmp_path)
    boundary, _ = begin_slice(
        repository_root=repository,
        slice_id=9,
        expected_branch="feature/transaction",
        scope_paths=("allowed.txt",),
    )
    (repository / "allowed.txt").write_text("work\n", encoding="utf-8")
    authorization = _authorization(repository, head, claude_approval=False)

    with pytest.raises(GitTransactionError, match="approving claude"):
        commit_slice(
            repository_root=repository,
            boundary=boundary,
            authorization=authorization,
            title="blocked",
        )
    assert _git(repository, "diff", "--cached", "--name-only") == ""
    assert _git(repository, "rev-parse", "HEAD") == head

    approved = _authorization(repository, head)
    with pytest.raises(GitTransactionError, match="one non-empty line"):
        commit_slice(
            repository_root=repository,
            boundary=boundary,
            authorization=approved,
            title="invalid\ntitle",
        )
    assert _git(repository, "diff", "--cached", "--name-only") == ""

    (repository / "allowed.txt").write_text("stale\n", encoding="utf-8")
    with pytest.raises(GitTransactionError, match="stale"):
        commit_slice(
            repository_root=repository,
            boundary=boundary,
            authorization=approved,
            title="stale",
        )
    assert _git(repository, "diff", "--cached", "--name-only") == ""


def test_commit_authorization_blocks_foreign_owned_blocker_in_canonical_findings(
    tmp_path: Path,
) -> None:
    repository, head = _new_repository(tmp_path)
    boundary, _ = begin_slice(
        repository_root=repository,
        slice_id=9,
        expected_branch="feature/transaction",
        scope_paths=("allowed.txt",),
    )
    (repository / "allowed.txt").write_text("work\n", encoding="utf-8")
    authorization = _authorization(repository, head)
    foreign_blocker = FindingRecord(
        finding_id="A-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="Antigravity still requires a correction",
        acceptance_test="Antigravity closes the corrected finding",
        origin=FindingOrigin("09", 1, AgentRole.ANTIGRAVITY),
    )

    with pytest.raises(GitTransactionError, match="globally open blockers"):
        commit_slice(
            repository_root=repository,
            boundary=boundary,
            authorization=replace(authorization, findings=(foreign_blocker,)),
            title="blocked globally",
        )

    assert _git(repository, "diff", "--cached", "--name-only") == ""
    assert _git(repository, "rev-parse", "HEAD") == head


def test_commit_blocks_foreign_paths_and_foreign_index_entries(tmp_path: Path) -> None:
    repository, head = _new_repository(tmp_path)
    boundary, _ = begin_slice(
        repository_root=repository,
        slice_id=9,
        expected_branch="feature/transaction",
        scope_paths=("allowed.txt",),
    )
    (repository / "allowed.txt").write_text("work\n", encoding="utf-8")
    (repository / "foreign.txt").write_text("foreign\n", encoding="utf-8")
    authorization = _authorization(repository, head)
    with pytest.raises(GitTransactionError, match="outside"):
        commit_slice(
            repository_root=repository,
            boundary=boundary,
            authorization=authorization,
            title="foreign",
        )
    assert _git(repository, "diff", "--cached", "--name-only") == ""

    boundary = replace(boundary, scope_paths=("allowed.txt", "foreign.txt"))
    authorization = _authorization(repository, head)
    _git(repository, "add", "foreign.txt")
    restricted = replace(boundary, scope_paths=("allowed.txt",))
    with pytest.raises(GitTransactionError, match="foreign staged"):
        commit_slice(
            repository_root=repository,
            boundary=restricted,
            authorization=authorization,
            title="foreign staged",
        )


def test_post_staging_failure_restores_the_exact_previous_index(
    tmp_path: Path, monkeypatch
) -> None:
    repository, head = _new_repository(tmp_path)
    boundary, _ = begin_slice(
        repository_root=repository,
        slice_id=9,
        expected_branch="feature/transaction",
        scope_paths=("allowed.txt",),
    )
    (repository / "allowed.txt").write_text("work\n", encoding="utf-8")
    authorization = _authorization(repository, head)
    original_collect = git_service.collect_repository_changes
    calls = 0

    def changed_after_staging(repository_root: Path, merge_base: str):
        nonlocal calls
        calls += 1
        changes = original_collect(repository_root, merge_base)
        if calls == 2:
            return replace(changes, fingerprint="f" * 64)
        return changes

    monkeypatch.setattr(
        git_service, "collect_repository_changes", changed_after_staging
    )
    with pytest.raises(GitTransactionError, match="changed during exact staging"):
        commit_slice(
            repository_root=repository,
            boundary=boundary,
            authorization=authorization,
            title="restore index",
        )

    assert _git(repository, "diff", "--cached", "--name-only") == ""
    assert _git(repository, "status", "--short") == "?? allowed.txt"
    assert _git(repository, "rev-parse", "HEAD") == head


def test_commit_stages_only_exact_slice_paths_and_records_result(tmp_path: Path) -> None:
    repository, head = _new_repository(tmp_path)
    boundary, _ = begin_slice(
        repository_root=repository,
        slice_id=9,
        expected_branch="feature/transaction",
        scope_paths=("base.txt", "new.txt"),
    )
    (repository / "base.txt").write_text("changed\n", encoding="utf-8")
    (repository / "new.txt").write_text("new\n", encoding="utf-8")
    authorization = _authorization(repository, head)

    result = commit_slice(
        repository_root=repository,
        boundary=boundary,
        authorization=authorization,
        title="branch and commit transaction",
    )

    assert result.slice_id == 9
    assert result.message == "Slice 09: branch and commit transaction"
    assert result.diff_fingerprint == authorization.diff_fingerprint
    assert result.committed_paths == ("base.txt", "new.txt")
    assert result.commit_hash == _git(repository, "rev-parse", "HEAD")
    assert result.commit_hash != head
    assert _git(repository, "status", "--short") == ""
    assert _git(repository, "show", "-s", "--format=%s", "HEAD") == result.message


def test_commit_handles_exact_rename_scope_without_including_predecessor_commit(
    tmp_path: Path,
) -> None:
    repository, predecessor = _new_repository(tmp_path)
    (repository / "previous.txt").write_text("previous slice\n", encoding="utf-8")
    _git(repository, "add", "previous.txt")
    _git(repository, "commit", "-m", "previous slice")
    start_commit = _git(repository, "rev-parse", "HEAD")
    boundary, _ = begin_slice(
        repository_root=repository,
        slice_id=9,
        expected_branch="feature/transaction",
        scope_paths=("base.txt", "renamed.txt"),
    )
    _git(repository, "mv", "base.txt", "renamed.txt")
    authorization = _authorization(repository, start_commit)

    result = commit_slice(
        repository_root=repository,
        boundary=boundary,
        authorization=authorization,
        title="exact rename",
    )

    assert boundary.start_commit == start_commit
    assert boundary.start_commit != predecessor
    assert result.committed_paths == ("renamed.txt",)
    assert "previous.txt" not in result.committed_paths
    assert _git(repository, "status", "--short") == ""


def test_commit_completes_source_deletion_for_partially_staged_rename(
    tmp_path: Path,
) -> None:
    repository, head = _new_repository(tmp_path)
    boundary, _ = begin_slice(
        repository_root=repository,
        slice_id=9,
        expected_branch="feature/transaction",
        scope_paths=("base.txt", "renamed.txt"),
    )
    (repository / "base.txt").rename(repository / "renamed.txt")
    _git(repository, "add", "renamed.txt")
    assert _git(repository, "diff", "--cached", "--name-status") == "A\trenamed.txt"
    authorization = _authorization(repository, head)

    result = commit_slice(
        repository_root=repository,
        boundary=boundary,
        authorization=authorization,
        title="partially staged rename",
    )

    assert result.committed_paths == ("renamed.txt",)
    assert not (repository / "base.txt").exists()
    assert set(_git(repository, "ls-tree", "-r", "--name-only", "HEAD").splitlines()) == {
        ".gitignore",
        "renamed.txt",
    }
    assert _git(repository, "status", "--short") == ""


def test_commit_stages_an_exact_tracked_deletion(tmp_path: Path) -> None:
    repository, head = _new_repository(tmp_path)
    boundary, _ = begin_slice(
        repository_root=repository,
        slice_id=9,
        expected_branch="feature/transaction",
        scope_paths=("base.txt",),
    )
    (repository / "base.txt").unlink()
    authorization = _authorization(repository, head)

    result = commit_slice(
        repository_root=repository,
        boundary=boundary,
        authorization=authorization,
        title="exact deletion",
    )

    assert result.committed_paths == ("base.txt",)
    assert not (repository / "base.txt").exists()
    assert _git(repository, "status", "--short") == ""


def test_commit_rejects_non_passing_or_foreign_review_binding(tmp_path: Path) -> None:
    repository, head = _new_repository(tmp_path)
    boundary, _ = begin_slice(
        repository_root=repository,
        slice_id=9,
        expected_branch="feature/transaction",
        scope_paths=("allowed.txt",),
    )
    (repository / "allowed.txt").write_text("work\n", encoding="utf-8")
    authorization = _authorization(repository, head)
    command = authorization.attestation.expected_commands[0]
    failing = ValidationAttestation(
        attestation_id="red",
        diff_fingerprint=authorization.diff_fingerprint,
        expected_commands=(command,),
        records=(ValidationRecord(ValidationStatus.FAIL, command, 1),),
        output_digest="b" * 64,
        summary="red",
    )

    with pytest.raises(GitTransactionError, match="passing"):
        commit_slice(
            repository_root=repository,
            boundary=boundary,
            authorization=replace(authorization, attestation=failing),
            title="red",
        )

    wrong_role = replace(
        authorization,
        claude_review=replace(
            authorization.claude_review,
            reviewer=AgentRole.ANTIGRAVITY,
        ),
    )
    with pytest.raises(GitTransactionError, match="claude review role"):
        commit_slice(
            repository_root=repository,
            boundary=boundary,
            authorization=wrong_role,
            title="wrong role",
        )
    assert _git(repository, "diff", "--cached", "--name-only") == ""


def test_commit_accepts_complete_red_attestation_only_with_named_followup(
    tmp_path: Path,
) -> None:
    repository, head = _new_repository(tmp_path)
    boundary, _ = begin_slice(
        repository_root=repository,
        slice_id=9,
        expected_branch="feature/transaction",
        scope_paths=("allowed.txt",),
    )
    (repository / "allowed.txt").write_text("work\n", encoding="utf-8")
    authorization = _authorization(repository, head)
    command = authorization.attestation.expected_commands[0]
    failing = ValidationAttestation(
        attestation_id="red",
        diff_fingerprint=authorization.diff_fingerprint,
        expected_commands=(command,),
        records=(ValidationRecord(ValidationStatus.FAIL, command, 1, "known red"),),
        output_digest="b" * 64,
        summary="red pending Slice 10",
    )
    authorized = replace(
        authorization,
        attestation=failing,
        claude_review=replace(
            authorization.claude_review,
            validation=failing,
            red_state_followup_slice="Slice 10",
        ),
        antigravity_review=replace(
            authorization.antigravity_review,
            validation=failing,
            red_state_followup_slice="Slice 10",
        ),
        red_state_followup_slice="Slice 10",
    )

    result = commit_slice(
        repository_root=repository,
        boundary=boundary,
        authorization=authorized,
        title="documented red state",
    )

    assert result.commit_hash == _git(repository, "rev-parse", "HEAD")


def test_final_audit_commit_restores_index_after_unexpected_staging_error(
    tmp_path: Path, monkeypatch
) -> None:
    repository, head = _new_repository(tmp_path)
    audit_path = "docs/internal/task-review-12345678.md"
    audit = repository / audit_path
    audit.parent.mkdir(parents=True)
    audit.write_text("# Overall audit\n\nInitial state.\n", encoding="utf-8")
    _git(repository, "add", audit_path)
    _git(repository, "commit", "-m", "add audit")
    start_head = _git(repository, "rev-parse", "HEAD")
    audit.write_text("# Overall audit\n\nFinal state.\n", encoding="utf-8")
    real_staged_paths = git_service._staged_paths
    calls = 0

    def fail_after_staging(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated unexpected staging check failure")
        return real_staged_paths(*args, **kwargs)

    monkeypatch.setattr(git_service, "_staged_paths", fail_after_staging)

    with pytest.raises(RuntimeError, match="unexpected staging check"):
        commit_managed_audit_report(
            repository_root=repository,
            branch="feature/transaction",
            audit_path=audit_path,
        )

    assert start_head != head
    assert _git(repository, "rev-parse", "HEAD") == start_head
    assert _git(repository, "diff", "--cached", "--name-only") == ""
    assert _git(repository, "diff", "--name-only") == audit_path


def test_repository_identity_rejects_detached_head(tmp_path: Path) -> None:
    repository, head = _new_repository(tmp_path)
    _git(repository, "checkout", "--detach", head)
    with pytest.raises(GitTransactionError, match="detached HEAD"):
        inspect_repository(repository)


def test_repository_identity_reports_local_upstream_divergence(tmp_path: Path) -> None:
    repository, _ = _new_repository(tmp_path)
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(repository, "remote", "add", "origin", str(remote))
    _git(repository, "push", "-u", "origin", "feature/transaction")

    tracking = inspect_repository(repository)
    assert tracking.upstream == "origin/feature/transaction"
    assert tracking.ahead == 0
    assert tracking.behind == 0
    assert tracking.remote_status == (
        "tracking origin/feature/transaction; ahead=0; behind=0"
    )

    (repository / "local.txt").write_text("ahead\n", encoding="utf-8")
    _git(repository, "add", "local.txt")
    _git(repository, "commit", "-m", "local ahead")
    ahead = inspect_repository(repository)
    assert ahead.ahead == 1
    assert ahead.behind == 0
