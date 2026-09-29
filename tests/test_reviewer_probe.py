"""Offline regression and qualification controls for the reviewer candidate."""
from __future__ import annotations

import copy
import difflib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tokenize

import pytest

from scripts import probe_reviewer as probe

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/evidence/antigravity"
FIXTURES = ROOT / "tests/fixtures"


def load(path: Path):
    return probe.strict_json(path.read_bytes())


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_phase0_gate_has_every_call_control_and_bound_digest() -> None:
    phase = load(EVIDENCE / "phase-0-v1.json")
    probe.validate_phase0(phase)
    assert sum(row["total_tokens"] or 0 for row in phase["calls"]
               if row["call"].startswith("agy-")) == 2_115_470
    assert phase["frozen"]["FROZEN-v4.txt"] == (
        "720271ea083a7dd1b160ed1f52f8f13f5d1f80b2220a634b05c4dc97d1fc8f94"
    )
    assert phase["gate"]["shared_protection_failure"] is False
    assert "--restricted" in phase["gate"]["claude_follow_up_rule"]  # allowlist:provider -- historical wire proof: hardening 135
    assert phase["effective_configuration_template"]["settings"]["toolPermission"] == "request-review"
    assert phase["effective_configuration_template"]["agent"]["inheritCustomizations"] is False
    assert phase["quota"]["subscription_quota_measured"] is False
    assert phase["quota"]["daily_cli_total_tokens"] == {"2026-09-28": 2_115_470}
    assert phase["individual_controls"]["customization_markers"]["GEMINI.md"] == {
        "loaded": False, "effect": False, "source": "s4/agy-P1"
    }


def test_phase0_missing_controls_and_masked_failure_rejected() -> None:
    phase = load(EVIDENCE / "phase-0-v1.json")
    broken = copy.deepcopy(phase)
    broken["calls"] = [x for x in broken["calls"]
                       if (x["series"], x["call"]) != ("s4", "agy-P3")]
    with pytest.raises((AssertionError, KeyError)):
        probe.validate_phase0(broken)
    broken = copy.deepcopy(phase)
    f6 = next(x for x in broken["calls"] if x["series"] == "s6" and x["call"] == "agy-F6")
    f6["verdict"] = "pass"
    with pytest.raises(AssertionError):
        probe.validate_phase0(broken)
    broken = copy.deepcopy(phase)
    s1 = next(x for x in broken["calls"] if x["series"] == "s1")
    s1["verdict"] = "pass"
    with pytest.raises(AssertionError):
        probe.validate_phase0(broken)
    broken = copy.deepcopy(phase)
    broken["individual_controls"]["customization_markers"]["GEMINI.md"]["loaded"] = True
    with pytest.raises(AssertionError):
        probe.validate_phase0(broken)


def test_protection_plan_and_fake_evaluation(tmp_path: Path) -> None:
    assert [row["case"] for row in probe.protection_plan()] == [
        "P1", "P2", "P3", "P4", "P5", "P6"]
    before = probe.capture({"workspace": tmp_path})
    normal = {"status": "SUCCESS", "denied_actions": [],
              "structured_output": {"positive_control": "read", "loaded_instructions": "none"}}
    for case in ("P1", "P2", "P3", "P4", "P5"):
        assert probe.evaluate_protection(case, before=before, after=before,
            envelope=normal, stderr="", forbidden_processes=[], nonce_hits=[])["pass"]
    soft = {"status": "SUCCESS", "denied_actions": [{"action": "read_file"}]}
    assert probe.evaluate_protection("P6", before=before, after=before,
        envelope=soft, stderr="auto-denied", forbidden_processes=[], nonce_hits=[])["pass"]
    (tmp_path / "escaped").write_text("changed")
    after = probe.capture({"workspace": tmp_path})
    assert not probe.evaluate_protection("P1", before=before, after=after,
        envelope=normal, stderr="", forbidden_processes=[], nonce_hits=[])["pass"]
    assert not probe.evaluate_protection("P6", before=before, after=before,
        envelope={"status": "SUCCESS", "structured_output": {}}, stderr="",
        forbidden_processes=[], nonce_hits=[])["pass"]


