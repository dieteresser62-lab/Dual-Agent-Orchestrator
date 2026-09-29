from __future__ import annotations

import json
import shlex
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from agent_config import AgentSettings, default_agent_settings
from agent_roles import AgentRoleName, AgentSlot, role_for_slot
from provider_input_budget import PreparedProviderInput, ProviderInputComponent
from reviewer_input import (
    REVIEW_PACKET_CHUNK_CHARS, ReviewerInputBundle, ReviewerInputError,
    build_reviewer_input,
)
from role_binding import RoleBinding, binding_for, binding_for_role
from role_certification import (
    CertificationError, CertificationErrorCode, CertificationTable,
    load_role_certifications,
)
from native_review_contract import (
    NativeReviewContractError,
    canonical_native_review_json,
)
from native_review_request import (
    NativeReviewRequestBundle,
)
from native_implementer_contract import (
    NativeImplementerContractError,
    canonical_native_implementer_json,
)
from native_implementer_request import (
    NativeImplementerRequestBundle,
)
from native_provider_schema import (
    NativeProviderSchemaError,
    assert_provider_capabilities,
    exact_cli_version_pattern,
    normalize_transport_profile,
)
from orchestrator_diagnostics import OrchestratorDiagnostic
from provider_identity import ProviderIdentity


PROJECT_ROOT = Path(__file__).resolve().parent.parent
REVIEW_HARNESS = Path(__file__).resolve().parent / "review_harness.py"
CLAUDE_REVIEW_PACKET_CHUNK_CHARS = REVIEW_PACKET_CHUNK_CHARS
CLAUDE_REVIEW_RESPONSE_MAX_CHARS = 12_000
PROVIDER_FAILURE_METRIC_KEYS = (
    "duration_api_ms",
    "num_turns",
    "total_cost_usd",
    "usage",
    "modelUsage",
    "permission_denials",
    "subtype",
)


class AgentOutputError(RuntimeError):
    """Raised when a CLI returns an invalid or explicitly failed output envelope."""

    def __init__(
        self,
        message: str,
        *,
        provider_text: str | None = None,
        provider_data: dict[str, object] | None = None,
        technical_text: str | None = None,
        exit_code: int | None = None,
        orchestrator_diagnostic: OrchestratorDiagnostic | None = None,
        kind_hint: object | None = None,
    ) -> None:
        if orchestrator_diagnostic is not None and not isinstance(
            orchestrator_diagnostic, OrchestratorDiagnostic
        ):
            raise TypeError("orchestrator diagnostic must be a closed enum member")
        self.provider_text = provider_text or message
        self.provider_data = provider_data
        self.technical_text = technical_text or self.provider_text
        self.exit_code = exit_code
        self.orchestrator_diagnostic = orchestrator_diagnostic
        self.kind_hint = kind_hint
        super().__init__(message)


class AgentPermissionError(AgentOutputError):
    """Raised when a reviewer attempts a tool call outside its explicit allowlist."""


class AgentBudgetError(AgentOutputError):
    """Raised when a configured per-call budget guard stops an invocation."""


@dataclass(frozen=True)
class CapabilitySpec:
    version_args: tuple[str, ...]
    help_args: tuple[str, ...]
    supported_version_patterns: tuple[str, ...]
    required_help_flags: tuple[str, ...]


@dataclass(slots=True)
class AgentInvocationData:
    """Mutable files and results belonging to one provider invocation."""

    runtime_dir: Path | None = None
    env: dict[str, str] = field(default_factory=lambda: {"NO_COLOR": "1"})
    metadata: dict[str, object] = field(default_factory=dict)
    request_id: str | None = None
    last_message_file: Path | None = None
    response_schema_file: Path | None = None
    reviewer_input: ReviewerInputBundle | None = None
    bound_review_harness: Path | None = None


class NativeCodexExecutionMode(StrEnum):
    """Execution policy selected before a native Codex provider invocation."""

    PRODUCTION = "production"
    CANARY = "canary"


