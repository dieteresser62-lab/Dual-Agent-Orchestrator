"""Keep provider preparation tests from leaking temporary runtime directories."""

from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path
import tempfile

import pytest


@pytest.fixture(autouse=True)
def cleanup_provider_runtime_directories(
    tmp_path: Path,
) -> Iterator[None]:
    """Confine provider runtimes to this test and require their explicit cleanup."""
    root = tmp_path / "provider-runtimes"
    root.mkdir()
    previous_env = os.environ.get("TMPDIR")
    previous_tempdir = tempfile.tempdir
    os.environ["TMPDIR"] = str(root)
    tempfile.tempdir = str(root)
    try:
        yield
    finally:
        tempfile.tempdir = previous_tempdir
        if previous_env is None:
            os.environ.pop("TMPDIR", None)
        else:
            os.environ["TMPDIR"] = previous_env
        remaining = sorted(path for path in root.glob("dao-*-runtime-*") if path.is_dir())
        assert not remaining, f"Provider runtime directories remain after test: {remaining}"
