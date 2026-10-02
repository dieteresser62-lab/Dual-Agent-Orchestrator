"""D1 isolation contracts, using marked fake executables only."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_adapters import AgentOutputError, AgentPermissionError, NativeCodexAdapter, NativeCodexExecutionBoundary  # allowlist:provider -- transport: D1 implementer isolation binding
from agent_config import AgentSettings, isolation_options_digest
from model_catalog import hardened_reviewer_catalog, reviewer_model_row_sha256
from native_provider_schema import CODEX_REVIEW_DISABLED_FEATURES, NativeProviderSchemaError, normalize_transport_profile  # allowlist:provider -- transport: D1 implementer isolation binding
from scripts.qualification import offline_boundary as boundary
from scripts.qualification.profiles import BOUNDARY_CODEX_IMPLEMENTER  # allowlist:provider -- transport: D1 implementer isolation binding
from test_codex_review_adapter import _entry  # allowlist:provider -- transport: D1 implementer isolation binding


def bind_fake_codex(adapter, tmp_path):  # allowlist:provider -- transport: D1 implementer isolation binding
    entry = _entry(tmp_path)
    entry.write_text("#!/bin/sh\n# dao-probe-fake-v1\nexit 0\n")
    adapter.provider_identity = SimpleNamespace(entry_path=str(entry), kind="verified")
    adapter.settings = replace(adapter.settings, reviewer_model_catalog_json=hardened_reviewer_catalog(
        {"models": [{"slug": adapter.model, "multi_agent_version": "v2", "supports_search_tool": True}]}, adapter.model))
    return adapter


def prepared(tmp_path):
    paths = boundary.fixture(tmp_path / "fixture")
    adapter = bind_fake_codex(NativeCodexAdapter(AgentSettings("codex", "codex", "gpt-6.1-sol", 60, "high")), tmp_path / "binary")  # allowlist:provider -- profile configuration: marked fake implementer
    adapter.settings = replace(adapter.settings, toolchain_read_roots=(str(paths["toolchain"]),))
    adapter.bind_implementer_boundary(paths["repo"], paths["repo"] / "inbox", paths["repo"] / "outbox", "boundary-probe")
    bundle = boundary.implementer_bundle("D1 boundary", paths["repo"], profile=BOUNDARY_CODEX_IMPLEMENTER)  # allowlist:provider -- transport: D1 implementer isolation binding
    command = adapter.prepare_native_provider_input(bundle, NativeCodexExecutionBoundary.production(paths["repo"]))  # allowlist:provider -- transport: D1 implementer isolation binding
    return adapter, paths, bundle, command


def normalize(adapter, command):
    return normalize_transport_profile(BOUNDARY_CODEX_IMPLEMENTER, command,  # allowlist:provider -- transport: D1 implementer isolation binding
        bound_package_root=Path(adapter.provider_identity.entry_path).parents[1],
        bound_repository_root=adapter._repository_root, bound_container=adapter._repository_root,
        bound_runtime_dir=adapter.invocation.runtime_dir, bound_scratch=adapter._scratch,
        bound_protected_paths=adapter._protected_paths, bound_toolchain_read_roots=adapter.settings.toolchain_read_roots,
        bound_environment=adapter.env,
        bound_model_row_sha256=reviewer_model_row_sha256(adapter.settings.reviewer_model_catalog_json, adapter.model))


def test_hardened_command_environment_and_rights(tmp_path, monkeypatch):
    monkeypatch.setenv("DAO_DECOY_TOKEN", "parent-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "parent-secret")
    adapter, paths, bundle, result = prepared(tmp_path)
    try:
        command = result.command
        assert "--sandbox" not in command and "project_doc_max_bytes=0" not in command
        assert {"--ignore-user-config", "--ignore-rules", 'web_search="disabled"', 'shell_environment_policy.inherit="core"'} <= set(command)
        assert [command[i+1] for i, arg in enumerate(command) if arg == "--disable"] == list(CODEX_REVIEW_DISABLED_FEATURES)  # allowlist:provider -- transport: D1 implementer isolation binding
        config = next(c for c in command if c.startswith("permissions.dao-implementer="))
        import tomllib
        permissions = tomllib.loads(config)["permissions"]["dao-implementer"]
        fs = permissions["filesystem"]
        assert fs[str(paths["repo"])]["."] == "write"
        assert all(fs[str(paths["repo"])][name] == "read" for name in (".git", ".orchestrator", "inbox", "outbox"))
        assert fs[str(paths["toolchain"])] == "read"
        assert fs[str(adapter._scratch)] == "write" and permissions["network"] == {"enabled": False}
        assert adapter._scratch.stat().st_mode & 0o777 == 0o700
        assert adapter.env["TMPDIR"] == str(adapter._scratch)
        assert "DAO_DECOY_TOKEN" not in adapter.env and "OPENAI_API_KEY" not in adapter.env
        assert not adapter.inherit_process_environment
        assert normalize(adapter, command).provider == "codex"  # allowlist:provider -- transport: D1 implementer isolation binding
        adapter.before_provider_process()
        adapter.after_provider_process()
    finally:
        scratch, runtime = adapter._scratch, adapter.invocation.runtime_dir
        adapter.cleanup()
        assert not scratch.exists() and not runtime.exists()


@pytest.mark.parametrize("change", ["sandbox", "extra-config", "feature", "permission", "environment", "catalog", "toolchain", "scratch-mode", "glob-root"])
def test_transport_rejects_weakened_or_drifted_bindings(tmp_path, change):
    adapter, paths, _, result = prepared(tmp_path)
    command = list(result.command)
    try:
        if change == "sandbox": command[-1:-1] = ["--sandbox", "workspace-write"]
        elif change == "extra-config": command[-1:-1] = ["-c", 'web_search="live"']
        elif change == "feature": command[command.index("multi_agent")] = "different-feature"
        elif change == "permission": command[command.index('default_permissions="dao-implementer"')] = 'default_permissions=":workspace"'
        elif change == "environment": adapter.env["DAO_DECOY_TOKEN"] = "secret"
        elif change == "catalog": (adapter.invocation.runtime_dir / "model-catalog.json").write_text('{"models":[]}')
        elif change == "toolchain": adapter.settings = replace(adapter.settings, toolchain_read_roots=())
        elif change == "scratch-mode": adapter._scratch.chmod(0o755)
        elif change == "glob-root":
            root = tmp_path / "tool[chain]"
            root.mkdir()
            adapter.settings = replace(adapter.settings, toolchain_read_roots=(str(root),))
        with pytest.raises(NativeProviderSchemaError): normalize(adapter, command)
    finally: adapter.cleanup()


@pytest.mark.parametrize("change", ["catalog", "scratch", "protected-content", "protected-add", "protected-mode"])
def test_process_hooks_fail_closed(tmp_path, change):
    adapter, paths, _, _ = prepared(tmp_path)
    try:
        if change in {"catalog", "scratch"}:
            if change == "catalog": (adapter.invocation.runtime_dir / "model-catalog.json").write_text('{"models":[]}')
            else: adapter._scratch.chmod(0o755)
            with pytest.raises(AgentOutputError, match="before start"): adapter.before_provider_process()
        else:
            adapter.before_provider_process()
            target = paths["repo"] / ".orchestrator/artifacts/native-codex-evidence/protected-decoy.txt"  # allowlist:provider -- transport: D1 implementer isolation binding
            if change == "protected-content": target.write_text("changed")
            elif change == "protected-add": (target.parent / "new.txt").touch()
            else: target.chmod(0o777)
            with pytest.raises(AgentPermissionError, match="protected trees"): adapter.after_provider_process()
    finally: adapter.cleanup()


def test_catalog_and_toolchain_are_resume_digest_inputs(tmp_path):
    adapter, _, _, _ = prepared(tmp_path)
    try:
        digest = isolation_options_digest(adapter.settings)
        assert digest != isolation_options_digest(replace(adapter.settings, toolchain_read_roots=()))
        source = json.loads(adapter.settings.reviewer_model_catalog_json)
        source["models"][0]["context_window"] = 123
        assert digest != isolation_options_digest(replace(adapter.settings, reviewer_model_catalog_json=hardened_reviewer_catalog(source, adapter.model)))
    finally: adapter.cleanup()


def test_pre_d1_run_stops_before_binary_probe_with_recovery_hint(monkeypatch):
    import workflow_run_setup
    from state_io import StateSchemaError
    from workflow_state import scripted_profile_binding
    profiles = {slot: scripted_profile_binding(slot) for slot in ("implementer", "reviewer", "final_reviewer")}
    profiles["implementer"] = replace(profiles["implementer"],
        capability_sha256="19040e1f2f2eec773e1e99132c544fe8d003db569cf8d663547fdde3e5077500",
        rights_sha256="a678bf59677c0f9d05040dfacd7e618d00e806cd30253b82c432cd2f117af3c8",
        isolation_options_sha256=None)
    state = SimpleNamespace(protocol_binding=SimpleNamespace(**{slot + "_profile": row for slot, row in profiles.items()}))
    args = SimpleNamespace(slot_settings={slot: AgentSettings(row.provider, row.binary, row.model, None, row.effort)
                                         for slot, row in profiles.items()}, agent_profile_overrides=())
    monkeypatch.setattr(workflow_run_setup, "_capture_slot_identities", lambda *a, **k: pytest.fail("provider probe started"))
    with pytest.raises(StateSchemaError, match="AGENT-PROFILE-DIFF.*slot=implementer.*start a new run"):
        workflow_run_setup._apply_resumed_agent_profiles(args, state)


def test_marked_fake_process_and_its_shell_do_not_inherit_parent_token(tmp_path, monkeypatch):
    from agent_runtime import _provider_environment
    monkeypatch.setenv("DAO_DECOY_TOKEN", "parent-decoy")
    adapter, paths, _, _ = prepared(tmp_path)
    entry = Path(adapter.provider_identity.entry_path)
    entry.write_text("#!/bin/sh\n# dao-probe-fake-v1\nenv | cut -d= -f1\n/bin/sh -c 'if test "
                     "\"${DAO_DECOY_TOKEN+x}\" = x; then echo TOKEN_VISIBLE; else echo TOKEN_HIDDEN; fi'\n")
    entry.chmod(0o755)
    try:
        result = boundary.execute([str(entry)], env=_provider_environment(adapter), cwd=paths["repo"], stdin="", timeout=5)
        assert result["exit_code"] == 0 and not result["timed_out"]
        assert "PATH" in result["stdout"].splitlines()
        assert "TOKEN_HIDDEN" in result["stdout"] and "TOKEN_VISIBLE" not in result["stdout"]
        assert "DAO_DECOY_TOKEN" not in result["stdout"]
    finally: adapter.cleanup()


def test_canary_binding_cannot_authorize_production(tmp_path):
    adapter, paths, bundle, _ = prepared(tmp_path)
    try:
        canary_root = tmp_path / "canary"
        canary_root.mkdir()
        adapter.prepare_native_provider_input(bundle, NativeCodexExecutionBoundary.canary(  # allowlist:provider -- transport: canary reuse regression
            paths["repo"], execution_root=canary_root, evidence_asset_root=canary_root))
        with pytest.raises(AgentOutputError, match="boundary is unbound"):
            adapter.prepare_native_provider_input(bundle, NativeCodexExecutionBoundary.production(paths["repo"]))  # allowlist:provider -- transport: production requires full fresh binding
        adapter.bind_implementer_boundary(paths["repo"], paths["repo"] / "inbox", paths["repo"] / "outbox", "boundary-probe")
        adapter.prepare_native_provider_input(bundle, NativeCodexExecutionBoundary.production(paths["repo"]))  # allowlist:provider -- transport: explicit full rebinding accepted
        assert len(adapter._protected_paths) > 4
    finally:
        adapter.cleanup()


def test_parent_path_keeps_env_node_identity_through_run_agent(tmp_path, monkeypatch):
    import agent_runtime
    from provider_identity import capture_provider_identity
    adapter, paths, bundle, _ = prepared(tmp_path)
    adapter.cleanup()
    adapter.settings = replace(adapter.settings, toolchain_read_roots=())
    entry = Path(adapter.provider_identity.entry_path)
    entry.write_text("#!/usr/bin/env node\n// dao-probe-fake-v1\n")
    entry.chmod(0o755)
    node_dirs = [tmp_path / "nvm/bin", tmp_path / "system/bin"]
    for i, root in enumerate(node_dirs):
        root.mkdir(parents=True)
        node = root / "node"
        node.write_text(f"dao-probe-fake-v1 native node {i}\n")
        node.chmod(0o755)
    path = ":".join(str(root) for root in node_dirs) + ":/usr/bin:/bin"
    monkeypatch.setenv("PATH", path)
    version = "codex-cli 0.159.2"  # allowlist:provider -- transport: identity probe fixture
    def probe(command, timeout=20):
        if command[-1] == "--version":
            return 0, version if str(entry) in command else "v22.23.2", ""
        return 0, " ".join(adapter.capability.required_help_flags), ""
    monkeypatch.setattr(agent_runtime, "run_local_command", probe)
    adapter.provider_identity = capture_provider_identity(str(entry), ("--version",), probe, path=path)
    adapter.capability_verified = True
    prepared_input = adapter.prepare_native_provider_input(bundle, NativeCodexExecutionBoundary.production(paths["repo"]))  # allowlist:provider -- transport: production identity regression
    try:
        assert adapter.env["PATH"] == path
        assert normalize(adapter, prepared_input.command)
        after = adapter.after_provider_process
        postchecks = []
        def postcheck():
            postchecks.append(True)
            after()
        monkeypatch.setattr(adapter, "after_provider_process", postcheck)
        def process(_adapter, command, stdin, **kwargs):
            assert command[:2] == [str(node_dirs[0] / "node"), str(entry)]
            assert kwargs["env"]["PATH"] == path
            adapter.invocation.last_message_file.write_text(json.dumps(boundary.valid_implementer_result(bundle)))
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        monkeypatch.setattr(agent_runtime, "_run_agent_process", process)
        result = agent_runtime.run_agent(adapter, "", config=agent_runtime.OrchestratorConfig(repo_root=paths["repo"]),
            shorten=lambda text, limit: text, operation="implementer_implementation", prepared_provider_input=prepared_input)
        assert json.loads(result)["request_id"] == bundle.bound_context.request_id
        assert postchecks == [True]
    finally:
        adapter.cleanup()


@pytest.mark.parametrize("end", ["normal", "timeout", "silence", "abort"])
def test_marked_fake_placeholders_cleaned_on_every_process_exit(tmp_path, end, caplog):
    import os
    import sys
    import agent_runtime
    caplog.set_level("INFO", logger="agent_adapters")
    adapter, paths, _, _ = prepared(tmp_path)
    entry = paths["repo"] / "placeholder-fake.py"
    entry.write_text("# dao-probe-fake-v1\nfrom pathlib import Path\nimport time\n"
                     "Path('.agents').mkdir()\nPath('.gemini').touch()\nPath('.gemini').chmod(0o444)\n"
                     + ("time.sleep(30)\n" if end != "normal" else ""))
    adapter.stall_timeout_seconds = 0.2 if end == "silence" else 0
    adapter.tool_timeout_seconds = 0
    adapter.before_provider_process()
    def abort(pid):
        if end == "abort":
            import time
            deadline = time.monotonic() + 5
            while not (paths["repo"] / ".gemini").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert (paths["repo"] / ".gemini").exists()
            raise KeyboardInterrupt
    try:
        try:
            result = agent_runtime._run_agent_process(
                adapter, [sys.executable, str(entry)], None,
                config=agent_runtime.OrchestratorConfig(repo_root=paths["repo"], agent_live_stream=False),
                env=os.environ.copy(), execution_root=paths["repo"],
                timeout_seconds=0.2 if end == "timeout" else 5, agent_key="marked-fake",
                process_started=abort,
            )
        except (agent_runtime.AgentProcessError, agent_runtime.subprocess.TimeoutExpired, KeyboardInterrupt):
            assert end != "normal"
        else:
            assert end == "normal" and result.returncode == 0
        adapter.after_provider_process()
        receipt = adapter.metadata["sandbox_placeholder_cleanup"]
        assert receipt["process_groups_ended"]
        assert set(receipt["removed"]) == {str(paths["repo"] / name) for name in (".agents", ".gemini")}
        assert not receipt["retained"]
        cleanup_lines = [record.message for record in caplog.records if "[SANDBOX_PLACEHOLDER_CLEANUP]" in record.message]
        assert cleanup_lines == ["[SANDBOX_PLACEHOLDER_CLEANUP] removed=['.agents', '.gemini'] retained=[]"]
        assert not any(record.levelname == "WARNING" and "placeholders retained" in record.message for record in caplog.records)
        assert adapter.remove_sandbox_placeholders() == ()
        assert not (paths["repo"] / ".agents").exists() and not (paths["repo"] / ".gemini").exists()
    finally:
        adapter.cleanup()


@pytest.mark.parametrize("kind", ["file", "directory", "symlink", "dangling", "foreign", "live", "unknown", "changed-signature"])
def test_cleanup_retains_unexpected_content_and_unproven_end(tmp_path, monkeypatch, kind, caplog):
    import os
    import protected_tree
    adapter, paths, _, _ = prepared(tmp_path)
    root = paths["repo"]
    outside = tmp_path / "outside"
    outside.touch()
    try:
        adapter.before_provider_process()
        candidate = root / ".gemini"
        if kind == "file": candidate.write_text("operator data")
        elif kind == "directory":
            candidate.mkdir()
            (candidate / "keep").touch()
        elif kind in {"symlink", "dangling"}: candidate.symlink_to(outside if kind == "symlink" else tmp_path / "absent")
        else: candidate.touch()
        monkeypatch.setattr(protected_tree, "process_group_ended", lambda *args: kind not in {"live", "unknown"})
        adapter.bind_sandbox_process(999999999, None)
        if kind in {"foreign", "changed-signature"}:
            original = os.stat
            calls = 0
            def altered(name, **kwargs):
                nonlocal calls
                info = original(name, **kwargs)
                if name == ".gemini" and kwargs.get("dir_fd") is not None:
                    calls += 1
                    values = {field: getattr(info, field) for field in dir(info) if field.startswith("st_")}
                    if kind == "foreign": values["st_uid"] += 1
                    elif calls > 1: values["st_ino"] += 1
                    return SimpleNamespace(**values)
                return info
            monkeypatch.setattr(protected_tree.os, "stat", altered)
        with pytest.raises(AgentPermissionError, match="protected trees"):
            adapter.after_provider_process()
        assert os.path.lexists(candidate)
        assert outside.read_bytes() == b""
        assert str(candidate) in adapter.metadata["sandbox_placeholder_cleanup"]["retained"]
        assert "Sandbox placeholders retained; inspect paths:" in caplog.text
        assert candidate.name in caplog.text
    finally:
        adapter.cleanup()


def test_cleanup_preserves_preexisting_empty_paths_and_external_mounts(tmp_path, monkeypatch):
    import protected_tree
    adapter, paths, _, _ = prepared(tmp_path)
    root = paths["repo"]
    present = root / ".gemini"
    present.touch()
    external = tmp_path / "external-protected"
    adapter._protected_paths += (external,)
    try:
        adapter.before_provider_process()
        external.mkdir()
        adapter.bind_sandbox_process(999999999, None)
        monkeypatch.setattr(protected_tree, "process_group_ended", lambda *args: True)
        adapter.after_provider_process()
        assert present.exists() and present.read_bytes() == b""
        assert not external.exists()
        assert str(present) not in adapter.metadata["sandbox_placeholder_cleanup"]["missing_before"]
        assert str(external) in adapter.metadata["sandbox_placeholder_cleanup"]["removed"]
    finally:
        adapter.cleanup()


def test_placeholder_cleanup_keeps_replaced_parent_and_symlink_ancestors(tmp_path):
    from protected_tree import SandboxPlaceholders
    parent = tmp_path / "original"
    parent.mkdir()
    candidate = parent / "missing"
    placeholders = SandboxPlaceholders((candidate,))
    moved = tmp_path / "moved"
    parent.rename(moved)
    parent.mkdir()
    candidate.write_text("new parent data")
    (moved / "missing").touch()
    try:
        result = placeholders.remove(process_groups_ended=True)
        assert result["removed"] == [str(candidate)]
        assert candidate.read_text() == "new parent data"
        assert not (moved / "missing").exists()
    finally:
        placeholders.close()
    alias = tmp_path / "alias"
    alias.symlink_to(parent, target_is_directory=True)
    linked = SandboxPlaceholders((alias / "other",))
    (parent / "other").touch()
    try:
        assert linked.remove(process_groups_ended=True)["removed"] == []
        assert (parent / "other").exists()
    finally:
        linked.close()


@pytest.mark.parametrize("status", ["running", "unknown", "ended"])
def test_placeholder_process_proof_requires_ended_session(monkeypatch, status):
    from protected_tree import process_group_ended
    import provider_process
    monkeypatch.setattr(provider_process, "observe_identity", lambda _: SimpleNamespace(status=provider_process.ProcessStatus(status)))
    assert process_group_ended(123, object()) is (status == "ended")


@pytest.mark.parametrize("error", [ProcessLookupError, PermissionError, None])
def test_placeholder_fast_exit_fallback_requires_esrch(monkeypatch, error):
    import protected_tree
    def killpg(pid, sig):
        assert (pid, sig) == (123, 0)
        if error is not None: raise error
    monkeypatch.setattr(protected_tree.os, "killpg", killpg)
    assert protected_tree.process_group_ended(123, None) is (error is ProcessLookupError)
