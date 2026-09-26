from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import signal
from types import SimpleNamespace
import subprocess
import time

import pytest

from artifact_models import (
    RecordType, RoleProfilePayload, RunProfilePayload, _payload_from_dict,
    artifact_payload_document, stable_side_effect_key,
)
from artifact_models import SideEffectPayload
from artifact_store import ArtifactStore
from artifact_replay import replay_artifacts
from git_service import GitTransactionError
from inbox_watcher import WatchTaskIdentity, save_watch_identity
import orchestrator
from orchestrator import ProductionWorkflowDriver
from side_effects import SideEffectBoundaryPhase, SideEffectExecutor, SideEffectSpec
from side_effects import SideEffectReconciliationError
import workflow_completion
from workflow_production import _finish_final_review, _run_production_transition_loop
from workflow import WorkflowHistory
from workflow_state import WorkUnitKind, WorkUnitStatus
from test_orchestrator_runtime import (
    _args as production_args, _native_final_review_output,
    _native_implementation_output, _native_plan_output,
    _native_review_approval, _repository as production_repository,
    _write_task as write_production_task,
)


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
        self.record_count = 0
        self.store = self

    def current_chain(self) -> tuple[()]:
        return ()

    def record_side_effect_intent(self, *, effect_class: str,
                                  work_unit_id: str, operation: tuple[str, ...],
                                  **_: object) -> tuple[None, bool]:
        key = stable_side_effect_key(effect_class, work_unit_id, operation)
        created = key not in self.effects
        if created:
            self.record_count += 1
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
        self.record_count += 1


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


def _post_merge_result(run: CompletionRun) -> dict[str, object]:
    effects = [item for item in run.bridge.effects.values()
               if item.effect_class == "post_merge_hook"]
    assert len(effects) == 1
    return json.loads(effects[0].result)


def test_standard_post_merge_hook_runs_once_after_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = repository(tmp_path)
    marker = tmp_path / "invocations.txt"
    hook = root / ".git/hooks/post-merge"
    hook.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$1\" >> '{marker}'\nprintf 'ready\\n'\nprintf 'notice\\n' >&2\n")
    hook.chmod(0o755)
    run = completion(root)
    merged = run.run(monkeypatch)
    assert marker.read_text() == "0\n"
    assert git(root, "rev-parse", "HEAD") == merged
    assert _post_merge_result(run)["status"] == "success"
    assert _post_merge_result(run)["stdout"] == "ready\n"
    assert _post_merge_result(run)["stderr"] == "notice\n"
    effect = next(item for item in run.bridge.effects.values()
                  if item.effect_class == "post_merge_hook")
    assert effect.operation == ("post_merge", merged, str(hook),
                                hashlib.sha256(hook.read_bytes()).hexdigest())
    run.run(monkeypatch)
    assert marker.read_text() == "0\n"


@pytest.mark.parametrize("legacy", (False, True))
def test_completed_hook_pair_survives_content_change_without_new_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy: bool,
) -> None:
    root = repository(tmp_path)
    marker = tmp_path / "hook-count"
    hook = root / ".git/hooks/post-merge"
    hook.write_text(f"#!/bin/sh\nprintf x >> '{marker}'\n")
    hook.chmod(0o755)
    run = completion(root)
    commit = run.run(monkeypatch)
    effect_key, effect = next((key, item) for key, item in run.bridge.effects.items()
                              if item.effect_class == "post_merge_hook")
    if legacy:
        del run.bridge.effects[effect_key]
        effect.operation = effect.operation[:3]
        effect_key = stable_side_effect_key("post_merge_hook", "3", effect.operation)
        run.bridge.effects[effect_key] = effect
    original_operation = effect.operation
    hook.write_text(f"#!/bin/sh\nprintf y >> '{marker}'\n")
    monkeypatch.setattr(workflow_completion, "_run_hook",
                        lambda *_: pytest.fail("completed hook ran twice"))
    assert run.run(monkeypatch) == commit
    assert marker.read_text() == "x"
    assert run.bridge.effects[effect_key] is effect
    assert effect.operation == original_operation
    assert len([item for item in run.bridge.effects.values()
                if item.effect_class == "post_merge_hook"]) == 1


