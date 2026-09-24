from __future__ import annotations

import codecs
import difflib
import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping, Sequence

from semantic_markdown import SemanticMarkdownError, canonical_semantic_markdown
from path_policy import PathPolicyError, resolve_path_within_roots
from review_packets import BinaryFileMetadata, exclude_review_diff_paths


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
class ChangeFingerprintEntry:
    path: str
    old_path: str | None
    kind: str
    payload_type: str | None
    payload_size: int | None
    payload_digest: str | None
    payload_mode: int | None

    def to_payload(self) -> dict[str, object]:
        payload = None
        if self.payload_type is not None:
            payload = {
                "type": self.payload_type,
                "size": self.payload_size,
                "sha256": self.payload_digest,
                "mode": self.payload_mode,
            }
        return {
            "path": self.path,
            "old_path": self.old_path,
            "kind": self.kind,
            "payload": payload,
        }


class ReviewDiff(str):
    """A diff string carrying independently measured binary file metadata."""

    def __new__(
        cls, value: str, binary_metadata: tuple[BinaryFileMetadata, ...]
    ) -> ReviewDiff:
        instance = super().__new__(cls, value)
        instance.binary_metadata = binary_metadata
        return instance


@dataclass(frozen=True)
class RepositoryChanges:
    repository_root: Path
    merge_base: str
    entries: tuple[ChangedPath, ...]
    diff_text: str
    fingerprint: str
    fingerprint_entries: tuple[ChangeFingerprintEntry, ...] = ()
    binary_metadata: tuple[BinaryFileMetadata, ...] = ()

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(entry.path for entry in self.entries)

    @property
    def review_paths(self) -> tuple[str, ...]:
        current = [entry.path for entry in self.entries if entry.working_tree]
        committed = [entry.path for entry in self.entries if not entry.working_tree]
        return tuple([*current, *committed])

    @property
    def change_groups(self) -> tuple[tuple[str, ...], ...]:
        """Return one canonical path group per change, retaining both rename sides."""
        return tuple(
            tuple(
                sorted(
                    path
                    for path in (entry.old_path, entry.path)
                    if path is not None
                )
            )
            for entry in self.entries
        )

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
class RepositorySnapshotProbe:
    """Cheap, content-addressed identity used to validate derived evidence caches."""

    repository_root: Path
    merge_base: str
    head_commit: str
    index_fingerprint: str
    entries: tuple[ChangedPath, ...]
    fingerprint: str
    fingerprint_entries: tuple[ChangeFingerprintEntry, ...]

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(entry.path for entry in self.entries)

    @property
    def review_paths(self) -> tuple[str, ...]:
        current = [entry.path for entry in self.entries if entry.working_tree]
        committed = [entry.path for entry in self.entries if not entry.working_tree]
        return tuple([*current, *committed])

    @property
    def identity_digest(self) -> str:
        payload = {
            "merge_base": self.merge_base,
            "head_commit": self.head_commit,
            "index_fingerprint": self.index_fingerprint,
            "fingerprint": self.fingerprint,
            "entries": [entry.to_payload() for entry in self.fingerprint_entries],
        }
        serialized = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()


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
    is_binary: bool = False


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


def _read_path_payload(
    repository_root: Path,
    relative_path: str,
    *,
    semantic_markdown_paths: frozenset[str] = frozenset(),
    raw_fingerprint_paths: frozenset[str] = frozenset(),
) -> _UntrackedPayload:
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
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    is_binary = False
    preview = bytearray()
    markdown_content = (
        bytearray()
        if relative_path not in raw_fingerprint_paths
        and (
            relative_path in semantic_markdown_paths
            or _uses_semantic_markdown_digest(relative_path)
        )
        else None
    )
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
            if opened_metadata.st_mode & 0o111 and not relative_path.lower().endswith(".md"):
                normalized_mode = 0o100755
            while True:
                block = os.read(descriptor, 64 * 1024)
                if not block:
                    break
                digest.update(block)
                if not is_binary:
                    if b"\x00" in block:
                        is_binary = True
                    else:
                        try:
                            decoder.decode(block)
                        except UnicodeDecodeError:
                            is_binary = True
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
    if not is_binary:
        try:
            decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            is_binary = True
    fingerprint_size = opened_size
    fingerprint_digest = raw_digest
    if markdown_content is not None:
        try:
            decoded = bytes(markdown_content).decode("utf-8")
            semantic = canonical_semantic_markdown(
                decoded,
                path=relative_path,
                remove_appendix=relative_path in semantic_markdown_paths,
            ).encode("utf-8")
        except (UnicodeError, SemanticMarkdownError) as exc:
            raise RepositoryChangeError(
                f"could not canonicalize managed audit sections in {relative_path!r}: {exc}"
            ) from exc
        fingerprint_size = len(semantic)
        fingerprint_digest = hashlib.sha256(semantic).hexdigest()
        preview = bytearray(semantic)
        opened_size_for_preview = len(semantic)
    else:
        opened_size_for_preview = opened_size
    return _UntrackedPayload(
        content_type="regular",
        size=opened_size,
        digest=raw_digest,
        fingerprint_size=fingerprint_size,
        fingerprint_digest=fingerprint_digest,
        preview=bytes(preview),
        truncated=opened_size_for_preview > len(preview),
        mode=normalized_mode,
        is_binary=is_binary,
    )


