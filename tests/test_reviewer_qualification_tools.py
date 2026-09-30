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
                    raters=["codex", "steering"],  # allowlist:provider -- certification data: neutral raters for fake AGY candidate
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
                    raters=["codex", "steering"],  # allowlist:provider -- certification data: neutral raters for fake AGY candidate
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


CANDIDATE_PROTOCOL = probe.ROOT / "docs/evidence/codex/qualification-protocol-v6.json"  # allowlist:provider -- certification data: candidate protocol


def test_codex_v6_protocol_binds_frozen_pair_cases_and_thresholds() -> None:  # allowlist:provider -- certification data: candidate protocol
    protocol = probe.read_evidence(CANDIDATE_PROTOCOL)
    probe.validate_qualification(protocol)
    pair = probe.qualification_pair(protocol)
    assert pair.providers == ("codex", "claude")  # allowlist:provider -- certification data: qualification pair
    assert pair.capability(pair.candidate) == "codex-reviewer"  # allowlist:provider -- profile configuration: candidate writer
    assert probe.qualification_raters(protocol) == ("agy", "steering")
    assert [len(probe.qualification_cases(kind, provider, pair))
            for kind, provider in probe._series_keys(pair)] == [12, 2, 1, 6, 6]
    old = probe.read_evidence(EVIDENCE / "qualification-protocol-v5.json")
    for name in ("transport", "large_output", "limits", "slots", "sample_counts", "format_regression_sha256"):
        assert protocol[name] == old[name]
    assert protocol["fixed_before_qualification"]
    assert protocol["quality"]["corpus_revision"] == 2
    assert protocol["quality"]["corpus_sha256"] == old["quality"]["corpus_sha256"]
    assert "runtime deadline" in protocol["print_timeout"]["mechanism"]
    assert "DISCOVERY_OUTPUT_LIMIT" in protocol["request_fidelity"]
    assert probe._review_budget(pair.capability(pair.candidate)).occupancy == (
        ("implementer", "implementer", "claude"),  # allowlist:provider -- profile configuration: measured topology budget
        ("reviewer", "reviewer", "codex"), ("final_reviewer", "reviewer", "codex"))  # allowlist:provider -- profile configuration: measured topology budget


@pytest.mark.parametrize("raters", [
    ["codex", "steering"], ["codex-reviewer", "steering"],  # allowlist:provider -- certification data: self-rating negative controls
    ["agy", "agy"], ["agy"], ["agy", "unknown"], "agy,steering", [[], "steering"],
])
def test_v6_rejects_non_neutral_or_invalid_raters(raters) -> None:
    protocol = probe.read_evidence(CANDIDATE_PROTOCOL)
    protocol["raters"] = protocol["quality"]["raters"] = raters
    with pytest.raises(ValueError):
        probe.validate_qualification(protocol)


def test_steering_is_anthropic_and_profile_aliases_cannot_hide_manufacturer() -> None:
    protocol = probe.read_evidence(CANDIDATE_PROTOCOL)
    protocol["candidate_provider"] = "candidate-alias"
    protocol["provider_profiles"] = {"candidate-alias": "claude", "claude": "codex-reviewer"}  # allowlist:provider -- profile configuration: manufacturer alias negative control
    with pytest.raises(ValueError, match="manufacturer"):
        probe.qualification_raters(protocol)


