from __future__ import annotations

import hashlib
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest
import native_provider_schema
import native_review_contract

from agent_adapters import AgentOutputError, create_reviewer_qualification_adapter
from agent_config import AgentSettings
from codex_review_adapter import NativeCodexReviewAdapter, codex_package_root  # allowlist:provider -- transport: reviewer adapter
from contracts import (
    AgentRole, ApprovalMarker, FindingClass, FindingOrigin, FindingRecord,
    FindingStatus, ValidationAttestation, ValidationRecord, ValidationStatus,
)
from native_provider_schema import (
    AGY_PROVIDER, ANTHROPIC_PROVIDER, NativeProviderSchemaError,
    assert_openai_structured_output_limits,
    assert_projected_provider_schema, normalize_transport_profile,
)
from native_review_contract import (
    NativeReviewContext, NativeReviewErrorCode, native_review_retry_guidance,
    native_review_provider_response_schema, parse_bound_native_contract_result,
)
from native_review_request import (
    NativeReviewEvidenceInput, NativeReviewKind, NativeReviewRequestSpec,
    NativeReviewRetryFeedback,
    build_native_review_request, validate_native_review_provider_response,
)
from schema_validation import SchemaMismatch, check_schema, validate_schema_document
from reviewer_input import REVIEW_INPUT_PATH_PLACEHOLDER


CASES = json.loads((Path(__file__).parent / "fixtures/reviewer-format-s6-v1.json").read_text())["cases"]


def _settings() -> AgentSettings:
    return AgentSettings("codex", "codex", "gpt-6-sol", 600, "medium")  # allowlist:provider -- profile configuration: fake reviewer


def _bundle(*, final: bool = False, maximum: int = 128, convergence: bool = False,
            retry_feedback: NativeReviewRetryFeedback | None = None,
            case: str | None = None):
    plan = case == "F1"
    final = final or case in {"F5", "F6"}
    convergence = convergence or case == "F4"
    red = case == "F3"
    no_validation = case == "F6"
    attestation = None if no_validation else ValidationAttestation(
        attestation_id="validation-fake", diff_fingerprint="c" * 64,
        expected_commands=("pytest",),
        records=(ValidationRecord(ValidationStatus.FAIL if red else ValidationStatus.PASS,
                                  "pytest", 1 if red else 0, "failed" if red else "passed"),),
        output_digest=hashlib.sha256(b"failed" if red else b"passed").hexdigest(),
        summary="failed" if red else "passed",
    )
    context = NativeReviewContext(
        run_id="run-fake", work_unit_id="1",
        operation="reviewer_plan_review" if plan else "reviewer_final_review" if final else "reviewer_slice_review",
        diff_fingerprint="c" * 64, reviewer=AgentRole.REVIEWER,
        approval_marker=ApprovalMarker.PLAN if plan else ApprovalMarker.FINAL_REVIEW if final else ApprovalMarker.SLICE,
        slice_id="PLAN" if plan else "FINAL" if final else "01",
        round_number=2 if convergence else 1,
        previous_findings=(FindingRecord(
            finding_id="R-01", finding_class=FindingClass.BLOCKER,
            status=FindingStatus.OPEN, summary="Existing finding",
            acceptance_test="Boundary is inclusive",
            origin=FindingOrigin("PLAN" if plan else "01", 1, AgentRole.REVIEWER),
        ),) if convergence else (),
        allow_new_findings=not convergence,
        validation_attestation=attestation,
        plan_artifact_path="docs/plan.md" if plan else None,
        max_new_findings=(2 if case == "F6" else maximum) if final else None,
    )
    return build_native_review_request(
        NativeReviewRequestSpec(
            context=context,
            review_kind=NativeReviewKind.PLAN if plan else NativeReviewKind.FINAL_REVIEW if final else NativeReviewKind.SLICE,
            target_branch="feature/fake", base_commit="b" * 40,
            authorized_paths=("src/limit.py", "tests/test_limit.py"),
            acceptance_criteria=("Boundary is inclusive.",),
            evidence=(NativeReviewEvidenceInput("diff", "diff", "diff --git a/src/limit.py b/src/limit.py"),),
            retry_feedback=retry_feedback,
        ), profile="codex-reviewer",  # allowlist:provider -- profile configuration: reviewer writer
    )


