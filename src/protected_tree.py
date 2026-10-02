"""Detect protected-tree changes without following directory symlinks."""
from contextlib import contextmanager
import hashlib
import logging
import os
from pathlib import Path
import stat
import struct


def fingerprint(roots, excluded=(), *, git_indexes=()):
    excluded = set(excluded)
    git_indexes = set(git_indexes)
    rows = {}
    def visit(path):
        if path in excluded: return
        try:
            info = path.lstat()
        except FileNotFoundError:
            rows[str(path)] = ('absent',)
            return
        kind = ('link' if stat.S_ISLNK(info.st_mode) else 'dir' if stat.S_ISDIR(info.st_mode)
                else 'file' if stat.S_ISREG(info.st_mode) else 'other')
        data = path.read_bytes() if kind == 'file' else None
        content = (hashlib.sha256(data).hexdigest() if kind == 'file'
                   else os.readlink(path) if kind == 'link' else '')
        rows[str(path)] = (kind, stat.S_IMODE(info.st_mode), info.st_dev, info.st_ino, info.st_nlink, content)
        if path in git_indexes and kind == 'file':
            from git_index import index_content
            try:
                content = index_content(data)
            except (OSError, ValueError, struct.error):
                # An invalid snapshot can never authorize an index replacement.
                pass
            else:
                # Git refresh replaces the index atomically, changing its inode.
                rows[str(path)] = (kind, stat.S_IMODE(info.st_mode), info.st_dev, info.st_nlink, content)
        if kind == 'dir':
            for child in sorted(path.iterdir()): visit(child)
    for root in roots: visit(Path(root))
    return rows


def differences(before, after):
    return sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))


def outermost_protected_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    """Drop nested roots; outer read-only mounts and fingerprints cover them.

    A missing nested mount would need a placeholder inside a read-only parent,
    preventing sandbox startup. The outer rule already protects that path.
    """
    return tuple(path for path in paths if not any(other != path and path.is_relative_to(other) for other in paths))


def process_group_ended(pid, identity):
    """Require session evidence, or ESRCH for a leader too fast to capture."""
    from provider_process import observe_identity, ProcessStatus
    if identity is not None:
        return observe_identity(identity).status is ProcessStatus.ENDED
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        pass
    return False


@contextmanager
def parent_directory(repo_fd, relative):
    """Anchor all metadata and removals to the original repo, without links."""
    fd = os.dup(repo_fd)
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(part in {".", ".."} for part in parts):
        os.close(fd)
        raise ValueError("invalid placeholder path")
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd, parts[-1]
    finally:
        os.close(fd)


def missing_protected_paths(repo, repo_fd, protected):
    missing = []
    # These are the effective mounts used by the production permission helper;
    # nested absent paths under the same mount do not create extra placeholders.
    for path in outermost_protected_paths(protected):
        if path == repo or not path.is_relative_to(repo):
            continue
        relative = path.relative_to(repo).as_posix()
        try:
            with parent_directory(repo_fd, relative) as (fd, name):
                os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            missing.append(relative)
    return tuple(missing)


def cleanup_sandbox_placeholders(repo_fd, candidates, *, process_groups_ended):
    result = {"missing_before": list(candidates), "removed": [], "retained": [], "warnings": [],
              "process_groups_ended": process_groups_ended}
    if not result["process_groups_ended"]:
        result["retained"] = list(candidates)
        result["warnings"].append("Prozessgruppe noch aktiv oder Ende unbekannt; Platzhalter nicht entfernt; bitte selbst prüfen.")
        return result
    for relative in candidates:
        try:
            with parent_directory(repo_fd, relative) as (fd, name):
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if info.st_uid != os.getuid():
                    raise ValueError("foreign owner")
                if stat.S_ISDIR(info.st_mode):
                    directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        opened = os.fstat(directory)
                        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                            raise ValueError("directory changed")
                        with os.scandir(directory) as entries:
                            if next(entries, None) is not None:
                                raise ValueError("nonempty directory")
                    finally:
                        os.close(directory)
                elif not (stat.S_ISREG(info.st_mode) and info.st_size == 0):
                    raise ValueError("not an empty regular file or directory")
                # Recheck metadata immediately before removal. rmdir also
                # atomically refuses any directory populated in the meantime.
                current = os.stat(name, dir_fd=fd, follow_symlinks=False)
                signature = lambda entry: (entry.st_dev, entry.st_ino, entry.st_mode, entry.st_uid,
                                           entry.st_size, entry.st_mtime_ns, entry.st_ctime_ns)
                if signature(current) != signature(info):
                    raise ValueError("placeholder changed")
                if stat.S_ISDIR(info.st_mode):
                    os.rmdir(name, dir_fd=fd)
                else:
                    os.unlink(name, dir_fd=fd)
                result["removed"].append(relative)
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            result["retained"].append(relative)
            result["warnings"].append(f"unerwarteter Inhalt an Schutzpfad {relative} nach der Prüfung; bitte selbst prüfen")  # allowlist:german -- shared operator cleanup warning
    return result


