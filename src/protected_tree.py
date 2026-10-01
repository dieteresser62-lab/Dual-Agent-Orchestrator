"""Detect protected-tree changes without following directory symlinks."""
import hashlib
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
