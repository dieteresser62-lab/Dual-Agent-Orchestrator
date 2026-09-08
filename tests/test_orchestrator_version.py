from __future__ import annotations

from pathlib import Path

from orchestrator_version import orchestrator_code_version


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

    versions = [orchestrator_code_version(tmp_path)]
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
        versions.append(orchestrator_code_version(tmp_path))

    assert len(set(versions)) == len(versions)
    assert all(len(version) == 64 for version in versions)
