from __future__ import annotations

import os
from pathlib import Path
import tempfile
import pytest


def can_symlink() -> bool:
    if not hasattr(os, "symlink"):
        return False
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            link = Path(tmpdir) / "link"
            target = Path(tmpdir) / "target"
            target.write_text("test", encoding="utf-8")
            link.symlink_to(target)
        return True
    except OSError:
        return False


requires_symlink = pytest.mark.skipif(
    not can_symlink(), reason="symlinks are unavailable or restricted by OS"
)
