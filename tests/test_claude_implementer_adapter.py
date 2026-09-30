from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
import agent_runtime

from agent_adapters import AgentOutputError
from agent_config import AgentSettings
from contracts import AgentRole, FindingClass, FindingOrigin, FindingRecord, FindingStatus, ReadinessMarker
from agent_runtime import OrchestratorConfig, run_native_implementer_agent
from claude_implementer_adapter import (  # allowlist:provider -- transport: implementer fake coverage
    NativeClaudeImplementerAdapter, implementer_settings, protected_implementer_paths,  # allowlist:provider -- transport: implementer fake coverage
)
from native_implementer_request import build_native_implementer_request
from native_implementer_contract import NativeImplementerRequestKind, parse_bound_native_implementer_contract_result
from native_provider_schema import NativeProviderSchemaError, normalize_transport_profile
from provider_input_budget import default_provider_input_budget_policy
from test_native_implementer_request import _spec


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "inbox").mkdir()
    (root / "outbox").mkdir()
    return root


def _adapter(root: Path) -> NativeClaudeImplementerAdapter:  # allowlist:provider -- transport: implementer fake
    adapter = NativeClaudeImplementerAdapter(  # allowlist:provider -- transport: implementer fake
        AgentSettings("claude", "claude", "opus", 60, "high")  # allowlist:provider -- profile configuration: implementer fake
    )
    adapter.bind_implementer_boundary(root, root / "inbox", root / "outbox", "run-1")
    return adapter


def _prepared(tmp_path: Path):
    root = _repo(tmp_path)
    adapter = _adapter(root)
    bundle = build_native_implementer_request(_spec(), profile="claude-implementer")  # allowlist:provider -- profile configuration: implementer writer
    prepared = adapter.prepare_native_provider_input(bundle)
    return root, adapter, bundle, prepared


def test_native_implementer_settings_and_measurement_are_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAO_DECOY_TOKEN", "must-not-pass")
    root, adapter, bundle, prepared = _prepared(tmp_path)
    try:
        settings = json.loads(prepared.command[prepared.command.index("--settings") + 1])
        assert settings == implementer_settings(
            protected_implementer_paths(root, root / "inbox", root / "outbox", "run-1"), root
        )
        assert settings["sandbox"]["failIfUnavailable"] is True
        assert settings["disableAllHooks"] is True
        assert settings["sandbox"]["filesystem"]["denyWrite"]
        assert settings["permissions"]["blockReadsOutsideWorkingDirectories"] is True
        assert set(adapter.env) == {"HOME", "USER", "LOGNAME", "PATH", "LANG", "TERM"}
        assert "DAO_DECOY_TOKEN" not in adapter.env
        first = hashlib.sha256(json.dumps(
            [(part.name, part.content) for part in prepared.components],
            separators=(",", ":"), ensure_ascii=False,
        ).encode()).hexdigest()
        second_prepared = adapter.prepare_native_provider_input(bundle)
        second = hashlib.sha256(json.dumps(
            [(part.name, part.content) for part in second_prepared.components],
            separators=(",", ":"), ensure_ascii=False,
        ).encode()).hexdigest()
        assert first == second
    finally:
        adapter.cleanup()


@pytest.mark.parametrize("replacement", [
    ("--restricted", "--bare"),
    ("--safe-mode", "--dangerously-skip-permissions"),
    ("Read,Edit,Write,Glob,Grep,Bash", "Read,Edit,Write,Glob,Grep,Bash,Agent"),
    ("acceptEdits", "bypassPermissions"),
    ("acceptEdits", "auto"),
    ("none", "manual"),
    ("--strict-mcp-config", "--mcp-config"),
])
def test_implementer_command_rejects_broadened_flags(tmp_path: Path, replacement: tuple[str, str]) -> None:
    _, adapter, _, prepared = _prepared(tmp_path)
    command = list(prepared.command)
    command[command.index(replacement[0])] = replacement[1]
    try:
        with pytest.raises(NativeProviderSchemaError):
            normalize_transport_profile(
                "claude-implementer", command,  # allowlist:provider -- profile configuration: implementer fake
                bound_settings_json=prepared.command[prepared.command.index("--settings") + 1],
            )
    finally:
        adapter.cleanup()


