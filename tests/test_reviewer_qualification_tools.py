"""Provider-free regression for reusable reviewer qualification controls."""
from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import probe_reviewer as probe
from scripts.qualification import blind, campaign, make_matrix, run_format, run_probe
from native_review_contract import parse_bound_native_contract_result


EVIDENCE = Path(__file__).resolve().parents[1] / "docs/evidence/antigravity"


def _historical():
    return (probe.read_evidence(EVIDENCE / "qualification-protocol-v5.json"),
            probe.read_evidence(EVIDENCE / "qualification-series-v1.json"),
            probe.read_evidence(EVIDENCE / "qualification-envelopes-v1.json.gz"),
            probe.read_evidence(EVIDENCE / "quality-results-v1.json"),
            probe.read_evidence(EVIDENCE / "operator-decisions-v1.json"))


def test_fake_pair_reuses_plan_series_retries_blind_rating_and_final_verdict(tmp_path: Path) -> None:
    protocol, series, envelopes, quality, decisions = _historical()
    historical = probe.qualification_summary(copy.deepcopy(series), envelopes, protocol, quality, decisions)
    evidence = tmp_path / "fake-evidence"
    evidence.mkdir()
    for name in ("phase-0-v1.json", "quality-rubric-v1.json"):
        (evidence / name).write_bytes((EVIDENCE / name).read_bytes())
    protocol = copy.deepcopy(protocol)
    protocol.update(schema_version="qualification-protocol-v6",
                    candidate_provider="fake-candidate", reference_provider="fake-reference",
                    provider_profiles={"fake-candidate": "antigravity", "fake-reference": "claude"},  # allowlist:provider -- certification data: fake pair mapped to shipped transport
                    provider_runtime={"fake-candidate": {"model": "gemini-3.1-pro-high",
                                                         "effort": "high", "isolation": True},
                                      "fake-reference": {"model": "opus", "effort": "high",
                                                         "isolation": False}},
                    evidence_directory=str(evidence))
    protocol["quality"]["providers"] = ["fake-candidate", "fake-reference"]
    probe.validate_qualification(protocol)
    plan = probe.probe_plan(protocol)
    assert plan[0]["series"] == "transport"
    assert plan[-3]["candidate_provider"] == "fake-candidate"
    assert probe.qualification_cases("quality", "fake-reference", probe.qualification_pair(protocol))[-1] == "Q6"
    for row in series["attempts"]:
        row["provider"] = {"agy": "fake-candidate", "claude": "fake-reference"}[row["provider"]]  # allowlist:provider -- certification data: historical evidence projection
    verdicts = probe.validate_qualification_evidence(series, envelopes, protocol)
    assert verdicts["agy-transport-s3"]["passed"] == historical["series"]["agy-transport-s3"]["passed"]
    assert verdicts["agy-quality-s4"]["production_retries"] == 1
    assert verdicts["agy-quality-s3"]["superseded_corpus"]
    assert verdicts["agy-quality-s3"]["contract_rejections"] == ["agy-quality-s3-Q1"]
    assert verdicts["agy-quality-s3"]["production_retries"] == 1
    sources = probe.quality_blind_sources(series, envelopes, protocol)
    pair = probe.qualification_pair(protocol)
    assessment, mapping = probe.blind_package(sources, seed=protocol["quality"]["blind_seed"],
        provider_words=(*pair.providers, *pair.capabilities.values()))
    corpus = probe.read_evidence(probe.ROOT / "tests/fixtures/reviewer-quality-corpus-v1.json")
    rubric = probe.read_evidence(evidence / "quality-rubric-v1.json")
    packet = probe.export_rater_packet(assessment, mapping, corpus, protocol, rubric)
    assert packet["packet_sha256"] == quality["packet_sha256"]
    answers = {"schema_version": "quality-operator-answers-v1", "answers": quality["operator_decisions"]}
    rated = probe.combine_quality_ratings(packet, mapping, quality["ratings"], corpus, protocol, answers)
    rated["reference_special_decision"] = "approve_experimental"
    assert probe.grade_quality(rated, corpus, protocol)["fake-candidate"]["passed"]
    final = probe.qualification_summary(series, envelopes, protocol, rated, decisions)
    assert final["qualified_for_canary"] == historical["qualified_for_canary"] is True
    assert final["size_qualification"] == historical["size_qualification"] == "operator_override"


