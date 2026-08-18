from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, Sequence

from contracts import (
    AgentRole,
    ContractResult,
    FindingClass,
    FindingRecord,
    FindingStatus,
    ValidationAttestation,
)
from repo_changes import RepositoryChanges, collect_repository_changes


FEATURE_BRANCH_PATTERN = re.compile(r"^(?:feature|codex)/[A-Za-z0-9][A-Za-z0-9._-]*$")


class GitTransactionError(RuntimeError):
    """Raised before an unsafe or stale Git side effect can be performed."""


@dataclass(frozen=True)
class RepositoryIdentity:
    repository_root: Path
    branch: str
    head: str
    upstream: str | None
    ahead: int | None
    behind: int | None

    @property
    def remote_status(self) -> str:
        if self.upstream is None:
            return "local-only"
        return f"tracking {self.upstream}; ahead={self.ahead}; behind={self.behind}"


@dataclass(frozen=True)
class TaskBranchPreparation:
    identity: RepositoryIdentity
    previous_branch: str
    action: Literal["already-active", "created", "switched"]


@dataclass(frozen=True)
class SliceGitBoundary:
    slice_id: int
    branch: str
    start_commit: str
    start_fingerprint: str
    scope_paths: tuple[str, ...]
    semantic_markdown_paths: tuple[str, ...] = ()
    excluded_control_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            isinstance(self.slice_id, bool)
            or not isinstance(self.slice_id, int)
            or self.slice_id < 1
        ):
            raise GitTransactionError("slice_id must be a 1-based integer")
        if not FEATURE_BRANCH_PATTERN.fullmatch(self.branch):
            raise GitTransactionError(
                "slice boundary requires a feature/<name> or codex/<name> branch"
            )
        if not self.start_commit.strip():
            raise GitTransactionError("slice boundary requires a start commit")
        if not re.fullmatch(r"[0-9a-f]{64}", self.start_fingerprint):
            raise GitTransactionError("slice boundary requires a SHA-256 start fingerprint")
        normalized = _normalize_scope_paths(self.scope_paths)
        if normalized != self.scope_paths:
            raise GitTransactionError(
                "slice boundary scope paths must be sorted and unique"
            )
        semantic = _normalize_optional_scope_paths(self.semantic_markdown_paths)
        if semantic != self.semantic_markdown_paths:
            raise GitTransactionError(
                "semantic Markdown paths must be sorted and unique"
            )
        if any(path not in self.scope_paths for path in semantic):
            raise GitTransactionError(
                "semantic Markdown paths must belong to the Slice scope"
            )
        excluded = _normalize_optional_scope_paths(self.excluded_control_paths)
        if excluded != self.excluded_control_paths:
            raise GitTransactionError("excluded control paths must be sorted and unique")
        if any(path in self.scope_paths for path in excluded):
            raise GitTransactionError("a control path cannot also belong to Slice scope")


@dataclass(frozen=True)
class CommitAuthorization:
    slice_id: int
    diff_fingerprint: str
    attestation: ValidationAttestation
    claude_review: ContractResult
    antigravity_review: ContractResult
    findings: tuple[FindingRecord, ...] = ()
    red_state_followup_slice: str | None = None

    def __post_init__(self) -> None:
        if (
            self.red_state_followup_slice is not None
            and not self.red_state_followup_slice.strip()
        ):
            raise GitTransactionError(
                "red-state follow-up slice must be non-empty when provided"
            )


@dataclass(frozen=True)
class SliceCommitResult:
    slice_id: int
    commit_hash: str
    message: str
    diff_fingerprint: str
    committed_paths: tuple[str, ...]


