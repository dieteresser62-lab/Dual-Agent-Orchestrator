"""Ledger-bound local archive and optional merge after a clean final review."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import subprocess
import tempfile
import time
from typing import Callable

from artifact_bridge import ArtifactBridge
from artifact_models import SideEffectPayload
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
HOOK_OUTPUT_LIMIT = 8192
HOOK_TIMEOUT_SECONDS = 600
HOOK_TERMINATION_GRACE_SECONDS = 0.5
logger = logging.getLogger(__name__)


def _hook_path(root: Path) -> tuple[Path, Path | None]:
    """Return Git's effective hook and an overridden standard hook, if any."""
    common_dir = Path(os.fsdecode(_git(root, "rev-parse", "--git-common-dir").stdout).strip())
    standard = common_dir / "hooks/post-merge"
    if not standard.is_absolute():
        standard = root / standard
    configured = _git(root, "config", "--path", "--get", "core.hooksPath", codes=(0, 1))
    if configured.returncode:
        return standard, None
    directory = Path(os.fsdecode(configured.stdout).strip())
    if not directory.is_absolute():
        directory = root / directory
    return directory / "post-merge", standard


def _hook_reason(root: Path, hook: Path, *, base_head: str | None,
                 target_head: str | None) -> str | None:
    absolute = Path(os.path.abspath(hook))
    try:
        relative = absolute.relative_to(root.resolve()).as_posix()
    except ValueError:
        relative = None
    if (relative is not None and relative.split("/", 1)[0] != ".git"
            and base_head is not None and target_head is not None):
        def tree_entry(commit: str) -> bytes:
            # The mode, object type and object ID all belong to the decision.
            return _git(root, "ls-tree", "-z", commit, "--", relative).stdout

        fork = _value(root, "merge-base", base_head, target_head)
        base_entry = tree_entry(base_head)
        target_entry = tree_entry(target_head)
        if (target_entry and not base_entry) or tree_entry(fork) != target_entry:
            return "changed_by_target_branch"
    for segment in (absolute, *absolute.parents):
        try:
            mode = segment.lstat().st_mode
        except OSError:
            return "missing"
        if stat.S_ISLNK(mode):
            return f"symlink:{segment}"
    mode = absolute.stat().st_mode
    if not stat.S_ISREG(mode):
        return "not_regular"
    if not mode & 0o111:
        return "not_executable"
    return None


def _hook_result(status: str, hook: Path, *, reason: str | None = None,
                 overridden: Path | None = None, exit_code: int | None = None,
                 stdout: bytes = b"", stderr: bytes = b"",
                 stdout_truncated: bool = False,
                 stderr_truncated: bool = False,
                 termination_uncertain: bool = False) -> str:
    return json.dumps({
        "status": status, "hook": str(hook), "reason": reason,
        "overridden_standard_hook": str(overridden) if overridden else None,
        "exit_code": exit_code,
        "stdout": stdout.decode("utf-8", "replace"),
        "stderr": stderr.decode("utf-8", "replace"),
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "termination_uncertain": termination_uncertain,
    }, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _hook_group_ended(group_id: int) -> bool:
    try:
        os.killpg(group_id, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def _wait_for_hook_group(process: subprocess.Popen[bytes], deadline: float) -> bool:
    while True:
        process.poll()
        if _hook_group_ended(process.pid):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.02, remaining))


