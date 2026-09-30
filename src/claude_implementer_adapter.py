"""Native implementer transport with a fail-closed sandbox boundary."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import shutil
import tempfile
from pathlib import Path

from agent_adapters import (
    AgentOutputError, AgentPermissionError, CapabilitySpec,
    PROVIDER_FAILURE_METRIC_KEYS, _BaseAdapter,
)
from provider_metrics import failure_metrics, log_permission_denials
from agent_config import AgentSettings
from toolchain_paths import validate_toolchain_read_roots, validate_private_scratch
from permission_policy import classify_implementer_denial
from provider_metrics import permission_denial_summaries
from agent_roles import AgentRoleName
from native_implementer_contract import NativeImplementerContractError, canonical_native_implementer_json
from native_implementer_request import NativeImplementerRequestBundle
from native_provider_schema import (
    CLAUDE_IMPLEMENTER_START_DIRECTIVE, NativeProviderSchemaError, assert_provider_capabilities,  # allowlist:provider -- profile configuration: implementer stdin directive
    exact_cli_version_pattern, normalize_transport_profile,
)
from provider_input_budget import PreparedProviderInput, ProviderInputComponent
from role_binding import RoleBinding, binding_for


def _canonical(document: object) -> str:
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _absolute_path(value: Path, root: Path) -> Path:
    candidate = value if value.is_absolute() else root / value
    if not candidate.is_absolute():
        raise AgentOutputError("implementer protection path is not absolute")
    try:
        candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise AgentOutputError("implementer protection path cannot be resolved") from exc
    return candidate.absolute()


def protected_implementer_paths(
    repository_root: Path, inbox_dir: Path | None, outbox_dir: Path | None,
    run_id: str,
) -> tuple[Path, ...]:
    """Bind lexical and resolved control paths, including a linked worktree's Git dirs."""
    root = repository_root.resolve(strict=True)
    if (not root.is_dir() or inbox_dir is None or outbox_dir is None
        or not run_id or Path(run_id).name != run_id or run_id in {".", ".."}):
        raise AgentOutputError("implementer repository or queue directories are unbound")
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--absolute-git-dir", "--git-common-dir"],
            cwd=root, check=True, capture_output=True, text=True,
            env={"HOME": str(Path.home()), "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C"},
        )
        git_paths = completed.stdout.splitlines()
        if len(git_paths) != 2 or not all(git_paths):
            raise ValueError("Git directory result is incomplete")
        paths = [
            root / ".git", root / ".orchestrator",
            root / ".orchestrator" / "artifacts" / run_id / "records",
            root / ".orchestrator" / "checkpoints" / run_id,
            root / ".orchestrator" / "artifacts" / "native-codex-evidence",  # allowlist:provider -- transport: established evidence namespace
            root / "inbox", root / "outbox",
            _absolute_path(inbox_dir, root), _absolute_path(outbox_dir, root),
            _absolute_path(Path(git_paths[0]), root),
            _absolute_path(Path(git_paths[1]), root),
        ]
        # Keep the in-repository aliases as well as resolved targets. The
        # file tools and Bash interpret path rules through different layers.
        unique = {str(path.absolute()): path.absolute() for path in paths}
        unique.update({str(path.resolve(strict=False)): path.resolve(strict=False) for path in paths})
    except (OSError, subprocess.CalledProcessError, RuntimeError, ValueError) as exc:
        raise AgentOutputError("implementer protection paths cannot be resolved") from exc
    # The CLI splits tool rules at commas and whitespace.
    if any("," in key or any(char.isspace() for char in key) for key in unique):
        raise AgentOutputError("implementer protection path contains a rule separator")
    return tuple(unique[key] for key in sorted(unique))


IMPLEMENTER_DENIED_ENV_VARS = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",  # allowlist:provider -- transport: sandbox credential denylist
)


def implementer_disallowed_tools(settings: dict[str, object]) -> str:
    """Repeat the protected-path Edit rules on the command line.

    A settings document that the CLI rejects is dropped without an error.
    The CLI rules keep the protected paths closed in that case, and Bash then
    needs an approval that --permission-prompts none denies.
    """
    return ",".join(settings["permissions"]["deny"])  # type: ignore[index]


def outermost_protected_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    """Drop paths inside another protected path.

    The sandbox mounts every write denial read-only and creates a placeholder
    for a missing one. A missing path below an already read-only parent makes
    that placeholder impossible, and then every Bash command fails to start.
    The outer rule already covers the inner path for file tools and Bash.
    """
    return tuple(
        path for path in paths
        if not any(other != path and path.is_relative_to(other) for other in paths)
    )