def commit_managed_audit_report(
    *,
    repository_root: Path,
    branch: str,
    audit_path: str,
    excluded_control_paths: Sequence[str] = (),
) -> str:
    """Commit only the deterministic final audit projection, idempotently."""
    identity = _require_expected_feature_branch(repository_root, branch)
    normalized_audit = _normalize_scope_paths((audit_path,))[0]
    changes = collect_repository_changes(
        identity.repository_root,
        identity.head,
        semantic_markdown_paths=(normalized_audit,),
        excluded_paths=_normalize_optional_scope_paths(excluded_control_paths),
    )
    if not changes.entries:
        return identity.head
    transaction_paths = tuple(
        sorted(
            {
                path
                for entry in changes.entries
                for path in (entry.path, entry.old_path)
                if path is not None
            }
        )
    )
    if transaction_paths != (normalized_audit,):
        raise GitTransactionError(
            "final audit commit found foreign paths: " + ", ".join(transaction_paths)
        )
    staged_before = _staged_paths(identity.repository_root, detect_renames=False)
    if staged_before and staged_before != (normalized_audit,):
        raise GitTransactionError(
            "foreign staged paths block final audit commit: " + ", ".join(staged_before)
        )
    index_tree_before = os.fsdecode(
        _git(identity.repository_root, "write-tree").stdout
    ).strip()
    try:
        _git(
            identity.repository_root,
            "add",
            "--chmod=-x",
            "--",
            f":(top,literal){normalized_audit}",
        )
        if _staged_paths(identity.repository_root) != (normalized_audit,):
            raise GitTransactionError("final audit staging did not remain path-exact")
        _git(
            identity.repository_root,
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "docs: finalize orchestrator audit",
        )
    except Exception:
        current_head = os.fsdecode(
            _git(identity.repository_root, "rev-parse", "--verify", "HEAD^{commit}").stdout
        ).strip()
        if current_head == identity.head:
            _git(identity.repository_root, "read-tree", index_tree_before)
        raise
    return os.fsdecode(
        _git(identity.repository_root, "rev-parse", "--verify", "HEAD^{commit}").stdout
    ).strip()


def inspect_repository(repository_root: Path) -> RepositoryIdentity:
    root = Path(repository_root).resolve()
    reported_root = Path(
        os.fsdecode(_git(root, "rev-parse", "--show-toplevel").stdout).strip()
    ).resolve()
    if reported_root != root:
        raise GitTransactionError(
            f"repository root mismatch: expected {root}, git reported {reported_root}"
        )
    branch_result = _git(
        root,
        "symbolic-ref",
        "--quiet",
        "--short",
        "HEAD",
        accepted_exit_codes=(0, 1),
    )
    if branch_result.returncode != 0:
        raise GitTransactionError("detached HEAD is not a valid slice branch")
    branch = os.fsdecode(branch_result.stdout).strip()
    head = os.fsdecode(_git(root, "rev-parse", "--verify", "HEAD^{commit}").stdout).strip()
    upstream_result = _git(
        root,
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
        accepted_exit_codes=(0, 128),
    )
    upstream = (
        os.fsdecode(upstream_result.stdout).strip()
        if upstream_result.returncode == 0
        else None
    )
    ahead: int | None = None
    behind: int | None = None
    if upstream is not None:
        counts = os.fsdecode(
            _git(
                root,
                "rev-list",
                "--left-right",
                "--count",
                f"HEAD...{upstream}",
            ).stdout
        ).split()
        if len(counts) != 2:
            raise GitTransactionError("could not determine local/upstream divergence")
        ahead, behind = (int(value) for value in counts)
    return RepositoryIdentity(root, branch, head, upstream, ahead, behind)


