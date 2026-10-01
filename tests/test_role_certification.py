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
import role_binding
from agent_adapters import NativeClaudeReviewAdapter, NativeCodexAdapter, create_agent_pair
from agent_config import AgentSettings
from agent_roles import AgentRoleName, AgentSlot
from role_binding import binding_for, binding_for_role
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
CANARY_EVIDENCE = "docs/evidence/antigravity/canary-v1.json"
CANDIDATE_EVIDENCE = "docs/evidence/role-certification-candidates-v1.json"
CODEX_REVIEW_CANDIDATE_EVIDENCE = "docs/evidence/codex/reviewer-candidate-v1.json"  # allowlist:provider -- certification data: reviewer proof


def _copy_sources(root: Path) -> None:
    paths = {TABLE, EVIDENCE, REVIEWER_EVIDENCE, AGY_EVIDENCE, CANARY_EVIDENCE, CANDIDATE_EVIDENCE,
             CODEX_REVIEW_CANDIDATE_EVIDENCE,  # allowlist:provider -- certification data: reviewer proof
             "schemas/native-provider-schema-capabilities-v2.json",
             "docs/evidence/antigravity/phase-0-v1.json",
             "docs/evidence/antigravity/qualification-series-v1.json",
             "docs/evidence/antigravity/quality-results-v1.json",
             "docs/evidence/antigravity/operator-decisions-v1.json"}
    source_table = json.loads((ROOT / TABLE).read_text())
    paths.update(row[key]["path"] for row in source_table["certifications"]
                 for key in ("evidence", "canary_evidence") if key in row)
    # Promoted rows bind their measurement bundles, including P/F/W reports.
    for ref in list(paths):
        document = json.loads((ROOT / ref).read_text())
        manifest_ref = document.get('measurements', {}).get('redaction_manifest')
        if manifest_ref:
            paths.add(manifest_ref['path'])
            manifest = json.loads((ROOT / manifest_ref['path']).read_text())
            paths.update(str(Path(manifest_ref['path']).parent / item['path']) for item in manifest['files'])
        references = document.get("shared_evidence", [])
        for shared in references.values() if isinstance(references, dict) else references:
            paths.add(shared["path"])
            if shared["path"].endswith("phase0-results.json"):
                completion = json.loads((ROOT / shared["path"]).read_text())
                for kind in ("protection", "format"):
                    paths.update(str(Path(shared["path"]).parent / item["path"])
                                 for item in completion.get(kind, []))
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
        (AgentSlot.REVIEWER, AgentRoleName.REVIEWER, "antigravity", "google", "experimental"),
        (AgentSlot.REVIEWER, AgentRoleName.REVIEWER, "claude", "anthropic", "certified"),
        (AgentSlot.FINAL_REVIEWER, AgentRoleName.REVIEWER, "antigravity", "google", "experimental"),
        (AgentSlot.FINAL_REVIEWER, AgentRoleName.REVIEWER, "claude", "anthropic", "certified"),
        (AgentSlot.REVIEWER, AgentRoleName.REVIEWER, "codex", "openai", "experimental"),  # allowlist:provider -- certification data: promoted row
        (AgentSlot.FINAL_REVIEWER, AgentRoleName.REVIEWER, "codex", "openai", "experimental"),  # allowlist:provider -- certification data: promoted row
        (AgentSlot.IMPLEMENTER, AgentRoleName.IMPLEMENTER, "claude", "anthropic", "experimental"),  # allowlist:provider -- certification data: promoted row
    ]
    assert table.entries[2].evidence_sha256 == table.entries[4].evidence_sha256
    assert table.entries[0].probe_profile["model"] == "gpt-6-sol"
    assert table.entries[0].probe_profile["reasoning_or_effort"] == "medium"
    assert [entry.capability_profile for entry in table.entries] == [
        "codex-implementer", "antigravity", "claude", "antigravity", "claude",  # allowlist:provider -- certification data: hardened implementer profile
            "codex-reviewer", "codex-reviewer", "claude-implementer",  # allowlist:provider -- certification data: candidate profiles
    ]
    assert not hasattr(table.entries[0], "profile")


