"""Project diagnostics with marked fake executables; never real provider CLIs."""
import json
import os
from pathlib import Path
import signal
import sys
import time
from types import SimpleNamespace

import pytest

from scripts import check_implementer_sandbox as probe
import claude_implementer_adapter as implementer  # allowlist:provider -- transport: production helper test
from test_codex_implementer_adapter import prepared  # allowlist:provider -- transport: existing marked-fake adapter fixture

FAKE_VERSION = "codex-cli 0.159.2"  # allowlist:provider -- transport: marked-fake version response
HELP_FIXTURE = Path(__file__).parent / "fixtures/codex-sandbox-help-0.159.2.json"  # allowlist:provider -- transport: operator-measured help excerpt
MEASURED_HELP = json.loads(HELP_FIXTURE.read_text())["help_excerpt"]
MEASURED_EXECUTION = json.loads(HELP_FIXTURE.read_text())["execution_measurement"]
MEASURED_PLACEHOLDERS = json.loads(HELP_FIXTURE.read_text())["placeholder_measurement"]

def executable(path, source):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\n# dao-probe-fake-v1\n" + source)
    path.chmod(0o755)
    return path


@pytest.fixture
def project(tmp_path, monkeypatch):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".git").mkdir()
    monkeypatch.chdir(repo)
    # Only the read-only Git discovery is simulated; the productive boundary helper runs.
    def git(command, **kwargs):
        assert command == ["git", "rev-parse", "--absolute-git-dir", "--git-common-dir"]
        return SimpleNamespace(stdout=f"{repo / '.git'}\n{repo / '.git'}\n")
    monkeypatch.setattr(implementer.subprocess, "run", git)
    tools = tmp_path / "tools"
    executable(tools / "bin/dao-test", "print('v22.0.0')\n")
    entry = executable(tmp_path / "provider/node_modules/@openai/codex/bin/codex.js",  # allowlist:provider -- transport: marked fake npm layout
        """import json, os, sys, tomllib
from pathlib import Path
a = sys.argv[1:]
if a == ['--version']:
    print('@VERSION@')
elif a == ['sandbox', '--help']:
    sys.stdout.write(@HELP@)
elif a[0] == 'sandbox':
    # Measured on 2026-10-02 at 11:26; version and provenance are in the fixture.
    measurement = @EXECUTION@
    fs = tomllib.loads(a[a.index('-c') + 1])['permissions']['dao-implementer']['filesystem']
    assert fs[':minimal'] == 'read'
    assert fs[os.environ['TMPDIR']] == 'write'
    assert not {'DAO_DECOY_TOKEN', 'OPENAI_API_KEY'} & os.environ.keys()
    assert a[a.index('-P') + 1] == 'dao-implementer'
    c = a[a.index('--') + 1:]
    for control in measurement['exit_codes']:
        if c == control['argv']:
            sys.exit(control['returncode'])
    for control in (measurement['missing_tool'], measurement['separate_streams']):
        if c == control['argv']:
            sys.stdout.write(control['stdout'])
            sys.stderr.write(control['stderr'])
            sys.exit(control['returncode'])
    if c == ['sh', '-c', 'printf dao-sandbox-ready']:
        print('dao-sandbox-ready', end='')
    elif c[:2] == ['sh', '-c'] and c[-2] == 'dao-tool-probe':
        if c[-1] == 'dao-missing':
            sys.exit(127)
        print(Path(os.environ['PATH'].split(':')[0]) / c[-1])
        print('v22.0.0')
    elif c == ['sh', '-c', 'ls -A "$HOME"']:
        print('.nvm')
    elif c[:2] == ['sh', '-c'] and c[2].startswith('mkdir '):
        pass
    elif c == ['sh', '-c', 'test ! -w .git']:
        pass
    elif c[:2] == ['sh', '-c'] and c[-2] == 'dao-link-probe':
        sys.exit(0 if any(Path(c[-1]).is_relative_to(Path(root)) for root in fs if root.startswith('/') and fs[root] == 'read') else 1)
    else:
        print('optional stdout')
        print('optional stderr', file=sys.stderr)
        sys.exit(0 if c[0] == 'dao-success' else 7)
else:
    raise AssertionError(a)
""".replace("@VERSION@", FAKE_VERSION).replace("@HELP@", repr(MEASURED_HELP))
    .replace("@EXECUTION@", repr(MEASURED_EXECUTION)))
    executable(entry.parents[1] / "vendor/fake/bin/codex", "raise AssertionError('never launch native fake')\n")  # allowlist:provider -- transport: marked fake native payload
    monkeypatch.setenv("PATH", str(tools / "bin") + ":/usr/bin:/bin")
    monkeypatch.setenv("DAO_DECOY_TOKEN", "must-not-inherit")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-inherit")
    for key in list(os.environ):
        if key.startswith("RUN_TASK_"):
            monkeypatch.delenv(key)
    def config(provider="codex", roots=()):  # allowlist:provider -- profile configuration: fake implementer selection
        (repo / "orchestrator.toml").write_text(
            '[validation]\ndefault_command = ["dao-test"]\n'
            '[agent_profiles.implementation]\n'
            f'provider = "{provider}"\nbinary = {json.dumps(str(entry))}\n'
            f'model = "{"opus" if provider == "claude" else "sol"}"\n'  # allowlist:provider -- profile configuration: valid models
            f'[agent_profiles.implementation.provider_options.{provider}]\n'
            f'toolchain_read_roots = {json.dumps([str(root) for root in roots])}\n')
    config()
    scratches = []
    original = probe.create_private_scratch
    def scratch():
        path = original()
        scratches.append(path)
        return path
    monkeypatch.setattr(probe, "create_private_scratch", scratch)
    yield SimpleNamespace(repo=repo, entry=entry, tools=tools, config=config, scratches=scratches)
    assert all(not path.exists() for path in scratches)