def prepare_new_watch_task_branch(
    repository_root: Path,
    *,
    target_branch: str,
    excluded_control_paths: Sequence[str] = (),
    preserved_task_paths: Sequence[str] = (),
) -> TaskBranchPreparation:
    """Create or activate a target branch for a new Inbox/Watch task.

    This operation is intentionally limited to new tasks. Callers must never use it
    to repair branch drift for a persisted workflow. Switching is refused when the
    current branch has non-ignored working-tree or index changes. Exact untracked
    task artifacts may be carried when ``preserved_task_paths`` names them; no
    files are stashed, cleaned, or otherwise adopted automatically.
    """
    if not FEATURE_BRANCH_PATTERN.fullmatch(target_branch):
        raise GitTransactionError(
            "automatic task branch preparation requires a feature/<name> or "
            "codex/<name> target branch"
        )
    current = inspect_repository(repository_root)
    excluded = _normalize_optional_scope_paths(excluded_control_paths)
    preserved = _normalize_optional_scope_paths(preserved_task_paths)
    if set(excluded).intersection(preserved):
        raise GitTransactionError(
            "task control paths and preserved task paths must be disjoint"
        )
    for path in excluded:
        tracked = _git(
            current.repository_root,
            "ls-files",
            "--error-unmatch",
            "--",
            path,
            accepted_exit_codes=(0, 1),
        )
        if tracked.returncode == 0:
            raise GitTransactionError(
                "automatic target-branch switch requires Inbox task control files "
                f"to be untracked; active branch remains {current.branch!r}; "
                f"tracked control path: {path}"
            )
    for path in preserved:
        tracked = _git(
            current.repository_root,
            "ls-files",
            "--error-unmatch",
            "--",
            path,
            accepted_exit_codes=(0, 1),
        )
        if tracked.returncode == 0:
            raise GitTransactionError(
                "automatic target-branch switch may preserve only untracked task "
                f"artifacts; active branch remains {current.branch!r}; "
                f"tracked task path: {path}"
            )
        candidate = current.repository_root.joinpath(*PurePosixPath(path).parts)
        if candidate.is_symlink() or not candidate.is_file():
            raise GitTransactionError(
                "automatic target-branch switch may preserve only regular untracked "
                f"task files; active branch remains {current.branch!r}; path: {path}"
            )
        untracked = os.fsdecode(
            _git(
                current.repository_root,
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
                path,
            ).stdout
        ).split("\0")
        if path not in untracked:
            raise GitTransactionError(
                "automatic target-branch switch may preserve only visible untracked "
                f"task files; active branch remains {current.branch!r}; path: {path}"
            )
    if current.branch == target_branch:
        return TaskBranchPreparation(current, current.branch, "already-active")

    changes = collect_repository_changes(
        current.repository_root,
        current.head,
        excluded_paths=(*excluded, *preserved),
    )
    if changes.entries:
        raise GitTransactionError(
            "automatic target-branch switch requires a clean non-ignored working "
            "tree; active branch remains "
            f"{current.branch!r}; found: {', '.join(changes.paths)}"
        )

    exists = _git(
        current.repository_root,
        "show-ref",
        "--verify",
        "--quiet",
        f"refs/heads/{target_branch}",
        accepted_exit_codes=(0, 1),
    )
    if exists.returncode == 0:
        _git(current.repository_root, "switch", target_branch)
        action: Literal["created", "switched"] = "switched"
    else:
        _git(current.repository_root, "switch", "-c", target_branch)
        action = "created"

    prepared = inspect_repository(current.repository_root)
    if prepared.branch != target_branch:
        raise GitTransactionError(
            "automatic target-branch preparation did not activate the requested "
            f"branch {target_branch!r}"
        )
    return TaskBranchPreparation(prepared, current.branch, action)