def test_reviewer_qualification_binds_restricted_transport_without_changing_codex() -> None:
    table = load_role_certifications()
    implementer, reviewer, final = (table.entries[i] for i in (0, 2, 4))
    assert len(implementer.capability_sha256) == 64
    assert implementer.rights_sha256 == binding_for("codex", AgentRoleName.IMPLEMENTER).rights_sha256  # allowlist:provider -- certification data: hardened boundary
    assert implementer.rights_sha256 != "a678bf59677c0f9d05040dfacd7e618d00e806cd30253b82c432cd2f117af3c8"
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


def test_agy_candidate_is_blocked_and_model_family_is_bound(tmp_path: Path) -> None:
    _copy_sources(tmp_path)
    _mutate(tmp_path, lambda doc: [row.update(status="candidate") for row in doc["certifications"]
                                   if row["provider"] == "antigravity"])
    table = load_role_certifications(root=tmp_path)
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
    promoted = load_role_certifications()
    for model in ("gemini-3.1-pro-high", "gemini-other"):
        for slot in (AgentSlot.REVIEWER, AgentSlot.FINAL_REVIEWER):
            assert promoted.require("antigravity", AgentRoleName.REVIEWER, slot, model=model).manufacturer == "google"
    assert set(promoted.require_occupancy(
        {AgentSlot.IMPLEMENTER: "codex", AgentSlot.REVIEWER: "antigravity",  # allowlist:provider -- certification data: promoted slot occupancy
         AgentSlot.FINAL_REVIEWER: "antigravity"},
        models={AgentSlot.IMPLEMENTER: "gpt-6-sol", AgentSlot.REVIEWER: "gemini-4-pro",
                AgentSlot.FINAL_REVIEWER: "gemini-4-pro"},
    )) == set(AgentSlot)
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