@dataclass(frozen=True, slots=True)
class NativeCodexExecutionBoundary:
    """Typed filesystem and sandbox boundary for one native Codex call."""

    mode: NativeCodexExecutionMode
    repository_root: Path
    execution_root: Path
    evidence_asset_root: Path
    sandbox_mode: str

    def __post_init__(self) -> None:
        repository_root = self.repository_root.resolve()
        execution_root = self.execution_root.resolve()
        evidence_asset_root = self.evidence_asset_root.resolve()
        object.__setattr__(self, "repository_root", repository_root)
        object.__setattr__(self, "execution_root", execution_root)
        object.__setattr__(self, "evidence_asset_root", evidence_asset_root)
        if not isinstance(self.mode, NativeCodexExecutionMode):
            raise TypeError("native Codex execution mode is invalid")
        if self.sandbox_mode not in {"workspace-write", "read-only"}:
            raise ValueError("native Codex sandbox mode is invalid")
        if not execution_root.is_dir() or not evidence_asset_root.is_dir():
            raise ValueError("native Codex execution and evidence roots must exist")
        if self.mode is NativeCodexExecutionMode.PRODUCTION:
            if (
                execution_root != repository_root
                or evidence_asset_root != repository_root
                or self.sandbox_mode != "workspace-write"
            ):
                raise ValueError("production native Codex boundary changed its defaults")
            return
        if self.sandbox_mode != "read-only":
            raise ValueError("native Codex canary must use read-only sandboxing")
        for label, path in (
            ("execution_root", execution_root),
            ("evidence_asset_root", evidence_asset_root),
        ):
            if path == repository_root or path.is_relative_to(repository_root):
                raise ValueError(
                    f"native Codex canary {label} must be outside the repository"
                )

    @classmethod
    def production(cls, repository_root: Path) -> "NativeCodexExecutionBoundary":
        root = repository_root.resolve()
        return cls(
            NativeCodexExecutionMode.PRODUCTION,
            root,
            root,
            root,
            "workspace-write",
        )

    @classmethod
    def canary(
        cls,
        repository_root: Path,
        *,
        execution_root: Path,
        evidence_asset_root: Path,
    ) -> "NativeCodexExecutionBoundary":
        return cls(
            NativeCodexExecutionMode.CANARY,
            repository_root,
            execution_root,
            evidence_asset_root,
            "read-only",
        )


def _json_object(text: str, role: str) -> dict[str, object]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AgentOutputError(
            f"{role} returned invalid JSON: {exc}", provider_text=text or str(exc)
        ) from exc
    if not isinstance(value, dict):
        raise AgentOutputError(f"{role} JSON envelope must be an object")
    return value




class AgentAdapter(Protocol):
    name: str
    cli_binary: str
    model: str
    effort: str
    timeout: int
    reviewer: bool
    env: dict[str, str]
    required_hosts: tuple[str, ...]
    capability: CapabilitySpec
    capability_verified: bool
    provider_identity: ProviderIdentity | None
    metadata: dict[str, object]
    inherit_process_environment: bool
    environment_passthrough: tuple[str, ...]
    stdin_closed_when_unused: bool
    suppress_live_stream: bool
    requires_attempt_ledger: bool
    sanitize_reviewer_environment: bool
    set_pwd: bool

    def build_command(self, prompt: str) -> tuple[list[str], bool]: ...

    def prepare_provider_input(self, prompt: str) -> PreparedProviderInput: ...

    def bind_reviewer_workspace(self, source_root: Path, snapshot_root: Path) -> None: ...

    def prepared_execution_root(self) -> Path | None: ...

    def extract_output(self, stdout: str, stderr: str, extra_files: dict[str, str]) -> str: ...

    def stream_filter(self, channel: str, line: str, state: dict[str, str | bool]) -> bool: ...

    def validate_process_output(self, stderr: str) -> None: ...

    def cleanup(self) -> None: ...