def test_hook_changed_after_intent_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = repository(tmp_path)
    marker = tmp_path / "hook-count"
    hook = root / ".git/hooks/post-merge"
    hook.write_text(f"#!/bin/sh\nprintf x >> '{marker}'\n")
    hook.chmod(0o755)
    run = completion(root)

    def change_after_intent(boundary) -> None:  # type: ignore[no-untyped-def]
        if (boundary.effect_class == "post_merge_hook"
                and boundary.phase is SideEffectBoundaryPhase.AFTER_INTENT):
            hook.write_text(f"#!/bin/sh\nprintf y >> '{marker}'\n")

    run.run(monkeypatch, change_after_intent)
    assert not marker.exists()
    assert _post_merge_result(run)["reason"] == "content_changed"


@pytest.mark.parametrize("merge", (True, False))
@pytest.mark.parametrize("failure", ("lstat", "open"))
@pytest.mark.parametrize("transient", (True, False))
def test_initial_hook_digest_denial_records_terminal_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, merge: bool, failure: str,
    transient: bool,
) -> None:
    root = repository(tmp_path)
    base_head = git(root, "rev-parse", "main")
    marker = tmp_path / "hook-count"
    hook = root / ".git/hooks/post-merge"
    hook.write_text(f"#!/bin/sh\nprintf x >> '{marker}'\n")
    hook.chmod(0o755)
    real_lstat, real_open = Path.lstat, workflow_completion.os.open
    denied = True

    def deny_lstat(path: Path, *args: object, **kwargs: object):
        nonlocal denied
        if denied and path == hook:
            if transient:
                denied = False
            raise PermissionError("denied hook lstat")
        return real_lstat(path, *args, **kwargs)

    def deny_open(path: object, *args: object, **kwargs: object):
        nonlocal denied
        if denied and path == hook:
            if transient:
                denied = False
            raise PermissionError("denied hook open")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", deny_lstat if failure == "lstat" else real_lstat)
    monkeypatch.setattr(workflow_completion.os, "open",
                        deny_open if failure == "open" else real_open)
    run = completion(root, merge=merge)
    completed = run.run(monkeypatch)
    effect = next(item for item in run.bridge.effects.values()
                  if item.effect_class == "post_merge_hook")
    assert effect.operation == ("post_merge", completed, str(hook), "unavailable")
    assert json.loads(effect.result)["status"] == "skipped"
    assert json.loads(effect.result)["reason"] == "digest_unreadable"
    assert "digest_unreadable" in caplog.text
    assert not marker.exists()
    assert git(root, "rev-parse", "main") == (completed if merge else base_head)
    record_count = run.bridge.record_count
    denied = False
    assert run.run(monkeypatch) == completed
    assert run.bridge.record_count == record_count
    assert len([item for item in run.bridge.effects.values()
                if item.effect_class == "post_merge_hook"]) == 1
    assert not marker.exists()


def test_hook_digest_denial_after_intent_records_terminal_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = repository(tmp_path)
    marker = tmp_path / "hook-count"
    hook = root / ".git/hooks/post-merge"
    hook.write_text(f"#!/bin/sh\nprintf x >> '{marker}'\n")
    hook.chmod(0o755)
    real_open = workflow_completion.os.open
    denied = False

    def deny_open(path: object, *args: object, **kwargs: object):
        if denied and path == hook:
            raise PermissionError("denied hook open")
        return real_open(path, *args, **kwargs)

    def deny_after_intent(boundary) -> None:  # type: ignore[no-untyped-def]
        nonlocal denied
        if (boundary.effect_class == "post_merge_hook"
                and boundary.phase is SideEffectBoundaryPhase.AFTER_INTENT):
            denied = True

    monkeypatch.setattr(workflow_completion.os, "open", deny_open)
    run = completion(root)
    run.run(monkeypatch, deny_after_intent)
    effect = next(item for item in run.bridge.effects.values()
                  if item.effect_class == "post_merge_hook")
    assert len([item for item in run.bridge.effects.values()
                if item.effect_class == "post_merge_hook"]) == 1
    assert stable_side_effect_key("post_merge_hook", "3", effect.operation) in run.bridge.effects
    assert effect.operation[3] == hashlib.sha256(hook.read_bytes()).hexdigest()
    assert json.loads(effect.result)["status"] == "skipped"
    assert json.loads(effect.result)["reason"] == "digest_unreadable"
    assert not marker.exists()


