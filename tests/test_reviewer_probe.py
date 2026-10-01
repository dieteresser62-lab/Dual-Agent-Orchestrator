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
    protocol = load(EVIDENCE / "qualification-protocol-v5.json")
    probe.validate_qualification(protocol)
    assert protocol["quality"]["corpus_sha256"] == sha(FIXTURES / "reviewer-quality-corpus-v1.json")
    from role_certification import read_qualification_evidence
    phase0 = EVIDENCE / 'phase-0-v1.json'
    assert read_qualification_evidence(probe.ROOT, str(phase0.relative_to(probe.ROOT)), protocol['phase0_sha256']) == phase0.read_bytes()
    assert protocol["format_regression_sha256"] == sha(FIXTURES / "reviewer-format-s6-v1.json")
    plan = probe.probe_plan(protocol)
    assert [sum(x["series"] == key for x in plan) for key in
            ("transport", "large_output", "print_timeout", "quality", "canary")] == [12, 2, 1, 6, 2]
    assert protocol["slots"]["reviewer"]["canary"] == "reviewer"
    assert protocol["slots"]["final_reviewer"]["canary"] == "final_reviewer"
    assert protocol["quality_rule"]["critical_required"] == 2
    assert protocol["quality_rule"]["defects_required"] == 3
    assert "keine Einzelretakes oder Filter" in protocol["restart_rule"]
    assert [len(probe.qualification_cases(kind, provider)) for kind, provider in (
        ("transport", "agy"), ("large_output", "agy"),
        ("print_timeout", "agy"), ("quality", "agy"), ("quality", "claude"))] == [12, 2, 1, 6, 6]  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
    verdicts = probe.validate_qualification_evidence(
        probe.read_evidence(EVIDENCE / "qualification-series-v1.json"),
        probe.read_evidence(EVIDENCE / "qualification-envelopes-v1.json.gz"), protocol)
    passed = {name for name, verdict in verdicts.items() if verdict["passed"]}
    assert passed == {"agy-transport-s3", "agy-timeout-s2", "agy-quality-s4",
                      "claude-quality-s3"}  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
    assert verdicts["agy-large-s1"]["failed_cases"] == ["128"]
    assert {name for name, verdict in verdicts.items() if verdict["superseded_corpus"]} == {
        "agy-quality-s1", "agy-quality-s3", "claude-quality-s2"}  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
    scores = probe.grade_quality(load(EVIDENCE / "quality-results-v1.json"),
                                 load(FIXTURES / "reviewer-quality-corpus-v1.json"), protocol)
    assert scores["agy"]["passed"] and not scores["claude"]["passed"]  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
    decisions = {row["id"]: row for row in load(EVIDENCE / "operator-decisions-v1.json")["decisions"]}
    assert decisions["size-override"]["failed_case"] == "128"
    assert load(EVIDENCE / "quality-results-v1.json")["claude_special_decision"] == "approve_experimental"  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification


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


def test_quality_corpus_is_approved_and_independently_reproducible(tmp_path: Path) -> None:
    assert (FIXTURES / "reviewer-quality-corpus-v1.json").stat().st_size < 1_048_576
    corpus = load(FIXTURES / "reviewer-quality-corpus-v1.json")
    assert corpus["operator_review"]["status"] == "approved_blanket"
    assert probe.qualification_ready(
        load(EVIDENCE / "qualification-protocol-v5.json"), corpus)
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
    monkeypatch.setattr(probe, "qualification_ready", lambda protocol, corpus: False)
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


def test_large_fixture_has_exact_independent_defects_and_withheld_proof(tmp_path: Path) -> None:
    for count in (128, 512):
        root = tmp_path / str(count)
        paths = probe.materialize_large_repo(count, root)
        assert len(paths) == count == len(set(paths))
        assert len(probe.large_hidden_proof(count).split("def test_boundary_")) - 1 == count
        assert all("< LIMIT" in (root / path).read_text() for path in paths)
        assert {path.relative_to(root).as_posix() for path in root.rglob("test_*.py")} == {
            "tests/test_public.py"}
        assert probe.verify_qualification_source("large_output", str(count), root) == probe._frozen_source_digest(
            "large_output", str(count))
        environment = {"PATH": "/no-provider-bin", "PYTHONPATH": str(root),
                       "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        public = subprocess.run([sys.executable, "-m", "pytest", "tests/test_public.py", "-q",
                                 "-p", "no:cacheprovider"], cwd=root, env=environment,
                                capture_output=True, text=True, timeout=30, check=False)
        assert public.returncode == 0 and "1 passed" in public.stdout
        proof_path = tmp_path / f"proof_{count}.py"
        proof_path.write_text(probe.large_hidden_proof(count), encoding="utf-8")
        hidden = subprocess.run([sys.executable, "-m", "pytest", str(proof_path), "-q",
                                 "--tb=no", "-p", "no:cacheprovider"], cwd=tmp_path,
                                env=environment, capture_output=True, text=True,
                                timeout=30, check=False)
        assert hidden.returncode == 1 and f"{count} failed" in hidden.stdout
        assert probe.verify_qualification_source("large_output", str(count), root) == probe._frozen_source_digest(
            "large_output", str(count))
        spec = probe.build_qualification_spec("large_output", str(count), run_id=f"large-{count}")
        assert spec.context.max_new_findings == (129 if count == 128 else 512)
        assert len(spec.authorized_paths) == count
        assert "test_boundary_" not in "".join(item.content for item in spec.evidence)


def test_quality_requests_use_frozen_context_without_hidden_proof(tmp_path: Path) -> None:
    from native_review_request import build_native_review_request
    corpus = load(FIXTURES / "reviewer-quality-corpus-v1.json")
    for case in corpus["cases"]:
        root = tmp_path / case["id"]
        probe.materialize_quality_repo(case["id"], root)
        assert probe.verify_qualification_source("quality", case["id"], root) == probe._frozen_source_digest(
            "quality", case["id"])
        spec = probe.build_qualification_spec("quality", case["id"], run_id="quality-" + case["id"])
        bundle = build_native_review_request(spec, profile="antigravity")
        assert spec.context.diff_fingerprint == case["review_context"]["binding"]["diff_fingerprint"]
        assert bundle.document["authorized_paths"] == case["review_context"]["authorized_paths"]
        assert case["proof"]["content"] not in bundle.canonical_json
        assert case["proof"]["hidden_file"] not in bundle.canonical_json
        if case["id"] == "Q3":
            assert bundle.document["review_contract"]["previous_findings"]
        if case["id"] in {"Q4", "Q6"}:
            assert bundle.document["review_kind"] == "final_review"


