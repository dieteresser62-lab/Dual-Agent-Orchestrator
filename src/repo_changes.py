from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence

from audit_trail import AuditTrailError, strip_managed_audit_sections
from path_policy import PathPolicyError, resolve_path_within_roots


UNTRACKED_PREVIEW_LIMIT = 256_000
_SLICE_AUDIT_MARKDOWN_PATTERN = re.compile(
    r"^slice-[a-z0-9]+(?:-[a-z0-9]+)*-\d{2}-"
    r"[a-z0-9]+(?:-[a-z0-9]+)*\.md$"
)


class RepositoryChangeError(RuntimeError):
    """Raised when the canonical repository change set cannot be determined."""


class NotGitRepositoryError(RepositoryChangeError):
    """Raised when a path has no discoverable Git worktree metadata."""


@dataclass(frozen=True)
class MergeBase:
    base_ref: str
    commit: str


@dataclass(frozen=True)
class ChangedPath:
    path: str
    kind: str
    raw_status: str
    old_path: str | None = None
    tracked: bool = True
    working_tree: bool = False


@dataclass(frozen=True)
class RepositoryChanges:
    repository_root: Path
    merge_base: str
    entries: tuple[ChangedPath, ...]
    diff_text: str
    fingerprint: str

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(entry.path for entry in self.entries)

    @property
    def review_paths(self) -> tuple[str, ...]:
        current = [entry.path for entry in self.entries if entry.working_tree]
        committed = [entry.path for entry in self.entries if not entry.working_tree]
        return tuple([*current, *committed])

    def render_snapshot(self, max_diff_chars: int) -> str:
        status_lines = [
            _format_status(entry) for entry in self.entries
        ] or ["(empty)"]
        diff = self.diff_text.strip() or "(empty)"
        truncated = False
        if max_diff_chars <= 0:
            diff = "...[diff omitted]"
            truncated = self.diff_text.strip() != ""
        elif len(diff) > max_diff_chars:
            diff = diff[:max_diff_chars]
            truncated = True
        if truncated:
            diff = f"{diff}\n...[truncated]"
        return "\n".join(
            [
                f"=== canonical changes since {self.merge_base} ===",
                *status_lines,
                "",
                "=== diff fingerprint ===",
                self.fingerprint,
                "",
                "=== branch diff (possibly truncated) ===",
                diff,
            ]
        ).strip()


@dataclass(frozen=True)
class _UntrackedPayload:
    content_type: str
    size: int
    digest: str
    fingerprint_size: int
    fingerprint_digest: str
    preview: bytes
    truncated: bool
    mode: int


def _display_path(path: str) -> str:
    return path.encode("unicode_escape", errors="backslashreplace").decode("ascii")


def _format_status(entry: ChangedPath) -> str:
    scope = "working" if entry.working_tree else "committed"
    if entry.old_path is not None:
        return (
            f"[{scope}] {entry.raw_status} {_display_path(entry.old_path)} -> "
            f"{_display_path(entry.path)}"
        )
    return f"[{scope}] {entry.raw_status} {_display_path(entry.path)}"