def test_v6_prepare_and_quality_judgments_use_configured_neutral_raters(tmp_path, monkeypatch) -> None:
    protocol = probe.read_evidence(CANDIDATE_PROTOCOL)
    old, series, envelopes, quality, _ = _historical()
    sources = probe.quality_blind_sources(series, envelopes, old)
    for source in sources:
        if source["provider"] == "agy":
            source["provider"] = "codex"  # allowlist:provider -- certification data: provider-free response remapping
    monkeypatch.setattr(probe, "quality_blind_sources", lambda *args: sources)
    out = tmp_path / "blind"
    report = blind.prepare(protocol_path=CANDIDATE_PROTOCOL, series_path=EVIDENCE / "qualification-series-v1.json",
        envelopes_path=EVIDENCE / "qualification-envelopes-v1.json.gz", output_dir=out)
    assert report["responses"] == 12
    assert (out / "packets/agy-packet.json").read_bytes() == (out / "packets/steering-packet.json").read_bytes()
    assert not (out / "packets/codex-packet.json").exists()  # allowlist:provider -- certification data: no candidate rating packet
    packet = probe.read_evidence(out / "packets/agy-packet.json")
    assert "provider" not in packet["responses"][0]
    schema = probe.read_evidence(out / "packets/agy-rating-schema.json")
    assert schema["properties"]["rater"]["const"] == "agy"
    assert schema["properties"]["packet_sha256"]["const"] == packet["packet_sha256"]
    ratings = {"agy": copy.deepcopy(quality["ratings"]["codex"]),  # allowlist:provider -- certification data: historical judgments remapped to neutral rater
               "steering": copy.deepcopy(quality["ratings"]["steering"])}
    for name, rating in ratings.items():
        rating.update(rater=name, packet_sha256=packet["packet_sha256"])
    corpus = probe.read_evidence(probe.ROOT / "tests/fixtures/reviewer-quality-corpus-v1.json")
    mapping = probe.read_evidence(out / "private/mapping.json")
    results = probe.combine_quality_ratings(packet, mapping, ratings, corpus, protocol,
        {"schema_version": "quality-operator-answers-v1", "answers": quality["operator_decisions"]})
    results["reference_special_decision"] = "approve_experimental"
    assert probe.grade_quality(results, corpus, protocol)["codex"]["passed"]  # allowlist:provider -- certification data: candidate grade
    assert all(set(row) >= {"agy", "steering"} for row in results["agreement"])
    assert all(set(reasons) == {"agy", "steering"}
               for row in results["judgments"] for reasons in row["reasons"].values())