def test_final_review_qualification_requests_carry_production_capacity_criterion() -> None:
    from native_review_request import build_native_review_request
    from workflow_requests import final_review_discovery_capacity_criterion

    for kind, case_id, capacity in (("transport", "F5:1", 2), ("transport", "F6:2", 2),
                                    ("quality", "Q4", 512), ("quality", "Q6", 512)):
        spec = probe.build_qualification_spec(kind, case_id, run_id=f"capacity-{case_id}")
        criterion = final_review_discovery_capacity_criterion(capacity)
        assert spec.acceptance_criteria[-1] == criterion
        assert spec.acceptance_criteria.count(criterion) == 1
        assert "rule_id=DISCOVERY_OUTPUT_LIMIT" in criterion
        bundle = build_native_review_request(spec, profile="antigravity")
        assert criterion in bundle.document["acceptance_criteria"]
    for kind, case_id in (("transport", "F1:1"), ("transport", "F4:1"), ("quality", "Q1"),
                          ("quality", "Q3"), ("large_output", "512")):
        spec = probe.build_qualification_spec(kind, case_id, run_id=f"capacity-{case_id}")
        assert not any("binds max_new_findings" in item for item in spec.acceptance_criteria)
    assert probe.spec_for("F6").acceptance_criteria == (
        "Find every reproducible defect; the request-bound discovery capacity is 2.",)


def _failed_attempt(kind: str, case_id: str, provider: str, *, series_id: str,
                    call_id: str) -> tuple[dict, dict]:
    from native_review_request import build_native_review_request
    bundle = build_native_review_request(probe.build_qualification_spec(
        kind, case_id, run_id=f"qualification-{series_id}-{call_id}"),
        profile="antigravity" if provider == "agy" else "claude")  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
    row = {
        "call_id": call_id, "series_id": series_id, "kind": kind, "provider": provider,
        "role": "reviewer", "slot": probe._qualified_slot(case_id, kind),
        "case_id": case_id, "request_id": bundle.bound_context.request_id,
        "case_sha256": probe._frozen_source_digest(kind, case_id),
        "request_sha256": probe.sha(bundle.canonical_json.encode()),
        "writer_sha256": probe.sha(bundle.provider_response_schema_json.encode()),
        "profile_sha256": "a" * 64, "binary_sha256": "b" * 64,
        "commit_sha": "c" * 40, "probe_limit_seconds": 600,
        "started_at": "2026-09-29T08:00:00+00:00", "duration_seconds": 1.0,
        "status": "technical_rejection", "denials": [], "usage": None,
        "quota": None, "tag": "test", "output_bytes": 0,
        "checks": {"writer_and_domain": False},
    }
    return row, {"envelope": {}, "stderr": "", "exit_code": None,
                 "technical_error": "synthetic failure",
                 "request_document": json.loads(bundle.canonical_json),
                 "writer_schema": json.loads(bundle.provider_response_schema_json)}


def test_series_count_restart_and_failure_visibility() -> None:
    protocol = load(EVIDENCE / "qualification-protocol-v5.json")
    series = {"schema_version": "qualification-series-v1", "attempts": []}
    envelopes = {"schema_version": "qualification-envelopes-v1", "envelopes": []}
    expected = probe.qualification_cases("transport", "agy")
    for position, case in enumerate(expected[:-1], 1):
        row, raw = _failed_attempt("transport", case, "agy", series_id="s1", call_id=f"a{position}")
        probe.append_qualification_attempt(series, envelopes, attempt=row, envelope=raw)
    verdicts = probe.validate_qualification_evidence(series, envelopes, protocol)
    assert verdicts["s1"]["incomplete"] and verdicts["s1"]["calls"] == 11
    assert not verdicts["s1"]["passed"]
    row, raw = _failed_attempt("transport", expected[-1], "agy", series_id="s1", call_id="a12")
    probe.append_qualification_attempt(series, envelopes, attempt=row, envelope=raw)
    verdicts = probe.validate_qualification_evidence(series, envelopes, protocol)
    assert verdicts["s1"]["calls"] == 12
    assert verdicts["s1"]["failed_cases"] == list(expected)
    with pytest.raises(ValueError, match="already recorded"):
        probe.append_qualification_attempt(series, envelopes, attempt=row, envelope=raw)
    restart, raw = _failed_attempt("transport", expected[0], "agy", series_id="s2", call_id="b1")
    restart["restart_diagnosis"] = "Provider rejected the old writer."
    restart["restart_change"] = "Writer update at a new commit."
    probe.append_qualification_attempt(series, envelopes, attempt=restart, envelope=raw)
    with pytest.raises(ValueError, match="did not change"):
        probe.validate_qualification_evidence(series, envelopes, protocol)
    restart["commit_sha"] = "d" * 40
    series["attempts"][-1]["commit_sha"] = "d" * 40
    assert probe.validate_qualification_evidence(series, envelopes, protocol)["s2"]["incomplete"]
    assert len(series["attempts"]) == 13


def test_series_preflight_prevents_skips_retake_and_undocumented_restart() -> None:
    common = dict(kind="transport", provider="agy", commit_sha="c" * 40,
                  profile_sha256="a" * 64, binary_sha256="b" * 64,
                  writer_sha256="d" * 64, restart_diagnosis=None,
                  restart_change=None)
    with pytest.raises(ValueError, match="first frozen case"):
        probe._preflight_series_position([], series_id="s1", case_id="F2:1", **common)
    first = {"kind": "transport", "provider": "agy", "series_id": "s1",
             "case_id": "F1:1", "commit_sha": "c" * 40,
             "profile_sha256": "a" * 64, "binary_sha256": "b" * 64,
             "writer_sha256": "d" * 64, "status": "technical_rejection",
             "checks": {"writer": False}}
    probe._preflight_series_position([first], series_id="s1", case_id="F1:2", **common)
    with pytest.raises(ValueError, match="frozen series order"):
        probe._preflight_series_position([first], series_id="s1", case_id="F2:1", **common)
    with pytest.raises(ValueError, match="requires diagnosis"):
        probe._preflight_series_position([first], series_id="s2", case_id="F1:1", **common)
    changed = dict(common, commit_sha="e" * 40,
                   restart_diagnosis="Technical output failed.",
                   restart_change="Updated committed adapter.")
    probe._preflight_series_position([first], series_id="s2", case_id="F1:1", **changed)
    second = dict(first, series_id="s2", commit_sha="e" * 40)
    with pytest.raises(ValueError, match="earlier series"):
        probe._preflight_series_position([first, second], series_id="s1", case_id="F1:2", **common)


def _rating_fixture(q6_findings: list[dict] | None = None) -> tuple[dict, dict, dict, dict, dict]:
    corpus = load(FIXTURES / "reviewer-quality-corpus-v1.json")
    protocol = load(EVIDENCE / "qualification-protocol-v5.json")
    rubric = load(EVIDENCE / "quality-rubric-v1.json")
    sources = [{"provider": provider, "case": case["id"],
                "content": {"new_findings": copy.deepcopy(q6_findings) if case["id"] == "Q6" and q6_findings is not None else []}}
               for provider in ("agy", "claude") for case in corpus["cases"]]  # allowlist:provider -- certification data: independent blind quality rating
    assessment, mapping = probe.blind_package(sources, seed=protocol["quality"]["blind_seed"])
    packet = probe.export_rater_packet(assessment, mapping, corpus, protocol, rubric)
    def rating(name: str) -> dict:
        judgments = []
        for entry in packet["responses"]:
            row = {"id": entry["id"],
                   "unfounded_findings": len(entry["response"]["new_findings"]) if not entry["ground_truth"]["defect"] else 0,
                   "invented_critical": any(item.get("finding_class") == "BLOCKER" for item in entry["response"]["new_findings"]) if not entry["ground_truth"]["defect"] else False,
                   "reasons": {"unfounded_findings": "No unsupported findings.",
                               "invented_critical": "No invented blocker."}}
            if entry["ground_truth"]["defect"]:
                row["defect_found"] = True
                row["reasons"]["defect_found"] = "Mechanism and path identified."
            judgments.append(row)
        return {"schema_version": "quality-rating-v1", "rater": name,
                "packet_sha256": packet["packet_sha256"], "judgments": judgments}
    return packet, mapping, {name: rating(name) for name in ("codex", "steering")}, corpus, protocol  # allowlist:provider -- certification data: independent blind quality rating


