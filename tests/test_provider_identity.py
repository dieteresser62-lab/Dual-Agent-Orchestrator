from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent_runtime
import agent_adapters
import provider_identity
import workflow_run_setup
from agent_adapters import CapabilitySpec
from agent_config import AgentSettings
from agent_runtime import AgentCompatibilityError, OrchestratorConfig, run_agent
from provider_input_budget import PreparedProviderInput, ProviderInputComponent
from provider_identity import capture_provider_identity, executable_candidates
from state_io import StateSchemaError
from workflow_state import ProtocolBinding, ProtocolMode, scripted_profile_binding, init_workflow_state


def _executable(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(0o755)
    return path


class FakeAdapter:
    name = "codex"
    cli_binary = "codex"
    model = "gpt-6-sol"
    effort = "high"
    timeout = 10
    reviewer = False
    env: dict[str, str] = {}
    required_hosts: tuple[str, ...] = ()
    capability = CapabilitySpec(
        ("--version",), ("exec", "--help"),
        (r"^codex-cli 0\.156\.1$",), ("--output-schema", "--output-last-message"),
    )
    capability_verified = False
    provider_identity = None
    metadata: dict[str, object] = {}

    def build_command(self, prompt: str) -> tuple[list[str], bool]:
        return [self.cli_binary, "exec", "-"], True

    def prepare_provider_input(self, prompt: str) -> PreparedProviderInput:
        return PreparedProviderInput(tuple(self.build_command(prompt)[0]), prompt, (ProviderInputComponent("stdin_prompt", prompt),))

    def validate_process_output(self, stderr: str) -> None:
        assert not stderr

    def extract_output(self, stdout: str, stderr: str, extra_files: dict[str, str]) -> str:
        return stdout

    def cleanup(self) -> None:
        pass


def _fake_runner(versions: dict[str, str]):
    def run(args: list[str], timeout: int = 20) -> tuple[int, str, str]:
        assert timeout == 20
        if args[-2:] == ["exec", "--help"]:
            return 0, "--output-schema --output-last-message", ""
        if args[-1] == "--version":
            target = args[-2] if len(args) > 2 else args[0]
            return 0, versions[str(Path(target).resolve())], ""
        raise AssertionError(args)
    return run


def _recording_runner(versions: dict[str, str], calls: list[list[str]]):
    delegate = _fake_runner(versions)

    def run(args: list[str], timeout: int = 20) -> tuple[int, str, str]:
        calls.append(list(args))
        return delegate(args, timeout)

    return run


def _install_script(root: Path, version: str, *, name: str = "codex") -> tuple[Path, Path, Path]:
    starter = _executable(root / "package" / f"{name}.js", b"#!/usr/bin/env node\n")
    node = _executable(root / "bin" / "node", b"fake node")
    link = root / "bin" / name
    link.symlink_to(starter)
    return link, starter, node


@pytest.mark.parametrize("mutation", ("unchanged", "symlink", "version", "content", "interpreter", "windows"))
def test_resume_rechecks_recorded_identity_without_provider_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    link, script, interpreter = _install_script(tmp_path, "0.156.1")
    monkeypatch.setenv("PATH", str(link.parent))
    versions = {
        str(script): "codex-cli 0.156.1",
        str(interpreter): "v22.23.2",
    }
    calls: list[list[str]] = []
    monkeypatch.setattr(agent_runtime, "run_local_command", _recording_runner(versions, calls))
    bound = capture_provider_identity(str(link), ("--version",), lambda args: _fake_runner(versions)(args))
    slots = {}
    profiles = {}
    for slot in ("implementer", "reviewer", "final_reviewer"):
        default = scripted_profile_binding(slot)
        slots[slot] = AgentSettings(default.provider, str(link), default.model, None, default.effort, default.max_budget_usd)
        profiles[slot] = replace(
            default, binary=str(link), binary_identity=bound,
            binary_identity_sha256=bound.digest,
        )
    state = init_workflow_state(
        run_id="identity-resume", task_file="/tmp/task.md", branch="feature/identity",
        branch_base="a" * 40, first_slice_start_commit="a" * 40, slice_count=1,
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2, "3",
            implementer_profile=profiles["implementer"],
            reviewer_profile=profiles["reviewer"],
            final_reviewer_profile=profiles["final_reviewer"],
        ),
    )

    def fake_registry(_slots):
        registry = {}
        for slot in _slots:
            adapter = FakeAdapter()
            adapter.cli_binary = str(link)
            registry[slot] = adapter
        return registry

    monkeypatch.setattr(agent_adapters, "build_slot_agent_registry", fake_registry)
    if mutation == "symlink":
        alternate = _executable(tmp_path / "package" / "other.js", b"#!/usr/bin/env node\n")
        link.unlink()
        link.symlink_to(alternate)
        versions[str(alternate)] = "codex-cli 0.156.1"
    elif mutation == "version":
        versions[str(script)] = "codex-cli 0.156.2"
    elif mutation == "content":
        script.write_bytes(b"#!/usr/bin/env node\n// changed\n")
    elif mutation == "interpreter":
        interpreter.write_bytes(b"changed node bytes")
    elif mutation == "windows":
        monkeypatch.setattr(provider_identity, "_windows_mount_points", lambda: (tmp_path,))

    args = SimpleNamespace(slot_settings=slots, agent_profile_overrides=())
    if mutation == "unchanged":
        workflow_run_setup._apply_resumed_agent_profiles(args, state)
        assert args.slot_identities["final_reviewer"] == bound
        assert args.slot_settings["implementer"].model == profiles["implementer"].model
    else:
        with pytest.raises(StateSchemaError, match=r"slot=implementer.*path=.*(drift|preflight failed)"):
            workflow_run_setup._apply_resumed_agent_profiles(args, state)
    assert calls if mutation != "windows" else not calls
    assert all(command[-1] == "--version" or command[-2:] == ["exec", "--help"] for command in calls)