class NativeReviewAdapter(AgentAdapter, Protocol):
    def prepare_native_provider_input(
        self, bundle: NativeReviewRequestBundle,
    ) -> PreparedProviderInput: ...


class NativeImplementerAdapter(AgentAdapter, Protocol):
    def prepare_native_provider_input(
        self, bundle: NativeImplementerRequestBundle,
        execution_boundary: NativeCodexExecutionBoundary | None = None,
    ) -> PreparedProviderInput: ...


class _BaseAdapter:
    reviewer = False
    required_hosts: tuple[str, ...] = ()
    inherit_process_environment = True
    environment_passthrough: tuple[str, ...] = ()
    stdin_closed_when_unused = False
    suppress_live_stream = False
    requires_attempt_ledger = False
    sanitize_reviewer_environment = True
    set_pwd = True

    def __init__(self, settings: AgentSettings) -> None:
        self.settings = settings
        self.name = settings.name
        self.cli_binary = settings.binary
        self.model = settings.model
        self.timeout = settings.timeout_seconds
        self.effort = settings.effort
        self.max_budget_usd = settings.max_budget_usd
        self.invocation = AgentInvocationData()
        self.capability_verified = False
        self.provider_identity: ProviderIdentity | None = None

    @property
    def env(self) -> dict[str, str]:
        return self.invocation.env

    @env.setter
    def env(self, value: dict[str, str]) -> None:
        self.invocation.env = value

    @property
    def metadata(self) -> dict[str, object]:
        return self.invocation.metadata

    @metadata.setter
    def metadata(self, value: dict[str, object]) -> None:
        self.invocation.metadata = value

    def bind_reviewer_workspace(self, source_root: Path, snapshot_root: Path) -> None:
        _ = source_root
        _ = snapshot_root

    def prepared_execution_root(self) -> Path | None:
        return None

    def prepare_provider_input(self, prompt: str) -> PreparedProviderInput:
        command, use_stdin = self.build_command(prompt)
        return PreparedProviderInput(
            command=tuple(command),
            stdin_text=prompt if use_stdin else None,
            components=self._provider_input_components(prompt, command),
        )

    def _provider_input_components(
        self, prompt: str, command: list[str]
    ) -> tuple[ProviderInputComponent, ...]:
        _ = prompt
        _ = command
        raise NotImplementedError

    def _new_runtime_dir(self) -> Path:
        bound_harness = self.invocation.bound_review_harness
        self._cleanup_runtime_dir()
        self.invocation = AgentInvocationData(bound_review_harness=bound_harness)
        self.invocation.runtime_dir = Path(tempfile.mkdtemp(prefix=f"dao-{self.name}-runtime-"))
        cache_dir = self.invocation.runtime_dir / "cache"
        try:
            cache_dir.mkdir()
        except BaseException:
            self._cleanup_runtime_dir()
            raise
        self.env = {
            "NO_COLOR": "1",
            "TMPDIR": str(self.invocation.runtime_dir),
            "XDG_CACHE_HOME": str(cache_dir),
        }
        return self.invocation.runtime_dir

    def stream_filter(self, channel: str, line: str, state: dict[str, str | bool]) -> bool:
        _ = channel
        txt = line.strip()
        if not txt:
            return False
        last_line = str(state.get("last_emitted_line", ""))
        if txt == last_line:
            return False
        state["last_emitted_line"] = txt
        return True

    def validate_process_output(self, stderr: str) -> None:
        lowered = (stderr or "").lower()
        incompatible = (
            "is ignored when",
            "has no effect when",
            "does not work with",
            "permission denied",
            "permission request rejected",
        )
        for marker in incompatible:
            if marker in lowered:
                error_type = AgentPermissionError if "permission" in marker else AgentOutputError
                raise error_type(
                    f"{self.name} reported an incompatible capability or permission warning: "
                    f"{stderr.strip()}"
                )

    def cleanup(self) -> None:
        self._cleanup_runtime_dir()
        # Checked callers publish usage after run_agent's finally block.
        # Keep this invocation's metadata until the next _new_runtime_dir,
        # which replaces the entire state object before another provider call.
        self.invocation.request_id = None
        self.invocation.last_message_file = None
        self.invocation.response_schema_file = None
        self.invocation.reviewer_input = None
        self.invocation.bound_review_harness = None

    def _cleanup_runtime_dir(self) -> None:
        if self.invocation.runtime_dir is None:
            return
        shutil.rmtree(self.invocation.runtime_dir, ignore_errors=True)
        self.invocation.runtime_dir = None