def test_v1_rejected_and_blanket_approval_is_digest_bound() -> None:
    v1 = load(EVIDENCE / "qualification-protocol-v1.json")
    assert v1["superseded_by"] == "v2"
    assert v1["superseded_reason"] == "Bewerterverfahren geändert vor der ersten Messung"
    with pytest.raises(AssertionError):
        probe.validate_qualification(v1)
    corpus = load(FIXTURES / "reviewer-quality-corpus-v1.json")
    protocol = load(EVIDENCE / "qualification-protocol-v5.json")
    assert probe.qualification_ready(protocol, corpus)
    changed = copy.deepcopy(corpus)
    changed["operator_review"]["operator_note"] = "Altered"
    assert not probe.qualification_ready(protocol, changed)


def test_rater_packets_and_strict_rating_schema() -> None:
    packet, mapping, ratings, corpus, protocol = _rating_fixture()
    assert len(packet["responses"]) == 12
    assert set(mapping["mapping"]) == {entry["id"] for entry in packet["responses"]}
    assert all("provider" not in entry and "provider" not in entry["response"]
               for entry in packet["responses"])
    for name, rating in ratings.items():
        probe.validate_rating(rating, packet, name)
    changed = copy.deepcopy(ratings["codex"])  # allowlist:provider -- certification data: independent blind quality rating
    changed["judgments"].pop()
    probe.validate_rating(changed, packet, "codex")  # allowlist:provider -- certification data: independent blind quality rating
    incomplete = probe.combine_quality_ratings(packet, mapping,
        {**ratings, "codex": changed}, corpus, protocol)  # allowlist:provider -- certification data: independent blind quality rating
    assert incomplete["status"] == "incomplete" and incomplete["mapping"] == {}
    assert not any(row["passed"] for row in probe.grade_quality(incomplete, corpus, protocol).values())
    changed["judgments"][0]["id"] = "unknown"
    with pytest.raises(ValueError, match="rating IDs"):
        probe.validate_rating(changed, packet, "codex")  # allowlist:provider -- certification data: independent blind quality rating
    forged = copy.deepcopy(packet)
    forged["responses"][0]["ground_truth"]["affected_path"] = "mini/forged.py"
    forged["packet_sha256"] = probe.digest({k: v for k, v in forged.items()
                                             if k != "packet_sha256"})
    forged_ratings = copy.deepcopy(ratings)
    for rating in forged_ratings.values():
        rating["packet_sha256"] = forged["packet_sha256"]
    with pytest.raises(ValueError, match="frozen ground truth"):
        probe.combine_quality_ratings(forged, mapping, forged_ratings, corpus, protocol)


def test_two_raters_agreement_disagreement_and_operator_answer() -> None:
    packet, mapping, ratings, corpus, protocol = _rating_fixture()
    result = probe.combine_quality_ratings(packet, mapping, ratings, corpus, protocol)
    assert result["status"] == "complete" and result["operator_questions"] == []
    assert all(row["agreed"] for row in result["agreement"])
    assert all(row["passed"] for row in probe.grade_quality(result, corpus, protocol).values())
    changed = copy.deepcopy(ratings)
    row = next(row for row in changed["steering"]["judgments"] if
               next(entry for entry in packet["responses"] if entry["id"] == row["id"])["case"] == "Q1")
    row["defect_found"] = False
    pending = probe.combine_quality_ratings(packet, mapping, changed, corpus, protocol)
    assert pending["status"] == "incomplete" and pending["mapping"] == {}
    assert len(pending["operator_questions"]) == 1
    assert not any(row["passed"] for row in probe.grade_quality(pending, corpus, protocol).values())
    question = pending["operator_questions"][0]
    answers = {"schema_version": "quality-operator-answers-v1", "answers": [
        {"id": question["id"], "criterion": question["criterion"], "answer": True}]}
    complete = probe.combine_quality_ratings(packet, mapping, changed, corpus, protocol, answers)
    assert complete["status"] == "complete"
    assert all(row["passed"] for row in probe.grade_quality(complete, corpus, protocol).values())


def test_defect_case_unfounded_dispute_needs_no_operator_question() -> None:
    packet, mapping, ratings, corpus, protocol = _rating_fixture()
    q1_ids = {entry["id"] for entry in packet["responses"] if entry["case"] == "Q1"}
    for entry in packet["responses"]:
        if entry["id"] in q1_ids:
            entry["response"]["new_findings"] = [{"summary": "a"}, {"summary": "b"}]
    assessment = {"schema_version": "blind-assessment-v1", "responses": [
        {"id": row["id"], "content": row["response"]} for row in packet["responses"]]}
    for row in mapping["mapping"].values():
        if row["case"] == "Q1":
            row["content_sha256"] = probe.sha(json.dumps(
                {"new_findings": [{"summary": "a"}, {"summary": "b"}]},
                ensure_ascii=False, sort_keys=True).encode())
    packet = probe.export_rater_packet(assessment, mapping, corpus, protocol,
                                       load(EVIDENCE / "quality-rubric-v1.json"))
    for name, rating in ratings.items():
        rating["packet_sha256"] = packet["packet_sha256"]
        for row in rating["judgments"]:
            if row["id"] in q1_ids:
                row["unfounded_findings"] = 1 if name == "codex" else 0  # allowlist:provider -- certification data: independent blind quality rating
    result = probe.combine_quality_ratings(packet, mapping, ratings, corpus, protocol)
    assert result["status"] == "complete" and result["operator_questions"] == []
    disputed = [row for row in result["agreement"] if not row["agreed"]]
    assert {row["id"] for row in disputed} == q1_ids
    assert all(not row["rule_relevant"] for row in disputed)
    assert all(row["unfounded_findings"] == 1 for row in result["judgments"] if row["id"] in q1_ids)
    assert all(row["passed"] for row in probe.grade_quality(result, corpus, protocol).values())
    forged = copy.deepcopy(result)
    forged["operator_questions"] = [{"id": sorted(q1_ids)[0], "criterion": "unfounded_findings",
        "question": "?", "yes_value": 1, "no_value": 0}]
    forged["operator_decisions"] = [{"id": sorted(q1_ids)[0], "criterion": "unfounded_findings",
        "answer": False}]
    with pytest.raises(ValueError, match="rule-irrelevant"):
        probe.grade_quality(forged, corpus, protocol)
    too_many = copy.deepcopy(ratings["steering"])
    next(row for row in too_many["judgments"] if row["id"] in q1_ids)["unfounded_findings"] = 3
    with pytest.raises(ValueError, match="more unfounded findings than findings"):
        probe.validate_rating(too_many, packet, "steering")


