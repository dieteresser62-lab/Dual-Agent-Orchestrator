"""Provider-free preparation of the two candidate role canaries."""
import copy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from scripts import probe_reviewer as probe
from scripts.qualification import canary, prerequisites
from scripts.qualification.profiles import BOUNDARY_IMPLEMENTER, BOUNDARY_REVIEWER
from native_provider_schema import provider_capability
from role_certification import _validate_role_canaries
from agent_roles import AgentRoleName, AgentSlot


PROTOCOL = canary.DEFAULT_PROTOCOL
DECISIONS = PROTOCOL.parent / "operator-decisions-v1.json"


def test_codex_measured_size_exception_and_legacy_bytes():  # allowlist:provider -- certification data: candidate regression
    protocol = probe.read_evidence(PROTOCOL)
    pair = probe.qualification_pair(protocol)
    decisions = probe.read_evidence(DECISIONS)
    verdicts = {"codex-large-s1": {"failed_cases": ["512"], "kind": "large_output", "provider": pair.candidate}}  # allowlist:provider -- certification data: measured series
    assert probe.size_override(decisions, verdicts, pair, protocol)["failed_case"] == "512"
    directory = probe.qualification_pair({"schema_version": "qualification-protocol-v5"}).evidence_directory
    old_protocol = probe.read_evidence(directory / "qualification-protocol-v5.json")
    old_series = probe.read_evidence(directory / "qualification-series-v1.json")
    envelopes = probe.read_evidence(directory / "qualification-envelopes-v1.json.gz")
    old_verdicts = probe.validate_qualification_evidence(old_series, envelopes, old_protocol)
    old_decisions = probe.read_evidence(directory / "operator-decisions-v1.json")
    assert probe.canonical(probe.size_override(old_decisions, old_verdicts)) == probe.canonical(
        probe.size_override(old_decisions, old_verdicts, probe.qualification_pair(old_protocol), old_protocol))
    # The legacy v5 rule keeps its exact historical semantics; only the new v6
    # candidate exception is restricted to the latest measured size series.
    expected = probe.canonical(probe.size_override(old_decisions, old_verdicts))
    old_verdicts["later-large-series"] = {"kind": "large_output", "provider": probe.LEGACY_CANDIDATE,
                                        "failed_cases": ["128"]}
    assert probe.canonical(probe.size_override(old_decisions, old_verdicts,
        probe.qualification_pair(old_protocol), old_protocol)) == expected


@pytest.mark.parametrize("change", ("not_run", "failed_case", "measured", "reference", "duplicate", "pair", "new-series"))
def test_size_exception_rejects_unbound_decisions(change):
    protocol = probe.read_evidence(PROTOCOL)
    pair = probe.qualification_pair(protocol)
    decisions = copy.deepcopy(probe.read_evidence(DECISIONS))
    row = decisions["decisions"][0]
    verdicts = {row["series"]: {"failed_cases": ["512"], "kind": "large_output", "provider": pair.candidate}}
    if change == "not_run":
        row["not_run"] = ["512"]
    elif change == "failed_case":
        row["failed_case"] = "128"
    elif change == "measured":
        verdicts[row["series"]]["failed_cases"] = ["128", "512"]
    elif change == "reference":
        verdicts[row["series"]]["provider"] = pair.reference
    elif change == "duplicate":
        decisions["decisions"].append(copy.deepcopy(row))
    elif change == "new-series":
        verdicts["later-large-series"] = copy.deepcopy(verdicts[row["series"]])
    else:
        pair = probe.qualification_pair({"schema_version": "qualification-protocol-v5"})
    with pytest.raises(ValueError):
        probe.size_override(decisions, verdicts, pair, protocol)