def invoke(capsys, *args):
    code = probe.main(["--json", *args])
    return code, json.loads(capsys.readouterr().out)


def placeholder_fake(project, *, stop=None, interrupt=None, unexpected=""):
    """Inject measured placeholders; stop=None deliberately leaves them as a fault.

    TERM cleanup and KILL leftovers were measured at 11:41 on 2026-10-02,
    with the fixture-bound CLI version; fixture data records the provenance.
    The delays, parent interruption and injected contents are test controls.
    """
    source = """    import signal, time
    placeholder_measurement = @PLACEHOLDERS@
    created = []
    for name in placeholder_measurement['regular_files']['paths']:
        if not os.path.lexists(name):
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                         int(placeholder_measurement['regular_files']['mode'], 8))
            os.close(fd)
            created.append(name)
    for name in placeholder_measurement['directories']['paths']:
        if not os.path.lexists(name):
            os.mkdir(name)
            created.append(name)
    def cleanup_on_term(signum, frame):
        time.sleep(0.2)
        for name in created:
            if Path(name).is_dir():
                os.rmdir(name)
            else:
                os.unlink(name)
        sys.exit(placeholder_measurement['sigterm_group']['returncode'])
    if @STOP@ == 'term':
        signal.signal(signal.SIGTERM, cleanup_on_term)
    elif @STOP@ == 'kill':
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
@UNEXPECTED@
    if c == ['sh', '-c', 'printf dao-sandbox-ready'] and @STOP@ is not None:
        if @INTERRUPT@ is not None:
            os.kill(os.getppid(), @INTERRUPT@)
        time.sleep(60)
""".replace("@PLACEHOLDERS@", repr(MEASURED_PLACEHOLDERS)).replace("@STOP@", repr(stop))
    source = source.replace("@INTERRUPT@", repr(int(interrupt) if interrupt else None)).replace("@UNEXPECTED@", unexpected)
    original = project.entry.read_text()
    anchor = "    for control in measurement['exit_codes']:"
    assert anchor in original
    project.entry.write_text(original.replace(anchor, source + anchor))


def test_placeholder_measurement_provenance_and_signature():
    measured = MEASURED_PLACEHOLDERS
    assert measured["source"]["kind"] == "operator_measurement_excerpt"
    assert measured["source"]["cli_version"] == FAKE_VERSION
    assert measured["source"]["measured_at"] == "2026-10-02 11:41"
    assert measured["regular_files"] == {
        "paths": [".gemini", ".orchestrator", "inbox", "outbox"], "size_bytes": 0, "mode": "0444"}
    assert measured["directories"] == {"paths": [".agents", ".codex"], "empty": True}  # allowlist:provider -- transport: measured placeholder locations
    assert measured["normal_exit"] == {"placeholders_removed": True, "confirmed_runs": 3}
    assert measured["sigterm_group"] == {"placeholders_removed": True, "returncode": 0}
    assert measured["sigkill_group"]["sandbox_process_remaining"] is False
    assert measured["sigkill_group"]["placeholders_removed"] is False
    assert set(measured["sigkill_group"]["remaining_paths"]) == {
        ".gemini", ".orchestrator", "inbox", "outbox", ".agents", ".codex"}  # allowlist:provider -- transport: measured placeholder locations


