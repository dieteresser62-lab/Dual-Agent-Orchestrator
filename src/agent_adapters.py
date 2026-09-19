from __future__ import annotations

import hashlib
import json
import shlex
import shutil
import sys
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from agent_config import AgentSettings, default_agent_settings
from provider_input_budget import PreparedProviderInput, ProviderInputComponent
from prompts import NATIVE_CLAUDE_SYSTEM_POLICY
from native_review_contract import (
    NativeReviewContractError,
    canonical_native_review_json,
)
from native_review_request import (
    NativeReviewRequestBundle,
    PROVIDER_INPUT_BOUNDARY_EVIDENCE_KIND,
)
from native_codex_contract import (
    NativeCodexContractError,
    canonical_native_codex_json,
)
from native_codex_request import (
    NativeCodexRequestBundle,
)
from native_provider_schema import (
    NativeProviderSchemaError,
    assert_provider_capabilities,
    exact_cli_version_pattern,
    normalize_transport_profile,
)
from orchestrator_diagnostics import OrchestratorDiagnostic


PROJECT_ROOT = Path(__file__).resolve().parent.parent
REVIEW_HARNESS = Path(__file__).resolve().parent / "review_harness.py"
CLAUDE_REVIEW_PACKET_CHUNK_CHARS = 24_000
CLAUDE_REVIEW_RESPONSE_MAX_CHARS = 12_000


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


def _split_text_at_lines(text: str, max_chars: int) -> tuple[str, ...]:
    """Split a review packet into bounded, lossless chunks readable in one call each."""
    if max_chars < 1:
        raise ValueError("review packet chunk size must be positive")
    if not text:
        return ("",)
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break
        boundary = remaining.rfind("\n", 0, max_chars)
        if boundary < 1:
            boundary = max_chars
        else:
            boundary += 1
        chunks.append(remaining[:boundary])
        remaining = remaining[boundary:]
    return tuple(chunks)


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
    metadata: dict[str, object]

    def build_command(self, prompt: str) -> tuple[list[str], bool]: ...

    def prepare_provider_input(self, prompt: str) -> PreparedProviderInput: ...

    def bind_reviewer_workspace(self, source_root: Path, snapshot_root: Path) -> None: ...

    def extract_output(self, stdout: str, stderr: str, extra_files: dict[str, str]) -> str: ...

    def stream_filter(self, channel: str, line: str, state: dict[str, str | bool]) -> bool: ...

    def validate_process_output(self, stderr: str) -> None: ...

    def cleanup(self) -> None: ...


