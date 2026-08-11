from __future__ import annotations

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


class AgentOutputError(RuntimeError):
    """Raised when a CLI returns an invalid or explicitly failed output envelope."""


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
    marker = "STATUS: DONE"
    idx = text.find(marker)
    if idx < 0:
        return text.strip()
    return text[: idx + len(marker)].strip()


def _json_object(text: str, role: str) -> dict[str, object]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AgentOutputError(f"{role} returned invalid JSON: {exc}") from exc
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
        supported_version_patterns=(r"^codex-cli 0\.147\.0$",),
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
        supported_version_patterns=(
            r"^2\.1\.227 \(Claude Code\)$",
        ),
        required_help_flags=(
            "--model",
            "--effort",
            "--tools",
            "--allowedTools",
            "--permission-mode",
            "--output-format",
            "--no-session-persistence",
            "--safe-mode",
            "--system-prompt",
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

    def bind_reviewer_workspace(self, source_root: Path, snapshot_root: Path) -> None:
        try:
            relative_harness = self.review_harness.relative_to(source_root.resolve())
        except ValueError:
            self._bound_review_harness = self.review_harness
        else:
            self._bound_review_harness = snapshot_root / relative_harness

    @property
    def review_harness_command(self) -> str:
        harness = self._bound_review_harness or self.review_harness
        return shlex.join([sys.executable, str(harness)])

    def build_command(self, prompt: str) -> tuple[list[str], bool]:
        _ = prompt
        self._new_runtime_dir()
        harness_command = self.review_harness_command
        policy = (
            "This is a read-only review. If validation is requested, run exactly once and "
            f"without wrappers or redirections: {harness_command}. "
            "Do not try alternative Bash commands when it is denied."
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
            "Bash,Read,Grep,Glob",
            "--allowedTools",
            f"Read,Grep,Glob,Bash({harness_command})",
            "--disallowedTools",
            "Edit,Write,NotebookEdit",
            "--permission-mode",
            "dontAsk",
            "--safe-mode",
            "--system-prompt",
            policy,
            "--no-session-persistence",
            "--disable-slash-commands",
        ]
        if self.max_budget_usd is not None:
            command.extend(["--max-budget-usd", str(self.max_budget_usd)])
        return command, True

    def extract_output(self, stdout: str, stderr: str, extra_files: dict[str, str]) -> str:
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
            subtype = str(envelope.get("subtype") or "").strip()
            detail = envelope.get("result") or envelope.get("error") or subtype
            if not detail:
                detail = json.dumps(envelope, ensure_ascii=False, sort_keys=True)[:1200]
            if "budget" in subtype.lower():
                raise AgentBudgetError(f"claude budget guard stopped the call: {detail}")
            raise AgentOutputError(f"claude returned is_error=true: {detail}")
        denials = envelope.get("permission_denials")
        if isinstance(denials, list) and denials:
            denial_detail = json.dumps(denials, ensure_ascii=False, sort_keys=True)
            raise AgentPermissionError(
                f"claude attempted {len(denials)} non-allowlisted tool call(s): "
                f"{denial_detail[:1200]}"
            )
        result = envelope.get("result")
        if not isinstance(result, str) or not result.strip():
            raise AgentOutputError("claude JSON envelope has no non-empty result")
        return _trim_after_done_marker(result)

    def cleanup(self) -> None:
        super().cleanup()
        self._bound_review_harness = None


class AntigravityAdapter(_BaseAdapter):
    """Read-only Antigravity adapter with a private file-backed long prompt."""

    reviewer = True
    required_hosts = ("generativelanguage.googleapis.com",)
    capability = CapabilitySpec(
        version_args=("--version",),
        help_args=("--help",),
        supported_version_patterns=(r"^1\.1\.11$", r"^1\.1\.12$"),
        required_help_flags=(
            "--model",
            "--effort",
            "--output-format",
            "--print-timeout",
            "--sandbox",
            "--print",
            "--add-dir",
            "--log-file",
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
        self._prompt_file: Path | None = None

    def bind_reviewer_workspace(self, source_root: Path, snapshot_root: Path) -> None:
        try:
            relative_harness = self.review_harness.relative_to(source_root.resolve())
        except ValueError:
            self._bound_review_harness = self.review_harness
        else:
            self._bound_review_harness = snapshot_root / relative_harness

    @property
    def review_harness_command(self) -> str:
        harness = self._bound_review_harness or self.review_harness
        return shlex.join([sys.executable, str(harness)])

    def build_command(self, prompt: str) -> tuple[list[str], bool]:
        runtime_dir = self._new_runtime_dir()
        self._prompt_file = runtime_dir / "review-prompt.md"
        self._prompt_file.write_text(prompt, encoding="utf-8")
        log_file = runtime_dir / "antigravity.log"
        directive = (
            f"Read the complete request from {self._prompt_file} and follow it. "
            "The repository is read-only. If validation is requested, run exactly once and "
            f"without wrappers or redirections: {self.review_harness_command}."
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
            "--log-file",
            str(log_file),
            "--print",
            directive,
        ]
        return command, False

    def extract_output(self, stdout: str, stderr: str, extra_files: dict[str, str]) -> str:
        _ = stderr
        _ = extra_files
        envelope = _json_object(stdout or "", self.name)
        self.metadata = {
            key: envelope[key]
            for key in ("conversation_id", "duration_seconds", "num_turns", "usage")
            if key in envelope
        }
        if envelope.get("status") != "SUCCESS":
            detail = envelope.get("response") or envelope.get("error") or "unknown JSON error"
            raise AgentOutputError(f"antigravity returned non-success status: {detail}")
        response = envelope.get("response")
        if not isinstance(response, str) or not response.strip():
            raise AgentOutputError("antigravity JSON envelope has no non-empty response")
        return _trim_after_done_marker(response)

    def cleanup(self) -> None:
        super().cleanup()
        self._bound_review_harness = None
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