def test_duplicate_hook_effects_stop_before_any_hook_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = repository(tmp_path)
    marker = tmp_path / "hook-count"
    hook = root / ".git/hooks/post-merge"
    hook.write_text(f"#!/bin/sh\nprintf x >> '{marker}'\n")
    hook.chmod(0o755)
    run = completion(root)
    merged = run.run(monkeypatch)
    original = next(item for item in run.bridge.effects.values()
                    if item.effect_class == "post_merge_hook")
    alternate = ("post_merge", merged, str(hook), "unavailable")
    run.bridge.effects[stable_side_effect_key("post_merge_hook", "3", alternate)] = (
        SimpleNamespace(effect_class="post_merge_hook", work_unit_id="3",
                        operation=alternate, result=None))
    before = dict(run.bridge.effects)
    record_count = run.bridge.record_count
    monkeypatch.setattr(workflow_completion, "_hook_path",
                        lambda *_: pytest.fail("hook path resolved after duplicate effects"))
    with pytest.raises(GitTransactionError, match="contradictory post-merge hook effects"):
        run.run(monkeypatch)
    assert run.bridge.effects == before
    assert run.bridge.record_count == record_count
    assert original.result is not None
    assert marker.read_text() == "x"
    assert git(root, "rev-parse", "main") == merged


def test_changed_open_hook_intent_stops_before_new_intent_or_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = repository(tmp_path)
    marker = tmp_path / "hook-count"
    hook = root / ".git/hooks/post-merge"
    hook.write_text(f"#!/bin/sh\nprintf x >> '{marker}'\n")
    hook.chmod(0o755)
    run = completion(root)

    def interrupt(boundary) -> None:  # type: ignore[no-untyped-def]
        if (boundary.effect_class == "post_merge_hook"
                and boundary.phase is SideEffectBoundaryPhase.AFTER_INTENT):
            raise RuntimeError("open hook intent")

    with pytest.raises(RuntimeError, match="open hook intent"):
        run.run(monkeypatch, interrupt)
    key, effect = next((key, item) for key, item in run.bridge.effects.items()
                       if item.effect_class == "post_merge_hook")
    assert len(effect.operation) == 4
    hook.write_text(f"#!/bin/sh\nprintf y >> '{marker}'\n")
    with pytest.raises(SideEffectReconciliationError, match="unknown physical outcome"):
        run.run(monkeypatch)
    assert not marker.exists()
    assert effect.result is None
    assert run.bridge.effects[key] is effect
    assert len([item for item in run.bridge.effects.values()
                if item.effect_class == "post_merge_hook"]) == 1


@pytest.mark.parametrize("merge", (True, False))
def test_legacy_profile_completion_does_not_start_post_merge_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, merge: bool,
) -> None:
    root = repository(tmp_path)
    marker = tmp_path / "legacy-hook-ran"
    hook = root / ".git/hooks/post-merge"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    hook.chmod(0o755)
    run = completion(root, merge=merge)
    legacy_document = artifact_payload_document(
        RunProfilePayload(
            run.profile.implementer, run.profile.reviewer,
            merge_completed_branch=merge, base_branch="main",
            post_merge_hook_enabled=False,
        )
    )
    assert "post_merge_hook_enabled" not in legacy_document
    run.profile = _payload_from_dict(RecordType.RUN_PROFILE, legacy_document)

    commit = run.run(monkeypatch)

    assert not marker.exists()
    assert git(root, "rev-parse", "main" if merge else "feature/task") == commit
    assert not any(item.effect_class == "post_merge_hook"
                   for item in run.bridge.effects.values())


@pytest.mark.parametrize("kind", ("failed", "timeout", "not_executable", "symlink"))
def test_hook_problem_preserves_merge_and_records_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    root = repository(tmp_path)
    hook = root / ".git/hooks/post-merge"
    if kind == "symlink":
        destination = tmp_path / "real-hook"
        destination.write_text("#!/bin/sh\nexit 0\n")
        destination.chmod(0o755)
        hook.symlink_to(destination)
    else:
        hook.write_text("#!/bin/sh\nsleep 1\n" if kind == "timeout" else
                        "#!/bin/sh\nprintf error >&2\nexit 7\n")
        hook.chmod(0o644 if kind == "not_executable" else 0o755)
    if kind == "timeout":
        monkeypatch.setattr(workflow_completion, "HOOK_TIMEOUT_SECONDS", 0.01)
    run = completion(root)
    merged = run.run(monkeypatch)
    assert git(root, "rev-parse", "main") == merged
    result = _post_merge_result(run)
    assert result["status"] == ("skipped" if kind in {"not_executable", "symlink"} else kind)
    effect = next(item for item in run.bridge.effects.values()
                  if item.effect_class == "post_merge_hook")
    assert effect.operation[3] == (
        "unavailable" if kind == "symlink"
        else hashlib.sha256(hook.read_bytes()).hexdigest()
    )
    if kind == "failed":
        assert result["exit_code"] == 7