def assert_no_placeholders(project):
    for name in MEASURED_PLACEHOLDERS["sigkill_group"]["remaining_paths"]:
        assert not os.path.lexists(project.repo / name)


def test_remaining_placeholders_removed_and_reported(project, capsys):
    placeholder_fake(project)
    code, report = invoke(capsys)
    assert code == 0 and report["warnings"] == []
    cleanup = report["cleanup"]
    assert set(cleanup["removed"]) == set(MEASURED_PLACEHOLDERS["sigkill_group"]["remaining_paths"])
    assert set(cleanup["removed"]) <= set(cleanup["missing_before"])
    assert cleanup["retained"] == [] and cleanup["process_groups_ended"] is True
    assert any("Platzhalter entfernt:" in note for note in report["notes"])
    assert_no_placeholders(project)
    assert probe.main([]) == 0
    assert "HINWEIS: Platzhalter entfernt:" in capsys.readouterr().out
    assert_no_placeholders(project)


@pytest.mark.parametrize("json_output", [False, True])
def test_startup_error_still_removes_and_reports_placeholders(project, capsys, json_output):
    placeholder_fake(project)
    project.entry.write_text(project.entry.read_text().replace("print('dao-sandbox-ready', end='')", "sys.exit(2)"))
    assert probe.main(["--json"] if json_output else []) == 2
    out = capsys.readouterr().out
    if json_output:
        report = json.loads(out)
        assert set(report["cleanup"]["removed"]) == set(MEASURED_PLACEHOLDERS["sigkill_group"]["remaining_paths"])
        assert report["cleanup"]["process_groups_ended"] is True
    else:
        assert "Fehler:" in out and "HINWEIS: Platzhalter entfernt:" in out
    assert_no_placeholders(project)


def test_existing_protected_paths_untouched(project, capsys):
    for name in MEASURED_PLACEHOLDERS["regular_files"]["paths"]:
        (project.repo / name).touch(mode=0o444)
    for name in MEASURED_PLACEHOLDERS["directories"]["paths"]:
        (project.repo / name).mkdir()
    before = {name: (project.repo / name).lstat() for name in MEASURED_PLACEHOLDERS["sigkill_group"]["remaining_paths"]}
    placeholder_fake(project)
    code, report = invoke(capsys)
    assert code == 0 and report["cleanup"]["removed"] == []
    assert not before.keys() & set(report["cleanup"]["missing_before"])
    assert {name: (project.repo / name).lstat() for name in before} == before


@pytest.mark.parametrize("interrupt", [None, signal.SIGINT, signal.SIGTERM])
def test_timeout_and_interrupt_allow_fake_term_cleanup(project, capsys, monkeypatch, interrupt):
    placeholder_fake(project, stop="term", interrupt=interrupt)
    signals = []
    send = probe.signal_process_group
    def record_signal(identity, sig):
        signals.append(sig)
        return send(identity, sig)
    monkeypatch.setattr(probe, "signal_process_group", record_signal)
    original_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    code, report = invoke(capsys, "--timeout", "1")
    assert code == 2
    assert ("abgebrochen" in report["error"] if interrupt else "Zeitlimit" in report["error"])  # allowlist:german -- operator-facing failure context
    assert signals == [signal.SIGTERM]
    assert report["cleanup"]["removed"] == [] and report["cleanup"]["process_groups_ended"] is True
    assert original_handlers == {sig: signal.getsignal(sig) for sig in original_handlers}
    assert_no_placeholders(project)
    assert all(not scratch.exists() for scratch in project.scratches)


def test_forced_kill_removes_placeholders_after_group_exit(project, capsys, monkeypatch):
    placeholder_fake(project, stop="kill")
    monkeypatch.setattr(probe, "STOP_GRACE_SECONDS", 0.15)
    signals = []
    send = probe.signal_process_group
    def record_signal(identity, sig):
        signals.append(sig)
        return send(identity, sig)
    monkeypatch.setattr(probe, "signal_process_group", record_signal)
    code, report = invoke(capsys, "--timeout", "1")
    assert code == 2 and signals == [signal.SIGTERM, signal.SIGKILL]
    assert report["cleanup"]["process_groups_ended"] is True
    assert set(report["cleanup"]["removed"]) == set(MEASURED_PLACEHOLDERS["sigkill_group"]["remaining_paths"])
    assert_no_placeholders(project)


