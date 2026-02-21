from __future__ import annotations

import os
import logging
import queue
import re
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TextIO

from agent_adapters import AGENT_REGISTRY, AgentAdapter

TEST_OUTPUT_LIMIT = 7000
ERROR_TRUNCATION_LIMIT = 1200
logger = logging.getLogger(__name__)


class QuotaReachedError(RuntimeError):
    def __init__(self, agent_key: str, detail: str) -> None:
        self.agent_key = agent_key
        message = f"{agent_key} quota/rate limit reached: {detail}"
        super().__init__(message)


@dataclass
class OrchestratorConfig:
    dry_run: bool = False
    agent_output_mode: str = "summary"
    agent_output_max_chars: int = 1800
    agent_live_stream: bool = False
    agent_live_stream_mode: str = "compact"
    agent_live_stream_channels: str = "both"
    allow_fallback_to_gemini: bool = False
    claude_quota_reached: bool = False


@dataclass
class StreamResult:
    returncode: int
    stdout: str
    stderr: str


def can_resolve_host(hostname: str) -> bool:
    try:
        socket.getaddrinfo(hostname, None)
        return True
    except socket.gaierror:
        return False


def run_local_command(args: list[str], timeout: int = 20) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode, (result.stdout or ""), (result.stderr or "")
    except Exception as exc:
        return 1, "", str(exc)


def repo_snapshot(max_diff_chars: int) -> str:
    if shutil.which("git") is None:
        return "Git is not available in PATH."

    status_rc, status_out, status_err = run_local_command(["git", "status", "--short"])
    diffstat_rc, diffstat_out, diffstat_err = run_local_command(["git", "diff", "--stat"])
    diff_rc, diff_out, diff_err = run_local_command(["git", "diff"])

    sections: list[str] = []
    sections.append("=== git status --short ===")
    sections.append((status_out if status_rc == 0 else status_err).strip() or "(empty)")
    sections.append("\n=== git diff --stat ===")
    sections.append((diffstat_out if diffstat_rc == 0 else diffstat_err).strip() or "(empty)")
    sections.append("\n=== git diff (possibly truncated) ===")

    raw_diff = (diff_out if diff_rc == 0 else diff_err).strip()
    if not raw_diff:
        sections.append("(empty)")
    else:
        sections.append(raw_diff[:max_diff_chars])
        if len(raw_diff) > max_diff_chars:
            sections.append("\n...[truncated]")

    return "\n".join(sections).strip()


def run_tests_snapshot(
    *,
    config: OrchestratorConfig,
    test_command: str,
    test_timeout_seconds: int,
    shorten: Callable[[str | None, int], str],
) -> tuple[int, str]:
    command_text = (test_command or "").strip()
    if not command_text:
        return 0, "Exit code: 0\n[skip] No test command configured."
    if config.dry_run:
        return 0, f"Exit code: 0\n[dry-run] '{command_text}' simulated."
    try:
        result = subprocess.run(
            command_text,
            capture_output=True,
            text=True,
            timeout=test_timeout_seconds,
            check=False,
            shell=True,
        )
        rc = result.returncode
        stdout = result.stdout or ""
        stderr = result.stderr or ""
    except Exception as exc:
        rc = 1
        stdout = ""
        stderr = str(exc)
    combined = (stdout + "\n" + stderr).strip()
    return rc, f"Exit code: {rc}\n{shorten(combined, TEST_OUTPUT_LIMIT)}"


def build_dry_run_agent_output(agent_key: str, prompt: str) -> str:
    lines = [
        f"# Dry Run Output ({agent_key})",
        "",
        "This response was simulated by the orchestrator.",
    ]
    if "CODEX_APPROVAL:" in prompt:
        lines.append("CODEX_APPROVAL: YES")
    if "OPEN_FINDINGS:" in prompt:
        lines.append("OPEN_FINDINGS: NONE")
    if "CLAUDE_APPROVAL:" in prompt:
        lines.append("CLAUDE_APPROVAL: YES")
    if "IMPLEMENTATION_READY:" in prompt:
        lines.append("IMPLEMENTATION_READY: YES")
    lines.append("STATUS: DONE")
    return "\n".join(lines)