def test_agy_experimental_requires_both_canaries_and_separate_slot_entries(tmp_path: Path) -> None:
    _copy_sources(tmp_path)
    source = json.loads((tmp_path / CANARY_EVIDENCE).read_text())

    def publish(change: Callable[[dict[str, object]], object] | None = None,
                experimental_slots: tuple[str, ...] = ("reviewer", "final_reviewer")) -> None:
        doc = json.loads(json.dumps(source))
        if change is not None:
            change(doc)
        path = tmp_path / CANARY_EVIDENCE
        path.write_text(json.dumps(doc))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        # Publish a consistent derived bundle so this test exercises the
        # canary's domain contract, separately from byte-binding failures.
        capability_path = tmp_path / AGY_EVIDENCE
        capability = json.loads(capability_path.read_text())
        manifest_ref = capability['measurements']['redaction_manifest']
        manifest_path = tmp_path / manifest_ref['path']
        manifest = json.loads(manifest_path.read_text())
        next(item for item in manifest['files'] if item['path'] == path.name)['derived_sha256'] = digest
        manifest_path.write_text(json.dumps(manifest))
        manifest_ref['sha256'] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        capability_path.write_text(json.dumps(capability))
        capability_digest = hashlib.sha256(capability_path.read_bytes()).hexdigest()

        def update(table: dict[str, object]) -> None:
            for row in table["certifications"]:
                if row["provider"] == "antigravity":
                    row['evidence']['sha256'] = capability_digest
                    row["canary_evidence"]["sha256"] = digest
                    row["status"] = "experimental" if row["slot"] in experimental_slots else "candidate"

        _mutate(tmp_path, update)

    publish(lambda doc: doc.update(status="pending"))
    with pytest.raises(CertificationError, match="both AGY canaries"):
        load_role_certifications(root=tmp_path)
    publish(lambda doc: doc["slots"].pop("reviewer"))
    with pytest.raises(CertificationError, match="both AGY canaries"):
        load_role_certifications(root=tmp_path)
    publish(lambda doc: doc["slots"]["final_reviewer"].update(status="pending", proof=None))
    with pytest.raises(CertificationError, match="final_reviewer canary binding"):
        load_role_certifications(root=tmp_path)
    publish(lambda doc: doc["slots"]["reviewer"]["proof"]["checks"].update(effective_rights=False))
    with pytest.raises(CertificationError, match="reviewer canary checks"):
        load_role_certifications(root=tmp_path)
    publish(lambda doc: doc["slots"]["reviewer"]["proof"]["raw"].update(status="error"))
    with pytest.raises(CertificationError, match="reviewer canary raw proof differs"):
        load_role_certifications(root=tmp_path)
    publish(lambda doc: doc["slots"]["final_reviewer"]["proof"].update(
        request_id=doc["slots"]["reviewer"]["proof"]["request_id"]))
    with pytest.raises(CertificationError, match="reused"):
        load_role_certifications(root=tmp_path)
    publish(lambda doc: doc["slots"]["final_reviewer"]["proof"].pop("claude_slice_review_sha256"))  # allowlist:provider -- certification data: saved Claude review
    with pytest.raises(CertificationError, match="final canary lacks Claude slice evidence"):  # allowlist:provider -- certification data: saved Claude review
        load_role_certifications(root=tmp_path)
    publish(experimental_slots=("reviewer",))
    one_slot = load_role_certifications(root=tmp_path)
    assert one_slot.require("antigravity", AgentRoleName.REVIEWER, AgentSlot.REVIEWER,
                            model="gemini-4-pro").status == "experimental"
    with pytest.raises(CertificationError, match="missing qualification evidence"):
        one_slot.require("antigravity", AgentRoleName.REVIEWER, AgentSlot.FINAL_REVIEWER,
                         model="gemini-4-pro")
    # Branch review 29 Sep 2026: a later "certified" status must not skip the canary check.
    publish(lambda doc: doc.update(status="pending"))

    def certify(table: dict[str, object]) -> None:
        for row in table["certifications"]:
            if row["provider"] == "antigravity":
                row["status"] = "certified"

    _mutate(tmp_path, certify)
    with pytest.raises(CertificationError, match="both AGY canaries"):
        load_role_certifications(root=tmp_path)
    publish()
    table = load_role_certifications(root=tmp_path)
    for slot in (AgentSlot.REVIEWER, AgentSlot.FINAL_REVIEWER):
        assert table.require("antigravity", AgentRoleName.REVIEWER, slot,
                             model="gemini-4-pro").status == "experimental"
    with pytest.raises(CertificationError, match="slot=implementer provider=antigravity"):
        table.require("antigravity", AgentRoleName.IMPLEMENTER, AgentSlot.IMPLEMENTER,
                      model="gemini-4-pro")


