from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

import orchestrator_version
from orchestrator_version import orchestrator_code_version


ROOT = Path(__file__).resolve().parents[1]


def _version_in_fresh_process(repository: Path) -> str:
    environment = dict(os.environ)
    environment.update(
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=str(ROOT / "src"),
    )
    return subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "from orchestrator_version import orchestrator_code_version; "
                "print(orchestrator_code_version(Path.cwd()))"
            ),
        ],
        cwd=repository,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_code_version_covers_all_shipped_source_and_schema_components(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "schemas").mkdir()
    source = tmp_path / "src" / "request_builder.py"
    schema = tmp_path / "schemas" / "writer.json"
    config = tmp_path / "orchestrator.toml"
    launcher = tmp_path / "run_task"
    agents = tmp_path / "AGENTS.md"
    claude = tmp_path / "CLAUDE.md"
    codex = tmp_path / "CODEX.md"
    source.write_text("request = 'one'\n", encoding="utf-8")
    schema.write_text('{"version":1}\n', encoding="utf-8")
    config.write_text("[agents]\n", encoding="utf-8")
    launcher.write_text("python orchestrator.py\n", encoding="utf-8")
    agents.write_text("provider policy\n", encoding="utf-8")
    claude.write_text("review policy\n", encoding="utf-8")
    codex.write_text("implementation policy\n", encoding="utf-8")

    versions = [orchestrator_version._compute_orchestrator_code_version(tmp_path)]
    for path, replacement in (
        (source, "request = 'two'\n"),
        (schema, '{"version":2}\n'),
        (config, "[agents]\nmode = 'strict'\n"),
        (launcher, "python3 orchestrator.py\n"),
        (agents, "changed provider policy\n"),
        (claude, "changed review policy\n"),
        (codex, "changed implementation policy\n"),
    ):
        path.write_text(replacement, encoding="utf-8")
        versions.append(
            orchestrator_version._compute_orchestrator_code_version(tmp_path)
        )

    assert len(set(versions)) == len(versions)
    assert all(len(version) == 64 for version in versions)


def test_code_version_hashes_each_installation_once_per_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "schemas").mkdir()
    (tmp_path / "src" / "runtime.py").write_text("runtime = 1\n", encoding="utf-8")
    (tmp_path / "schemas" / "record.json").write_text("{}\n", encoding="utf-8")
    original_read_bytes = Path.read_bytes
    reads = 0

    def counted_read_bytes(path: Path) -> bytes:
        nonlocal reads
        reads += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)

    first = orchestrator_code_version(tmp_path)
    second = orchestrator_code_version(tmp_path)

    assert first == second
    assert reads == 2


def test_code_version_recomputes_changed_installation_in_new_process(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "schemas").mkdir()
    source = tmp_path / "src" / "runtime.py"
    source.write_text("runtime = 1\n", encoding="utf-8")
    (tmp_path / "schemas" / "record.json").write_text("{}\n", encoding="utf-8")
    first = _version_in_fresh_process(tmp_path)

    source.write_text("runtime = 2\n", encoding="utf-8")
    second = _version_in_fresh_process(tmp_path)

    assert first != second