def test_two_installations_bind_first_real_script_and_report_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    first, first_target, first_node = _install_script(tmp_path / "nvm", "0.156.1")
    second, second_target, second_node = _install_script(tmp_path / "global", "0.150.1")
    monkeypatch.setenv("PATH", f"{first.parent}:{second.parent}")
    versions = {
        str(first_target): "codex-cli 0.156.1",
        str(second_target): "codex-cli 0.150.1",
        str(first_node): "v22.23.2",
        str(second_node): "v20.0.0",
    }
    monkeypatch.setattr(agent_runtime, "run_local_command", _fake_runner(versions))
    starts: list[list[str]] = []

    def fake_start(_adapter: object, command: list[str], *_args: object, **_kwargs: object):
        starts.append(command)
        return SimpleNamespace(returncode=0, stdout="done", stderr="")

    monkeypatch.setattr(agent_runtime, "_run_agent_process", fake_start)
    adapter = FakeAdapter()
    caplog.set_level("WARNING")
    assert run_agent(adapter, "prompt", config=OrchestratorConfig(repo_root=tmp_path), shorten=str, operation="implementer_implementation") == "done"
    assert starts == [[str(first_node), str(first_target), "exec", "-"]]
    assert adapter.provider_identity.interpreter_real_path == str(first_node)
    assert "0.156.1" in caplog.text and "0.150.1" in caplog.text
    assert str(first) in caplog.text and str(second) in caplog.text