def test_timed_out_hook_kills_descendant_and_is_not_repeated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = repository(tmp_path)
    marker = tmp_path / "late-hook-marker"
    started = tmp_path / "hook-descendant-started"
    hook = root / ".git/hooks/post-merge"
    hook.write_text(
        f"#!/bin/sh\n(sleep 0.45; touch '{marker}') &\n"
        f"touch '{started}'\nwait\n"
    )
    hook.chmod(0o755)
    monkeypatch.setattr(workflow_completion, "HOOK_TIMEOUT_SECONDS", 0.15)
    run = completion(root)
    merged = run.run(monkeypatch)
    result = _post_merge_result(run)
    assert result["status"] == "timeout"
    assert result["termination_uncertain"] is False
    assert started.exists()
    assert git(root, "rev-parse", "main") == merged
    time.sleep(0.55)
    assert not marker.exists()
    run.run(monkeypatch)
    assert not marker.exists()


def test_timeout_reports_uncertain_termination_when_signal_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    root = repository(tmp_path)
    hook = root / ".git/hooks/post-merge"
    hook.write_text("#!/bin/sh\nsleep 2\n")
    hook.chmod(0o755)
    monkeypatch.setattr(workflow_completion, "HOOK_TIMEOUT_SECONDS", 0.01)
    real_killpg = workflow_completion.os.killpg

    def fail_term(group: int, sig: int) -> None:
        if sig == signal.SIGTERM:
            raise PermissionError("simulated signal denial")
        real_killpg(group, sig)

    monkeypatch.setattr(workflow_completion.os, "killpg", fail_term)
    run = completion(root)
    merged = run.run(monkeypatch)
    result = _post_merge_result(run)
    assert git(root, "rev-parse", "main") == merged
    assert result["status"] == "timeout"
    assert result["termination_uncertain"] is True
    assert "termination_uncertain=True" in caplog.text


def test_changed_tracked_hook_is_skipped_and_external_hook_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = repository(tmp_path)
    tracked = root / ".githooks/post-merge"
    tracked.parent.mkdir()
    tracked.write_text("#!/bin/sh\nexit 8\n")
    tracked.chmod(0o755)
    git(root, "add", ".githooks/post-merge")
    git(root, "commit", "-qm", "add hook")
    git(root, "config", "core.hooksPath", ".githooks")
    run = completion(root)
    run.run(monkeypatch)
    assert _post_merge_result(run)["reason"] == "changed_by_target_branch"

    external_parent = tmp_path / "external"
    external_parent.mkdir()
    root2 = repository(external_parent)
    external_hook_dir = tmp_path / "hooks"
    external_hook_dir.mkdir()
    external_hook = external_hook_dir / "post-merge"
    external_hook.write_text("#!/bin/sh\nprintf external\n")
    external_hook.chmod(0o755)
    git(root2, "config", "core.hooksPath", str(external_hook_dir))
    run2 = completion(root2)
    run2.run(monkeypatch)
    assert _post_merge_result(run2)["stdout"] == "external"


def test_missing_hook_in_tracked_hook_directory_is_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    root = repository(tmp_path)
    hook_directory = root / ".githooks"
    hook_directory.mkdir()
    (hook_directory / "pre-push").write_text("#!/bin/sh\nexit 0\n")
    git(root, "add", ".githooks/pre-push")
    git(root, "commit", "-qm", "add neighboring hook")
    git(root, "config", "core.hooksPath", ".githooks")

    run = completion(root)
    merged = run.run(monkeypatch)

    assert git(root, "rev-parse", "main") == merged
    assert _post_merge_result(run)["status"] == "skipped"
    assert _post_merge_result(run)["reason"] == "missing"
    effect = next(item for item in run.bridge.effects.values()
                  if item.effect_class == "post_merge_hook")
    assert effect.operation[3] == "unavailable"
    assert "post-merge hook" not in caplog.text


def _base_hook(root: Path, *, executable: bool, content: str) -> Path:
    git(root, "switch", "main")
    hook = root / ".githooks/post-merge"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(content)
    hook.chmod(0o755 if executable else 0o644)
    git(root, "add", ".githooks/post-merge")
    git(root, "commit", "-qm", "base hook")
    git(root, "switch", "feature/task")
    git(root, "merge", "--no-ff", "-qm", "bring base hook into target", "main")
    git(root, "config", "core.hooksPath", ".githooks")
    return hook


