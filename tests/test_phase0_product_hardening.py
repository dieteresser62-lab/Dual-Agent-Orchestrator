"""Product corrections from s2, exercised without installed provider binaries."""
import copy
from dataclasses import replace, asdict, FrozenInstanceError
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_adapters import AgentOutputError, NativeCodexAdapter  # allowlist:provider -- profile configuration: provider-specific transport fixture
from agent_config import AgentSettings, isolation_options_digest
from model_catalog import bind_catalog_models, hardened_reviewer_catalog, reviewer_model_row_sha256
from native_implementer_contract import parse_bound_native_implementer_contract_result
from native_implementer_request import build_native_implementer_request
from native_provider_schema import NativeProviderSchemaError, normalize_transport_profile
from scripts.qualification import offline_boundary as boundary, phase0_trace
from test_claude_implementer_adapter import _adapter, _repo, _prepared, _valid_plan  # allowlist:provider -- profile configuration: provider-specific transport fixture
from test_codex_review_adapter import _prepared as reviewer_prepared  # allowlist:provider -- profile configuration: provider-specific transport fixture
from test_native_implementer_request import _spec
from test_workflow_requests import _codex_bundle  # allowlist:provider -- profile configuration: provider-specific transport fixture


FIXTURES = Path(__file__).parent / "fixtures/phase0-traces/s2"


def catalog():
    return {"models": [{"slug": slug, "multi_agent_version": "v2", "multi_agent_reasoning_effort": "high",
                         "experimental_supported_tools": ["web_search", "send_message"],
                         "supports_search_tool": True, "web_search_tool_type": "web_search", "tool_mode": "code_mode_only"}
                        for slug in ("gpt-6.1-sol", "gpt-6-sol")]}


def test_transient_transport_bindings_preserve_the_public_configuration_projection():
    settings = AgentSettings("codex", "provider", "gpt-6.1-sol", 60, "medium",  # allowlist:provider -- profile configuration: transient runtime metadata
                             reviewer_model_catalog_json=hardened_reviewer_catalog(catalog(), "gpt-6.1-sol"),
                             fixed_environment=(("FIXED_TEST_VALUE", "1"),))
    public = asdict(settings)
    assert "reviewer_model_catalog_json" not in public and "fixed_environment" not in public
    updated = replace(settings, effort="high")
    assert updated.reviewer_model_catalog_json == settings.reviewer_model_catalog_json
    assert updated.fixed_environment == settings.fixed_environment
    assert isolation_options_digest(updated) == isolation_options_digest(settings)
    with pytest.raises(FrozenInstanceError):
        settings.reviewer_model_catalog_json = None


def test_all_catalog_models_are_hardened_without_mutating_source():
    source = catalog()
    original = copy.deepcopy(source)
    result = json.loads(hardened_reviewer_catalog(source, "gpt-6.1-sol"))
    assert source == original
    for row in result["models"]:
        assert set(row) == {"slug", "tool_mode", "experimental_supported_tools", "supports_search_tool"}
        assert row["experimental_supported_tools"] == []
        assert row["supports_search_tool"] is False
    assert hardened_reviewer_catalog(result, "gpt-6.1-sol") == hardened_reviewer_catalog(source, "gpt-6.1-sol")


@pytest.mark.parametrize("source", [None, {}, {"models": []}, {"models": [{"slug": "another-model"}]}])
def test_missing_catalog_or_model_fails_before_reviewer_start(tmp_path, source):
    adapter, repo, bundle = reviewer_prepared(tmp_path)
    adapter.settings = replace(adapter.settings, reviewer_model_catalog_json=json.dumps(source) if source else None)
    with adapter.review_execution_boundary(repo, None):
        with pytest.raises(AgentOutputError, match="catalog"):
            adapter.prepare_native_provider_input(bundle)
        assert adapter._started is False