@pytest.mark.parametrize("field,value", [
    (("sandbox", "failIfUnavailable"), False),
    (("sandbox", "allowUnsandboxedCommands"), True),
    (("sandbox", "autoAllowBashIfSandboxed"), False),
    (("sandbox", "excludedCommands"), ["git"]),
    (("sandbox", "network", "allowedDomains"), ["example.com"]),
    (("sandbox", "network", "strictAllowlist"), False),
    (("sandbox", "filesystem", "denyWrite"), []),
    (("permissions", "deny"), []),
    (("permissions", "blockReadsOutsideWorkingDirectories"), False),
    (("disableAllHooks",), False),
])
def test_implementer_settings_reject_weakened_field(
    tmp_path: Path, field: tuple[str, ...], value: object,
) -> None:
    _, adapter, _, prepared = _prepared(tmp_path)
    bound = prepared.command[prepared.command.index("--settings") + 1]
    changed = json.loads(bound)
    cursor = changed
    for key in field[:-1]:
        cursor = cursor[key]
    cursor[field[-1]] = value
    command = list(prepared.command)
    command[command.index("--settings") + 1] = json.dumps(changed, sort_keys=True, separators=(",", ":"))
    try:
        with pytest.raises(NativeProviderSchemaError):
            normalize_transport_profile("claude-implementer", command, bound_settings_json=bound)  # allowlist:provider -- profile configuration: implementer fake
    finally:
        adapter.cleanup()


@pytest.mark.parametrize("extra", [
    "--add-dir", "--plugin-dir", "--agents", "--bare",
    "--allow-dangerously-skip-permissions", "--dangerously-skip-permissions",
])
def test_implementer_command_rejects_extra_flags(tmp_path: Path, extra: str) -> None:
    _, adapter, _, prepared = _prepared(tmp_path)
    command = [*prepared.command[:-1], extra, prepared.command[-1]]
    try:
        with pytest.raises(NativeProviderSchemaError):
            normalize_transport_profile(
                "claude-implementer", command,  # allowlist:provider -- profile configuration: implementer fake
                bound_settings_json=prepared.command[prepared.command.index("--settings") + 1],
            )
    finally:
        adapter.cleanup()


def test_linked_worktree_gitdir_and_common_dir_are_protected() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = protected_implementer_paths(root, root / "inbox", root / "outbox", "run-1")
    completed = subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir", "--git-common-dir"],
        cwd=root, check=True, capture_output=True, text=True,
    )
    gitdir, common_dir = completed.stdout.splitlines()
    assert Path(gitdir).resolve() in paths
    assert Path(common_dir).resolve() in paths
    settings = implementer_settings(paths, root)
    deny_write = [Path(item) for item in settings["sandbox"]["filesystem"]["denyWrite"]]
    for path in (gitdir, common_dir):
        # A linked worktree's gitdir lies inside the common dir, which covers it.
        resolved = Path(path).resolve()
        assert any(resolved == item or resolved.is_relative_to(item) for item in deny_write)
    assert Path(common_dir).resolve() in deny_write


