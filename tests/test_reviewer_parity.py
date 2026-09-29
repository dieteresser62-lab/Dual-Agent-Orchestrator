"""Provider-free parity of bound requests and domain outcomes."""
from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import probe_reviewer as probe
from artifact_models import ArtifactValidationError, FindingSeverity, FindingTransitionPayload, Role
from contracts import AgentRole, FindingOrigin
from native_review_contract import parse_bound_native_contract_result
from native_review_request import (build_native_review_request,
                                   validate_native_review_provider_response)
from native_provider_schema import AGY_PROVIDER


FIXTURE = Path(__file__).parent / "fixtures/reviewer-format-s6-v1.json"


def _bound(case: str, profile: str):
    spec = probe.spec_for(case)
    bundle = build_native_review_request(spec, profile=profile)
    stored = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"][case]["envelope"]
    response = copy.deepcopy(stored["structured_output"]["result"])
    response["request_id"] = bundle.bound_context.request_id
    validate_native_review_provider_response(response, bundle)
    result = parse_bound_native_contract_result(response, bundle.bound_context)
    return bundle, response, result


@pytest.mark.parametrize("left,right", [
    ("codex", "claude"), ("codex", AGY_PROVIDER),  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
    ("claude", AGY_PROVIDER),  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
])
def test_convergence_final_discovery_and_stop_are_domain_equal(left: str, right: str) -> None:
    for case, transition in (("F2", "review_approved"), ("F3", "review_denied"),
                             ("F4", "review_approved"), ("F5", "final_discovery"),
                             ("F6", "stop")):
        left_bundle, _, left_result = _bound(case, left)
        right_bundle, _, right_result = _bound(case, right)
        assert left_bundle.bound_context.request_id != right_bundle.bound_context.request_id
        actual = probe.domain_projection(left_bundle, left_result)
        assert actual == probe.domain_projection(right_bundle, right_result)
        assert actual["transition"] == transition
        assert actual["operation"] == probe.spec_for(case).context.operation
        assert actual["evidence"]
        assert actual["base_commit"] == probe.spec_for(case).base_commit
        if case == "F4":
            assert actual["round_number"] == 2
            assert actual["closures"] == (("R-01", "fixed"),)
            assert actual["known_findings"]
        if case == "F5":
            assert actual["scan_complete"] is True
            assert actual["findings"]
        if case == "F6":
            # Historical stored F6 remains a known semantic failure, never relabeled.
            assert actual["stop_rule"] == "CONTRACT-UNCLEAR"


def test_claude_slice_and_agy_final_slot_keep_separate_request_bindings() -> None:  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
    slice_bundle, _, slice_result = _bound("F4", "claude")  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
    final_bundle, _, final_result = _bound("F5", AGY_PROVIDER)
    slice_projection = probe.domain_projection(slice_bundle, slice_result)
    final_projection = probe.domain_projection(final_bundle, final_result)
    assert slice_projection["transition"] == "review_approved"
    assert final_projection["transition"] == "final_discovery"
    assert slice_projection["operation"] == "reviewer_slice_review"
    assert final_projection["operation"] == "reviewer_final_review"
    assert slice_bundle.bound_context.request_id != final_bundle.bound_context.request_id


def test_foreign_request_id_cannot_be_replayed_across_provider_or_resume() -> None:
    claude_bundle, response, _ = _bound("F4", "claude")  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
    agy_bundle, _, _ = _bound("F4", AGY_PROVIDER)
    with pytest.raises(Exception):
        parse_bound_native_contract_result(response, agy_bundle.bound_context)
    # The writer may accept the shape; the bound domain parser owns identity.
    validate_native_review_provider_response(response, agy_bundle)
    assert parse_bound_native_contract_result(response, claude_bundle.bound_context)  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
    # Exact resume rebuilds the same request; a different invocation rejects replay.
    rebuilt = build_native_review_request(probe.spec_for("F4"), profile="claude")  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
    assert rebuilt.bound_context.request_id == claude_bundle.bound_context.request_id  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
    second = parse_bound_native_contract_result(response, rebuilt.bound_context)
    assert probe.domain_projection(claude_bundle, second) == probe.domain_projection(  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
        claude_bundle, parse_bound_native_contract_result(response, claude_bundle.bound_context))  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
    moved = replace(probe.spec_for("F4"), context=replace(
        probe.context_for("F4"), run_id="new-invocation"))
    changed = build_native_review_request(moved, profile="claude")  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
    assert changed.bound_context.request_id != rebuilt.bound_context.request_id
    with pytest.raises(Exception):
        parse_bound_native_contract_result(response, changed.bound_context)


def test_stop_result_cannot_become_approval_on_another_provider() -> None:
    bundle, response, result = _bound("F6", "claude")  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
    assert result.stopped and result.approval is None
    forged = dict(response, result_type="review_result", decision="approved")
    with pytest.raises(Exception):
        parse_bound_native_contract_result(forged, bundle.bound_context)