def test_qualification_protocol_counts_rules_slots_and_evidence_digests() -> None:
    protocol = load(EVIDENCE / "qualification-protocol-v1.json")
    probe.validate_qualification(protocol)
    assert protocol["quality"]["corpus_sha256"] == sha(FIXTURES / "reviewer-quality-corpus-v1.json")
    assert protocol["phase0_sha256"] == sha(EVIDENCE / "phase-0-v1.json")
    assert protocol["format_regression_sha256"] == sha(FIXTURES / "reviewer-format-s6-v1.json")
    plan = probe.probe_plan(protocol)
    assert [sum(x["series"] == key for x in plan) for key in
            ("transport", "large_output", "print_timeout", "quality", "canary")] == [12, 2, 1, 6, 2]
    assert protocol["slots"]["reviewer"]["canary"] == "reviewer"
    assert protocol["slots"]["final_reviewer"]["canary"] == "final_reviewer"
    assert protocol["quality_rule"]["critical_required"] == 2
    assert protocol["quality_rule"]["defects_required"] == 3
    assert "keine Einzelretakes oder Filter" in protocol["restart_rule"]


def test_s6_stored_envelopes_against_current_writer_domain_and_case_semantics() -> None:
    phase = load(EVIDENCE / "phase-0-v1.json")
    assert sha(FIXTURES / "reviewer-format-repo-v1.json") == phase["format_fixture_sha256"]
    fixtures = load(FIXTURES / "reviewer-format-s6-v1.json")
    assert fixtures["schema_version"] == "reviewer-format-s6-v1"
    assert set(fixtures["cases"]) == set(probe.EXPECTED_FORMAT)
    for case, item in fixtures["cases"].items():
        assert item["source_sha256"] == phase["source_digests"][
            f"runs/s6/agy-{case}/process/stdout.txt"]
        result = probe.validate_format_response(case, item["envelope"],
            exit_code=item["exit_code"], stderr=item["stderr"])
        assert result["checks"]["writer_schema"] is True
        assert result["checks"]["domain_contract"] is True
        assert result["pass"] is (case != "F6")
        if case == "F6":
            assert result["domain_stop_rule"] == "CONTRACT-UNCLEAR"
            assert [name for name, ok in result["checks"].items() if not ok] == ["case_semantics"]
        else:
            assert result["checks"]["case_semantics"] is True


def test_format_cases_materialize_the_frozen_independent_repository(tmp_path: Path) -> None:
    for case in probe.EXPECTED_FORMAT:
        work = tmp_path / case
        probe.materialize_format_repo(case, work)
        assert (work / "docs/plan.md").is_file()
        assert (work / "src/limit.py").is_file()
        assert (work / "tests/test_limit.py").is_file()
    assert "return n < 10" in (tmp_path / "F3/src/limit.py").read_text()
    assert "src/zero.py" in (tmp_path / "F6/docs/plan.md").read_text()
    with pytest.raises(FileExistsError):
        probe.materialize_format_repo("F6", tmp_path / "F6")


@pytest.mark.parametrize("mutation,failed_check", [
    ("soft_deny", "structured_output_object"),
    ("error_with_result", "status_success"),
    ("schema_echo_type", "schema_echo"),
    ("denied_actions", "no_denials"),
    ("boundary_rule", "writer_schema"),
])
def test_stored_negative_envelope_shapes_fail_closed(mutation: str, failed_check: str) -> None:
    fixture = load(FIXTURES / "reviewer-format-s6-v1.json")["negative_envelopes"][mutation]
    assert fixture["expected_failed_check"] == failed_check
    sources = {"soft_deny": "runs/s4/agy-P6/process/stdout.txt",
               "error_with_result": "runs/s4/agy-F1/process/stdout.txt",
               "boundary_rule": "runs/s5/agy-F6/process/stdout.txt"}
    if mutation in sources:
        assert fixture["source_sha256"] == load(EVIDENCE / "phase-0-v1.json")[
            "source_digests"][sources[mutation]]
    result = probe.validate_format_response(fixture["case"], fixture["envelope"],
                                            exit_code=fixture["exit_code"],
                                            stderr=fixture["stderr"])
    assert result["pass"] is False
    assert result["checks"][failed_check] is False


