from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from artifact_models import RoleProfilePayload, RunProfilePayload, stable_side_effect_key
from git_service import GitTransactionError
import orchestrator
from orchestrator import ProductionWorkflowDriver
from side_effects import SideEffectBoundaryPhase, SideEffectExecutor, SideEffectSpec
import workflow_completion
from workflow_production import _finish_final_review, _run_production_transition_loop
from workflow import WorkflowHistory
from workflow_state import WorkUnitKind, WorkUnitStatus


def git(root: Path, *args: str) -> str:
    result = subprocess.run(("git", *args), cwd=root, text=True,
                            capture_output=True, check=True)
    return result.stdout.strip()


def repository(tmp_path: Path, *, conflict: bool = False,
               name_conflict: bool = False) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Test Operator")
    git(root, "config", "user.email", "operator@example.invalid")
    (root / "docs/internal/archive").mkdir(parents=True)
    (root / "docs/internal/existing.md").write_text("existing\n")
    (root / "shared.txt").write_text("base\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    git(root, "switch", "-qc", "feature/task")
    (root / "docs/internal/plan.md").write_text("plan\n")
    (root / "docs/internal/earlier.md").write_text("earlier\n")
    (root / "docs/internal/notes.txt").write_text("notes\n")
    (root / "docs/internal/existing.md").write_text("existing modified\n")
    (root / "docs/internal/nested").mkdir()
    (root / "docs/internal/nested/keep.md").write_text("nested\n")
    if name_conflict:
        (root / workflow_completion.ARCHIVE_RELATIVE / "plan.md").write_text("collision\n")
    if conflict:
        (root / "shared.txt").write_text("target\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "first cycle")
    (root / "docs/internal/earlier.md").write_text("later edit\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "second cycle")
    if conflict:
        git(root, "switch", "main")
        (root / "shared.txt").write_text("base edit\n")
        git(root, "add", ".")
        git(root, "commit", "-qm", "conflicting base")
        git(root, "switch", "feature/task")
    return root


class MemoryBridge:
    def __init__(self) -> None:
        self.effects: dict[str, SimpleNamespace] = {}
        self.store = self

    def current_chain(self) -> tuple[()]:
        return ()

    def record_side_effect_intent(self, *, effect_class: str,
                                  work_unit_id: str, operation: tuple[str, ...],
                                  **_: object) -> tuple[None, bool]:
        key = stable_side_effect_key(effect_class, work_unit_id, operation)
        created = key not in self.effects
        self.effects.setdefault(key, SimpleNamespace(
            effect_class=effect_class, work_unit_id=work_unit_id,
            operation=operation, result=None,
        ))
        return None, created

    def side_effect_result(self, *, effect_class: str, work_unit_id: str,
                           operation: tuple[str, ...]) -> str | None:
        key = stable_side_effect_key(effect_class, work_unit_id, operation)
        return self.effects[key].result

    def record_side_effect_result(self, *, effect_class: str, work_unit_id: str,
                                  operation: tuple[str, ...], result: str,
                                  **_: object) -> None:
        key = stable_side_effect_key(effect_class, work_unit_id, operation)
        self.effects[key].result = result


@dataclass
class CompletionRun:
    root: Path
    bridge: MemoryBridge
    profile: RunProfilePayload
    run_id: str = "run"
    branch: str = "feature/task"

    def run(self, monkeypatch: pytest.MonkeyPatch,
            observer=None) -> str:  # type: ignore[no-untyped-def]
        monkeypatch.setattr(workflow_completion, "replay_artifacts", lambda *_: SimpleNamespace(
            run_profile=self.profile, side_effects=tuple(self.bridge.effects.values()),
        ))
        state = SimpleNamespace(branch=self.branch, current_work_unit_id=3,
                                run_id=self.run_id)
        return workflow_completion.complete_chain(
            self.root, state, self.bridge, SideEffectExecutor(self.bridge, observer),
            lambda effect_class, operation, *, fingerprint: SideEffectSpec(
                effect_class, "3", operation, fingerprint,
            ),
        )


def completion(root: Path, *, merge: bool = True,
               archive_pattern: str | None = None,
               run_id: str = "run", branch: str = "feature/task") -> CompletionRun:
    return CompletionRun(root, MemoryBridge(), RunProfilePayload(
        RoleProfilePayload("implementer", "high"),
        RoleProfilePayload("reviewer", "high"),
        merge_completed_branch=merge, base_branch="main",
        archive_run_directory=archive_pattern,
    ), run_id, branch)


@pytest.mark.parametrize("conflict", ("directory", "file", "symlink", "parent_file", "parent_symlink"))
def test_new_archive_destination_conflicts_before_work(
    tmp_path: Path, conflict: str,
) -> None:
    root = repository(tmp_path)
    profile = completion(root, archive_pattern="{year}/{run_id}").profile
    parent = root / workflow_completion.ARCHIVE_RELATIVE / "2026"
    target = parent / "watch-20260925-abc"
    if conflict.startswith("parent"):
        parent.parent.mkdir(parents=True, exist_ok=True)
        if conflict == "parent_file":
            parent.write_text("occupied")
        else:
            parent.symlink_to(root / "docs/internal")
        expected = parent
    else:
        parent.mkdir(parents=True)
        if conflict == "directory":
            target.mkdir()
        elif conflict == "file":
            target.write_text("occupied")
        else:
            target.symlink_to(root / "docs/internal")
        expected = target
    with pytest.raises(GitTransactionError, match=str(expected)):
        workflow_completion.check_archive_directory(
            root, profile, "watch-20260925-abc", "feature/task"
        )


@pytest.mark.parametrize("kind", ("directory", "file", "symlink"))
def test_first_slice_conflict_stops_before_implementer(
    tmp_path: Path, kind: str,
) -> None:
    root = repository(tmp_path)
    run = completion(root, archive_pattern="{run_id}", run_id="watch-20260925-abc")
    destination = root / workflow_completion.ARCHIVE_RELATIVE / "watch-20260925-abc"
    if kind == "directory":
        destination.mkdir()
    elif kind == "file":
        destination.write_text("occupied")
    else:
        destination.symlink_to(root / "docs/internal")
    state = SimpleNamespace(
        current_work_unit=SimpleNamespace(
            status=WorkUnitStatus.IN_PROGRESS, kind=WorkUnitKind.SLICE,
        ),
        current_slice_id=1,
        current_slice=SimpleNamespace(scope_paths=("src/a.py",)),
    )
    calls: list[str] = []

    class Driver:
        def preflight_archive_directory(self, _state):  # type: ignore[no-untyped-def]
            calls.append("archive")
            workflow_completion.check_archive_directory(
                root, run.profile, run.run_id, run.branch
            )

    class Engine:
        def run_current_work_unit(self, *_args):  # type: ignore[no-untyped-def]
            calls.append("implementer")
            raise AssertionError("implementer started after archive conflict")

    dependencies = SimpleNamespace(
        recover_final_review_attestation=lambda _state, history, *_: history,
    )
    with pytest.raises(GitTransactionError, match=str(destination)):
        _run_production_transition_loop(
            root=root, task_file=root / "task.md", assignment="task",
            args=SimpleNamespace(), dependencies=dependencies, state=state,
            history=WorkflowHistory(1), effective_resume=False,
            driver=Driver(), engine=Engine(),
        )
    assert calls == ["archive"]


def test_driver_early_preflight_uses_bound_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = repository(tmp_path)
    profile = completion(root, archive_pattern="{run_id}").profile
    destination = root / workflow_completion.ARCHIVE_RELATIVE / "watch-20260925-bound"
    destination.mkdir()
    monkeypatch.setattr(
        orchestrator, "replay_artifacts",
        lambda *_: SimpleNamespace(run_profile=profile),
    )
    driver = object.__new__(ProductionWorkflowDriver)
    driver.root = root
    driver._artifact_bridge = SimpleNamespace(
        store=SimpleNamespace(current_chain=lambda: ()),
    )
    state = SimpleNamespace(
        current_work_unit=SimpleNamespace(kind=WorkUnitKind.SLICE),
        current_slice_id=1, run_id="watch-20260925-bound", branch="feature/task",
    )
    with pytest.raises(GitTransactionError, match=str(destination)):
        driver.preflight_archive_directory(state)


def test_two_runs_archive_same_name_in_distinct_bound_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = repository(tmp_path)
    first = completion(root, archive_pattern="{year}-feature/{run_id}",
                       run_id="watch-20260925-first")
    first.run(monkeypatch)
    first_target = root / workflow_completion.ARCHIVE_RELATIVE / "2026-feature/watch-20260925-first/plan.md"
    assert first_target.read_text() == "plan\n"
    git(root, "switch", "-qc", "feature/second")
    (root / "docs/internal/plan.md").write_text("second run\n")
    git(root, "add", "docs/internal/plan.md")
    git(root, "commit", "-qm", "second plan")
    second = completion(root, archive_pattern="{year}-feature/{run_id}",
                        run_id="watch-20260925-second", branch="feature/second")
    second.run(monkeypatch)
    second_target = root / workflow_completion.ARCHIVE_RELATIVE / "2026-feature/watch-20260925-second/plan.md"
    assert first_target.read_text() == "plan\n"
    assert second_target.read_text() == "second run\n"
    assert git(root, "status", "--porcelain") == ""


def test_legacy_profile_still_uses_flat_archive(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    root = repository(tmp_path)
    run = completion(root)
    run.run(monkeypatch)
    assert (root / workflow_completion.ARCHIVE_RELATIVE / "plan.md").read_text() == "plan\n"


def test_archive_placeholders_expand_from_bound_identity(tmp_path: Path) -> None:
    root = repository(tmp_path)
    profile = completion(
        root, archive_pattern="{year}-{branch_slug}/{run_id}",
    ).profile
    assert workflow_completion.check_archive_directory(
        root, profile, "watch-20260925-abc", "feature/Sample_Task"
    ) == workflow_completion.ARCHIVE_RELATIVE / "2026-feature-sample-task/watch-20260925-abc"
    with pytest.raises(GitTransactionError, match="invalid run_id"):
        workflow_completion.check_archive_directory(root, profile, "../escape", "feature/task")


def test_nested_archive_failure_restores_files_and_new_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = repository(tmp_path)
    run = completion(root, archive_pattern="{year}/{run_id}",
                     run_id="watch-20260925-rollback")
    before = completion_snapshot(root)
    original_git = workflow_completion._git

    def fail_commit(repo: Path, *args: str, **kwargs):  # type: ignore[no-untyped-def]
        if args and "commit" in args and workflow_completion.ARCHIVE_SUBJECT in args:
            raise GitTransactionError("injected commit failure")
        return original_git(repo, *args, **kwargs)

    monkeypatch.setattr(workflow_completion, "_git", fail_commit)
    with pytest.raises(GitTransactionError, match="injected commit failure"):
        run.run(monkeypatch)
    assert completion_snapshot(root) == before
    assert not (root / workflow_completion.ARCHIVE_RELATIVE / "2026").exists()


def completion_snapshot(root: Path) -> tuple[bytes, bytes, bytes, bytes, tuple[tuple[str, bytes], ...]]:
    def raw_git(*args: str) -> bytes:
        return subprocess.run(("git", *args), cwd=root, capture_output=True,
                              check=True).stdout

    files: list[tuple[str, bytes]] = []
    for parent, directories, names in os.walk(root):
        directories[:] = [name for name in directories if name != ".git"]
        for name in names:
            path = Path(parent) / name
            files.append((path.relative_to(root).as_posix(), path.read_bytes()))
    return (
        raw_git("rev-parse", "HEAD"),
        raw_git("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads"),
        raw_git("ls-files", "--stage", "-z"),
        raw_git("status", "--porcelain=v1", "-z", "--untracked-files=all"),
        tuple(sorted(files)),
    )


def finish_with_audit(root: Path, run: CompletionRun,
                      monkeypatch: pytest.MonkeyPatch) -> str:
    state = SimpleNamespace(branch="feature/task", current_work_unit_id=3,
                            run_id="run", audit_report_path="docs/internal/audit.md")
    monkeypatch.setattr(workflow_completion, "replay_artifacts", lambda *_: SimpleNamespace(
        run_profile=run.profile, side_effects=tuple(run.bridge.effects.values()),
    ))

    class Driver:
        def final_review_has_followup(self, _state):  # type: ignore[no-untyped-def]
            return False

        def preflight_chain(self, _state):  # type: ignore[no-untyped-def]
            return workflow_completion.preflight_chain(root, state, run.bridge)

        def finalize_audit(self, _state):  # type: ignore[no-untyped-def]
            git(root, "add", "--", state.audit_report_path)
            git(root, "commit", "-qm", "docs: finalize orchestrator audit")
            return git(root, "rev-parse", "HEAD")

        def complete_chain(self, _state):  # type: ignore[no-untyped-def]
            return run.run(monkeypatch)

    return _finish_final_review(Driver(), state, WorkflowHistory(3)).commit_ref


def assert_resumed_completion(root: Path, run: CompletionRun,
                              monkeypatch: pytest.MonkeyPatch) -> None:
    result = finish_with_audit(root, run, monkeypatch)
    assert git(root, "branch", "--show-current") == "main"
    assert git(root, "rev-parse", "HEAD") == result
    assert len(git(root, "show", "-s", "--format=%P", "HEAD").split()) == 2
    assert git(root, "show", "-s", "--format=%s", "HEAD^2") == workflow_completion.ARCHIVE_SUBJECT
    assert git(root, "show", "-s", "--format=%s", "HEAD^2^") == "docs: finalize orchestrator audit"
    assert (root / workflow_completion.ARCHIVE_RELATIVE / "audit.md").read_text() == "final review\n"
    assert not (root / "docs/internal/audit.md").exists()
    assert git(root, "status", "--porcelain") == ""


@pytest.mark.parametrize("followup", (False, True))
def test_final_review_only_completes_chain_without_followup(
    followup: bool, tmp_path: Path,
) -> None:
    calls: list[str] = []

    class Driver:
        def final_review_has_followup(self, _state):  # type: ignore[no-untyped-def]
            calls.append("check")
            return followup

        def publish_followup_task(self, _state):  # type: ignore[no-untyped-def]
            calls.append("publish")
            if followup:
                document = tmp_path / "inbox/followup.md"
                document.parent.mkdir()
                document.write_text("review finding\n")
                return document
            return None

        def finalize_audit(self, _state):  # type: ignore[no-untyped-def]
            calls.append("audit")
            return "audit-commit"

        def complete_chain(self, _state):  # type: ignore[no-untyped-def]
            calls.append("complete")
            return "merge-commit"

        def preflight_chain(self, _state):  # type: ignore[no-untyped-def]
            calls.append("preflight")
            return False

    state = SimpleNamespace()
    history = WorkflowHistory(1)
    result = _finish_final_review(Driver(), state, history)  # type: ignore[arg-type]
    assert calls == (["check", "audit", "publish"] if followup else
                     ["check", "preflight", "audit", "complete"])
    assert result.commit_ref == ("audit-commit" if followup else "merge-commit")
    assert (tmp_path / "inbox/followup.md").exists() is followup


def test_final_review_resume_skips_audit_after_archive_intent() -> None:
    class Driver:
        def final_review_has_followup(self, _state):  # type: ignore[no-untyped-def]
            return False

        def publish_followup_task(self, _state):  # type: ignore[no-untyped-def]
            return None

        def preflight_chain(self, _state):  # type: ignore[no-untyped-def]
            return True

        def finalize_audit(self, _state):  # type: ignore[no-untyped-def]
            raise AssertionError("audit must not be committed after archival starts")

        def complete_chain(self, _state):  # type: ignore[no-untyped-def]
            return "already-merged"

    result = _finish_final_review(Driver(), SimpleNamespace(), WorkflowHistory(3))  # type: ignore[arg-type]
    assert result.commit_ref == "already-merged"


def test_clean_completion_archives_branch_additions_and_merges(tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    root = repository(tmp_path)
    run = completion(root)
    result = run.run(monkeypatch)
    assert git(root, "branch", "--show-current") == "main"
    assert git(root, "rev-parse", "HEAD") == result
    assert len(git(root, "show", "-s", "--format=%P", "HEAD").split()) == 2
    assert git(root, "show", "-s", "--format=%s", "HEAD") == "Merge feature/task into main"
    assert git(root, "show", "-s", "--format=%s", "HEAD^2") == workflow_completion.ARCHIVE_SUBJECT
    assert f"R100\tdocs/internal/plan.md\t{workflow_completion.ARCHIVE_RELATIVE}/plan.md" in git(
        root, "diff", "--name-status", "-M", "HEAD^2^", "HEAD^2"
    )
    assert (root / workflow_completion.ARCHIVE_RELATIVE / "plan.md").read_text() == "plan\n"
    assert (root / workflow_completion.ARCHIVE_RELATIVE / "earlier.md").read_text() == "later edit\n"
    assert not (root / "docs/internal/plan.md").exists()
    assert (root / workflow_completion.ARCHIVE_RELATIVE / "notes.txt").read_text() == "notes\n"
    assert (root / "docs/internal/existing.md").read_text() == "existing modified\n"
    assert (root / "docs/internal/nested/keep.md").read_text() == "nested\n"
    assert git(root, "status", "--porcelain") == ""
    assert run.run(monkeypatch) == result


def test_final_review_handler_reaches_local_git_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = repository(tmp_path)
    run = completion(root)
    state = SimpleNamespace(branch="feature/task", current_work_unit_id=3,
                            run_id="run", audit_report_path=None)
    monkeypatch.setattr(workflow_completion, "replay_artifacts", lambda *_: SimpleNamespace(
        run_profile=run.profile, side_effects=tuple(run.bridge.effects.values()),
    ))

    class Driver:
        def final_review_has_followup(self, _state):  # type: ignore[no-untyped-def]
            return False

        def publish_followup_task(self, _state):  # type: ignore[no-untyped-def]
            return None

        def preflight_chain(self, _state):  # type: ignore[no-untyped-def]
            return workflow_completion.preflight_chain(root, state, run.bridge)

        def finalize_audit(self, _state):  # type: ignore[no-untyped-def]
            return git(root, "rev-parse", "HEAD")

        def complete_chain(self, _state):  # type: ignore[no-untyped-def]
            return run.run(monkeypatch)

    result = _finish_final_review(Driver(), state, WorkflowHistory(3))  # type: ignore[arg-type]
    assert result.commit_ref == git(root, "rev-parse", "main")
    assert git(root, "branch", "--show-current") == "main"
    assert (root / workflow_completion.ARCHIVE_RELATIVE / "plan.md").exists()


def test_empty_archive_still_gets_its_own_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Test Operator")
    git(root, "config", "user.email", "operator@example.invalid")
    (root / "src.txt").write_text("base\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    git(root, "switch", "-qc", "feature/task")
    (root / "src.txt").write_text("feature\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "feature")
    feature_head = git(root, "rev-parse", "HEAD")
    run = completion(root, merge=False)
    committed = run.run(monkeypatch)
    assert committed != feature_head
    assert git(root, "show", "-s", "--format=%P", committed) == feature_head
    assert git(root, "show", "-s", "--format=%T", committed) == git(root, "show", "-s", "--format=%T", feature_head)


def test_merge_disabled_still_commits_archive(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    root = repository(tmp_path)
    base = git(root, "rev-parse", "main")
    run = completion(root, merge=False)
    result = run.run(monkeypatch)
    assert git(root, "branch", "--show-current") == "feature/task"
    assert git(root, "rev-parse", "main") == base
    assert git(root, "rev-parse", "HEAD") == result
    assert git(root, "show", "-s", "--format=%s", "HEAD") == workflow_completion.ARCHIVE_SUBJECT
    assert not any(effect.effect_class == "git_merge" for effect in run.bridge.effects.values())


def test_preflight_includes_new_audit_and_blocks_conflict_before_audit_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = repository(tmp_path, conflict=True)
    audit = root / "docs/internal/audit.md"
    audit.write_text("final review\n")
    run = completion(root)
    monkeypatch.setattr(workflow_completion, "replay_artifacts", lambda *_: SimpleNamespace(
        run_profile=run.profile, side_effects=(),
    ))
    state = SimpleNamespace(branch="feature/task", current_work_unit_id=3,
                            run_id="run", audit_report_path="docs/internal/audit.md")
    before = git(root, "rev-parse", "HEAD")
    with pytest.raises(GitTransactionError, match="merge conflict"):
        workflow_completion.preflight_chain(root, state, run.bridge)
    assert git(root, "rev-parse", "HEAD") == before
    assert audit.read_text() == "final review\n"
    assert not (root / workflow_completion.ARCHIVE_RELATIVE / "audit.md").exists()


def test_preflight_new_audit_is_archived_after_audit_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = repository(tmp_path)
    audit = root / "docs/internal/audit.md"
    audit.write_text("final review\n")
    run = completion(root)
    monkeypatch.setattr(workflow_completion, "replay_artifacts", lambda *_: SimpleNamespace(
        run_profile=run.profile, side_effects=tuple(run.bridge.effects.values()),
    ))
    state = SimpleNamespace(branch="feature/task", current_work_unit_id=3,
                            run_id="run", audit_report_path="docs/internal/audit.md")
    workflow_completion.preflight_chain(root, state, run.bridge)
    git(root, "add", "docs/internal/audit.md")
    git(root, "commit", "-qm", "docs: finalize orchestrator audit")
    run.run(monkeypatch)
    assert (root / workflow_completion.ARCHIVE_RELATIVE / "audit.md").read_text() == "final review\n"


def test_other_worktree_blocks_all_completion_commits_and_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = repository(tmp_path)
    other = tmp_path / "base-worktree"
    git(root, "worktree", "add", "-q", str(other), "main")
    (root / "docs/internal/audit.md").write_text("final review\n")
    run = completion(root)
    before = completion_snapshot(root)

    with pytest.raises(GitTransactionError) as failure:
        finish_with_audit(root, run, monkeypatch)
    assert completion_snapshot(root) == before
    assert str(other) in str(failure.value)
    assert not run.bridge.effects

    git(root, "worktree", "remove", str(other))
    assert_resumed_completion(root, run, monkeypatch)


@pytest.mark.parametrize("dirty_kind", ("modified", "untracked"))
def test_dirty_worktree_blocks_audit_commit_and_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dirty_kind: str,
) -> None:
    root = repository(tmp_path)
    (root / "docs/internal/audit.md").write_text("final review\n")
    dirty = root / ("shared.txt" if dirty_kind == "modified" else "untracked.txt")
    dirty.write_text("pending operator change\n")
    run = completion(root)
    before = completion_snapshot(root)

    with pytest.raises(GitTransactionError) as failure:
        finish_with_audit(root, run, monkeypatch)
    assert completion_snapshot(root) == before
    assert "clean worktree outside the final audit" in str(failure.value)
    assert not run.bridge.effects

    if dirty_kind == "modified":
        dirty.write_text("base\n")
    else:
        dirty.unlink()
    assert_resumed_completion(root, run, monkeypatch)


@pytest.mark.parametrize("condition", (
    "name_conflict", "untracked_name_conflict", "dirty", "merge_conflict", "missing_base",
))
def test_preflight_failure_leaves_branch_and_tree_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, condition: str,
) -> None:
    root = repository(tmp_path, name_conflict=condition == "name_conflict",
                      conflict=condition == "merge_conflict")
    if condition == "dirty":
        (root / "dirty.txt").write_text("untracked\n")
    if condition == "untracked_name_conflict":
        (root / workflow_completion.ARCHIVE_RELATIVE / "plan.md").write_text("collision\n")
    run = completion(root)
    if condition == "missing_base":
        git(root, "branch", "-D", "main")
    head = git(root, "rev-parse", "HEAD")
    base = None if condition == "missing_base" else git(root, "rev-parse", "main")
    status = git(root, "status", "--porcelain")
    with pytest.raises(GitTransactionError, match={
        "name_conflict": "archive name conflict",
        "untracked_name_conflict": "archive name conflict",
        "dirty": "clean worktree",
        "merge_conflict": "merge conflict",
        "missing_base": "is missing",
    }[condition]):
        run.run(monkeypatch)
    assert git(root, "rev-parse", "HEAD") == head
    assert git(root, "branch", "--show-current") == "feature/task"
    assert git(root, "status", "--porcelain") == status
    if base:
        assert git(root, "rev-parse", "main") == base
    assert not run.bridge.effects


@pytest.mark.parametrize("effect,phase", [
    (effect, phase) for effect in ("git_commit", "git_merge")
    for phase in SideEffectBoundaryPhase
])
@pytest.mark.parametrize("archive_pattern", (None, "{run_id}"))
def test_each_completion_boundary_resumes_to_same_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    effect: str, phase: SideEffectBoundaryPhase,
    archive_pattern: str | None,
) -> None:
    root = repository(tmp_path)
    run = completion(root, archive_pattern=archive_pattern)
    interrupted = False

    def interrupt(boundary) -> None:  # type: ignore[no-untyped-def]
        nonlocal interrupted
        if not interrupted and boundary.effect_class == effect and boundary.phase == phase:
            interrupted = True
            raise RuntimeError("injected crash")

    with pytest.raises(RuntimeError, match="injected crash"):
        run.run(monkeypatch, interrupt)
    assert interrupted
    result = run.run(monkeypatch)
    assert git(root, "branch", "--show-current") == "main"
    assert git(root, "rev-parse", "HEAD") == result
    assert len(git(root, "show", "-s", "--format=%P", "HEAD").split()) == 2