def implementer_settings(paths: tuple[Path, ...], root: Path, tool_roots: tuple[str, ...] = (), *, scratch: Path | None = None) -> dict[str, object]:
    if not paths or any(not path.is_absolute() for path in paths):
        raise AgentOutputError("implementer protection paths are incomplete")
    resolved_roots = validate_toolchain_read_roots(tool_roots, root, paths)
    if resolved_roots != tool_roots:
        raise AgentOutputError("toolchain read roots changed after profile binding")
    if scratch is None:
        raise AgentOutputError("implementer scratch is unbound")
    validate_private_scratch(scratch, root, paths, tool_roots)
    paths = outermost_protected_paths(paths)
    repository = root.resolve(strict=True)
    deny = []
    for path in paths:
        value = "./" + path.relative_to(repository).as_posix() if path.is_relative_to(repository) else str(path)
        deny.extend((f"Edit({value})", f"Edit({value}/**)"))
    return {
        "disableAllHooks": True,
        "permissions": {
            "deny": deny,
            "blockReadsOutsideWorkingDirectories": True,
        },
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "allowUnsandboxedCommands": False,
            "autoAllowBashIfSandboxed": True,
            "excludedCommands": [],
            "filesystem": {"denyWrite": [str(path) for path in paths], "allowWrite": [str(scratch)],
                           **({"allowRead": list(tool_roots)} if tool_roots else {})},
            "network": {"allowedDomains": [], "strictAllowlist": True},
            # Documented list form. The CLI silently ignores the whole
            # --settings document when one entry has another shape.
            "credentials": {"envVars": [
                {"name": name, "mode": "deny"} for name in IMPLEMENTER_DENIED_ENV_VARS
            ]},
        },
    }