def test_interrupt_during_identity_capture_stops_registered_child(project, capsys, monkeypatch):
    placeholder_fake(project, stop="term")
    capture = probe.capture_process_identity
    spawn = probe.subprocess.Popen
    ready_pid = None
    def record_spawn(argv, **kwargs):
        nonlocal ready_pid
        child = spawn(argv, **kwargs)
        if "printf dao-sandbox-ready" in argv:
            ready_pid = child.pid
        return child
    def interrupt_capture(pid):
        if pid == ready_pid:
            deadline = time.monotonic() + 2
            while not (project.repo / ".agents").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert (project.repo / ".agents").exists()
            raise KeyboardInterrupt
        return capture(pid)
    monkeypatch.setattr(probe.subprocess, "Popen", record_spawn)
    monkeypatch.setattr(probe, "capture_process_identity", interrupt_capture)
    code, report = invoke(capsys)
    assert code == 2 and "abgebrochen" in report["error"]
    assert report["cleanup"]["process_groups_ended"] is True
    assert_no_placeholders(project)


def test_interrupt_during_spawn_is_deferred_until_child_registered(project, capsys, monkeypatch):
    spawn = probe.subprocess.Popen
    def interrupted_spawn(*args, **kwargs):
        child = spawn(*args, **kwargs)
        os.kill(os.getpid(), signal.SIGTERM)
        return child
    monkeypatch.setattr(probe.subprocess, "Popen", interrupted_spawn)
    code, report = invoke(capsys)
    assert code == 2 and "abgebrochen" in report["error"]
    assert report["cleanup"]["process_groups_ended"] is True
    assert all(not scratch.exists() for scratch in project.scratches)


@pytest.mark.parametrize("contents", ["file", "directory", "link", "dangling-link"])
def test_unexpected_protected_content_retained_and_warned(project, tmp_path, capsys, contents):
    target = tmp_path / "external-data"
    target.write_text("preserve outside data")
    if contents == "file":
        injected = "    Path('inbox').chmod(0o600)\n    Path('inbox').write_text('preserve data')"
        name = "inbox"
    elif contents == "directory":
        injected = "    Path('.agents/keep').write_text('preserve data')"
        name = ".agents"
    else:
        destination = target if contents == "link" else tmp_path / "absent-data"
        injected = f"    Path('inbox').unlink()\n    Path('inbox').symlink_to({str(destination)!r})"
        name = "inbox"
    placeholder_fake(project, unexpected=injected)
    code, report = invoke(capsys)
    assert code == 2 and report["cleanup"]["retained"] == [name]
    assert name not in report["cleanup"]["removed"]
    assert report["warnings"] == [f"unerwarteter Inhalt an Schutzpfad {name} nach der Prüfung; bitte selbst prüfen"]  # allowlist:german -- cleanup warning contract
    assert os.path.lexists(project.repo / name)
    assert target.read_text() == "preserve outside data"
    if contents in {"link", "dangling-link"}:
        assert (project.repo / name).is_symlink()
    elif contents == "file":
        assert (project.repo / name).read_text() == "preserve data"
    else:
        assert (project.repo / name / "keep").read_text() == "preserve data"


def test_foreign_owner_not_removed(project, capsys, monkeypatch):
    placeholder_fake(project)
    stat_original = os.stat
    def foreign_owner(name, **kwargs):
        info = stat_original(name, **kwargs)
        if name == "inbox" and kwargs.get("dir_fd") is not None:
            return SimpleNamespace(st_uid=os.getuid() + 1)
        return info
    monkeypatch.setattr(probe.os, "stat", foreign_owner)
    code, report = invoke(capsys)
    assert code == 2 and report["cleanup"]["retained"] == ["inbox"]
    assert "inbox" not in report["cleanup"]["removed"]
    assert report["warnings"] and (project.repo / "inbox").exists()


def test_uncertain_or_live_group_vetoes_cleanup(project, monkeypatch):
    path = project.repo / "inbox"
    path.touch()
    monkeypatch.setattr(probe, "group_ended", lambda group: False)
    cleanup = probe.placeholder_cleanup(None, ("inbox",), [{"drained": True}])
    assert cleanup["process_groups_ended"] is False
    assert cleanup["removed"] == [] and cleanup["warnings"] and path.exists()