def test_catalog_is_private_runtime_argument_and_bound_to_selected_model(tmp_path):
    adapter, repo, bundle = reviewer_prepared(tmp_path)
    with adapter.review_execution_boundary(repo, None):
        prepared = adapter.prepare_native_provider_input(bundle)
        path = adapter.invocation.runtime_dir / "model-catalog.json"
        assert "model_catalog_json=" + json.dumps(str(path)) in prepared.command
        assert path.stat().st_mode & 0o777 == 0o600
        assert not path.is_relative_to(adapter.prepared_execution_root())
        assert json.loads(path.read_text())["models"][0]["slug"] == adapter.model
        path.write_text(json.dumps(catalog()))
        with pytest.raises(NativeProviderSchemaError, match="catalog"):
            normalize_transport_profile("codex-reviewer", prepared.command,  # allowlist:provider -- profile configuration: private catalog grammar
                bound_package_root=Path(adapter.provider_identity.entry_path).resolve().parents[1],
                bound_container=adapter.prepared_execution_root(), bound_runtime_dir=adapter.invocation.runtime_dir,
                bound_model_row_sha256=reviewer_model_row_sha256(adapter.settings.reviewer_model_catalog_json, adapter.model))


def test_changed_hardened_catalog_is_rejected_at_the_process_boundary(tmp_path):
    adapter, repo, bundle = reviewer_prepared(tmp_path)
    with adapter.review_execution_boundary(repo, None):
        adapter.prepare_native_provider_input(bundle)
        adapter.seal_provider_input()
        path = adapter.invocation.runtime_dir / "model-catalog.json"
        changed = json.loads(path.read_text())
        changed["models"][0]["context_window"] = 123
        path.write_text(hardened_reviewer_catalog(changed, adapter.model))
        with pytest.raises(AgentOutputError, match="catalog changed before start"):
            adapter.before_provider_process()
        assert adapter._started is False


def test_catalog_preserves_required_shell_and_instruction_metadata():
    source = catalog()
    retained = {"apply_patch_tool_type": "freeform", "shell_type": "unified_exec", "tool_mode": "code_mode_only",
                "include_skills_usage_instructions": False, "include_plugin_usage_instructions": False,
                "include_apps_usage_instructions": False, "node_repl_auto_review_required": True,
                "node_repl_disabled": False}
    source["models"][0].update(retained)
    row = json.loads(hardened_reviewer_catalog(source, "gpt-6.1-sol"))["models"][0]
    assert {key: row[key] for key in retained} == retained


@pytest.mark.parametrize("change", ["other-model", "cache-metadata", "bound-model", "unhardened", "symlink"])
def test_catalog_file_is_hardened_and_bound_to_only_selected_row(tmp_path, change):
    adapter, repo, bundle = reviewer_prepared(tmp_path)
    with adapter.review_execution_boundary(repo, None):
        prepared = adapter.prepare_native_provider_input(bundle)
        adapter.seal_provider_input()
        path = adapter.invocation.runtime_dir / "model-catalog.json"
        source = json.loads(path.read_text())
        # Keep a second model even if the minimal adapter fixture only had one.
        source["models"].append({"slug": "unrelated-model", "description": "new"})
        if change == "cache-metadata":
            source.update(fetched_at="tomorrow", etag="new")
        if change == "bound-model":
            source["models"][0]["base_instructions"] = "different"
        text = hardened_reviewer_catalog(source, adapter.model)
        if change == "unhardened":
            source = json.loads(text)
            source["models"][0]["multi_agent_version"] = "v2"
            text = json.dumps(source, sort_keys=True, separators=(",", ":"))
        path.write_text(text)
        if change == "symlink":
            saved = path.with_suffix(".saved")
            path.rename(saved)
            path.symlink_to(saved)
        def normalize():
            return normalize_transport_profile("codex-reviewer", prepared.command,  # allowlist:provider -- profile configuration: selected model normalization
                bound_package_root=Path(adapter.provider_identity.entry_path).resolve().parents[1],
                bound_container=adapter.prepared_execution_root(), bound_runtime_dir=adapter.invocation.runtime_dir,
                bound_model_row_sha256=reviewer_model_row_sha256(adapter.settings.reviewer_model_catalog_json, adapter.model))
        if change in {"bound-model", "unhardened", "symlink"}:
            with pytest.raises(NativeProviderSchemaError, match="catalog"):
                normalize()
            with pytest.raises(AgentOutputError, match="catalog"):
                adapter.before_provider_process()
        else:
            normalize()
            adapter.before_provider_process()
            assert adapter._started