def test_target_type_change_from_base_symlink_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = repository(tmp_path)
    git(root, "switch", "main")
    hook = root / ".githooks/post-merge"
    hook.parent.mkdir(exist_ok=True)
    source = root / ".githooks/original"
    source.write_text("#!/bin/sh\nexit 0\n")
    source.chmod(0o755)
    hook.symlink_to("original")
    git(root, "add", ".githooks")
    git(root, "commit", "-qm", "base symlink hook")
    git(root, "switch", "feature/task")
    git(root, "merge", "--no-ff", "-qm", "bring base hook into target", "main")
    marker = tmp_path / "changed-hook-ran"
    hook.unlink()
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    hook.chmod(0o755)
    git(root, "add", ".githooks/post-merge")
    git(root, "commit", "-qm", "replace hook type")
    git(root, "config", "core.hooksPath", ".githooks")
    run = completion(root)
    merged = run.run(monkeypatch)
    assert not marker.exists()
    assert _post_merge_result(run)["status"] == "skipped"
    assert _post_merge_result(run)["reason"] == "changed_by_target_branch"
    assert git(root, "rev-parse", "main") == merged


@pytest.mark.parametrize("change", ("content", "mode"))
def test_target_hook_entry_change_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    root = repository(tmp_path)
    marker = tmp_path / "changed-hook-ran"
    hook = _base_hook(root, executable=change == "content",
                      content="#!/bin/sh\nexit 0\n")
    if change == "mode":
        hook.chmod(0o755)
    else:
        hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    git(root, "add", ".githooks/post-merge")
    git(root, "commit", "-qm", f"change hook {change}")
    run = completion(root)
    run.run(monkeypatch)
    assert not marker.exists()
    assert _post_merge_result(run)["reason"] == "changed_by_target_branch"


def test_unchanged_tracked_hook_runs_and_untracked_base_hook_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = repository(tmp_path)
    marker = tmp_path / "unchanged-hook-ran"
    _base_hook(root, executable=True,
               content=f"#!/bin/sh\ntouch '{marker}'\n")
    run = completion(root)
    run.run(monkeypatch)
    assert marker.exists()
    assert _post_merge_result(run)["status"] == "success"

    other = tmp_path / "other"
    other.mkdir()
    root2 = repository(other)
    marker2 = tmp_path / "new-hook-ran"
    hook2 = root2 / ".githooks/post-merge"
    hook2.parent.mkdir()
    hook2.write_text(f"#!/bin/sh\ntouch '{marker2}'\n")
    hook2.chmod(0o755)
    git(root2, "add", ".githooks/post-merge")
    git(root2, "commit", "-qm", "add target hook")
    git(root2, "config", "core.hooksPath", ".githooks")
    run2 = completion(root2)
    run2.run(monkeypatch)
    assert not marker2.exists()
    assert _post_merge_result(run2)["reason"] == "changed_by_target_branch"


def test_ignored_untracked_worktree_hook_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = repository(tmp_path)
    marker = tmp_path / "untracked-hook-ran"
    hook = root / ".githooks/post-merge"
    hook.parent.mkdir()
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    hook.chmod(0o755)
    git(root, "config", "core.hooksPath", ".githooks")
    git(root, "config", "--local", "core.excludesFile", "/dev/null")
    (root / ".git/info/exclude").write_text(".githooks/post-merge\n")
    assert git(root, "check-ignore", ".githooks/post-merge") == ".githooks/post-merge"
    run = completion(root)
    merged = run.run(monkeypatch)
    assert not marker.exists()
    assert _post_merge_result(run)["status"] == "skipped"
    assert _post_merge_result(run)["reason"] == "not_tracked_in_base"
    assert git(root, "rev-parse", "main") == merged


def test_overridden_standard_hook_and_disabled_git_hook_path_are_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = repository(tmp_path)
    standard = root / ".git/hooks/post-merge"
    marker = tmp_path / "standard-ran"
    standard.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    standard.chmod(0o755)
    git(root, "config", "core.hooksPath", "/dev/null")
    run = completion(root)
    run.run(monkeypatch)
    assert not marker.exists()
    result = _post_merge_result(run)
    assert result["status"] == "skipped"
    assert result["overridden_standard_hook"] == str(standard)


