"""Prove the runtime cleanup guard never touches another process's temp files."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import shutil
import tempfile

import pytest


@pytest.fixture(scope="module")
def external_runtime_probe() -> Iterator[list[Path]]:
    created: list[Path] = []
    yield created
    try:
        assert created and all(path.joinpath("owner-marker").read_text() == "external" for path in created)
    finally:
        for path in created:
            shutil.rmtree(path)


def test_external_runtime_survives_test_fixture_teardown(
    tmp_path: Path, external_runtime_probe: list[Path],
) -> None:
    assert Path(tempfile.gettempdir()) == tmp_path / "provider-runtimes"
    external = Path(tempfile.mkdtemp(prefix="dao-claude-runtime-", dir="/tmp"))  # allowlist:provider -- transport: simulate another process's runtime
    external_runtime_probe.append(external)
    external.joinpath("owner-marker").write_text("external")