def test_q6_filter_finding_is_unfounded_and_final_rule_is_separate() -> None:
    finding = {"summary": "Die None-Filterung in render_review ist zu breit und verliert Pflichtfelder.",
               "finding_class": "FINDING"}
    packet, mapping, ratings, corpus, protocol = _rating_fixture([finding])
    assert "None-Filterung" in packet["preregistered_classification"]["q6_rule"]
    result = probe.combine_quality_ratings(packet, mapping, ratings, corpus, protocol)
    scores = probe.grade_quality(result, corpus, protocol)
    assert scores["agy"]["clean_false_positives"] == 1
    assert scores["agy"]["passed"]
    wrong = copy.deepcopy(ratings)
    q6 = {entry["id"] for entry in packet["responses"] if entry["case"] == "Q6"}
    for rating in wrong.values():
        for row in rating["judgments"]:
            if row["id"] in q6:
                row["unfounded_findings"] = 0
    with pytest.raises(ValueError, match="every clean-case finding is unfounded"):
        probe.combine_quality_ratings(packet, mapping, wrong, corpus, protocol)
    packet, mapping, ratings, corpus, protocol = _rating_fixture([finding, finding])
    result = probe.combine_quality_ratings(packet, mapping, ratings, corpus, protocol)
    assert not probe.grade_quality(result, corpus, protocol)["agy"]["passed"]
    packet, mapping, ratings, corpus, protocol = _rating_fixture()
    noncritical_ids = {entry["id"] for entry in packet["responses"]
                       if entry["case"] in {"Q3", "Q4"}}
    for rating in ratings.values():
        for row in rating["judgments"]:
            if row["id"] in noncritical_ids:
                row["defect_found"] = False
    result = probe.combine_quality_ratings(packet, mapping, ratings, corpus, protocol)
    assert probe.grade_quality(result, corpus, protocol)["agy"]["defects"] == 2
    assert not probe.grade_quality(result, corpus, protocol)["agy"]["passed"]
    blocker = {"summary": "Filter zu breit", "finding_class": "BLOCKER"}
    packet, mapping, ratings, corpus, protocol = _rating_fixture([blocker])
    result = probe.combine_quality_ratings(packet, mapping, ratings, corpus, protocol)
    assert not probe.grade_quality(result, corpus, protocol)["agy"]["passed"]
    packet, mapping, ratings, corpus, protocol = _rating_fixture()
    critical_ids = {entry["id"] for entry in packet["responses"] if entry["case"] in {"Q1", "Q2"}}
    for rating in ratings.values():
        for row in rating["judgments"]:
            if row["id"] in critical_ids:
                row["defect_found"] = False
    result = probe.combine_quality_ratings(packet, mapping, ratings, corpus, protocol)
    assert not probe.grade_quality(result, corpus, protocol)["agy"]["passed"]