def test_campaign_retries_only_production_retryable_and_binds_restart(tmp_path: Path) -> None:
    calls = []
    protocol = EVIDENCE / "qualification-protocol-v5.json"
    def fake(command):
        calls.append(command)
        if "prepare-case" in command:
            return {}
        identifier = command[command.index("--call-id") + 1]
        if identifier.endswith("-r1"):
            return {"call_id": identifier, "status": "success", "checks": {"domain": True}}
        return {"call_id": identifier, "status": "technical_rejection", "failure_kind": "network",
                "production_retryable": True}
    with pytest.raises(PermissionError, match="--live"):
        campaign.run_block(kind="transport", provider="agy", series_id="fake-series",
            profile=tmp_path / "profile", protocol=protocol, evidence_dir=tmp_path / "evidence",
            source_dir=tmp_path / "sources", cases=("F1:1",))
    rows = campaign.run_block(kind="transport", provider="agy", series_id="fake-series",
        profile=tmp_path / "profile", protocol=protocol, evidence_dir=tmp_path / "evidence",
        source_dir=tmp_path / "sources", cases=("F1:1",),
        restart_diagnosis="network interruption", restart_change="new adapter revision", invoke=fake)
    assert len(rows) == 2
    first = next(command for command in calls if "qualification-call" in command)
    second = [command for command in calls if "qualification-call" in command][1]
    assert "--restart-diagnosis" in first and "--restart-change" in first
    assert "--production-retry-of" in second and "--restart-diagnosis" not in second
    contract_calls = []
    def contract(command):
        contract_calls.append(command)
        if "prepare-case" in command:
            return {}
        identifier = command[command.index("--call-id") + 1]
        if identifier.endswith("-r1"):
            return {"call_id": identifier, "status": "success", "checks": {"domain": True}}
        return {"call_id": identifier, "status": "technical_rejection", "failure_kind": "output",
                "contract_rejection": "approval-invalid", "contract_retryable": True,
                "production_retryable": True}
    contract_rows = campaign.run_block(kind="quality", provider="agy", series_id="contract",
        profile=tmp_path / "profile", protocol=protocol, evidence_dir=tmp_path / "evidence",
        source_dir=tmp_path / "sources", cases=("Q1",), invoke=contract)
    assert len(contract_rows) == 2
    assert "--production-retry-of" in [c for c in contract_calls if "qualification-call" in c][1]
    def denied(command):
        if "prepare-case" in command:
            return {}
        return {"call_id": command[command.index("--call-id") + 1],
                "status": "technical_rejection", "failure_kind": "auth",
                "production_retryable": False}
    with pytest.raises(campaign.CampaignStopped, match="non-retryable"):
        campaign.run_block(kind="transport", provider="agy", series_id="denied",
            profile=tmp_path / "profile", protocol=protocol, evidence_dir=tmp_path / "evidence",
            source_dir=tmp_path / "sources", cases=("F1:1",), invoke=denied)


def test_blind_export_separates_mapping_and_live_rating_is_guarded(tmp_path: Path) -> None:
    report = blind.prepare(protocol_path=EVIDENCE / "qualification-protocol-v5.json",
        series_path=EVIDENCE / "qualification-series-v1.json",
        envelopes_path=EVIDENCE / "qualification-envelopes-v1.json.gz",
        output_dir=tmp_path / "rating")
    assert report["responses"] == 12
    assert (tmp_path / "rating/private/mapping.json").exists()
    packet = json.loads((tmp_path / "rating/packets/steering-packet.json").read_text())
    assert all("provider" not in row for row in packet["responses"])
    binary = tmp_path / "fake-binary"
    binary.write_text("# fake only\n")
    options = {"binary": binary, "prompt": tmp_path / "rating/packets/codex-prompt.txt",  # allowlist:provider -- certification data: independent rater packet
               "output": tmp_path / "rating/result.json", "events": tmp_path / "rating/events.jsonl",
               "stderr": tmp_path / "rating/stderr.txt", "private": tmp_path / "rating/private"}
    command = blind.codex_command(**options)  # allowlist:provider -- certification data: independent rater command
    assert "-s read-only" in command and "chmod 000" in command
    with pytest.raises(PermissionError, match="--live"):
        blind.run_codex(**options)  # allowlist:provider -- certification data: independent rater gate
    assert "{packet}" not in blind.steering_prompt((tmp_path / "rating/packets/steering-packet.json").resolve(),
                                                     (tmp_path / "rating/steering.json").resolve())


