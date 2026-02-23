from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Protocol


class AgentAdapter(Protocol):
    """Minimal adapter contract for each agent CLI backend."""

    name: str
    cli_binary: str
    timeout: int
    env: dict[str, str]
    required_hosts: tuple[str, ...]

    def build_command(self, prompt: str) -> tuple[list[str], bool]:
        """Build CLI command and whether prompt should be piped to stdin."""

    def extract_output(self, stdout: str, stderr: str, extra_files: dict[str, str]) -> str:
        """Extract final response text from raw process output."""

    def stream_filter(self, channel: str, line: str, state: dict[str, str | bool]) -> bool:
        """Return True when a live stream line should be emitted."""

    def cleanup(self) -> None:
        """Cleanup temporary resources created by the adapter."""


class CodexAdapter:
    """Adapter for Codex CLI with compact streaming and temp-file final output."""

    name = "codex"
    cli_binary = "codex"
    timeout = 1800
    env = {"NO_COLOR": "1"}
    required_hosts = ("chatgpt.com", "api.openai.com")

    _base_command = [
        "codex",
        "exec",
        # Orchestrator enforces its own git-safety checks before invoking the CLI.
        "--skip-git-repo-check",
        # Keep filesystem access constrained to repository and temp directories.
        "--sandbox",
        "workspace-write",
        "--color",
        "never",
    ]

    def __init__(self) -> None:
        self._last_message_file: str | None = None

    def build_command(self, prompt: str) -> tuple[list[str], bool]:
        self.cleanup()
        # Persist the final assistant message to a temp file because stream logs can be noisy.
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".txt",
            prefix=f"{self.name}-last-message-",
            delete=False,
        ) as tmp:
            self._last_message_file = tmp.name
        cmd = list(self._base_command) + ["--output-last-message", str(self._last_message_file)]
        return cmd, True

    def extract_output(self, stdout: str, stderr: str, extra_files: dict[str, str]) -> str:
        _ = stderr
        _ = extra_files
        output = (stdout or "").strip()
        if self._last_message_file:
            path = Path(self._last_message_file)
            if path.exists():
                # Prefer the explicit "last assistant message" file over mixed stream output.
                content = path.read_text(encoding="utf-8").strip()
                if content:
                    output = content
        return output

    def stream_filter(self, channel: str, line: str, state: dict[str, str | bool]) -> bool:
        txt = line.strip()
        if not txt:
            return False

        if channel == "stderr":
            if txt == "user":
                # Codex echoes the prompt; suppress it to keep live logs signal-focused.
                state["skip_prompt_echo"] = True
                return False
            if bool(state.get("skip_prompt_echo", False)):
                if txt.startswith("mcp startup:") or txt in {"thinking", "codex", "exec"}:
                    state["skip_prompt_echo"] = False
                else:
                    return False

            noisy_prefixes = (
                # Suppress boilerplate CLI metadata and diff streams in compact mode.
                "Reading prompt from stdin...",
                "OpenAI Codex ",
                "workdir:",
                "model:",
                "provider:",
                "approval:",
                "sandbox:",
                "reasoning effort:",
                "reasoning summaries:",
                "session id:",
                "mcp startup:",
                "--------",
                "diff --git ",
                "index ",
                "--- a/",
                "+++ b/",
                "@@",
                "deleted file mode ",
                "new file mode ",
                "file update:",
                "apply_patch(",
                "/bin/bash -lc ",
                "succeeded in ",
                "tokens used",
            )
            if txt.startswith(noisy_prefixes):
                return False
            if txt.startswith("202") and "ERROR codex_core::rollout::list" in txt:
                return False
            if txt.startswith(("+", "-")) and len(txt) > 2:
                return False

        last_line = str(state.get("last_emitted_line", ""))
        if txt == last_line:
            return False
        state["last_emitted_line"] = txt
        return True

    def cleanup(self) -> None:
        if not self._last_message_file:
            return
        path = Path(self._last_message_file)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        self._last_message_file = None


class ClaudeAdapter:
    """Adapter for Claude CLI configured for stateless plain-text responses."""

    name = "claude"
    cli_binary = "claude"
    timeout = 1800
    env = {"NO_COLOR": "1"}
    required_hosts = ("api.anthropic.com",)

    def build_command(self, prompt: str) -> tuple[list[str], bool]:
        _ = prompt
        return (
            [
                "claude",
                "-p",
                "--output-format",
                "text",
                "--no-session-persistence",
                "--model",
                "opus",
            ],
            True,
        )

    def extract_output(self, stdout: str, stderr: str, extra_files: dict[str, str]) -> str:
        _ = stderr
        _ = extra_files
        return (stdout or "").strip()

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

    def cleanup(self) -> None:
        return None


class GeminiAdapter:
    """Adapter for Gemini CLI using stdin prompts and plain stdout output."""

    name = "gemini"
    cli_binary = "gemini"
    timeout = 1800
    env = {"NO_COLOR": "1"}
    required_hosts = ("generativelanguage.googleapis.com",)

    def build_command(self, prompt: str) -> tuple[list[str], bool]:
        _ = prompt
        return ["gemini"], True

    def extract_output(self, stdout: str, stderr: str, extra_files: dict[str, str]) -> str:
        _ = stderr
        _ = extra_files
        return (stdout or "").strip()

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

    def cleanup(self) -> None:
        return None


AGENT_REGISTRY: dict[str, AgentAdapter] = {
    # Singleton adapter instances avoid repeated CLI configuration setup.
    "codex": CodexAdapter(),
    "claude": ClaudeAdapter(),
    "gemini": GeminiAdapter(),
}