def test_cleanup_never_traverses_symlink_ancestor(project, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "placeholder").touch()
    (project.repo / "alias").symlink_to(outside, target_is_directory=True)
    fd = os.open(project.repo, os.O_RDONLY | os.O_DIRECTORY)
    try:
        cleanup = probe.placeholder_cleanup(fd, ("alias/placeholder",), [])
    finally:
        os.close(fd)
    assert cleanup["removed"] == [] and cleanup["retained"] == ["alias/placeholder"]
    assert cleanup["warnings"] and (outside / "placeholder").exists()


def test_matching_system_version_and_package_root(project, capsys):
    code, report = invoke(capsys)
    assert code == 0 and report["warnings"] == []
    assert report["tools"][0]["host"] == report["tools"][0]["sandbox"]
    assert report["checks"] == {"repository_writable": True, "git_read_only": True, "home_entries": [".nvm"]}
    assert report["package_read_root"] == str(project.entry.parents[1])


def test_fake_help_matches_operator_measurement(project):
    fixture = json.loads(HELP_FIXTURE.read_text())
    assert fixture["source"]["cli_version"] == FAKE_VERSION
    assert fixture["source"]["measured_at"] == "2026-10-02 11:05"
    assert fixture["source"]["kind"] == "operator_measurement_excerpt"
    assert fixture["help_excerpt"] == (
        "  -P, --permission-profile <NAME>\n"
        "          Named permissions profile to apply from the active configuration stack\n"
        "  -C, --cd <DIR>\n"
        "          Working directory used for profile resolution and command execution\n"
    )
    rc, out, err = probe.run_command([str(project.entry), "sandbox", "--help"],
                                     repo=project.repo, env=probe.codex_process_environment(), timeout=5)  # allowlist:provider -- transport: launch marked fake only
    assert (rc, out, err) == (0, fixture["help_excerpt"], "")


@pytest.mark.parametrize("missing", ["--permission-profile", "--cd"])
def test_requires_measured_help_flags(project, capsys, missing):
    help_text = MEASURED_HELP.replace(missing, "--unsupported")
    project.entry.write_text(project.entry.read_text().replace(repr(MEASURED_HELP), repr(help_text)))
    code, report = invoke(capsys)
    assert code == 2 and "-P/--permission-profile" in report["error"]


def test_old_invented_permission_flag_is_rejected(project, capsys):
    project.entry.write_text(project.entry.read_text().replace(
        repr(MEASURED_HELP), repr("--permissions --cd")))
    code, report = invoke(capsys)
    assert code == 2 and "-P/--permission-profile" in report["error"]


def test_version_mismatch(project, capsys):
    executable(project.tools / "bin/dao-test", "print('v24.0.0')\n")
    code, report = invoke(capsys)
    assert code == 1 and any("weicht" in text for text in report["warnings"])
    assert report["notes"] == []


def test_missing_tool(project, capsys):
    code, report = invoke(capsys, "--tool", "dao-missing")
    assert code == 1 and report["tools"][0]["sandbox"]["path"] is None


def test_empty_version_is_a_warning(project, capsys):
    executable(project.tools / "bin/dao-test", "pass\n")
    code, report = invoke(capsys)
    assert code == 1 and "--version fehlgeschlagen" in report["warnings"][0]


@pytest.mark.parametrize("exit_code", [1, 2])
def test_failed_version_is_warning_even_with_equal_version_text(project, capsys, exit_code):
    executable(project.tools / "bin/dao-test", f"import sys\nprint('v22.0.0')\nsys.exit({exit_code})\n")
    project.entry.write_text(project.entry.read_text().replace(
        "print(Path(os.environ['PATH'].split(':')[0]) / c[-1])", "print('/usr/bin/' + c[-1])"))
    code, report = invoke(capsys)
    assert code == 1 and report["notes"] == []
    assert report["warnings"] == [f"dao-test: --version fehlgeschlagen (Host, Exit {exit_code})."]


def test_path_mismatch_even_with_matching_version(project, capsys):
    project.entry.write_text(project.entry.read_text().replace(
        "print(Path(os.environ['PATH'].split(':')[0]) / c[-1])", "print('/usr/bin/' + c[-1])"))
    code, report = invoke(capsys)
    assert code == 0 and report["warnings"] == []
    assert report["notes"] == ["dao-test: andere Installation, gleiche Version."]
    assert probe.main([]) == 0
    out = capsys.readouterr().out
    assert "HINWEIS: dao-test: andere Installation, gleiche Version." in out
    assert "WARNUNG:" not in out