def test_new_candidate_pairs_are_blocked_at_start_and_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = load_role_certifications()
    # Keep the immutable candidate-gate proof meaningful after promotion.
    table = CertificationTable(tuple(replace(entry, status="candidate")
        if entry.status == "experimental" and entry.capability_profile in {"codex-reviewer", "claude-implementer"}  # allowlist:provider -- certification data: test-only candidate downgrade
        else entry for entry in table.entries))
    selected = {
        AgentSlot.IMPLEMENTER: "claude", AgentSlot.REVIEWER: "codex",  # allowlist:provider -- certification data: candidate occupancy
        AgentSlot.FINAL_REVIEWER: "codex",  # allowlist:provider -- certification data: candidate occupancy
    }
    models = {
        AgentSlot.IMPLEMENTER: "opus", AgentSlot.REVIEWER: "gpt-6-sol",
        AgentSlot.FINAL_REVIEWER: "gpt-6-sol",
    }
    with pytest.raises(CertificationError, match="slot=implementer provider=claude.*missing qualification evidence"):  # allowlist:provider -- certification data: candidate rejection
        table.require_occupancy(selected, models=models)
    for slot in AgentSlot:
        with pytest.raises(CertificationError, match=f"slot={slot.value} provider={selected[slot]}.*missing qualification evidence"):
            table.require(selected[slot], AgentRoleName.IMPLEMENTER if slot is AgentSlot.IMPLEMENTER
                          else AgentRoleName.REVIEWER, slot, model=models[slot])
    monkeypatch.setattr("workflow_run_setup.load_role_certifications", lambda: table)
    settings = {slot.value: AgentSettings(selected[slot], selected[slot], models[slot], 600, "high")
                for slot in AgentSlot}
    persisted = SimpleNamespace(protocol_binding=SimpleNamespace(**{
        f"{slot.value}_profile": SimpleNamespace(provider=selected[slot], model=models[slot])
        for slot in AgentSlot
    }))
    with pytest.raises(StateSchemaError, match="AGENT-PROFILE-DIFF.*missing qualification evidence"):
        _apply_resumed_agent_profiles(Namespace(slot_settings=settings), persisted)
    assert binding_for("codex", AgentRoleName.IMPLEMENTER) is not binding_for_role(AgentRoleName.IMPLEMENTER)  # allowlist:provider -- certification data: baseline binding
    assert binding_for("claude", AgentRoleName.REVIEWER) is binding_for_role(AgentRoleName.REVIEWER)  # allowlist:provider -- certification data: baseline binding
    assert binding_for(selected[AgentSlot.REVIEWER], AgentRoleName.REVIEWER).permissions["profile"] == "dao-reviewer"