def test_catalog_normalizer_rejects_unbound_row_digest(tmp_path):
    adapter, repo, bundle = reviewer_prepared(tmp_path)
    with adapter.review_execution_boundary(repo, None):
        prepared = adapter.prepare_native_provider_input(bundle)
        with pytest.raises(NativeProviderSchemaError, match="catalog"):
            normalize_transport_profile("codex-reviewer", prepared.command,  # allowlist:provider -- profile configuration: unbound row digest
                bound_package_root=Path(adapter.provider_identity.entry_path).resolve().parents[1],
                bound_container=adapter.prepared_execution_root(), bound_runtime_dir=adapter.invocation.runtime_dir)


@pytest.mark.parametrize("mutation", ["removed-opt-in", "safe-metadata"])
def test_run_catalog_identity_binds_sanitized_bytes(mutation):
    identity = SimpleNamespace(kind="verified", digest="bound", entry_path="/fixture/provider", launch_prefix=("/fixture/provider",))
    slots = {"reviewer": AgentSettings("codex", "provider", "gpt-6.1-sol", 60, "medium")}  # allowlist:provider -- profile configuration: isolated catalog identity
    source = catalog()
    bind_catalog_models(slots, {"reviewer": identity}, lambda c: (0, json.dumps(source), ""))
    first = isolation_options_digest(slots["reviewer"])
    source["models"][0]["multi_agent_version"] = "future" if mutation == "removed-opt-in" else "v2"
    if mutation == "safe-metadata":
        source["models"][0]["context_window"] = 200000
    bind_catalog_models(slots, {"reviewer": identity}, lambda c: (0, json.dumps(source), ""), resume=True)
    assert (first == isolation_options_digest(slots["reviewer"])) == (mutation == "removed-opt-in")


@pytest.mark.parametrize("name,expected", [("reviewer-tools-hardened.json", "passed"), ("reviewer-tools-collaboration.json", "failed")])
def test_real_additional_tools_namespaces(name, expected):
    body = json.loads((FIXTURES / name).read_text())
    result = phase0_trace.reviewer_tool_surface([body])
    assert result["status"] == expected
    if expected == "passed":
        assert result["namespaces"] == {"functions": ["exec", "request_user_input", "wait"]}
    assert ("collaboration" in result["namespaces"]) == (expected == "failed")


@pytest.mark.parametrize("extra", ["web_search", "spawn_agent", "send_message", "request_user_input_async"])
def test_extra_function_in_allowed_namespace_is_rejected(extra):
    body = json.loads((FIXTURES / "reviewer-tools-hardened.json").read_text())
    body["input"][0]["tools"][0]["tools"].append({"type": "function", "name": extra})
    assert phase0_trace.reviewer_tool_surface([body])["status"] == "failed"


@pytest.mark.parametrize("namespace", ["clock", "unexpected"])
def test_extra_namespace_is_rejected_even_without_collaboration(namespace):
    body = json.loads((FIXTURES / "reviewer-tools-hardened.json").read_text())
    body["input"][0]["tools"].append({"type": "namespace", "name": namespace,
                                     "tools": [{"type": "function", "name": "sleep"}]})
    assert phase0_trace.reviewer_tool_surface([body])["status"] == "failed"


@pytest.mark.parametrize("existing,nonempty,symlink", [(False, False, False), (True, False, False), (False, True, False), (False, False, True)])
def test_write_placeholder_cleanup_preserves_operator_state(tmp_path, caplog, existing, nonempty, symlink):
    root = _repo(tmp_path)
    path = root / ".claude/.cc-writes"  # allowlist:provider -- profile configuration: provider-specific transport fixture
    if existing:
        path.mkdir(parents=True)
    adapter = _adapter(root)
    bundle = build_native_implementer_request(_spec(), profile="claude-implementer")  # allowlist:provider -- profile configuration: placeholder fixture
    adapter.prepare_native_provider_input(bundle)
    try:
        if not existing:
            path.parent.mkdir(exist_ok=True)
            if symlink:
                target = tmp_path / "operator-owned"
                target.mkdir()
                path.symlink_to(target, target_is_directory=True)
            else:
                path.mkdir()
        if nonempty:
            (path / "operator-file").write_text("keep")
        removed = adapter.remove_sandbox_placeholders()
        if existing or nonempty or symlink:
            assert path.exists()
            assert path not in removed
            if nonempty or symlink:
                assert "retained" in caplog.text and str(path) in caplog.text
        else:
            assert removed == (path, path.parent)
            assert not path.parent.exists()
    finally:
        adapter.cleanup()