class _BaseAdapter:
    reviewer = False
    required_hosts: tuple[str, ...] = ()

    def __init__(self, settings: AgentSettings) -> None:
        self.settings = settings
        self.name = settings.name
        self.cli_binary = settings.binary
        self.model = settings.model
        self.timeout = settings.timeout_seconds
        self.effort = settings.effort
        self.max_budget_usd = settings.max_budget_usd
        self.env: dict[str, str] = {"NO_COLOR": "1"}
        self.capability_verified = False
        self.metadata: dict[str, object] = {}
        self._runtime_dir: Path | None = None

    def bind_reviewer_workspace(self, source_root: Path, snapshot_root: Path) -> None:
        _ = source_root
        _ = snapshot_root

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
        self._cleanup_runtime_dir()
        self.metadata = {}
        self._runtime_dir = Path(tempfile.mkdtemp(prefix=f"dao-{self.name}-runtime-"))
        cache_dir = self._runtime_dir / "cache"
        cache_dir.mkdir()
        self.env = {
            "NO_COLOR": "1",
            "TMPDIR": str(self._runtime_dir),
            "XDG_CACHE_HOME": str(cache_dir),
        }
        return self._runtime_dir

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

    def _cleanup_runtime_dir(self) -> None:
        if self._runtime_dir is None:
            return
        shutil.rmtree(self._runtime_dir, ignore_errors=True)
        self._runtime_dir = None


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

    def __init__(self, settings: AgentSettings | None = None) -> None:
        super().__init__(settings or default_agent_settings()["codex"])
        self._last_message_file: Path | None = None
        self._native_request_id: str | None = None
        self._response_schema_file: Path | None = None

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
            "native Codex requests require a bound NativeCodexRequestBundle"
        )

    def prepare_native_provider_input(
        self,
        bundle: NativeCodexRequestBundle,
        execution_boundary: NativeCodexExecutionBoundary | None = None,
    ) -> PreparedProviderInput:
        if not isinstance(bundle, NativeCodexRequestBundle):
            raise TypeError("native Codex adapter requires NativeCodexRequestBundle")
        boundary = execution_boundary or NativeCodexExecutionBoundary.production(
            PROJECT_ROOT
        )
        runtime_dir = self._new_runtime_dir()
        self._last_message_file = runtime_dir / "last-message.json"
        self._response_schema_file = runtime_dir / "response-schema.json"
        response_schema_json = bundle.provider_response_schema_json
        self._response_schema_file.write_text(response_schema_json, encoding="utf-8")
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
        self._native_request_id = bundle.bound_context.request_id
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
            str(self._response_schema_file),
            "--output-last-message",
            str(self._last_message_file),
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
        if self._last_message_file is None or not self._last_message_file.is_file():
            raise AgentOutputError("native Codex produced no last-message file")
        raw = self._last_message_file.read_text(encoding="utf-8").strip()
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
        if self._native_request_id is None:
            raise AgentOutputError("native Codex adapter has no bound request id")
        if document.get("request_id") != self._native_request_id:
            raise AgentOutputError("native Codex response request_id differs from request")
        try:
            return canonical_native_codex_json(document)
        except NativeCodexContractError as exc:
            raise AgentOutputError(
                "native Codex response violates the local result schema",
                provider_data=document,
                technical_text=f"{exc.code.value}: {exc.detail}",
                orchestrator_diagnostic=exc.orchestrator_diagnostic,
            ) from exc

    def cleanup(self) -> None:
        super().cleanup()
        self._native_request_id = None
        self._response_schema_file = None


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
            "--setting-sources",
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
    ) -> None:
        if settings is None:
            raise TypeError(
                "NativeClaudeReviewAdapter requires explicit Claude AgentSettings"
            )
        super().__init__(settings)
        self.review_harness = review_harness.resolve()
        self._bound_review_harness: Path | None = None
        self._review_manifest_file: Path | None = None
        self._review_packet_files: tuple[Path, ...] = ()
        self._native_evidence_files: tuple[Path, ...] = ()
        self._native_request_id: str | None = None

    def bind_reviewer_workspace(self, source_root: Path, snapshot_root: Path) -> None:
        try:
            relative_harness = self.review_harness.relative_to(source_root.resolve())
        except ValueError:
            self._bound_review_harness = self.review_harness
        else:
            self._bound_review_harness = snapshot_root / relative_harness

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
        harness = self._bound_review_harness or self.review_harness
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
            "--setting-sources",
            "user",
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
        request_chunks = _split_text_at_lines(
            bundle.canonical_json, CLAUDE_REVIEW_PACKET_CHUNK_CHARS
        )
        self._review_packet_files = tuple(
            runtime_dir / f"native-request-{index:03d}.json.part"
            for index in range(1, len(request_chunks) + 1)
        )
        manifest_entries: list[tuple[str, Path, str, int]] = []
        for index, (packet_file, chunk) in enumerate(
            zip(self._review_packet_files, request_chunks, strict=True), start=1
        ):
            packet_file.write_text(chunk, encoding="utf-8")
            manifest_entries.append(
                (
                    f"request_chunk_{index:03d}",
                    packet_file,
                    hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
                    len(chunk.encode("utf-8")),
                )
            )

        evidence_files: list[Path] = []
        for asset in bundle.evidence_assets:
            target = runtime_dir.joinpath(*Path(asset.path).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(asset.content, encoding="utf-8")
            evidence_files.append(target)
            manifest_entries.append(
                (
                    f"evidence_asset_{len(evidence_files):03d}",
                    target,
                    asset.sha256,
                    asset.byte_count,
                )
            )
        self._native_evidence_files = tuple(evidence_files)
        self._review_manifest_file = runtime_dir / "native-review-manifest.md"
        manifest_lines = [
            "# Native review request manifest",
            "",
            "Read every listed file exactly once in order. Concatenate request chunks without separators before interpreting the JSON request. Evidence assets are referenced by content_ref in that request.",
            "",
        ]
        for name, path, digest, byte_count in manifest_entries:
            manifest_lines.append(
                f"- `{path}` | component={name} | bytes={byte_count} | sha256={digest}"
            )
        self._review_manifest_file.write_text(
            "\n".join(manifest_lines) + "\n", encoding="utf-8"
        )
        read_call_budget = 1 + len(manifest_entries)
        response_schema_json = bundle.provider_response_schema_json
        policy = NATIVE_CLAUDE_SYSTEM_POLICY
        boundary_evidence_withheld = any(
            item.get("kind") == PROVIDER_INPUT_BOUNDARY_EVIDENCE_KIND
            for item in bundle.document["evidence_manifest"]
        )
        if boundary_evidence_withheld:
            directive = (
                f"Read {self._review_manifest_file} exactly once, then every listed file "
                "exactly once in order before using additional Read calls for repository "
                "paths required by the provider-input boundary notice. Review the "
                "reconstructed native request and current read-only repository snapshot; "
                "return only the schema-bound JSON result."
            )
        else:
            directive = (
                f"Read {self._review_manifest_file} exactly once, then every listed file "
                f"exactly once in order ({read_call_budget} Read calls total). Review the "
                "reconstructed native request and return only the schema-bound JSON result."
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
            "Read",
            "--allowedTools",
            "Read",
            "--disallowedTools",
            "Bash,Edit,Write,NotebookEdit,Grep,Glob",
            "--permission-mode",
            "dontAsk",
            "--setting-sources",
            "user",
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
        runtime_path = str(runtime_dir)
        runtime_prefix = f"dao-{self.name}-runtime-"
        random_suffix = runtime_dir.name.removeprefix(runtime_prefix)
        stable_runtime_path = str(
            runtime_dir.with_name(runtime_prefix + "_" * len(random_suffix))
        )

        def stable_paths(content: str) -> str:
            return content.replace(runtime_path, stable_runtime_path)

        components = [
            ProviderInputComponent(
                name,
                path.read_text(encoding="utf-8"),
            )
            for name, path, _digest, _byte_count in manifest_entries
        ]
        components.extend(
            (
                ProviderInputComponent(
                    "packet_manifest",
                    stable_paths(self._review_manifest_file.read_text(encoding="utf-8")),
                ),
                ProviderInputComponent("system_policy", policy),
                ProviderInputComponent("response_schema", response_schema_json),
                ProviderInputComponent("start_directive", stable_paths(directive)),
            )
        )
        self._native_request_id = bundle.bound_context.request_id
        return PreparedProviderInput(tuple(command), None, tuple(components))

    def extract_output(
        self, stdout: str, stderr: str, extra_files: dict[str, str]
    ) -> str:
        _ = stderr
        _ = extra_files
        envelope = _json_object(stdout or "", self.name)
        self.metadata = {
            key: envelope[key]
            for key in (
                "duration_api_ms",
                "num_turns",
                "total_cost_usd",
                "usage",
                "modelUsage",
                "permission_denials",
                "subtype",
            )
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
        if self._native_request_id is None:
            raise AgentOutputError("native Claude adapter has no bound request id")
        if result.get("request_id") != self._native_request_id:
            raise AgentOutputError("native Claude response request_id differs from request")
        try:
            return canonical_native_review_json(result)
        except NativeReviewContractError as exc:
            raise AgentOutputError(
                "native Claude response violates the local result schema",
                provider_data=result,
                technical_text=f"{exc.code.value}: {exc.detail}",
                orchestrator_diagnostic=exc.orchestrator_diagnostic,
            ) from exc

    def cleanup(self) -> None:
        super().cleanup()
        self._bound_review_harness = None
        self._review_manifest_file = None
        self._review_packet_files = ()
        self._native_evidence_files = ()
        self._native_request_id = None


def build_agent_registry(
    settings: dict[str, AgentSettings] | None = None,
) -> dict[str, AgentAdapter]:
    resolved = settings or default_agent_settings()
    if set(resolved) != {"codex", "claude"}:
        raise ValueError("agent settings must contain exactly codex and claude")
    return {
        "codex": NativeCodexAdapter(resolved["codex"]),
        "claude": NativeClaudeReviewAdapter(resolved["claude"]),
    }


AGENT_REGISTRY: dict[str, AgentAdapter] = build_agent_registry()
