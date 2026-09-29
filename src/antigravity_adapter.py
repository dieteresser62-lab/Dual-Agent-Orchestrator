"""Measured AGY print transport composed with the native reviewer role binding.

The adapter owns the measured native isolation boundary around each review.
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import fcntl
import hashlib
import logging
import stat
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from agent_adapters import (
    AgentOutputError, CapabilitySpec, _BaseAdapter,
)
from agent_config import AgentSettings, default_antigravity_home, default_antigravity_run_root
from agent_roles import AgentRoleName
from native_provider_schema import (
    NativeProviderSchemaError, assert_provider_capabilities,
    exact_cli_version_pattern, normalize_transport_profile,
)
from native_review_contract import NativeReviewContractError, canonical_native_review_json
from native_review_request import NativeReviewRequestBundle
from provider_input_budget import PreparedProviderInput, ProviderInputComponent
from reviewer_input import REVIEW_PACKET_CHUNK_CHARS, ReviewerInputError, build_reviewer_input
from role_binding import RoleBinding, binding_for_role
from workflow_state import AgentFailureKind


STDERR_CLASSIFIER_VERSION = "agy-stderr-v1"
logger = logging.getLogger(__name__)
AGY_DENY = (
    "command(*)", "write_file(*)", "mcp(*)", "read_url(*)", "execute_url(*)",
    "read_file(/tmp)", "read_file(/home)", "read_file(/root)",
    "read_file(/mnt)", "read_file(/proc)", "read_file(/run)",
)
AGY_TOOLS = ("view_file", "grep_search", "list_dir", "find_by_name", "finish")
AGY_RUN_PARENT = Path("/var/tmp")
_TIMEOUT = re.compile(r"^\[agy\] print timeout after [0-9]+[smh](?:[0-9]+[smh])* with turn in progress; returning partial output$", re.I)
_SOFT_DENIAL = re.compile(r'^jetski: no output produced — a tool required the "read_file" permission that headless mode cannot prompt for, so it was auto-denied\..*$', re.I | re.S)
_AUTH = re.compile(r"(?:authentication required|not authenticated|login required)", re.I)
_QUOTA = re.compile(r"(?:quota exceeded|rate limit exceeded|resource exhausted)(?: for this account)?", re.I)
_NETWORK = re.compile(r"(?:network error|connection refused|connection reset|dns lookup failed|name resolution failed)", re.I)
_SCHEMA = re.compile(r"(?:invalid json schema|schema validation failed|invalid schema)", re.I)
_MODEL = re.compile(r"(?:unknown model|model not found|invalid model)", re.I)
_PERMISSION = re.compile(r"(?:permission denied|auto-denied|permission request rejected)", re.I)


def _reject_duplicate(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant {value}")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def strict_json_object(text: str) -> dict[str, object]:
    try:
        value = json.loads(text, object_pairs_hook=_reject_duplicate, parse_constant=_reject_constant, parse_float=_finite_float)
    except (ValueError, TypeError) as exc:
        raise AgentOutputError("antigravity stdout is not one strict JSON object", kind_hint=AgentFailureKind.OUTPUT) from exc
    if not isinstance(value, dict):
        raise AgentOutputError("antigravity stdout must be an object", kind_hint=AgentFailureKind.OUTPUT)
    return value


def _same_json_type(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_json_type(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_json_type(a, b) for a, b in zip(left, right, strict=True))
    return left == right


def classify_agy_stderr(stderr: str) -> tuple[AgentFailureKind | None, str]:
    """Classify only versioned diagnostics; never return untrusted stderr text."""
    if not stderr.strip():
        return None, "none"
    if _TIMEOUT.fullmatch(stderr.strip()):
        return AgentFailureKind.TIMEOUT, "print-timeout"
    if _SOFT_DENIAL.fullmatch(stderr.strip()):
        return AgentFailureKind.PERMISSION, "soft-read-denial"
    for pattern, kind, code in (
        (_AUTH, AgentFailureKind.AUTH, "authentication"),
        (_QUOTA, AgentFailureKind.QUOTA, "quota"),
        (_NETWORK, AgentFailureKind.NETWORK, "network"),
        (_SCHEMA, AgentFailureKind.OUTPUT, "schema"),
        (_MODEL, AgentFailureKind.PROCESS, "model"),
        (_PERMISSION, AgentFailureKind.PERMISSION, "permission"),
    ):
        if pattern.fullmatch(stderr.strip()):
            return kind, code
    if stderr.lstrip().startswith("AGY_ERROR:"):
        return AgentFailureKind.PROCESS, "agy-error"
    return AgentFailureKind.PROCESS, "unknown-stderr"


@dataclass(frozen=True)
class AntigravityWorkspace:
    """Prepared, isolated paths owned and sealed by the Slice-4 boundary."""

    container: Path
    repo: Path
    input_dir: Path
    home: Path
    agent_file: Path
    settings_file: Path
    log_file: Path

    def __post_init__(self) -> None:
        for path in (self.container, self.repo, self.input_dir, self.home):
            if not path.is_dir() or path.is_symlink():
                raise ValueError("antigravity workspace has an invalid directory")
        if self.input_dir != self.container / "input" or self.repo != self.container / "repo":
            raise ValueError("antigravity repo and input must be directly inside the container")
        for path in (self.agent_file, self.settings_file):
            if not path.is_file() or path.is_symlink():
                raise ValueError("antigravity workspace lacks a regular agent or settings file")
        if self.log_file.is_symlink() or not self.log_file.parent.is_dir():
            raise ValueError("antigravity log path is invalid")


def _regular_private_token(home: Path) -> bool:
    token = home / ".gemini/antigravity-cli/antigravity-oauth-token"
    cursor = home
    for part in (".gemini", "antigravity-cli"):
        cursor = cursor / part
        if cursor.is_symlink() or not cursor.is_dir():
            return False
    try:
        token_stat = token.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(token_stat.st_mode)
        and stat.S_IMODE(token_stat.st_mode) == 0o600
        and token_stat.st_uid == os.getuid()
    )


def _check_no_symlink_ancestors(path: Path) -> None:
    cursor = path
    while cursor != cursor.parent:
        if cursor.is_symlink():
            raise AgentOutputError("antigravity isolation path traverses a symlink", kind_hint=AgentFailureKind.PERMISSION)
        cursor = cursor.parent


def _write_private(path: Path, data: bytes) -> None:
    _check_no_symlink_ancestors(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".dao-reviewer-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        os.close(descriptor)
        Path(temporary).unlink(missing_ok=True)


def _tree_fingerprint(root: Path) -> tuple[tuple[object, ...], ...]:
    records = []
    for path in (root, *sorted(root.rglob("*"))):
        metadata = path.lstat()
        mode = metadata.st_mode
        kind = "link" if stat.S_ISLNK(mode) else "dir" if stat.S_ISDIR(mode) else "file" if stat.S_ISREG(mode) else "other"
        if kind == "file":
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        elif kind == "link":
            digest = os.readlink(path)
        else:
            digest = ""
        records.append((
            path.relative_to(root).as_posix(), kind, stat.S_IMODE(mode),
            metadata.st_dev, metadata.st_ino, metadata.st_nlink, digest,
        ))
    return tuple(records)


def _same_semantic_json(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _same_semantic_json(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_semantic_json(a, b) for a, b in zip(left, right, strict=True)
        )
    return left == right


def _agent_markdown(policy: str) -> bytes:
    tools = "".join(f"  - {tool}\n" for tool in AGY_TOOLS)
    return (
        "---\nname: dao-reviewer\n"
        "description: Read-only reviewer for Dual-Agent-Orchestrator reviews. Reads the provided files and returns "
        "the schema-bound result.\n"
        f"tools:\n{tools}mainAgent: true\nsubagent: false\ninheritCustomizations: false\ninheritMcp: false\n"
        f'commandExecutionPolicy: "off"\n---\n# System Prompt\n{policy}\n'
    ).encode("utf-8")


def _expected_settings(base: Path) -> dict[str, object]:
    return {
        "enableTerminalSandbox": True,
        "permissions": {"allow": [f"read_file({base})"], "deny": list(AGY_DENY)},
        "showFeedbackSurvey": False, "showTips": False,
        "toolPermission": "request-review",
    }


def _check_post_run(workspace: AntigravityWorkspace, base: Path, sealed: tuple, agent_bytes: bytes) -> None:
    try:
        if _tree_fingerprint(workspace.container) != sealed or set(base.iterdir()) != {workspace.container}:
            raise ValueError("review evidence or container changed")
        if workspace.settings_file.is_symlink() or not workspace.settings_file.is_file():
            raise ValueError("settings file changed type")
        settings = json.loads(workspace.settings_file.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate)
        expected = _expected_settings(base)
        # AGY removes this documented default when it rewrites settings.json.
        if isinstance(settings, dict) and "toolPermission" not in settings:
            settings["toolPermission"] = "request-review"
        if not _same_semantic_json(settings, expected) or "trustedWorkspaces" in settings:
            raise ValueError("settings changed")
        if workspace.agent_file.is_symlink() or workspace.agent_file.read_bytes() != agent_bytes:
            raise ValueError("agent changed")
        expected_line = (
            f"CLI settings initialized: permissions=&{{Allow:[read_file({base})] "
            f"Deny:[{' '.join(AGY_DENY)}] Ask:[]}}, toolPermission=request-review"
        )
        if workspace.log_file.is_symlink() or not workspace.log_file.is_file():
            raise ValueError("runtime log missing")
        with workspace.log_file.open(encoding="utf-8", errors="replace") as stream:
            lines = [line.rstrip("\r\n") for line in stream if "CLI settings initialized:" in line]
        if len(lines) != 1 or lines[0].split("CLI settings initialized: ", 1)[-1] != expected_line.removeprefix("CLI settings initialized: "):
            raise ValueError("effective settings log differs")
    except (OSError, ValueError, TypeError) as exc:
        # The log may contain the account email; never include its text or JSON values.
        raise AgentOutputError("antigravity isolation postcheck failed", kind_hint=AgentFailureKind.PERMISSION) from None


class AntigravityTransport:
    """Provider-only transport facts; no role policy or contract is embedded here."""

    @staticmethod
    def command(settings: AgentSettings, workspace: AntigravityWorkspace, directive: str, schema: Path) -> list[str]:
        return [
            settings.binary, "-p", directive, "--output-format", "json",
            "--json-schema", str(schema), "--print-timeout",
            f"{settings.timeout_seconds or 0}s", "--disable-slash-commands",
            "--sandbox", "--model", settings.model, "--effort", settings.effort,
            "--log-file", str(workspace.log_file), "--agent", "dao-reviewer",
        ]

    @staticmethod
    def envelope(stdout: str, stderr: str, exit_code: int, writer_json: str) -> tuple[dict[str, object], dict[str, int]]:
        stderr_kind, stderr_code = classify_agy_stderr(stderr)
        if stderr_kind is not None:
            raise AgentOutputError(
                f"antigravity {stderr_code} ({STDERR_CLASSIFIER_VERSION})",
                exit_code=exit_code, kind_hint=stderr_kind,
            )
        if exit_code != 0 and not stdout.strip():
            raise AgentOutputError(
                f"antigravity {AntigravityTransport.exit_diagnostic(exit_code)} ({STDERR_CLASSIFIER_VERSION})",
                exit_code=exit_code, kind_hint=AgentFailureKind.PROCESS,
            )
        envelope = strict_json_object(stdout)
        usage = envelope.get("usage")
        clean_usage = {
            key: value for key, value in usage.items()
            if key in {"input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens"}
            and type(value) is int and value >= 0
        } if isinstance(usage, dict) else {}

        def fail(code: str, kind: AgentFailureKind) -> None:
            raise AgentOutputError(
                f"antigravity {code} ({STDERR_CLASSIFIER_VERSION})",
                technical_text=f"antigravity {code} ({STDERR_CLASSIFIER_VERSION})",
                exit_code=exit_code, kind_hint=kind,
            )

        if exit_code != 0:
            fail(AntigravityTransport.exit_diagnostic(exit_code), AgentFailureKind.PROCESS)
        if envelope.get("status") != "SUCCESS":
            fail("unsuccessful-status", AgentFailureKind.OUTPUT)
        if envelope.get("error") not in (None, ""):
            fail("envelope-error", AgentFailureKind.OUTPUT)
        if envelope.get("denied_actions") not in (None, []):
            fail("denied-actions", AgentFailureKind.PERMISSION)
        if not isinstance(envelope.get("structured_output"), dict):
            fail("missing-structured-output", AgentFailureKind.OUTPUT)
        writer = strict_json_object(writer_json)
        if not _same_json_type(envelope.get("json_schema"), writer):
            fail("schema-echo-mismatch", AgentFailureKind.OUTPUT)
        return envelope, clean_usage

    @staticmethod
    def exit_diagnostic(exit_code: int) -> str:
        return {1: "configuration-exit", 3: "model-api-exit"}.get(exit_code, "nonzero-exit")


class NativeAntigravityReviewAdapter(_BaseAdapter):
    reviewer = True
    required_hosts: tuple[str, ...] = ()
    inherit_process_environment = False
    environment_passthrough = ("WSL_INTEROP", "WSL_DISTRO_NAME", "WSLENV")
    stdin_closed_when_unused = True
    suppress_live_stream = True
    requires_attempt_ledger = True
    sanitize_reviewer_environment = False
    set_pwd = False
    egress_disclosure = (
        "Google", ("repository snapshot", "native review request", "evidence",
                   "writer schema", "reviewer policy")
    )
    capability = CapabilitySpec(
        version_args=("--version",), help_args=("--help",),
        supported_version_patterns=(exact_cli_version_pattern("antigravity"),),
        required_help_flags=(
            "-p", "--output-format", "--json-schema", "--print-timeout",
            "--disable-slash-commands", "--sandbox", "--model", "--effort",
            "--log-file", "--agent",
        ),
    )

    def __init__(
        self, settings: AgentSettings, *, role_binding: RoleBinding | None = None,
        isolated_home: Path | None = None, run_root: Path | None = None,
    ) -> None:
        if settings.name != "antigravity" or settings.effort not in {"low", "medium", "high", "max"}:
            raise ValueError("antigravity requires an explicit measured model and effort")
        super().__init__(settings)
        self.role_binding = role_binding or binding_for_role(AgentRoleName.REVIEWER)
        if self.role_binding.role is not AgentRoleName.REVIEWER:
            raise TypeError("antigravity has no implementer binding")
        self.prepared_workspace: AntigravityWorkspace | None = None
        self._writer_json: str | None = None
        self._request_id: str | None = None
        self.isolated_home = Path(isolated_home or settings.antigravity_home or default_antigravity_home()).expanduser().absolute()
        self.run_root = Path(run_root or settings.antigravity_run_root or default_antigravity_run_root()).expanduser().absolute()
        self._sealed: tuple | None = None
        self._process_started = False
        self._base: Path | None = None

    def _isolated_environment(self, *, include_wsl: bool = False) -> dict[str, str]:
        env = {
            "HOME": str(self.isolated_home), "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8", "TERM": "dumb", "AGY_CLI_DISABLE_AUTO_UPDATE": "true",
        }
        if include_wsl:
            for key in self.environment_passthrough:
                if key in os.environ:
                    env[key] = os.environ[key]
        return env

    def run_capability_command(self, command: list[str]) -> tuple[int, str, str]:
        """Inspect the binary without loading personal or repository configuration."""
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=20, check=False,
                env=self._isolated_environment(include_wsl=True), cwd="/",
            )
        except (OSError, subprocess.TimeoutExpired):
            return 1, "", "isolated capability probe failed"
        if result.returncode:
            return result.returncode, "", "isolated capability probe failed"
        return result.returncode, result.stdout or "", result.stderr or ""

    @contextmanager
    def review_execution_boundary(
        self, repo_root: Path, manifest_paths: tuple[str, ...] | None,
    ) -> Iterator[None]:
        """Hold the home lock from fresh configuration through post-run verification."""
        from agent_runtime import ReviewerWorkspace, create_read_only_reviewer_workspace

        home = self.isolated_home
        root = self.run_root
        _check_no_symlink_ancestors(home)
        _check_no_symlink_ancestors(root)
        home = home.resolve(strict=False)
        root = root.resolve(strict=False)
        source = repo_root.resolve()
        if (home == Path.home().resolve() or home.is_relative_to(Path.home().resolve() / ".gemini")
            or home.is_relative_to(source) or source.is_relative_to(home)):
            raise AgentOutputError("antigravity HOME must be isolated outside the reviewed repository", kind_hint=AgentFailureKind.PERMISSION)
        if not root.is_relative_to(AGY_RUN_PARENT) or root == AGY_RUN_PARENT:
            raise AgentOutputError("antigravity run root must be below /var/tmp", kind_hint=AgentFailureKind.PERMISSION)
        if (root.is_relative_to(source) or source.is_relative_to(root)
            or root.is_relative_to(home) or home.is_relative_to(root)):
            raise AgentOutputError("antigravity run root overlaps HOME or the reviewed repository", kind_hint=AgentFailureKind.PERMISSION)
        if not home.is_dir() or not _regular_private_token(home):
            login = (
                f"env -i HOME={shlex.quote(str(home))} PATH=/usr/local/bin:/usr/bin:/bin "
                f"LANG=C.UTF-8 TERM=xterm-256color AGY_CLI_DISABLE_AUTO_UPDATE=true {shlex.quote(self.settings.binary)}"
            )
            raise AgentOutputError(
                f"antigravity isolated login missing or token is not a regular 0600 file; run once interactively: {login}",
                kind_hint=AgentFailureKind.AUTH,
            )
        lock_path = home / ".dao-reviewer.lock"
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        acquired = False
        deadline = time.monotonic() + 15
        try:
            while time.monotonic() < deadline:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    time.sleep(0.1)
            if not acquired:
                raise AgentOutputError("antigravity isolated HOME is busy", kind_hint=AgentFailureKind.PERMISSION)
            if not _regular_private_token(home):
                raise AgentOutputError("antigravity isolated login changed before start", kind_hint=AgentFailureKind.AUTH)
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if root.is_symlink() or not root.is_dir() or root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
                raise AgentOutputError("antigravity run root is not private", kind_hint=AgentFailureKind.PERMISSION)
            base = Path(tempfile.mkdtemp(prefix="dao-agy-run-", dir=root))
            self._base = base
            snapshot = None
            log_file = root / f"{base.name}.log"
            try:
                snapshot = create_read_only_reviewer_workspace(source, manifest_paths, base_dir=base)
                container = snapshot.container
                container.chmod(0o700)
                input_dir = container / "input"
                input_dir.mkdir(mode=0o700)
                settings_dir = home / ".gemini" / "antigravity-cli"
                agent_dir = home / ".gemini" / "config" / "agents" / "dao-reviewer"
                _check_no_symlink_ancestors(settings_dir)
                _check_no_symlink_ancestors(agent_dir)
                settings_dir.mkdir(parents=True, exist_ok=True)
                agent_dir.mkdir(parents=True, exist_ok=True)
                settings_file = settings_dir / "settings.json"
                agent_file = agent_dir / "agent.md"
                agent_bytes = _agent_markdown(self.role_binding.policy)
                settings_bytes = (json.dumps(_expected_settings(base), indent=2, sort_keys=True) + "\n").encode()
                _write_private(settings_file, settings_bytes)
                _write_private(agent_file, agent_bytes)
                _write_private(log_file, b"")
                workspace = AntigravityWorkspace(container, snapshot.root, input_dir, home, agent_file, settings_file, log_file)
                self.bind_prepared_workspace(workspace)
                self._sealed = None
                self._process_started = False
                try:
                    yield
                finally:
                    if self._process_started:
                        if self._sealed is None:
                            raise AgentOutputError("antigravity input was not sealed", kind_hint=AgentFailureKind.PERMISSION)
                        _check_post_run(workspace, base, self._sealed, agent_bytes)
            finally:
                self.prepared_workspace = None
                self._sealed = None
                self._base = None
                cleanup_started = time.monotonic()
                if snapshot is not None:
                    snapshot.cleanup()
                ReviewerWorkspace(root=base, container=base).cleanup()
                log_file.unlink(missing_ok=True)
                elapsed = time.monotonic() - cleanup_started
                logger.info("Antigravity reviewer workspace cleanup elapsed=%.2fs", elapsed)
                if elapsed > 15:
                    logger.warning("Antigravity reviewer workspace cleanup exceeded 15 seconds")
                    raise AgentOutputError("antigravity isolation cleanup exceeded 15 seconds", kind_hint=AgentFailureKind.PERMISSION)
                if base.exists() or log_file.exists():
                    raise AgentOutputError("antigravity isolation cleanup incomplete", kind_hint=AgentFailureKind.PERMISSION)
        finally:
            if acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def seal_provider_input(self) -> None:
        workspace = self.prepared_workspace
        if workspace is None or self._base is None:
            raise AgentOutputError("antigravity isolation workspace is not prepared", kind_hint=AgentFailureKind.PERMISSION)
        for path in sorted(workspace.input_dir.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if path.is_symlink() or not (path.is_dir() or path.is_file()):
                raise AgentOutputError("antigravity input contains an unsafe path", kind_hint=AgentFailureKind.PERMISSION)
            path.chmod(0o555 if path.is_dir() else 0o444)
        workspace.input_dir.chmod(0o555)
        workspace.container.chmod(0o555)
        self._sealed = _tree_fingerprint(workspace.container)

    def before_provider_process(self) -> None:
        if self._sealed is None or self.prepared_workspace is None:
            raise AgentOutputError("antigravity isolation was not sealed", kind_hint=AgentFailureKind.PERMISSION)
        if _tree_fingerprint(self.prepared_workspace.container) != self._sealed:
            raise AgentOutputError("antigravity isolation changed before start", kind_hint=AgentFailureKind.PERMISSION)
        self._process_started = True

    def bind_prepared_workspace(self, workspace: AntigravityWorkspace) -> None:
        if not isinstance(workspace, AntigravityWorkspace):
            raise TypeError("antigravity requires a prepared isolation workspace")
        if workspace.home.resolve() != self.isolated_home.resolve():
            raise ValueError("antigravity workspace HOME differs from the isolated profile")
        self.prepared_workspace = workspace

    def prepared_execution_root(self) -> Path:
        if self.prepared_workspace is None:
            raise AgentOutputError("antigravity isolation workspace is not prepared", kind_hint=AgentFailureKind.PERMISSION)
        return self.prepared_workspace.container

    def build_command(self, prompt: str) -> tuple[list[str], bool]:
        raise RuntimeError("antigravity requires a native review bundle")

    def prepare_provider_input(self, prompt: str) -> PreparedProviderInput:
        raise RuntimeError("antigravity requires a native review bundle")

    def prepare_native_provider_input(self, bundle: NativeReviewRequestBundle) -> PreparedProviderInput:
        workspace = self.prepared_workspace
        if workspace is None:
            raise AgentOutputError("antigravity isolation workspace is not prepared", kind_hint=AgentFailureKind.PERMISSION)
        if not isinstance(bundle, NativeReviewRequestBundle) or bundle.capability_profile != "antigravity":
            raise TypeError("antigravity requires a provider-bound native review bundle")
        agent_text = workspace.agent_file.read_text(encoding="utf-8")
        if self.role_binding.policy not in agent_text:
            raise AgentOutputError("antigravity agent lacks the bound reviewer policy", kind_hint=AgentFailureKind.PERMISSION)
        try:
            reviewer_input = build_reviewer_input(bundle, workspace.input_dir, chunk_chars=REVIEW_PACKET_CHUNK_CHARS)
            schema_json = bundle.provider_response_schema_json
            schema_path = workspace.input_dir / "writer-schema.json"
            schema_path.write_bytes(schema_json.encode("utf-8"))
            directive = reviewer_input.directive
            command = AntigravityTransport.command(self.settings, workspace, directive, schema_path)
            profile = normalize_transport_profile("antigravity", command)
            assert_provider_capabilities("antigravity", (), profile=profile)
            transmitted_files = (
                *reviewer_input.request_files, *reviewer_input.evidence_files,
                *reviewer_input.manifest_pages, reviewer_input.manifest_file,
            )
            if len(transmitted_files) != len(reviewer_input.components):
                raise ReviewerInputError("antigravity manifest component count differs")
            components = [
                *(ProviderInputComponent(item.name, path.read_text(encoding="utf-8"))
                  for item, path in zip(reviewer_input.components, transmitted_files, strict=True)),
                ProviderInputComponent("system_policy", agent_text),
                ProviderInputComponent("response_schema", schema_path.read_text(encoding="utf-8")),
                ProviderInputComponent("start_directive", directive),
            ]
            self.env = self._isolated_environment()
            self._writer_json = schema_json
            self._request_id = bundle.bound_context.request_id
            return PreparedProviderInput(tuple(command), None, tuple(components), allow_duplicate_indexed_content=True)
        except (ReviewerInputError, NativeProviderSchemaError) as exc:
            raise AgentOutputError("antigravity provider input is invalid", kind_hint=AgentFailureKind.OUTPUT) from exc

    def validate_process_output(self, stderr: str) -> None:
        # The versioned classifier considers stderr jointly with the envelope.
        _ = stderr

    def stream_filter(self, channel: str, line: str, state: dict[str, str | bool]) -> bool:
        return False

    def extract_output(self, stdout: str, stderr: str, extra_files: dict[str, str]) -> str:
        if self._writer_json is None or self._request_id is None:
            raise AgentOutputError("antigravity request binding is missing", kind_hint=AgentFailureKind.OUTPUT)
        exit_code = int(extra_files.get("exit_code", "0"))
        if stdout.strip():
            preliminary = (
                strict_json_object(stdout)
                if classify_agy_stderr(stderr)[0] is None else {}
            )
            preliminary_usage = preliminary.get("usage")
            if isinstance(preliminary_usage, dict):
                self.metadata = {"usage": {
                    key: value for key, value in preliminary_usage.items()
                    if key in {"input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens"}
                    and type(value) is int and value >= 0
                }}
        envelope, usage = AntigravityTransport.envelope(stdout, stderr, exit_code, self._writer_json)
        self.metadata = {"usage": usage, "num_turns": envelope.get("num_turns")}
        structured = envelope["structured_output"]
        result = structured.get("result") if isinstance(structured, dict) else None
        if not isinstance(structured, dict) or set(structured) != {"result"} or not isinstance(result, dict):
            raise AgentOutputError("antigravity structured output lacks its sole result object", kind_hint=AgentFailureKind.OUTPUT)
        if result.get("request_id") != self._request_id:
            raise AgentOutputError("antigravity response request binding differs", kind_hint=AgentFailureKind.OUTPUT)
        if result.get("schema_version") != self.role_binding.contract:
            raise AgentOutputError("antigravity response role contract differs", kind_hint=AgentFailureKind.OUTPUT)
        try:
            return canonical_native_review_json(result)
        except NativeReviewContractError as exc:
            raise AgentOutputError("antigravity response violates local result schema", kind_hint=AgentFailureKind.OUTPUT) from exc

    def cleanup(self) -> None:
        super().cleanup()
        self._writer_json = None
        self._request_id = None
        # The outer review_execution_boundary owns the workspace and postcheck.