def print_agent_output(
    agent_key: str,
    log_path: Path,
    attempt: int,
    output: str,
    *,
    config: OrchestratorConfig,
    shorten: Callable[[str | None, int], str],
) -> None:
    if config.agent_output_mode == "none":
        return

    logger.info("[AGENT] %s attempt=%s log=%s", agent_key, attempt, log_path)
    if config.agent_live_stream:
        logger.info("[AGENT] live stream was enabled; final response saved to log.")
        return
    if config.agent_output_mode == "full":
        logger.info("%s", output.strip())
        return

    logger.info("%s", shorten(output, config.agent_output_max_chars))


def run_agent(
    adapter: AgentAdapter,
    prompt: str,
    *,
    config: OrchestratorConfig,
    shorten: Callable[[str | None, int], str],
) -> str:
    agent_key = adapter.name
    if config.dry_run:
        return build_dry_run_agent_output(agent_key, prompt)

    command_parts, use_stdin_prompt = adapter.build_command(prompt)
    env = os.environ.copy()
    env.update(adapter.env)
    timeout_seconds = adapter.timeout
    extra_files: dict[str, str] = {}

    try:
        try:
            if config.agent_live_stream:
                process = subprocess.Popen(
                    command_parts,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env,
                    bufsize=1,
                )
                assert process.stdin is not None
                assert process.stdout is not None
                assert process.stderr is not None

                if use_stdin_prompt:
                    process.stdin.write(prompt)
                process.stdin.close()

                stream_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
                stdout_chunks: list[str] = []
                stderr_chunks: list[str] = []
                start = time.monotonic()
                stream_state: dict[str, str | bool] = {
                    "skip_prompt_echo": False,
                    "last_emitted_line": "",
                }

                def read_stream(stream: TextIO, channel: str) -> None:
                    try:
                        while True:
                            line = stream.readline()
                            if line == "":
                                break
                            stream_queue.put((channel, line))
                    finally:
                        stream_queue.put((channel, None))
                        try:
                            stream.close()
                        except Exception:
                            pass

                threads = [
                    threading.Thread(
                        target=read_stream,
                        args=(process.stdout, "stdout"),
                        daemon=True,
                    ),
                    threading.Thread(
                        target=read_stream,
                        args=(process.stderr, "stderr"),
                        daemon=True,
                    ),
                ]
                for thread in threads:
                    thread.start()

                completed_channels: set[str] = set()
                while len(completed_channels) < 2:
                    if time.monotonic() - start > timeout_seconds:
                        process.kill()
                        raise subprocess.TimeoutExpired(command_parts, timeout_seconds)
                    try:
                        channel, line = stream_queue.get(timeout=0.2)
                    except queue.Empty:
                        continue

                    if line is None:
                        completed_channels.add(channel)
                        continue
                    if channel == "stdout":
                        stdout_chunks.append(line)
                    else:
                        stderr_chunks.append(line)

                    if config.agent_live_stream_channels == "stdout" and channel != "stdout":
                        continue
                    if config.agent_live_stream_channels == "stderr" and channel != "stderr":
                        continue
                    if config.agent_live_stream_mode == "full" or adapter.stream_filter(
                        channel, line, stream_state
                    ):
                        logger.info("[%s:%s] %s", agent_key, channel, line.rstrip())

                for thread in threads:
                    thread.join(timeout=1)
                process.wait(timeout=5)
                result = StreamResult(
                    process.returncode if process.returncode is not None else 1,
                    "".join(stdout_chunks),
                    "".join(stderr_chunks),
                )
            else:
                result = subprocess.run(
                    command_parts,
                    input=(prompt if use_stdin_prompt else None),
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=timeout_seconds,
                    check=False,
                )
        finally:
            try:
                adapter.cleanup()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Adapter cleanup failed for %s: %s", agent_key, exc)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{agent_key} timed out after {timeout_seconds}s.")

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    output = adapter.extract_output(stdout, stderr, extra_files)
    if result.returncode != 0:
        error_text = shorten(
            stderr or output or "Unknown CLI error without output.",
            ERROR_TRUNCATION_LIMIT,
        )
        raise RuntimeError(f"{agent_key} failed: {error_text}")
    if not output:
        raise RuntimeError(f"{agent_key} returned empty output.")
    return output