def test_generic_provider_canary_gates_candidate_experimental_and_certified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _copy_sources(tmp_path)
    capability_path = tmp_path / "schemas/native-provider-schema-capabilities-v2.json"
    capabilities = json.loads(capability_path.read_text())
    profile = json.loads(json.dumps(next(item for item in capabilities["providers"] if item["profile_id"] == "codex")))  # allowlist:provider -- certification data: fixture source profile
    profile["provider"] = profile["profile_id"] = "fiction"
    profile["transport_profile"]["provider"] = "fiction"
    profile["transport_profile"]["model"] = "sample-v1"
    capabilities["providers"].append(profile)
    capability_path.write_text(json.dumps(capabilities))
    evidence_path = "docs/evidence/fiction/capability-v1.json"
    evidence_file = tmp_path / evidence_path
    evidence_file.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / CANDIDATE_EVIDENCE, evidence_file)
    evidence_sha = hashlib.sha256(evidence_file.read_bytes()).hexdigest()
    source = json.loads((tmp_path / TABLE).read_text())["certifications"][5]
    row = json.loads(json.dumps(source))
    row.pop("canary_evidence", None)
    row.update(status="candidate", provider="fiction", manufacturer="sample-maker", capability_profile="fiction",
               probe_profile=profile["transport_profile"],
               capability_sha256=hashlib.sha256(json.dumps(profile, sort_keys=True,
                   separators=(",", ":")).encode()).hexdigest(),
               evidence={"path": evidence_path, "sha256": evidence_sha},
               model_family_pattern=r"^sample-[a-z0-9-]+$")
    row["rights_sha256"] = binding_for_role(AgentRoleName.REVIEWER).rights_sha256
    canary_path = "docs/evidence/fiction/role-canary-v1.json"
    request = {"request_id": "request-fiction-1"}
    writer = {"type": "object"}
    runtime_profile = {"provider": "fiction", "model": "sample-v1"}
    digest = lambda value: hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
    raw = {"request_id": request["request_id"], "status": "success",
           "evidence": {"request_document": request, "writer_schema": writer}}
    canary = {"schema_version": "role-canary-v1", "status": "passed", "slots": {
        "reviewer": {"provider": "fiction", "role": "reviewer", "slot": "reviewer",
                     "case": "sample-review", "transport_series": "sample-series", "status": "passed",
                     "proof": {"provider": "fiction", "role": "reviewer", "slot": "reviewer",
                               "case": "sample-review", "transport_series": "sample-series",
                               "request_id": request["request_id"], "request_sha256": digest(request),
                               "writer_sha256": digest(writer), "raw_sha256": digest(raw),
                               "profile_sha256": digest(runtime_profile), "model": "sample-v1",
                               "profile": runtime_profile,
                               "checks": {"writer": True, "domain": True, "effective_rights": True,
                                          "isolation_postcheck": True, "no_denials": True},
                               "raw": raw}}}}
    canary_file = tmp_path / canary_path

    def publish() -> None:
        canary_file.write_text(json.dumps(canary))
        row["canary_evidence"] = {"path": canary_path,
                                  "sha256": hashlib.sha256(canary_file.read_bytes()).hexdigest()}
        _mutate(tmp_path, lambda table: table["certifications"].__setitem__(-1, row))

    _mutate(tmp_path, lambda table: (table["provider_manufacturers"].update(fiction="sample-maker"),
                                      table["certifications"].append(row)))
    table = load_role_certifications(root=tmp_path)
    with pytest.raises(CertificationError, match="missing qualification evidence"):
        table.require("fiction", AgentRoleName.REVIEWER, AgentSlot.REVIEWER, model="sample-v1")
    row["status"] = "experimental"
    _mutate(tmp_path, lambda table: table["certifications"].__setitem__(-1, row))
    with pytest.raises(CertificationError, match="missing provider/role rights binding"):
        load_role_certifications(root=tmp_path)
    monkeypatch.setitem(role_binding._PAIR_BINDINGS, ("fiction", AgentRoleName.REVIEWER),
                        binding_for_role(AgentRoleName.REVIEWER))
    with pytest.raises(CertificationError, match="missing canary evidence"):
        load_role_certifications(root=tmp_path)
    publish()
    table = load_role_certifications(root=tmp_path)
    assert table.require("fiction", AgentRoleName.REVIEWER, AgentSlot.REVIEWER, model="sample-v2").status == "experimental"
    with pytest.raises(CertificationError, match="does not match manufacturer sample-maker family"):
        table.require("fiction", AgentRoleName.REVIEWER, AgentSlot.REVIEWER, model="other-v1")
    for field, value in (("status", "pending"), ("slot", "final_reviewer")):
        original = canary["slots"]["reviewer"][field]
        canary["slots"]["reviewer"][field] = value
        publish()
        with pytest.raises(CertificationError):
            load_role_certifications(root=tmp_path)
        canary["slots"]["reviewer"][field] = original
    canary["slots"]["reviewer"]["proof"]["raw"]["status"] = "error"
    publish()
    with pytest.raises(CertificationError, match="raw proof differs"):
        load_role_certifications(root=tmp_path)
    canary["slots"]["reviewer"]["proof"]["raw"]["status"] = "success"
    publish()
    row["status"] = "certified"
    publish()
    assert load_role_certifications(root=tmp_path).require(
        "fiction", AgentRoleName.REVIEWER, AgentSlot.REVIEWER, model="sample-v3").status == "certified"
    canary["status"] = "pending"
    publish()
    with pytest.raises(CertificationError, match="role canary did not pass"):
        load_role_certifications(root=tmp_path)
    canary["status"] = "passed"
    duplicate = json.loads(json.dumps(canary["slots"]["reviewer"]))
    duplicate["slot"] = duplicate["proof"]["slot"] = "final_reviewer"
    canary["slots"]["final_reviewer"] = duplicate
    publish()
    with pytest.raises(CertificationError, match="reused"):
        load_role_certifications(root=tmp_path)
    canary["slots"].pop("final_reviewer")
    canary["slots"]["reviewer"]["proof"]["writer_sha256"] = "0" * 64
    publish()
    with pytest.raises(CertificationError, match="request or writer proof differs"):
        load_role_certifications(root=tmp_path)
    canary["slots"]["reviewer"]["proof"]["writer_sha256"] = digest(writer)
    row["model_family_pattern"] = r"^other-[a-z0-9-]+$"
    publish()
    with pytest.raises(CertificationError, match="model family differs from capability profile"):
        load_role_certifications(root=tmp_path)