class SandboxPlaceholders:
    """Hold original parent fds for absent effective mounts, including Gitdirs."""

    def __init__(self, protected):
        self.anchors = []
        root_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for path in outermost_protected_paths(protected):
                try:
                    with parent_directory(root_fd, str(path.relative_to("/"))) as (fd, name):
                        try:
                            os.stat(name, dir_fd=fd, follow_symlinks=False)
                        except FileNotFoundError:
                            self.anchors.append((path, os.dup(fd), name))
                except OSError:
                    # Unavailable or linked ancestors are never cleanup authority.
                    continue
        except BaseException:
            self.close()
            raise
        finally:
            os.close(root_fd)

    def remove(self, *, process_groups_ended):
        removed, retained = [], []
        for path, fd, name in self.anchors:
            result = cleanup_sandbox_placeholders(fd, (name,), process_groups_ended=process_groups_ended)
            removed.extend(path for _ in result["removed"])
            retained.extend(path for _ in result["retained"])
        return {"missing_before": [str(path) for path, _, _ in self.anchors],
                "removed": [str(path) for path in removed], "retained": [str(path) for path in retained],
                "process_groups_ended": process_groups_ended}

    def close(self):
        for _, fd, _ in self.anchors:
            os.close(fd)
        self.anchors = []


class ProtectedTreeGuard:
    """Shared host-side postcheck; only parent-owned diagnostic files are exempt."""

    def before_provider_process(self) -> None:
        self._fingerprint_excluded = {self._process_evidence_path} if self._process_evidence_path is not None else set()
        # Only files actually opened by the parent logger may change while
        # stream messages are emitted. Records/head/checkpoints are not exempt.
        for logger in (logging.getLogger(), logging.getLogger(type(self).__module__)):
            for handler in logger.handlers:
                if isinstance(handler, logging.FileHandler):
                    self._fingerprint_excluded.add(Path(handler.baseFilename))
        # Derive only the active Gitdir from the repository's own .git entry;
        # another protected directory containing HEAD is not a Gitdir authority.
        gitdir = self._repository_root / '.git'
        self._git_indexes = set()
        try:
            if not gitdir.is_symlink():
                if gitdir.is_file():
                    pointer = gitdir.read_text(encoding='utf-8').strip()
                    if pointer.startswith('gitdir: '):
                        gitdir = (self._repository_root / pointer[8:]).resolve(strict=True)
                if gitdir.is_dir() and gitdir in self._protected_paths:
                    self._git_indexes.add(gitdir / 'index')
        except (OSError, UnicodeError, RuntimeError):
            pass  # No exemption when the Gitdir cannot be established.
        self._protected_before = fingerprint(outermost_protected_paths(self._protected_paths), self._fingerprint_excluded,
                                             git_indexes=self._git_indexes)

    def after_provider_process(self) -> None:
        if self._protected_before is None: return
        self.remove_sandbox_placeholders()
        after = fingerprint(outermost_protected_paths(self._protected_paths), self._fingerprint_excluded,
                            git_indexes=self._git_indexes)
        changed = differences(self._protected_before, after)
        self._protected_before = None
        if changed:
            self.metadata["protected_tree_changes"] = changed
            raise self._protection_error("implementer changed protected trees: " + ", ".join(changed),
                                       provider_data=self.metadata)