def test_symlink_in_configured_hook_directory_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = repository(tmp_path)
    real = tmp_path / "real-hooks"
    real.mkdir()
    marker = tmp_path / "external-ran"
    hook = real / "post-merge"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    hook.chmod(0o755)
    alias = tmp_path / "linked-hooks"
    alias.symlink_to(real, target_is_directory=True)
    git(root, "config", "core.hooksPath", str(alias))
    run = completion(root)
    run.run(monkeypatch)
    assert not marker.exists()
    assert str(_post_merge_result(run)["reason"]).startswith("symlink:")


def test_hook_output_is_bounded_and_merge_disabled_reports_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = repository(tmp_path)
    hook = root / ".git/hooks/post-merge"
    hook.write_text("#!/bin/sh\nhead -c 9000 /dev/zero | tr '\\0' x\n")
    hook.chmod(0o755)
    run = completion(root)
    run.run(monkeypatch)
    result = _post_merge_result(run)
    assert len(result["stdout"]) == workflow_completion.HOOK_OUTPUT_LIMIT
    assert result["stdout_truncated"] is True

    disabled_parent = tmp_path / "disabled"
    disabled_parent.mkdir()
    root2 = repository(disabled_parent)
    marker = tmp_path / "disabled-hook-ran"
    hook2 = root2 / ".git/hooks/post-merge"
    hook2.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    hook2.chmod(0o755)
    run2 = completion(root2, merge=False)
    run2.run(monkeypatch)
    assert not marker.exists()
    assert _post_merge_result(run2)["reason"] == "merge_disabled"


@pytest.mark.parametrize("phase", tuple(SideEffectBoundaryPhase))
def test_post_merge_crash_windows_never_repeat_an_open_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    phase: SideEffectBoundaryPhase,
) -> None:
    root = repository(tmp_path)
    marker = tmp_path / "hook-count"
    hook = root / ".git/hooks/post-merge"
    hook.write_text(f"#!/bin/sh\nprintf x >> '{marker}'\n")
    hook.chmod(0o755)
    run = completion(root)
    interrupted = False

    def interrupt(boundary) -> None:  # type: ignore[no-untyped-def]
        nonlocal interrupted
        if (not interrupted and boundary.effect_class == "post_merge_hook"
            and boundary.phase == phase):
            interrupted = True
            raise RuntimeError("injected post-merge crash")

    with pytest.raises(RuntimeError, match="injected post-merge crash"):
        run.run(monkeypatch, interrupt)
    assert interrupted
    if phase is SideEffectBoundaryPhase.BEFORE_INTENT:
        run.run(monkeypatch)
        assert marker.read_text() == "x"
    elif phase is SideEffectBoundaryPhase.AFTER_RESULT:
        run.run(monkeypatch)
        assert marker.read_text() == "x"
    else:
        with pytest.raises(SideEffectReconciliationError, match="unknown physical outcome"):
            run.run(monkeypatch)
        expected = "x" if phase in {
            SideEffectBoundaryPhase.AFTER_EFFECT,
            SideEffectBoundaryPhase.BEFORE_RESULT,
        } else ""
        assert (marker.read_text() if marker.exists() else "") == expected