def test_every_evidence_node_id_exists_in_the_test_suite() -> None:
    _assert_node_ids_exist(json.loads((ROOT / EVIDENCE).read_text()))
    _assert_node_ids_exist(json.loads((ROOT / REVIEWER_EVIDENCE).read_text()))
    _assert_node_ids_exist(json.loads((ROOT / AGY_EVIDENCE).read_text()))
    _assert_node_ids_exist(json.loads((ROOT / CANDIDATE_EVIDENCE).read_text()))
    _assert_node_ids_exist(json.loads((ROOT / CODEX_REVIEW_CANDIDATE_EVIDENCE).read_text()))  # allowlist:provider -- certification data: reviewer proof


def test_runtime_certification_does_not_need_a_tests_directory(tmp_path: Path) -> None:
    _copy_sources(tmp_path)
    assert not (tmp_path / "tests").exists()
    assert len(load_role_certifications(root=tmp_path).entries) == 8


@pytest.mark.parametrize("change,code", [
    (lambda doc: doc["certifications"].pop(4), CertificationErrorCode.BASELINE_MISSING),
    (lambda doc: doc["certifications"].append(doc["certifications"][0]), CertificationErrorCode.DUPLICATE_ENTRY),
    (lambda doc: doc["certifications"][0].update(status="unknown"), CertificationErrorCode.ENTRY_INVALID),
    (lambda doc: doc["certifications"][0].update(status="experimental"), CertificationErrorCode.SOURCE_MISMATCH),
    (lambda doc: doc["certifications"][1]["canary_evidence"].update(sha256="0" * 64), CertificationErrorCode.EVIDENCE_INVALID),
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
    assert len(load_role_certifications(root=tmp_path).entries) == 8
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
    assert implementer.role_binding.permissions["profile"] == "dao-implementer"
    assert implementer.role_binding.permissions["sandbox_flag"] == "absent"
    assert reviewer.role_binding.permissions["allowed_tools"] == "Read"
    assert reviewer.role_binding.policy == binding_for_role(AgentRoleName.REVIEWER).policy
    assert implementer.role_binding.policy != reviewer.role_binding.policy

    def should_not_construct(*args: object, **kwargs: object) -> object:
        raise AssertionError("adapter was constructed")

    monkeypatch.setattr(agent_adapters, "NativeCodexAdapter", should_not_construct)
    table = CertificationTable(tuple(replace(entry, status="candidate")
        if entry.provider == "codex" and entry.role is AgentRoleName.REVIEWER else entry  # allowlist:provider -- certification data: explicit candidate gate
        for entry in table.entries))
    with pytest.raises(CertificationError, match="slot=reviewer provider=codex.*missing qualification evidence"):
        create_agent_pair("codex", AgentRoleName.REVIEWER, slot=AgentSlot.REVIEWER, settings=settings["codex"], certifications=table)


def test_import_has_no_global_registry() -> None:
    assert "AGENT_REGISTRY" not in vars(agent_adapters)


def test_agy_rows_keep_the_measured_gemini_family_binding() -> None:
    # Steering review of Slice C1: the Gemini binding moved from code into the table.
    # The AGY CLI also offers other vendors' models, so the data must stay pinned.
    table = json.loads((ROOT / TABLE).read_text())
    rows = [row for row in table["certifications"] if row["provider"] == "antigravity"]
    assert rows and all(row["manufacturer"] == "google" and
                        row["model_family_pattern"] == r"^gemini-[a-z0-9.-]+$" for row in rows)