@pytest.mark.parametrize("drift", ("symlink", "version", "interpreter", "interpreter_version"))
def test_script_drift_stops_before_another_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str,
) -> None:
    link, starter, node = _install_script(tmp_path / "install", "0.156.1")
    monkeypatch.setenv("PATH", str(link.parent))
    versions = {str(starter): "codex-cli 0.156.1", str(node): "v22.23.2"}
    monkeypatch.setattr(agent_runtime, "run_local_command", _fake_runner(versions))
    starts: list[list[str]] = []
    monkeypatch.setattr(
        agent_runtime, "_run_agent_process",
        lambda _adapter, command, *_args, **_kwargs: (
            starts.append(command) or SimpleNamespace(returncode=0, stdout="done", stderr="")
        ),
    )
    adapter = FakeAdapter()
    run_agent(adapter, "prompt", config=OrchestratorConfig(repo_root=tmp_path), shorten=str, operation="implementer_implementation")
    if drift == "symlink":
        replacement = _executable(tmp_path / "install" / "package" / "other.js", b"#!/usr/bin/env node\n")
        versions[str(replacement)] = "codex-cli 0.156.1"
        link.unlink()
        link.symlink_to(replacement)
    elif drift == "version":
        versions[str(starter)] = "codex-cli 0.160.1"
    elif drift == "interpreter":
        replacement = _executable(tmp_path / "install" / "bin" / "node-new", b"fake node 2")
        versions[str(replacement)] = "v22.23.2"
        node.unlink()
        node.symlink_to(replacement)
    else:
        versions[str(node)] = "v23.0.0"
    with pytest.raises(AgentCompatibilityError, match="identity drift"):
        run_agent(adapter, "prompt", config=OrchestratorConfig(repo_root=tmp_path), shorten=str, operation="implementer_implementation")
    assert len(starts) == 1


def test_minimal_path_below_baseline_starts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    link, starter, node = _install_script(tmp_path / "global", "0.150.1")
    monkeypatch.setenv("PATH", str(link.parent))
    monkeypatch.setattr(agent_runtime, "run_local_command", _fake_runner({
        str(starter): "codex-cli 0.150.1", str(node): "v20.0.0",
    }))
    starts: list[list[str]] = []
    monkeypatch.setattr(agent_runtime, "_run_agent_process", lambda *_a, **_k: starts.append([]))
    with pytest.raises(AgentCompatibilityError, match="Unsupported codex CLI version"):
        run_agent(FakeAdapter(), "prompt", config=OrchestratorConfig(repo_root=tmp_path), shorten=str, operation="implementer_implementation")
    assert not starts


def test_single_file_binary_has_hash_and_detects_same_version_content_drift(tmp_path: Path) -> None:
    binary = _executable(tmp_path / "claude", b"ELF fake 1")
    run = lambda _args: (0, "2.1.283 (Claude Code)", "")
    first = capture_provider_identity(str(binary), ("--version",), run)
    assert first.launch_prefix == (str(binary),)
    assert first.sha256 is not None and first.interpreter_real_path is None
    binary.write_bytes(b"ELF fake 2")
    assert capture_provider_identity(str(binary), ("--version",), run) != first


def test_binary_launch_uses_realpath_and_hash_drift_stops_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _executable(tmp_path / "versions" / "codex", b"ELF fake 1")
    link = tmp_path / "bin" / "codex"
    link.parent.mkdir()
    link.symlink_to(target)
    monkeypatch.setenv("PATH", str(link.parent))
    monkeypatch.setattr(agent_runtime, "run_local_command", _fake_runner({
        str(target): "codex-cli 0.156.1",
    }))
    starts: list[list[str]] = []
    monkeypatch.setattr(
        agent_runtime, "_run_agent_process",
        lambda _adapter, command, *_args, **_kwargs: (
            starts.append(command) or SimpleNamespace(returncode=0, stdout="done", stderr="")
        ),
    )
    adapter = FakeAdapter()
    run_agent(adapter, "prompt", config=OrchestratorConfig(repo_root=tmp_path), shorten=str, operation="implementer_implementation")
    assert starts == [[str(target), "exec", "-"]]
    target.write_bytes(b"ELF fake 2")
    with pytest.raises(AgentCompatibilityError, match="identity drift"):
        run_agent(adapter, "prompt", config=OrchestratorConfig(repo_root=tmp_path), shorten=str, operation="implementer_implementation")
    assert len(starts) == 1