@pytest.mark.parametrize("slot", ("reviewer", "final_reviewer", "implementer"))
def test_default_command_does_not_read_profile_or_start(slot, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(canary, "run", lambda *a, **k: pytest.fail("provider start without --live"))
    profile, output = tmp_path / "missing.json", tmp_path / "missing/output.json"
    assert canary.main([slot, "--profile", str(profile), "--out", str(output)]) == 0
    assert "--live" in capsys.readouterr().out
    assert not output.parent.exists()
    with pytest.raises(PermissionError, match="--live"):
        # The original live gate is tested separately from the main double.
        ORIGINAL_RUN(slot, profile_file=profile, output=output)


ORIGINAL_RUN = canary.run


def profile_file(tmp_path, monkeypatch, capability):
    provider = provider_capability(capability)["provider"]
    binary = tmp_path / provider
    binary.write_text("fake executable")
    binary.chmod(0o700)
    profile = {"live": True, "commit_sha": "c" * 40, "binary": str(binary),
               "model": "opus" if capability == BOUNDARY_IMPLEMENTER else "gpt-6.1-sol",
               "effort": "high", "timeout_seconds": 600}
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile))
    monkeypatch.setattr(probe, "_current_commit", lambda: "c" * 40)
    monkeypatch.setattr(probe, "_assert_committed_qualification_code", lambda: None)
    return path, provider


@pytest.mark.parametrize("outcome", ("passed", "foreign-id", "outside-write", "process-error"))
def test_implementer_canary_uses_product_adapter_and_validates_binding(tmp_path, monkeypatch, outcome):
    import agent_runtime
    profile, provider = profile_file(tmp_path, monkeypatch, BOUNDARY_IMPLEMENTER)
    monkeypatch.setattr(agent_runtime, "verify_agent_capabilities", lambda *a, **k: None)
    monkeypatch.setattr(agent_runtime, "_bound_launch_command", lambda adapter, command: list(command))
    calls = []
    def fake_process(adapter, command, stdin, **kwargs):
        calls.append(command)
        root = kwargs["execution_root"]
        request = json.loads(stdin)
        assert "--restricted" in command
        assert request["target_branch"] == "feature/boundary-fixture"
        if outcome == "process-error":
            raise RuntimeError("fake process failed")
        (root / "positive-write.txt").write_text("CANARY_OK\n")
        if outcome == "outside-write":
            (root.parent.parent / "decoys/new.txt").write_text("forbidden write")
        result = {"schema_version": "native-agent-implementer-result-v3", "request_id": request["request_id"],
                  "result_type": "implementation_result", "ready": True, "test_files": [], "finding_dispositions": []}
        if outcome == "foreign-id":
            result["request_id"] = "foreign-request"
        stream = json.dumps({"type": "result", "subtype": "success", "is_error": False,
                             "structured_output": {"result": result}})
        return subprocess.CompletedProcess(command, 0, stream, "")
    monkeypatch.setattr(agent_runtime, "_run_agent_process", fake_process)
    output = tmp_path / "role-canary-v1.json"
    report = canary.run("implementer", profile_file=profile, output=output, private_dir=tmp_path / "private", live=True)
    assert len(calls) == 1
    assert report["status"] == ("passed" if outcome == "passed" else "failed"), report
    assert list((tmp_path / "private").glob("*/profile.json"))
    if outcome != "process-error":
        assert list((tmp_path / "private").glob("*/provider-output.json"))
    if outcome == "passed":
        _validate_role_canaries(report, provider=provider, role=AgentRoleName.IMPLEMENTER,
                               slot=AgentSlot.IMPLEMENTER, model_family_pattern=None)
    assert "/home/" not in output.read_text()


def test_reviewer_canaries_emit_certification_format_and_keep_direct_proof(tmp_path, monkeypatch):
    profile, provider = profile_file(tmp_path, monkeypatch, BOUNDARY_REVIEWER)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    for name in ("qualification-series-v1.json", "qualification-envelopes-v1.json", "quality-results-v1.json", "operator-decisions-v1.json"):
        (evidence / name).write_text("{}")
    monkeypatch.setattr(probe, "qualification_summary", lambda *a: {"qualified_for_canary": True})
    gates = []
    monkeypatch.setattr(prerequisites, "verify", lambda protocol, completion: gates.append(completion))
    calls = []
    def direct(slot, **kwargs):
        calls.append(slot)
        case = "F4" if slot == "reviewer" else "F5"
        request_id = "bound-" + slot
        evidence = {"request_document": {"request_id": request_id}, "writer_schema": {},
                    "envelope": {"raw_last_message": json.dumps({"result": {"request_id": request_id}})}}
        raw = {"request_id": request_id, "status": "success", "evidence": evidence}
        report = {"case": case, "proof": {"raw": raw, "checks": dict.fromkeys(
            ("writer", "domain", "effective_rights", "isolation_postcheck", "no_denials"), True)}}
        probe._write_evidence_file(kwargs["output_dir"] / "direct-report.json", report)
        return report
    monkeypatch.setattr(probe, "run_canary_call", direct)
    output = tmp_path / "role-canary-v1.json"
    for slot in ("reviewer", "final_reviewer"):
        report = canary.run(slot, profile_file=profile, output=output, evidence_dir=evidence,
            phase0_results=tmp_path / "phase0-results.json", private_dir=tmp_path / "private", live=True)
        _validate_role_canaries(report, provider=provider, role=AgentRoleName.REVIEWER,
                               slot=AgentSlot(slot), model_family_pattern=None)
    assert calls == ["reviewer", "final_reviewer"] and len(gates) == 2
    assert len(list((tmp_path / "private").glob("*/direct-report.json"))) == 2
    with pytest.raises(FileExistsError):
        canary.run("reviewer", profile_file=profile, output=output, live=True)


