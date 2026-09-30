"""Antigravity isolation paths are strict, profile-bound TOML inputs."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_config import AgentSettings, isolation_options_digest, default_antigravity_home, default_antigravity_run_root
from antigravity_adapter import NativeAntigravityReviewAdapter
from cli import ConfigError, load_repo_config


ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path: Path, *, provider: str = "antigravity", options: str = "") -> Path:
    repository = tmp_path / "reviewed"
    repository.mkdir(exist_ok=True)
    model = "gemini-3.1-pro-high" if provider == "antigravity" else "sonnet"
    path = repository / "orchestrator.toml"
    path.write_text(
        f'[agent_profiles.agy]\nprovider = "{provider}"\nmodel = "{model}"\neffort = "high"\n'
        f'[agent_profiles.agy.provider_options.antigravity]\n{options}', encoding="utf-8",
    )
    return path


def test_antigravity_toml_paths_and_defaults_are_normalized(tmp_path: Path) -> None:
    home = tmp_path / "isolated-home"
    root = "/var/tmp/dao-agy-profile-example"
    explicit = load_repo_config(_config(tmp_path, options=f'home = "{home}"\nrun_root = "{root}"\n'))
    assert (explicit.agent_profiles["agy"].antigravity_home, explicit.agent_profiles["agy"].antigravity_run_root) == (str(home), root)
    default = load_repo_config(_config(tmp_path, options=""))
    assert (default.agent_profiles["agy"].antigravity_home, default.agent_profiles["agy"].antigravity_run_root) == (default_antigravity_home(), default_antigravity_run_root())
    selected = AgentSettings("antigravity", "agy", "gemini-3.1-pro-high", None, "high", antigravity_home=str(home), antigravity_run_root=root)
    adapter = NativeAntigravityReviewAdapter(selected)
    assert (adapter.isolated_home, adapter.run_root) == (home, Path(root))
    assert isolation_options_digest(selected) != isolation_options_digest(AgentSettings("antigravity", "agy", "gemini-3.1-pro-high", None, "high"))


@pytest.mark.parametrize("option,value,diagnostic", [
    ("home", "relative/home", "absolute path"),
    ("home", str(Path.home()), "personal HOME"),
    ("home", str(Path.home() / ".gemini"), "personal HOME"),
    ("home", "{repository}/nested", "repository"),
    ("run_root", "relative/root", "absolute path"),
    ("run_root", "/tmp/dao-agy-review", "below /var/tmp"),
    ("run_root", "/home/operator/dao-agy-review", "below /var/tmp"),
    ("unknown", "value", "unknown keys"),
])
def test_invalid_antigravity_option_is_rejected(tmp_path: Path, option: str, value: str, diagnostic: str) -> None:
    value = value.replace("{repository}", str(tmp_path / "reviewed"))
    with pytest.raises(ConfigError, match=diagnostic):
        load_repo_config(_config(tmp_path, options=f'{option} = "{value}"\n'))


def test_antigravity_option_rejected_for_other_provider(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="require provider antigravity"):
        load_repo_config(_config(tmp_path, provider="claude", options=f'home = "{tmp_path / "isolated-home"}"\n'))  # allowlist:provider -- profile configuration: reject options on another provider


def test_no_orchestrator_environment_bypass_for_agy_paths() -> None:
    for path in (ROOT / "src").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "DAO_AGY_HOME" not in source, path
        assert "DAO_AGY_RUN_ROOT" not in source, path


def _tool_config(tmp_path, roots, slot="implementer"):
    import json
    repository = tmp_path / "reviewed"
    repository.mkdir(exist_ok=True)
    path = repository / "orchestrator.toml"
    path.write_text(f'[roles]\n{slot} = "toolprofile"\n'
                    '[agent_profiles.toolprofile]\nprovider = "claude"\nmodel = "opus"\neffort = "high"\n'  # allowlist:provider -- profile configuration: tool roots
                    '[agent_profiles.toolprofile.provider_options.claude]\n'  # allowlist:provider -- profile configuration: tool roots
                    f'toolchain_read_roots = {json.dumps(roots)}\n')
    return path


def test_tool_roots_bind_resolved_home_subdirectory_and_digest(tmp_path, monkeypatch):
    from dataclasses import replace
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    tool = home / ".nvm" / "versions" / "node" / "v22.23.2"
    tool.mkdir(parents=True)
    alias = tmp_path / "node-link"
    alias.symlink_to(tool, target_is_directory=True)
    config = load_repo_config(_tool_config(tmp_path, [str(alias)]))
    profile = config.agent_profiles["toolprofile"]
    assert profile.toolchain_read_roots == (str(tool),)
    settings = AgentSettings(profile.provider, profile.binary, profile.model, None, "high", toolchain_read_roots=profile.toolchain_read_roots)
    assert isolation_options_digest(settings)
    assert isolation_options_digest(settings) != isolation_options_digest(replace(settings, toolchain_read_roots=()))


@pytest.mark.parametrize("kind", ["relative", "missing", "file", "root", "home", "home-parent", "repo", "repo-parent", "repo-child", "comma", "space", "many", "duplicate"])
def test_tool_roots_reject_unsafe_paths(tmp_path, monkeypatch, kind):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    repo = tmp_path / "reviewed"
    repo.mkdir()
    good = tmp_path / "tool"
    good.mkdir()
    file = tmp_path / "file"
    file.write_text("x")
    for name in ("a,b", "a b"):
        (tmp_path / name).mkdir()
    roots = {"relative": ["relative"], "missing": [str(tmp_path / "missing")], "file": [str(file)],
             "root": ["/"], "home": [str(home)], "home-parent": [str(tmp_path)], "repo": [str(repo)],
             "repo-parent": [str(tmp_path)], "repo-child": [str(repo / "sub")], "comma": [str(tmp_path / "a,b")],
             "space": [str(tmp_path / "a b")], "many": [str(good)] * 9, "duplicate": [str(good)] * 2}[kind]
    (repo / "sub").mkdir()
    with pytest.raises(ConfigError, match="toolchain_read_roots"):
        load_repo_config(_tool_config(tmp_path, roots))


@pytest.mark.parametrize("slot", ["reviewer", "final_reviewer"])
def test_review_profiles_reject_even_empty_tool_roots(tmp_path, slot):
    with pytest.raises(ConfigError, match="implementer-only"):
        load_repo_config(_tool_config(tmp_path, [], slot))



def test_toolchain_symlink_in_repository_is_rejected_even_when_target_is_external(tmp_path):
    repository = tmp_path / "reviewed"
    repository.mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    alias = repository / "tool-alias"
    alias.symlink_to(tools, target_is_directory=True)
    with pytest.raises(ConfigError, match="repository"):
        load_repo_config(_tool_config(tmp_path, [str(alias)]))