def require_committed_file_at_head(
    repository_root: Path,
    *,
    expected_commit: str,
    relative_path: str,
) -> None:
    """Verify HEAD descends from the approved commit and still contains its plan."""
    identity = inspect_repository(repository_root)
    ancestry = _git(
        identity.repository_root,
        "merge-base",
        "--is-ancestor",
        expected_commit,
        identity.head,
        accepted_exit_codes=(0, 1, 128),
    )
    if ancestry.returncode != 0:
        raise GitTransactionError(
            "approved-plan handoff requires APPROVED_PLAN_COMMIT to be an ancestor of HEAD"
        )
    normalized = _normalize_scope_paths((relative_path,))[0]
    exists = _git(
        identity.repository_root,
        "cat-file",
        "-e",
        f"{expected_commit}:{normalized}",
        accepted_exit_codes=(0, 128),
    )
    if exists.returncode != 0:
        raise GitTransactionError("approved work plan is not present in its commit")
    committed_unchanged = _git(
        identity.repository_root,
        "diff",
        "--quiet",
        expected_commit,
        identity.head,
        "--",
        f":(top,literal){normalized}",
        accepted_exit_codes=(0, 1),
    )
    if committed_unchanged.returncode != 0:
        raise GitTransactionError("approved work plan differs in HEAD")
    staged_unchanged = _git(
        identity.repository_root,
        "diff",
        "--cached",
        "--quiet",
        expected_commit,
        "--",
        f":(top,literal){normalized}",
        accepted_exit_codes=(0, 1),
    )
    if staged_unchanged.returncode != 0:
        raise GitTransactionError("approved work plan differs in the index")
    working_unchanged = _git(
        identity.repository_root,
        "diff",
        "--quiet",
        expected_commit,
        "--",
        f":(top,literal){normalized}",
        accepted_exit_codes=(0, 1),
    )
    if working_unchanged.returncode != 0:
        raise GitTransactionError("approved work plan differs in the working tree")


def begin_slice(
    *,
    repository_root: Path,
    slice_id: int,
    expected_branch: str,
    scope_paths: Sequence[str],
) -> tuple[SliceGitBoundary, RepositoryIdentity]:
    normalized_scope = _normalize_scope_paths(scope_paths)
    identity = _require_expected_feature_branch(repository_root, expected_branch)
    changes = collect_repository_changes(identity.repository_root, identity.head)
    if changes.entries:
        raise GitTransactionError(
            "a new slice requires a clean non-ignored working tree; found: "
            + ", ".join(changes.paths)
        )
    boundary = SliceGitBoundary(
        slice_id=slice_id,
        branch=identity.branch,
        start_commit=identity.head,
        start_fingerprint=changes.fingerprint,
        scope_paths=normalized_scope,
    )
    return boundary, identity


def resume_slice(
    *,
    repository_root: Path,
    boundary: SliceGitBoundary,
    paused_fingerprint: str | None = None,
) -> RepositoryChanges:
    identity = _require_expected_feature_branch(repository_root, boundary.branch)
    if identity.head != boundary.start_commit:
        raise GitTransactionError("slice HEAD changed since its persisted start commit")
    changes = _collect_boundary_changes(identity.repository_root, boundary)
    _require_scope(changes, boundary.scope_paths)
    if not changes.entries and changes.fingerprint != boundary.start_fingerprint:
        raise GitTransactionError("persisted slice start fingerprint does not match repository")
    if paused_fingerprint is not None and changes.fingerprint != paused_fingerprint:
        raise GitTransactionError("repository fingerprint changed since the persisted pause")
    return changes


