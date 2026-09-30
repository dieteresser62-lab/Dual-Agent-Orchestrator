from dataclasses import replace
from provider_identity import ProviderIdentity
from types import SimpleNamespace
import json

import pytest

from agent_config import AgentSettings, default_agent_settings
from model_catalog import bind_catalog_models, select_catalog_model
import agent_runtime
import workflow_run_setup


def _catalog():
    return {"models": [
        {"slug": "gpt-6.1-sol", "priority": 1, "visibility": "list", "supported_in_api": True},
        {"slug": "gpt-6-sol", "priority": 3, "visibility": "list", "supported_in_api": True},
        {"slug": "gpt-5.6-sol", "priority": 5, "visibility": "list", "supported_in_api": True},
        {"slug": "gpt-7-sol", "priority": 0, "visibility": "hidden", "supported_in_api": True},
        {"slug": "gpt-8-sol", "priority": 0, "visibility": "list", "supported_in_api": False},
    ]}


def test_catalog_selection_changes_with_rank_and_accepts_explicit_old_model():
    catalog = _catalog()
    assert select_catalog_model("sol", catalog) == "gpt-6.1-sol"
    catalog["models"][1]["priority"] = 0
    assert select_catalog_model("sol", catalog) == "gpt-6-sol"
    assert select_catalog_model("gpt-6-sol", catalog) == "gpt-6-sol"
    assert select_catalog_model("gpt-6.1-sol", catalog, resume=True) == "gpt-6.1-sol"


@pytest.mark.parametrize("requested", ["terra", "luna", "astra"])
def test_all_families_are_resolved(requested):
    catalog = [{"slug": "gpt-12.3.4-" + requested, "priority": 2, "visibility": "list", "supported_in_api": True}]
    assert select_catalog_model(requested, catalog) == "gpt-12.3.4-" + requested


@pytest.mark.parametrize("mutation,message", [
    (lambda c: c["models"].clear(), "no eligible"),
    (lambda c: c["models"][1].update(priority=1), "ambiguous"),
    (lambda c: c["models"][0].update(priority=True), "invalid priority"),
    (lambda c: c["models"].append(c["models"][0]), "duplicate"),
])
def test_catalog_fails_closed(mutation, message):
    catalog = _catalog()
    mutation(catalog)
    with pytest.raises(ValueError, match=message):
        select_catalog_model("sol", catalog)


def test_resume_never_reselects_and_rejects_disappeared_model():
    catalog = _catalog()
    assert select_catalog_model("gpt-6-sol", catalog, resume=True) == "gpt-6-sol"
    catalog["models"].pop(1)
    with pytest.raises(ValueError, match="bound model.*missing"):
        select_catalog_model("gpt-6-sol", catalog, resume=True)


def test_one_catalog_per_bound_binary_with_npm_interpreter():
    selected = AgentSettings("codex", "wrong-PATH-binary", "sol", None, "high")  # allowlist:provider -- profile configuration: fake catalog
    slots = {"reviewer": selected, "final_reviewer": selected}
    identity = SimpleNamespace(kind="verified", digest="same", entry_path="/nvm/bin/codex",  # allowlist:provider -- transport: real npm layout shape
                               launch_prefix=("/nvm/bin/node", "/nvm/lib/node_modules/@openai/codex/bin/codex.js"))  # allowlist:provider -- transport: bound npm entry
    calls = []
    def runner(command):
        calls.append(command)
        return 0, json.dumps(_catalog()), ""
    bind_catalog_models(slots, {slot: identity for slot in slots}, runner)
    assert calls == [[*identity.launch_prefix, "debug", "models"]]
    assert all(settings.model == "gpt-6.1-sol" for settings in slots.values())
    _catalog_data = _catalog()
    _catalog_data["models"][1]["priority"] = 0
    bind_catalog_models(slots, {slot: identity for slot in slots},
                        lambda cmd: (0, json.dumps(_catalog_data), ""), resume=True)
    assert slots["reviewer"].model == "gpt-6.1-sol"


def test_runtime_start_binds_resolved_settings_before_profile_creation(monkeypatch):
    slots = default_agent_settings()
    identities = {}
    def verify(adapter, **kwargs):
        adapter.provider_identity = ProviderIdentity("/bound/" + adapter.name, "/bound/" + adapter.name, "fake-version", "a" * 64, None, None, None)
        identities[adapter.bound_slot] = adapter.provider_identity
    monkeypatch.setattr(agent_runtime, "verify_agent_capabilities", verify)
    monkeypatch.setattr(agent_runtime, "run_local_command", lambda cmd: (0, json.dumps(_catalog()), ""))
    assert workflow_run_setup._capture_slot_identities(slots) == identities
    assert slots["implementer"].model == "gpt-6.1-sol"


def test_preflight_environment_drops_secret_and_catalog_uses_absolute_binary(monkeypatch):
    observed = {}
    monkeypatch.setenv("DAO_SECRET_TOKEN", "never-inherit")
    def run(command, **kwargs):
        observed.update(kwargs)
        assert command == ["/bound/binary", "debug", "models"]
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")
    monkeypatch.setattr(agent_runtime.subprocess, "run", run)
    agent_runtime.run_local_command(["/bound/binary", "debug", "models"])
    assert "DAO_SECRET_TOKEN" not in observed["env"]


def test_explicit_old_full_model_from_toml_is_preserved_at_runtime(tmp_path, monkeypatch):
    from cli import parse_args
    (tmp_path / "orchestrator.toml").write_text('[agent_profiles.implementation]\nmodel = "gpt-6-sol"\n')
    slots = parse_args([], cwd=tmp_path, environ={}).slot_settings
    assert slots["implementer"].model == "gpt-6-sol"
    identity = ProviderIdentity("/bound/provider", "/bound/provider", "fake-version", "a" * 64, None, None, None)
    monkeypatch.setattr(agent_runtime, "run_local_command", lambda cmd: (0, json.dumps(_catalog()), ""))
    workflow_run_setup._bind_slot_catalog_models(slots, {slot: identity for slot in slots})
    assert slots["implementer"].model == "gpt-6-sol"



def test_resume_family_override_matches_bound_slug_without_catalog_selection():
    assert workflow_run_setup._bound_family_matches("codex", "sol", "gpt-6-sol")  # allowlist:provider -- profile configuration: bound family
    assert workflow_run_setup._bound_family_matches("codex", "sol", "gpt-6.1-sol")  # allowlist:provider -- profile configuration: bound family
    assert not workflow_run_setup._bound_family_matches("codex", "terra", "gpt-6-sol")  # allowlist:provider -- profile configuration: foreign family
    assert not workflow_run_setup._bound_family_matches("codex", "sol", "gpt-6-sol-preview")  # allowlist:provider -- profile configuration: anchored family pattern