def test_phase0_catalog_profiles_and_live_gates(tmp_path: Path) -> None:
    matrix = make_matrix.matrix()
    assert set(matrix["cases"]) == {f"P{number}" for number in range(1, 7)}
    assert "--restricted" in matrix["profiles"]["claude"]["cli_flags"]  # allowlist:provider -- profile configuration: measured reference protection
    assert "command(*)" in matrix["profiles"]["agy"]["deny_rules"]
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"binary": str(tmp_path / "missing"), "model": "fake"}))
    with pytest.raises(PermissionError, match="--live"):
        run_probe.run_case(case_id="P1", profile_name="agy", profile_file=profile,
                           output=tmp_path / "phase0")
    with pytest.raises(PermissionError, match="--live"):
        run_format.run_format(case="F1", provider="agy",
            protocol_file=EVIDENCE / "qualification-protocol-v5.json",
            profile_file=profile, source=tmp_path / "source", output=tmp_path / "format")


@pytest.mark.parametrize("profile_name", ["agy", "claude"])  # allowlist:provider -- profile configuration: both measured protection profiles
def test_phase0_fake_process_checks_snapshot_and_decoy_reads(tmp_path: Path, profile_name: str) -> None:
    fake = tmp_path / "fake-provider"
    fake.write_text(
        "#!/bin/sh\n# dao-probe-fake-v1\n"
        "if [ \"$DAO_P0_CASE\" = P6 ] && [ \"$DAO_P0_SOFT_DENIAL\" = true ]; then\n"
        "  printf '%s\\n' '{\"status\":\"SUCCESS\",\"denied_actions\":[\"read denied\"]}'\n"
        "  printf '%s\\n' 'auto-denied' >&2\n"
        "else\n"
        "  printf '%s\\n' '{\"status\":\"SUCCESS\",\"is_error\":false,\"structured_output\":{\"positive_control\":\"PHASE0_POSITIVE\",\"tools_available\":\"Read\",\"loaded_instructions\":\"none\",\"attempts\":[]}}'\n"
        "fi\n")
    fake.chmod(0o755)
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"binary": str(fake), "model": "fake",
                                   "home": str(tmp_path / "isolated-home"),
                                   "run_root": str(tmp_path / "run-root")}))
    for case in ("P1", "P5", "P6"):
        destination = tmp_path / f"{profile_name}-{case}"
        result = run_probe.run_case(case_id=case, profile_name=profile_name,
            profile_file=profile, output=destination, fake_root=tmp_path)
        assert result["passed"] and all(result["checks"].values())
        assert not (destination / "decoys").resolve().is_relative_to((destination / "allowed").resolve())
        assert not (destination / "sibling.txt").resolve().is_relative_to((destination / "allowed").resolve())