@pytest.mark.parametrize("legacy", (False, True))
def test_unknown_post_merge_can_be_acknowledged_once_without_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, legacy: bool,
) -> None:
    root = repository(tmp_path)
    marker = tmp_path / "hook-count"
    hook = root / ".git/hooks/post-merge"
    hook.write_text(f"#!/bin/sh\nprintf x >> '{marker}'\n")
    hook.chmod(0o755)
    run = completion(root)

    def interrupt(boundary) -> None:  # type: ignore[no-untyped-def]
        if (boundary.effect_class == "post_merge_hook"
            and boundary.phase is SideEffectBoundaryPhase.AFTER_EFFECT):
            raise RuntimeError("injected post-merge crash")

    with pytest.raises(RuntimeError, match="injected post-merge crash"):
        run.run(monkeypatch, interrupt)
    commit = git(root, "rev-parse", "main")
    task = tmp_path / "task.md"
    task.write_text("unchanged task\n")
    import hashlib
    digest = hashlib.sha256(task.read_text().encode()).hexdigest()
    key, effect = next((key, item) for key, item in run.bridge.effects.items()
                       if item.effect_class == "post_merge_hook")
    if legacy:
        del run.bridge.effects[key]
        effect.operation = effect.operation[:3]
        key = stable_side_effect_key("post_merge_hook", "3", effect.operation)
        run.bridge.effects[key] = effect
    original_operation = effect.operation
    effect.intent_record_id = "intent"
    payload = SideEffectPayload(stable_side_effect_key(
        "post_merge_hook", "3", effect.operation), "post_merge_hook", "3",
        effect.operation, "intent", None)
    record = SimpleNamespace(record_id="intent", payload=payload,
                             fingerprint=SimpleNamespace(sha256="0" * 64,
                                                         kind="implementation"))
    monkeypatch.setattr(workflow_completion, "replay_artifacts", lambda *_: SimpleNamespace(
        run_profile=run.profile, side_effects=tuple(run.bridge.effects.values()),
        records=(record,),
    ))
    state = SimpleNamespace(run_id="run", current_work_unit_id=3,
                            task_file=str(task), task_digest=digest)
    task.write_text("changed\n")
    with pytest.raises(GitTransactionError, match="task content changed"):
        workflow_completion.acknowledge_unknown_post_merge(
            root, state, run.bridge, task, commit, "reviewed")
    task.write_text("unchanged task\n")
    workflow_completion.acknowledge_unknown_post_merge(
        root, state, run.bridge, task, commit, "reviewed")
    assert json.loads(effect.result)["status"] == "acknowledged_unknown"
    assert effect.operation == original_operation
    with pytest.raises(GitTransactionError, match="no unique open"):
        workflow_completion.acknowledge_unknown_post_merge(
            root, state, run.bridge, task, commit, "reviewed")
    run.run(monkeypatch)
    assert marker.read_text() == "x"
    assert effect.operation == original_operation
    assert run.bridge.effects[key] is effect
    assert len([item for item in run.bridge.effects.values()
                if item.effect_class == "post_merge_hook"]) == 1
    assert "acknowledged_unknown" in caplog.text
    assert str(hook) in caplog.text
    assert commit in caplog.text
    assert "uncertain" in caplog.text
    run.run(monkeypatch)
    assert marker.read_text() == "x"


