from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
import hashlib
import json
import shutil

import pytest

import agent_adapters
from agent_roles import AgentRoleName, AgentSlot
from cli import ConfigError, load_repo_config, parse_args
from provider_input_budget import default_provider_input_budget_policy
from role_occupancy import provider_roles
from workflow_state import WorkflowStep


def _fake_certifications(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Promote only test copies of candidate pairs with generic fake proofs."""
    import cli
    import role_certification
    import workflow_production
    import workflow_run_setup

    root = Path(__file__).resolve().parents[1]
    table_path = "schemas/role-provider-certifications-v1.json"
    for relative in (
        table_path,
        "schemas/native-provider-schema-capabilities-v2.json",
        "docs/evidence/role-certification-v1.json",
        "docs/evidence/role-certification-reviewer-restricted-v1.json",
        "docs/evidence/role-certification-candidates-v1.json",
        "docs/evidence/codex/reviewer-candidate-v1.json",  # allowlist:provider -- certification data: reviewer candidate proof
        "docs/evidence/antigravity/capability-v1.json",
        "docs/evidence/antigravity/canary-v1.json",
        "docs/evidence/antigravity/phase-0-v1.json",
        "docs/evidence/antigravity/qualification-series-v1.json",
        "docs/evidence/antigravity/quality-results-v1.json",
        "docs/evidence/antigravity/operator-decisions-v1.json",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, target)
    table = json.loads((tmp_path / table_path).read_text(encoding="utf-8"))

    def digest(value: object) -> str:
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    for provider, slots, model in (
        ("claude", ("implementer",), "opus"),  # allowlist:provider -- certification data: fake canary
        ("codex", ("reviewer", "final_reviewer"), "gpt-6.1-sol"),  # allowlist:provider -- certification data: fake canary
    ):
        cases = {}
        for slot in slots:
            role = "implementer" if slot == "implementer" else "reviewer"
            request = {"request_id": f"fake-{provider}-{slot}"}
            writer = {"type": "object"}
            profile = {"provider": provider, "model": model}
            raw = {"request_id": request["request_id"], "status": "success", "evidence": {"request_document": request, "writer_schema": writer}}
            fields = {"provider": provider, "role": role, "slot": slot, "case": "fake-journey", "transport_series": "task-c2"}
            cases[slot] = {**fields, "status": "passed", "proof": {
                **fields, "request_id": request["request_id"], "request_sha256": digest(request),
                "writer_sha256": digest(writer), "raw_sha256": digest(raw),
                "profile_sha256": digest(profile), "model": model, "profile": profile,
                "checks": {key: True for key in ("writer", "domain", "effective_rights", "isolation_postcheck", "no_denials")},
                "raw": raw,
            }}
        path = f"docs/evidence/{provider}/role-canary-v1.json"
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"schema_version": "role-canary-v1", "status": "passed", "slots": cases}), encoding="utf-8")
        reference = {"path": path, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
        for row in table["certifications"]:
            if row["provider"] == provider and row["slot"] in slots:
                row["status"] = "experimental"
                row["canary_evidence"] = reference
    (tmp_path / table_path).write_text(json.dumps(table), encoding="utf-8")
    original = role_certification.load_role_certifications
    loader = lambda: original(root=tmp_path)
    for module in (cli, role_certification, workflow_production, workflow_run_setup, agent_adapters):
        monkeypatch.setattr(module, "load_role_certifications", loader)
    loader()


@pytest.mark.parametrize(
    ("implementer", "reviewer", "final_reviewer"),
    (
        ("codex", "claude", "claude"),  # allowlist:provider -- profile configuration: baseline topology
        ("codex", "antigravity", "antigravity"),  # allowlist:provider -- profile configuration: review topology
        ("claude", "codex", "codex"),  # allowlist:provider -- profile configuration: target topology
    ),
)
def test_budget_defaults_follow_selected_slots(
    implementer: str, reviewer: str, final_reviewer: str,
) -> None:
    occupancy = (
        ("implementer", "implementer", implementer),
        ("reviewer", "reviewer", reviewer),
        ("final_reviewer", "reviewer", final_reviewer),
    )
    policy = default_provider_input_budget_policy(occupancy)
    assert len(policy.rules) == 7
    assert policy.select(implementer, "implementer", WorkflowStep.IMPLEMENTER_PLAN.value)
    assert policy.select(reviewer, "reviewer", WorkflowStep.REVIEWER_SLICE_REVIEW.value)
    assert policy.select(final_reviewer, "reviewer", WorkflowStep.REVIEWER_FINAL_REVIEW.value)
    assert {rule.provider for rule in policy.rules} == {implementer, reviewer, final_reviewer}


def test_repo_defaults_and_explicit_budget_use_selected_occupancy(tmp_path: Path) -> None:
    config = tmp_path / "orchestrator.toml"
    config.write_text(
        '[roles]\nimplementer = "writer"\nreviewer = "reader"\nfinal_reviewer = "reader"\n'
        '[agent_profiles.writer]\nprovider = "claude"\nmodel = "sonnet"\neffort = "high"\n'  # allowlist:provider -- profile configuration: selected implementer
        '[agent_profiles.reader]\nprovider = "codex"\nmodel = "gpt-6.1-sol"\neffort = "high"\n',  # allowlist:provider -- profile configuration: selected reviewer
        encoding="utf-8",
    )
    loaded = load_repo_config(config)
    assert loaded.provider_input_budget.select("claude", "implementer", "implementer_plan")  # allowlist:provider -- profile configuration: selected implementer
    assert loaded.provider_input_budget.select("codex", "reviewer", "reviewer_final_review")  # allowlist:provider -- profile configuration: selected reviewer
    assert {rule.provider for rule in loaded.provider_input_budget.rules} == {"claude", "codex"}  # allowlist:provider -- profile configuration: selected topology
    with config.open("a", encoding="utf-8") as stream:
        stream.write(
            '[[provider_input_budget]]\nprovider = "antigravity"\n'
            'role = "reviewer"\noperation = "reviewer_plan_review"\n'
            'max_chars = 10\nmax_bytes = 10\n'
        )
    with pytest.raises(ConfigError, match="must be complete"):
        load_repo_config(config)


@pytest.mark.parametrize(
    "occupancy",
    (
        {AgentSlot.IMPLEMENTER: "codex", AgentSlot.REVIEWER: "codex", AgentSlot.FINAL_REVIEWER: "claude"},  # allowlist:provider -- profile configuration: invalid topology
        {AgentSlot.IMPLEMENTER: "claude", AgentSlot.REVIEWER: "codex", AgentSlot.FINAL_REVIEWER: "claude"},  # allowlist:provider -- profile configuration: invalid topology
    ),
)
def test_provider_roles_reject_implementer_review_collision(occupancy: dict[AgentSlot, str]) -> None:
    with pytest.raises(ValueError, match="conflicting roles"):
        provider_roles(occupancy)


@pytest.mark.parametrize(
    ("implementer", "reviewer", "final_reviewer"),
    (("codex", "codex", "claude"), ("claude", "codex", "claude")),  # allowlist:provider -- profile configuration: invalid topologies
)
def test_colliding_topology_is_rejected_before_provider_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    implementer: str, reviewer: str, final_reviewer: str,
) -> None:
    _fake_certifications(tmp_path / "qualification", monkeypatch)
    models = {"codex": "gpt-6.1-sol", "claude": "opus"}  # allowlist:provider -- profile configuration: fake models
    names = {"implementer": implementer, "reviewer": reviewer, "final_reviewer": final_reviewer}
    config = tmp_path / "orchestrator.toml"
    config.write_text(
        "[roles]\n" + "".join(f'{slot} = "{slot}"\n' for slot in names)
        + "".join(
            f'[agent_profiles.{slot}]\nprovider = "{provider}"\nmodel = "{models[provider]}"\neffort = "high"\n'
            for slot, provider in names.items()
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="manufacturer|conflict"):
        parse_args([], cwd=tmp_path, environ={})


def test_registered_fake_transports_are_accepted_per_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeImplementer:
        name = "claude"  # allowlist:provider -- transport: fake implementer
        execution_boundary_profile = "none"

    class FakeReviewer:
        name = "codex"  # allowlist:provider -- transport: fake reviewer

    monkeypatch.setitem(agent_adapters.NATIVE_IMPLEMENTER_TRANSPORTS, "claude", FakeImplementer)  # allowlist:provider -- transport: fake registration
    monkeypatch.setitem(agent_adapters.NATIVE_REVIEW_TRANSPORTS, "codex", FakeReviewer)  # allowlist:provider -- transport: fake registration
    assert agent_adapters.is_native_implementer_adapter(FakeImplementer())
    assert agent_adapters.is_native_review_adapter(FakeReviewer())
    assert not agent_adapters.is_native_implementer_adapter(SimpleNamespace(name="claude"))  # allowlist:provider -- transport: registration negative control
    assert not agent_adapters.is_native_review_adapter(SimpleNamespace(name="codex"))  # allowlist:provider -- transport: registration negative control
    assert not agent_adapters.is_native_implementer_adapter(FakeReviewer())


def test_unknown_implementer_boundary_is_rejected_before_preparation() -> None:
    import agent_runtime
    from test_agent_adapters import _codex_bundle  # allowlist:provider -- transport: existing request fixture

    class FakeImplementer:
        name = "claude"  # allowlist:provider -- transport: fake implementer
        execution_boundary_profile = "none"

        def prepare_native_provider_input(self, bundle):
            raise AssertionError("unknown boundary reached provider preparation")

    request = _codex_bundle()  # allowlist:provider -- transport: existing request fixture

    with pytest.raises(ValueError, match="unknown implementer execution boundary"):
        agent_runtime.run_native_implementer_agent(
            FakeImplementer(), request,
            config=agent_runtime.OrchestratorConfig(),
            shorten=lambda text, _limit: text or "",
            operation="implementer_plan", binding_fingerprint="a" * 64,
        )


def test_live_stream_and_quota_use_transport_profiles() -> None:
    from datetime import datetime, timezone
    from agent_runtime import _compact_stream_text, parse_quota_reset

    adapter = SimpleNamespace(name="fiction", reviewer=False, live_stream_profile="json-events")
    assert _compact_stream_text(
        adapter, "stdout", '{"item":{"text":"progress"}}', {}
    ) == "progress"
    reset = parse_quota_reset(
        "fiction", "reset at 2026-09-30T10:00:00Z",
        received_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
        reset_profile="standard",
    )
    assert reset is not None
    assert reset.reset_at_utc == datetime(2026, 9, 30, 10, tzinfo=timezone.utc)


def test_existing_topology_request_bytes_match_pre_slice_digests() -> None:
    from native_implementer_request import build_native_implementer_request
    from native_review_request import build_native_review_request
    from test_native_implementer_request import _spec as implementer_spec
    from test_native_review_request import _spec as review_spec
    from test_antigravity_adapter import _bundle as alternative_review_bundle

    requests = (
        (build_native_implementer_request(implementer_spec()).canonical_json,
         "4cde1043b4abcb1e20d24a28195a7fbbfde2229866853ad8b09a1dc07b2c8ae4"),
        (build_native_review_request(review_spec()).canonical_json,
         "15cb6a530b96433f69d4f60e1a39cdeab6af3a0d90a00b8e056c2475f5f92097"),
        (alternative_review_bundle().canonical_json,
         "fa6239da816e18a62a2f913660e3b5787d16246cf72bbe54dbadd51fcd5ab7ca"),
    )
    for content, expected in requests:
        assert hashlib.sha256(content.encode("utf-8")).hexdigest() == expected


@pytest.mark.parametrize(
    ("implementer", "reviewer", "work_plan", "interruption"),
    (("codex", "claude", None, None), ("codex", "antigravity", None, None), ("claude", "codex", None, None),  # allowlist:provider -- profile configuration: journey topologies
     ("claude", "codex", "docs/work-plan.md", None),  # allowlist:provider -- profile configuration: external plan artifact regression
     ("claude", "codex", "docs/work-plan.md", WorkflowStep.REVIEWER_PLAN_REVIEW),  # allowlist:provider -- profile configuration: record-ahead plan recovery
     ("claude", "codex", "docs/work-plan.md", WorkflowStep.REVIEWER_FINAL_REVIEW)),  # allowlist:provider -- profile configuration: record-ahead final recovery
)
def test_topology_plan_slice_final_review_and_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    implementer: str, reviewer: str, work_plan: str | None,
    interruption: WorkflowStep | None,
) -> None:
    import test_orchestrator_runtime as fixture
    from agent_adapters import build_slot_agent_registry
    from artifact_store import ArtifactStore
    from artifact_models import FinalReviewCompletedPayload
    from orchestrator import ProductionWorkflowDriver, run_production_workflow
    from workflow import ImplementerInvocation, ReviewerInvocation
    from workflow_state import WorkUnitKind

    if implementer == "claude":  # allowlist:provider -- profile configuration: target fake journey
        _fake_certifications(tmp_path / "qualification", monkeypatch)

        from claude_implementer_adapter import NativeClaudeImplementerAdapter  # allowlist:provider -- transport: real implementer journey
        assert agent_adapters._implementer_transports()["claude"] is NativeClaudeImplementerAdapter  # allowlist:provider -- transport: real implementer journey

    repository = fixture._repository(tmp_path, "feature/fake-topology")
    if reviewer != "claude" or implementer != "codex":  # allowlist:provider -- profile configuration: selected journey
        provider_model = {"codex": "gpt-6.1-sol", "claude": "opus", "antigravity": "gemini-3.1-pro-high"}  # allowlist:provider -- profile configuration: fake models
        (repository / "orchestrator.toml").write_text(
            '[roles]\nimplementer = "writer"\nreviewer = "reader"\nfinal_reviewer = "reader"\n'
            f'[agent_profiles.writer]\nprovider = "{implementer}"\nmodel = "{provider_model[implementer]}"\neffort = "high"\n'
            f'[agent_profiles.reader]\nprovider = "{reviewer}"\nmodel = "{provider_model[reviewer]}"\neffort = "high"\n',
            encoding="utf-8",
        )
        fixture._git(repository, "add", "orchestrator.toml")
        fixture._git(repository, "commit", "-m", "test slot profiles")
    task = tmp_path / "task.md"
    fixture._write_task(task, "feature/fake-topology", "src/one.py")
    if work_plan:
        task.write_text("ORCHESTRATOR_MODE: PLAN_ONLY\n"
                        f"WORK_PLAN_PATH: {work_plan}\nTARGET_BRANCH: feature/fake-topology\n"
                        f"TASK_SCOPE: {work_plan}\n", encoding="utf-8")
    args = fixture._args(repository, task)
    registry = build_slot_agent_registry(args.slot_settings)
    assert agent_adapters.is_native_implementer_adapter(registry["implementer"])
    assert agent_adapters.is_native_review_adapter(registry["reviewer"])
    assert agent_adapters.is_native_review_adapter(registry["final_reviewer"])
    steps: list[WorkflowStep] = []
    expected_review = None
    expected_implementer = None
    if implementer == "claude":  # allowlist:provider -- transport: reviewer fake process boundary
        import agent_runtime
        from codex_review_adapter import NativeCodexReviewAdapter  # allowlist:provider -- transport: real reviewer adapter
        package = tmp_path / "node_modules/@openai/codex"  # allowlist:provider -- transport: fake identity package
        (package / "bin").mkdir(parents=True)
        native = package / "node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex"  # allowlist:provider -- transport: installed npm layout
        native.parent.mkdir(parents=True)
        native.write_text("fake native binary")
        native.chmod(0o755)
        entry = package / "bin/codex.js"  # allowlist:provider -- transport: fake identity entry
        entry.write_text("fake")

        def fake_provider_process(adapter, prompt, *, prepared_provider_input, **_kwargs):
            if isinstance(adapter, NativeCodexReviewAdapter):  # allowlist:provider -- transport: real reviewer adapter
                assert prepared_provider_input.stdin_text.startswith(adapter.role_binding.policy)
                adapter.before_provider_process()
                adapter.invocation.last_message_file.write_text(json.dumps({"result": json.loads(expected_review.canonical_json)}))
                return adapter.extract_output("", "", {})
            assert isinstance(adapter, NativeClaudeImplementerAdapter)  # allowlist:provider -- transport: real implementer journey
            assert prepared_provider_input.command[prepared_provider_input.command.index("--settings") + 1]
            envelope = {"type": "result", "subtype": "success", "is_error": False,
                        "structured_output": {"result": json.loads(expected_implementer.canonical_json)}}
            try:
                return adapter.extract_output(json.dumps(envelope), "", {})
            finally:
                adapter.cleanup()

        monkeypatch.setattr(agent_runtime, "run_agent", fake_provider_process)

    def implement(_driver: ProductionWorkflowDriver, invocation: ImplementerInvocation):
        steps.append(invocation.step)
        target = repository / "src/one.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        if invocation.step is WorkflowStep.IMPLEMENTER_PLAN:
            if work_plan:
                plan = repository / work_plan
                plan.parent.mkdir(parents=True, exist_ok=True)
                plan.write_text("# Work plan\n\n### Slice 1 - Implement one value\n\n"
                                "**Exakter Änderungspfad**\n\n- `src/one.py`\n\n"
                                "**Akzeptanzkriterien**\n\n- value equals one.\n", encoding="utf-8")  # allowlist:german -- contract fixture: canonical plan heading
            else:
                target.write_text("value = 0\n", encoding="utf-8")
            expected = fixture._native_plan_output(invocation, summary="add implementation", scope_paths=(work_plan or "src/one.py",))
        else:
            target.write_text("value = 1\n", encoding="utf-8")
            expected = fixture._native_implementation_output(invocation)
        if implementer != "claude":  # allowlist:provider -- transport: baseline fake implementer
            return expected
        from agent_runtime import run_native_implementer_agent
        adapter = _driver._adapter_for_slot("implementer")
        assert isinstance(adapter, NativeClaudeImplementerAdapter)  # allowlist:provider -- transport: real implementer journey
        nonlocal expected_implementer
        expected_implementer = expected
        return run_native_implementer_agent(
            adapter, invocation.native_request, config=_driver.config,
            shorten=lambda value, _limit: value or "", operation=invocation.step.value,
            binding_fingerprint=invocation.native_request.bound_context.context.current_fingerprint,
        )

    def review(driver: ProductionWorkflowDriver, invocation: ReviewerInvocation):
        steps.append(invocation.step)
        if invocation.step is WorkflowStep.REVIEWER_FINAL_REVIEW:
            expected = fixture._native_final_review_output(driver, invocation, finding_id="R-01")
        else:
            expected = fixture._native_review_approval(invocation)
        if implementer != "claude":  # allowlist:provider -- transport: existing baseline fake reviews
            return expected
        from agent_runtime import OrchestratorConfig, run_native_review_agent
        from codex_review_adapter import NativeCodexReviewAdapter  # allowlist:provider -- transport: real reviewer adapter
        adapter = driver._adapter_for_slot("final_reviewer" if invocation.step is WorkflowStep.REVIEWER_FINAL_REVIEW else "reviewer")
        assert isinstance(adapter, NativeCodexReviewAdapter)  # allowlist:provider -- transport: real reviewer adapter
        adapter.provider_identity = SimpleNamespace(kind="verified", entry_path=str(entry))
        from model_catalog import hardened_reviewer_catalog
        adapter.settings = replace(adapter.settings, reviewer_model_catalog_json=hardened_reviewer_catalog({"models": [{"slug": adapter.model}]}, adapter.model))
        nonlocal expected_review
        expected_review = expected
        driver._persist_native_agent_request_bundle(invocation)
        driver._write_native_agent_raw_response(
            driver._native_reviewer_response_path(invocation), expected.canonical_json
        )
        return run_native_review_agent(
            adapter, invocation.native_request,
            config=OrchestratorConfig(repo_root=repository),
            shorten=lambda value, _limit: value or "",
            reviewer_manifest_paths=invocation.review_packet.manifest.snapshot_paths if invocation.review_packet else None,
            operation=invocation.step.value, binding_fingerprint=invocation.fingerprint,
        )

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_implementer", implement)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", review)
    monkeypatch.chdir(repository)
    original_persist = ProductionWorkflowDriver.persist_native_review_contract
    interrupted = False

    def persist_then_interrupt(driver, *arguments, **keywords):
        nonlocal interrupted
        original_persist(driver, *arguments, **keywords)
        if (interruption is not None and not interrupted
                and driver.active_state.current_step is interruption):
            interrupted = True
            raise RuntimeError("review decision persisted before interruption")

    monkeypatch.setattr(ProductionWorkflowDriver, "persist_native_review_contract", persist_then_interrupt)

    def run_with_recovery(task, arguments, expected_step):
        if interruption is not expected_step:
            return run_production_workflow(task, arguments)
        with pytest.raises(RuntimeError, match="review decision persisted before interruption"):
            run_production_workflow(task, arguments)
        assert interrupted
        resumed_args = fixture._args(repository, task)
        resumed_args.resume = True
        return run_production_workflow(task, resumed_args)

    result = run_with_recovery(task, args, WorkflowStep.REVIEWER_PLAN_REVIEW)
    assert result.workflow_completed, result.state.current_work_unit.gate.detail
    if work_plan:
        task = task.with_name("task-implement.md")
        assert task.is_file()
        handoff_args = fixture._args(repository, task)
        handoff_args.resume = False
        handoff_args.force_overwrite_state = True
        result = run_with_recovery(task, handoff_args, WorkflowStep.REVIEWER_FINAL_REVIEW)
        assert result.workflow_completed, result.state.current_work_unit.gate.detail
    assert result.state.current_work_unit.kind is WorkUnitKind.FINAL_REVIEW
    assert {WorkflowStep.IMPLEMENTER_PLAN, WorkflowStep.IMPLEMENTER_IMPLEMENTATION,
            WorkflowStep.REVIEWER_PLAN_REVIEW, WorkflowStep.REVIEWER_SLICE_REVIEW,
            WorkflowStep.REVIEWER_FINAL_REVIEW} <= set(steps)
    chain = ArtifactStore(repository, result.state.run_id).load_chain()
    assert sum(isinstance(record.payload, FinalReviewCompletedPayload) for record in chain) == 1
    resumed_args = fixture._args(repository, task)
    resumed_args.resume = True
    resumed = run_production_workflow(task, resumed_args)
    assert resumed.workflow_completed
    assert steps.count(WorkflowStep.REVIEWER_FINAL_REVIEW) == 1
    if work_plan:
        assert steps.count(WorkflowStep.REVIEWER_PLAN_REVIEW) == 1
    drifted_args = fixture._args(repository, task)
    drifted_args.resume = True
    drifted_args.agent_profile_overrides = frozenset({("reviewer", "model")})
    drifted_args.slot_settings["reviewer"] = replace(
        drifted_args.slot_settings["reviewer"], model="different-model"
    )
    from state_io import StateSchemaError
    with pytest.raises(StateSchemaError, match="AGENT-PROFILE-DIFF"):
        run_production_workflow(task, drifted_args)


@pytest.mark.parametrize("tracked", (False, True))
def test_bound_external_markdown_keeps_body_and_rejects_malformed_audit(
    tmp_path: Path, tracked: bool,
) -> None:
    import test_orchestrator_runtime as fixture
    from test_repo_changes import _managed_slice_markdown
    from orchestrator import OrchestratorConfig, ProductionWorkflowDriver
    from repo_changes import RepositoryChangeError
    from workflow_state import init_workflow_state

    repository = fixture._repository(tmp_path, "feature/semantic-boundary")
    base = fixture._git(repository, "rev-parse", "HEAD")
    paths = ("docs/work-plan.md", "docs/internal/task-review-12345678.md", "docs/ordinary.md")
    for path in paths:
        document = repository / path
        document.parent.mkdir(parents=True, exist_ok=True)
        document.write_text(_managed_slice_markdown(), encoding="utf-8")
    if tracked:
        fixture._git(repository, "add", *paths)
        fixture._git(repository, "commit", "-m", "add test documents")
    driver = ProductionWorkflowDriver(
        repository_root=repository, state_file=repository / ".orchestrator/state.json",
        agents={}, config=OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.active_state = init_workflow_state(
        run_id="semantic-boundary", task_file=str(tmp_path / "task.md"),
        branch="feature/semantic-boundary", branch_base=base,
        first_slice_start_commit=base, slice_count=1,
        work_plan_path=paths[0], audit_report_path=paths[1],
    )
    assert driver._collect_change_path_selection() == (tuple(sorted(paths[:2])), None)
    before = driver.collect_changes(base)
    for path in paths[:2]:
        document = repository / path
        document.write_text(_managed_slice_markdown("approved audit"), encoding="utf-8")
    assert driver.collect_changes(base).fingerprint == before.fingerprint
    for path in paths[:2]:
        document = repository / path
        document.write_text(_managed_slice_markdown().replace("semantic body", "changed body"), encoding="utf-8")
        assert driver.collect_changes(base).fingerprint != before.fingerprint
        document.write_text(_managed_slice_markdown(), encoding="utf-8")
    (repository / paths[2]).write_text(_managed_slice_markdown("ordinary document edit"), encoding="utf-8")
    assert driver.collect_changes(base).fingerprint != before.fingerprint
    (repository / paths[0]).write_text("# Audit\n<!-- audit:findings:begin -->\nunclosed\n", encoding="utf-8")
    with pytest.raises(RepositoryChangeError, match="canonicalize managed audit sections"):
        driver.collect_changes(base)
