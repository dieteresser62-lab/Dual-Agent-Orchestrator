from __future__ import annotations

import ast
import hashlib
import json
import shutil
from pathlib import Path
from typing import Callable

import pytest

import agent_adapters
from agent_adapters import NativeClaudeReviewAdapter, NativeCodexAdapter, create_agent_pair
from agent_config import AgentSettings
from agent_roles import AgentRoleName, AgentSlot
from role_binding import binding_for_role
from role_certification import (
    CertificationError, CertificationErrorCode, load_role_certifications,
)


ROOT = Path(__file__).resolve().parents[1]
TABLE = "schemas/role-provider-certifications-v1.json"
EVIDENCE = "docs/evidence/role-certification-v1.json"


def _copy_sources(root: Path) -> None:
    paths = {TABLE, EVIDENCE, "schemas/native-provider-schema-capabilities-v1.json"}
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
        (AgentSlot.REVIEWER, AgentRoleName.REVIEWER, "claude", "anthropic", "certified"),
        (AgentSlot.FINAL_REVIEWER, AgentRoleName.REVIEWER, "claude", "anthropic", "certified"),
    ]
    assert table.entries[1].evidence_sha256 == table.entries[2].evidence_sha256
    assert table.entries[0].probe_profile["model"] == "gpt-6-sol"
    assert table.entries[0].probe_profile["reasoning_or_effort"] == "medium"
    assert not hasattr(table.entries[0], "profile")


def test_every_evidence_node_id_exists_in_the_test_suite() -> None:
    _assert_node_ids_exist(json.loads((ROOT / EVIDENCE).read_text()))


def test_runtime_certification_does_not_need_a_tests_directory(tmp_path: Path) -> None:
    _copy_sources(tmp_path)
    assert not (tmp_path / "tests").exists()
    assert len(load_role_certifications(root=tmp_path).entries) == 3


@pytest.mark.parametrize("change,code", [
    (lambda doc: doc["certifications"].pop(), CertificationErrorCode.BASELINE_MISSING),
    (lambda doc: doc["certifications"].append(doc["certifications"][0]), CertificationErrorCode.DUPLICATE_ENTRY),
    (lambda doc: doc["certifications"][0].update(status="unknown"), CertificationErrorCode.ENTRY_INVALID),
    (lambda doc: doc["certifications"][0].update(status="experimental"), CertificationErrorCode.SOURCE_MISMATCH),
    (lambda doc: doc["certifications"][0].update(extra=True), CertificationErrorCode.ENTRY_INVALID),
    (lambda doc: doc["certifications"][0].update(policy_sha256="0" * 64), CertificationErrorCode.SOURCE_MISMATCH),
    (lambda doc: doc["certifications"][0].update(rights_sha256="0" * 64), CertificationErrorCode.SOURCE_MISMATCH),
    (lambda doc: doc["certifications"][0].update(capability_sha256="0" * 64), CertificationErrorCode.SOURCE_MISMATCH),
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
    assert len(load_role_certifications(root=tmp_path).entries) == 3
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