def _uses_semantic_markdown_digest(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    if len(path.parts) != 3 or path.parts[:2] != ("docs", "internal"):
        return False
    return (
        path.name == "orchestrator-modernization-work-plan.md"
        or _SLICE_AUDIT_MARKDOWN_PATTERN.fullmatch(path.name) is not None
    )


def _render_semantic_tracked_diff(
    repository_root: Path,
    merge_base: str,
    entry: ChangedPath,
    *,
    remove_appendix: bool,
) -> str:
    if entry.old_path is not None and entry.old_path != entry.path:
        raise RepositoryChangeError(
            f"managed Markdown rename is unsupported: {entry.old_path!r} -> {entry.path!r}"
        )
    if entry.kind == "added":
        old_text = ""
    else:
        raw_old = _git(repository_root, ("show", f"{merge_base}:{entry.path}")).stdout
        try:
            old_text = canonical_semantic_markdown(
                raw_old.decode("utf-8"),
                path=entry.path,
                remove_appendix=remove_appendix,
            )
        except (UnicodeError, SemanticMarkdownError) as exc:
            raise RepositoryChangeError(
                f"could not canonicalize base Markdown {entry.path!r}: {exc}"
            ) from exc
    if entry.kind == "deleted":
        new_text = ""
    else:
        try:
            new_text = canonical_semantic_markdown(
                (repository_root / PurePosixPath(entry.path)).read_text(encoding="utf-8"),
                path=entry.path,
                remove_appendix=remove_appendix,
            )
        except (OSError, UnicodeError, SemanticMarkdownError) as exc:
            raise RepositoryChangeError(
                f"could not canonicalize working Markdown {entry.path!r}: {exc}"
            ) from exc
    display = _display_path(entry.path)
    old_label = "/dev/null" if entry.kind == "added" else f"a/{display}"
    new_label = "/dev/null" if entry.kind == "deleted" else f"b/{display}"
    body = "".join(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=old_label,
            tofile=new_label,
            n=3,
        )
    ).rstrip("\n")
    if not body:
        return ""
    metadata = (
        "new file mode 100644\n"
        if entry.kind == "added"
        else "deleted file mode 100644\n"
        if entry.kind == "deleted"
        else ""
    )
    return f"diff --git a/{display} b/{display}\n{metadata}{body}"


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
    if _is_binary_payload(payload):
        return "\n".join(
            [
                *header,
                f"Binary file; size={payload.size}; sha256={payload.digest}",
            ]
        )
    text = payload.preview.decode("utf-8")
    lines = text.splitlines()
    rendered = [*header, f"@@ -0,0 +1,{len(lines)} @@"]
    rendered.extend(f"+{line}" for line in lines)
    if payload.truncated:
        rendered.append(
            f"+...[untracked preview truncated; size={payload.size}; sha256={payload.digest}]"
        )
    return "\n".join(rendered)


def _is_binary_payload(payload: _UntrackedPayload) -> bool:
    return payload.content_type == "regular" and payload.is_binary


def _is_binary_bytes(content: bytes) -> bool:
    if b"\x00" in content:
        return True
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _binary_metadata(
    root: Path, merge_base: str, entries: list[ChangedPath],
    payloads: dict[str, _UntrackedPayload], tracked_diff: bytes,
) -> tuple[BinaryFileMetadata, ...]:
    binary_paths: set[str] = set()
    tracked_lines = tracked_diff.splitlines()
    starts = [index for index, line in enumerate(tracked_lines) if line.startswith(b"diff --git ")]
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(tracked_lines)
        lines = tracked_lines[start:end]
        if not any(
            line == b"GIT binary patch" or line.startswith(b"Binary files ")
            for line in lines
        ):
            continue
        header = os.fsdecode(lines[0][len(b"diff --git "):])
        for entry in entries:
            if entry.tracked and header == f"a/{_display_path(entry.path)} b/{_display_path(entry.path)}":
                binary_paths.add(entry.path)
                break
        else:
            raise RepositoryChangeError("Git binary diff path is not a known change")
    binary_paths.update(
        entry.path for entry in entries
        if not entry.tracked and _is_binary_payload(payloads[entry.path])
    )
    binary_paths.update(
        entry.path for entry in entries
        if entry.tracked and entry.kind != "deleted" and _is_binary_payload(payloads[entry.path])
    )
    old_binary: dict[str, bytes] = {}
    for entry in entries:
        if not entry.tracked or entry.kind == "added":
            continue
        old = _git(root, ("show", f"{merge_base}:{entry.old_path or entry.path}")).stdout
        if _is_binary_bytes(old):
            old_binary[entry.path] = old
            binary_paths.add(entry.path)
    result: list[BinaryFileMetadata] = []
    for entry in entries:
        if entry.path not in binary_paths:
            continue
        old_size: int | None = None
        old_digest: str | None = None
        if entry.tracked and entry.kind != "added":
            tree = _git(root, ("ls-tree", "-z", merge_base, "--", entry.path)).stdout
            records = [part for part in tree.split(b"\0") if part]
            if len(records) != 1 or b"\t" not in records[0]:
                raise RepositoryChangeError(f"binary old path is not a regular Git file: {entry.path}")
            descriptor, tree_path = records[0].split(b"\t", 1)
            if tree_path != os.fsencode(entry.path) or not descriptor.startswith(
                (b"100644 blob ", b"100755 blob ")
            ):
                raise RepositoryChangeError(f"binary old path is not a regular Git file: {entry.path}")
            old = old_binary.get(entry.path)
            if old is None:
                old = _git(root, ("show", f"{merge_base}:{entry.path}")).stdout
            old_size, old_digest = len(old), hashlib.sha256(old).hexdigest()
        new_size: int | None = None
        new_digest: str | None = None
        if entry.kind != "deleted":
            payload = payloads[entry.path]
            if payload.content_type != "regular":
                raise RepositoryChangeError(f"binary new path is not a regular file: {entry.path}")
            new_size, new_digest = payload.size, payload.digest
        result.append(BinaryFileMetadata(
            entry.path, "added" if entry.kind == "untracked" else entry.kind,
            old_size, old_digest, new_size, new_digest,
        ))
    return tuple(result)


