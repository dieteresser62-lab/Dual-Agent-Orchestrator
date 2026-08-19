from __future__ import annotations

import hashlib
import json
import re
import shlex
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agent_config import AgentSettings, default_agent_settings


PROJECT_ROOT = Path(__file__).resolve().parent.parent
REVIEW_HARNESS = Path(__file__).resolve().parent / "review_harness.py"
CLAUDE_REVIEW_PACKET_CHUNK_CHARS = 24_000
CLAUDE_REVIEW_RESPONSE_MAX_CHARS = 12_000
ANTIGRAVITY_REVIEW_RESPONSE_MAX_CHARS = 12_000


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
    ) -> None:
        self.provider_text = provider_text or message
        self.provider_data = provider_data
        self.technical_text = technical_text or self.provider_text
        self.exit_code = exit_code
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


def _trim_after_done_marker(text: str) -> str:
    stripped = text.strip()
    fenced = re.fullmatch(
        r"(?:Here is the corrected output complying with the STATE-V3 CONTRACT:\s*)?"
        r"```(?:text)?[ \t]*\r?\n"
        r"(?P<body>REVIEWER:[\s\S]*?^STATUS: DONE)[ \t]*\r?\n"
        r"(?:[ \t]*\r?\n)*```",
        stripped,
        re.IGNORECASE | re.MULTILINE,
    )
    if fenced is not None:
        text = fenced.group("body").replace("\r\n", "\n")
    matches = list(re.finditer(r"(?m)^STATUS: DONE[ \t]*\r?$", text))
    if matches:
        return text[: matches[-1].end()].strip()
    # Claude Code 2.1.227 can leak its internal closing wrapper into the
    # structured `response` field. Normalize only that exact terminal shape;
    # arbitrary text after STATUS: DONE must continue to fail closed.
    wrapped = re.search(
        r"(?m)^STATUS: DONE(?=</response>[ \t]*(?:\r?\n</invoke>)?[ \t]*(?:\r?\n)?\Z)",
        text,
    )
    return text[: wrapped.end()].strip() if wrapped else text.strip()


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

    def bind_reviewer_workspace(self, source_root: Path, snapshot_root: Path) -> None: ...

    def extract_output(self, stdout: str, stderr: str, extra_files: dict[str, str]) -> str: ...

    def stream_filter(self, channel: str, line: str, state: dict[str, str | bool]) -> bool: ...

    def validate_process_output(self, stderr: str) -> None: ...

    def cleanup(self) -> None: ...


class _BaseAdapter:
    reviewer = False
    required_hosts: tuple[str, ...] = ()

    def __init__(self, settings: AgentSettings) -> None:
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


class CodexAdapter(_BaseAdapter):
    """Implementer adapter for Codex CLI with JSONL and a final-message file."""

    required_hosts = ("chatgpt.com", "api.openai.com")
    capability = CapabilitySpec(
        version_args=("--version",),
        help_args=("exec", "--help"),
        supported_version_patterns=(r"^codex-cli 0\.147\.\d+$",),
        required_help_flags=(
            "--model",
            "--sandbox",
            "--ephemeral",
            "--json",
            "--output-last-message",
        ),
    )

    def __init__(self, settings: AgentSettings | None = None) -> None:
        super().__init__(settings or default_agent_settings()["codex"])
        self._last_message_file: Path | None = None

    def build_command(self, prompt: str) -> tuple[list[str], bool]:
        _ = prompt
        runtime_dir = self._new_runtime_dir()
        self._last_message_file = runtime_dir / "last-message.txt"
        return (
            [
                self.cli_binary,
                "exec",
                "--model",
                self.model,
                "--config",
                f'model_reasoning_effort="{self.effort}"',
                "--skip-git-repo-check",
                "--ephemeral",
                "--sandbox",
                "workspace-write",
                "--color",
                "never",
                "--json",
                "--output-last-message",
                str(self._last_message_file),
                "-",
            ],
            True,
        )

    def extract_output(self, stdout: str, stderr: str, extra_files: dict[str, str]) -> str:
        _ = stderr
        _ = extra_files
        if self._last_message_file and self._last_message_file.is_file():
            content = self._last_message_file.read_text(encoding="utf-8").strip()
            if content:
                return content

        messages: list[str] = []
        for raw_line in (stdout or "").splitlines():
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            item = event.get("item")
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str) and text.strip():
                    messages.append(text.strip())
            direct = event.get("message")
            if isinstance(direct, str) and direct.strip():
                messages.append(direct.strip())
        return messages[-1] if messages else ""

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

    def cleanup(self) -> None:
        super().cleanup()
        self._last_message_file = None