def test_queue_symlink_alias_and_target_are_both_protected(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    alias = root / "queue-link"
    alias.symlink_to(outside, target_is_directory=True)
    paths = protected_implementer_paths(root, alias / "pending", root / "outbox", "run-1")
    assert (alias / "pending").absolute() in paths
    assert (outside / "pending").resolve() in paths
    settings = implementer_settings(paths, root)
    assert f"Edit(./queue-link/pending/**)" in settings["permissions"]["deny"]
    assert str(outside / "pending") in settings["sandbox"]["filesystem"]["denyWrite"]


def test_evidence_asset_symlink_parent_fails_before_write(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / ".orchestrator").symlink_to(outside, target_is_directory=True)
    adapter = _adapter(root)
    bundle = build_native_implementer_request(
        _spec(), profile="claude-implementer", inline_evidence_chars=1,  # allowlist:provider -- profile configuration: protected evidence fake
    )
    with pytest.raises(AgentOutputError, match="parent is a link"):
        adapter.prepare_native_provider_input(bundle)
    assert list(outside.iterdir()) == []


def test_unbound_queue_and_unknown_boundary_fail_before_start(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    with pytest.raises(AgentOutputError, match="unbound"):
        protected_implementer_paths(root, None, root / "outbox", "run-1")
    adapter = _adapter(root)
    adapter.execution_boundary_profile = "unknown"
    bundle = build_native_implementer_request(_spec(), profile="claude-implementer")  # allowlist:provider -- profile configuration: implementer writer
    try:
        with pytest.raises(ValueError, match="unknown implementer execution boundary"):
            run_native_implementer_agent(
                adapter, bundle, config=OrchestratorConfig(repo_root=root),
                shorten=lambda value, limit: value or "", operation="implementer_plan",
                binding_fingerprint="a" * 64,
            )
    finally:
        adapter.cleanup()


def test_real_binary_layout_is_native_symlink_when_installed() -> None:
    entry = Path.home() / ".local" / "bin" / "claude"  # allowlist:provider -- transport: native installation fixture
    if not entry.exists():
        pytest.skip("native binary is not installed in this test environment")
    assert entry.is_symlink()
    assert entry.resolve().is_file()
    assert entry.resolve().read_bytes()[:4] == b"\x7fELF"


def test_fake_process_uses_implementer_boundary_and_validates_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _repo(tmp_path)
    bundle = build_native_implementer_request(_spec(), profile="claude-implementer")  # allowlist:provider -- profile configuration: implementer writer
    adapter = NativeClaudeImplementerAdapter(  # allowlist:provider -- transport: fake process
        AgentSettings("claude", "claude", "opus", 60, "high")  # allowlist:provider -- profile configuration: implementer fake
    )
    monkeypatch.setattr(agent_runtime, "verify_agent_capabilities", lambda *a, **k: None)
    monkeypatch.setattr(agent_runtime, "_bound_launch_command", lambda _adapter, command: list(command))
    seen = []

    def fake_process(_adapter, command_parts, stdin_text, **kwargs):
        assert kwargs["execution_root"] == root
        assert stdin_text == bundle.canonical_json
        assert bundle.canonical_json not in command_parts
        assert command_parts[command_parts.index("--system-prompt") + 1] == adapter.role_binding.policy
        assert set(kwargs["env"]) == {"HOME", "USER", "LOGNAME", "PATH", "LANG", "TERM"}
        seen.append(kwargs["execution_root"])
        result = {
            "schema_version": "native-agent-implementer-result-v3",
            "result_type": "plan_result", "request_id": bundle.bound_context.request_id,
            "ready": True,
            "slice_plan": [{
                "slice_id": 1, "summary": "Create the bound plan.",
                "scope_paths": ["docs/internal/plan.md"],
                "acceptance_criteria": [{"text": "The plan is executable.", "measured_against": "SOURCE"}],
            }],
            "finding_dispositions": [],
        }
        stdout = json.dumps({"is_error": False, "structured_output": {"result": result}})
        return subprocess.CompletedProcess(command_parts, 0, stdout, "")

    monkeypatch.setattr(agent_runtime, "_run_agent_process", fake_process)
    config = OrchestratorConfig(
        repo_root=root, inbox_dir=root / "inbox", outbox_dir=root / "outbox",
        provider_input_budget=default_provider_input_budget_policy((
            ("implementer", "implementer", "claude"),  # allowlist:provider -- profile configuration: fake occupancy
            ("reviewer", "reviewer", "codex"),  # allowlist:provider -- profile configuration: fake occupancy
            ("final_reviewer", "reviewer", "codex"),  # allowlist:provider -- profile configuration: fake occupancy
        )),
    )
    output = run_native_implementer_agent(
        adapter, bundle, config=config, shorten=lambda text, limit: text or "",
        operation="implementer_plan", binding_fingerprint="a" * 64,
    )
    assert output.result.ready is True
    assert seen == [root]


@pytest.mark.parametrize("kind", ["correction", "stop", "multi"])
def test_fake_envelope_preserves_dispositions_and_stop_result(tmp_path: Path, kind: str) -> None:
    root = _repo(tmp_path)
    spec = _spec()
    finding = FindingRecord(
        finding_id="R-01", finding_class=FindingClass.FINDING,
        status=FindingStatus.OPEN, summary="Repair the boundary.",
        acceptance_test="The boundary rejects traversal.",
        origin=FindingOrigin("01", 1, AgentRole.REVIEWER),
    )
    if kind == "correction":
        context = replace(
            spec.context, operation="implementer_correction",
            request_kind=NativeImplementerRequestKind.CORRECTION,
            previous_findings=(finding,),
            contract=replace(
                spec.context.contract, readiness_marker=ReadinessMarker.IMPLEMENTATION,
                require_slice_plan=False, plan_artifact_path=None,
                require_test_files_record=True, test_changes_approved=True,
            ),
        )
        spec = replace(spec, context=context)
    elif kind == "multi":
        spec = replace(spec, authorized_paths=("docs/internal/plan.md", "src/one.py"))
    bundle = build_native_implementer_request(spec, profile="claude-implementer")  # allowlist:provider -- profile configuration: implementer writer
    adapter = _adapter(root)
    adapter.prepare_native_provider_input(bundle)
    base = {"schema_version": "native-agent-implementer-result-v3", "request_id": bundle.bound_context.request_id}
    if kind == "correction":
        result = {**base, "result_type": "correction_result", "ready": True,
                  "test_files": [], "finding_dispositions": [{
                      "finding_id": "R-01", "decision": "accepted",
                      "rationale": "The named boundary is repaired.",
                  }]}
    elif kind == "stop":
        result = {**base, "result_type": "stop_result", "rule_id": "CONTRACT-UNCLEAR",
                  "rationale": "The task omits the required path.", "remediation_paths": []}
    else:
        result = {**base, "result_type": "plan_result", "ready": True,
                  "finding_dispositions": [], "slice_plan": [
                      {"slice_id": index, "summary": f"Complete step {index}.",
                       "scope_paths": [path], "acceptance_criteria": [
                           {"text": f"Step {index} is complete.", "measured_against": "SOURCE"},
                       ]}
                      for index, path in enumerate(("docs/internal/plan.md", "src/one.py"), 1)
                  ]}
    try:
        canonical = adapter.extract_output(
            json.dumps({"is_error": False, "structured_output": {"result": result}}), "", {},
        )
        parsed = parse_bound_native_implementer_contract_result(json.loads(canonical), bundle.bound_context)
        assert parsed.stopped is (kind == "stop")
        if kind == "correction":
            assert parsed.findings[0].responses[0].decision.value == "ACCEPTED"
        if kind == "multi":
            assert len(parsed.slice_plan) == 2
    finally:
        adapter.cleanup()


def test_interrupted_fake_process_retries_same_bound_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _repo(tmp_path)
    bundle = build_native_implementer_request(_spec(), profile="claude-implementer")  # allowlist:provider -- profile configuration: implementer writer
    adapter = NativeClaudeImplementerAdapter(  # allowlist:provider -- transport: fake process retry
        AgentSettings("claude", "claude", "opus", 60, "high")  # allowlist:provider -- profile configuration: implementer fake
    )
    monkeypatch.setattr(agent_runtime, "verify_agent_capabilities", lambda *a, **k: None)
    monkeypatch.setattr(agent_runtime, "_bound_launch_command", lambda _adapter, command: list(command))
    starts = []

    def fake_process(_adapter, command_parts, stdin_text, **kwargs):
        starts.append(kwargs["execution_root"])
        if len(starts) == 1:
            raise RuntimeError("simulated interruption")
        result = {
            "schema_version": "native-agent-implementer-result-v3", "result_type": "plan_result",
            "request_id": bundle.bound_context.request_id, "ready": True,
            "slice_plan": [{"slice_id": 1, "summary": "Complete the plan.",
                            "scope_paths": ["docs/internal/plan.md"],
                            "acceptance_criteria": [{"text": "The plan is ready.", "measured_against": "SOURCE"}]}],
            "finding_dispositions": [],
        }
        return subprocess.CompletedProcess(
            command_parts, 0,
            json.dumps({"is_error": False, "structured_output": {"result": result}}), "",
        )

    monkeypatch.setattr(agent_runtime, "_run_agent_process", fake_process)
    config = OrchestratorConfig(
        repo_root=root, inbox_dir=root / "inbox", outbox_dir=root / "outbox",
        provider_input_budget=default_provider_input_budget_policy((
            ("implementer", "implementer", "claude"),  # allowlist:provider -- profile configuration: fake occupancy
            ("reviewer", "reviewer", "codex"),  # allowlist:provider -- profile configuration: fake occupancy
            ("final_reviewer", "reviewer", "codex"),  # allowlist:provider -- profile configuration: fake occupancy
        )),
    )
    digests = []

    def run():
        return run_native_implementer_agent(
            adapter, bundle, config=config, shorten=lambda value, _limit: value or "",
            operation="implementer_plan", binding_fingerprint="a" * 64,
            pre_start_callback=lambda measurement: digests.append(measurement.input_digest),
        )

    with pytest.raises(RuntimeError, match="simulated interruption"):
        run()
    assert adapter.invocation.runtime_dir is None or not adapter.invocation.runtime_dir.exists()
    assert run().result.ready is True
    assert starts == [root, root]
    assert digests[0] == digests[1]


def test_settings_credentials_use_documented_list_form(tmp_path: Path) -> None:
    # Regression: another shape made the CLI drop the whole settings document.
    _, adapter, _, prepared = _prepared(tmp_path)
    try:
        settings = json.loads(prepared.command[prepared.command.index("--settings") + 1])
        assert settings["sandbox"]["credentials"] == {"envVars": [
            {"name": "ANTHROPIC_API_KEY", "mode": "deny"},
            {"name": "ANTHROPIC_AUTH_TOKEN", "mode": "deny"},
            {"name": "CLAUDE_CODE_OAUTH_TOKEN", "mode": "deny"},  # allowlist:provider -- certification data: credential denylist
        ]}
    finally:
        adapter.cleanup()


def test_cli_edit_rules_repeat_the_settings_deny_rules(tmp_path: Path) -> None:
    _, adapter, _, prepared = _prepared(tmp_path)
    command = list(prepared.command)
    bound = command[command.index("--settings") + 1]
    try:
        cli_rules = command[command.index("--disallowedTools") + 1]
        assert cli_rules == ",".join(json.loads(bound)["permissions"]["deny"])
        assert "Edit(./.orchestrator/**)" in cli_rules.split(",")
        command[command.index("--disallowedTools") + 1] = "Edit(./.git),Edit(./.git/**)"
        with pytest.raises(NativeProviderSchemaError):
            normalize_transport_profile("claude-implementer", command, bound_settings_json=bound)  # allowlist:provider -- profile configuration: implementer fake
    finally:
        adapter.cleanup()


@pytest.mark.parametrize("change", [
    lambda s: s["sandbox"].__setitem__("credentials", {"envVars": {"deny": ["ANTHROPIC_API_KEY"]}}),
    lambda s: s["sandbox"].__setitem__("failIfUnavailable", False),
    lambda s: s["sandbox"]["network"].__setitem__("allowedDomains", ["example.com"]),
    lambda s: s["sandbox"].__setitem__("filesystem", {"denyWrite": []}),
    lambda s: s["permissions"].__setitem__("deny", ["Edit(./src)", "Edit(./src/**)"]),
    lambda s: s.__setitem__("disableAllHooks", False),
    lambda s: s.__setitem__("extra", True),
])
def test_normalizer_checks_settings_semantics_even_when_bound(tmp_path: Path, change) -> None:
    _, adapter, _, prepared = _prepared(tmp_path)
    command = list(prepared.command)
    settings = json.loads(command[command.index("--settings") + 1])
    change(settings)
    weakened = json.dumps(settings, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    command[command.index("--settings") + 1] = weakened
    deny = settings["permissions"]["deny"]
    command[command.index("--disallowedTools") + 1] = ",".join(deny)
    try:
        with pytest.raises(NativeProviderSchemaError):
            normalize_transport_profile("claude-implementer", command, bound_settings_json=weakened)  # allowlist:provider -- profile configuration: implementer fake
    finally:
        adapter.cleanup()


def test_protection_path_with_rule_separator_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "repo with space"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    with pytest.raises(AgentOutputError, match="rule separator"):
        protected_implementer_paths(root, root / "inbox", root / "outbox", "run-1")


def test_settings_keep_only_outermost_protected_paths(tmp_path: Path) -> None:
    # Live smoke 2026-09-30: a missing nested denial below .orchestrator made
    # bwrap fail before every Bash command ("Can't create file ... Read-only").
    root, adapter, _, prepared = _prepared(tmp_path)
    try:
        settings = json.loads(prepared.command[prepared.command.index("--settings") + 1])
        deny_write = settings["sandbox"]["filesystem"]["denyWrite"]
        assert not any(
            inner != outer and Path(inner).is_relative_to(Path(outer))
            for inner in deny_write for outer in deny_write
        )
        assert str(root.resolve() / ".orchestrator") in deny_write
        assert "Edit(./.orchestrator/**)" in settings["permissions"]["deny"]
        assert not any("native-codex-evidence" in rule for rule in settings["permissions"]["deny"])  # allowlist:provider -- transport: evidence namespace
    finally:
        adapter.cleanup()


def test_normalizer_rejects_nested_protected_paths(tmp_path: Path) -> None:
    _, adapter, _, prepared = _prepared(tmp_path)
    command = list(prepared.command)
    settings = json.loads(command[command.index("--settings") + 1])
    settings["permissions"]["deny"] += ["Edit(./.orchestrator/checkpoints)", "Edit(./.orchestrator/checkpoints/**)"]
    settings["sandbox"]["filesystem"]["denyWrite"].append("/nested/placeholder")
    nested = json.dumps(settings, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    command[command.index("--settings") + 1] = nested
    command[command.index("--disallowedTools") + 1] = ",".join(settings["permissions"]["deny"])
    try:
        with pytest.raises(NativeProviderSchemaError, match="nested"):
            normalize_transport_profile("claude-implementer", command, bound_settings_json=nested)  # allowlist:provider -- profile configuration: implementer fake
    finally:
        adapter.cleanup()


def test_toolchain_settings_path_and_semantic_binding(tmp_path):
    root = _repo(tmp_path)
    tools = tmp_path / "node-v22"
    (tools / "bin").mkdir(parents=True)
    no_bin = tmp_path / "another-tool"
    no_bin.mkdir()
    adapter = NativeClaudeImplementerAdapter(  # allowlist:provider -- transport: toolchain coverage
        AgentSettings("claude", "claude", "opus", 60, "high", toolchain_read_roots=(str(tools), str(no_bin)))  # allowlist:provider -- profile configuration: toolchain coverage
    )
    adapter.bind_implementer_boundary(root, root / "inbox", root / "outbox", "run-1")
    bundle = build_native_implementer_request(_spec(), profile="claude-implementer")  # allowlist:provider -- profile configuration: toolchain coverage
    prepared = adapter.prepare_native_provider_input(bundle)
    try:
        settings_json = prepared.command[prepared.command.index("--settings") + 1]
        settings = json.loads(settings_json)
        assert settings["sandbox"]["filesystem"]["allowRead"] == [str(tools), str(no_bin)]
        assert "allowWrite" not in settings["sandbox"]["filesystem"]
        assert adapter.env["PATH"] == str(tools / "bin") + ":/usr/local/bin:/usr/bin:/bin"
        assert normalize_transport_profile("claude-implementer", prepared.command,  # allowlist:provider -- profile configuration: toolchain coverage
            bound_settings_json=settings_json, bound_repository_root=root,
            bound_toolchain_read_roots=(str(tools), str(no_bin)))
    finally:
        adapter.cleanup()


@pytest.mark.parametrize("kind", ["home", "repo", "ancestor", "protected", "missing", "write", "unbound"])
def test_semantic_normalization_rejects_manipulated_allow_read(tmp_path, kind):
    root, adapter, _, prepared = _prepared(tmp_path)
    safe = tmp_path / "safe-tools"
    safe.mkdir()
    bound = prepared.command[prepared.command.index("--settings") + 1]
    changed = json.loads(bound)
    replacement = {"home": Path.home(), "repo": root, "ancestor": root.parent,
                   "protected": root / ".git", "missing": tmp_path / "missing",
                   "write": safe, "unbound": safe}[kind]
    changed["sandbox"]["filesystem"]["allowRead"] = [str(replacement)]
    if kind == "write":
        changed["sandbox"]["filesystem"]["allowWrite"] = [str(safe)]
    new_bound = json.dumps(changed, sort_keys=True, separators=(",", ":"))
    command = list(prepared.command)
    command[command.index("--settings") + 1] = new_bound
    try:
        with pytest.raises(NativeProviderSchemaError):
            normalize_transport_profile("claude-implementer", command,  # allowlist:provider -- profile configuration: manipulated read root
                bound_settings_json=new_bound, bound_repository_root=root,
                bound_toolchain_read_roots=() if kind == "unbound" else (str(replacement),))
    finally:
        adapter.cleanup()


def test_tool_roots_cannot_overlap_external_queue_or_worktree_common_dir(tmp_path):
    from toolchain_paths import validate_toolchain_read_roots
    root = _repo(tmp_path)
    external = tmp_path / "external-queue"
    external.mkdir()
    protected = protected_implementer_paths(root, external, root / "outbox", "run-1")
    with pytest.raises(ValueError, match="protected paths"):
        validate_toolchain_read_roots([str(external)], root, protected)
    outside = tmp_path / "shared-git"
    outside.mkdir()
    with pytest.raises(ValueError, match="protected paths"):
        validate_toolchain_read_roots([str(outside)], root, (*protected, outside / "worktrees"))



def test_toolchain_root_drift_fails_before_preparing_command(tmp_path):
    root = _repo(tmp_path)
    tools = tmp_path / "tools"
    tools.mkdir()
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    tools.rmdir()
    tools.symlink_to(replacement, target_is_directory=True)
    with pytest.raises(AgentOutputError, match="changed after profile binding"):
        implementer_settings(protected_implementer_paths(root, root / "inbox", root / "outbox", "run-1"),
                             root, (str(tools),))