def commit_slice(
    *,
    repository_root: Path,
    boundary: SliceGitBoundary,
    authorization: CommitAuthorization,
    title: str,
) -> SliceCommitResult:
    if authorization.slice_id != boundary.slice_id:
        raise GitTransactionError("commit authorization belongs to a different slice")
    identity = _require_expected_feature_branch(repository_root, boundary.branch)
    if identity.head != boundary.start_commit:
        raise GitTransactionError("slice HEAD changed after its persisted start")
    changes = _collect_boundary_changes(identity.repository_root, boundary)
    if not changes.entries:
        raise GitTransactionError("slice commit requires at least one changed path")
    staged_before = _staged_paths(identity.repository_root, detect_renames=False)
    foreign_staged = tuple(
        path for path in staged_before if path not in boundary.scope_paths
    )
    if foreign_staged:
        raise GitTransactionError(
            "foreign staged paths block the slice commit: " + ", ".join(foreign_staged)
        )
    transaction_paths = _require_scope(changes, boundary.scope_paths)
    _validate_authorization(authorization, changes.fingerprint)
    normalized_title = title.strip()
    if not normalized_title or "\n" in normalized_title or "\r" in normalized_title:
        raise GitTransactionError("slice commit title must be one non-empty line")
    message = f"Slice {boundary.slice_id:02d}: {normalized_title}"
    index_tree_before = os.fsdecode(
        _git(identity.repository_root, "write-tree").stdout
    ).strip()

    update_paths = tuple(
        sorted(
            {
                entry.old_path if entry.old_path is not None else entry.path
                for entry in changes.entries
                if entry.kind in ("deleted", "renamed")
                and (
                    entry.old_path if entry.old_path is not None else entry.path
                )
                not in staged_before
            }
        )
    )
    content_paths = tuple(
        sorted(entry.path for entry in changes.entries if entry.kind != "deleted")
    )
    try:
        if update_paths:
            _git(
                identity.repository_root,
                "add",
                "-u",
                "--",
                *(f":(top,literal){path}" for path in update_paths),
            )
        if content_paths:
            _git(
                identity.repository_root,
                "add",
                "--",
                *(f":(top,literal){path}" for path in content_paths),
            )
        markdown_paths = tuple(
            path for path in content_paths if path.lower().endswith(".md")
        )
        if markdown_paths:
            _git(
                identity.repository_root,
                "add",
                "--chmod=-x",
                "--",
                *(f":(top,literal){path}" for path in markdown_paths),
            )
        staged_after = _staged_paths(identity.repository_root)
        if staged_after != changes.paths:
            raise GitTransactionError(
                "staged paths do not exactly match the slice transaction: "
                + ", ".join(staged_after)
            )
        final_changes = _collect_boundary_changes(identity.repository_root, boundary)
        if final_changes.fingerprint != changes.fingerprint:
            raise GitTransactionError("slice fingerprint changed during exact staging")

        _git(
            identity.repository_root,
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            message,
        )
    except GitTransactionError:
        current_head = os.fsdecode(
            _git(identity.repository_root, "rev-parse", "--verify", "HEAD^{commit}").stdout
        ).strip()
        if current_head == boundary.start_commit:
            _git(identity.repository_root, "read-tree", index_tree_before)
        raise
    commit_hash = os.fsdecode(
        _git(identity.repository_root, "rev-parse", "--verify", "HEAD^{commit}").stdout
    ).strip()
    committed_paths = tuple(
        sorted(
            field
            for field in os.fsdecode(
                _git(
                    identity.repository_root,
                    "diff-tree",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    "--find-renames",
                    "-z",
                    commit_hash,
                    "--",
                ).stdout
            ).split("\0")
            if field
        )
    )
    if committed_paths != changes.paths:
        raise GitTransactionError("created commit path list differs from the reviewed slice")
    return SliceCommitResult(
        slice_id=boundary.slice_id,
        commit_hash=commit_hash,
        message=message,
        diff_fingerprint=changes.fingerprint,
        committed_paths=committed_paths,
    )