def test_rating_cli_exports_identical_packets_and_records_questions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet, mapping, ratings, corpus, protocol = _rating_fixture()
    assessment = {"schema_version": "blind-assessment-v1", "responses": [
        {"id": row["id"], "content": row["response"]} for row in packet["responses"]]}
    for name, value in (("assessment", assessment), ("mapping", mapping)):
        (tmp_path / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["probe_reviewer.py", "export-rater-packets",
        str(tmp_path / "assessment.json"), str(tmp_path / "mapping.json"), str(tmp_path / "packets")])
    assert probe.main() == 0
    assert (tmp_path / "packets/codex-packet.json").read_bytes() == (  # allowlist:provider -- certification data: independent blind quality rating
        tmp_path / "packets/steering-packet.json").read_bytes()
    monkeypatch.setattr(sys, "argv", ["probe_reviewer.py", "render-rater-prompt",
        str(tmp_path / "packets/codex-packet.json"), str(tmp_path / "prompt.txt")])  # allowlist:provider -- certification data: independent blind quality rating
    assert probe.main() == 0
    assert "Blindpaket:" in (tmp_path / "prompt.txt").read_text()
    changed = copy.deepcopy(ratings)
    q1_id = next(row["id"] for row in packet["responses"] if row["case"] == "Q1")
    next(row for row in changed["steering"]["judgments"]
         if row["id"] == q1_id)["defect_found"] = False
    for name, value in changed.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
    command = ["probe_reviewer.py", "combine-quality-ratings",
        str(tmp_path / "packets/codex-packet.json"),  # allowlist:provider -- certification data: independent blind quality rating
        str(tmp_path / "mapping.json"), str(tmp_path / "codex.json"),  # allowlist:provider -- certification data: independent blind quality rating
        str(tmp_path / "steering.json"), str(tmp_path / "results.json"),
        "--questions-out", str(tmp_path / "questions.json")]
    monkeypatch.setattr(sys, "argv", command)
    assert probe.main() == 1
    questions = load(tmp_path / "questions.json")["questions"]
    assert len(questions) == 1
    answer = {"schema_version": "quality-operator-answers-v1", "answers": [
        {"id": questions[0]["id"], "criterion": questions[0]["criterion"], "answer": True}]}
    (tmp_path / "answers.json").write_text(json.dumps(answer), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", command + ["--operator-answers", str(tmp_path / "answers.json")])
    assert probe.main() == 0
    assert load(tmp_path / "results.json")["status"] == "complete"
    assert load(tmp_path / "questions.json")["questions"] == []


def test_qualification_live_gate_and_evidence_sanitization(tmp_path: Path) -> None:
    profile = tmp_path / "profile.toml"
    profile.write_text('live = false\n', encoding="utf-8")
    with pytest.raises(PermissionError, match="--live"):
        probe.run_qualification_call(kind="transport", case_id="F1:1", provider="agy",
            series_id="s1", call_id="a1", profile_file=profile, source_repo=tmp_path,
            output_dir=tmp_path / "out")
    with pytest.raises(PermissionError, match="profile"):
        probe.run_qualification_call(kind="transport", case_id="F1:1", provider="agy",
            series_id="s1", call_id="a1", profile_file=profile, source_repo=tmp_path,
            output_dir=tmp_path / "out", live=True)
    cleaned = probe.sanitize_evidence({"stderr": "person@example.invalid ya29.secret"})
    assert "person@example.invalid" not in str(cleaned) and "ya29.secret" not in str(cleaned)


def test_qualification_call_routes_one_fake_response_through_native_agy_adapter(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    import agent_runtime
    from antigravity_adapter import NativeAntigravityReviewAdapter
    from native_review_contract import parse_bound_native_contract_result

    source = tmp_path / "repo"
    probe.materialize_format_repo("F1", source)
    profile = tmp_path / "profile.toml"
    profile.write_text(
        'live = true\nmodel = "gemini-3.1-pro-high"\neffort = "high"\n'
        f'binary = "{sys.executable}"\ncommit_sha = "{"c" * 40}"\n'
        f'[provider_options.antigravity]\nhome = "{tmp_path / "home"}"\n'
        f'run_root = "{tmp_path / "runs"}"\n', encoding="utf-8")
    observed = []
    stored = load(FIXTURES / "reviewer-format-s6-v1.json")["cases"]["F1"]["envelope"]

    def fake_native_review(adapter, bundle, **kwargs):
        assert isinstance(adapter, NativeAntigravityReviewAdapter)
        assert kwargs["attempt_invocation"].call_id == "a1"
        assert kwargs["operation"] == "reviewer_plan_review"
        response = copy.deepcopy(stored["structured_output"]["result"])
        response["request_id"] = bundle.bound_context.request_id
        envelope = {**stored, "structured_output": {"result": response},
                    "json_schema": json.loads(bundle.provider_response_schema_json)}
        adapter.extract_output(json.dumps(envelope), "", {"exit_code": "0"})
        observed.append(bundle.bound_context.request_id)
        return SimpleNamespace(result=parse_bound_native_contract_result(
            response, bundle.bound_context))

    monkeypatch.setattr(agent_runtime, "run_native_review_agent", fake_native_review)
    monkeypatch.setattr(NativeAntigravityReviewAdapter, "extract_output", lambda self, stdout, stderr, extra: "captured")
    monkeypatch.setattr(probe, "_current_commit", lambda: "c" * 40)
    monkeypatch.setattr(probe, "_assert_committed_qualification_code", lambda: None)
    out = tmp_path / "out"
    row = probe.run_qualification_call(kind="transport", case_id="F1:1", provider="agy",
        series_id="s1", call_id="a1", profile_file=profile,
        source_repo=source, output_dir=out, live=True)
    assert observed == [row["request_id"]]
    assert row["status"] == "success"
    assert all(row["checks"].values())
    assert probe.strict_json((out / "qualification-series-v1.json").read_bytes())["attempts"][0]["call_id"] == "a1"
    assert probe.validate_qualification_evidence(
        probe.strict_json((out / "qualification-series-v1.json").read_bytes()),
        probe.strict_json((out / "qualification-envelopes-v1.json").read_bytes()),
        load(EVIDENCE / "qualification-protocol-v5.json"))["s1"]["incomplete"]


def test_print_timeout_is_technical_and_timeout_proposal_uses_512() -> None:
    protocol = load(EVIDENCE / "qualification-protocol-v5.json")
    series = {"schema_version": "qualification-series-v1", "attempts": []}
    envelopes = {"schema_version": "qualification-envelopes-v1", "envelopes": []}
    row, raw = _failed_attempt("print_timeout", "T1", "agy", series_id="timeout-s1", call_id="t1")
    row["checks"] = {"print_timeout": True, "no_valid_stop": True}
    row["failure_kind"] = "timeout"
    raw["stderr"] = "[agy] print timeout after 1s with turn in progress; returning partial output"
    raw["envelope"] = {"status": "SUCCESS", "structured_output": None}
    probe.append_qualification_attempt(series, envelopes, attempt=row, envelope=raw)
    verdicts = probe.validate_qualification_evidence(series, envelopes, protocol)
    assert verdicts["timeout-s1"]["passed"]
    # Measured live (29 Sep 2026): the runtime deadline fired before AGY's own print timeout.
    deadline_series = {"schema_version": "qualification-series-v1", "attempts": []}
    deadline_envelopes = {"schema_version": "qualification-envelopes-v1", "envelopes": []}
    first, first_raw = _failed_attempt("print_timeout", "T1", "agy", series_id="timeout-s1", call_id="d1")
    first.update(failure_kind="timeout", checks={"print_timeout": False, "no_valid_stop": True})
    first_raw.update(technical_error="AgentProcessError: antigravity timed out after 30s.")
    probe.append_qualification_attempt(deadline_series, deadline_envelopes, attempt=first, envelope=first_raw)
    verdicts = probe.validate_qualification_evidence(deadline_series, deadline_envelopes, protocol)
    assert not verdicts["timeout-s1"]["passed"]  # a historical negative stays negative
    second, second_raw = _failed_attempt("print_timeout", "T1", "agy", series_id="timeout-s2", call_id="d2")
    second.update(failure_kind="timeout", commit_sha="e" * 40,
                  restart_diagnosis="Check matched only the word timeout.",
                  restart_change="Runtime deadline counts as a safe timeout.",
                  checks={"print_timeout": True, "no_valid_stop": True})
    second_raw.update(technical_error="AgentProcessError: antigravity timed out after 30s.")
    probe.append_qualification_attempt(deadline_series, deadline_envelopes, attempt=second, envelope=second_raw)
    verdicts = probe.validate_qualification_evidence(deadline_series, deadline_envelopes, protocol)
    assert verdicts["timeout-s2"]["passed"]
    forged = copy.deepcopy(deadline_series)
    forged["attempts"][1]["failure_kind"] = "process"
    with pytest.raises(ValueError, match="print timeout"):
        probe.validate_qualification_evidence(forged, deadline_envelopes, protocol)
    changed = copy.deepcopy(envelopes)
    changed["envelopes"][0]["envelope"]["envelope"]["structured_output"] = {
        "result": {"result_type": "stop_request"}}
    series["attempts"][0]["envelope_sha256"] = probe.sha(probe.canonical(
        changed["envelopes"][0]["envelope"]).encode())
    with pytest.raises(ValueError, match="print timeout"):
        probe.validate_qualification_evidence(series, changed, protocol)

    measurements = {"attempts": [
        {"series_id": "transport", "kind": "transport", "case_id": "F1:1", "duration_seconds": 590,
         "status": "technical_rejection", "failure_kind": "network"},
        {"series_id": "transport", "kind": "transport", "case_id": "F1:1", "duration_seconds": 210,
         "status": "success"},
        {"series_id": "size", "kind": "large_output", "case_id": "128", "duration_seconds": 330,
         "status": "success"},
        {"series_id": "size", "kind": "large_output", "case_id": "512", "duration_seconds": 401,
         "status": "success"},
    ]}
    successes = {"transport": {"passed": True, "provider": "agy", "kind": "transport"},
                 "size": {"passed": True, "provider": "agy", "kind": "large_output"}}
    proposal = probe.timeout_proposal(measurements, successes, protocol)
    assert proposal["suggested_seconds"] == 660  # ceil(1.5 * 401 / 60) * 60
    assert proposal["profile_zero_allowed"] is True
    assert proposal["censored_by_limit"] is False


def test_qualification_claude_call_builds_restricted_productive_input(  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    import agent_runtime
    from agent_adapters import NativeClaudeReviewAdapter  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
    from native_review_contract import parse_bound_native_contract_result

    source = tmp_path / "quality"
    probe.materialize_quality_repo("Q1", source)
    fake_binary = tmp_path / "claude"  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
    fake_binary.write_text("#!/bin/sh\n# dao-probe-fake-v1\n", encoding="utf-8")
    fake_binary.chmod(0o700)
    profile = tmp_path / "claude.toml"  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
    profile.write_text(
        f'live = true\nmodel = "opus"\neffort = "high"\n'
        f'binary = "{fake_binary}"\ncommit_sha = "{"c" * 40}"\n',
        encoding="utf-8")
    response = load(FIXTURES / "reviewer-format-s6-v1.json")["cases"]["F1"]["envelope"]["structured_output"]["result"]
    observed = []

    def fake_native_review(adapter, bundle, **kwargs):
        assert isinstance(adapter, NativeClaudeReviewAdapter)  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
        prepared = adapter.prepare_native_provider_input(bundle)
        assert prepared.command.count("--restricted") == 1
        assert "--json-schema" in prepared.command
        assert bundle.canonical_json in "".join(
            path.read_text() for path in adapter.invocation.reviewer_input.request_files)
        result = dict(response, request_id=bundle.bound_context.request_id)
        adapter.extract_output(json.dumps({"is_error": False, "structured_output": {"result": result}}),
                               "", {"exit_code": "0"})
        observed.append(prepared.command)
        adapter.cleanup()
        return SimpleNamespace(result=parse_bound_native_contract_result(result, bundle.bound_context))

    monkeypatch.setattr(probe, "qualification_ready", lambda protocol, corpus: True)
    monkeypatch.setattr(probe, "_current_commit", lambda: "c" * 40)
    monkeypatch.setattr(probe, "_assert_committed_qualification_code", lambda: None)
    monkeypatch.setattr(agent_runtime, "run_native_review_agent", fake_native_review)
    monkeypatch.setattr(NativeClaudeReviewAdapter, "extract_output", lambda self, stdout, stderr, extra: "captured")  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
    row = probe.run_qualification_call(kind="quality", case_id="Q1", provider="claude",  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
        series_id="claude-quality-s1", call_id="q1", profile_file=profile,  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
        source_repo=source, output_dir=tmp_path / "out", live=True)
    assert observed and row["status"] == "success", probe.strict_json(
        (tmp_path / "out/qualification-envelopes-v1.json").read_bytes())["envelopes"][0]["envelope"]["technical_error"]
    assert row["slot"] == "reviewer"
    assert row["checks"] == {"writer_and_domain": True}
    assert len(row["public_validation_sha256"]) == 64


def test_blind_sources_require_all_twelve_bound_outputs_without_selection() -> None:
    from native_review_request import build_native_review_request

    protocol = load(EVIDENCE / "qualification-protocol-v5.json")
    stored = load(FIXTURES / "reviewer-format-s6-v1.json")["cases"]
    templates = {"Q1": "F1", "Q2": "F2", "Q3": "F4", "Q4": "F5", "Q5": "F1", "Q6": "F5"}
    series = {"schema_version": "qualification-series-v1", "attempts": []}
    envelopes = {"schema_version": "qualification-envelopes-v1", "envelopes": []}
    for provider in ("agy", "claude"):  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
        series_id = f"quality-{provider}-s1"
        for case_id, template in templates.items():
            call_id = f"{provider}-{case_id}"
            row, raw = _failed_attempt("quality", case_id, provider,
                                       series_id=series_id, call_id=call_id)
            bundle = build_native_review_request(probe.build_qualification_spec(
                "quality", case_id, run_id=f"qualification-{series_id}-{call_id}"),
                profile="antigravity" if provider == "agy" else "claude")  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
            response = copy.deepcopy(stored[template]["envelope"]["structured_output"]["result"])
            response["request_id"] = bundle.bound_context.request_id
            if case_id == "Q3":
                response["status_changes"][0]["finding_id"] = "R-17"
            if case_id in {"Q4", "Q6"}:
                response["new_findings"] = []
            raw["envelope"] = {"structured_output": {"result": response}}
            if provider == "agy":
                raw["envelope"].update(status="SUCCESS", json_schema=json.loads(
                    bundle.provider_response_schema_json))
            else:
                raw["envelope"]["is_error"] = False
            raw["technical_error"] = None
            row["status"] = "success"
            row["checks"] = {"writer_and_domain": True}
            row["output_bytes"] = len(probe.canonical(raw["envelope"]).encode())
            probe.append_qualification_attempt(series, envelopes, attempt=row, envelope=raw)
    sources = probe.quality_blind_sources(series, envelopes, protocol)
    assert len(sources) == 12
    assert {(item["provider"], item["case"]) for item in sources} == {
        (provider, f"Q{number}") for provider in ("agy", "claude") for number in range(1, 7)}  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
    assessment, mapping = probe.blind_package(sources, seed=protocol["quality"]["blind_seed"])
    assert len(assessment["responses"]) == 12
    probe.verify_quality_mapping({"mapping": mapping["mapping"]}, series, envelopes, protocol)
    modified = copy.deepcopy(mapping["mapping"])
    modified["B001"]["case"] = "Q6" if modified["B001"]["case"] != "Q6" else "Q5"
    with pytest.raises(ValueError, match="mapping differs"):
        probe.verify_quality_mapping({"mapping": modified}, series, envelopes, protocol)
    # Removing a single outcome leaves the entire series ineligible for blinding.
    shortened = copy.deepcopy(series)
    shortened["attempts"].pop()
    reduced_raw = copy.deepcopy(envelopes)
    reduced_raw["envelopes"].pop()
    with pytest.raises(ValueError, match="not complete"):
        probe.quality_blind_sources(shortened, reduced_raw, protocol)


def _quality_success(provider: str, case_id: str, *, series_id: str, call_id: str,
                     retry_of: str | None = None) -> tuple[dict, dict]:
    from native_review_request import build_native_review_request

    templates = {"Q1": "F1", "Q2": "F2", "Q3": "F4", "Q4": "F5", "Q5": "F1", "Q6": "F5"}
    stored = load(FIXTURES / "reviewer-format-s6-v1.json")["cases"]
    row, raw = _failed_attempt("quality", case_id, provider, series_id=series_id, call_id=call_id)
    bundle = build_native_review_request(probe.build_qualification_spec(
        "quality", case_id, run_id=f"qualification-{series_id}-{call_id}"),
        profile="antigravity" if provider == "agy" else "claude")  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
    response = copy.deepcopy(stored[templates[case_id]]["envelope"]["structured_output"]["result"])
    response["request_id"] = bundle.bound_context.request_id
    if case_id == "Q3":
        response["status_changes"][0]["finding_id"] = "R-17"
    if case_id in {"Q4", "Q6"}:
        response["new_findings"] = []
    raw["envelope"] = {"structured_output": {"result": response}, "status": "SUCCESS",
                       "json_schema": json.loads(bundle.provider_response_schema_json)}
    raw["technical_error"] = None
    row.update(status="success", checks={"writer_and_domain": True}, failure_kind=None,
               retry_of=retry_of, output_bytes=len(probe.canonical(raw["envelope"]).encode()))
    return row, raw


def _stream_interrupted(row: dict, raw: dict) -> tuple[dict, dict]:
    failed, envelope = copy.deepcopy(row), copy.deepcopy(raw)
    envelope["envelope"].update(
        status="ERROR",
        error="The stream was interrupted. Please continue the task you were working on.")
    envelope["exit_code"] = 0
    envelope["technical_error"] = "AgentOutputError: antigravity stream-interrupted (agy-stderr-v4)"
    failed.update(status="technical_rejection", failure_kind="network",
                  checks={"writer_and_domain": False})
    return failed, envelope


def test_production_retry_counts_transient_failures_and_uses_final_call() -> None:
    protocol = load(EVIDENCE / "qualification-protocol-v5.json")
    assert protocol["production_retry"]["max_retries_per_case"] == 2
    v2 = load(EVIDENCE / "qualification-protocol-v2.json")
    assert v2["superseded_by"] == "v3"
    with pytest.raises(AssertionError):
        probe.validate_qualification(v2)

    def build(chain_q1: int) -> tuple[dict, dict]:
        series = {"schema_version": "qualification-series-v1", "attempts": []}
        envelopes = {"schema_version": "qualification-envelopes-v1", "envelopes": []}
        for provider in ("agy", "claude"):  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
            series_id = f"quality-{provider}-s1"
            for number in range(1, 7):
                case_id = f"Q{number}"
                call_id = f"{provider}-{case_id}"
                if provider == "agy" and case_id == "Q1":
                    previous = None
                    for attempt in range(chain_q1):
                        this_id = call_id if attempt == 0 else f"{call_id}-r{attempt}"
                        row, raw = _stream_interrupted(*_quality_success(
                            provider, case_id, series_id=series_id, call_id=this_id,
                            retry_of=previous))
                        probe.append_qualification_attempt(series, envelopes, attempt=row, envelope=raw)
                        previous = this_id
                    call_id, retry_of = f"{call_id}-r{chain_q1}", previous
                else:
                    retry_of = None
                row, raw = _quality_success(provider, case_id, series_id=series_id,
                                            call_id=call_id, retry_of=retry_of)
                probe.append_qualification_attempt(series, envelopes, attempt=row, envelope=raw)
        return series, envelopes

    series, envelopes = build(2)
    verdicts = probe.validate_qualification_evidence(series, envelopes, protocol)
    agy = verdicts["quality-agy-s1"]
    assert agy["passed"] and agy["production_retries"] == 2 and agy["calls"] == 8
    assert agy["transient_failures"] == ["agy-Q1", "agy-Q1-r1"]
    sources = probe.quality_blind_sources(series, envelopes, protocol)
    q1 = next(item for item in sources if item["provider"] == "agy" and item["case"] == "Q1")
    final = next(row for row in envelopes["envelopes"] if row["call_id"] == "agy-Q1-r2")
    assert q1["content"] == final["envelope"]["envelope"]["structured_output"]["result"]

    with pytest.raises(ValueError, match="invalid production retry"):
        probe.validate_qualification_evidence(*build(3), protocol)

    forged_series, forged_envelopes = build(1)
    forged = next(row for row in forged_envelopes["envelopes"] if row["call_id"] == "agy-Q1")
    forged["envelope"]["envelope"]["error"] = "Some other failure."
    next(row for row in forged_series["attempts"] if row["call_id"] == "agy-Q1")[
        "envelope_sha256"] = probe.sha(probe.canonical(forged["envelope"]).encode())
    with pytest.raises(ValueError, match="not reproducible"):
        probe.validate_qualification_evidence(forged_series, forged_envelopes, protocol)

    output_failure_series, output_failure_envelopes = build(1)
    first = next(row for row in output_failure_series["attempts"] if row["call_id"] == "agy-Q1")
    first["failure_kind"] = "output"
    with pytest.raises(ValueError, match="invalid production retry"):
        probe.validate_qualification_evidence(output_failure_series, output_failure_envelopes, protocol)


def test_preflight_allows_production_retry_only_after_transient_failure() -> None:
    common = dict(kind="transport", provider="agy", commit_sha="c" * 40,
                  profile_sha256="a" * 64, binary_sha256="b" * 64,
                  writer_sha256="d" * 64, restart_diagnosis=None, restart_change=None)
    first = {"call_id": "t1", "kind": "transport", "provider": "agy", "series_id": "s1",
             "case_id": "F1:1", "commit_sha": "c" * 40, "profile_sha256": "a" * 64,
             "binary_sha256": "b" * 64, "writer_sha256": "d" * 64,
             "status": "technical_rejection", "failure_kind": "network",
             "retry_of": None, "checks": {"writer": False}}
    probe._preflight_series_position([first], series_id="s1", case_id="F1:1",
                                     retry_of="t1", **common)
    with pytest.raises(ValueError, match="invalid production retry"):
        probe._preflight_series_position([first], series_id="s1", case_id="F1:2",
                                         retry_of="t1", **common)
    retried = [first, dict(first, call_id="t1-r1", retry_of="t1"),
               dict(first, call_id="t1-r2", retry_of="t1-r1")]
    with pytest.raises(ValueError, match="invalid production retry"):
        probe._preflight_series_position(retried, series_id="s1", case_id="F1:1",
                                         retry_of="t1-r2", **common)
    probe._preflight_series_position(retried, series_id="s1", case_id="F1:2", **common)
    with pytest.raises(ValueError, match="invalid production retry"):
        probe._preflight_series_position([dict(first, failure_kind="output")], series_id="s1",
                                         case_id="F1:1", retry_of="t1", **common)
    timeout = dict(first, kind="print_timeout", case_id="T1", failure_kind="timeout")
    with pytest.raises(ValueError, match="invalid production retry"):
        probe._preflight_series_position(
            [timeout], series_id="s1", case_id="T1", retry_of="t1",
            **dict(common, kind="print_timeout"))


def _approval_invalid(row: dict, raw: dict) -> tuple[dict, dict]:
    """The measured AGY Q5 shape: a plan approval that leaves a new finding open."""
    rejected, envelope = copy.deepcopy(row), copy.deepcopy(raw)
    stored = load(FIXTURES / "reviewer-format-s6-v1.json")["cases"]
    finding = copy.deepcopy(stored["F3"]["envelope"]["structured_output"]["result"]["new_findings"][0])
    result = envelope["envelope"]["structured_output"]["result"]
    result["decision"], result["new_findings"] = "approved", [finding]
    envelope["technical_error"] = "AgentOutputError: native review result violates its bound contract"
    rejected.update(status="technical_rejection", failure_kind="output",
                    contract_rejection="approval-invalid", contract_retryable=True,
                    orchestrator_diagnostic="approval-invalid: native review decision must satisfy its approval contract",
                    checks={"writer_and_domain": False})
    return rejected, envelope


def _with_feedback(row: dict, raw: dict, previous: tuple[dict, dict]) -> tuple[dict, dict]:
    from dataclasses import replace
    from native_review_request import build_native_review_request

    spec = probe.build_qualification_spec(
        row["kind"], row["case_id"], run_id=f"qualification-{row['series_id']}-{row['call_id']}")
    feedback = probe._contract_retry_feedback(spec, *previous)
    bundle = build_native_review_request(replace(spec, retry_feedback=feedback),
                                         profile="antigravity")
    raw["envelope"]["structured_output"]["result"]["request_id"] = bundle.bound_context.request_id
    raw["request_document"] = json.loads(bundle.canonical_json)
    row.update(request_id=bundle.bound_context.request_id,
               request_sha256=probe.sha(bundle.canonical_json.encode()),
               retry_feedback={"prior_invocation_id": feedback.prior_invocation_id,
                               "rejection_code": feedback.rejection_code.value,
                               "correction_instruction": feedback.correction_instruction},
               output_bytes=len(probe.canonical(raw["envelope"]).encode()))
    return row, raw


def test_contract_retry_uses_production_feedback_and_final_response() -> None:
    protocol = load(EVIDENCE / "qualification-protocol-v5.json")
    assert protocol["production_retry"]["contract_max_retries_per_case"] == 2
    with pytest.raises(AssertionError):
        probe.validate_qualification(load(EVIDENCE / "qualification-protocol-v3.json"))

    def build(rejections: int, *, forge: bool = False, retryable: bool = True) -> tuple[dict, dict]:
        series = {"schema_version": "qualification-series-v1", "attempts": []}
        envelopes = {"schema_version": "qualification-envelopes-v1", "envelopes": []}
        for provider in ("agy", "claude"):  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
            series_id = f"quality-{provider}-s1"
            for number in range(1, 7):
                case_id, call_id, previous = f"Q{number}", f"{provider}-Q{number}", None
                attempts = rejections if provider == "agy" and case_id == "Q5" else 0
                for attempt in range(attempts + 1):
                    this_id = call_id if attempt == 0 else f"{call_id}-r{attempt}"
                    row, raw = _quality_success(provider, case_id, series_id=series_id,
                                                call_id=this_id, retry_of=None if previous is None else previous[0]["call_id"])
                    if previous is not None:
                        row, raw = _with_feedback(row, raw, previous)
                    if attempt < attempts:
                        row, raw = _approval_invalid(row, raw)
                        row["contract_retryable"] = retryable
                    if forge and attempt == attempts and previous is not None:
                        row["retry_feedback"]["correction_instruction"] = "Just approve."
                    probe.append_qualification_attempt(series, envelopes, attempt=row, envelope=raw)
                    previous = (row, raw)
        return series, envelopes

    series, envelopes = build(2)
    agy = probe.validate_qualification_evidence(series, envelopes, protocol)["quality-agy-s1"]
    assert agy["passed"] and agy["production_retries"] == 2
    assert agy["contract_rejections"] == ["agy-Q5", "agy-Q5-r1"]
    sources = probe.quality_blind_sources(series, envelopes, protocol)
    q5 = next(item for item in sources if item["provider"] == "agy" and item["case"] == "Q5")
    final = next(row for row in envelopes["envelopes"] if row["call_id"] == "agy-Q5-r2")
    assert q5["content"] == final["envelope"]["envelope"]["structured_output"]["result"]
    retry = next(row for row in series["attempts"] if row["call_id"] == "agy-Q5-r1")
    assert retry["retry_feedback"]["rejection_code"] == "approval-invalid"
    assert retry["retry_feedback"]["prior_invocation_id"] == "agy-Q5"

    with pytest.raises(ValueError, match="invalid production retry"):
        probe.validate_qualification_evidence(*build(3), protocol)
    with pytest.raises(ValueError, match="invalid production retry|only to a contract retry"):
        probe.validate_qualification_evidence(*build(1, retryable=False), protocol)
    with pytest.raises(ValueError, match="production guidance"):
        probe.validate_qualification_evidence(*build(1, forge=True), protocol)
    wrong_series, wrong_envelopes = build(1)
    rejected = next(row for row in wrong_series["attempts"] if row["call_id"] == "agy-Q5")
    rejected["contract_rejection"] = "schema-invalid"
    with pytest.raises(ValueError, match="not reproducible|production guidance"):
        probe.validate_qualification_evidence(wrong_series, wrong_envelopes, protocol)


def test_superseded_corpus_series_stays_visible_and_never_passes() -> None:
    protocol = load(EVIDENCE / "qualification-protocol-v5.json")
    assert protocol["quality"]["corpus_revision"] == 2
    with pytest.raises(AssertionError):
        probe.validate_qualification(load(EVIDENCE / "qualification-protocol-v4.json"))
    corpus = load(FIXTURES / "reviewer-quality-corpus-v1.json")
    assert corpus["revision"]["number"] == 2 and corpus["revision"]["changed_cases"] == ["Q5", "Q6"]
    series = {"schema_version": "qualification-series-v1", "attempts": []}
    envelopes = {"schema_version": "qualification-envelopes-v1", "envelopes": []}
    for number in range(1, 7):
        row, raw = _quality_success("claude", f"Q{number}", series_id="quality-claude-s1",  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
                                    call_id=f"c-Q{number}")
        probe.append_qualification_attempt(series, envelopes, attempt=row, envelope=raw)
    assert probe.validate_qualification_evidence(series, envelopes, protocol)["quality-claude-s1"]["passed"]  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
    stale = copy.deepcopy(series)
    next(row for row in stale["attempts"] if row["case_id"] == "Q5")["case_sha256"] = "e" * 64
    verdict = probe.validate_qualification_evidence(stale, envelopes, protocol)["quality-claude-s1"]  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
    assert verdict["superseded_corpus"] and not verdict["passed"] and verdict["failed_cases"] == []
    probe._preflight_series_position(
        stale["attempts"], kind="quality", provider="claude", series_id="quality-claude-s2",  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
        case_id="Q1", commit_sha="f" * 40, profile_sha256="a" * 64, binary_sha256="b" * 64,
        writer_sha256="d" * 64, restart_diagnosis="Corpus revision 2.", restart_change="Protocol v5.")
    with pytest.raises(ValueError, match="successful series cannot be restarted"):
        probe._preflight_series_position(
            series["attempts"], kind="quality", provider="claude", series_id="quality-claude-s2",  # allowlist:provider -- certification data: Slice-5 bound reviewer qualification
            case_id="Q1", commit_sha="f" * 40, profile_sha256="a" * 64, binary_sha256="b" * 64,
            writer_sha256="d" * 64, restart_diagnosis="Corpus revision 2.", restart_change="Protocol v5.")
    transport_row, transport_raw = _failed_attempt("transport", "F1:1", "agy", series_id="t", call_id="t1")
    transport_row["case_sha256"] = "e" * 64
    with pytest.raises(ValueError, match="case binding"):
        probe._verify_recorded_result(transport_row, transport_raw)


def test_negative_size_result_stays_evaluable_and_cannot_be_forged() -> None:
    from native_review_request import build_native_review_request

    protocol = load(EVIDENCE / "qualification-protocol-v5.json")
    stored = load(FIXTURES / "reviewer-format-s6-v1.json")["cases"]
    row, raw = _failed_attempt("large_output", "128", "agy", series_id="large-s1", call_id="l128")
    bundle = build_native_review_request(probe.build_qualification_spec(
        "large_output", "128", run_id="qualification-large-s1-l128"), profile="antigravity")
    response = copy.deepcopy(stored["F5"]["envelope"]["structured_output"]["result"])
    response["request_id"] = bundle.bound_context.request_id
    finding = response["new_findings"][0]
    finding["affected_paths"] = ["src/boundary_0001.py"]
    raw["envelope"] = {"status": "SUCCESS", "structured_output": {"result": response},
                       "json_schema": json.loads(bundle.provider_response_schema_json)}
    raw["technical_error"] = None
    measured = {"writer_and_domain": True, "finding_count": False, "unique_ids": False,
                "scope": True, "all_paths": False, "capacity_semantics": True}
    row.update(status="success", checks=measured, failure_kind=None,
               output_bytes=len(probe.canonical(raw["envelope"]).encode()))
    series = {"schema_version": "qualification-series-v1", "attempts": []}
    envelopes = {"schema_version": "qualification-envelopes-v1", "envelopes": []}
    probe.append_qualification_attempt(series, envelopes, attempt=row, envelope=raw)
    verdict = probe.validate_qualification_evidence(series, envelopes, protocol)["large-s1"]
    assert not verdict["passed"] and verdict["failed_cases"] == ["128"]
    forged = copy.deepcopy(series)
    forged["attempts"][0]["checks"] = {name: True for name in measured}
    with pytest.raises(ValueError, match="claim more"):
        probe.validate_qualification_evidence(forged, envelopes, protocol)
