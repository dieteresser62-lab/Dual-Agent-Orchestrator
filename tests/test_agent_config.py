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