def test_existing_parent_survives_new_empty_child_cleanup(tmp_path):
    root = _repo(tmp_path)
    parent = root / ".claude"  # allowlist:provider -- profile configuration: provider-specific transport fixture
    parent.mkdir()
    adapter = _adapter(root)
    adapter.prepare_native_provider_input(build_native_implementer_request(_spec(), profile="claude-implementer"))  # allowlist:provider -- profile configuration: existing directory preservation
    (parent / ".cc-writes").mkdir()
    adapter.cleanup()
    assert parent.is_dir() and not (parent / ".cc-writes").exists()


def test_refusal_fallback_environment_is_fixed_and_normalized(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK", "0")  # allowlist:provider -- profile configuration: provider-specific transport fixture
    root, adapter, _, prepared = _prepared(tmp_path)
    try:
        assert adapter.env["CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK"] == "1"  # allowlist:provider -- profile configuration: provider-specific transport fixture
        for value in (None, "0", ""):
            env = dict(adapter.env)
            env.pop("CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK")  # allowlist:provider -- profile configuration: provider-specific transport fixture
            if value is not None:
                env["CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK"] = value  # allowlist:provider -- profile configuration: provider-specific transport fixture
            with pytest.raises(NativeProviderSchemaError, match="refusal fallback environment"):
                normalize_transport_profile("claude-implementer", prepared.command,  # allowlist:provider -- profile configuration: fixed environment normalizer
                    bound_environment=env, bound_settings_json=prepared.command[prepared.command.index("--settings") + 1],
                    bound_scratch=adapter._scratch, bound_protected_paths=adapter._protected_paths, bound_repository_root=root)
    finally:
        adapter.cleanup()


@pytest.mark.parametrize("mode", ["fallback-event", "different-model", "model-usage", "refusal-error", "refusal-end", "unchanged"])
def test_model_fidelity_and_refusal_failures(tmp_path, mode):
    _, adapter, bundle, _ = _prepared(tmp_path)
    events = [{"type": "system", "subtype": "init", "model": "model-a"},
              {"type": "assistant", "message": {"model": "model-b" if mode == "different-model" else "model-a", "content": []}}]
    if mode == "fallback-event":
        events.append({"type": "system", "subtype": "model_refusal_fallback", "original_model": "model-a", "fallback_model": "model-b"})
    final = {"type": "result", "subtype": "success", "is_error": False, "structured_output": {"result": _valid_plan(bundle)}}
    if mode == "model-usage":
        final["modelUsage"] = {"model-a": {}, "model-b": {}}
    if mode == "refusal-error":
        final.update(subtype="error_during_execution", is_error=True, errors=["model refusal"])
    if mode == "refusal-end":
        events[1]["message"]["stop_reason"] = "refusal"
    events.append(final)
    try:
        stdout = "\n".join(json.dumps(e) for e in events)
        if mode == "unchanged":
            assert json.loads(adapter.extract_output(stdout, "", {}))["request_id"] == bundle.bound_context.request_id
        else:
            with pytest.raises(AgentOutputError, match="reported an error" if mode.startswith("refusal") else "model switch observed") as caught:
                adapter.extract_output(stdout, "", {})
            assert caught.value.provider_data["init_model"] == "model-a"
            assert caught.value.kind_hint.value == "output"
        assert adapter.metadata["init_model"] == "model-a"
        assert "actual_models" in adapter.metadata
    finally:
        adapter.cleanup()


def test_model_switch_failure_preserves_denial_and_model_record_metadata(tmp_path):
    from agent_runtime import classify_agent_failure
    _, adapter, bundle, _ = _prepared(tmp_path)
    events = [{"type": "system", "subtype": "init", "model": "model-a"},
              {"type": "assistant", "message": {"model": "model-b", "content": []}},
              {"type": "result", "subtype": "success", "is_error": False,
               "permission_denials": [{"tool_name": "Write", "tool_input": {"file_path": str(adapter._scratch / "new-file")}}],
               "structured_output": {"result": _valid_plan(bundle)}}]
    try:
        with pytest.raises(AgentOutputError, match="model switch observed") as caught:
            adapter.extract_output("\n".join(json.dumps(e) for e in events), "", {})
        failure = classify_agent_failure("provider", caught.value, invocation_id="switch-test")
        assert failure.kind.value == "output"
        assert caught.value.provider_data["actual_models"] == ["model-b"]
        assert adapter.metadata["permission_denials"][0]["disposition"] == "tolerated"
        from agent_runtime import ProviderAttemptLifecycle, _ProviderAttemptInvocation
        from artifact_models import ArtifactRecord, Role
        from test_artifact_models import _attempt_for_pair, _record, CREATED_AT
        captured = []
        invocation = _ProviderAttemptInvocation(ProviderAttemptLifecycle(
            start=lambda *args: "handle", terminal=lambda *args, **kw: captured.append((args, kw)), monotonic_fn=lambda: 1.0))
        invocation.begin(None, None)
        invocation.finish(failure.kind, adapter.metadata)
        args, telemetry = captured[0]
        assert args[2] == "output" and telemetry["actual_models"] == ("model-b",)
        started = _attempt_for_pair("claude", Role.IMPLEMENTER)  # allowlist:provider -- certification data: failed implementer model record
        terminal = replace(started, phase="failed", ended_at=CREATED_AT, duration_seconds=1, failure_kind="output", **telemetry)
        document = _record(terminal).to_dict()
        assert document["payload"]["init_model"] == "model-a"
        assert document["payload"]["actual_models"] == ["model-b"]
        assert ArtifactRecord.from_dict(document).to_dict() == document
    finally:
        adapter.cleanup()


@pytest.mark.parametrize("profile", ["codex", "claude-implementer"])  # allowlist:provider -- profile configuration: both implementer native contracts
@pytest.mark.parametrize("rule,rationale,paths", [
    ("OPERATOR-PREREQUISITE-MISSING", "Missing prerequisite: Tool\nWhy it cannot be self-provided: Requires operator\nOperator action: Install tool", []),
    ("SCOPE-EXTENSION-REQUESTED", "Required paths: src/new.py\nWhy required for current Slice: Needed to implement", ["src/new.py"]),
    *[(rule, "Die Ausführung ist aus dem genannten Grund blockiert.", []) for rule in
      ("BRANCH-MISMATCH", "VALIDATION-UNAVAILABLE", "UNEXPECTED-PATH", "CONTRACT-UNCLEAR")],
])
def test_transported_stop_instructions_validate_with_both_adapters(tmp_path, profile, rule, rationale, paths):
    spec = _spec()
    from gates import render_implementer_stop_instructions
    bundle = build_native_implementer_request(replace(spec, work_context=render_implementer_stop_instructions()), profile=profile)
    instructions = bundle.document["work_context"]
    assert rule in instructions
    if rule in {"OPERATOR-PREREQUISITE-MISSING", "SCOPE-EXTENSION-REQUESTED"}:
        assert all(line.split(":", 1)[0] + ":" in instructions for line in rationale.splitlines())
    result = {"schema_version": "native-agent-implementer-result-v3", "request_id": bundle.bound_context.request_id,
              "result_type": "stop_result", "rule_id": rule, "rationale": rationale, "remediation_paths": paths}
    if profile == "codex":  # allowlist:provider -- transport: native implementer fake output
        adapter = NativeCodexAdapter(AgentSettings(profile, profile, "gpt-6.1-sol", 60, "high"))  # allowlist:provider -- profile configuration: provider-specific transport fixture
        adapter.invocation.request_id = bundle.bound_context.request_id
        adapter.invocation.last_message_file = tmp_path / "last-message.json"
        adapter.invocation.last_message_file.write_text(json.dumps({"result": result}))
        canonical = adapter.extract_output("", "", {})
    else:
        adapter = _adapter(_repo(tmp_path))
        adapter.prepare_native_provider_input(bundle)
        canonical = adapter.extract_output(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                                                        "structured_output": {"result": result}}), "", {})
    try:
        assert parse_bound_native_implementer_contract_result(json.loads(canonical), bundle.bound_context).stop_request.rule_id == rule
    finally:
        adapter.cleanup()


