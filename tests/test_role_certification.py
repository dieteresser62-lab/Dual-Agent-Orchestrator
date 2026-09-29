from __future__ import annotations

import ast
import hashlib
import json
import shutil
from argparse import Namespace
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest

import agent_adapters
from agent_adapters import NativeClaudeReviewAdapter, NativeCodexAdapter, create_agent_pair
from agent_config import AgentSettings
from agent_roles import AgentRoleName, AgentSlot
from role_binding import binding_for_role
from role_certification import (
    CertificationError, CertificationErrorCode, CertificationTable, load_role_certifications,
)
from workflow_run_setup import _apply_resumed_agent_profiles
from state_io import StateSchemaError


ROOT = Path(__file__).resolve().parents[1]
TABLE = "schemas/role-provider-certifications-v1.json"
EVIDENCE = "docs/evidence/role-certification-v1.json"
REVIEWER_EVIDENCE = "docs/evidence/role-certification-reviewer-restricted-v1.json"
AGY_EVIDENCE = "docs/evidence/antigravity/capability-v1.json"


def _copy_sources(root: Path) -> None:
    paths = {TABLE, EVIDENCE, REVIEWER_EVIDENCE, AGY_EVIDENCE, "schemas/native-provider-schema-capabilities-v2.json"}
    for path in paths:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, target)


def _mutate(root: Path, change: Callable[[dict[str, object]], object]) -> None:
    path = root / TABLE
    table = json.loads(path.read_text())
    change(table)
    path.write_text(json.dumps(table))


def _mutate_evidence(root: Path, change: Callable[[dict[str, object]], object]) -> None:
    path = root / EVIDENCE
    document = json.loads(path.read_text())
    change(document)
    path.write_text(json.dumps(document))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    def update_digest(table: dict[str, object]) -> None:
        for row in table["certifications"]:
            if row["evidence"]["path"] == EVIDENCE:
                row["evidence"]["sha256"] = digest

    _mutate(root, update_digest)


def _assert_node_ids_exist(document: dict[str, object], source_root: Path = ROOT) -> None:
    for refs in document["slots"].values():
        for ref in refs:
            node_id = ref["node_id"]
            path, function = node_id.split("::", 1)
            function = function.split("[", 1)[0]
            source = source_root / path
            assert source.is_file(), node_id
            tree = ast.parse(source.read_text(encoding="utf-8"))
            assert any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == function
                for node in tree.body
            ), node_id


def test_three_existing_slots_are_certified_with_distinct_reviewer_entries() -> None:
    table = load_role_certifications()
    assert [(entry.slot, entry.role, entry.provider, entry.manufacturer, entry.status) for entry in table.entries] == [
        (AgentSlot.IMPLEMENTER, AgentRoleName.IMPLEMENTER, "codex", "openai", "certified"),
        (AgentSlot.REVIEWER, AgentRoleName.REVIEWER, "antigravity", "google", "candidate"),
        (AgentSlot.REVIEWER, AgentRoleName.REVIEWER, "claude", "anthropic", "certified"),
        (AgentSlot.FINAL_REVIEWER, AgentRoleName.REVIEWER, "antigravity", "google", "candidate"),
        (AgentSlot.FINAL_REVIEWER, AgentRoleName.REVIEWER, "claude", "anthropic", "certified"),
    ]
    assert table.entries[2].evidence_sha256 == table.entries[4].evidence_sha256
    assert table.entries[0].probe_profile["model"] == "gpt-6-sol"
    assert table.entries[0].probe_profile["reasoning_or_effort"] == "medium"
    assert [entry.capability_profile for entry in table.entries] == [
        "codex", "antigravity", "claude", "antigravity", "claude",
    ]
    assert not hasattr(table.entries[0], "profile")