def test_no_test_command_requires_explicit_tools(project, capsys):
    (project.repo / "orchestrator.toml").write_text("")
    code, report = invoke(capsys)
    assert code == 2 and "--tool" in report["error"]


def test_tool_probe_launcher_failure_is_error(project, capsys):
    project.entry.write_text(project.entry.read_text().replace(
        "if c[-1] == 'dao-missing':", "sys.exit(2)\n        if c[-1] == 'dao-missing':"))
    code, report = invoke(capsys)
    assert code == 2 and "Werkzeugauflösung" in report["error"]


def test_text_report_labels(project, capsys):
    assert probe.main([]) == 0
    out = capsys.readouterr().out
    for expected in ("Host:", "Sandbox:", "Repository beschreibbar: ja", ".git schreibgeschützt: ja", "Sichtbare Home-Einträge: .nvm"):
        assert expected in out


def test_text_path_is_short_but_json_retains_full_path(project, capsys, monkeypatch):
    path = os.environ["PATH"] + ":" + ":".join(f"/unused/toolchain-{i}" for i in range(30))
    monkeypatch.setenv("PATH", path)
    assert probe.main([]) == 0
    out = capsys.readouterr().out
    path_line = next(line for line in out.splitlines() if line.startswith("PATH:"))
    assert path_line == "PATH: 33 Einträge (vollständig mit --json)"
    code, report = invoke(capsys)
    assert code == 0 and report["path"] == path


@pytest.mark.parametrize("granted", [False, True])
def test_external_dependency_symlink(project, tmp_path, capsys, granted):
    target = tmp_path / "dependencies"
    target.mkdir()
    (project.repo / "node_modules").symlink_to(target, target_is_directory=True)
    project.config(roots=(target,) if granted else ())
    code, report = invoke(capsys)
    assert code == (0 if granted else 1)
    assert report["dependency_links"] == [{"path": "node_modules", "target": str(target),
                                           "read_root_granted": granted, "visible": granted}]


def test_claude_is_labelled_approximation(project, capsys):  # allowlist:provider -- transport: no real provider invocation
    project.config(provider="claude", roots=(project.tools,))  # allowlist:provider -- profile configuration: fake selection
    code, report = invoke(capsys)
    assert code == 0 and report["mode"] == "claude-resolution-approximation"  # allowlist:provider -- transport: honest approximation
    assert "keine Sandboxmessung" in report["measurement"]
    assert report["checks"]["home_entries"] is None
    code, report = invoke(capsys, "--", "dao-test")
    assert code == 2 and "nicht ausgeführt" in report["error"]


def test_claude_text_omits_absent_package_root(project, capsys):  # allowlist:provider -- transport: approximation presentation
    project.config(provider="claude", roots=(project.tools,))  # allowlist:provider -- profile configuration: fake selection
    assert probe.main([]) == 0
    out = capsys.readouterr().out
    assert "Codex-Paketwurzel" not in out and "None" not in out  # allowlist:provider -- transport: irrelevant read grant omitted
    assert "Näherung:" in out


def test_review_profile_rejected(project, capsys):
    code, report = invoke(capsys, "--profile", "review")
    assert code == 2 and "Reviewprofile" in report["error"]
    assert project.scratches == []


def test_unknown_configuration(project, capsys):
    (project.repo / "orchestrator.toml").write_text('unknown = true\n')
    code, report = invoke(capsys)
    assert code == 2 and "Unknown key" in report["error"]


@pytest.mark.parametrize("failure", ["missing", "old", "help", "sandbox", "timeout"])
def test_provider_errors_remove_scratch(project, capsys, failure):
    source = project.entry.read_text()
    if failure == "missing":
        project.entry.unlink()
    elif failure == "old":
        project.entry.write_text(source.replace("0.159.2", "0.100.0"))
    elif failure == "help":
        project.entry.write_text(source.replace(f"sys.stdout.write({MEASURED_HELP!r})", "sys.exit(2)"))
    elif failure == "sandbox":
        project.entry.write_text(source.replace("print('dao-sandbox-ready', end='')", "sys.exit(2)"))
    else:
        project.entry.write_text(source.replace("print('dao-sandbox-ready', end='')", "import time; time.sleep(5)"))
    code, report = invoke(capsys, "--timeout", "1")
    assert code == 2 and report["error"]
    assert project.scratches and all(not path.exists() for path in project.scratches)