def _validate_authorization(
    authorization: CommitAuthorization,
    current_fingerprint: str,
) -> None:
    if authorization.diff_fingerprint != current_fingerprint:
        raise GitTransactionError("commit authorization fingerprint is stale")
    attestation = authorization.attestation
    validation_authorized = attestation.passed or (
        attestation.complete
        and authorization.red_state_followup_slice is not None
    )
    if not validation_authorized or attestation.diff_fingerprint != current_fingerprint:
        raise GitTransactionError(
            "commit requires a passing current attestation or named complete red-state exception"
        )
    if any(
        finding.status is FindingStatus.OPEN
        and finding.finding_class is FindingClass.BLOCKER
        for finding in authorization.findings
    ):
        raise GitTransactionError("commit requires no globally open blockers")
    for expected_role, result in (
        (AgentRole.CLAUDE, authorization.claude_review),
        (AgentRole.ANTIGRAVITY, authorization.antigravity_review),
    ):
        if result.reviewer is not expected_role:
            raise GitTransactionError(f"commit requires the {expected_role.value} review role")
        if (
            result.stopped
            or result.stop_request is not None
            or result.approval is not True
            or result.own_open_blockers
        ):
            raise GitTransactionError(f"commit requires an approving {expected_role.value} review")
        if (
            not attestation.passed
            and result.red_state_followup_slice
            != authorization.red_state_followup_slice
        ):
            raise GitTransactionError(
                f"{expected_role.value} review is not bound to the named red-state exception"
            )
        if result.validation != attestation:
            raise GitTransactionError(
                f"{expected_role.value} review is not bound to the commit attestation"
            )


def _collect_boundary_changes(
    repository_root: Path,
    boundary: SliceGitBoundary,
) -> RepositoryChanges:
    if boundary.semantic_markdown_paths or boundary.excluded_control_paths:
        return collect_repository_changes(
            repository_root,
            boundary.start_commit,
            semantic_markdown_paths=boundary.semantic_markdown_paths,
            excluded_paths=boundary.excluded_control_paths,
        )
    return collect_repository_changes(repository_root, boundary.start_commit)


def _require_expected_feature_branch(
    repository_root: Path,
    expected_branch: str,
) -> RepositoryIdentity:
    if not FEATURE_BRANCH_PATTERN.fullmatch(expected_branch):
        raise GitTransactionError(
            "expected branch must match feature/<name> or codex/<name>"
        )
    identity = inspect_repository(repository_root)
    if identity.branch != expected_branch:
        raise GitTransactionError(
            f"branch mismatch: expected {expected_branch}, got {identity.branch}"
        )
    return identity


def _require_scope(
    changes: RepositoryChanges,
    scope_paths: tuple[str, ...],
) -> tuple[str, ...]:
    transaction_paths = tuple(
        sorted(
            {
                path
                for entry in changes.entries
                for path in (entry.path, entry.old_path)
                if path is not None
            }
        )
    )
    unexpected = tuple(path for path in transaction_paths if path not in scope_paths)
    if unexpected:
        raise GitTransactionError(
            "paths outside the persisted slice scope: " + ", ".join(unexpected)
        )
    return transaction_paths


def _staged_paths(
    repository_root: Path, *, detect_renames: bool = True
) -> tuple[str, ...]:
    arguments = ["diff", "--cached", "--name-only", "-z"]
    arguments.append("--find-renames" if detect_renames else "--no-renames")
    raw = _git(repository_root, *arguments, "--").stdout
    return tuple(sorted(os.fsdecode(field) for field in raw.split(b"\0") if field))


def _normalize_scope_paths(paths: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(sorted(set(paths)))
    if not normalized:
        raise GitTransactionError("slice scope must contain at least one path")
    for raw in normalized:
        if not isinstance(raw, str) or not raw or "\\" in raw:
            raise GitTransactionError("slice scope paths must be relative POSIX paths")
        path = PurePosixPath(raw)
        if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
            raise GitTransactionError("slice scope paths must be relative POSIX paths")
    return normalized


def _normalize_optional_scope_paths(paths: Sequence[str]) -> tuple[str, ...]:
    if not paths:
        return ()
    return _normalize_scope_paths(paths)


def _git(
    repository_root: Path,
    *arguments: str,
    accepted_exit_codes: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=repository_root,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise GitTransactionError(f"could not execute git: {exc}") from exc
    if result.returncode not in accepted_exit_codes:
        detail = os.fsdecode(result.stderr or result.stdout).strip() or "no diagnostic output"
        raise GitTransactionError(
            f"git {' '.join(arguments)} failed with exit code {result.returncode}: {detail}"
        )
    return result