def test_reviewer_qualification_binds_restricted_transport_without_changing_codex() -> None:
    table = load_role_certifications()
    implementer, reviewer, final = (table.entries[i] for i in (0, 2, 4))
    assert len(implementer.capability_sha256) == 64
    assert implementer.rights_sha256 == "a678bf59677c0f9d05040dfacd7e618d00e806cd30253b82c432cd2f117af3c8"
    for entry in (reviewer, final):
        assert entry.probe_profile["semantic_flags"].count("--restricted") == 1
        assert "--setting-sources=user" not in entry.probe_profile["semantic_flags"]
        assert entry.version_scope["cli_version"] == "2.1.283 (Claude Code)"
        assert entry.capability_sha256 != "3492500ce735ee5a236aa474bb322c32c02287421bf5ed15f04dfa2f41a2cc3e"
        assert entry.rights_sha256 != "d58b1c96b18baa35c24c9daf74da0625eb8d682f48061561fd4d67f71cfcac5c"
        assert entry.evidence_path == REVIEWER_EVIDENCE


def test_manufacturer_separation_uses_registry_identity_not_alias_names() -> None:
    baseline = tuple(entry for entry in load_role_certifications().entries if entry.status == "certified")
    aliases = CertificationTable((
        replace(baseline[0], provider="one", manufacturer="vendor-a"),
        replace(baseline[1], provider="two", manufacturer="vendor-b"),
        replace(baseline[2], provider="three", manufacturer="vendor-b"),
    ))
    selected = {AgentSlot.IMPLEMENTER: "one", AgentSlot.REVIEWER: "two", AgentSlot.FINAL_REVIEWER: "three"}
    assert set(aliases.require_occupancy(selected)) == set(AgentSlot)
    same_maker = CertificationTable((aliases.entries[0], replace(aliases.entries[1], manufacturer="vendor-a"), aliases.entries[2]))
    with pytest.raises(CertificationError, match="slot=reviewer provider=two"):
        same_maker.require_occupancy(selected)
    missing = {**selected, AgentSlot.FINAL_REVIEWER: "not-qualified"}
    with pytest.raises(CertificationError, match="slot=final_reviewer provider=not-qualified"):
        aliases.require_occupancy(missing)


def test_agy_candidate_is_blocked_and_model_family_is_bound() -> None:
    table = load_role_certifications()
    with pytest.raises(CertificationError, match="slot=implementer provider=antigravity"):
        table.require("antigravity", AgentRoleName.IMPLEMENTER, AgentSlot.IMPLEMENTER, model="gemini-3.1-pro-high")
    settings = AgentSettings("antigravity", "agy", "gemini-3.1-pro-high", 600, "high")
    with pytest.raises(CertificationError, match="missing qualification evidence"):
        create_agent_pair("antigravity", AgentRoleName.REVIEWER, slot=AgentSlot.REVIEWER, settings=settings, certifications=table)
    for slot in (AgentSlot.REVIEWER, AgentSlot.FINAL_REVIEWER):
        with pytest.raises(CertificationError, match="missing qualification evidence"):
            table.require("antigravity", AgentRoleName.REVIEWER, slot, model="gemini-3.1-pro-high")
        for model in ("claude-sonnet-4-6", "gpt-oss-120b-medium"):
            with pytest.raises(CertificationError, match="does not match manufacturer google family"):
                table.require("antigravity", AgentRoleName.REVIEWER, slot, model=model)
    promoted = CertificationTable(tuple(
        replace(entry, status="experimental") if entry.provider == "antigravity" else entry
        for entry in table.entries
    ))
    for model in ("gemini-3.1-pro-high", "gemini-other"):
        assert promoted.require("antigravity", AgentRoleName.REVIEWER, AgentSlot.REVIEWER, model=model).manufacturer == "google"
    selected = {AgentSlot.IMPLEMENTER: "codex", AgentSlot.REVIEWER: "antigravity", AgentSlot.FINAL_REVIEWER: "claude"}
    models = {AgentSlot.IMPLEMENTER: "gpt-6-sol", AgentSlot.REVIEWER: "gpt-oss-120b-medium", AgentSlot.FINAL_REVIEWER: "opus"}
    with pytest.raises(CertificationError, match="does not match manufacturer google family"):
        promoted.require_occupancy(selected, models=models)
    slots = {
        "implementer": AgentSettings("codex", "codex", "gpt-6-sol", 600, "high"),
        "reviewer": AgentSettings("antigravity", "agy", "gpt-oss-120b-medium", 600, "high"),
        "final_reviewer": AgentSettings("claude", "claude", "opus", 600, "high"),
    }
    persisted = SimpleNamespace(protocol_binding=SimpleNamespace(
        implementer_profile=SimpleNamespace(provider="codex", model="gpt-6-sol"),
        reviewer_profile=SimpleNamespace(provider="antigravity", model="gpt-oss-120b-medium"),
        final_reviewer_profile=SimpleNamespace(provider="claude", model="opus"),
    ))
    with pytest.raises(StateSchemaError, match="AGENT-PROFILE-DIFF.*does not match manufacturer google family"):
        _apply_resumed_agent_profiles(Namespace(slot_settings=slots), persisted)