def test_product_requests_already_transport_stop_labels_and_phase0_now_does_too(tmp_path):
    for bundle in (_codex_bundle(), boundary.implementer_bundle("Probe", boundary.fixture(tmp_path / "fixture")["repo"])):  # allowlist:provider -- profile configuration: provider-specific transport fixture
        text = bundle.document["work_context"]
        for label in ("Missing prerequisite:", "Why it cannot be self-provided:", "Operator action:",
                      "Required paths:", "Why required for current Slice:"):
            assert label in text


@pytest.mark.parametrize("changed", ["none", "other-model", "cache-metadata", "bound-model", "unhardened"])
def test_resume_rejects_catalog_digest_drift_before_dispatch(monkeypatch, changed):
    import workflow_run_setup
    from workflow_state import init_workflow_state, scripted_profile_binding, ProtocolBinding, ProtocolMode
    from state_io import StateSchemaError
    profiles = {slot: scripted_profile_binding(slot) for slot in ("implementer", "reviewer", "final_reviewer")}
    profiles["implementer"] = replace(profiles["implementer"], model="gpt-6.1-sol")
    settings = AgentSettings("codex", "codex", "gpt-6.1-sol", 60, "medium",  # allowlist:provider -- profile configuration: resume reviewer identity
                             reviewer_model_catalog_json=hardened_reviewer_catalog(catalog(), "gpt-6.1-sol"))
    profiles["reviewer"] = replace(profiles["reviewer"], provider=settings.name, binary=settings.binary, model=settings.model,
                                   isolation_options_sha256=isolation_options_digest(settings))
    state = init_workflow_state(run_id="catalog-resume", task_file="/fixture/task.md", branch="feature/fixture",
        branch_base="a" * 40, first_slice_start_commit="a" * 40, slice_count=1,
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "3", **{slot + "_profile": value for slot, value in profiles.items()}))
    class Certificates:
        def require_occupancy(self, *args, **kwargs):
            pass
        def require(self, provider, role, slot, **kwargs):
            row = profiles[slot.value]
            return SimpleNamespace(**{name: getattr(row, name) for name in
                ("manufacturer", "capability_sha256", "transport_sha256", "rights_sha256", "policy_sha256")}, digest=row.certification_sha256)
    monkeypatch.setattr(workflow_run_setup, "load_role_certifications", Certificates)
    identities = {slot: row.binary_identity for slot, row in profiles.items()}
    monkeypatch.setattr(workflow_run_setup, "_capture_slot_identities", lambda *a, **k: identities)
    source = catalog()
    if changed == "bound-model":
        source["models"][0]["base_instructions"] = "Different bound policy"
    if changed == "other-model":
        source["models"][1]["description"] = "Changed unrelated model"
    if changed == "cache-metadata":
        source.update(fetched_at="tomorrow", etag="new-cache-tag")
    def bind(slots, identities, *, resume):
        assert resume
        fake = SimpleNamespace(kind="verified", digest="same", entry_path="/fixture/provider", launch_prefix=("/fixture/provider",))
        bind_catalog_models(slots, {slot: fake for slot in slots}, lambda c: (0, json.dumps(source), ""), resume=True)
        if changed == "unhardened":
            tampered = json.loads(slots["reviewer"].reviewer_model_catalog_json)
            tampered["models"][0]["multi_agent_version"] = "v2"
            slots["reviewer"] = replace(slots["reviewer"], reviewer_model_catalog_json=json.dumps(tampered, sort_keys=True, separators=(",", ":")))
    monkeypatch.setattr(workflow_run_setup, "_bind_slot_catalog_models", bind)
    slots = {slot: AgentSettings(row.provider, row.binary, row.model, row.timeout_seconds, row.effort) for slot, row in profiles.items()}
    args = SimpleNamespace(slot_settings=slots, agent_profile_overrides=(), scripted_provider_identity=False)
    if changed in {"bound-model", "unhardened"}:
        with pytest.raises(StateSchemaError, match="AGENT-PROFILE-DIFF.*reviewer model entry changed"):
            workflow_run_setup._apply_resumed_agent_profiles(args, state)
    else:
        workflow_run_setup._apply_resumed_agent_profiles(args, state)
        assert isolation_options_digest(args.slot_settings["reviewer"]) == profiles["reviewer"].isolation_options_sha256


@pytest.mark.parametrize("name,expected", [("reviewer-tools-hardened.json", "passed"), ("reviewer-tools-collaboration.json", "failed")])
def test_phase0_measures_additional_tools_when_request_is_in_stream(name, expected):
    from test_phase0_trace import assess
    recording = json.loads((FIXTURES.parent / "reviewer-P1.json").read_text())
    recording["run"]["stdout"] += "\n" + json.dumps({"type": "request", "body": json.loads((FIXTURES / name).read_text())})
    result = assess(recording)
    assert result["tool_surface"]["status"] == expected
    assert result["checks"]["tools_reported"] == (expected == "passed")