def collect_repository_changes(
    repository_root: Path,
    merge_base: str,
    *,
    semantic_markdown_paths: Iterable[str] = (),
    raw_fingerprint_paths: Iterable[str] = (),
    excluded_paths: Iterable[str] = (),
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
    untracked_raw = _git(
        root,
        ("ls-files", "--others", "--exclude-standard", "-z", "--"),
    ).stdout
    working_tree_raw = _git(
        root,
        ("diff", "--name-only", "-z", "--find-renames", "HEAD", "--"),
    ).stdout

    semantic_paths = frozenset(
        _normalize_selected_path(path) for path in semantic_markdown_paths
    )
    raw_paths = frozenset(
        _normalize_selected_path(path) for path in raw_fingerprint_paths
    )
    exclusions = frozenset(_normalize_selected_path(path) for path in excluded_paths)
    entries = [
        entry
        for entry in _parse_name_status(name_status)
        if entry.path not in exclusions and entry.old_path not in exclusions
    ]
    untracked_paths = sorted(
        path
        for field in untracked_raw.split(b"\0")
        if field and (path := os.fsdecode(field)) not in exclusions
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

    tracked_paths = tuple(
        sorted(
            {
                path
                for entry in entries
                if entry.tracked
                for path in (entry.path, entry.old_path)
                if path is not None
            }
        )
    )
    semantic_tracked_paths = {
        entry.path
        for entry in entries
        if entry.tracked
        and entry.path not in raw_paths
        and (
            entry.path in semantic_paths
            or _uses_semantic_markdown_digest(entry.path)
        )
    }
    raw_tracked_paths = tuple(
        path for path in tracked_paths if path not in semantic_tracked_paths
    )
    tracked_diff = (
        _git(
            root,
            (
                "diff",
                "--binary",
                "--full-index",
                "--find-renames",
                canonical_merge_base,
                "--",
                *(f":(top,literal){path}" for path in raw_tracked_paths),
            ),
        ).stdout
        if raw_tracked_paths
        else b""
    )

    entries.sort(key=lambda item: (item.path, item.old_path or "", item.raw_status))
    for entry in entries:
        if entry.kind != "deleted":
            payloads[entry.path] = _read_path_payload(
                root,
                entry.path,
                semantic_markdown_paths=semantic_paths,
                raw_fingerprint_paths=raw_paths,
            )
    fingerprint_entries = tuple(
        _change_fingerprint_entry(entry, payloads) for entry in entries
    )
    fingerprint_payload = {
        "version": 1,
        "merge_base": canonical_merge_base,
        "entries": [entry.to_payload() for entry in fingerprint_entries],
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
        _render_semantic_tracked_diff(
            root,
            canonical_merge_base,
            entry,
            remove_appendix=entry.path in semantic_paths,
        )
        for entry in entries
        if entry.tracked and entry.path in semantic_tracked_paths
    )
    diff_parts.extend(
        _render_untracked_diff(entry.path, payloads[entry.path])
        for entry in entries
        if not entry.tracked and entry.path in payloads
    )
    binary_metadata = _binary_metadata(
        root, canonical_merge_base, entries, payloads, tracked_diff
    )
    return RepositoryChanges(
        repository_root=root,
        merge_base=canonical_merge_base,
        entries=tuple(entries),
        diff_text=ReviewDiff(
            "\n\n".join(part for part in diff_parts if part), binary_metadata
        ),
        fingerprint=fingerprint,
        fingerprint_entries=fingerprint_entries,
        binary_metadata=binary_metadata,
    )


def probe_repository_snapshot(
    repository_root: Path,
    merge_base: str,
    *,
    semantic_markdown_paths: Iterable[str] = (),
    excluded_paths: Iterable[str] = (),
) -> RepositorySnapshotProbe:
    """Fingerprint the live repository without constructing its potentially huge diff.

    The probe deliberately repeats the canonical path and payload rules used by
    :func:`collect_repository_changes`.  It additionally binds HEAD and the staged
    index, including an index-only change hidden by the working tree.  Managed
    Markdown is canonicalized before hashing so projection-only audit rewrites do
    not manufacture a new semantic snapshot.
    """

    root = _validated_repository_root(repository_root)
    verified = _git(
        root,
        ("rev-parse", "--verify", "--end-of-options", f"{merge_base}^{{commit}}"),
    )
    canonical_merge_base = os.fsdecode(verified.stdout).strip()
    head_commit = os.fsdecode(
        _git(root, ("rev-parse", "--verify", "HEAD^{commit}")).stdout
    ).strip()
    semantic_paths = frozenset(
        _normalize_selected_path(path) for path in semantic_markdown_paths
    )
    exclusions = frozenset(_normalize_selected_path(path) for path in excluded_paths)
    entries = [
        entry
        for entry in _parse_name_status(
            _git(
                root,
                (
                    "diff",
                    "--name-status",
                    "-z",
                    "--find-renames",
                    canonical_merge_base,
                    "--",
                ),
            ).stdout
        )
        if entry.path not in exclusions and entry.old_path not in exclusions
    ]
    untracked_paths = sorted(
        path
        for field in _git(
            root, ("ls-files", "--others", "--exclude-standard", "-z", "--")
        ).stdout.split(b"\0")
        if field and (path := os.fsdecode(field)) not in exclusions
    )
    existing_paths = {entry.path for entry in entries}
    for path in untracked_paths:
        if path not in existing_paths:
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
        os.fsdecode(field)
        for field in _git(
            root,
            ("diff", "--name-only", "-z", "--find-renames", "HEAD", "--"),
        ).stdout.split(b"\0")
        if field
    }
    working_tree_paths.update(untracked_paths)
    entries = [
        replace(entry, working_tree=entry.path in working_tree_paths)
        for entry in entries
    ]
    entries.sort(key=lambda item: (item.path, item.old_path or "", item.raw_status))
    payloads = {
        entry.path: _read_path_payload(
            root,
            entry.path,
            semantic_markdown_paths=semantic_paths,
        )
        for entry in entries
        if entry.kind != "deleted"
    }
    fingerprint_entries = tuple(
        _change_fingerprint_entry(entry, payloads) for entry in entries
    )
    fingerprint_payload = {
        "version": 1,
        "merge_base": canonical_merge_base,
        "entries": [entry.to_payload() for entry in fingerprint_entries],
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    index_fingerprint = _index_fingerprint(
        root,
        head_commit=head_commit,
        semantic_paths=semantic_paths,
        exclusions=exclusions,
    )
    return RepositorySnapshotProbe(
        repository_root=root,
        merge_base=canonical_merge_base,
        head_commit=head_commit,
        index_fingerprint=index_fingerprint,
        entries=tuple(entries),
        fingerprint=fingerprint,
        fingerprint_entries=fingerprint_entries,
    )


def _index_fingerprint(
    repository_root: Path,
    *,
    head_commit: str,
    semantic_paths: frozenset[str],
    exclusions: frozenset[str],
) -> str:
    raw = _git(
        repository_root,
        ("diff", "--cached", "--name-status", "-z", "--find-renames", "HEAD", "--"),
    ).stdout
    entries = [
        entry
        for entry in _parse_name_status(raw)
        if entry.path not in exclusions and entry.old_path not in exclusions
    ]
    entries.sort(key=lambda item: (item.path, item.old_path or "", item.raw_status))
    payload_entries: list[dict[str, object]] = []
    for entry in entries:
        payload: dict[str, object] | None = None
        if entry.kind != "deleted":
            stage_lines = [
                field
                for field in _git(
                    repository_root,
                    ("ls-files", "--stage", "-z", "--", entry.path),
                ).stdout.split(b"\0")
                if field
            ]
            if len(stage_lines) != 1 or b"\t" not in stage_lines[0]:
                raise RepositoryChangeError(
                    f"could not resolve one stage-zero index entry for {entry.path!r}"
                )
            metadata, recorded_path = stage_lines[0].split(b"\t", 1)
            fields = metadata.split()
            if len(fields) != 3 or fields[2] != b"0" or os.fsdecode(recorded_path) != entry.path:
                raise RepositoryChangeError(
                    f"index entry for {entry.path!r} is unmerged or malformed"
                )
            mode = os.fsdecode(fields[0])
            if entry.path in semantic_paths or _uses_semantic_markdown_digest(entry.path):
                staged = _git(repository_root, ("show", f":{entry.path}")).stdout
                try:
                    canonical = canonical_semantic_markdown(
                        staged.decode("utf-8"),
                        path=entry.path,
                        remove_appendix=entry.path in semantic_paths,
                    ).encode("utf-8")
                except (UnicodeError, SemanticMarkdownError) as exc:
                    raise RepositoryChangeError(
                        f"could not canonicalize staged Markdown {entry.path!r}: {exc}"
                    ) from exc
                payload = {
                    "mode": mode,
                    "type": "semantic-markdown",
                    "size": len(canonical),
                    "sha256": hashlib.sha256(canonical).hexdigest(),
                }
            else:
                payload = {
                    "mode": mode,
                    "type": "git-blob",
                    "sha256": os.fsdecode(fields[1]),
                }
        payload_entries.append(
            {
                "path": entry.path,
                "old_path": entry.old_path,
                "kind": entry.kind,
                "raw_status": entry.raw_status,
                "payload": payload,
            }
        )
    canonical = json.dumps(
        {"head_commit": head_commit, "entries": payload_entries},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def fingerprint_change_subset(
    changes: RepositoryChanges,
    selected_paths: Iterable[str],
) -> str:
    """Fingerprint only change entries selected by current or historical path.

    The payload shape intentionally matches the canonical repository fingerprint, while
    excluding unrelated entries. A rename is selected when either side is selected and
    remains bound to the destination payload, so moving a test out of a test directory
    cannot evade a previously captured test-change gate.
    """
    selected = {_normalize_selected_path(path) for path in selected_paths}
    entries = tuple(
        entry
        for entry in changes.entries
        if entry.path in selected or (entry.old_path is not None and entry.old_path in selected)
    )
    if not entries:
        raise RepositoryChangeError(
            "selected paths do not reference any canonical repository change entry"
        )
    stored_by_identity = {
        (entry.path, entry.old_path, entry.kind): entry
        for entry in changes.fingerprint_entries
    }
    if not changes.fingerprint_entries:
        raise RepositoryChangeError(
            "subset fingerprint requires payload metadata captured with repository changes"
        )
    try:
        fingerprint_entries = tuple(
            stored_by_identity[(
                entry.path,
                entry.old_path,
                "added" if entry.kind == "untracked" else entry.kind,
            )]
            for entry in entries
        )
    except KeyError as exc:
        raise RepositoryChangeError(
            "captured fingerprint metadata does not cover the selected change entry"
        ) from exc
    fingerprint_payload = {
        "version": 1,
        "merge_base": changes.merge_base,
        "entries": [entry.to_payload() for entry in fingerprint_entries],
    }
    serialized = json.dumps(
        fingerprint_payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _change_fingerprint_entry(
    entry: ChangedPath,
    payloads: Mapping[str, _UntrackedPayload],
) -> ChangeFingerprintEntry:
    payload = payloads.get(entry.path)
    return ChangeFingerprintEntry(
        path=entry.path,
        old_path=entry.old_path,
        kind="added" if entry.kind == "untracked" else entry.kind,
        payload_type=payload.content_type if payload is not None else None,
        payload_size=payload.fingerprint_size if payload is not None else None,
        payload_digest=payload.fingerprint_digest if payload is not None else None,
        payload_mode=payload.mode if payload is not None else None,
    )


def _normalize_selected_path(raw_path: str) -> str:
    if not isinstance(raw_path, str) or not raw_path.strip() or "\\" in raw_path:
        raise RepositoryChangeError("selected change paths must be non-empty POSIX paths")
    path = PurePosixPath(raw_path)
    if path.is_absolute() or ".." in path.parts:
        raise RepositoryChangeError("selected change paths must be repository-relative")
    return path.as_posix()


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

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_CACHE_TYPE = "final-review-evidence-snapshot"


@dataclass(frozen=True)
class AuditCompactionSummary:
    audit_path: str
    change_kind: str
    semantic_payload_type: str | None
    semantic_payload_size: int | None
    semantic_payload_sha256: str | None
    omitted_diff_chars: int

    def render(self) -> str:
        return "\n".join(
            (
                "=== DETERMINISTIC AUDIT PROJECTION (COMPACT EVIDENCE) ===",
                f"path: {self.audit_path}",
                f"change_kind: {self.change_kind}",
                f"semantic_payload_type: {self.semantic_payload_type or 'none'}",
                "semantic_payload_size: "
                f"{self.semantic_payload_size if self.semantic_payload_size is not None else 'none'}",
                f"semantic_payload_sha256: {self.semantic_payload_sha256 or 'none'}",
                f"omitted_full_diff_chars: {self.omitted_diff_chars}",
                "reason: The full managed audit projection is deterministic and repeats "
                "structured findings, approvals, and validation attestations supplied "
                "separately. Its canonical semantic payload remains bound to the complete "
                "branch fingerprint and validation boundary.",
                "=== END COMPACT AUDIT EVIDENCE ===",
            )
        )


@dataclass(frozen=True)
class FinalReviewEvidenceSnapshot:
    """Immutable derived evidence bound to one fully validated repository snapshot."""

    start_commit: str
    merge_base: str
    head_commit: str
    index_fingerprint: str
    repository_identity_digest: str
    repository_fingerprint: str
    semantic_markdown_paths: tuple[str, ...]
    excluded_paths: tuple[str, ...]
    audit_path: str | None
    entries: tuple[ChangedPath, ...]
    fingerprint_entries: tuple[ChangeFingerprintEntry, ...]
    canonical_diff: str
    evidence_diff: str
    gate_paths: tuple[str, ...]
    audit_summary: AuditCompactionSummary | None
    collection_elapsed_ms: int

    def __post_init__(self) -> None:
        for label, value in (
            ("merge_base", self.merge_base),
            ("head_commit", self.head_commit),
        ):
            if not _GIT_OBJECT_ID.fullmatch(value):
                raise ValueError(f"snapshot {label} must be a Git object ID")
        for label, value in (
            ("index_fingerprint", self.index_fingerprint),
            ("repository_identity_digest", self.repository_identity_digest),
            ("repository_fingerprint", self.repository_fingerprint),
        ):
            if not _SHA256.fullmatch(value):
                raise ValueError(f"snapshot {label} must be SHA-256")
        if self.collection_elapsed_ms < 0:
            raise ValueError("snapshot collection time must not be negative")
        if self.semantic_markdown_paths != tuple(sorted(set(self.semantic_markdown_paths))):
            raise ValueError("snapshot semantic paths are not canonical")
        if self.excluded_paths != tuple(sorted(set(self.excluded_paths))):
            raise ValueError("snapshot exclusions are not canonical")
        paths = tuple(entry.path for entry in self.entries)
        if tuple(entry.path for entry in self.fingerprint_entries) != paths:
            raise ValueError("snapshot fingerprint entries differ from paths")
        expected_gate_paths = tuple(sorted({entry.path for entry in self.entries}))
        if self.gate_paths != expected_gate_paths:
            raise ValueError("snapshot gate paths are not canonical")
        fingerprint_payload = {
            "version": 1,
            "merge_base": self.merge_base,
            "entries": [entry.to_payload() for entry in self.fingerprint_entries],
        }
        expected_fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if self.repository_fingerprint != expected_fingerprint:
            raise ValueError("snapshot repository fingerprint is inconsistent")
        expected_evidence = self.canonical_diff
        if self.audit_summary is not None:
            if self.audit_path != self.audit_summary.audit_path:
                raise ValueError("snapshot audit summary has another path")
            compacted = exclude_review_diff_paths(
                self.canonical_diff, (self.audit_summary.audit_path,)
            )
            expected_evidence = "\n\n".join(
                part
                for part in (compacted.strip(), self.audit_summary.render())
                if part
            )
        if self.evidence_diff != expected_evidence:
            raise ValueError("snapshot evidence differs from canonical inputs")

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(entry.path for entry in self.entries)

    def matches(
        self,
        probe: RepositorySnapshotProbe,
        *,
        start_commit: str,
        semantic_markdown_paths: Iterable[str],
        excluded_paths: Iterable[str],
        audit_path: str | None,
    ) -> bool:
        return (
            self.start_commit == start_commit
            and self.semantic_markdown_paths
            == tuple(sorted(set(semantic_markdown_paths)))
            and self.excluded_paths == tuple(sorted(set(excluded_paths)))
            and self.audit_path == audit_path
            and self.merge_base == probe.merge_base
            and self.head_commit == probe.head_commit
            and self.index_fingerprint == probe.index_fingerprint
            and self.repository_identity_digest == probe.identity_digest
            and self.repository_fingerprint == probe.fingerprint
            and self.entries == probe.entries
            and self.fingerprint_entries == probe.fingerprint_entries
            and self.paths == probe.paths
            and self.gate_paths == tuple(sorted(set(probe.review_paths)))
        )

    def repository_changes(self, repository_root: Path) -> RepositoryChanges:
        return RepositoryChanges(
            repository_root=repository_root.resolve(),
            merge_base=self.merge_base,
            entries=self.entries,
            diff_text=self.canonical_diff,
            fingerprint=self.repository_fingerprint,
            fingerprint_entries=self.fingerprint_entries,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "type": _CACHE_TYPE,
            "start_commit": self.start_commit,
            "merge_base": self.merge_base,
            "head_commit": self.head_commit,
            "index_fingerprint": self.index_fingerprint,
            "repository_identity_digest": self.repository_identity_digest,
            "repository_fingerprint": self.repository_fingerprint,
            "semantic_markdown_paths": list(self.semantic_markdown_paths),
            "excluded_paths": list(self.excluded_paths),
            "audit_path": self.audit_path,
            "entries": [_changed_path_payload(item) for item in self.entries],
            "fingerprint_entries": [
                _fingerprint_entry_payload(item) for item in self.fingerprint_entries
            ],
            "canonical_diff": self.canonical_diff,
            "evidence_diff": self.evidence_diff,
            "gate_paths": list(self.gate_paths),
            "audit_summary": (
                None if self.audit_summary is None else _audit_payload(self.audit_summary)
            ),
        }

    @classmethod
    def from_payload(cls, raw: object) -> FinalReviewEvidenceSnapshot:
        if not isinstance(raw, Mapping) or set(raw) != {
            "type",
            "start_commit",
            "merge_base",
            "head_commit",
            "index_fingerprint",
            "repository_identity_digest",
            "repository_fingerprint",
            "semantic_markdown_paths",
            "excluded_paths",
            "audit_path",
            "entries",
            "fingerprint_entries",
            "canonical_diff",
            "evidence_diff",
            "gate_paths",
            "audit_summary",
        }:
            raise ValueError("snapshot cache has invalid fields")
        if raw["type"] != _CACHE_TYPE:
            raise ValueError("snapshot cache has invalid type")
        return cls(
            start_commit=_string(raw["start_commit"], "start_commit"),
            merge_base=_string(raw["merge_base"], "merge_base"),
            head_commit=_string(raw["head_commit"], "head_commit"),
            index_fingerprint=_string(raw["index_fingerprint"], "index_fingerprint"),
            repository_identity_digest=_string(
                raw["repository_identity_digest"], "repository_identity_digest"
            ),
            repository_fingerprint=_string(
                raw["repository_fingerprint"], "repository_fingerprint"
            ),
            semantic_markdown_paths=_strings(
                raw["semantic_markdown_paths"], "semantic_markdown_paths"
            ),
            excluded_paths=_strings(raw["excluded_paths"], "excluded_paths"),
            audit_path=(
                None
                if raw["audit_path"] is None
                else _string(raw["audit_path"], "audit_path")
            ),
            entries=tuple(_changed_path(item) for item in _list(raw["entries"], "entries")),
            fingerprint_entries=tuple(
                _fingerprint_entry(item)
                for item in _list(raw["fingerprint_entries"], "fingerprint_entries")
            ),
            canonical_diff=_string(raw["canonical_diff"], "canonical_diff", empty=True),
            evidence_diff=_string(raw["evidence_diff"], "evidence_diff", empty=True),
            gate_paths=_strings(raw["gate_paths"], "gate_paths"),
            audit_summary=(
                None
                if raw["audit_summary"] is None
                else _audit_summary(raw["audit_summary"])
            ),
            # Collection time is process-local diagnostics. Persisting it would
            # make otherwise identical cache bytes (and the file-write intent)
            # change after a crash between intent and physical write.
            collection_elapsed_ms=0,
        )


def build_final_review_evidence_snapshot(
    changes: RepositoryChanges,
    probe: RepositorySnapshotProbe,
    *,
    start_commit: str,
    semantic_markdown_paths: Iterable[str],
    excluded_paths: Iterable[str],
    audit_path: str | None,
    collection_elapsed_ms: int,
) -> FinalReviewEvidenceSnapshot:
    if (
        changes.merge_base != probe.merge_base
        or changes.fingerprint != probe.fingerprint
        or changes.entries != probe.entries
        or changes.fingerprint_entries != probe.fingerprint_entries
    ):
        raise ValueError(
            "repository changed while final-review evidence was being collected"
        )
    summary = None
    evidence_diff = changes.diff_text
    if audit_path is not None and audit_path in changes.paths:
        matching = tuple(
            entry for entry in changes.fingerprint_entries if entry.path == audit_path
        )
        if len(matching) != 1:
            raise ValueError(
                "final-review audit compaction requires exactly one fingerprint entry"
            )
        compacted = exclude_review_diff_paths(changes.diff_text, (audit_path,))
        entry = matching[0]
        summary = AuditCompactionSummary(
            audit_path=audit_path,
            change_kind=entry.kind,
            semantic_payload_type=entry.payload_type,
            semantic_payload_size=entry.payload_size,
            semantic_payload_sha256=entry.payload_digest,
            omitted_diff_chars=max(0, len(changes.diff_text) - len(compacted)),
        )
        evidence_diff = "\n\n".join(
            part for part in (compacted.strip(), summary.render()) if part
        )
    return FinalReviewEvidenceSnapshot(
        start_commit=start_commit,
        merge_base=changes.merge_base,
        head_commit=probe.head_commit,
        index_fingerprint=probe.index_fingerprint,
        repository_identity_digest=probe.identity_digest,
        repository_fingerprint=changes.fingerprint,
        semantic_markdown_paths=tuple(sorted(set(semantic_markdown_paths))),
        excluded_paths=tuple(sorted(set(excluded_paths))),
        audit_path=audit_path,
        entries=changes.entries,
        fingerprint_entries=changes.fingerprint_entries,
        canonical_diff=changes.diff_text,
        evidence_diff=evidence_diff,
        gate_paths=tuple(sorted(set(changes.review_paths))),
        audit_summary=summary,
        collection_elapsed_ms=collection_elapsed_ms,
    )


def load_final_review_evidence_cache(
    path: Path, *, expected_file_sha256: str | None
) -> FinalReviewEvidenceSnapshot | None:
    if expected_file_sha256 is None or not _SHA256.fullmatch(expected_file_sha256):
        return None
    try:
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_file_sha256:
            return None
        envelope = json.loads(raw.decode("utf-8"))
        if not isinstance(envelope, Mapping) or set(envelope) != {
            "snapshot_sha256",
            "snapshot",
        }:
            return None
        snapshot_bytes = _canonical_bytes(envelope["snapshot"])
        if envelope["snapshot_sha256"] != hashlib.sha256(snapshot_bytes).hexdigest():
            return None
        return FinalReviewEvidenceSnapshot.from_payload(envelope["snapshot"])
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def render_final_review_evidence_cache(snapshot: FinalReviewEvidenceSnapshot) -> str:
    payload = snapshot.to_payload()
    snapshot_sha256 = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    envelope = {"snapshot_sha256": snapshot_sha256, "snapshot": payload}
    return _canonical_bytes(envelope).decode("utf-8") + "\n"


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _changed_path_payload(item: ChangedPath) -> dict[str, object]:
    return {
        "path": item.path,
        "kind": item.kind,
        "raw_status": item.raw_status,
        "old_path": item.old_path,
        "tracked": item.tracked,
        "working_tree": item.working_tree,
    }


def _changed_path(raw: object) -> ChangedPath:
    if not isinstance(raw, Mapping) or set(raw) != {
        "path",
        "kind",
        "raw_status",
        "old_path",
        "tracked",
        "working_tree",
    }:
        raise ValueError("snapshot changed path has invalid fields")
    old_path = raw["old_path"]
    if old_path is not None and not isinstance(old_path, str):
        raise ValueError("snapshot old path is invalid")
    return ChangedPath(
        path=_string(raw["path"], "path"),
        kind=_string(raw["kind"], "kind"),
        raw_status=_string(raw["raw_status"], "raw_status"),
        old_path=old_path,
        tracked=_boolean(raw["tracked"], "tracked"),
        working_tree=_boolean(raw["working_tree"], "working_tree"),
    )


def _fingerprint_entry_payload(item: ChangeFingerprintEntry) -> dict[str, object]:
    return {
        "path": item.path,
        "old_path": item.old_path,
        "kind": item.kind,
        "payload_type": item.payload_type,
        "payload_size": item.payload_size,
        "payload_digest": item.payload_digest,
        "payload_mode": item.payload_mode,
    }


def _fingerprint_entry(raw: object) -> ChangeFingerprintEntry:
    if not isinstance(raw, Mapping) or set(raw) != {
        "path",
        "old_path",
        "kind",
        "payload_type",
        "payload_size",
        "payload_digest",
        "payload_mode",
    }:
        raise ValueError("snapshot fingerprint entry has invalid fields")
    return ChangeFingerprintEntry(
        path=_string(raw["path"], "fingerprint path"),
        old_path=_optional_string(raw["old_path"], "fingerprint old_path"),
        kind=_string(raw["kind"], "fingerprint kind"),
        payload_type=_optional_string(raw["payload_type"], "payload_type"),
        payload_size=_optional_integer(raw["payload_size"], "payload_size"),
        payload_digest=_optional_string(raw["payload_digest"], "payload_digest"),
        payload_mode=_optional_integer(raw["payload_mode"], "payload_mode"),
    )


def _audit_payload(item: AuditCompactionSummary) -> dict[str, object]:
    return {
        "audit_path": item.audit_path,
        "change_kind": item.change_kind,
        "semantic_payload_type": item.semantic_payload_type,
        "semantic_payload_size": item.semantic_payload_size,
        "semantic_payload_sha256": item.semantic_payload_sha256,
        "omitted_diff_chars": item.omitted_diff_chars,
    }


def _audit_summary(raw: object) -> AuditCompactionSummary:
    if not isinstance(raw, Mapping) or set(raw) != {
        "audit_path",
        "change_kind",
        "semantic_payload_type",
        "semantic_payload_size",
        "semantic_payload_sha256",
        "omitted_diff_chars",
    }:
        raise ValueError("snapshot audit summary has invalid fields")
    return AuditCompactionSummary(
        audit_path=_string(raw["audit_path"], "audit_path"),
        change_kind=_string(raw["change_kind"], "change_kind"),
        semantic_payload_type=_optional_string(
            raw["semantic_payload_type"], "semantic_payload_type"
        ),
        semantic_payload_size=_optional_integer(
            raw["semantic_payload_size"], "semantic_payload_size"
        ),
        semantic_payload_sha256=_optional_string(
            raw["semantic_payload_sha256"], "semantic_payload_sha256"
        ),
        omitted_diff_chars=_integer(raw["omitted_diff_chars"], "omitted_diff_chars"),
    )


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"snapshot {label} must be a list")
    return value


def _strings(value: object, label: str) -> tuple[str, ...]:
    return tuple(_string(item, label) for item in _list(value, label))


def _string(value: object, label: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value):
        raise ValueError(f"snapshot {label} must be a string")
    return value


def _optional_string(value: object, label: str) -> str | None:
    return None if value is None else _string(value, label)


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"snapshot {label} must be a non-negative integer")
    return value


def _optional_integer(value: object, label: str) -> int | None:
    return None if value is None else _integer(value, label)


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"snapshot {label} must be a boolean")
    return value