class ClaudeAdapter(_BaseAdapter):
    """Read-only reviewer adapter using a strict JSON envelope and one harness."""

    reviewer = True
    required_hosts = ("api.anthropic.com",)
    capability = CapabilitySpec(
        version_args=("--version",),
        help_args=("--help",),
        supported_version_patterns=(r"^2\.1\.\d+ \(Claude Code\)$",),
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
        super().__init__(settings or default_agent_settings()["claude"])
        self.review_harness = review_harness.resolve()
        self._bound_review_harness: Path | None = None
        self._review_manifest_file: Path | None = None
        self._review_packet_files: tuple[Path, ...] = ()

    def bind_reviewer_workspace(self, source_root: Path, snapshot_root: Path) -> None:
        try:
            relative_harness = self.review_harness.relative_to(source_root.resolve())
        except ValueError:
            self._bound_review_harness = self.review_harness
        else:
            self._bound_review_harness = snapshot_root / relative_harness

    def build_command(self, prompt: str) -> tuple[list[str], bool]:
        runtime_dir = self._new_runtime_dir()
        chunks = _split_text_at_lines(prompt, CLAUDE_REVIEW_PACKET_CHUNK_CHARS)
        self._review_packet_files = tuple(
            runtime_dir / f"review-packet-{index:03d}.md"
            for index in range(1, len(chunks) + 1)
        )
        manifest_lines = [
            "# Review packet manifest",
            "",
            "Read every chunk below exactly once, in order. Their concatenation is the complete review packet.",
            "",
        ]
        for packet_file, chunk in zip(self._review_packet_files, chunks, strict=True):
            packet_file.write_text(chunk, encoding="utf-8")
            digest = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
            manifest_lines.append(
                f"- `{packet_file.name}` | chars={len(chunk)} | sha256={digest}"
            )
        self._review_manifest_file = runtime_dir / "review-manifest.md"
        self._review_manifest_file.write_text(
            "\n".join(manifest_lines) + "\n", encoding="utf-8"
        )
        read_call_budget = len(chunks) + 1
        policy = (
            "You are a concise read-only reviewer. Use only the supplied manifest and "
            "numbered review-packet chunks; "
            "do not explore the repository and do not run validation commands. Authoritative "
            "validation evidence is supplied by the orchestrator as either a legacy test "
            "snapshot or a fingerprint-bound v3 attestation. Use the "
            "available reasoning budget for adversarial implementation analysis: invariants, "
            "failure paths, security boundaries, resume/idempotency risks, and missing tests. "
            f"Use exactly {read_call_budget} Read calls: the manifest once, then every listed "
            "chunk once in order. Return only evidence, findings, decisions, and mandatory contract "
            f"markers, within {CLAUDE_REVIEW_RESPONSE_MAX_CHARS} characters."
        )
        response_schema = json.dumps(
            {
                "type": "object",
                "properties": {
                    "response": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": CLAUDE_REVIEW_RESPONSE_MAX_CHARS,
                    }
                },
                "required": ["response"],
                "additionalProperties": False,
            },
            separators=(",", ":"),
        )
        directive = (
            f"Read {self._review_manifest_file} exactly once, then read every listed packet "
            f"chunk exactly once in order ({read_call_budget} Read calls total), and follow "
            "the concatenated request. Do not run tests or the review harness; inspect the "
            "supplied validation evidence and focus on the implementation. Return the answer "
            "in the response field."
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
            response_schema,
            "--no-session-persistence",
            "--disable-slash-commands",
        ]
        if self.max_budget_usd is not None:
            command.extend(["--max-budget-usd", str(self.max_budget_usd)])
        command.append(directive)
        return command, False

    def build_capability_smoke_command(
        self,
        prompt: str,
        *,
        test_command: str,
        probe_path: str = "README.md",
        timeout: int = 1800,
    ) -> tuple[list[str], bool]:
        """Build an explicit opt-in diagnostic command; never used by normal reviews."""
        command, use_stdin = self.build_command(prompt)
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
        command[command.index("--tools") + 1] = "Bash,Read"
        command[command.index("--allowedTools") + 1] = (
            f"Read,Bash({harness_command})"
        )
        command[command.index("--disallowedTools") + 1] = (
            "Edit,Write,NotebookEdit,Grep,Glob"
        )
        command[command.index("--system-prompt") + 1] = (
            "This is an explicit adapter/version capability diagnostic, not a normal "
            "implementation review. Read only the supplied manifest and packet chunks, "
            "then run the exact allowlisted review harness once. Do not try alternatives."
        )
        command[-1] = (
            f"Read {self._review_manifest_file}, then every listed packet chunk exactly "
            "once. Run this exact capability diagnostic once and no alternative: "
            f"{harness_command}. Return the answer in the response field."
        )
        return command, use_stdin

    def extract_output(self, stdout: str, stderr: str, extra_files: dict[str, str]) -> str:
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
            subtype = str(envelope.get("subtype") or "").strip()
            detail = envelope.get("result") or envelope.get("error") or subtype
            if not detail:
                detail = json.dumps(envelope, ensure_ascii=False, sort_keys=True)[:1200]
            if "budget" in subtype.lower():
                raise AgentBudgetError(f"claude budget guard stopped the call: {detail}")
            raise AgentOutputError(
                f"claude returned is_error=true: {detail}",
                provider_text=str(detail),
                provider_data=envelope,
            )
        denials = envelope.get("permission_denials")
        if isinstance(denials, list) and denials:
            denial_detail = json.dumps(denials, ensure_ascii=False, sort_keys=True)
            raise AgentPermissionError(
                f"claude attempted {len(denials)} non-allowlisted tool call(s): "
                f"{denial_detail[:1200]}"
            )
        result: object = envelope.get("result")
        structured_output = envelope.get("structured_output")
        if isinstance(structured_output, dict):
            result = structured_output.get("response")
        elif isinstance(result, dict):
            result = result.get("response")
        elif isinstance(result, str):
            try:
                decoded_result = json.loads(result)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(decoded_result, dict):
                    result = decoded_result.get("response")
        if not isinstance(result, str) or not result.strip():
            raise AgentOutputError("claude JSON envelope has no non-empty result")
        return _trim_after_done_marker(result)

    def cleanup(self) -> None:
        super().cleanup()
        self._bound_review_harness = None
        self._review_manifest_file = None
        self._review_packet_files = ()