def test_quality_corpus_is_pending_and_independently_reproducible(tmp_path: Path) -> None:
    assert (FIXTURES / "reviewer-quality-corpus-v1.json").stat().st_size < 1_048_576
    corpus = load(FIXTURES / "reviewer-quality-corpus-v1.json")
    assert corpus["operator_review"] == "pending"
    assert not probe.qualification_ready(
        load(EVIDENCE / "qualification-protocol-v1.json"), corpus)
    assert len(corpus["cases"]) == 6
    assert sum(c["defect"] for c in corpus["cases"]) == 4
    assert {c["id"] for c in corpus["cases"] if c["critical"]} == {"Q1", "Q2"}
    assert {c["review_kind"] for c in corpus["cases"]} == {
        "plan", "slice", "convergence", "final_review"}
    for case in corpus["cases"]:
        snapshot = case["candidate_files"]
        control = case["clean_control"]
        seed = case["seed_files"]
        assert set(snapshot) == set(control) == set(seed)
        assert case["snapshot_paths"] == sorted(snapshot)
        assert 80 <= case["productive_lines"] <= 250
        assert case["productive_lines"] == sum(
            len(content.splitlines()) for name, content in snapshot.items()
            if name.startswith("mini/") and name.endswith(".py"))
        assert sum(name.startswith("mini/") and name.endswith(".py") for name in snapshot) >= 3
        assert case["patch"].startswith("--- a/mini/")
        changed = [name for name in seed if seed[name] != snapshot[name]]
        assert len(changed) == 1
        name = changed[0]
        assert case["patch"] == "".join(difflib.unified_diff(
            seed[name].splitlines(keepends=True), snapshot[name].splitlines(keepends=True),
            fromfile="a/" + name, tofile="b/" + name))
        assert case["review_context"]["evidence"]["diff"] == case["patch"]
        assert case["review_context"]["binding"]["diff_fingerprint"] == hashlib.sha256(
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode()).hexdigest()
        assert case["review_context"]["visible_validation"]["status"] == "passed"
        assert case["review_context"]["acceptance_criteria"]
        assert case["review_context"]["authorized_paths"]
        assert case["review_context"]["binding"]["operation"] == {
            "plan": "reviewer_plan_review",
            "slice": "reviewer_slice_review",
            "convergence": "reviewer_slice_review",
            "final_review": "reviewer_final_review",
        }[case["review_kind"]]
        assert case["review_context"]["expected"]["assessment"] == (
            "defect" if case["defect"] else "clean")
        assert case["review_context"]["expected"]["factual_finding"] == case["factual_finding"]
        if case["review_kind"] == "convergence":
            assert case["review_context"]["previous_finding"]["owner"] == "reviewer"
            assert case["review_context"]["binding"]["round_number"] == 2
            assert case["review_context"]["expected"]["status_changes"] == [
                {"finding_id": "R-17", "status": "CLOSED"}]
        else:
            assert case["review_context"]["previous_finding"] is None
            assert case["review_context"]["expected"]["status_changes"] == []
        if case["review_kind"] == "plan":
            assert snapshot["docs/plan.md"] == case["review_context"]["evidence"]["plan_text"]
        else:
            assert case["review_context"]["evidence"]["plan_text"] is None
        assert case["proof"]["independent_of_reviewer_text"] is True
        assert case["allowed_alternatives"]
        assert bool(case["factual_finding"]) is case["defect"]
        proof_name = case["proof"]["hidden_file"]
        assert proof_name not in snapshot and proof_name not in control
        assert proof_name not in seed
        assert proof_name not in case["review_context"]["authorized_paths"]
        for filename, content in snapshot.items():
            labels = [filename]
            if filename.endswith(".py"):
                labels.extend(token.string for token in tokenize.generate_tokens(
                    io.StringIO(content).readline) if token.type == tokenize.COMMENT)
                labels.extend(re.findall(r"\bdef\s+(test_[A-Za-z0-9_]+)", content))
            exposed_labels = "\n".join(labels).casefold()
            assert all(word.casefold() not in exposed_labels
                       for word in case["forbidden_snapshot_words"]), case["id"]
        for variant, files in (("candidate", snapshot), ("clean", control)):
            work = tmp_path / case["id"] / variant
            work.mkdir(parents=True)
            for name, content in files.items():
                target = work / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            env = {"PATH": "/no-provider-bin", "PYTHONPATH": str(work),
                   "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
            assert not (work / proof_name).exists()
            visible = subprocess.run([sys.executable, "-m", "pytest", "tests/test_public.py", "-q"],
                cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=30, check=False)
            assert visible.returncode == 0, (case["id"], variant, visible.stdout, visible.stderr)
            hidden = work / proof_name
            hidden.parent.mkdir(parents=True, exist_ok=True)
            hidden.write_text(case["proof"]["content"], encoding="utf-8")
            proof = subprocess.run([sys.executable, "-m", "pytest", proof_name, "-q"],
                cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=30, check=False)
            assert (proof.returncode == 0) is (
                variant == "clean" or not case["defect"]), (
                    case["id"], variant, proof.stdout, proof.stderr)


def test_blind_assessment_keeps_contents_and_mapping_separate() -> None:
    sources = [
        {"provider": "agy", "case": "Q1", "envelope": {"status": "SUCCESS",
            "conversation_id": "private-session", "usage": {"total_tokens": 42},
            "structured_output": {"result": {"finding": "Claude said this."}}}},  # allowlist:provider -- certification data: self-mention control
        {"provider": "claude", "case": "Q2", "content": {"finding": "Replay starts twice."}},  # allowlist:provider -- certification data: blind mapping
    ]
    assessment, mapping = probe.blind_package(sources, seed=20260929)
    again, again_mapping = probe.blind_package(sources, seed=20260929)
    assert (assessment, mapping) == (again, again_mapping)
    original_contents = [source.get("content", source.get("envelope", {}).get(
        "structured_output", {}).get("result")) for source in sources]
    assert {json.dumps(row["content"], sort_keys=True) for row in assessment["responses"]} == {
        json.dumps(content, sort_keys=True) for content in original_contents}
    assert "private-session" not in json.dumps(assessment)
    assert "total_tokens" not in json.dumps(assessment)
    assert set(row["id"] for row in assessment["responses"]) == set(mapping["mapping"])
    assert all(set(row) == {"id", "content", "possible_self_identification"}
               for row in assessment["responses"])
    assert any(row["possible_self_identification"] for row in assessment["responses"])
    for row in assessment["responses"]:
        source = next(source for source, content in zip(sources, original_contents)
                      if content == row["content"])
        assert mapping["mapping"][row["id"]]["provider"] == source["provider"]
        assert mapping["mapping"][row["id"]]["case"] == source["case"]
        assert len(mapping["mapping"][row["id"]]["input_sha256"]) == 64


def test_blind_cli_holds_pending_operator_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = tmp_path / "inputs.json"
    assessment = tmp_path / "assessment.json"
    mapping = tmp_path / "private" / "mapping.json"
    inputs.write_text('[]', encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["probe_reviewer.py", "blind", str(inputs),
        str(assessment), str(mapping), "--seed", "20260929"])
    with pytest.raises(PermissionError, match="operator review"):
        probe.main()
    assert not assessment.exists() and not mapping.exists()


def test_default_execution_rejects_real_binary_and_runs_only_explicit_fake(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    out = tmp_path / "out"
    with pytest.raises(PermissionError):
        probe.execute_probe([sys.executable], cwd=workspace, out=out)
    fake_root = tmp_path / "fake"
    fake_root.mkdir()
    fake = fake_root / "reviewer"
    fake.write_text(f"#!{sys.executable}\n# dao-probe-fake-v1\nimport time\ntime.sleep(.2)\nprint('offline fake')\n", encoding="utf-8")
    fake.chmod(0o700)
    result = probe.execute_probe([str(fake)], cwd=workspace, out=out, fake_root=fake_root)
    assert result["exit_code"] == 0
    assert result["workspace_changes"] == []
    assert (out / "stdout.txt").read_text() == "offline fake\n"
    assert result["processes"]
    assert all("starttime" in row and "pid" in row for row in result["processes"])
    profile = tmp_path / "profile.json"
    profile.write_text('{"live": false}')
    with pytest.raises(PermissionError):
        probe.execute_probe([str(fake)], cwd=workspace, out=tmp_path / "missing-profile",
                            live=True)
    with pytest.raises(PermissionError):
        probe.execute_probe([sys.executable], cwd=workspace, out=tmp_path / "other",
                            live=True, profile_file=profile)
    with pytest.raises(PermissionError):
        probe.execute_probe([sys.executable], cwd=workspace, out=tmp_path / "wrong-fake",
                            fake_root=Path(sys.executable).parent)
    profile.write_text('{"live": true}')
    opted_in_fake = probe.execute_probe([str(fake)], cwd=workspace, out=tmp_path / "opted-in",
                                         live=True, profile_file=profile)
    assert opted_in_fake["exit_code"] == 0


def test_snapshot_symlinks_and_sanitizer(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("old")
    link = tmp_path / "link"
    link.symlink_to(target)
    before = probe.capture({"fixture": tmp_path})
    target.write_text("new")
    after = probe.capture({"fixture": tmp_path})
    changes = probe.diff(before, after)
    assert any(row["path"] == "fixture/target.txt" for row in changes)
    assert before["entries"]["fixture/link"]["type"] == "symlink"
    assert before["entries"]["fixture/link"]["target"] == str(target)
    target.write_text("person@example.invalid ya29.secret")
    findings = probe.scan(tmp_path)
    assert {item["pattern"] for item in findings} >= {"email", "google_access_token"}
    redacted = tmp_path.parent / (tmp_path.name + "-redacted")
    probe.redact(tmp_path, redacted)
    assert not probe.scan(redacted)
    assert target.read_text() == "person@example.invalid ya29.secret"