def test_public_canary_rejects_personal_paths():
    with pytest.raises(ValueError, match="personal path"):
        canary.assert_public({"rationale": "/home/operator/project"})


def test_public_canary_rejects_operator_username(monkeypatch):
    monkeypatch.setenv("USER", "private-operator")
    with pytest.raises(ValueError, match="personal path"):
        canary.assert_public({"rationale": "Reported by private-operator."})


def test_reviewer_role_canaries_run_native_codex_adapter_with_fake_process(tmp_path, monkeypatch):  # allowlist:provider -- transport: actual candidate path
    import agent_runtime
    from tests.test_codex_review_adapter import _entry  # allowlist:provider -- transport: fake verified package
    profile, provider = profile_file(tmp_path, monkeypatch, BOUNDARY_REVIEWER)
    entry = _entry(tmp_path / "package")
    entry.chmod(0o700)
    binary = tmp_path / provider
    binary.unlink()
    binary.symlink_to(entry)
    factory = probe._qualification_adapter
    def prepared(*args, **kwargs):
        adapter = factory(*args, **kwargs)
        adapter.provider_identity = SimpleNamespace(entry_path=str(entry), kind="verified",
                                                    digest="fake", launch_prefix=(str(binary),))
        return adapter
    monkeypatch.setattr(probe, "_qualification_adapter", prepared)
    monkeypatch.setattr(agent_runtime, "run_local_command", lambda c: (0, json.dumps({"models": [{"slug": "gpt-6.1-sol"}]}), ""))
    monkeypatch.setattr(agent_runtime, "verify_agent_capabilities", lambda *a, **k: None)
    monkeypatch.setattr(agent_runtime, "_bound_launch_command", lambda adapter, command: list(command))
    monkeypatch.setattr(probe, "qualification_summary", lambda *a: {"qualified_for_canary": True})
    monkeypatch.setattr(prerequisites, "verify", lambda *a: None)
    fixture = probe.read_evidence(probe.ROOT / "tests/fixtures/reviewer-format-s6-v1.json")
    monkeypatch.setattr(probe, "_validated_reference_slice_review", lambda *a: (
        fixture["cases"]["F3"]["envelope"]["structured_output"]["result"], "f" * 64))
    started = []
    def process(adapter, command, stdin, **kwargs):
        case = "F4" if not started else "F5"
        started.append(case)
        assert any("model_catalog_json=" in part for part in command)
        result = copy.deepcopy(fixture["cases"][case]["envelope"]["structured_output"]["result"])
        result["request_id"] = adapter.invocation.request_id
        adapter.invocation.last_message_file.write_text(json.dumps({"result": result}))
        return subprocess.CompletedProcess(command, 0, '{"type":"turn.completed"}\n', "")
    monkeypatch.setattr(agent_runtime, "_run_agent_process", process)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    for name in ("qualification-series-v1.json", "qualification-envelopes-v1.json", "quality-results-v1.json", "operator-decisions-v1.json"):
        (evidence / name).write_text("{}")
    output = tmp_path / "canaries.json"
    for slot in ("reviewer", "final_reviewer"):
        report = canary.run(slot, profile_file=profile, output=output, evidence_dir=evidence,
                            private_dir=tmp_path / "private", live=True)
        assert report["status"] == "passed", report
        _validate_role_canaries(report, provider=provider, role=AgentRoleName.REVIEWER,
                               slot=AgentSlot(slot), model_family_pattern=None)
    assert started == ["F4", "F5"]
    assert len(list((tmp_path / "private").glob("*/canary-*-v1.json"))) == 2