def _git(
    repository_root: Path,
    arguments: Sequence[str],
    *,
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
        raise RepositoryChangeError(f"could not execute git: {exc}") from exc
    if result.returncode not in accepted_exit_codes:
        detail = os.fsdecode(result.stderr or result.stdout).strip() or "no diagnostic output"
        command = "git " + " ".join(arguments)
        raise RepositoryChangeError(
            f"{command} failed with exit code {result.returncode}: {detail}"
        )
    return result


def _validated_repository_root(repository_root: Path) -> Path:
    root = repository_root.resolve()
    try:
        result = _git(root, ("rev-parse", "--show-toplevel"))
    except RepositoryChangeError as exc:
        git_metadata = root / ".git"
        if (
            not git_metadata.exists()
            and not git_metadata.is_symlink()
            and exc.__cause__ is None
        ):
            raise NotGitRepositoryError(
                f"path is not inside a Git worktree: {root}"
            ) from exc
        raise
    reported_root = Path(os.fsdecode(result.stdout).strip()).resolve()
    if reported_root != root:
        raise RepositoryChangeError(
            f"repository root mismatch: expected {root}, git reported {reported_root}"
        )
    return root


def _candidate_base_refs(repository_root: Path) -> tuple[str, ...]:
    candidates: list[str] = []
    remote_head = _git(
        repository_root,
        ("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"),
        accepted_exit_codes=(0, 1),
    )
    if remote_head.returncode == 0:
        value = os.fsdecode(remote_head.stdout).strip()
        if value:
            candidates.append(value)
    candidates.extend(("main", "master"))
    return tuple(dict.fromkeys(candidates))


def resolve_merge_base(repository_root: Path, base_ref: str | None = None) -> MergeBase:
    """Resolve the actual branch merge-base without hard-coding main or master."""
    root = _validated_repository_root(repository_root)
    candidates = (base_ref,) if base_ref else _candidate_base_refs(root)
    failures: list[str] = []
    for candidate in candidates:
        result = _git(
            root,
            ("merge-base", "--", "HEAD", candidate),
            accepted_exit_codes=(0, 1, 128),
        )
        if result.returncode == 0:
            commit = os.fsdecode(result.stdout).strip()
            if commit:
                verified = _git(
                    root,
                    ("rev-parse", "--verify", "--end-of-options", f"{commit}^{{commit}}"),
                )
                return MergeBase(candidate, os.fsdecode(verified.stdout).strip())
        detail = os.fsdecode(result.stderr or result.stdout).strip()
        failures.append(f"{candidate}: {detail or f'exit {result.returncode}'}")
    attempted = "; ".join(failures) or "no base refs available"
    raise RepositoryChangeError(f"could not resolve merge-base ({attempted})")


def _kind_for_status(raw_status: str) -> str:
    return {
        "A": "added",
        "M": "modified",
        "D": "deleted",
        "R": "renamed",
        "C": "copied",
        "T": "type_changed",
        "U": "unmerged",
    }.get(raw_status[:1], "unknown")


def _parse_name_status(raw: bytes) -> list[ChangedPath]:
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    entries: list[ChangedPath] = []
    index = 0
    while index < len(fields):
        raw_status = os.fsdecode(fields[index])
        index += 1
        if not raw_status or index >= len(fields):
            raise RepositoryChangeError("git returned malformed --name-status -z output")
        if raw_status.startswith(("R", "C")):
            if index + 1 >= len(fields):
                raise RepositoryChangeError("git returned incomplete rename/copy metadata")
            old_path = os.fsdecode(fields[index])
            new_path = os.fsdecode(fields[index + 1])
            index += 2
            entries.append(
                ChangedPath(
                    path=new_path,
                    old_path=old_path,
                    kind=_kind_for_status(raw_status),
                    raw_status=raw_status,
                )
            )
        else:
            path = os.fsdecode(fields[index])
            index += 1
            entries.append(
                ChangedPath(
                    path=path,
                    kind=_kind_for_status(raw_status),
                    raw_status=raw_status,
                )
            )
    return entries


def _safe_untracked_candidate(repository_root: Path, relative_path: str) -> Path:
    parsed = PurePosixPath(relative_path)
    if parsed.is_absolute() or ".." in parsed.parts or not parsed.parts:
        raise RepositoryChangeError(f"git returned unsafe untracked path: {relative_path!r}")
    candidate = repository_root.joinpath(*parsed.parts)
    try:
        resolve_path_within_roots(candidate.parent, (repository_root,))
    except PathPolicyError as exc:
        raise RepositoryChangeError(
            f"untracked path parent escapes repository root: {relative_path!r}"
        ) from exc
    return candidate


def _read_path_payload(repository_root: Path, relative_path: str) -> _UntrackedPayload:
    candidate = _safe_untracked_candidate(repository_root, relative_path)
    try:
        metadata = candidate.lstat()
    except OSError as exc:
        raise RepositoryChangeError(
            f"could not inspect untracked path {relative_path!r}: {exc}"
        ) from exc

    if stat.S_ISLNK(metadata.st_mode):
        target = os.fsencode(os.readlink(candidate))
        return _UntrackedPayload(
            content_type="symlink",
            size=len(target),
            digest=hashlib.sha256(target).hexdigest(),
            fingerprint_size=len(target),
            fingerprint_digest=hashlib.sha256(target).hexdigest(),
            preview=target,
            truncated=False,
            mode=0o120000,
        )
    if not stat.S_ISREG(metadata.st_mode):
        marker = f"special:{stat.S_IFMT(metadata.st_mode):o}:{metadata.st_size}".encode()
        return _UntrackedPayload(
            content_type="special",
            size=metadata.st_size,
            digest=hashlib.sha256(marker).hexdigest(),
            fingerprint_size=metadata.st_size,
            fingerprint_digest=hashlib.sha256(marker).hexdigest(),
            preview=b"",
            truncated=False,
            mode=stat.S_IFMT(metadata.st_mode),
        )

    digest = hashlib.sha256()
    preview = bytearray()
    markdown_content = bytearray() if _uses_semantic_markdown_digest(relative_path) else None
    opened_size = metadata.st_size
    normalized_mode = 0o100644
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
        try:
            opened_metadata = os.fstat(descriptor)
            if not stat.S_ISREG(opened_metadata.st_mode):
                raise RepositoryChangeError(
                    f"untracked path changed type while reading: {relative_path!r}"
                )
            opened_size = opened_metadata.st_size
            if opened_metadata.st_mode & 0o111:
                normalized_mode = 0o100755
            while True:
                block = os.read(descriptor, 64 * 1024)
                if not block:
                    break
                digest.update(block)
                if markdown_content is not None:
                    markdown_content.extend(block)
                if len(preview) < UNTRACKED_PREVIEW_LIMIT:
                    remaining = UNTRACKED_PREVIEW_LIMIT - len(preview)
                    preview.extend(block[:remaining])
        finally:
            os.close(descriptor)
    except RepositoryChangeError:
        raise
    except OSError as exc:
        raise RepositoryChangeError(
            f"could not read untracked path {relative_path!r}: {exc}"
        ) from exc
    raw_digest = digest.hexdigest()
    fingerprint_size = opened_size
    fingerprint_digest = raw_digest
    if markdown_content is not None:
        try:
            semantic = strip_managed_audit_sections(
                bytes(markdown_content).decode("utf-8")
            ).encode("utf-8")
        except (UnicodeError, AuditTrailError) as exc:
            raise RepositoryChangeError(
                f"could not canonicalize managed audit sections in {relative_path!r}: {exc}"
            ) from exc
        fingerprint_size = len(semantic)
        fingerprint_digest = hashlib.sha256(semantic).hexdigest()
    return _UntrackedPayload(
        content_type="regular",
        size=opened_size,
        digest=raw_digest,
        fingerprint_size=fingerprint_size,
        fingerprint_digest=fingerprint_digest,
        preview=bytes(preview),
        truncated=opened_size > len(preview),
        mode=normalized_mode,
    )


def _uses_semantic_markdown_digest(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    if len(path.parts) != 3 or path.parts[:2] != ("docs", "internal"):
        return False
    return (
        path.name == "orchestrator-modernization-work-plan.md"
        or _SLICE_AUDIT_MARKDOWN_PATTERN.fullmatch(path.name) is not None
    )


def _render_untracked_diff(path: str, payload: _UntrackedPayload) -> str:
    display = _display_path(path)
    executable = bool(payload.mode & 0o111)
    file_mode = "100755" if executable else "100644"
    if payload.content_type == "symlink":
        file_mode = "120000"
    header = [
        f"diff --git a/{display} b/{display}",
        f"new file mode {file_mode}",
        "--- /dev/null",
        f"+++ b/{display}",
    ]
    if payload.content_type == "special":
        return "\n".join(
            [*header, f"[untracked special file; sha256={payload.digest}]"]
        )
    try:
        text = payload.preview.decode("utf-8")
        if "\x00" in text:
            raise UnicodeDecodeError("utf-8", payload.preview, 0, 1, "NUL byte")
    except UnicodeDecodeError:
        return "\n".join(
            [
                *header,
                f"Binary file; size={payload.size}; sha256={payload.digest}",
            ]
        )
    lines = text.splitlines()
    rendered = [*header, f"@@ -0,0 +1,{len(lines)} @@"]
    rendered.extend(f"+{line}" for line in lines)
    if payload.truncated:
        rendered.append(
            f"+...[untracked preview truncated; size={payload.size}; sha256={payload.digest}]"
        )
    return "\n".join(rendered)


def collect_repository_changes(
    repository_root: Path,
    merge_base: str,
) -> RepositoryChanges:
    """Collect all tracked and non-ignored untracked changes since an explicit merge-base."""
    root = _validated_repository_root(repository_root)
    verified = _git(
        root,
        ("rev-parse", "--verify", "--end-of-options", f"{merge_base}^{{commit}}"),
    )
    canonical_merge_base = os.fsdecode(verified.stdout).strip()
    name_status = _git(
        root,
        ("diff", "--name-status", "-z", "--find-renames", canonical_merge_base, "--"),
    ).stdout
    tracked_diff = _git(
        root,
        ("diff", "--binary", "--full-index", "--find-renames", canonical_merge_base, "--"),
    ).stdout
    untracked_raw = _git(
        root,
        ("ls-files", "--others", "--exclude-standard", "-z", "--"),
    ).stdout
    working_tree_raw = _git(
        root,
        ("diff", "--name-only", "-z", "--find-renames", "HEAD", "--"),
    ).stdout

    entries = _parse_name_status(name_status)
    untracked_paths = sorted(
        os.fsdecode(field) for field in untracked_raw.split(b"\0") if field
    )
    existing_paths = {entry.path for entry in entries}
    payloads: dict[str, _UntrackedPayload] = {}
    for path in untracked_paths:
        if path in existing_paths:
            continue
        entries.append(
            ChangedPath(
                path=path,
                kind="untracked",
                raw_status="??",
                tracked=False,
                working_tree=True,
            )
        )
    working_tree_paths = {
        os.fsdecode(field) for field in working_tree_raw.split(b"\0") if field
    }
    working_tree_paths.update(untracked_paths)
    entries = [
        replace(entry, working_tree=entry.path in working_tree_paths)
        for entry in entries
    ]

    entries.sort(key=lambda item: (item.path, item.old_path or "", item.raw_status))
    for entry in entries:
        if entry.kind != "deleted":
            payloads[entry.path] = _read_path_payload(root, entry.path)
    fingerprint_payload = {
        "version": 1,
        "merge_base": canonical_merge_base,
        "entries": [
            {
                "path": entry.path,
                "old_path": entry.old_path,
                "kind": "added" if entry.kind == "untracked" else entry.kind,
                "payload": (
                    {
                        "type": payloads[entry.path].content_type,
                        "size": payloads[entry.path].fingerprint_size,
                        "sha256": payloads[entry.path].fingerprint_digest,
                        "mode": payloads[entry.path].mode,
                    }
                    if entry.path in payloads
                    else None
                ),
            }
            for entry in entries
        ],
    }
    serialized = json.dumps(
        fingerprint_payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    fingerprint = hashlib.sha256(serialized).hexdigest()

    diff_parts: list[str] = []
    if tracked_diff.strip():
        diff_parts.append(tracked_diff.decode("utf-8", errors="replace").rstrip())
    diff_parts.extend(
        _render_untracked_diff(entry.path, payloads[entry.path])
        for entry in entries
        if not entry.tracked and entry.path in payloads
    )
    return RepositoryChanges(
        repository_root=root,
        merge_base=canonical_merge_base,
        entries=tuple(entries),
        diff_text="\n\n".join(part for part in diff_parts if part),
        fingerprint=fingerprint,
    )


def merge_reported_paths(
    changes: RepositoryChanges,
    reported_paths: Iterable[str],
) -> list[str]:
    """Append agent-reported paths without allowing them to remove Git-derived paths."""
    merged = list(changes.review_paths)
    seen = set(merged)
    for raw_path in reported_paths:
        path = str(raw_path).strip()
        if path and path not in seen:
            merged.append(path)
            seen.add(path)
    return merged