def _entry(tmp_path: Path) -> Path:
    package = tmp_path / "node_modules" / "@openai" / "codex"  # allowlist:provider -- transport: npm package layout
    (package / "bin").mkdir(parents=True)
    native = package / "node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex"  # allowlist:provider -- transport: installed npm layout
    native.parent.mkdir(parents=True)
    native.write_text("fake native binary")
    native.chmod(0o755)
    entry = package / "bin" / "codex.js"  # allowlist:provider -- transport: npm entry
    entry.write_text("fake entry")
    return entry


def _adapter(tmp_path: Path) -> NativeCodexReviewAdapter:  # allowlist:provider -- transport: reviewer factory
    adapter = create_reviewer_qualification_adapter(_settings())
    assert isinstance(adapter, NativeCodexReviewAdapter)  # allowlist:provider -- transport: reviewer registration
    adapter.provider_identity = SimpleNamespace(entry_path=str(_entry(tmp_path)), kind="verified")
    return adapter


def _prepared(tmp_path: Path, bundle=None):
    adapter = _adapter(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src/limit.py").write_text("def eligible(value): return value <= 10\n")
    return adapter, repo, bundle or _bundle()


def _input_digest(prepared) -> str:
    content = json.dumps(
        [(item.name, item.content) for item in prepared.components],
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def test_repeated_review_input_measurement_is_stable_and_wire_paths_are_real(tmp_path: Path) -> None:
    adapter, repo, bundle = _prepared(tmp_path)
    digests = []
    for current in (bundle, bundle, _bundle(case="F1")):
        with adapter.review_execution_boundary(repo, None):
            prepared = adapter.prepare_native_provider_input(current)
            digests.append(_input_digest(prepared))
            reviewer_input = adapter.invocation.reviewer_input
            assert reviewer_input is not None
            assert REVIEW_INPUT_PATH_PLACEHOLDER in next(
                item.content for item in prepared.components if item.name == "packet_manifest"
            )
            assert REVIEW_INPUT_PATH_PLACEHOLDER not in (prepared.stdin_text or "")
            assert str(reviewer_input.manifest_file) in (prepared.stdin_text or "")
            assert REVIEW_INPUT_PATH_PLACEHOLDER not in "".join(
                path.read_text(encoding="utf-8") for path in (
                    *reviewer_input.request_files, *reviewer_input.evidence_files,
                    *reviewer_input.manifest_pages, reviewer_input.manifest_file,
                )
            )
    assert digests[0] == digests[1]
    assert digests[0] != digests[2]


@pytest.mark.parametrize("case", ("F1", "F2", "F3", "F4", "F5", "F6"))
def test_six_fake_review_format_cases_use_bound_writer(tmp_path: Path, case: str) -> None:
    bundle = _bundle(case=case)
    adapter, repo, _ = _prepared(tmp_path, bundle)
    result = json.loads(json.dumps(CASES[case]["envelope"]["structured_output"]["result"]))
    result["request_id"] = bundle.bound_context.request_id
    with adapter.review_execution_boundary(repo, None):
        prepared = adapter.prepare_native_provider_input(bundle)
        assert adapter.role_binding.policy in prepared.stdin_text
        assert next(item.content for item in prepared.components if item.name == "system_policy") == adapter.role_binding.policy
        assert prepared.command[-1] == "-"
        assert "--sandbox" not in prepared.command
        assert normalize_transport_profile(
            "codex-reviewer", prepared.command,  # allowlist:provider -- transport: reviewer normalizer
            bound_package_root=codex_package_root(adapter.provider_identity.entry_path),  # allowlist:provider -- transport: identity-bound root
            bound_container=adapter.prepared_execution_root(),
            bound_runtime_dir=adapter.invocation.runtime_dir,
        ).provider == "codex"  # allowlist:provider -- transport: reviewer normalizer
        check_schema(bundle.provider_response_schema)
        assert_projected_provider_schema(bundle.provider_response_schema, provider="codex-reviewer")  # allowlist:provider -- profile configuration: strict reviewer guard
        assert_openai_structured_output_limits(bundle.provider_response_schema)
        assert '"prefixItems"' not in bundle.provider_response_schema_json
        assert '"oneOf"' not in bundle.provider_response_schema_json
        pending = [bundle.provider_response_schema]
        while pending:
            node = pending.pop()
            if isinstance(node, dict):
                assert not isinstance(node.get("const"), list)
                pending.extend(node.values())
            elif isinstance(node, list):
                pending.extend(node)
        adapter.seal_provider_input()
        adapter.before_provider_process()
        adapter.invocation.last_message_file.write_text(json.dumps({"result": result}))
        canonical = adapter.extract_output("", "", {})
        document = json.loads(canonical)
        validate_native_review_provider_response(document, bundle)
        parse_bound_native_contract_result(document, bundle.bound_context)


def test_final_writer_and_fake_result_support_512_findings(tmp_path: Path) -> None:
    bundle = _bundle(final=True, maximum=512)
    assert bundle.provider_response_schema["$defs"]["bound_final_review_completed"]["properties"]["new_findings"]["maxItems"] == 512
    result = json.loads(json.dumps(CASES["F5"]["envelope"]["structured_output"]["result"]))
    result["request_id"] = bundle.bound_context.request_id
    finding = result["new_findings"][0]
    result["new_findings"] = [{**finding, "finding_id": f"R-{index:02d}"} for index in range(1, 513)]
    validate_native_review_provider_response(result, bundle)
    parsed = parse_bound_native_contract_result(result, bundle.bound_context)
    assert parsed.stopped and parsed.stop_request.rule_id == "DISCOVERY_OUTPUT_LIMIT"


def _legacy_open_overlay_writer(monkeypatch: pytest.MonkeyPatch, **options):
    # Recreate the pre-fix projection without weakening the guard used for the assertion.
    with monkeypatch.context() as patch:
        patch.setattr(native_provider_schema, "_expand_reviewer_object_overlays", lambda node: node)
        patch.setattr(native_provider_schema, "assert_projected_provider_schema", lambda *_args, **_kwargs: None)
        patch.setattr(native_review_contract, "assert_projected_provider_schema", lambda *_args, **_kwargs: None)
        return _bundle(**options).provider_response_schema


def test_strict_guard_exposes_all_74_legacy_open_overlay_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    old = _legacy_open_overlay_writer(monkeypatch, case="F2")
    pending = [old]
    open_nodes = 0
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            open_nodes += int("properties" in node and node.get("additionalProperties") is not False)
            pending.extend(value for key, value in node.items() if key not in {"const", "enum"})
        elif isinstance(node, list):
            pending.extend(node)
    assert open_nodes == 74
    with pytest.raises(NativeProviderSchemaError, match=r"bound_approved_finding/anyOf/0/additionalProperties"):
        assert_projected_provider_schema(old, provider="codex-reviewer")  # allowlist:provider -- profile configuration: strict reviewer guard


def test_existing_reviewer_writers_keep_frozen_bytes() -> None:
    context = _bundle(case="F2").bound_context.context
    for profile in (ANTHROPIC_PROVIDER, AGY_PROVIDER):
        writer = native_review_provider_response_schema(context, profile=profile)
        payload = json.dumps(writer, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        assert hashlib.sha256(payload).hexdigest() == "3f812a2686cf2ffc9df212eba3f6d8f5a4f1edd449b342632502fbb5cc26ac2f"


@pytest.mark.parametrize(("name", "options", "fixture"), (
    ("plan", {"case": "F1"}, "F1"),
    ("plan_after", {"case": "F1", "convergence": True}, "F4"),
    ("slice_first", {"case": "F2"}, "F2"),
    ("slice_after", {"case": "F2", "convergence": True}, "F4"),
    ("convergence", {"case": "F4"}, "F4"),
    ("red", {"case": "F3"}, "F3"),
    ("final_512", {"final": True, "maximum": 512}, "F5"),
    ("final_stop", {"case": "F6"}, "F6"),
))
def test_all_review_operations_keep_writer_acceptance_and_size(
    monkeypatch: pytest.MonkeyPatch, name: str, options: dict, fixture: str,
) -> None:
    bundle = _bundle(**options)
    writer = bundle.provider_response_schema
    old = _legacy_open_overlay_writer(monkeypatch, **options)
    assert_projected_provider_schema(writer, provider="codex-reviewer")  # allowlist:provider -- profile configuration: strict reviewer guard
    assert_openai_structured_output_limits(writer)
    valid = copy.deepcopy(CASES[fixture]["envelope"]["structured_output"]["result"])
    valid["request_id"] = bundle.bound_context.request_id
    validate_native_review_provider_response(valid, bundle)
    examples = [valid]
    extra = copy.deepcopy(valid)
    extra["unknown_field"] = 1
    examples.append(extra)
    missing = copy.deepcopy(valid)
    missing.pop("request_id")
    examples.append(missing)
    wrong_type = copy.deepcopy(valid)
    wrong_type["request_id"] = 42
    examples.append(wrong_type)
    if valid["result_type"] == "review_result":
        invalid_evidence = copy.deepcopy(valid)
        invalid_evidence["review_evidence"] = None if valid["decision"] == "approved" else {"dimensions": ""}
        examples.append(invalid_evidence)
        if valid["new_findings"]:
            invalid_pair = copy.deepcopy(valid)
            invalid_pair["new_findings"][0]["evidence_anchor_sha256"] = "a" * 64
            examples.append(invalid_pair)
        if valid["status_changes"]:
            invalid_closure = copy.deepcopy(valid)
            invalid_closure["status_changes"][0]["closure"] = None
            examples.append(invalid_closure)
    for index, example in enumerate(examples):
        accepted = []
        for schema in (old, writer):
            try:
                validate_schema_document({"result": example}, schema)
                accepted.append(True)
            except SchemaMismatch:
                accepted.append(False)
        assert accepted[0] == accepted[1], (name, index)
        assert accepted[0] is (index == 0), (name, index)


@pytest.mark.parametrize("mutation", (
    lambda x: x.insert(2, "--sandbox"),
    lambda x: x.insert(2, "--dangerously-bypass-approvals-and-sandbox"),
    lambda x: x.insert(2, "--add-dir"),
    lambda x: x.insert(2, "--full-auto"),
    lambda x: x.remove("--ignore-rules"),
    lambda x: x.remove("--ignore-user-config"),
    lambda x: x.remove("apps"),
    lambda x: x.__setitem__(x.index("project_doc_max_bytes=0"), "project_doc_max_bytes=100"),
    lambda x: x.__setitem__(x.index('web_search="disabled"'), 'web_search="enabled"'),
    lambda x: x.__setitem__(x.index('shell_environment_policy.inherit="core"'), 'shell_environment_policy.inherit="all"'),
    lambda x: x.__setitem__(x.index('default_permissions="dao-reviewer"'), 'default_permissions="default"'),
    lambda x: x.__setitem__(x.index("-C") + 1, str(Path(x[x.index("-C") + 1]).parent)),
    lambda x: x.__setitem__(x.index("--output-schema") + 1, x[x.index("--output-last-message") + 1]),
    lambda x: x.__setitem__(x.index("--output-last-message") + 1, x[x.index("--output-schema") + 1]),
    lambda x: x.__setitem__(next(i for i, item in enumerate(x) if item.startswith("permissions.dao-reviewer=")), 'permissions.dao-reviewer={filesystem={":root"="read"}}'),
    lambda x: x.__setitem__(next(i for i, item in enumerate(x) if item.startswith("permissions.dao-reviewer=")), x[next(i for i, item in enumerate(x) if item.startswith("permissions.dao-reviewer="))].replace('="read"', '="write"', 1)),
    lambda x: x.__setitem__(next(i for i, item in enumerate(x) if item.startswith("permissions.dao-reviewer=")), x[next(i for i, item in enumerate(x) if item.startswith("permissions.dao-reviewer="))].replace('="read"', '="deny"', 1)),
    lambda x: x.__setitem__(next(i for i, item in enumerate(x) if item.startswith("permissions.dao-reviewer=")), x[next(i for i, item in enumerate(x) if item.startswith("permissions.dao-reviewer="))].replace('":workspace_roots"', '"/home"="read",":workspace_roots"')),
    lambda x: x.extend(["--unknown"]),
))
def test_review_command_rejects_broader_or_unknown_flags(tmp_path: Path, mutation) -> None:
    adapter, repo, bundle = _prepared(tmp_path)
    with adapter.review_execution_boundary(repo, None):
        command = list(adapter.prepare_native_provider_input(bundle).command)
        mutation(command)
        with pytest.raises(NativeProviderSchemaError):
            normalize_transport_profile(
                "codex-reviewer", command,  # allowlist:provider -- transport: deny broader CLI form
                bound_package_root=codex_package_root(adapter.provider_identity.entry_path),  # allowlist:provider -- transport: identity-bound root
                bound_container=adapter.prepared_execution_root(),
                bound_runtime_dir=adapter.invocation.runtime_dir,
            )


def test_package_root_from_bound_entry_symlink_and_fail_closed(tmp_path: Path) -> None:
    entry = _entry(tmp_path)
    native = entry.parents[1] / "node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex"  # allowlist:provider -- transport: installed npm layout
    assert native.is_file() and native.stat().st_mode & 0o111
    link = tmp_path / "codex"  # allowlist:provider -- transport: npm entry symlink
    link.symlink_to(entry)
    assert codex_package_root(str(link)) == entry.parents[1]  # allowlist:provider -- transport: identity-bound root
    entry.unlink()
    with pytest.raises(AgentOutputError):
        codex_package_root(str(link))  # allowlist:provider -- transport: missing npm package
    entry.write_text("fake")
    native.unlink()
    with pytest.raises(AgentOutputError):
        codex_package_root(str(link))  # allowlist:provider -- transport: missing native binary
    other = tmp_path / "other"
    other.write_text("fake")
    with pytest.raises(AgentOutputError):
        codex_package_root(str(other))  # allowlist:provider -- transport: foreign entry
    nested = tmp_path / "node_modules/@openai/codex/node_modules/@openai/codex"  # allowlist:provider -- transport: ambiguous npm layout
    (nested / "bin").mkdir(parents=True)
    nested_entry = nested / "bin/codex.js"  # allowlist:provider -- transport: ambiguous npm entry
    nested_entry.write_text("fake")
    with pytest.raises(AgentOutputError, match="ambiguous"):
        codex_package_root(str(nested_entry))  # allowlist:provider -- transport: nested package rejection


def test_package_root_rejects_multiple_inert_and_escaping_binaries(tmp_path: Path) -> None:
    entry = _entry(tmp_path)
    package = entry.parents[1]
    native = package / "node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex"  # allowlist:provider -- transport: installed npm layout
    second = package / "node_modules/@openai/codex-linux-arm64/vendor/aarch64-unknown-linux-musl/bin/codex"  # allowlist:provider -- transport: second platform
    second.parent.mkdir(parents=True)
    second.write_text("another fake binary")
    second.chmod(0o755)
    with pytest.raises(AgentOutputError):
        codex_package_root(str(entry))  # allowlist:provider -- transport: multiple platform binaries
    second.unlink()
    native.chmod(0o644)
    with pytest.raises(AgentOutputError):
        codex_package_root(str(entry))  # allowlist:provider -- transport: nonexecutable native binary
    native.unlink()
    outside = tmp_path / "outside-codex"  # allowlist:provider -- transport: external symlink target
    outside.write_text("fake external binary")
    outside.chmod(0o755)
    native.symlink_to(outside)
    with pytest.raises(AgentOutputError):
        codex_package_root(str(entry))  # allowlist:provider -- transport: symlink escapes package root


def test_profile_root_must_match_bound_package_identity(tmp_path: Path) -> None:
    adapter, repo, bundle = _prepared(tmp_path)
    with adapter.review_execution_boundary(repo, None):
        command = adapter.prepare_native_provider_input(bundle).command
        other = tmp_path / "other/node_modules/@openai/codex"  # allowlist:provider -- transport: foreign package root
        other.mkdir(parents=True)
        with pytest.raises(NativeProviderSchemaError, match="bound identity"):
            normalize_transport_profile(
                "codex-reviewer", command, bound_package_root=other,  # allowlist:provider -- transport: identity mismatch
                bound_container=adapter.prepared_execution_root(),
                bound_runtime_dir=adapter.invocation.runtime_dir,
            )
        package = codex_package_root(adapter.provider_identity.entry_path)  # allowlist:provider -- transport: bound package root
        native = package / "node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex"  # allowlist:provider -- transport: installed npm layout
        native.chmod(0o644)
        with pytest.raises(NativeProviderSchemaError, match="unsafe"):
            normalize_transport_profile(
                "codex-reviewer", command, bound_package_root=package,  # allowlist:provider -- transport: same package validator
                bound_container=adapter.prepared_execution_root(),
                bound_runtime_dir=adapter.invocation.runtime_dir,
            )


def test_container_change_after_seal_prevents_process(tmp_path: Path) -> None:
    adapter, repo, bundle = _prepared(tmp_path)
    with adapter.review_execution_boundary(repo, None):
        adapter.prepare_native_provider_input(bundle)
        adapter.seal_provider_input()
        evidence = adapter.prepared_execution_root() / "repo" / "src" / "limit.py"
        evidence.chmod(0o644)
        evidence.write_text("tampered")
        with pytest.raises(AgentOutputError, match="changed before start"):
            adapter.before_provider_process()


def test_container_change_during_fake_process_fails_postcheck(tmp_path: Path) -> None:
    adapter, repo, bundle = _prepared(tmp_path)
    with pytest.raises(AgentOutputError, match="changed during review"):
        with adapter.review_execution_boundary(repo, None):
            adapter.prepare_native_provider_input(bundle)
            adapter.seal_provider_input()
            adapter.before_provider_process()
            evidence = adapter.prepared_execution_root() / "repo/src/limit.py"
            evidence.chmod(0o644)
            evidence.write_text("tampered")


def test_reviewer_runtime_cannot_be_created_inside_source_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, repo, _bundle_value = _prepared(tmp_path)
    monkeypatch.setattr(tempfile, "tempdir", str(repo))
    with pytest.raises(AgentOutputError, match="overlaps"):
        with adapter.review_execution_boundary(repo, None):
            pass


@pytest.mark.parametrize("payload", (
    '{"result":{},"result":{}}',
    '{"result":{"decision":NaN}}',
))
def test_last_message_rejects_duplicate_keys_and_nonfinite_json(tmp_path: Path, payload: str) -> None:
    adapter, repo, bundle = _prepared(tmp_path)
    with adapter.review_execution_boundary(repo, None):
        adapter.prepare_native_provider_input(bundle)
        adapter.invocation.last_message_file.write_text(payload)
        with pytest.raises(AgentOutputError, match="invalid JSON"):
            adapter.extract_output("", "", {})


def test_contract_rejection_retries_with_bound_feedback_and_fake_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent_runtime

    adapter, repo, initial = _prepared(tmp_path)
    response = json.loads(json.dumps(CASES["F1"]["envelope"]["structured_output"]["result"]))

    def fake_process(current, _prompt, *, prepared_provider_input, **_kwargs):
        assert current is adapter
        assert prepared_provider_input.stdin_text.startswith(adapter.role_binding.policy)
        adapter.before_provider_process()
        adapter.invocation.last_message_file.write_text(json.dumps({"result": response}))
        return adapter.extract_output("", "", {})

    monkeypatch.setattr(agent_runtime, "run_agent", fake_process)
    response["request_id"] = initial.bound_context.request_id
    response["decision"] = "invalid"
    with pytest.raises(AgentOutputError, match="local result schema"):
        agent_runtime.run_native_review_agent(
            adapter, initial, config=agent_runtime.OrchestratorConfig(repo_root=repo),
            shorten=lambda value, _limit: value or "", operation="reviewer_slice_review",
            binding_fingerprint="c" * 64,
        )
    feedback = NativeReviewRetryFeedback(
        prior_invocation_id="review-attempt-1",
        rejection_code=NativeReviewErrorCode.SCHEMA_INVALID,
        correction_instruction=native_review_retry_guidance(NativeReviewErrorCode.SCHEMA_INVALID),
    )
    retry = _bundle(retry_feedback=feedback)
    assert retry.bound_context.request_id != initial.bound_context.request_id
    assert retry.document["retry_feedback"]["correction_instruction"] == feedback.correction_instruction
    response["request_id"] = retry.bound_context.request_id
    response["decision"] = "approved"
    accepted = agent_runtime.run_native_review_agent(
        adapter, retry, config=agent_runtime.OrchestratorConfig(repo_root=repo),
        shorten=lambda value, _limit: value or "", operation="reviewer_slice_review",
        binding_fingerprint="c" * 64,
    )
    assert accepted.result.approval is True


def test_strict_reviewer_writers_declare_a_type_on_every_schema() -> None:
    # Live smoke 2026-09-30: OpenAI rejected an untyped const property with HTTP 400.
    from native_provider_schema import _untyped_schema_pointers

    for kwargs in ({}, {"final": True, "maximum": 512}):
        schema = json.loads(_bundle(**kwargs).provider_response_schema_json)
        assert _untyped_schema_pointers(schema) == []
        kind = schema["$defs"]["prose_acceptance"]["properties"]["kind"]
        assert kind["type"] == "string" and "const" in kind


def test_strict_guard_rejects_an_untyped_literal_schema() -> None:
    from native_provider_schema import NativeProviderSchemaError, assert_projected_provider_schema

    schema = {
        "type": "object", "properties": {"kind": {"const": "text"}},
        "required": ["kind"], "additionalProperties": False,
    }
    with pytest.raises(NativeProviderSchemaError, match="must declare a type"):
        assert_projected_provider_schema(schema, provider="codex-reviewer")  # allowlist:provider -- profile configuration: reviewer guard