class NativeClaudeImplementerAdapter(_BaseAdapter):  # allowlist:provider -- transport: implementer registration
    execution_boundary_profile = "claude-write-boundary"  # allowlist:provider -- transport: implementer boundary
    live_stream_profile = "claude-stream-json"  # allowlist:provider -- transport: implementer live stream
    live_stream_version_field = "claude_code_version"  # allowlist:provider -- profile configuration: init version field
    session_limit_profile = "technical-session-limit"
    required_hosts = ("api.anthropic.com",)
    inherit_process_environment = False
    set_pwd = False
    capability = CapabilitySpec(
        version_args=("--version",), help_args=("--help",),
        supported_version_patterns=(exact_cli_version_pattern("claude"),),  # allowlist:provider -- transport: implementer CLI binding
        required_help_flags=(
            "--json-schema", "--model", "--effort", "--tools", "--output-format",
            "--settings", "--restricted", "--safe-mode", "--permission-prompts",
            "--strict-mcp-config", "--system-prompt", "--add-dir", "--verbose",
        ),
    )

    @property
    def log_profile(self) -> str:
        return "claude-sandbox-implementer"  # allowlist:provider -- transport: implementer log profile

    def __init__(self, settings: AgentSettings, *, role_binding: RoleBinding | None = None) -> None:
        super().__init__(settings)
        self.role_binding = role_binding or binding_for("claude", AgentRoleName.IMPLEMENTER)  # allowlist:provider -- certification data: implementer rights
        if self.role_binding.role is not AgentRoleName.IMPLEMENTER:
            raise TypeError("Claude implementer requires implementer role binding")  # allowlist:provider -- transport: implementer binding
        self._scratch: Path | None = None
        self._repository_root: Path | None = None
        self._protected_paths: tuple[Path, ...] | None = None

    def bind_implementer_boundary(
        self, repository_root: Path, inbox_dir: Path | None, outbox_dir: Path | None,
        run_id: str,
    ) -> None:
        self._repository_root = repository_root.resolve(strict=True)
        self._protected_paths = protected_implementer_paths(
            self._repository_root, inbox_dir, outbox_dir, run_id,
        )

    def build_command(self, prompt: str) -> tuple[list[str], bool]:
        raise RuntimeError("native Claude implementer requires a bound request")  # allowlist:provider -- transport: implementer binding

    def prepare_provider_input(self, prompt: str) -> PreparedProviderInput:
        raise RuntimeError("native Claude implementer requires a bound request")  # allowlist:provider -- transport: implementer binding

    def prepare_native_provider_input(self, bundle: NativeImplementerRequestBundle) -> PreparedProviderInput:
        if self._repository_root is None or self._protected_paths is None:
            raise AgentOutputError("Claude implementer boundary is unbound")  # allowlist:provider -- transport: implementer boundary
        if not isinstance(bundle, NativeImplementerRequestBundle) or bundle.capability_profile != "claude-implementer":  # allowlist:provider -- transport: implementer profile
            raise AgentOutputError("Claude implementer writer profile differs")  # allowlist:provider -- transport: implementer profile
        if len(bundle.canonical_json.encode("utf-8")) > 10_000_000:
            raise AgentOutputError("Claude implementer request exceeds stdin limit")  # allowlist:provider -- transport: implementer stdin limit
        try:
            self.cleanup()
            self._new_runtime_dir()
            self._scratch = Path(tempfile.mkdtemp(prefix="dao-implementer-scratch-", dir="/tmp"))
            self._scratch.chmod(0o700)
            validate_private_scratch(self._scratch, self._repository_root, self._protected_paths,
                                     self.settings.toolchain_read_roots)
            self.env = {
                "HOME": str(Path.home().resolve()),
                "USER": os.environ.get("USER", ""),
                "LOGNAME": os.environ.get("LOGNAME", ""),
                "PATH": ":".join([*(str(Path(root) / "bin") for root in self.settings.toolchain_read_roots
                                   if (Path(root) / "bin").is_dir()), "/usr/local/bin:/usr/bin:/bin"]),
                "LANG": "C.UTF-8", "TERM": "dumb", "TMPDIR": str(self._scratch),
            }
            settings_json = _canonical(implementer_settings(self._protected_paths, self._repository_root, self.settings.toolchain_read_roots, scratch=self._scratch))
            for asset in bundle.evidence_assets:
                target = self._repository_root.joinpath(*Path(asset.path).parts)
                if not target.is_relative_to(self._repository_root / ".orchestrator" / "artifacts" / "native-codex-evidence"):  # allowlist:provider -- transport: established evidence namespace
                    raise AgentOutputError("Claude evidence asset path escapes the protected area")  # allowlist:provider -- transport: implementer evidence
                if any(parent.is_symlink() for parent in target.parents if parent.is_relative_to(self._repository_root)):
                    raise AgentOutputError("Claude evidence asset parent is a link")  # allowlist:provider -- transport: implementer evidence
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.is_symlink() or target.parent.is_symlink():
                    raise AgentOutputError("Claude evidence asset is a link")  # allowlist:provider -- transport: implementer evidence
                if target.exists():
                    if not target.is_file() or target.read_text(encoding="utf-8") != asset.content:
                        raise AgentOutputError("Claude evidence asset differs")  # allowlist:provider -- transport: implementer evidence
                else:
                    target.write_text(asset.content, encoding="utf-8")
                if hashlib.sha256(target.read_bytes()).hexdigest() != asset.sha256:
                    raise AgentOutputError("Claude evidence asset digest differs")  # allowlist:provider -- transport: implementer evidence
            self.invocation.request_id = bundle.bound_context.request_id
            policy = self.role_binding.policy
            schema = bundle.provider_response_schema_json
            directive = CLAUDE_IMPLEMENTER_START_DIRECTIVE  # allowlist:provider -- transport: implementer stdin directive
            command = [
                self.cli_binary, "-p", "--output-format", "stream-json", "--model", self.model,
                "--effort", self.effort, "--no-session-persistence", "--disable-slash-commands",
                "--strict-mcp-config", "--restricted", "--safe-mode", "--prompt-suggestions", "false",
                "--tools", self.role_binding.permissions["tools"],
                "--permission-mode", self.role_binding.permissions["permission_mode"],
                "--permission-prompts", self.role_binding.permissions["permission_prompts"],
                "--disallowedTools", implementer_disallowed_tools(json.loads(settings_json)),
                "--settings", settings_json, "--json-schema", schema,
                "--system-prompt", policy, "--verbose", "--add-dir", str(self._scratch), directive,
            ]
            profile = normalize_transport_profile(
                "claude-implementer", command, bound_settings_json=settings_json, bound_repository_root=self._repository_root,  # allowlist:provider -- transport: implementer normalization
                bound_toolchain_read_roots=self.settings.toolchain_read_roots, bound_scratch=self._scratch,
            )
            assert_provider_capabilities("claude-implementer", (), profile=profile)  # allowlist:provider -- transport: implementer capability
            measured_settings = json.loads(settings_json)
            measured_settings["sandbox"]["filesystem"]["allowWrite"] = ["<private-per-invocation>"]
            return PreparedProviderInput(
                tuple(command), bundle.canonical_json,
                (
                    ProviderInputComponent("stdin_prompt", bundle.canonical_json),
                    ProviderInputComponent("system_policy", policy),
                    ProviderInputComponent("response_schema", schema),
                    ProviderInputComponent("settings", _canonical(measured_settings)),
                    ProviderInputComponent("start_directive", directive),
                    *(ProviderInputComponent(f"evidence_asset_{index:03d}", asset.content)
                      for index, asset in enumerate(bundle.evidence_assets, start=1)),
                ),
            )
        except BaseException:
            self.cleanup()
            raise

    def cleanup(self) -> None:
        if self._scratch is not None:
            shutil.rmtree(self._scratch, ignore_errors=True)
            self._scratch = None
        super().cleanup()

    def extract_output(self, stdout: str, stderr: str, extra_files: dict[str, str]) -> str:
        events = []
        try:
            for line in stdout.splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                    raise ValueError("invalid stream event")
                events.append(event)
        except (ValueError, TypeError) as exc:
            raise AgentOutputError("implementer returned invalid JSON stream") from exc
        results = [event for event in events if event["type"] == "result"]
        if len(results) != 1 or not events or events[-1] is not results[0]:
            raise AgentOutputError("implementer stream requires exactly one final result")
        envelope = results[0]
        self.metadata = failure_metrics(envelope, PROVIDER_FAILURE_METRIC_KEYS)
        denials = envelope.get("permission_denials", [])
        if not isinstance(denials, list):
            denials = [None]
        calls = {}
        for event in events:
            message = event.get("message")
            content = message.get("content", []) if isinstance(message, dict) else []
            if event["type"] == "assistant" and isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and isinstance(block.get("id"), str):
                        call = {"tool_name": block.get("name"), "tool_input": block.get("input")}
                        if block["id"] in calls and calls[block["id"]] != call:
                            calls[block["id"]] = None
                        else:
                            calls[block["id"]] = call
        # Live events carry only the call ID. Recover complete input from the
        # preceding tool_use or the final denial array, never from a log excerpt.
        for event in events:
            if event["type"] != "system" or event.get("subtype") != "permission_denied":
                continue
            call_id = event.get("tool_use_id")
            call = calls.get(call_id) if isinstance(call_id, str) else None
            matching = [item for item in denials if isinstance(item, dict)
                        and isinstance(call_id, str) and item.get("tool_use_id") == call_id
                        and item.get("tool_name") == event.get("tool_name")]
            if isinstance(call_id, str) and call_id in calls and call is None:
                denials.append(None)
                continue
            if matching and (call is None or all(item.get("tool_input") == call["tool_input"]
                                                 and item.get("tool_name") == call["tool_name"] for item in matching)):
                continue
            if call is not None and call["tool_name"] == event.get("tool_name"):
                event = {**event, "tool_input": call["tool_input"]}
            denials.append(event)
        dispositions = [classify_implementer_denial(denial, self._repository_root, self._protected_paths, self._scratch)
                        for denial in denials]
        summaries = permission_denial_summaries(denials)
        self.metadata["permission_denials"] = [
            {**summary, "disposition": disposition}
            for summary, disposition in zip(summaries, dispositions)
        ]
        log_permission_denials(self.metadata)
        if "violation" in dispositions:
            raise AgentPermissionError("implementer attempted a denied protected action: "
                                       + json.dumps(self.metadata["permission_denials"], ensure_ascii=False),
                                       provider_data=self.metadata,
                                       technical_text="implementer attempted a denied protected action")
        safe_envelope = {**envelope, **self.metadata}
        if envelope.get("is_error") is not False or envelope.get("subtype") != "success":
            raise AgentOutputError("implementer reported an error", provider_data=safe_envelope)
        structured = envelope.get("structured_output")
        if not isinstance(structured, dict) or set(structured) != {"result"} or not isinstance(structured["result"], dict):
            raise AgentOutputError("Claude implementer result envelope is invalid")  # allowlist:provider -- transport: implementer output
        result = structured["result"]
        if result.get("request_id") != self.invocation.request_id or result.get("schema_version") != self.role_binding.contract:
            raise AgentOutputError("Claude implementer result differs from bound request")  # allowlist:provider -- transport: implementer output
        try:
            return canonical_native_implementer_json(result)
        except NativeImplementerContractError as exc:
            raise AgentOutputError(
                "Claude implementer result violates the local result schema",  # allowlist:provider -- transport: implementer output
                provider_data=result, technical_text=f"{exc.code.value}: {exc.detail}",
                orchestrator_diagnostic=exc.orchestrator_diagnostic,
            ) from exc