class AntigravityAdapter(_BaseAdapter):
    """Read-only Antigravity adapter with a private file-backed long prompt."""

    reviewer = True
    required_hosts = ("generativelanguage.googleapis.com",)
    capability = CapabilitySpec(
        version_args=("--version",),
        help_args=("--help",),
        supported_version_patterns=(r"^1\.1\.\d+$",),
        required_help_flags=(
            "--model",
            "--effort",
            "--output-format",
            "--print-timeout",
            "--sandbox",
            "--print",
            "--add-dir",
            "--log-file",
            "--json-schema",
        ),
    )

    def __init__(
        self,
        settings: AgentSettings | None = None,
        *,
        review_harness: Path = REVIEW_HARNESS,
    ) -> None:
        super().__init__(settings or default_agent_settings()["antigravity"])
        self.review_harness = review_harness.resolve()
        self._bound_review_harness: Path | None = None
        self._bound_reviewer_workspace: Path | None = None
        self._prompt_file: Path | None = None

    def bind_reviewer_workspace(self, source_root: Path, snapshot_root: Path) -> None:
        self._bound_reviewer_workspace = snapshot_root.resolve()
        try:
            relative_harness = self.review_harness.relative_to(source_root.resolve())
        except ValueError:
            self._bound_review_harness = self.review_harness
        else:
            self._bound_review_harness = snapshot_root / relative_harness

    def build_command(self, prompt: str) -> tuple[list[str], bool]:
        runtime_dir = self._new_runtime_dir()
        self._prompt_file = runtime_dir / "review-prompt.md"
        self._prompt_file.write_text(prompt, encoding="utf-8")
        log_file = runtime_dir / "antigravity.log"
        repository_instruction = (
            f"Use {self._bound_reviewer_workspace} as the repository root for every "
            "repository-relative search or read. "
            if self._bound_reviewer_workspace is not None
            else ""
        )
        directive = (
            f"Read the complete request from {self._prompt_file} and follow it. "
            f"{repository_instruction}"
            "The repository is read-only. Do not rerun full validation; inspect the supplied "
            "orchestrator validation evidence and spend the review budget on "
            "adversarial implementation analysis. Return only the requested contract in the "
            "response field: no headings, no repeated analysis, and no description of planned "
            "changes as if they were already implemented."
        )
        response_schema = json.dumps(
            {
                "type": "object",
                "properties": {
                    "response": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": ANTIGRAVITY_REVIEW_RESPONSE_MAX_CHARS,
                    }
                },
                "required": ["response"],
                "additionalProperties": False,
            },
            separators=(",", ":"),
        )
        command = [
            self.cli_binary,
            "--model",
            self.model,
            "--effort",
            self.effort,
            "--output-format",
            "json",
            "--print-timeout",
            f"{self.timeout}s",
            "--sandbox",
            "--dangerously-skip-permissions",
            "--disable-slash-commands",
            "--add-dir",
            str(runtime_dir),
        ]
        if self._bound_reviewer_workspace is not None:
            command.extend(["--add-dir", str(self._bound_reviewer_workspace)])
        command.extend(
            [
                "--log-file",
                str(log_file),
                "--json-schema",
                response_schema,
                "--print",
                directive,
            ]
        )
        return command, False

    def build_capability_smoke_command(
        self,
        prompt: str,
        *,
        test_command: str,
        probe_path: str = "README.md",
        timeout: int = 1800,
    ) -> tuple[list[str], bool]:
        """Build an explicit opt-in diagnostic command; never used by normal reviews."""
        command, use_stdin = self.build_command(prompt)
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
        command[-1] = (
            f"Read the complete diagnostic request from {self._prompt_file}. The repository "
            "is read-only. This is an explicit adapter/version capability diagnostic, not "
            "a normal review. Run this exact harness command once and no alternative: "
            f"{harness_command}."
        )
        return command, use_stdin

    def extract_output(self, stdout: str, stderr: str, extra_files: dict[str, str]) -> str:
        _ = extra_files
        envelope = _json_object(stdout or "", self.name)
        self.metadata = {
            key: envelope[key]
            for key in ("conversation_id", "duration_seconds", "num_turns", "usage")
            if key in envelope
        }
        if envelope.get("status") != "SUCCESS":
            status = str(envelope.get("status") or "UNKNOWN")
            error = envelope.get("error")
            technical_status = status.upper() in {
                "RESOURCE_EXHAUSTED",
                "RATE_LIMITED",
                "UNAVAILABLE",
                "NETWORK_ERROR",
            }
            response_detail = envelope.get("response") if technical_status else None
            detail = str(error or stderr or response_detail or f"status={status}")[-1200:]
            diagnostic = {
                key: envelope[key]
                for key in (
                    "status",
                    "error",
                    "code",
                    "conversation_id",
                    "retry_after",
                    "retry_after_seconds",
                    "reset_at",
                )
                if key in envelope
            }
            raise AgentOutputError(
                f"antigravity returned non-success status: {detail}",
                provider_text=detail,
                provider_data=diagnostic,
                technical_text=detail,
            )
        response = envelope.get("response")
        if isinstance(response, str):
            try:
                decoded = json.loads(response)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(decoded, dict):
                    response = decoded.get("response")
        elif isinstance(response, dict):
            response = response.get("response")
        if response is None and isinstance(envelope.get("structured_output"), dict):
            response = envelope["structured_output"].get("response")
        if not isinstance(response, str) or not response.strip():
            raise AgentOutputError("antigravity JSON envelope has no non-empty response")
        return _trim_after_done_marker(response)

    def cleanup(self) -> None:
        super().cleanup()
        self._bound_review_harness = None
        self._bound_reviewer_workspace = None
        self._prompt_file = None


def build_agent_registry(
    settings: dict[str, AgentSettings] | None = None,
) -> dict[str, AgentAdapter]:
    resolved = settings or default_agent_settings()
    if set(resolved) != {"codex", "claude", "antigravity"}:
        raise ValueError("agent settings must contain exactly codex, claude, and antigravity")
    return {
        "codex": CodexAdapter(resolved["codex"]),
        "claude": ClaudeAdapter(resolved["claude"]),
        "antigravity": AntigravityAdapter(resolved["antigravity"]),
    }


AGENT_REGISTRY: dict[str, AgentAdapter] = build_agent_registry()