def is_quota_or_rate_limit_error(text: str) -> bool:
    raw = (text or "").lower()
    markers = (
        "quota",
        "hit your limit",
        "you've hit your limit",
        "usage cap",
        "rate limit",
        "too many requests",
        "429",
        "insufficient credits",
        "credit balance is too low",
        "usage limit",
        "resource exhausted",
    )
    return any(marker in raw for marker in markers)


def compute_retry_backoff_seconds(error_text: str, attempt: int) -> int:
    exponential = min(30, 2 * (2 ** max(0, attempt - 1)))
    if is_quota_or_rate_limit_error(error_text):
        return max(10, exponential)
    return exponential


def run_agent_checked(
    *,
    agent_key: str,
    prompt: str,
    log_prefix: str,
    max_retries: int,
    required_flags: list[str] | None,
    output_validator: Callable[[str], str | None] | None,
    config: OrchestratorConfig,
    agents: dict[str, AgentAdapter],
    log_dir: Path,
    write_file: Callable[[Path, str], None],
    shorten: Callable[[str | None, int], str],
    parse_flag: Callable[[str, str], str | None],
    validate_done_marker: Callable[[str], bool],
) -> str:
    required_flags = required_flags or []
    errors: list[str] = []
    effective_agent_key = agent_key

    if (
        effective_agent_key == "claude"
        and config.allow_fallback_to_gemini
        and config.claude_quota_reached
    ):
        effective_agent_key = "gemini"
        logger.info("Claude quota previously exceeded - using Gemini directly.")

    def validate_output_contract(output: str) -> str | None:
        if not validate_done_marker(output):
            return "missing required final completion marker 'STATUS: DONE'"
        missing_flags = [flag for flag in required_flags if parse_flag(output, flag) is None]
        if missing_flags:
            return f"missing required flags: {', '.join(missing_flags)}"
        if output_validator:
            validation_error = output_validator(output)
            if validation_error:
                return validation_error
        return None

    for attempt in range(1, max_retries + 2):
        has_next_attempt = attempt < (max_retries + 1)
        prompt_to_send = prompt
        if attempt > 1:
            prompt_to_send = (
                f"{prompt}\n\n"
                "Your last response was formally unacceptable. "
                "Fix only the issues listed below.\n"
                f"Error context:\n{chr(10).join(errors[-2:])}\n"
            )

        try:
            output = run_agent(
                agents[effective_agent_key],
                prompt_to_send,
                config=config,
                shorten=shorten,
            )
            log_path = log_dir / f"{log_prefix}.attempt-{attempt}.log"
            write_file(log_path, output)
            print_agent_output(
                effective_agent_key, log_path, attempt, output, config=config, shorten=shorten
            )
            validation_error = validate_output_contract(output)
            if validation_error:
                errors.append(validation_error)
            else:
                return output
        except Exception as exc:
            error_text = shorten(str(exc), ERROR_TRUNCATION_LIMIT)
            errors.append(error_text)

            if (
                config.allow_fallback_to_gemini
                and effective_agent_key == "claude"
                and is_quota_or_rate_limit_error(error_text)
            ):
                logger.warning("Claude quota/rate limit detected. Attempting Gemini fallback.")
                try:
                    fallback_output = run_agent(
                        agents["gemini"],
                        prompt_to_send,
                        config=config,
                        shorten=shorten,
                    )
                    fallback_log_path = log_dir / f"{log_prefix}.attempt-{attempt}.gemini-fallback.log"
                    write_file(fallback_log_path, fallback_output)
                    logger.info(
                        "[AGENT] fallback from=claude to=gemini attempt=%s log=%s",
                        attempt,
                        fallback_log_path,
                    )
                    if config.agent_output_mode != "none":
                        print_agent_output(
                            "gemini",
                            fallback_log_path,
                            attempt,
                            fallback_output,
                            config=config,
                            shorten=shorten,
                        )

                    validation_error = validate_output_contract(fallback_output)
                    if validation_error:
                        errors.append(f"gemini fallback invalid output: {validation_error}")
                    else:
                        config.claude_quota_reached = True
                        return fallback_output
                except Exception as fallback_exc:
                    fallback_error = shorten(str(fallback_exc), ERROR_TRUNCATION_LIMIT)
                    errors.append(f"gemini fallback failed: {fallback_error}")
                    if is_quota_or_rate_limit_error(fallback_error):
                        config.claude_quota_reached = True
                        raise QuotaReachedError("gemini", fallback_error) from fallback_exc

            if is_quota_or_rate_limit_error(error_text):
                if effective_agent_key == "claude" and config.allow_fallback_to_gemini:
                    raise QuotaReachedError("claude", error_text) from exc
                raise QuotaReachedError(effective_agent_key, error_text) from exc

        if has_next_attempt:
            delay_seconds = compute_retry_backoff_seconds(errors[-1], attempt)
            logger.info(
                "[RETRY] %s attempt=%s failed. Waiting %ss before retry.",
                effective_agent_key,
                attempt,
                delay_seconds,
            )
            time.sleep(delay_seconds)

    raise RuntimeError(
        f"{effective_agent_key} did not produce valid output after {max_retries + 1} attempts: "
        f"{shorten(chr(10).join(errors), ERROR_TRUNCATION_LIMIT)}"
    )