@pytest.mark.parametrize("case", ["tool", "command"])
@pytest.mark.parametrize("json_output", [False, True])
def test_timeout_has_short_context_and_json_details(project, capsys, case, json_output):
    source = project.entry.read_text()
    if case == "tool":
        project.entry.write_text(source.replace("if c[-1] == 'dao-missing':",
            "import time; time.sleep(5)\n        if c[-1] == 'dao-missing':"))
        args = ["--tool", "dao-test"]
        context = "Werkzeugprüfung `dao-test`"  # allowlist:german -- assert the operator-facing diagnostic
    else:
        project.entry.write_text(source.replace("print('optional stdout')",
            "import time; time.sleep(5)\n        print('optional stdout')"))
        args = ["--", "sh", "-c", "exec sleep 40"]
        context = "Zusatzbefehl `sh -c …`"
    code = probe.main([*(["--json"] if json_output else []), "--timeout", "1", *args])
    assert code == 2
    out = capsys.readouterr().out
    message = f"Zeitlimit von 1 s überschritten bei: {context}"  # allowlist:german -- assert the operator-facing diagnostic
    if json_output:
        report = json.loads(out)
        assert report["error"] == message
        details = report["error_details"]
        assert details["context"] == context and details["timeout_seconds"] == 1
        assert any(arg.startswith("permissions.dao-implementer=") for arg in details["command"])
        if case == "command":
            assert details["command"][-3:] == ["sh", "-c", "exec sleep 40"]
    else:
        assert out == "Fehler: " + message + "\n"
        assert "permissions." not in out and str(project.entry) not in out
    assert all(not path.exists() for path in project.scratches)


@pytest.mark.parametrize("exit_code", [0, 7])
def test_optional_command_output_and_exit(project, capsys, exit_code):
    # Exit 7 forwarding and stream separation were measured on 2026-10-02,
    # 11:26, with the fixture-bound CLI version; successful exit 0 at 11:05.
    command = "dao-success" if exit_code == 0 else "dao-test"
    code, report = invoke(capsys, "--", command, "argument with spaces")
    assert code == (1 if exit_code else 0)
    assert report["command"]["argv"] == [command, "argument with spaces"]
    assert report["command"]["exit_code"] == exit_code
    assert report["command"]["stdout"] == "optional stdout\n"
    assert report["command"]["stderr"] == "optional stderr\n"
    assert "darf im Repository schreiben" in report["command"]["notice"]


@pytest.mark.parametrize("message, detected", [
    ("spawnSync /usr/bin/node EPERM", True),
    ("Operation not permitted", True),
    ("test failed", False),
    ("AssertionError: expected 2, got 3", False),
    ("EPERMISSION", False),
])
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@pytest.mark.parametrize("exit_code", [0, 7])
def test_optional_command_sandbox_boundary_hint(project, capsys, message, detected, stream, exit_code):
    replacement = f"print({message!r}" + (", file=sys.stderr)" if stream == "stderr" else ")")
    source = project.entry.read_text().replace(
        "print('optional stdout')" if stream == "stdout" else "print('optional stderr', file=sys.stderr)",
        replacement)
    project.entry.write_text(source)
    command = "dao-success" if exit_code == 0 else "dao-test"
    code, report = invoke(capsys, "--", command)
    assert code == (1 if exit_code else 0)
    assert report["command"]["sandbox_boundary_hint"] == (
        "mögliche Sandbox-Grenze, siehe 2.3" if detected else None)
    assert message in report["command"][stream]
    assert report["warnings"] == (["Zusatzbefehl: Exit 7."] if exit_code else [])
    rendered = probe.render(report)
    assert ("HINWEIS: mögliche Sandbox-Grenze, siehe 2.3" in rendered) == detected


def test_successful_optional_command_notice_is_not_warning(project, capsys):
    assert probe.main(["--", "dao-success"]) == 0
    output = capsys.readouterr()
    assert output.err.startswith("HINWEIS: Der Zusatzbefehl darf im Repository schreiben")
    assert "HINWEIS: Der Zusatzbefehl darf im Repository schreiben" in output.out
    assert "WARNUNG:" not in output.out + output.err


