"""Ledger-bound local archive and optional merge after a clean final review."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
from typing import Callable

from artifact_bridge import ArtifactBridge
from artifact_replay import replay_artifacts
from git_service import GitTransactionError, inspect_repository
from side_effects import (
    Reconciliation,
    ReconciliationOutcome,
    SideEffectExecutor,
    SideEffectSpec,
    reconcile_git_commit,
)
from workflow_state import WorkflowState


ARCHIVE_SUBJECT = "docs: archive completed orchestrator work"
ARCHIVE_RELATIVE = PurePosixPath("docs/internal") / "archive"


def merge_subject(target: str, base: str) -> str:
    return f"Merge {target} into {base}"


def _git(root: Path, *args: str, env: dict[str, str] | None = None,
         codes: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(("git", *args), cwd=root, env=env,
                            capture_output=True, check=False)
    if result.returncode not in codes:
        detail = os.fsdecode(result.stderr or result.stdout).strip()
        raise GitTransactionError(f"git {' '.join(args)}: {detail}")
    return result


def _value(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return os.fsdecode(_git(root, *args, env=env).stdout).strip()


def _clean(root: Path) -> None:
    dirty = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    if dirty:
        raise GitTransactionError(
            "completion requires a clean worktree and index; restore or commit "
            "the reported files, then resume the task"
        )


def _clean_except_audit(root: Path, audit_path: str | None) -> None:
    dirty = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    for row in dirty.split(b"\0"):
        if not row:
            continue
        if os.fsdecode(row[3:]) != audit_path or row[:2] in {b"R ", b" R", b"C ", b" C"}:
            raise GitTransactionError(
                "completion requires a clean worktree outside the final audit; "
                "restore or commit the reported files, then resume the task"
            )


def _base_head(root: Path, base: str) -> str:
    if not base:
        raise GitTransactionError(
            "completion has no bound base branch; restore the run profile before resuming"
        )
    check = _git(root, "show-ref", "--verify", "--quiet", f"refs/heads/{base}",
                 codes=(0, 1))
    if check.returncode:
        raise GitTransactionError(
            f"base branch {base!r} is missing; restore it, then resume the task"
        )
    return _value(root, "rev-parse", "--verify", f"refs/heads/{base}^{{commit}}")


def _archive_paths(root: Path, base: str, target_head: str) -> tuple[str, ...]:
    fork = _value(root, "merge-base", base, target_head)
    names = _git(root, "diff", "--name-only", "--diff-filter=A", "--no-renames",
                 "-z", fork, target_head, "--", "docs/internal/").stdout
    return tuple(sorted(
        os.fsdecode(raw) for raw in names.split(b"\0") if raw
        and PurePosixPath(os.fsdecode(raw)).parent == PurePosixPath("docs/internal")
    ))


def _check_archive_names(root: Path, paths: tuple[str, ...]) -> None:
    for path in paths:
        destination = root / ARCHIVE_RELATIVE / PurePosixPath(path).name
        if destination.exists() or destination.is_symlink():
            raise GitTransactionError(
                f"archive name conflict at {destination}; resolve it, then resume"
            )


def _preview_archive_tree(root: Path, head: str, paths: tuple[str, ...]) -> str:
    archive = root / ARCHIVE_RELATIVE
    if archive.exists() and (archive.is_symlink() or not archive.is_dir()):
        raise GitTransactionError(
            "archive destination is not a directory; repair it, then resume the task"
        )
    with tempfile.TemporaryDirectory(prefix="orchestrator-archive-index-") as temp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(temp) / "index"))
        _git(root, "read-tree", head, env=env)
        for path in paths:
            source = root / path
            destination = archive / source.name
            if not source.is_file() or source.is_symlink():
                raise GitTransactionError(
                    f"archive source {path} is not a regular file; repair it, then resume"
                )
            if destination.exists() or destination.is_symlink():
                raise GitTransactionError(
                    f"archive name conflict at {destination}; resolve it, then resume"
                )
            raw = _git(root, "ls-files", "--stage", "-z", "--", path,
                       env=env).stdout
            if len(raw.split(b"\0")) != 2:
                raise GitTransactionError(f"archive source is not tracked: {path}")
            mode, object_id, stage = os.fsdecode(raw.split(b"\t", 1)[0]).split()
            if stage != "0" or mode not in {"100644", "100755"}:
                raise GitTransactionError(f"archive source is not a regular Git file: {path}")
            _git(root, "update-index", "--force-remove", "--", path, env=env)
            _git(root, "update-index", "--add", "--cacheinfo",
                 f"{mode},{object_id},{ARCHIVE_RELATIVE}/{source.name}", env=env)
        return _value(root, "write-tree", env=env)


def _preflight_merge(root: Path, base: str, base_head: str,
                     target_head: str, archive_tree: str) -> None:
    # A synthetic child has the exact tree the archive commit will create.
    synthetic = _value(root, "-c", "user.name=Orchestrator",
                       "-c", "user.email=orchestrator@localhost", "commit-tree",
                       archive_tree, "-p", target_head, "-m", ARCHIVE_SUBJECT)
    merge = _git(root, "merge-tree", "--write-tree", base_head, synthetic,
                 codes=(0, 1))
    if merge.returncode:
        raise GitTransactionError(
            f"merge conflict between {base} and the archived target; resolve the "
            "conflicting base-branch changes with a reviewed commit, then resume"
        )
    worktrees = _value(root, "worktree", "list", "--porcelain")
    active = None
    for line in worktrees.splitlines():
        if line.startswith("worktree "):
            active = Path(line[9:]).resolve()
        elif line == f"branch refs/heads/{base}" and active != root.resolve():
            raise GitTransactionError(
                f"base branch {base!r} is checked out in worktree {active}; "
                "release it there, then resume the task"
            )


def _commit_info(root: Path, commit: str) -> tuple[tuple[str, ...], str]:
    lines = _value(root, "show", "-s", "--format=%P%n%T", commit).splitlines()
    if len(lines) != 2:
        raise GitTransactionError(f"could not inspect commit {commit}")
    return tuple(lines[0].split()), lines[1]


def _fingerprint(operation: tuple[str, ...]) -> str:
    return hashlib.sha256("\0".join(operation).encode()).hexdigest()


def preflight_chain(root: Path, state: WorkflowState, bridge: ArtifactBridge) -> bool:
    """Prove deterministic completion blockers before final audit can commit."""
    replay = replay_artifacts(bridge.store.current_chain(), state.run_id)
    profile = replay.run_profile
    if profile is None:
        raise GitTransactionError("completion has no bound run profile")
    if any(effect.effect_class == "git_commit"
           and effect.operation[0] == "archive_commit"
           and effect.work_unit_id == str(state.current_work_unit_id)
           for effect in replay.side_effects):
        return True
    identity = inspect_repository(root)
    if identity.branch != state.branch:
        raise GitTransactionError(
            f"expected target branch {state.branch!r}; switch to it, then resume"
        )
    base_head = _base_head(root, profile.base_branch or "")
    initial_paths = _archive_paths(root, profile.base_branch or "", identity.head)
    if state.audit_report_path is not None:
        audit = PurePosixPath(state.audit_report_path)
        if (audit.parent == PurePosixPath("docs/internal")
            and not _git(root, "ls-tree", "--name-only", identity.head, "--",
                         state.audit_report_path).stdout):
            initial_paths = tuple(sorted(set((*initial_paths, state.audit_report_path))))
    _check_archive_names(root, initial_paths)
    _clean_except_audit(root, state.audit_report_path)
    target_head = identity.head
    if state.audit_report_path is not None:
        with tempfile.TemporaryDirectory(prefix="orchestrator-audit-index-") as temp:
            env = dict(os.environ, GIT_INDEX_FILE=str(Path(temp) / "index"))
            _git(root, "read-tree", target_head, env=env)
            tracked = _git(root, "ls-files", "--error-unmatch", "--",
                           state.audit_report_path, env=env, codes=(0, 1))
            if tracked.returncode == 0 or (root / state.audit_report_path).exists():
                _git(root, "add", "-A", "--", state.audit_report_path, env=env)
            audit_tree = _value(root, "write-tree", env=env)
        if audit_tree != _value(root, "rev-parse", "HEAD^{tree}"):
            target_head = _value(
                root, "-c", "user.name=Orchestrator",
                "-c", "user.email=orchestrator@localhost", "commit-tree",
                audit_tree, "-p", target_head,
                "-m", "docs: finalize orchestrator audit",
            )
    paths = _archive_paths(root, profile.base_branch or "", target_head)
    tree = _preview_archive_tree(root, target_head, paths)
    if profile.merge_completed_branch:
        _preflight_merge(root, profile.base_branch or "", base_head, target_head, tree)
    return False


def complete_chain(
    root: Path,
    state: WorkflowState,
    bridge: ArtifactBridge,
    executor: SideEffectExecutor,
    make_spec: Callable[..., SideEffectSpec],
) -> str:
    """Finish once; a stored intent selects the same operation on resume."""
    replay = replay_artifacts(bridge.store.current_chain(), state.run_id)
    profile = replay.run_profile
    if profile is None:
        raise GitTransactionError("completion has no bound run profile")
    target, base = state.branch, profile.base_branch
    prior_archive = next((effect for effect in reversed(replay.side_effects)
                          if effect.effect_class == "git_commit"
                          and effect.work_unit_id == str(state.current_work_unit_id)
                          and effect.operation[0] == "archive_commit"), None)
    prior_merge = next((effect for effect in reversed(replay.side_effects)
                        if effect.effect_class == "git_merge"
                        and effect.work_unit_id == str(state.current_work_unit_id)), None)
    if prior_archive is None:
        identity = inspect_repository(root)
        if identity.branch != target:
            raise GitTransactionError(
                f"expected target branch {target!r}; switch to it, then resume"
            )
        base_head = _base_head(root, base or "")
        paths = _archive_paths(root, base or "", identity.head)
        _check_archive_names(root, paths)
        _clean(root)
        tree = _preview_archive_tree(root, identity.head, paths)
        if profile.merge_completed_branch:
            _preflight_merge(root, base or "", base_head, identity.head, tree)
        operation = (
            "archive_commit", target, identity.head, tree,
            hashlib.sha256("\0".join(paths).encode()).hexdigest(),
            hashlib.sha256(ARCHIVE_SUBJECT.encode()).hexdigest(),
        )
    else:
        operation = prior_archive.operation
        paths = _archive_paths(root, base or "", operation[2])
    spec = make_spec("git_commit", operation, fingerprint=operation[4])

    def reconcile_archive() -> Reconciliation:
        identity = inspect_repository(root)
        if identity.branch != target:
            return Reconciliation(ReconciliationOutcome.UNKNOWN)
        if identity.head == operation[2]:
            return Reconciliation(ReconciliationOutcome.NOT_OCCURRED)
        parents, tree = _commit_info(root, identity.head)
        return reconcile_git_commit(
            prior_head=operation[2], current_head=identity.head,
            current_parent=parents[0] if len(parents) == 1 else None,
            expected_tree=operation[3], current_tree=tree,
        )

    def perform_archive() -> tuple[str, str]:
        identity = inspect_repository(root)
        if identity.branch != target or identity.head != operation[2]:
            raise GitTransactionError("archive source HEAD changed; restore it and resume")
        _clean(root)
        original_index = _value(root, "write-tree")
        moved: list[tuple[Path, Path]] = []
        archive_dir = root / ARCHIVE_RELATIVE
        created_archive_dir = bool(paths) and not archive_dir.exists()
        try:
            if paths:
                archive_dir.mkdir(parents=True, exist_ok=True)
            for path in paths:
                source = root / path
                destination = root / ARCHIVE_RELATIVE / source.name
                if destination.exists() or destination.is_symlink():
                    raise GitTransactionError(f"archive name conflict at {destination}")
                source.rename(destination)
                moved.append((source, destination))
                _git(root, "add", "-A", "--", path, f"{ARCHIVE_RELATIVE}/{source.name}")
            if _value(root, "write-tree") != operation[3]:
                raise GitTransactionError("archive tree differs from the preflight tree")
            _git(root, "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
                 "commit", "--allow-empty", "-m", ARCHIVE_SUBJECT)
        except Exception:
            if inspect_repository(root).head == operation[2]:
                for source, destination in reversed(moved):
                    destination.rename(source)
                _git(root, "read-tree", original_index)
                if created_archive_dir:
                    archive_dir.rmdir()
            raise
        committed = inspect_repository(root).head
        return committed, committed

    archive_result = executor.execute(spec, reconcile=reconcile_archive,
                                      perform=perform_archive)
    archive_head = str(archive_result)
    if not profile.merge_completed_branch:
        return archive_head

    if prior_merge is None:
        identity = inspect_repository(root)
        if identity.branch != target or identity.head != archive_head:
            raise GitTransactionError("target branch moved after archive; restore it and resume")
        _clean(root)
        base_head = _base_head(root, base or "")
        _preflight_merge(root, base or "", base_head, archive_head,
                         _commit_info(root, archive_head)[1])
        merge_tree = _value(root, "merge-tree", "--write-tree", base_head, archive_head).splitlines()[0]
        subject = merge_subject(target, base or "")
        merge_operation = (
            "merge_commit", base or "", base_head, archive_head, merge_tree,
            hashlib.sha256(subject.encode()).hexdigest(), target,
        )
    else:
        merge_operation = prior_merge.operation
        subject = merge_subject(target, base or "")
    merge_spec = make_spec("git_merge", merge_operation,
                           fingerprint=_fingerprint(merge_operation))

    def reconcile_merge() -> Reconciliation:
        current_base = _base_head(root, base or "")
        if current_base == merge_operation[2]:
            return Reconciliation(ReconciliationOutcome.NOT_OCCURRED)
        parents, tree = _commit_info(root, current_base)
        if parents != (merge_operation[2], merge_operation[3]) or tree != merge_operation[4]:
            return Reconciliation(ReconciliationOutcome.UNKNOWN)
        if inspect_repository(root).branch != base:
            _git(root, "switch", base or "")
        return Reconciliation(ReconciliationOutcome.OCCURRED, current_base)

    def perform_merge() -> tuple[str, str]:
        if _base_head(root, base or "") != merge_operation[2]:
            raise GitTransactionError("base branch moved after preflight; restore it and resume")
        identity = inspect_repository(root)
        if identity.branch == target:
            _clean(root)
            _git(root, "switch", base or "")
        elif identity.branch != base:
            raise GitTransactionError("unexpected active branch during merge; restore it and resume")
        try:
            _git(root, "-c", "merge.autoStash=false", "-c", "core.hooksPath=/dev/null",
                 "-c", "commit.gpgsign=false", "merge", "--no-ff", "-m", subject,
                 merge_operation[6])
        except GitTransactionError as exc:
            if (root / ".git/MERGE_HEAD").exists():
                _git(root, "merge", "--abort")
            _git(root, "switch", target)
            raise GitTransactionError(
                f"local merge failed and was rolled back: {exc}; "
                "repair the base branch or Git configuration, then resume"
            ) from exc
        merged = _base_head(root, base or "")
        parents, tree = _commit_info(root, merged)
        if parents != (merge_operation[2], merge_operation[3]) or tree != merge_operation[4]:
            raise GitTransactionError("merge commit differs from its preflight result")
        return merged, merged

    merged_result = executor.execute(merge_spec, reconcile=reconcile_merge,
                                     perform=perform_merge)
    return str(merged_result)