@pytest.mark.parametrize("merge", (True, False))
def test_production_acknowledgment_resumes_real_open_hook_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, merge: bool,
) -> None:
    branch = "feature/post-merge-acknowledgment"
    root = production_repository(tmp_path, branch)
    (root / ".git/info/exclude").write_text("inbox/\noutbox/\n")
    inbox = root / "inbox"
    inbox.mkdir()
    task = inbox / "task.md"
    write_production_task(task, branch, "src/one.py")
    audit_path = f"docs/internal/task-review-{hashlib.sha256(task.read_bytes()).hexdigest()[:8]}.md"
    run_id = "watch-post-merge-acknowledgment"
    save_watch_identity(task, WatchTaskIdentity(
        run_id, hashlib.sha256(task.read_bytes()).hexdigest(),
        True, "structured-v2", 2,
    ))
    marker = tmp_path / "hook-count"
    hook = root / ".git/hooks/post-merge"
    hook.write_text(f"#!/bin/sh\nprintf x >> '{marker}'\n")
    hook.chmod(0o755)
    if not merge:
        monkeypatch.setattr(
            ProductionWorkflowDriver, "_completion_policy",
            lambda _driver: (False, None, "{run_id}", True),
        )

    def args_for_run():  # type: ignore[no-untyped-def]
        args = production_args(root, task)
        args.watch_run_id = run_id
        if not merge:
            args.repo_config = replace(
                args.repo_config,
                workflow=replace(args.repo_config.workflow,
                                 merge_completed_branch=False),
            )
        return args

    def codex(_driver, invocation):  # type: ignore[no-untyped-def]
        target = root / "src/one.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        if invocation.step.value == "codex_plan":
            target.write_text("value = 0\n")
            return _native_plan_output(invocation, summary="add value",
                                       scope_paths=(audit_path, "src/one.py"))
        target.write_text("value = 1\n")
        return _native_implementation_output(invocation)

    def review(driver, invocation):  # type: ignore[no-untyped-def]
        if invocation.step.value == "claude_final_review":
            return _native_final_review_output(driver, invocation, finding_id=None)
        return _native_review_approval(invocation)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", review)
    monkeypatch.chdir(root)
    real_hook = workflow_completion._run_hook
    real_reason = workflow_completion._hook_reason

    def run_then_interrupt(*args):  # type: ignore[no-untyped-def]
        real_hook(*args)
        raise RuntimeError("hook result interrupted")

    if merge:
        monkeypatch.setattr(workflow_completion, "_run_hook", run_then_interrupt)
    else:
        def interrupt_before_skip(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("hook result interrupted")
        monkeypatch.setattr(workflow_completion, "_hook_reason", interrupt_before_skip)
    with pytest.raises(RuntimeError, match="hook result interrupted"):
        orchestrator.run_production_workflow(task, args_for_run())
    assert (marker.read_text() if marker.exists() else "") == ("x" if merge else "")
    commit = git(root, "rev-parse", "master" if merge else branch)
    store = ArtifactStore(root, run_id)
    before = replay_artifacts(store.load_chain(), run_id)
    open_hooks = [item for item in before.side_effects
                  if item.effect_class == "post_merge_hook" and item.result is None]
    assert len(open_hooks) == 1
    assert open_hooks[0].operation[1] == commit
    assert before.run_profile.base_branch == ("master" if merge else None)

    if not merge:
        monkeypatch.setattr(workflow_completion, "_hook_reason", real_reason)
        args = args_for_run()
        args.resume = True
        resumed = orchestrator.run_production_workflow(task, args)
        assert resumed.workflow_completed
        assert not marker.exists()
        replay = replay_artifacts(store.load_chain(), run_id)
        skipped = [item for item in replay.side_effects
                   if item.effect_class == "post_merge_hook"]
        assert len(skipped) == 1
        assert json.loads(skipped[0].result)["reason"] == "merge_disabled"
        assert len([record for record in store.load_chain()
                    if isinstance(record.payload, SideEffectPayload)
                    and record.payload.effect_class == "post_merge_hook"
                    and record.payload.phase == "result"]) == 1
        return

    args = args_for_run()
    args.resume = True
    args.acknowledge_post_merge = commit
    args.post_merge_rationale = "Hook-Ausgang manuell geprüft"
    changed_task = task.read_text()
    task.write_text("changed task\n")
    record_count = len(store.load_chain())
    with pytest.raises(Exception, match="task.*digest|task.*identity|task.*differ"):
        orchestrator.run_production_workflow(task, args)
    assert len(store.load_chain()) == record_count
    task.write_text(changed_task)
    args.acknowledge_post_merge = "0" * 40
    with pytest.raises(GitTransactionError,
                       match="base branch moved" if merge else "no unique open"):
        orchestrator.run_production_workflow(task, args)
    assert len(store.load_chain()) == record_count

    monkeypatch.setattr(workflow_completion, "_run_hook", real_hook)
    monkeypatch.setattr(workflow_completion, "_hook_reason", real_reason)
    args.acknowledge_post_merge = commit
    resumed = orchestrator.run_production_workflow(task, args)
    assert resumed.workflow_completed
    assert (marker.read_text() if marker.exists() else "") == ("x" if merge else "")
    results = [item for item in replay_artifacts(store.load_chain(), run_id).side_effects
               if item.effect_class == "post_merge_hook"]
    assert len(results) == 1
    persisted_results = [record for record in store.load_chain()
                         if isinstance(record.payload, SideEffectPayload)
                         and record.payload.effect_class == "post_merge_hook"
                         and record.payload.phase == "result"]
    assert len(persisted_results) == 1
    intent = next(record for record in store.load_chain()
                  if record.record_id == open_hooks[0].intent_record_id)
    assert persisted_results[0].payload.effect_key == intent.payload.effect_key
    assert persisted_results[0].logical_id == intent.logical_id
    assert json.loads(results[0].result) == {
        "status": "acknowledged_unknown", "hook": str(hook),
        "merge_commit": commit, "rationale": "Hook-Ausgang manuell geprüft",
        "task_digest": hashlib.sha256(task.read_bytes()).hexdigest(),
    }
    record_count = len(store.load_chain())
    args.acknowledge_post_merge = None
    again = orchestrator.run_production_workflow(task, args)
    assert again.workflow_completed
    assert (marker.read_text() if marker.exists() else "") == ("x" if merge else "")
    assert len(store.load_chain()) == record_count

    args.resume_explicit = True
    args.task_file_explicit = True
    args.inbox_dir = inbox
    args.outbox_dir = root / "outbox"
    assert orchestrator.run_pipeline(task, args) == 0
    assert not task.exists()
    done = list((root / "outbox/done").glob("*.md"))
    assert len(done) == 1
    assert done[0].read_text() == changed_task