def test_every_evidence_node_id_exists_in_the_test_suite() -> None:
    _assert_node_ids_exist(json.loads((ROOT / EVIDENCE).read_text()))
    _assert_node_ids_exist(json.loads((ROOT / REVIEWER_EVIDENCE).read_text()))
    _assert_node_ids_exist(json.loads((ROOT / AGY_EVIDENCE).read_text()))


def test_runtime_certification_does_not_need_a_tests_directory(tmp_path: Path) -> None:
    _copy_sources(tmp_path)
    assert not (tmp_path / "tests").exists()
    assert len(load_role_certifications(root=tmp_path).entries) == 5


@pytest.mark.parametrize("change,code", [
    (lambda doc: doc["certifications"].pop(), CertificationErrorCode.BASELINE_MISSING),
    (lambda doc: doc["certifications"].append(doc["certifications"][0]), CertificationErrorCode.DUPLICATE_ENTRY),
    (lambda doc: doc["certifications"][0].update(status="unknown"), CertificationErrorCode.ENTRY_INVALID),
    (lambda doc: doc["certifications"][0].update(status="experimental"), CertificationErrorCode.SOURCE_MISMATCH),
    (lambda doc: doc["certifications"][1].update(status="experimental"), CertificationErrorCode.EVIDENCE_INVALID),
    (lambda doc: doc["certifications"][0].update(extra=True), CertificationErrorCode.ENTRY_INVALID),
    (lambda doc: doc["certifications"][0].update(policy_sha256="0" * 64), CertificationErrorCode.SOURCE_MISMATCH),
    (lambda doc: doc["certifications"][0].update(rights_sha256="0" * 64), CertificationErrorCode.SOURCE_MISMATCH),
    (lambda doc: doc["certifications"][0].update(capability_sha256="0" * 64), CertificationErrorCode.SOURCE_MISMATCH),
    (lambda doc: doc["certifications"][0].update(capability_profile="claude"), CertificationErrorCode.SOURCE_MISMATCH),
    (lambda doc: doc["certifications"][0]["probe_profile"].update(model="other"), CertificationErrorCode.SOURCE_MISMATCH),
    (lambda doc: doc["certifications"][0].update(profile=doc["certifications"][0].pop("probe_profile")), CertificationErrorCode.ENTRY_INVALID),
    (lambda doc: doc["certifications"][0]["evidence"].update(sha256="0" * 64), CertificationErrorCode.EVIDENCE_INVALID),
    (lambda doc: doc.pop("provider_manufacturers"), CertificationErrorCode.ENTRY_INVALID),
    (lambda doc: doc.update(provider_manufacturers={}), CertificationErrorCode.ENTRY_INVALID),
    (lambda doc: doc.update(provider_manufacturers={"codex": "openai", "claude": "anthropic"}), CertificationErrorCode.ENTRY_INVALID),
    (lambda doc: doc["provider_manufacturers"].pop("codex"), CertificationErrorCode.ENTRY_INVALID),
    (lambda doc: doc["provider_manufacturers"].update(codex="OpenAI"), CertificationErrorCode.ENTRY_INVALID),
    (lambda doc: doc["provider_manufacturers"].update(codex=""), CertificationErrorCode.ENTRY_INVALID),
    (lambda doc: doc["certifications"][0].update(manufacturer="anthropic"), CertificationErrorCode.SOURCE_MISMATCH),
])
def test_missing_duplicate_unknown_and_tampered_entries_fail_closed(
    tmp_path: Path, change: Callable[[dict[str, object]], object], code: CertificationErrorCode,
) -> None:
    _copy_sources(tmp_path)
    _mutate(tmp_path, change)
    with pytest.raises(CertificationError) as raised:
        load_role_certifications(root=tmp_path)
    assert raised.value.code is code