def test_interpreter_uses_adapter_start_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    link, starter, node = _install_script(tmp_path / "private", "0.156.1")
    monkeypatch.setenv("PATH", str(tmp_path / "missing"))
    monkeypatch.setattr(agent_runtime, "run_local_command", _fake_runner({
        str(starter): "codex-cli 0.156.1", str(node): "v22.23.2",
    }))
    starts: list[list[str]] = []
    monkeypatch.setattr(
        agent_runtime, "_run_agent_process",
        lambda _adapter, command, *_args, **_kwargs: (
            starts.append(command) or SimpleNamespace(returncode=0, stdout="done", stderr="")
        ),
    )
    adapter = FakeAdapter()
    adapter.env = {"PATH": str(link.parent)}
    run_agent(adapter, "prompt", config=OrchestratorConfig(repo_root=tmp_path), shorten=str, operation="implementer_implementation")
    assert starts == [[str(node), str(starter), "exec", "-"]]


def test_missing_path_does_not_fall_back_to_other_installation(tmp_path: Path) -> None:
    link, _, _ = _install_script(tmp_path / "global", "0.150.1")
    assert executable_candidates("codex", str(link.parent)) == (str(link),)
    assert executable_candidates("claude", str(link.parent)) == ()


def test_none_path_uses_environment_for_candidates_interpreter_and_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    link, script, node = _install_script(tmp_path / "private", "0.156.1")
    monkeypatch.setenv("PATH", str(link.parent))
    monkeypatch.setattr(provider_identity, "_windows_mount_points", lambda: ())
    assert executable_candidates("codex", None) == (str(link),)
    target, interpreter_entry, interpreter_real, _ = provider_identity.check_provider_candidate(
        str(link), path=None,
    )
    assert target == script
    assert interpreter_entry == interpreter_real == str(node)
    identities, failures = provider_identity.inspect_provider_installations(
        "codex", ("--version",), _fake_runner({
            str(script): "codex-cli 0.156.1", str(node): "v22.23.2",
        }), path=None,
    )
    assert failures == ()
    assert len(identities) == 1 and identities[0].real_path == str(script)