def preflight(required_agents: list[str], strict: bool, agents: dict[str, AgentAdapter]) -> bool:
    ok = True
    logger.info("Preflight: checking CLI binaries and DNS resolution.")

    for agent_key in required_agents:
        agent_config = agents[agent_key]
        cli_binary = agent_config.cli_binary
        if shutil.which(cli_binary) is None:
            logger.error("Missing CLI binary for '%s': %s", agent_key, cli_binary)
            ok = False

    missing_hosts: list[tuple[str, str]] = []
    for agent_key in required_agents:
        for host in agents[agent_key].required_hosts:
            if not can_resolve_host(host):
                missing_hosts.append((agent_key, host))

    if missing_hosts:
        logger.warning("DNS resolution failed for:")
        for agent_key, host in missing_hosts:
            logger.warning("  - %s: %s", agent_key, host)
        if strict:
            ok = False

    if ok:
        logger.info("Preflight result: OK")
    else:
        logger.error("Preflight result: FAILED")
    return ok


def collect_file_snapshots(changed_files: list[str], max_lines: int, max_files: int) -> str:
    def is_plausible_path(value: str) -> bool:
        if not value or value.startswith("#"):
            return False
        if value.startswith("..."):
            return False
        if re.search(r"\s", value):
            return False
        return bool(re.match(r"^[^\s|:][^|:]*$", value))

    parts: list[str] = []
    seen: set[str] = set()
    selected = 0
    for raw in changed_files:
        path_text = raw.strip()
        if not is_plausible_path(path_text):
            continue
        if path_text in seen:
            continue
        seen.add(path_text)
        if selected >= max_files:
            break

        selected += 1
        path = Path(path_text)
        parts.append(f"### {path_text}")
        if not path.exists():
            parts.append("[missing] File does not exist.")
            parts.append("")
            continue
        if path.is_dir():
            parts.append("[skip] Path is a directory.")
            parts.append("")
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception as exc:  # pragma: no cover - defensive
            parts.append(f"[error] Could not read file: {exc}")
            parts.append("")
            continue
        truncated = lines[:max_lines]
        parts.append("\n".join(truncated) if truncated else "(empty)")
        if len(lines) > max_lines:
            parts.append(f"...[truncated to {max_lines} lines]")
        parts.append("")

    body = "\n".join(parts).strip() or "(empty)"
    return f"<<<FILES_BEGIN>>>\n{body}\n<<<FILES_END>>>"