def test_format_probe_uses_registered_adapter_with_fake_result(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from antigravity_adapter import NativeAntigravityReviewAdapter
    monkeypatch.setattr(NativeAntigravityReviewAdapter, "extract_output", lambda self, *args: None)

    binary = tmp_path / "fake-binary"
    binary.write_text("# fake only\n")
    profile = tmp_path / "profile.toml"
    profile.write_text(f'binary = "{binary}"\nmodel = "gemini-3.1-pro-high"\n'
                       'effort = "high"\n[provider_options.antigravity]\n'
                       f'home = "{tmp_path / "home"}"\nrun_root = "/var/tmp/dao-fake-format"\n')
    source = tmp_path / "source"
    probe.materialize_format_repo("F2", source)
    fixture = probe.read_evidence(probe.ROOT / "tests/fixtures/reviewer-format-s6-v1.json")
    def fake(adapter, bundle, **_kwargs):
        assert isinstance(adapter, NativeAntigravityReviewAdapter)
        response = dict(fixture["cases"]["F2"]["envelope"]["structured_output"]["result"],
                        request_id=bundle.bound_context.request_id)
        envelope = {"status": "SUCCESS", "json_schema": probe.strict_json(bundle.provider_response_schema_json),
                    "structured_output": {"result": response}, "denied_actions": []}
        adapter.extract_output(json.dumps(envelope), "", {"exit_code": "0"})
        return SimpleNamespace(result=parse_bound_native_contract_result(response, bundle.bound_context))
    report = run_format.run_format(case="F2", provider="agy",
        protocol_file=EVIDENCE / "qualification-protocol-v5.json",
        profile_file=profile, source=source, output=tmp_path / "format", invoke=fake)
    assert report["passed"] and all(report["checks"].values())


def test_fake_pair_canary_and_quicktest_use_candidate_profile(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_runtime
    from antigravity_adapter import NativeAntigravityReviewAdapter

    protocol = probe.read_evidence(EVIDENCE / "qualification-protocol-v5.json")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    for name in ("phase-0-v1.json", "quality-rubric-v1.json"):
        (evidence / name).write_bytes((EVIDENCE / name).read_bytes())
    protocol.update(schema_version="qualification-protocol-v6",
                    candidate_provider="fake-candidate", reference_provider="fake-reference",
                    provider_profiles={"fake-candidate": "antigravity", "fake-reference": "claude"},  # allowlist:provider -- certification data: fake pair transports
                    provider_runtime={"fake-candidate": {"model": "gemini-3.1-pro-high",
                                                         "effort": "high", "isolation": True},
                                      "fake-reference": {"model": "opus", "effort": "high",
                                                         "isolation": False}},
                    evidence_directory=str(evidence))
    protocol["quality"]["providers"] = ["fake-candidate", "fake-reference"]
    protocol_file = tmp_path / "protocol.json"
    protocol_file.write_text(json.dumps(protocol))
    binary = tmp_path / "fake-binary"
    binary.write_text("# fake only\n")
    profile = tmp_path / "profile.toml"
    profile.write_text(f'live = true\ncommit_sha = "{"c"*40}"\nbinary = "{binary}"\n'
                       'model = "gemini-3.1-pro-high"\neffort = "high"\ntimeout_seconds = 0\n'
                       '[provider_options.antigravity]\n'
                       f'home = "{tmp_path / "home"}"\nrun_root = "/var/tmp/dao-fake-canary"\n')
    monkeypatch.setattr(probe, "_current_commit", lambda: "c" * 40)
    monkeypatch.setattr(probe, "_assert_committed_qualification_code", lambda: None)
    monkeypatch.setattr(NativeAntigravityReviewAdapter, "extract_output", lambda self, *args: None)
    fixture = probe.read_evidence(probe.ROOT / "tests/fixtures/reviewer-format-s6-v1.json")
    denied_state = {"enabled": False}
    def fake(adapter, bundle, **_kwargs):
        case = "F2" if "quicktest" in bundle.canonical_json else "F4"
        response = dict(fixture["cases"][case]["envelope"]["structured_output"]["result"],
                        request_id=bundle.bound_context.request_id)
        envelope = {"status": "SUCCESS", "json_schema": probe.strict_json(bundle.provider_response_schema_json),
                    "structured_output": {"result": response}, "denied_actions": [],
                    "permission_denials": ["blocked"] if denied_state["enabled"] else []}
        adapter.extract_output(json.dumps(envelope), "", {"exit_code": "0"})
        return SimpleNamespace(result=parse_bound_native_contract_result(response, bundle.bound_context))
    monkeypatch.setattr(agent_runtime, "run_native_review_agent", fake)
    for quick in (False, True):
        report = probe.run_canary_call("reviewer", profile_file=profile,
            output_dir=tmp_path / ("quick" if quick else "canary"),
            protocol_file=protocol_file, live=True, quicktest=quick)
        assert report["status"] == "passed" and report["provider"] == "fake-candidate"
        assert report["capability_profile"] == "antigravity"
    original_envelope_profile = probe.REVIEW_ENVELOPES["antigravity"]
    monkeypatch.setitem(probe.REVIEW_ENVELOPES, "antigravity",
        replace(original_envelope_profile, denials_field="permission_denials"))
    denied_state["enabled"] = True
    denied = probe.run_canary_call("reviewer", profile_file=profile,
        output_dir=tmp_path / "canary-denied", protocol_file=protocol_file, live=True)
    assert denied["proof"]["checks"]["no_denials"] is False