def test_record_payload_rejects_non_reviewer_finding_closure() -> None:
    with pytest.raises(ArtifactValidationError, match="only the reporting reviewer"):
        FindingTransitionPayload(
            finding_id="R-01", reporter=Role.REVIEWER, actor=Role.IMPLEMENTER,
            action="status_changed", severity=FindingSeverity.FINDING,
            finding_status="closed", rationale="Foreign closure", work_unit_id="slice-1")
    with pytest.raises(ValueError, match="reporter must be reviewer"):
        FindingOrigin("1", 1, AgentRole.IMPLEMENTER)


def test_qualification_size_override_is_explicit_and_bound_to_failed_case() -> None:
    base = probe.ROOT / "docs/evidence/antigravity"
    series = probe.read_evidence(base / "qualification-series-v1.json")
    envelopes = probe.read_evidence(base / "qualification-envelopes-v1.json.gz")
    protocol = probe.read_evidence(base / "qualification-protocol-v5.json")
    quality = probe.read_evidence(base / "quality-results-v1.json")
    decisions = probe.read_evidence(base / "operator-decisions-v1.json")
    accepted = probe.qualification_summary(series, envelopes, protocol, quality, decisions)
    assert accepted["qualified_for_canary"] is True
    assert accepted["size_qualification"] == "operator_override"
    assert accepted["size_override"]["series"] == "agy-large-s1"
    assert accepted["size_override"]["failed_case"] == "128"
    assert accepted["size_override"]["documented_weakness"]
    assert "timeout_proposal_pending" in accepted and "timeout_proposal" not in accepted
    missing = copy.deepcopy(decisions)
    missing["decisions"] = [row for row in missing["decisions"] if row["id"] != "size-override"]
    assert not probe.qualification_summary(series, envelopes, protocol, quality, missing)["qualified_for_canary"]
    manipulated = copy.deepcopy(decisions)
    next(row for row in manipulated["decisions"] if row["id"] == "size-override")["failed_case"] = "512"
    with pytest.raises(ValueError, match="size override"):
        probe.qualification_summary(series, envelopes, protocol, quality, manipulated)


@pytest.mark.parametrize("slot,case", [("reviewer", "F4"), ("final_reviewer", "F5")])
def test_direct_canary_uses_bound_adapter_and_saved_claude_review(  # allowlist:provider -- certification data: saved Claude review
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slot: str, case: str) -> None:
    import agent_runtime
    from antigravity_adapter import NativeAntigravityReviewAdapter
    binary = tmp_path / "fake-agy"
    binary.write_text("#!/bin/sh\n# dao-probe-fake-v1\n", encoding="utf-8")
    profile = tmp_path / "agy.toml"
    profile.write_text(f'live = true\ncommit_sha = "{"c"*40}"\nbinary = "{binary}"\n'
                       'model = "gemini-3.1-pro-high"\neffort = "high"\ntimeout_seconds = 0\n'
                       '[provider_options.antigravity]\n'
                       f'home = "{tmp_path / "home"}"\nrun_root = "/var/tmp/dao-canary-test"\n')
    monkeypatch.setattr(probe, "_current_commit", lambda: "c" * 40)
    monkeypatch.setattr(probe, "_assert_committed_qualification_code", lambda: None)
    monkeypatch.setattr(NativeAntigravityReviewAdapter, "extract_output", lambda self, *args: None)
    stored_cases = json.loads(FIXTURE.read_text())["cases"]

    def fake_run(adapter, bundle, **kwargs):
        assert isinstance(adapter, NativeAntigravityReviewAdapter)
        assert kwargs["operation"] == ("reviewer_final_review" if slot == "final_reviewer" else "reviewer_slice_review")
        assert ("claude_slice_review" in bundle.canonical_json) == (slot == "final_reviewer")  # allowlist:provider -- certification data: saved Claude review
        selected_case = "F2" if "quicktest" in bundle.canonical_json else case
        response = dict(stored_cases[selected_case]["envelope"]["structured_output"]["result"],
                        request_id=bundle.bound_context.request_id)
        envelope = {"status": "SUCCESS", "json_schema": probe.strict_json(bundle.provider_response_schema_json),
                    "structured_output": {"result": response}, "denied_actions": []}
        adapter.extract_output(json.dumps(envelope), "", {"exit_code": "0"})
        return SimpleNamespace(result=parse_bound_native_contract_result(response, bundle.bound_context))

    monkeypatch.setattr(agent_runtime, "run_native_review_agent", fake_run)
    with pytest.raises(PermissionError, match="--live"):
        probe.run_canary_call(slot, profile_file=profile, output_dir=tmp_path / "unused")
    report = probe.run_canary_call(slot, profile_file=profile, output_dir=tmp_path / "out", live=True)
    assert report["status"] == "passed"
    assert all(report["proof"]["checks"].values())
    assert report["proof"]["timeout_seconds"] == 0
    if slot == "final_reviewer":
        assert len(report["proof"]["claude_slice_review_sha256"]) == 64  # allowlist:provider -- certification data: saved Claude review
    else:
        quick = probe.run_canary_call(slot, profile_file=profile,
            output_dir=tmp_path / "quick", live=True, quicktest=True)
        assert quick["mode"] == "quicktest" and quick["status"] == "passed"
        assert quick["proof"]["timeout_seconds"] == 240
    with pytest.raises(FileExistsError, match="already"):
        probe.run_canary_call(slot, profile_file=profile, output_dir=tmp_path / "out", live=True)