def _terminate_hook_group(process: subprocess.Popen[bytes]) -> bool:
    """Stop the hook's own session; return whether its end was confirmed."""
    uncertain = False
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        except OSError:
            uncertain = True
        if _wait_for_hook_group(
            process, time.monotonic() + HOOK_TERMINATION_GRACE_SECONDS
        ):
            break
    else:
        uncertain = True
    try:
        process.wait(timeout=HOOK_TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        uncertain = True
    return not uncertain


def _run_hook(root: Path, hook: Path, overridden: Path | None) -> str:
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        termination_uncertain = False
        try:
            process = subprocess.Popen((str(hook), "0"), cwd=root, stdout=output,
                                       stderr=errors, start_new_session=True)
            exit_code = process.wait(timeout=HOOK_TIMEOUT_SECONDS)
            status = "success" if exit_code == 0 else "failed"
        except subprocess.TimeoutExpired:
            status, exit_code = "timeout", None
            termination_uncertain = not _terminate_hook_group(process)
        except OSError as exc:
            status, exit_code = "failed", None
            errors.write(os.fsencode(str(exc)))
        output.seek(0)
        errors.seek(0)
        stdout = output.read(HOOK_OUTPUT_LIMIT)
        stderr = errors.read(HOOK_OUTPUT_LIMIT)
        return _hook_result(
            status, hook, overridden=overridden, exit_code=exit_code,
            stdout=stdout, stderr=stderr,
            stdout_truncated=output.read(1) != b"",
            stderr_truncated=errors.read(1) != b"",
            termination_uncertain=termination_uncertain,
        )


def _post_merge_effect(root: Path, state: WorkflowState, replay: object,
                       executor: SideEffectExecutor,
                       make_spec: Callable[..., SideEffectSpec],
                       commit: str, *, merge_enabled: bool,
                       base_head: str | None = None,
                       target_head: str | None = None) -> None:
    previous = next((item for item in reversed(replay.side_effects)
                     if item.effect_class == "post_merge_hook"
                     and item.work_unit_id == str(state.current_work_unit_id)
                     and item.operation[1] == commit), None)
    hook, standard = _hook_path(root)
    operation = previous.operation if previous else ("post_merge", commit, str(hook))
    hook = Path(operation[2])
    spec = make_spec("post_merge_hook", operation, fingerprint=_fingerprint(operation))
    overridden = (standard if standard is not None
                  and Path(os.path.abspath(standard)) != Path(os.path.abspath(hook))
                  and standard.is_file() else None)

    def perform() -> tuple[str, str]:
        reason = _hook_reason(root, hook, base_head=base_head, target_head=target_head)
        if not merge_enabled:
            reason = "missing" if reason == "missing" else "merge_disabled"
        if reason is not None:
            result = _hook_result("skipped", hook, reason=reason, overridden=overridden)
        else:
            result = _run_hook(root, hook, overridden)
        return result, result

    result = executor.execute(
        spec, reconcile=lambda: Reconciliation(ReconciliationOutcome.UNKNOWN),
        perform=perform,
    )
    outcome = json.loads(str(result))
    if outcome["status"] in {"failed", "timeout"}:
        logger.warning("post-merge hook %s: %s (exit=%s, termination_uncertain=%s)",
                       hook, outcome["status"], outcome["exit_code"],
                       outcome["termination_uncertain"])
    elif outcome["status"] == "skipped" and outcome["reason"] != "missing":
        logger.warning("post-merge hook %s skipped: %s", hook, outcome["reason"])
    if overridden is not None:
        logger.warning("post-merge hook %s skipped: overridden by core.hooksPath", overridden)


def acknowledge_unknown_post_merge(root: Path, state: WorkflowState,
                                   bridge: ArtifactBridge, task_file: Path, commit: str,
                                   rationale: str) -> None:
    """Record an operator's acknowledgment of one exact unresolved hook intent."""
    if not rationale.strip():
        raise GitTransactionError("post-merge acknowledgment requires a rationale")
    replay = replay_artifacts(bridge.store.current_chain(), state.run_id)
    profile = replay.run_profile
    if profile is None or not profile.post_merge_hook_enabled:
        raise GitTransactionError("run has no post-merge policy")
    if task_file.resolve() != Path(state.task_file).resolve():
        raise GitTransactionError("task identity changed")
    try:
        task_digest = hashlib.sha256(task_file.read_text(encoding="utf-8").encode()).hexdigest()
    except (OSError, UnicodeError) as exc:
        raise GitTransactionError("task file is unreadable") from exc
    if task_digest != state.task_digest:
        raise GitTransactionError("task content changed since the run began")
    if profile.merge_completed_branch and _base_head(root, profile.base_branch or "") != commit:
        raise GitTransactionError("base branch moved after the acknowledged merge")
    effects = [item for item in replay.side_effects
               if item.effect_class == "post_merge_hook"
               and item.operation[1] == commit
               and item.work_unit_id == str(state.current_work_unit_id)]
    if len(effects) != 1 or effects[0].result is not None:
        raise GitTransactionError("no unique open post-merge intent for that commit")
    prior = [item for item in replay.side_effects
             if item.effect_class in {"git_merge", "git_commit"}
             and item.result == commit
             and item.work_unit_id == effects[0].work_unit_id]
    if len(prior) != 1:
        raise GitTransactionError("post-merge intent lacks a confirmed commit result")
    intent = next(record for record in replay.records
                  if record.record_id == effects[0].intent_record_id)
    assert isinstance(intent.payload, SideEffectPayload)
    result = json.dumps({
        "status": "acknowledged_unknown", "hook": effects[0].operation[2],
        "merge_commit": commit, "rationale": rationale.strip(),
        "task_digest": state.task_digest,
    }, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    bridge.record_side_effect_result(
        effect_class="post_merge_hook", work_unit_id=effects[0].work_unit_id,
        operation=effects[0].operation, result=result,
        fingerprint_sha256=intent.fingerprint.sha256,
        fingerprint_kind=intent.fingerprint.kind,
    )


def archive_relative_directory(profile: object, run_id: str, branch: str) -> PurePosixPath:
    pattern = getattr(profile, "archive_run_directory", None)
    if pattern is None:
        return ARCHIVE_RELATIVE
    if not re.fullmatch(r"[A-Za-z0-9._-]+", run_id) or run_id in {".", ".."}:
        raise GitTransactionError(f"invalid run_id for archive directory: {run_id!r}")
    year = re.search(r"(?<!\d)(20\d{2})(?:\d{4})?(?!\d)", run_id)
    if "{year}" in pattern and year is None:
        raise GitTransactionError(f"archive pattern needs a year in run_id {run_id!r}")
    slug = re.sub(r"[^a-z0-9]+", "-", branch.lower()).strip("-")
    if not slug:
        raise GitTransactionError(f"invalid branch for archive directory: {branch!r}")
    expanded = (pattern.replace("{run_id}", run_id)
                .replace("{year}", year.group(1) if year else "")
                .replace("{branch_slug}", slug))
    return ARCHIVE_RELATIVE / PurePosixPath(expanded)


def check_archive_directory(root: Path, profile: object, run_id: str,
                            branch: str) -> PurePosixPath:
    relative = archive_relative_directory(profile, run_id, branch)
    # The legacy flat destination may already exist; only new run folders must be absent.
    for index in range(1, len(relative.parts) + 1):
        segment = root.joinpath(*relative.parts[:index])
        if segment.is_symlink() or (segment.exists() and not segment.is_dir()):
            raise GitTransactionError(f"archive destination conflict at {segment}")
        if index == len(relative.parts) and getattr(profile, "archive_run_directory", None) is not None:
            if segment.exists():
                raise GitTransactionError(f"archive destination conflict at {segment}")
    return relative


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


def _check_archive_names(root: Path, paths: tuple[str, ...], archive_relative: PurePosixPath) -> None:
    for path in paths:
        destination = root / archive_relative / PurePosixPath(path).name
        if destination.exists() or destination.is_symlink():
            raise GitTransactionError(
                f"archive name conflict at {destination}; resolve it, then resume"
            )


def _preview_archive_tree(root: Path, head: str, paths: tuple[str, ...],
                          archive_relative: PurePosixPath) -> str:
    archive = root / archive_relative
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
                 f"{mode},{object_id},{archive_relative}/{source.name}", env=env)
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
    archive_relative = archive_relative_directory(profile, state.run_id, state.branch)
    if any(effect.effect_class == "git_commit"
           and effect.operation[0] == "archive_commit"
           and effect.work_unit_id == str(state.current_work_unit_id)
           for effect in replay.side_effects):
        return True
    check_archive_directory(root, profile, state.run_id, state.branch)
    identity = inspect_repository(root)
    if identity.branch != state.branch:
        raise GitTransactionError(
            f"expected target branch {state.branch!r}; switch to it, then resume"
        )
    base_ref = profile.base_branch or state.branch_base
    base_head = _base_head(root, profile.base_branch or "") if profile.merge_completed_branch else None
    initial_paths = _archive_paths(root, base_ref, identity.head)
    if state.audit_report_path is not None:
        audit = PurePosixPath(state.audit_report_path)
        if (audit.parent == PurePosixPath("docs/internal")
            and not _git(root, "ls-tree", "--name-only", identity.head, "--",
                         state.audit_report_path).stdout):
            initial_paths = tuple(sorted(set((*initial_paths, state.audit_report_path))))
    _check_archive_names(root, initial_paths, archive_relative)
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
    paths = _archive_paths(root, base_ref, target_head)
    tree = _preview_archive_tree(root, target_head, paths, archive_relative)
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
    archive_relative = archive_relative_directory(profile, state.run_id, state.branch)
    target, base = state.branch, profile.base_branch
    base_ref = base or state.branch_base
    prior_archive = next((effect for effect in reversed(replay.side_effects)
                          if effect.effect_class == "git_commit"
                          and effect.work_unit_id == str(state.current_work_unit_id)
                          and effect.operation[0] == "archive_commit"), None)
    prior_merge = next((effect for effect in reversed(replay.side_effects)
                        if effect.effect_class == "git_merge"
                        and effect.work_unit_id == str(state.current_work_unit_id)), None)
    if prior_archive is None:
        check_archive_directory(root, profile, state.run_id, state.branch)
        identity = inspect_repository(root)
        if identity.branch != target:
            raise GitTransactionError(
                f"expected target branch {target!r}; switch to it, then resume"
            )
        base_head = _base_head(root, base or "") if profile.merge_completed_branch else None
        paths = _archive_paths(root, base_ref, identity.head)
        _check_archive_names(root, paths, archive_relative)
        _clean(root)
        tree = _preview_archive_tree(root, identity.head, paths, archive_relative)
        if profile.merge_completed_branch:
            _preflight_merge(root, base or "", base_head, identity.head, tree)
        operation = (
            "archive_commit", target, identity.head, tree,
            hashlib.sha256("\0".join(paths).encode()).hexdigest(),
            hashlib.sha256(ARCHIVE_SUBJECT.encode()).hexdigest(),
        )
    else:
        operation = prior_archive.operation
        paths = _archive_paths(root, base_ref, operation[2])
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
        archive_dir = root / archive_relative
        created_dirs: list[Path] = []
        try:
            if paths:
                check_archive_directory(root, profile, state.run_id, state.branch)
                for index in range(1, len(archive_relative.parts) + 1):
                    directory = root.joinpath(*archive_relative.parts[:index])
                    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
                        raise GitTransactionError(f"archive destination conflict at {directory}")
                    if index == len(archive_relative.parts) and profile.archive_run_directory is not None:
                        directory.mkdir()
                        created_dirs.append(directory)
                    elif not directory.exists():
                        directory.mkdir()
                        created_dirs.append(directory)
            for path in paths:
                source = root / path
                destination = archive_dir / source.name
                if destination.exists() or destination.is_symlink():
                    raise GitTransactionError(f"archive name conflict at {destination}")
                source.rename(destination)
                moved.append((source, destination))
                _git(root, "add", "-A", "--", path, f"{archive_relative}/{source.name}")
            if _value(root, "write-tree") != operation[3]:
                raise GitTransactionError("archive tree differs from the preflight tree")
            _git(root, "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
                 "commit", "--allow-empty", "-m", ARCHIVE_SUBJECT)
        except Exception:
            if inspect_repository(root).head == operation[2]:
                for source, destination in reversed(moved):
                    destination.rename(source)
                _git(root, "read-tree", original_index)
                for directory in reversed(created_dirs):
                    if directory.is_dir():
                        directory.rmdir()
            raise
        committed = inspect_repository(root).head
        return committed, committed

    archive_result = executor.execute(spec, reconcile=reconcile_archive,
                                      perform=perform_archive)
    archive_head = str(archive_result)
    if not profile.merge_completed_branch:
        if getattr(profile, "post_merge_hook_enabled", False):
            _post_merge_effect(root, state, replay, executor, make_spec,
                               archive_head, merge_enabled=False)
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
    if getattr(profile, "post_merge_hook_enabled", False):
        _post_merge_effect(root, state, replay, executor, make_spec,
                           str(merged_result), merge_enabled=True,
                           base_head=merge_operation[2],
                           target_head=merge_operation[3])
    return str(merged_result)