class NativeCodexAdapter(_BaseAdapter):
    """Codex transport whose last message is one schema-bound JSON object."""

    required_hosts = ("chatgpt.com", "api.openai.com")
    capability = CapabilitySpec(
        version_args=("--version",),
        help_args=("exec", "--help"),
        supported_version_patterns=(exact_cli_version_pattern("codex"),),
        required_help_flags=(
            "--model",
            "--sandbox",
            "--ephemeral",
            "--json",
            "--output-last-message",
            "--output-schema",
        ),
    )

    def __init__(
        self, settings: AgentSettings | None = None,
        *, role_binding: RoleBinding | None = None,
    ) -> None:
        super().__init__(settings or default_agent_settings()["implementer"])
        self.role_binding = role_binding or binding_for_role(AgentRoleName.IMPLEMENTER)
        if self.role_binding.role is not AgentRoleName.IMPLEMENTER:
            raise TypeError("native Codex adapter requires implementer role binding")

    def _provider_input_components(
        self, prompt: str, command: list[str]
    ) -> tuple[ProviderInputComponent, ...]:
        _ = command
        return (ProviderInputComponent("stdin_prompt", prompt),)

    def stream_filter(self, channel: str, line: str, state: dict[str, str | bool]) -> bool:
        txt = line.strip()
        if not txt:
            return False
        if channel == "stdout" and txt.startswith("{"):
            try:
                event = json.loads(txt)
            except json.JSONDecodeError:
                return False
            if not isinstance(event, dict):
                return False
            item = event.get("item")
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    line = text
                    txt = text.strip()
            if not txt or txt.startswith("{"):
                return False
        return super().stream_filter(channel, line, state)

    def build_command(self, prompt: str) -> tuple[list[str], bool]:
        _ = prompt
        raise RuntimeError(
            "native Codex requests require prepare_native_provider_input(bundle)"
        )

    def prepare_provider_input(self, prompt: str) -> PreparedProviderInput:
        _ = prompt
        raise RuntimeError(
            "native Codex requests require a bound NativeImplementerRequestBundle"
        )

    def prepare_native_provider_input(
        self,
        bundle: NativeImplementerRequestBundle,
        execution_boundary: NativeCodexExecutionBoundary | None = None,
    ) -> PreparedProviderInput:
        try:
            return self._prepare_native_provider_input_unchecked(bundle, execution_boundary)
        except BaseException:
            self.cleanup()
            raise

    def _prepare_native_provider_input_unchecked(
        self,
        bundle: NativeImplementerRequestBundle,
        execution_boundary: NativeCodexExecutionBoundary | None = None,  # allowlist:provider -- transport: typed preparation boundary
    ) -> PreparedProviderInput:
        if not isinstance(bundle, NativeImplementerRequestBundle):
            raise TypeError("native Codex adapter requires NativeImplementerRequestBundle")
        boundary = execution_boundary or NativeCodexExecutionBoundary.production(
            PROJECT_ROOT
        )
        if (
            boundary.mode is NativeCodexExecutionMode.PRODUCTION
            and boundary.sandbox_mode != self.role_binding.permissions["sandbox"]
        ):
            raise AgentOutputError("native Codex production boundary differs from role rights")
        runtime_dir = self._new_runtime_dir()
        self.invocation.last_message_file = runtime_dir / "last-message.json"
        self.invocation.response_schema_file = runtime_dir / "response-schema.json"
        response_schema_json = bundle.provider_response_schema_json
        self.invocation.response_schema_file.write_text(response_schema_json, encoding="utf-8")
        for asset in bundle.evidence_assets:
            target = boundary.evidence_asset_root.joinpath(*Path(asset.path).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                existing = target.read_text(encoding="utf-8")
                if existing != asset.content:
                    raise AgentOutputError(
                        "native Codex evidence asset digest path contains different bytes"
                    )
            else:
                target.write_text(asset.content, encoding="utf-8")
        self.invocation.request_id = bundle.bound_context.request_id
        command = (
            self.cli_binary,
            "exec",
            "--model",
            self.model,
            "--config",
            f'model_reasoning_effort="{self.effort}"',
            "--skip-git-repo-check",
            "--ephemeral",
            "--sandbox",
            boundary.sandbox_mode,
            "--color",
            "never",
            "--json",
            "--output-schema",
            str(self.invocation.response_schema_file),
            "--output-last-message",
            str(self.invocation.last_message_file),
            "-",
        )
        try:
            profile = normalize_transport_profile("codex", command)
            assert_provider_capabilities("codex", (), profile=profile)
        except NativeProviderSchemaError as exc:
            raise AgentOutputError(
                "native Codex transport differs from its probed schema capability",
                technical_text=str(exc),
            ) from exc
        return PreparedProviderInput(
            command=command,
            stdin_text=bundle.canonical_json,
            components=(
                ProviderInputComponent("stdin_prompt", bundle.canonical_json),
                ProviderInputComponent("response_schema", response_schema_json),
                *(
                    ProviderInputComponent(
                        f"evidence_asset_{index:03d}", asset.content
                    )
                    for index, asset in enumerate(bundle.evidence_assets, start=1)
                ),
            ),
        )

    def extract_output(
        self, stdout: str, stderr: str, extra_files: dict[str, str]
    ) -> str:
        _ = stdout
        _ = stderr
        _ = extra_files
        if self.invocation.last_message_file is None or not self.invocation.last_message_file.is_file():
            raise AgentOutputError("native Codex produced no last-message file")
        raw = self.invocation.last_message_file.read_text(encoding="utf-8").strip()
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AgentOutputError(
                "native Codex last message is not valid JSON",
                provider_text=raw or str(exc),
            ) from exc
        if not isinstance(document, dict):
            raise AgentOutputError("native Codex result must be one JSON object")
        if tuple(document) != ("result",) or not isinstance(document["result"], dict):
            raise AgentOutputError(
                "native Codex result must use the closed provider envelope"
            )
        document = document["result"]
        if self.invocation.request_id is None:
            raise AgentOutputError("native Codex adapter has no bound request id")
        if document.get("request_id") != self.invocation.request_id:
            raise AgentOutputError("native Codex response request_id differs from request")
        if document.get("schema_version") != self.role_binding.contract:
            raise AgentOutputError("native Codex response differs from role contract")
        try:
            return canonical_native_implementer_json(document)
        except NativeImplementerContractError as exc:
            raise AgentOutputError(
                "native Codex response violates the local result schema",
                provider_data=document,
                technical_text=f"{exc.code.value}: {exc.detail}",
                orchestrator_diagnostic=exc.orchestrator_diagnostic,
            ) from exc

class NativeClaudeReviewAdapter(_BaseAdapter):
    """Claude reviewer transport whose output is the native review JSON object."""

    reviewer = True
    required_hosts = ("api.anthropic.com",)
    capability = CapabilitySpec(
        version_args=("--version",),
        help_args=("--help",),
        supported_version_patterns=(exact_cli_version_pattern("claude"),),
        required_help_flags=(
            "--add-dir",
            "--json-schema",
            "--model",
            "--effort",
            "--tools",
            "--allowedTools",
            "--permission-mode",
            "--output-format",
            "--no-session-persistence",
            "--restricted",
            "--safe-mode",
            "--strict-mcp-config",
            "--system-prompt",
            "--prompt-suggestions",
        ),
    )

    def __init__(
        self,
        settings: AgentSettings | None = None,
        *,
        review_harness: Path = REVIEW_HARNESS,
        role_binding: RoleBinding | None = None,
    ) -> None:
        if settings is None:
            raise TypeError(
                "NativeClaudeReviewAdapter requires explicit Claude AgentSettings"
            )
        super().__init__(settings)
        self.review_harness = review_harness.resolve()
        self.role_binding = role_binding or binding_for_role(AgentRoleName.REVIEWER)
        if self.role_binding.role is not AgentRoleName.REVIEWER:
            raise TypeError("native Claude adapter requires reviewer role binding")

    def bind_reviewer_workspace(self, source_root: Path, snapshot_root: Path) -> None:
        try:
            relative_harness = self.review_harness.relative_to(source_root.resolve())
        except ValueError:
            self.invocation.bound_review_harness = self.review_harness
        else:
            self.invocation.bound_review_harness = snapshot_root / relative_harness

    def build_capability_smoke_command(
        self,
        prompt: str,
        *,
        test_command: str,
        probe_path: str = "README.md",
        timeout: int = 1800,
    ) -> tuple[list[str], bool]:
        """Build the explicit reviewer-harness diagnostic without a text contract."""
        runtime_dir = self._new_runtime_dir()
        harness = self.invocation.bound_review_harness or self.review_harness
        harness_command = shlex.join(
            [
                sys.executable,
                str(harness),
                "--repo-root",
                ".",
                "--test-command",
                test_command,
                "--probe-path",
                probe_path,
                "--timeout",
                str(timeout),
            ]
        )
        schema = json.dumps(
            {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
            separators=(",", ":"),
        )
        command = [
            self.cli_binary,
            "-p",
            "--output-format",
            "json",
            "--model",
            self.model,
            "--effort",
            self.effort,
            "--tools",
            "Bash,Read",
            "--allowedTools",
            f"Read,Bash({harness_command})",
            "--disallowedTools",
            "Edit,Write,NotebookEdit,Grep,Glob",
            "--permission-mode",
            "dontAsk",
            self.role_binding.permissions["restricted_flag"],
            "--safe-mode",
            "--strict-mcp-config",
            "--prompt-suggestions",
            "false",
            "--add-dir",
            str(runtime_dir),
            "--system-prompt",
            "Run the exact allowlisted reviewer-harness diagnostic once.",
            "--json-schema",
            schema,
            "--no-session-persistence",
            "--disable-slash-commands",
            f"{prompt.strip()} Run exactly: {harness_command}",
        ]
        return command, False

    def _provider_input_components(
        self, prompt: str, command: list[str]
    ) -> tuple[ProviderInputComponent, ...]:
        _ = prompt
        return (
            ProviderInputComponent(
                "system_policy", command[command.index("--system-prompt") + 1]
            ),
            ProviderInputComponent(
                "response_schema", command[command.index("--json-schema") + 1]
            ),
            ProviderInputComponent("start_directive", command[-1]),
        )

    def build_command(self, prompt: str) -> tuple[list[str], bool]:
        _ = prompt
        raise RuntimeError(
            "native Claude reviews require prepare_native_provider_input(bundle)"
        )

    def prepare_provider_input(self, prompt: str) -> PreparedProviderInput:
        _ = prompt
        raise RuntimeError(
            "native Claude reviews require a bound NativeReviewRequestBundle"
        )

    def prepare_native_provider_input(
        self, bundle: NativeReviewRequestBundle
    ) -> PreparedProviderInput:
        if not isinstance(bundle, NativeReviewRequestBundle):
            raise TypeError("native Claude adapter requires NativeReviewRequestBundle")
        runtime_dir = self._new_runtime_dir()
        try:
            reviewer_input = build_reviewer_input(
                bundle, runtime_dir, chunk_chars=REVIEW_PACKET_CHUNK_CHARS
            )
            self.invocation.reviewer_input = reviewer_input
            response_schema_json = bundle.provider_response_schema_json
            policy = self.role_binding.policy
            directive = reviewer_input.directive
            command = [
                self.cli_binary,
                "-p",
                "--output-format",
                "json",
                "--model",
                self.model,
                "--effort",
                self.effort,
                "--tools",
                self.role_binding.permissions["tools"],
                "--allowedTools",
                self.role_binding.permissions["allowed_tools"],
                "--disallowedTools",
                self.role_binding.permissions["disallowed_tools"],
                "--permission-mode",
                self.role_binding.permissions["permission_mode"],
                self.role_binding.permissions["restricted_flag"],
                "--safe-mode",
                "--strict-mcp-config",
                "--prompt-suggestions",
                "false",
                "--add-dir",
                str(runtime_dir),
                "--system-prompt",
                policy,
                "--json-schema",
                response_schema_json,
                "--no-session-persistence",
                "--disable-slash-commands",
            ]
            if self.max_budget_usd is not None:
                command.extend(["--max-budget-usd", str(self.max_budget_usd)])
            command.append(directive)
            try:
                profile = normalize_transport_profile("claude", command)
                assert_provider_capabilities("claude", (), profile=profile)
            except NativeProviderSchemaError as exc:
                raise AgentOutputError(
                    "native Claude transport differs from its probed schema capability",
                    technical_text=str(exc),
                ) from exc
            components = [
                *reviewer_input.components,
                ProviderInputComponent("system_policy", policy),
                ProviderInputComponent("response_schema", response_schema_json),
                ProviderInputComponent("start_directive", directive),
            ]
            # The directive contains the runtime path; normalize only its random suffix
            # so the measured byte count is unchanged across invocations.
            runtime_prefix = f"dao-{self.name}-runtime-"
            random_suffix = runtime_dir.name.removeprefix(runtime_prefix)
            stable_runtime_path = str(runtime_dir.with_name(runtime_prefix + "_" * len(random_suffix)))
            components[-1] = ProviderInputComponent(
                "start_directive", directive.replace(str(runtime_dir), stable_runtime_path)
            )
            self.invocation.request_id = bundle.bound_context.request_id
            return PreparedProviderInput(
                tuple(command), None, tuple(components),
                allow_duplicate_indexed_content=True,
            )
        except ReviewerInputError as exc:
            self.cleanup()
            raise AgentOutputError(str(exc)) from None
        except BaseException:
            self.cleanup()
            raise

    def extract_output(
        self, stdout: str, stderr: str, extra_files: dict[str, str]
    ) -> str:
        _ = stderr
        _ = extra_files
        envelope = _json_object(stdout or "", self.name)
        self.metadata = {
            key: envelope[key]
            for key in PROVIDER_FAILURE_METRIC_KEYS
            if key in envelope
        }
        if envelope.get("is_error") is not False:
            detail = envelope.get("result") or envelope.get("error") or "native Claude error"
            raise AgentOutputError(
                f"claude returned is_error=true: {detail}",
                provider_text=str(detail),
                provider_data=envelope,
            )
        denials = envelope.get("permission_denials")
        if isinstance(denials, list) and denials:
            raise AgentPermissionError(
                "claude attempted non-allowlisted tool calls in native review"
            )
        structured_output = envelope.get("structured_output")
        if not isinstance(structured_output, dict):
            raise AgentOutputError(
                "native Claude JSON envelope has no structured_output object"
            )
        result = structured_output.get("result")
        if set(structured_output) != {"result"} or not isinstance(result, dict):
            raise AgentOutputError(
                "native Claude structured_output has no sole native result object",
                provider_data=structured_output,
            )
        if self.invocation.request_id is None:
            raise AgentOutputError("native Claude adapter has no bound request id")
        if result.get("request_id") != self.invocation.request_id:
            raise AgentOutputError("native Claude response request_id differs from request")
        if result.get("schema_version") != self.role_binding.contract:
            raise AgentOutputError("native Claude response differs from role contract")
        try:
            return canonical_native_review_json(result)
        except NativeReviewContractError as exc:
            raise AgentOutputError(
                "native Claude response violates the local result schema",
                provider_data=result,
                technical_text=f"{exc.code.value}: {exc.detail}",
                orchestrator_diagnostic=exc.orchestrator_diagnostic,
            ) from exc

def is_native_review_adapter(adapter: object) -> bool:
    """Whether a slot adapter is one of the registered native review transports."""
    if isinstance(adapter, NativeClaudeReviewAdapter):  # allowlist:provider -- transport: registered native review adapter
        return True
    from antigravity_adapter import NativeAntigravityReviewAdapter

    return isinstance(adapter, NativeAntigravityReviewAdapter)


def create_reviewer_qualification_adapter(
    settings: AgentSettings, *, role_binding: RoleBinding | None = None,
) -> AgentAdapter:
    """Build the production reviewer transport without a certification gate.

    Qualification and direct canaries establish that gate; they still use the
    same role binding and adapter class as a normal reviewer slot.
    """
    binding = role_binding or binding_for(settings.name, AgentRoleName.REVIEWER)
    if settings.name == "claude":
        return NativeClaudeReviewAdapter(settings, role_binding=binding)
    if settings.name == "antigravity":
        from antigravity_adapter import NativeAntigravityReviewAdapter

        return NativeAntigravityReviewAdapter(settings, role_binding=binding)
    raise ValueError(f"provider={settings.name}: missing reviewer transport registration")


def create_agent_pair(
    provider: str, role: AgentRoleName, *, slot: AgentSlot,
    settings: AgentSettings, certifications: CertificationTable | None = None,
) -> AgentAdapter:
    """Authorize a slot before constructing a fresh transport with its role binding."""
    if not isinstance(role, AgentRoleName) or not isinstance(slot, AgentSlot):
        raise TypeError("provider pair requires typed role and slot")
    if role is not role_for_slot(slot):
        raise CertificationError(
            CertificationErrorCode.NOT_CERTIFIED,
            f"slot={slot.value} provider={provider}: missing qualification evidence for role={role.value}",
        )
    table = certifications if certifications is not None else load_role_certifications()
    certificate = table.require(provider, role, slot, model=settings.model)
    if settings.name != provider:
        raise ValueError(f"slot={slot.value} provider={provider}: settings provider differs")
    binding = binding_for(provider, role)
    if provider == "codex" and role is AgentRoleName.IMPLEMENTER:
        adapter = NativeCodexAdapter(settings, role_binding=binding)
    elif role is AgentRoleName.REVIEWER:
        adapter = create_reviewer_qualification_adapter(settings, role_binding=binding)
    else:
        raise ValueError(
            f"slot={slot.value} provider={provider}: missing transport/role rights binding"
        )
    adapter.certification_status = getattr(certificate, "status", None)
    return adapter


def build_agent_registry(
    settings: dict[str, AgentSettings] | None = None,
) -> dict[str, AgentAdapter]:
    """Create one adapter per slot from its selected provider and profile."""
    resolved = settings if settings is not None else default_agent_settings()
    return build_slot_agent_registry(resolved)


def build_slot_agent_registry(
    slots: dict[str, AgentSettings], *, certifications: CertificationTable | None = None,
) -> dict[str, AgentAdapter]:
    """Create the run's adapters solely from its resolved slot settings."""
    if set(slots) != {slot.value for slot in AgentSlot}:
        raise ValueError("slot_settings must contain implementer, reviewer and final_reviewer")
    table = certifications or load_role_certifications()
    table.require_occupancy(
        {slot: slots[slot.value].name for slot in AgentSlot},
        models={slot: slots[slot.value].model for slot in AgentSlot},
    )
    registry = {
        slot.value: create_agent_pair(
            slots[slot.value].name, role_for_slot(slot), slot=slot,
            settings=slots[slot.value], certifications=table,
        )
        for slot in AgentSlot
    }
    for slot in AgentSlot:
        registry[slot.value].bound_slot = slot.value
    return registry
