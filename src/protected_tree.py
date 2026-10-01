"""Detect protected-tree changes without following directory symlinks."""
import hashlib
import logging
import os
from pathlib import Path
import stat


def fingerprint(roots, excluded=()):
    excluded = set(excluded)
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
        content = (hashlib.sha256(path.read_bytes()).hexdigest() if kind == 'file'
                   else os.readlink(path) if kind == 'link' else '')
        rows[str(path)] = (kind, stat.S_IMODE(info.st_mode), info.st_dev, info.st_ino, info.st_nlink, content)
        if kind == 'dir':
            for child in sorted(path.iterdir()): visit(child)
    for root in roots: visit(Path(root))
    return rows


def differences(before, after):
    return sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))


def _outermost(paths):
    return tuple(path for path in paths if not any(other != path and path.is_relative_to(other) for other in paths))


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
        self._protected_before = fingerprint(_outermost(self._protected_paths), self._fingerprint_excluded)

    def after_provider_process(self) -> None:
        if self._protected_before is None: return
        self.remove_sandbox_placeholders()
        after = fingerprint(_outermost(self._protected_paths), self._fingerprint_excluded)
        changed = differences(self._protected_before, after)
        self._protected_before = None
        if changed:
            self.metadata["protected_tree_changes"] = changed
            raise self._protection_error("implementer changed protected trees: " + ", ".join(changed),
                                       provider_data=self.metadata)