def test_linux_elf_with_exe_suffix_is_bound_and_started_through_realpath(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"\x7fELF Linux Claude fake"
    target = _executable(tmp_path / "pkg" / "bin" / "claude.exe", content)
    link = tmp_path / "bin" / "claude"
    link.parent.mkdir()
    link.symlink_to(target)
    monkeypatch.setenv("PATH", str(link.parent))
    monkeypatch.setattr(provider_identity, "_windows_mount_points", lambda: ())
    probes: list[list[str]] = []

    def fake_run(args: list[str], timeout: int = 20) -> tuple[int, str, str]:
        assert timeout == 20
        probes.append(list(args))
        if args[-1] == "--version":
            return 0, "2.1.283 (Claude Code)", ""
        assert args[-1] == "--help"
        return 0, "--json-schema --effort", ""

    monkeypatch.setattr(agent_runtime, "run_local_command", fake_run)
    starts: list[list[str]] = []
    monkeypatch.setattr(
        agent_runtime, "_run_agent_process",
        lambda _adapter, command, *_args, **_kwargs: (
            starts.append(command) or SimpleNamespace(returncode=0, stdout="done", stderr="")
        ),
    )
    adapter = FakeAdapter()
    adapter.name = "claude"
    adapter.cli_binary = "claude"
    adapter.model = "opus"
    adapter.capability = CapabilitySpec(
        ("--version",), ("--help",),
        (r"^2\.1\.283 \(Claude Code\)$",), ("--json-schema", "--effort"),
    )
    assert run_agent(adapter, "prompt", config=OrchestratorConfig(repo_root=tmp_path), shorten=str, operation="reviewer_slice_review") == "done"
    assert adapter.provider_identity.real_path == str(target)
    assert adapter.provider_identity.sha256 == hashlib.sha256(content).hexdigest()
    assert adapter.provider_identity.interpreter_real_path is None
    assert starts == [[str(target), "exec", "-"]]
    assert probes == [[str(target), "--version"], [str(target), "--help"]]


def test_drvfs_first_path_hit_rejects_without_any_probe_or_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    drive = tmp_path / "drive"
    windows = _executable(drive / "bin" / "codex", b"#!/bin/sh\n")
    linux, _, _ = _install_script(tmp_path / "linux", "0.156.1")
    monkeypatch.setattr(provider_identity, "_windows_mount_points", lambda: (drive,))
    monkeypatch.setenv("PATH", f"{windows.parent}:{linux.parent}")
    probes: list[list[str]] = []
    starts: list[list[str]] = []
    monkeypatch.setattr(agent_runtime, "run_local_command", _recording_runner({}, probes))
    monkeypatch.setattr(agent_runtime, "_run_agent_process", lambda *_a, **_k: starts.append([]))

    with pytest.raises(AgentCompatibilityError) as failure:
        run_agent(FakeAdapter(), "prompt", config=OrchestratorConfig(repo_root=tmp_path), shorten=str, operation="implementer_implementation")
    message = str(failure.value)
    assert str(windows) in message
    assert "not permitted (Windows/DrvFS), not inspected" in message
    assert "--implementer-binary" in message and "RUN_TASK_IMPLEMENTER_BINARY" in message
    assert "Remedy:" in message and "adjust PATH" in message
    assert probes == starts == []


def test_drvfs_elf_exe_is_rejected_before_content_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    drive = tmp_path / "drive"
    binary = _executable(drive / "claude.exe", b"\x7fELF Linux bytes on DrvFS")
    monkeypatch.setattr(provider_identity, "_windows_mount_points", lambda: (drive,))
    original_open = Path.open

    def reject_content_read(path: Path, *args: object, **kwargs: object):
        if path == binary:
            pytest.fail("DrvFS file content was read before mount rejection")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", reject_content_read)
    with pytest.raises(ValueError, match="lies on a Windows/DrvFS mount"):
        provider_identity.check_provider_candidate(str(binary))


def test_drvfs_later_path_hit_is_reported_without_probe_and_linux_target_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    linux, script, node = _install_script(tmp_path / "linux", "0.156.1")
    drive = tmp_path / "drive"
    windows = _executable(drive / "bin" / "codex", b"#!/bin/sh\n")
    monkeypatch.setattr(provider_identity, "_windows_mount_points", lambda: (drive,))
    monkeypatch.setenv("PATH", f"{linux.parent}:{windows.parent}")
    probes: list[list[str]] = []
    monkeypatch.setattr(agent_runtime, "run_local_command", _recording_runner({
        str(script): "codex-cli 0.156.1", str(node): "v22.23.2",
    }, probes))
    starts: list[list[str]] = []
    monkeypatch.setattr(
        agent_runtime, "_run_agent_process",
        lambda _adapter, command, *_args, **_kwargs: (
            starts.append(command) or SimpleNamespace(returncode=0, stdout="done", stderr="")
        ),
    )
    caplog.set_level("WARNING")
    adapter = FakeAdapter()
    assert run_agent(adapter, "prompt", config=OrchestratorConfig(repo_root=tmp_path), shorten=str, operation="implementer_implementation") == "done"
    assert starts == [[str(node), str(script), "exec", "-"]]
    assert str(windows) in caplog.text
    assert "not permitted (Windows/DrvFS), not inspected" in caplog.text
    assert all(str(windows) not in item for call in probes for item in call)


@pytest.mark.parametrize("kind", ("pe", "exe_mz", "exe_other", "cmd", "bat", "ps1"))
def test_windows_format_or_suffix_never_reaches_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    name = "codex" if kind == "pe" else f"codex.{kind.split('_', 1)[0]}"
    binary = _executable(tmp_path / name, b"MZfake" if kind in {"pe", "exe_mz"} else b"fake")
    monkeypatch.setattr(provider_identity, "_windows_mount_points", lambda: ())
    probes: list[list[str]] = []
    monkeypatch.setattr(agent_runtime, "run_local_command", _recording_runner({}, probes))
    adapter = FakeAdapter()
    adapter.cli_binary = str(binary)  # absolute override is subject to the same guard
    with pytest.raises(AgentCompatibilityError) as failure:
        agent_runtime.verify_agent_capabilities(adapter)
    assert str(binary) in str(failure.value)
    assert "not permitted (Windows/DrvFS), not inspected" in str(failure.value)
    if kind == "exe_mz":
        assert "PE magic MZ" in str(failure.value)
    if kind == "exe_other":
        assert "not ELF" in str(failure.value)
    assert probes == []
    assert adapter.provider_identity is None


def test_script_with_drvfs_interpreter_is_never_probed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    starter = _executable(tmp_path / "linux" / "codex", b"#!/usr/bin/env node\n")
    drive = tmp_path / "drive"
    node = _executable(drive / "bin" / "node", b"fake node")
    monkeypatch.setattr(provider_identity, "_windows_mount_points", lambda: (drive,))
    monkeypatch.setenv("PATH", f"{starter.parent}:{node.parent}")
    probes: list[list[str]] = []
    monkeypatch.setattr(agent_runtime, "run_local_command", _recording_runner({}, probes))
    with pytest.raises(AgentCompatibilityError) as failure:
        agent_runtime.verify_agent_capabilities(FakeAdapter())
    assert str(node) in str(failure.value)
    assert "not permitted (Windows/DrvFS), not inspected" in str(failure.value)
    assert probes == []


def test_linux_shell_wrapper_is_unchecked_and_rejected_without_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = _executable(tmp_path / "codex", b"#!/bin/sh\nexec something\n")
    monkeypatch.setattr(provider_identity, "_windows_mount_points", lambda: ())
    probes: list[list[str]] = []
    monkeypatch.setattr(agent_runtime, "run_local_command", _recording_runner({}, probes))
    adapter = FakeAdapter()
    adapter.cli_binary = str(wrapper)
    with pytest.raises(AgentCompatibilityError, match="unchecked wrapper"):
        agent_runtime.verify_agent_capabilities(adapter)
    assert probes == []


def test_mountinfo_and_proc_mounts_identify_windows_mounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = Path.read_text

    def mountinfo(path: Path, *args: object, **kwargs: object) -> str:
        if path == Path("/proc/self/mountinfo"):
            return "1 0 0:1 / / rw - ext4 /dev/root rw\n2 1 0:2 / /mnt/c rw - 9p C: rw\n"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", mountinfo)
    assert provider_identity._windows_mount_points() == (Path("/mnt/c"),)

    def fallback(path: Path, *args: object, **kwargs: object) -> str:
        if path == Path("/proc/self/mountinfo"):
            raise PermissionError("unreadable")
        if path == Path("/proc/mounts"):
            return "C: /mnt/c drvfs rw 0 0\n"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fallback)
    assert provider_identity._windows_mount_points() == (Path("/mnt/c"),)


def test_missing_mount_tables_fail_closed_before_version_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = _executable(tmp_path / "codex", b"ELF fake")
    monkeypatch.setattr(provider_identity, "_windows_mount_points", lambda: None)
    probes: list[list[str]] = []
    monkeypatch.setattr(agent_runtime, "run_local_command", _recording_runner({}, probes))
    adapter = FakeAdapter()
    adapter.cli_binary = str(binary)
    with pytest.raises(AgentCompatibilityError, match="mount type cannot be verified"):
        agent_runtime.verify_agent_capabilities(adapter)
    assert probes == []
