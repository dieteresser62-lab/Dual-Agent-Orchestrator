from pathlib import Path

import pytest

from toolchain_paths import CREDENTIAL_LOCATIONS, validate_private_scratch, validate_toolchain_read_roots


@pytest.mark.parametrize("name", CREDENTIAL_LOCATIONS)
@pytest.mark.parametrize("relation", ["exact", "child", "parent", "alias"])
def test_home_credentials_and_aliases_are_not_tool_roots(tmp_path, monkeypatch, name, relation):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    credential = home / name
    (credential / "child").mkdir(parents=True)
    root = {"exact": credential, "child": credential / "child", "parent": credential.parent}.get(relation)
    if relation == "alias":
        root = tmp_path / "innocent-tools"
        root.symlink_to(credential, target_is_directory=True)
    with pytest.raises(ValueError):
        validate_toolchain_read_roots([str(root)], tmp_path / "repo")


def test_nvm_node_under_home_remains_allowed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    node = home / ".nvm/versions/node/v22.20.0"
    node.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert validate_toolchain_read_roots([str(node)], tmp_path / "repo") == (str(node),)


@pytest.mark.parametrize("location", ["repo", "home", "protected", "tools", "ancestor", "symlink", "mode"])
def test_scratch_rejects_unsafe_locations(tmp_path, monkeypatch, location):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    protected = tmp_path / "gitdir"
    tools = tmp_path / "tools"
    for path in (home, repo, protected, tools):
        path.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    parent = {"repo": repo, "home": home, "protected": protected, "tools": tools}.get(location, tmp_path)
    scratch = parent / "scratch"
    scratch.mkdir(mode=0o700)
    if location == "ancestor":
        protected = scratch / "nested-protected"
    if location == "symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(scratch, target_is_directory=True)
        scratch = alias
    if location == "mode":
        scratch.chmod(0o755)
    with pytest.raises(ValueError):
        validate_private_scratch(scratch, repo, (protected,), (str(tools),))