def test_provider_registry_rejects_unknown_capability_provider(tmp_path: Path) -> None:
    _copy_sources(tmp_path)
    _mutate(tmp_path, lambda doc: doc.update(provider_manufacturers={
        "aardvark": "unknown", "claude": "anthropic", "codex": "openai",
    }))
    with pytest.raises(CertificationError, match="unregistered capability provider: aardvark") as raised:
        load_role_certifications(root=tmp_path)
    assert raised.value.code is CertificationErrorCode.ENTRY_INVALID


def test_missing_or_changed_evidence_file_fails_closed(tmp_path: Path) -> None:
    _copy_sources(tmp_path)
    (tmp_path / EVIDENCE).unlink()
    with pytest.raises(CertificationError, match="missing source"):
        load_role_certifications(root=tmp_path)
    _copy_sources(tmp_path)
    (tmp_path / EVIDENCE).write_text("tampered")
    with pytest.raises(CertificationError, match="evidence digest differs"):
        load_role_certifications(root=tmp_path)


@pytest.mark.parametrize("change", [
    lambda doc: doc["slots"].pop("reviewer"),
    lambda doc: doc["slots"].update(final_reviewer=[]),
])
def test_evidence_slot_coverage_and_nonempty_references_are_required(
    tmp_path: Path, change: Callable[[dict[str, object]], object],
) -> None:
    _copy_sources(tmp_path)
    _mutate_evidence(tmp_path, change)
    with pytest.raises(CertificationError) as raised:
        load_role_certifications(root=tmp_path)
    assert raised.value.code is CertificationErrorCode.EVIDENCE_INVALID


def test_unknown_evidence_node_is_a_test_failure_only(tmp_path: Path) -> None:
    _copy_sources(tmp_path)
    _mutate_evidence(
        tmp_path,
        lambda doc: doc["slots"]["implementer"][0].update(
            node_id="tests/test_agent_adapters.py::test_missing_evidence_node"
        ),
    )
    assert len(load_role_certifications(root=tmp_path).entries) == 5
    with pytest.raises(AssertionError, match="test_missing_evidence_node"):
        _assert_node_ids_exist(json.loads((tmp_path / EVIDENCE).read_text()))


def test_factory_checks_pair_and_slot_before_instantiating(monkeypatch: pytest.MonkeyPatch) -> None:
    table = load_role_certifications()
    settings = {
        "codex": AgentSettings("codex", "codex", "gpt-6-sol", 1800, "high"),
        "claude": AgentSettings("claude", "claude", "opus", 1800, "high"),
    }
    implementer = create_agent_pair("codex", AgentRoleName.IMPLEMENTER, slot=AgentSlot.IMPLEMENTER, settings=settings["codex"], certifications=table)
    reviewer = create_agent_pair("claude", AgentRoleName.REVIEWER, slot=AgentSlot.REVIEWER, settings=settings["claude"], certifications=table)
    final = create_agent_pair("claude", AgentRoleName.REVIEWER, slot=AgentSlot.FINAL_REVIEWER, settings=settings["claude"], certifications=table)
    assert isinstance(implementer, NativeCodexAdapter)
    assert isinstance(reviewer, NativeClaudeReviewAdapter)
    assert final is not reviewer and isinstance(final, NativeClaudeReviewAdapter)
    assert implementer.role_binding.permissions["sandbox"] == "workspace-write"
    assert reviewer.role_binding.permissions["allowed_tools"] == "Read"
    assert reviewer.role_binding.policy == binding_for_role(AgentRoleName.REVIEWER).policy
    assert implementer.role_binding.policy != reviewer.role_binding.policy

    def should_not_construct(*args: object, **kwargs: object) -> object:
        raise AssertionError("adapter was constructed")

    monkeypatch.setattr(agent_adapters, "NativeCodexAdapter", should_not_construct)
    with pytest.raises(CertificationError, match="slot=reviewer provider=codex.*missing qualification evidence"):
        create_agent_pair("codex", AgentRoleName.REVIEWER, slot=AgentSlot.REVIEWER, settings=settings["codex"], certifications=table)


def test_import_has_no_global_registry() -> None:
    assert "AGENT_REGISTRY" not in vars(agent_adapters)