def test_v5_exported_packets_and_prompt_are_byte_identical(tmp_path) -> None:
    protocol, series, envelopes, quality, _ = _historical()
    sources = probe.quality_blind_sources(series, envelopes, protocol)
    assessment, mapping = probe.blind_package(sources, seed=protocol["quality"]["blind_seed"])
    corpus = probe.read_evidence(probe.ROOT / "tests/fixtures/reviewer-quality-corpus-v1.json")
    rubric = probe.read_evidence(EVIDENCE / "quality-rubric-v1.json")
    packet = probe.export_rater_packet(assessment, mapping, corpus, protocol, rubric)
    assert packet["packet_sha256"] == quality["packet_sha256"]
    blind.prepare(protocol_path=EVIDENCE / "qualification-protocol-v5.json",
        series_path=EVIDENCE / "qualification-series-v1.json",
        envelopes_path=EVIDENCE / "qualification-envelopes-v1.json.gz", output_dir=tmp_path / "out")
    expected = (json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    for name in probe.qualification_raters(protocol):
        assert (tmp_path / f"out/packets/{name}-packet.json").read_bytes() == expected
    prompt = rubric["codex_prompt"] + "\n\nBlindpaket:\n" + json.dumps(packet, ensure_ascii=False, indent=2) + "\n"  # allowlist:provider -- certification data: original v5 prompt bytes
    assert (tmp_path / "out/packets/codex-prompt.txt").read_bytes() == prompt.encode()  # allowlist:provider -- certification data: historical prompt path


def test_agy_rater_command_prints_without_side_effects_and_start_needs_live(tmp_path, monkeypatch) -> None:
    packets = tmp_path / "packets"
    packets.mkdir()
    prompt, schema = packets / "prompt.txt", packets / "schema.json"
    prompt.write_text("blind input")
    schema.write_text("{}")
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    options = dict(binary=tmp_path / "fake-binary", prompt=prompt, schema=schema,
                   output=tmp_path / "rating.json", events=tmp_path / "events.json",
                   stderr=tmp_path / "stderr.txt", private=private,
                   home=tmp_path / "isolated-home", run_root=Path("/var/tmp/dao-blind-rater-test"))
    def forbidden(*args, **kwargs):
        raise AssertionError("provider-free print/gate must not execute")
    monkeypatch.setattr(blind.subprocess, "run", forbidden)
    monkeypatch.setattr(blind.subprocess, "Popen", forbidden)
    before = sorted(p.name for p in tmp_path.iterdir())
    command = blind.agy_command(**options)
    assert "run-agy" in command and "--live" in command
    assert str(options["home"]) in command and str(schema) in command
    with pytest.raises(PermissionError, match="--live"):
        blind.run_agy(**options)
    assert not options["home"].exists() and private.stat().st_mode & 0o777 == 0o700
    assert sorted(p.name for p in tmp_path.iterdir()) == before


@pytest.mark.parametrize("word", ["OpenAI", "Anthropic", "Google", "ChatGPT", "GPT-6.1", "Sonnet", "Opus", "Codex", "AGY", "Gemini"])  # allowlist:provider -- certification data: self-identification aliases

def test_v6_blind_scan_flags_provider_and_model_aliases(word) -> None:
    from scripts.qualification.profiles import BLIND_WORDS
    assessment, _ = probe.blind_package([{"provider": "candidate", "case": "Q1",
        "content": {"summary": f"I am {word}"}}], seed=1, provider_words=BLIND_WORDS)
    assert assessment["responses"][0]["possible_self_identification"] is True


@pytest.mark.parametrize("case,outcome", [
    *((case, "success") for case in ("F1", "F2", "F3", "F4", "F5", "F6")),
    *(("F2", outcome) for outcome in ("invalid", "missing", "refusal", "contract", "network", "permission", "process", "timeout", "timeout-case")),
])
def test_codex_measurement_last_message_runs_real_adapter_with_fake_process(tmp_path, monkeypatch, case, outcome):  # allowlist:provider -- transport: candidate measurement
    import subprocess
    import agent_runtime
    from agent_adapters import create_reviewer_qualification_adapter
    from agent_config import AgentSettings
    from codex_review_adapter import NativeCodexReviewAdapter  # allowlist:provider -- transport: candidate adapter
    from tests.test_codex_review_adapter import _entry  # allowlist:provider -- transport: fake installed package

    entry = _entry(tmp_path)
    binary = entry.with_name("codex")  # allowlist:provider -- transport: CLI executable name
    binary.write_text("fake executable")
    profile = tmp_path / "profile.toml"
    profile.write_text(f'live = true\ncommit_sha = "{"c" * 40}"\nbinary = "{binary}"\n'
                       'model = "gpt-6.1-sol"\neffort = "high"\ntimeout_seconds = 600\n')
    if outcome == "timeout-case":
        profile.write_text(profile.read_text().replace("timeout_seconds = 600", "timeout_seconds = 1"))
    adapter = create_reviewer_qualification_adapter(AgentSettings("codex", str(binary), "gpt-6.1-sol", 1 if outcome == "timeout-case" else 600, "high"))  # allowlist:provider -- profile configuration: native candidate fake identity
    adapter.provider_identity = SimpleNamespace(entry_path=str(entry), kind="verified")
    assert isinstance(adapter, NativeCodexReviewAdapter)  # allowlist:provider -- transport: registration assertion
    factory = probe._qualification_adapter
    def prepared(*args, **kwargs):
        built = factory(*args, **kwargs)
        assert isinstance(built, NativeCodexReviewAdapter)  # allowlist:provider -- transport: real factory resolution
        return adapter
    monkeypatch.setattr(probe, "_qualification_adapter", prepared)
    monkeypatch.setattr(probe, "_current_commit", lambda: "c" * 40)
    monkeypatch.setattr(probe, "_assert_committed_qualification_code", lambda: None)
    monkeypatch.setattr(agent_runtime, "verify_agent_capabilities", lambda *args, **kwargs: None)
    monkeypatch.setattr(agent_runtime, "_bound_launch_command", lambda current, command: list(command))
    response = copy.deepcopy(probe.read_evidence(probe.ROOT / "tests/fixtures/reviewer-format-s6-v1.json")["cases"][case]["envelope"]["structured_output"]["result"])
    if case == "F6":
        response["rule_id"] = probe.EXPECTED_FORMAT[case]["rule_id"]
        response["rationale"] = "The discovery capacity is exhausted; partial results cannot be authoritative."
    def process(current, command, stdin, **kwargs):
        response["request_id"] = current.invocation.request_id
        assert "--sandbox" not in command and "--output-last-message" in command
        if outcome in {"timeout", "timeout-case"}:
            raise subprocess.TimeoutExpired(command, 1 if outcome == "timeout-case" else 600)
        if outcome == "contract":
            response["decision"] = "approved"
            response["new_findings"] = copy.deepcopy(probe.read_evidence(probe.ROOT / "tests/fixtures/reviewer-format-s6-v1.json")["cases"]["F3"]["envelope"]["structured_output"]["result"]["new_findings"])
            response["new_findings"][0]["finding_class"] = "BLOCKER"
        if outcome != "missing":
            value = ("not JSON" if outcome == "invalid" else
                     json.dumps({"refusal": "declined"}) if outcome == "refusal" else
                     json.dumps({"result": response}))
            current.invocation.last_message_file.write_text(value)
        events = ('{"type":"turn.failed","error":"network connection failed"}' if outcome == "network" else
                  '{"permission_denials":["denied"]}' if outcome == "permission" else
                  '{"type":"turn.completed"}\n')
        return subprocess.CompletedProcess(command, 1 if outcome == "process" else 0,
                                           events, "execution error" if outcome == "process" else "")
    monkeypatch.setattr(agent_runtime, "_run_agent_process", process)
    source = tmp_path / "repo"
    probe.materialize_format_repo(case, source)
    # Start at this frozen case only to exercise each envelope/context independently.
    monkeypatch.setattr(probe, "_preflight_series_position", lambda *args, **kwargs: None)
    result = probe.run_qualification_call(kind="print_timeout" if outcome == "timeout-case" else "transport",
        case_id="T1" if outcome == "timeout-case" else case + ":1", provider="codex",  # allowlist:provider -- certification data: candidate series
        series_id="s1", call_id="c1", profile_file=profile, source_repo=source,
        output_dir=tmp_path / "out", protocol_file=CANDIDATE_PROTOCOL, live=True)
    if outcome != "success":
        assert result["status"] == "technical_rejection"
        expected = {"invalid": "output", "missing": "output", "refusal": "output", "contract": "output",
                    "network": "network", "permission": "permission", "process": "process", "timeout": "timeout", "timeout-case": "timeout"}
        assert result["failure_kind"] == expected[outcome]
        assert result["production_retryable"] is (outcome in {"network", "timeout", "contract"})
        if outcome == "timeout-case":
            assert result["checks"] == {"print_timeout": True, "no_valid_stop": True}
        if outcome == "contract":
            assert result["contract_rejection"] == "schema-invalid"
        return
    assert result["status"] == "success" and all(result["checks"].values()), (tmp_path / "out/qualification-envelopes-v1.json").read_text()
    envelope = probe.read_evidence(tmp_path / "out/qualification-envelopes-v1.json")["envelopes"][0]["envelope"]["envelope"]
    assert envelope["structured_output"]["result"]["request_id"] == result["request_id"]
    assert envelope["process_events"] == '{"type":"turn.completed"}'
    assert result["output_bytes"] == len(envelope["raw_last_message"].encode())
    if case == "F1":
        probe.validate_qualification_evidence(probe.read_evidence(tmp_path / "out/qualification-series-v1.json"),
            probe.read_evidence(tmp_path / "out/qualification-envelopes-v1.json"), probe.read_evidence(CANDIDATE_PROTOCOL))
    if case == "F2":
        with pytest.raises(PermissionError, match="--live"):
            probe.run_canary_call("reviewer", profile_file=profile,
                output_dir=tmp_path / "quick", quicktest=True, protocol_file=CANDIDATE_PROTOCOL)
        quick = probe.run_canary_call("reviewer", profile_file=profile, output_dir=tmp_path / "quick",
            quicktest=True, protocol_file=CANDIDATE_PROTOCOL, live=True)
        assert quick["status"] == "passed" and quick["mode"] == "quicktest"
        monkeypatch.setattr(probe.sys, "argv", ["probe_reviewer.py", "quicktest", "--candidate", "codex",  # allowlist:provider -- certification data: new candidate CLI
            "--profile", str(profile)])
        with pytest.raises(PermissionError, match="--live"):
            probe.main()


@pytest.mark.parametrize("last,events,exit_code,stderr", [
    ("", "", 0, ""), ("invalid JSON", "", 0, ""),
    ('{"result":{}}', '{"type":"turn.failed","error":"network"}', 1, ""),
    ('{"result":{}}', '{"permission_denials":["denied"]}', 0, ""),
    ('{"result":{}}', "", 0, "timed out"),
])
def test_codex_measured_envelope_rejects_missing_malformed_error_or_denial(tmp_path, last, events, exit_code, stderr):  # allowlist:provider -- transport: envelope negative controls
    from native_review_request import build_native_review_request
    bundle = build_native_review_request(probe.build_qualification_spec("transport", "F2:1", run_id="fake"), profile="codex-reviewer")  # allowlist:provider -- profile configuration: measured writer
    path = tmp_path / "last.json"
    if last:
        path.write_text(last)
    adapter = SimpleNamespace(invocation=SimpleNamespace(last_message_file=path))
    raw = {}
    from agent_adapters import AgentOutputError
    from agent_runtime import AgentProcessError
    if events or not last or last == "invalid JSON":
        with pytest.raises((AgentOutputError, AgentProcessError)):
            probe.capture_review_output(adapter, raw, events, stderr, {"exit_code": str(exit_code)}, "codex-reviewer")  # allowlist:provider -- transport: failed process capture
    else:
        probe.capture_review_output(adapter, raw, events, stderr, {"exit_code": str(exit_code)}, "codex-reviewer")  # allowlist:provider -- transport: last-message capture
    result = probe.validate_format_response("F2", probe.strict_json(raw["stdout"]), bundle=bundle,
        exit_code=exit_code, stderr=stderr, transport_profile="codex-reviewer")  # allowlist:provider -- transport: measured envelope
    assert result["pass"] is False


@pytest.mark.parametrize("postcheck_fails", [False, True])
def test_agy_rater_uses_production_read_only_transport_and_accepts_after_postcheck(tmp_path, monkeypatch, postcheck_fails):
    from contextlib import contextmanager
    import agent_adapters
    packets = tmp_path / "packets"
    packets.mkdir()
    prompt, schema = packets / "prompt.txt", packets / "schema.json"
    prompt.write_text("blind prompt")
    writer = {"type": "object", "properties": {"rater": {"type": "string", "const": "agy"}},
              "required": ["rater"], "additionalProperties": False}
    schema.write_text(json.dumps(writer))
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    binary = tmp_path / "fake-binary"
    binary.write_text("fake only")
    output = tmp_path / "rating.json"
    observed = []
    class FakeBoundary:
        prepared_workspace = SimpleNamespace(repo=packets, log_file=tmp_path / "log")
        @contextmanager
        def review_execution_boundary(self, source, manifest):
            assert source == packets and manifest == ("prompt.txt", "schema.json")
            assert private.stat().st_mode & 0o777 == 0
            observed.append("boundary")
            yield
            assert not output.exists()
            observed.append("postcheck")
            if postcheck_fails:
                raise RuntimeError("integrity postcheck failed")
        def seal_provider_input(self):
            observed.append("seal")
        def before_provider_process(self):
            observed.append("start")
        def _isolated_environment(self):
            return {"HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin"}
    def factory(settings, role_binding):
        assert settings.antigravity_home == str(tmp_path / "home")
        assert "independent blind rater" in role_binding.policy
        return FakeBoundary()
    monkeypatch.setattr(agent_adapters, "create_reviewer_qualification_adapter", factory)
    def process(command, *, env, cwd, out, limit, cleanup_limit):
        assert command[command.index("--agent") + 1] == "dao-reviewer"
        assert command[command.index("--json-schema") + 1] == str(schema)
        assert "--sandbox" in command and "--disable-slash-commands" in command
        assert command[command.index("--output-format") + 1] == "json"
        assert env["HOME"] == str(tmp_path / "home") and limit == 600 and cleanup_limit == 15
        assert observed == ["boundary", "seal", "start"]
        out.mkdir()
        envelope = {"status": "SUCCESS", "json_schema": writer, "structured_output": {"rater": "agy"}}
        (out / "stdout.txt").write_text(json.dumps(envelope))
        (out / "stderr.txt").write_text("")
        return {"timed_out": False, "remaining_identities": [], "survivor_identities": [], "exit_code": 0}
    monkeypatch.setattr(probe, "run", process)
    options = dict(binary=binary, prompt=prompt, schema=schema, output=output,
                   events=tmp_path / "events.json", stderr=tmp_path / "stderr.txt", private=private,
                   home=tmp_path / "home", run_root=Path("/var/tmp/dao-blind-rater-test"), live=True)
    if postcheck_fails:
        with pytest.raises(RuntimeError, match="postcheck"):
            blind.run_agy(**options)
        assert not output.exists()
    else:
        assert blind.run_agy(**options) == 0
        assert probe.read_evidence(output) == {"rater": "agy"}
    assert observed == ["boundary", "seal", "start", "postcheck"]
    assert private.stat().st_mode & 0o777 == 0o700
