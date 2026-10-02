#!/usr/bin/env python3
"""Model-free project toolchain diagnostics; no orchestrator run or records."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import re
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_config import codex_process_environment, resolve_agent_settings  # allowlist:provider -- transport: production environment reuse
from agent_roles import AgentSlot
from claude_implementer_adapter import (  # allowlist:provider -- transport: production implementer boundary reuse
    implementer_process_environment, implementer_settings, protected_implementer_paths,
)
from cli import detect_test_command, load_repo_config
from gates import classify_path, matches_path_patterns
from path_policy import PathClass
from codex_review_adapter import codex_package_root  # allowlist:provider -- transport: production package resolution reuse
from native_provider_schema import (  # allowlist:provider -- transport: production permission and version reuse
    codex_implementer_permission_config, compatible_cli_version,  # allowlist:provider -- transport: production permission helper
)
from provider_identity import capture_provider_identity, executable_candidates
from provider_process import capture_process_identity, signal_process_group
from protected_tree import outermost_protected_paths, missing_protected_paths, cleanup_sandbox_placeholders, process_group_ended
from toolchain_paths import create_private_scratch, validate_private_scratch


COMMAND_NOTICE = ("Der Zusatzbefehl darf im Repository schreiben, wie der Implementer. "
                   "Verwenden Sie gezielte, nicht verändernde Befehle.")
SANDBOX_BOUNDARY_HINT = "mögliche Sandbox-Grenze, siehe 2.3"
STOP_GRACE_SECONDS = 5.0
STOP_DRAIN_SECONDS = 2.0


@contextmanager
def interrupt_policy(handler):
    """Restore caller handlers; cleanup defers further INT/TERM interruptions."""
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, handler)
    try:
        yield
    finally:
        for sig, original in previous.items():
            signal.signal(sig, original)


def interrupted(signum, frame):
    raise KeyboardInterrupt


def group_ended(group):
    return process_group_ended(group["pid"], group["identity"])


def signal_command_group(child, group, sig):
    if group["identity"] is not None:
        signal_process_group(group["identity"], sig)
    elif child.poll() is None:
        try:
            if os.getsid(child.pid) == child.pid and os.getpgid(child.pid) == child.pid:
                os.killpg(child.pid, sig)
        except ProcessLookupError:
            pass


def stop_command(child, group):
    with interrupt_policy(signal.SIG_IGN):
        signal_command_group(child, group, signal.SIGTERM)
        deadline = time.monotonic() + STOP_GRACE_SECONDS
        while time.monotonic() < deadline:
            child.poll()
            if group_ended(group):
                break
            time.sleep(0.05)
        if not group_ended(group):
            signal_command_group(child, group, signal.SIGKILL)
        child.communicate(timeout=STOP_DRAIN_SECONDS)
        group["drained"] = True
        deadline = time.monotonic() + STOP_DRAIN_SECONDS
        while not group_ended(group) and time.monotonic() < deadline:
            time.sleep(0.05)
        if not group_ended(group):
            raise RuntimeError("Prozessgruppe nicht sicher beendet; keine Platzhalterbereinigung möglich.")


def placeholder_cleanup(repo_fd, candidates, groups):
    return cleanup_sandbox_placeholders(
        repo_fd, candidates,
        process_groups_ended=all(group["drained"] and group_ended(group) for group in groups),
    )

class CheckTimeout(RuntimeError):
    """A short operator message with technical details reserved for JSON."""

    def __init__(self, timeout, context, command):
        super().__init__(f"Zeitlimit von {timeout} s überschritten bei: {context}")
        self.details = {"kind": "timeout", "timeout_seconds": timeout,
                        "context": context, "command": command}


def short_command(argv):
    summary = shlex.join(argv[:2]).replace("\n", " ").replace("\r", " ")
    if len(summary) > 80:
        return summary[:77] + "…"
    return summary + (" …" if len(argv) > 2 else "")


def run_command(argv, *, repo, env, timeout, groups=None):
    """Allow CLI cleanup on TERM, then bound KILL, drain and session exit."""
    child = None
    group = None
    stop_attempted = False
    pending_interrupts = []
    def defer_interrupt(signum, frame):
        pending_interrupts.append(signum)
    try:
        # Register the child before acting on an interrupt during Popen. Once
        # registered, every exception (including identity capture) stops it.
        with interrupt_policy(defer_interrupt):
            child = subprocess.Popen(argv, cwd=repo, env=env, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, start_new_session=True)
            group = {"pid": child.pid, "identity": None, "drained": False}
            if groups is not None:
                groups.append(group)
        if pending_interrupts:
            raise KeyboardInterrupt
        try:
            group["identity"] = capture_process_identity(child.pid)
        except (OSError, ValueError, IndexError):
            pass
        out, err = child.communicate(timeout=timeout)
        group["drained"] = True
        if not group_ended(group):
            stop_attempted = True
            stop_command(child, group)
        return child.returncode, out, err
    except BaseException:
        if child is not None and group is not None and not stop_attempted:
            stop_command(child, group)
        raise
    finally:
        with interrupt_policy(signal.SIG_IGN):
            if child is not None:
                for stream in (child.stdout, child.stderr):
                    stream.close()


def default_tools(config, repo):
    commands = [config.validation.default_command, *(rule.command for rule in config.validation.rules)]
    result = []
    for command in commands:
        words = (command.argv or tuple(shlex.split(command.shell_command or ""))) if command else ()
        if words:
            result.append(words[0])
    if not result:
        detected = shlex.split(detect_test_command(repo)) if not config.validation_declared else []
        if not detected:
            raise ValueError("Kein Testbefehl zur Werkzeugableitung vorhanden; verwenden Sie --tool.")
        result.append(detected[0])
    if any(Path(tool).name == "npm" for tool in result):
        result.append("node")
    return list(dict.fromkeys(result))


def tool_probe(tool, launch):
    # Positional argv prevents shell interpolation of operator-supplied tool names.
    rc, out, err = launch(["sh", "-c", 'p=$(command -v "$1") || exit 127; '
                          'printf "%s\\n" "$p"; "$p" --version', "dao-tool-probe", tool])
    lines = out.splitlines()
    if rc == 127 and not lines:
        return {"path": None, "version": None, "exit_code": rc, "stderr": err}
    if not lines:
        raise ValueError("Werkzeugauflösung konnte nicht ausgeführt werden: " + (err or out).strip())
    return {"path": lines[0] if lines else None,
            "version": "\n".join(lines[1:]).strip() or err.strip() or None,
            "exit_code": rc, "stderr": err}


def dependency_links(repo, classes):
    """Inspect links without traversing dependencies, control or generated roots."""
    result = []
    # A subtree glob such as dist/** classifies its contents, not dist itself.
    generated_roots = tuple(pattern[:-3] for pattern in classes.generated if pattern.endswith("/**"))
    excluded = {".git", ".orchestrator", "inbox", "outbox", ".agents", ".codex", ".claude", ".gemini"}  # allowlist:provider -- transport: credential and control directories excluded
    for directory, dirs, files in os.walk(repo, followlinks=False):
        for name in ("node_modules", ".venv"):
            path = Path(directory) / name
            if name in dirs or name in files:
                if path.is_symlink():
                    target = path.resolve()
                    if not target.is_relative_to(repo):
                        result.append((path.relative_to(repo).as_posix(), target))
        descend = []
        for name in dirs:
            if name in excluded | {"node_modules", ".venv"}:
                continue
            relative = (Path(directory) / name).relative_to(repo).as_posix()
            if (classify_path(relative, classes).path_class is PathClass.GENERATED
                or matches_path_patterns(relative, generated_roots)):
                continue
            descend.append(name)
        dirs[:] = descend
    return result


def check(args, repo, cleanup):
    config = load_repo_config(repo / "orchestrator.toml")
    selected = config.roles[AgentSlot.IMPLEMENTER]
    if args.profile is not None and args.profile != selected:
        raise ValueError("Nur das konfigurierte Implementer-Profil darf geprüft werden; Reviewprofile sind ausgeschlossen.")
    namespace = argparse.Namespace(**{f"{role}_{field}": None
                                      for role in ("implementer", "reviewer", "final_reviewer")
                                      for field in ("binary", "model", "timeout", "effort")})
    settings = resolve_agent_settings(namespace, os.environ,
                                      roles=config.roles, profiles=config.agent_profiles)["implementer"]
    if settings.name not in {"codex", "claude"}:  # allowlist:provider -- transport: supported implementer providers
        raise ValueError("Das konfigurierte Profil ist kein unterstützter Implementer.")
    measured = settings.name == "codex"  # allowlist:provider -- transport: model-free sandbox availability
    if args.command and not measured:
        raise ValueError("Claude: kein modellfreier Zugang zur gleichen Sandbox; Zusatzbefehl wird nicht ausgeführt.")  # allowlist:provider -- transport: explicit provider measurement limit
    tools = list(dict.fromkeys(args.tool or default_tools(config, repo)))
    if any(not tool or tool.startswith("-") or any(c in tool for c in "\x00\r\n") for tool in tools):
        raise ValueError("Ungültiger Werkzeugname.")
    protected = protected_implementer_paths(repo, repo / "inbox", repo / "outbox", "sandbox-precheck")
    scratch = create_private_scratch()
    groups = []
    repo_fd = None
    placeholder_candidates = ()
    context = "CLI-Identitätsprüfung"
    try:
        validate_private_scratch(scratch, repo, protected, settings.toolchain_read_roots)
        host_env = codex_process_environment()  # allowlist:provider -- transport: allowlisted parent PATH, no credential values
        host_env["TMPDIR"] = str(scratch)
        def host(argv):
            return run_command(argv, repo=repo, env=host_env, timeout=args.timeout, groups=groups)
        package = None
        if measured:
            repo_fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            placeholder_candidates = missing_protected_paths(repo, repo_fd, protected)
            candidates = executable_candidates(settings.binary, host_env["PATH"])
            if not candidates:
                raise ValueError(f"Codex fehlt: {settings.binary}")  # allowlist:provider -- transport: missing implementer CLI
            identity = capture_provider_identity(candidates[0], ("--version",), host, path=host_env["PATH"])
            if not compatible_cli_version("codex", identity.version):  # allowlist:provider -- transport: same supported version policy as startup
                raise ValueError(f"Codex ist zu alt oder inkompatibel: {identity.version}")  # allowlist:provider -- transport: unsupported implementer CLI
            package = codex_package_root(identity.entry_path)  # allowlist:provider -- transport: bound production package root
            context = "Sandbox-Hilfeprüfung"
            rc, out, err = host([*identity.launch_prefix, "sandbox", "--help"])
            if rc or not all(flag in (out + err).split() for flag in ("--permission-profile", "--cd")):
                raise ValueError("Codex kennt sandbox mit -P/--permission-profile und -C/--cd nicht: " + err.strip())  # allowlist:provider -- transport: measured sandbox capability
            permissions = codex_implementer_permission_config(  # allowlist:provider -- transport: exact production permission function
                package, repo, scratch, protected, settings.toolchain_read_roots, execution_root=repo)
            prefix = [*identity.launch_prefix, "sandbox", "-c", permissions,
                      "-P", "dao-implementer", "-C", str(repo), "--"]
            def sandbox(argv):
                return host([*prefix, *argv])
            launch = sandbox
            environment = host_env
            context = "Sandboxstart"
            rc, out, err = launch(["sh", "-c", "printf dao-sandbox-ready"])
            if rc or out != "dao-sandbox-ready":
                raise ValueError("Codex-Sandbox kann nicht gestartet werden: " + (err or out).strip())  # allowlist:provider -- transport: failed sandbox startup
        else:
            environment = implementer_process_environment(settings.toolchain_read_roots, scratch)
            # Validate the same settings and roots without starting a provider.
            implementer_settings(protected, repo, settings.toolchain_read_roots, scratch=scratch)
            def approximation(argv):
                return run_command(argv, repo=repo, env=environment, timeout=args.timeout, groups=groups)
            launch = approximation
        report = {
            "provider": settings.name, "profile": selected, "repository": str(repo),
            "mode": "codex-sandbox-approximation" if measured else "claude-resolution-approximation",  # allowlist:provider -- transport: explicit measurement limits
            "measurement": ("Codex: codex sandbox mit produktivem Rechteprofil; Näherung für exec."  # allowlist:provider -- transport: explicit measurement limits
                            if measured else "Claude: nur Auflösungsnäherung mit festem PATH; keine Sandboxmessung."),  # allowlist:provider -- transport: explicit measurement limits
            "path": environment["PATH"], "toolchain_read_roots": list(settings.toolchain_read_roots),
            "package_read_root": str(package) if package else None,
            "protected_paths": [str(p) for p in protected], "tools": [], "notes": [], "warnings": [],
            "checks": {}, "dependency_links": [], "command": None,
        }
        for tool in tools:
            context = f"Werkzeugprüfung `{tool}`"
            actual = tool_probe(tool, launch)
            outside = tool_probe(tool, host)
            row = {"tool": tool, "host": outside, "sandbox" if measured else "approximation": actual}
            report["tools"].append(row)
            for label, value in (("Host", outside), ("Sandbox" if measured else "Näherung", actual)):
                if not value["path"]:
                    report["warnings"].append(f"{tool}: fehlt ({label}).")
                elif value["exit_code"] or not value["version"]:
                    report["warnings"].append(f"{tool}: --version fehlgeschlagen ({label}, Exit {value['exit_code']}).")
            if all(value["path"] and value["version"] and value["exit_code"] == 0 for value in (outside, actual)):
                if outside["version"] != actual["version"]:
                    report["warnings"].append(f"{tool}: Version weicht vom Host ab.")
                elif outside["path"] != actual["path"]:
                    report["notes"].append(f"{tool}: andere Installation, gleiche Version.")
        if measured:
            # No credential contents: Home inspection returns entry names only.
            context = "Home-Einträge"
            rc, out, err = launch(["sh", "-c", 'ls -A "$HOME"'])
            if rc:
                raise ValueError("Home-Einträge können nicht gemessen werden: " + err.strip())
            report["checks"]["home_entries"] = out.splitlines()
            name = ".dao-sandbox-check-" + uuid.uuid4().hex
            context = "Repository-Schreibprüfung"
            rc, out, err = launch(["sh", "-c", 'mkdir "$1" && rmdir "$1"', "dao-write-probe", name])
            report["checks"]["repository_writable"] = rc == 0
            # access(W_OK) observes the kernel's read-only mount; no Git metadata is modified.
            context = ".git-Schreibschutzprüfung"
            rc, out, err = launch(["sh", "-c", 'test ! -w .git'])
            if rc not in (0, 1):
                raise ValueError("Git-Schreibschutz kann nicht gemessen werden: " + err.strip())
            report["checks"]["git_read_only"] = rc == 0
            for key in ("repository_writable", "git_read_only"):
                if not report["checks"][key]:
                    label = "Repository nicht beschreibbar" if key == "repository_writable" else ".git nicht schreibgeschützt"
                    report["warnings"].append(label + ".")
        else:
            report["checks"] = {"home_entries": None, "repository_writable": None, "git_read_only": None,
                                "note": "Nicht gemessen; konfigurierte Lesefreigaben und Schutzpfade stehen oben."}
        for relative, target in dependency_links(repo, config.paths):
            roots = [*(Path(root) for root in settings.toolchain_read_roots), *([package] if package else [])]
            granted = any(target.is_relative_to(root) for root in roots)
            visible = None
            if measured:
                context = f"Symlinkprüfung `{relative}`"
                rc, out, err = launch(["sh", "-c", 'test -d "$1"', "dao-link-probe", str(target)])
                if rc not in (0, 1):
                    raise ValueError("Symlink-Sichtbarkeit kann nicht gemessen werden: " + err.strip())
                visible = rc == 0
            report["dependency_links"].append({"path": relative, "target": str(target),
                                               "read_root_granted": granted, "visible": visible})
            if not granted or visible is False:
                report["warnings"].append(f"{relative}: externes Ziel nicht freigegeben oder nicht sichtbar: {target}")
        if args.command:
            context = f"Zusatzbefehl `{short_command(args.command)}`"
            rc, out, err = launch(args.command)
            report["command"] = {"argv": args.command, "stdout": out, "stderr": err, "exit_code": rc,
                                 "notice": COMMAND_NOTICE,
                                 "sandbox_boundary_hint": (SANDBOX_BOUNDARY_HINT if
                                     re.search(r"\bEPERM\b|Operation not permitted", out + "\n" + err)
                                     else None)}
            if rc:
                report["warnings"].append(f"Zusatzbefehl: Exit {rc}.")
        report["exit_code"] = 1 if report["warnings"] else 0
        return report
    except subprocess.TimeoutExpired as exc:
        raise CheckTimeout(args.timeout, context, exc.cmd) from None
    finally:
        with interrupt_policy(signal.SIG_IGN):
            try:
                cleanup.update(placeholder_cleanup(repo_fd, placeholder_candidates, groups))
                if cleanup["process_groups_ended"]:
                    shutil.rmtree(scratch)
                else:
                    cleanup["scratch_retained"] = str(scratch)
            finally:
                if repo_fd is not None:
                    os.close(repo_fd)


def render(report):
    if "error" in report:
        lines = ["Fehler: " + report["error"]]
        lines.extend("HINWEIS: " + note for note in report["notes"])
        lines.extend("WARNUNG: " + warning for warning in report["warnings"])
        return "\n".join(lines)
    lines = [f"Implementer: {report['profile']} ({report['provider']})", report["measurement"],
             f"PATH: {len(report['path'].split(os.pathsep))} Einträge (vollständig mit --json)",
             "Lesewurzeln: " + json.dumps(report["toolchain_read_roots"])]
    if report["package_read_root"] is not None:
        lines.append("Codex-Paketwurzel: " + report["package_read_root"])  # allowlist:provider -- transport: show bound grant only when present
    for row in report["tools"]:
        lines.append(row["tool"] + ":")
        for label in ("host", "sandbox", "approximation"):
            if label in row:
                value = row[label]
                title = {"host": "Host", "sandbox": "Sandbox", "approximation": "Näherung"}[label]
                lines.append(f"  {title}: {value['path'] or 'fehlt'} | {value['version'] or 'keine Version'}")
    checks = report["checks"]
    for title, key in (("Repository beschreibbar", "repository_writable"), (".git schreibgeschützt", "git_read_only")):
        value = checks[key]
        lines.append(f"{title}: " + ("nicht gemessen" if value is None else "ja" if value else "nein"))
    entries = checks["home_entries"]
    lines.append("Sichtbare Home-Einträge: " + ("nicht gemessen" if entries is None else ", ".join(entries) or "keine"))
    lines.append("Schreibgeschützte Pfade: " + json.dumps(report["protected_paths"], ensure_ascii=False))
    for link in report["dependency_links"]:
        lines.append(f"{link['path']} -> {link['target']} | freigegeben: {link['read_root_granted']} | sichtbar: {link['visible']}")
    lines.extend("HINWEIS: " + note for note in report["notes"])
    lines.extend("WARNUNG: " + warning for warning in report["warnings"])
    if report["command"]:
        result = report["command"]
        if result["sandbox_boundary_hint"]:
            lines.append("HINWEIS: " + result["sandbox_boundary_hint"])
        lines.extend(["HINWEIS: " + result["notice"], result["stdout"], result["stderr"], f"Zusatzbefehl Exit: {result['exit_code']}"])
    lines.append(f"Exit: {report['exit_code']}")
    return "\n".join(lines)


def build_parser():
    parser = argparse.ArgumentParser(description="Implementer-Werkzeuge im aktuellen Projekt ohne Modellaufruf vorab prüfen.")
    parser.add_argument("--tool", action="append", help="Werkzeug (mehrfach); Standard: Test- und Regelbefehle, bei npm auch node")
    parser.add_argument("--profile", help="Muss dem konfigurierten Implementer entsprechen; keine Reviewprofile")
    parser.add_argument("--json", action="store_true", help="Maschinenlesbarer Bericht")
    parser.add_argument("--timeout", type=int, default=60, help="Zeitlimit pro Prüfung/Zusatzbefehl in Sekunden (Standard: 60)")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Nach --: Sandboxbefehl. " + COMMAND_NOTICE)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    cleanup = {"missing_before": [], "removed": [], "retained": [], "warnings": [],
               "process_groups_ended": True}
    with interrupt_policy(interrupted):
        try:
            if args.timeout <= 0:
                raise ValueError("--timeout muss positiv sein.")
            if args.command:
                print("HINWEIS: " + COMMAND_NOTICE, file=sys.stderr, flush=True)
            report = check(args, Path.cwd().resolve(strict=True), cleanup)
        except CheckTimeout as exc:
            report = {"exit_code": 2, "error": str(exc), "error_details": exc.details,
                      "notes": [], "warnings": []}
        except KeyboardInterrupt:
            report = {"exit_code": 2, "error": "Prüfung abgebrochen.", "notes": [], "warnings": []}
        except (OSError, ValueError, RuntimeError, UnicodeError, subprocess.SubprocessError) as exc:
            report = {"exit_code": 2, "error": str(exc), "notes": [], "warnings": []}
        report["cleanup"] = cleanup
        if cleanup["removed"]:
            report["notes"].append("Platzhalter entfernt: " + ", ".join(cleanup["removed"]) + ".")
        report["warnings"].extend(cleanup["warnings"])
        if cleanup["warnings"]:
            report["exit_code"] = 2
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render(report))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