def test_execution_fixture_records_operator_provenance():
    source = MEASURED_EXECUTION["source"]
    assert source["kind"] == "operator_measurement_excerpt"
    assert source["cli_version"] == FAKE_VERSION
    assert source["measured_at"] == "2026-10-02 11:26"
    assert [row["returncode"] for row in MEASURED_EXECUTION["exit_codes"]] == [1, 2, 7, 127]
    assert MEASURED_EXECUTION["separate_streams"] == {
        "argv": ["sh", "-c", "echo OUT; echo ERR >&2; exit 3"],
        "returncode": 3, "stdout": "OUT\n", "stderr": "ERR\n", "additional_cli_output": False}
    assert MEASURED_EXECUTION["missing_tool"]["returncode"] == 127
    assert MEASURED_EXECUTION["missing_tool"]["stdout"] == ""
    assert MEASURED_EXECUTION["timeout"]["scratch_removed"] is True
    assert MEASURED_EXECUTION["timeout"]["sleep_process_remaining"] is False


@pytest.mark.parametrize("control", MEASURED_EXECUTION["exit_codes"])
def test_measured_nonzero_exit_codes_are_preserved(project, capsys, control):
    code, report = invoke(capsys, "--", *control["argv"])
    assert code == 1 and report["command"]["exit_code"] == control["returncode"]


@pytest.mark.parametrize("control", [MEASURED_EXECUTION["separate_streams"], MEASURED_EXECUTION["missing_tool"]])
def test_measured_streams_and_missing_command(project, capsys, control):
    code, report = invoke(capsys, "--", *control["argv"])
    assert code == 1
    result = report["command"]
    for name in ("stdout", "stderr"):
        assert result[name] == control[name]
    assert result["exit_code"] == control["returncode"]


def test_default_tools_include_rules_and_node(project):
    (project.repo / "orchestrator.toml").write_text('''[validation]
default_command = ["npm", "test"]
[[validation.rules]]
patterns = ["src/**"]
command = ["python3", "-m", "pytest"]
''')
    assert probe.default_tools(probe.load_repo_config(project.repo / "orchestrator.toml"), project.repo) == ["npm", "python3", "node"]


def test_generated_directories_are_not_traversed(project, tmp_path, monkeypatch, capsys):
    target = tmp_path / "dependencies"
    target.mkdir()
    for relative in ("node_modules", "app/.venv", "src-tauri/target/deep/node_modules", "dist/deep/.venv", "cache/deep/.venv"):
        path = project.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target, target_is_directory=True)
    with (project.repo / "orchestrator.toml").open("a") as stream:
        stream.write('[paths]\ngenerated = ["src-tauri/target/**", "dist/**", "cache", "node_modules/**", "app/.venv/**"]\n')
    visited = []
    walk = os.walk
    def guarded_walk(root, **kwargs):
        for directory, dirs, files in walk(root, **kwargs):
            relative = Path(directory).relative_to(root).as_posix()
            visited.append(relative)
            assert relative not in {"src-tauri/target", "dist", "cache"}
            yield directory, dirs, files
    monkeypatch.setattr(probe.os, "walk", guarded_walk)
    code, report = invoke(capsys)
    assert code == 1
    assert {link["path"] for link in report["dependency_links"]} == {"node_modules", "app/.venv"}
    assert set(visited) == {".", "app", "src-tauri"}


def test_production_permissions_do_not_drift(tmp_path):
    adapter, paths, bundle, result = prepared(tmp_path)
    try:
        config = probe.codex_implementer_permission_config(  # allowlist:provider -- transport: compare with real adapter builder using marked fake
            probe.codex_package_root(adapter.provider_identity.entry_path),  # allowlist:provider -- transport: production package helper
            paths["repo"], adapter._scratch, adapter._protected_paths,
            adapter.settings.toolchain_read_roots, execution_root=paths["repo"])
        assert config == next(arg for arg in result.command if arg.startswith("permissions.dao-implementer="))
        assert probe.codex_process_environment()["PATH"] == adapter.env["PATH"]  # allowlist:provider -- transport: environment parity
        assert probe.protected_implementer_paths(paths["repo"], paths["repo"] / "inbox", paths["repo"] / "outbox", "boundary-probe") == adapter._protected_paths
    finally:
        adapter.cleanup()


def test_claude_environment_is_shared_with_adapter():  # allowlist:provider -- transport: fixed environment parity
    assert probe.implementer_process_environment is implementer.implementer_process_environment
    import inspect
    assert "self.env = implementer_process_environment(" in inspect.getsource(implementer.NativeClaudeImplementerAdapter.prepare_native_provider_input)  # allowlist:provider -- transport: adapter reuse guard
