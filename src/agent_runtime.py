from __future__ import annotations

import json
import hashlib
import logging
import math
import os
import queue
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, TextIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agent_adapters import (
    AGENT_REGISTRY,
    AgentAdapter,
    AgentBudgetError,
    AgentPermissionError,
    AgentOutputError,
    NativeCodexExecutionBoundary,
)
from path_policy import PathPolicyError, resolve_repository_path
from repo_changes import RepositoryChanges
from contracts import CodexContractResult, ContractResult, ValidationAttestation
from native_codex_contract import (
    NativeCodexContractError,
    NativeCodexErrorCode,
    parse_bound_native_codex_contract_result,
    validate_native_codex_document,
)
from native_codex_request import (
    NativeCodexRequestBundle,
    NativeCodexRequestError,
    validate_native_codex_provider_response,
)
from native_review_contract import (
    NativeReviewContext,
    NativeReviewContractError,
    NativeReviewErrorCode,
    is_retryable_native_review_form_error,
    parse_bound_native_contract_result,
    validate_native_review_disposition_budget,
    validate_native_review_document,
)
from native_review_request import (
    NativeReviewRequestBundle,
    NativeReviewRequestError,
    NativeReviewRequestErrorCode,
    validate_native_review_provider_response,
)
from native_provider_schema import (
    NativeProviderSchemaError,
    assert_provider_capabilities,
)
from validation_matrix import ValidationMatrixRunner, ValidationRequest
from workflow_state import AgentFailureKind
from artifact_models import ProviderUsagePayload
from provider_input_budget import (
    PROVIDER_OPERATIONS,
    PreparedProviderInput,
    ProviderInputComponent,
    ProviderInputBudgetError,
    ProviderInputBudgetExceeded,
    ProviderInputBudgetPolicy,
    ProviderInputMeasurement,
    default_provider_input_budget_policy,
    measure_provider_input,
)
from orchestrator_diagnostics import (
    OrchestratorDiagnostic,
    STRUCTURED_OUTPUT_RETRY_EXHAUSTED_SUBTYPE,
)

TEST_OUTPUT_LIMIT = 7000
ERROR_TRUNCATION_LIMIT = 1200
logger = logging.getLogger(__name__)
REVIEW_SNAPSHOT_EXCLUDED_ROOTS = frozenset(
    {
        ".git",
        ".orchestrator",
        ".pytest_cache",
        ".tmp",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "release-archive",
        "scratch",
        "tmp",
    }
)
REVIEW_SNAPSHOT_AUDIT_PATH_PATTERNS = (
    re.compile(
        r"^docs/internal/[a-z0-9]+(?:-[a-z0-9]+)*-review-[0-9a-f]{8}\.md$"
    ),
    re.compile(
        r"^docs/internal/slice-[a-z0-9]+(?:-[a-z0-9]+)*-"
        r"[0-9]{2,}-[a-z0-9]+(?:-[a-z0-9]+)*\.md$"
    ),
)


@dataclass(frozen=True)
class QuotaReset:
    reset_at_utc: datetime
    parse_path: str
    source_timezone: str

    def __post_init__(self) -> None:
        if self.reset_at_utc.tzinfo is None or self.reset_at_utc.utcoffset() is None:
            raise ValueError("quota reset timestamp must be timezone-aware")
        object.__setattr__(self, "reset_at_utc", self.reset_at_utc.astimezone(timezone.utc))
        if not self.parse_path.strip() or not self.source_timezone.strip():
            raise ValueError("quota reset evidence must name parse path and timezone")


class AgentInvocationError(RuntimeError):
    """One classified role invocation failure; never authorizes an internal retry."""

    def __init__(
        self,
        *,
        agent_key: str,
        kind: AgentFailureKind,
        invocation_id: str,
        provider_text: str,
        received_at: datetime,
        exit_code: int | None = None,
        quota_reset: QuotaReset | None = None,
        provider_data: Mapping[str, object] | None = None,
        technical_text: str | None = None,
        orchestrator_diagnostic: OrchestratorDiagnostic | None = None,
    ) -> None:
        if orchestrator_diagnostic is not None and not isinstance(
            orchestrator_diagnostic, OrchestratorDiagnostic
        ):
            raise TypeError("orchestrator diagnostic must be a closed enum member")
        self.agent_key = agent_key
        self.kind = kind
        self.invocation_id = invocation_id
        self.provider_text = provider_text
        self.received_at = received_at.astimezone(timezone.utc)
        self.process_exit_code = exit_code
        self.quota_reset = quota_reset
        self.provider_data = dict(provider_data) if provider_data is not None else None
        self.technical_text = technical_text or provider_text
        self.orchestrator_diagnostic = orchestrator_diagnostic
        label = "quota/rate limit reached" if kind is AgentFailureKind.QUOTA else f"{kind.value} failure"
        super().__init__(
            f"{agent_key} {label} [invocation {invocation_id}]: {provider_text}"
        )

    @property
    def readable_orchestrator_diagnostic(self) -> str | None:
        diagnostic = self.orchestrator_diagnostic
        return diagnostic.text if isinstance(diagnostic, OrchestratorDiagnostic) else None


class ProviderRequestRoundRequired(RuntimeError):
    """A rebuilt request changed while its repository binding stayed identical."""

    def __init__(
        self,
        *,
        binding_fingerprint: str,
        previous_input_digest: str,
        current_input_digest: str,
    ) -> None:
        self.binding_fingerprint = binding_fingerprint
        self.previous_input_digest = previous_input_digest
        self.current_input_digest = current_input_digest
        super().__init__(
            "provider request composition changed for unchanged binding_fingerprint "
            f"{binding_fingerprint[:12]}: {previous_input_digest[:12]} -> "
            f"{current_input_digest[:12]}"
        )


class QuotaReachedError(AgentInvocationError):
    def __init__(
        self,
        agent_key: str,
        detail: str,
        *,
        invocation_id: str = "legacy-quota",
        received_at: datetime | None = None,
        quota_reset: QuotaReset | None = None,
        exit_code: int | None = None,
        provider_data: Mapping[str, object] | None = None,
        technical_text: str | None = None,
        orchestrator_diagnostic: OrchestratorDiagnostic | None = None,
    ) -> None:
        super().__init__(
            agent_key=agent_key,
            kind=AgentFailureKind.QUOTA,
            invocation_id=invocation_id,
            provider_text=detail,
            received_at=received_at or datetime.now(timezone.utc),
            quota_reset=quota_reset,
            exit_code=exit_code,
            provider_data=provider_data,
            technical_text=technical_text or detail,
            orchestrator_diagnostic=orchestrator_diagnostic,
        )


class AgentCompatibilityError(RuntimeError):
    """Raised for missing, unknown, or capability-incompatible CLI versions."""


class AgentProcessError(RuntimeError):
    def __init__(
        self,
        provider_text: str,
        *,
        exit_code: int | None = None,
        kind_hint: AgentFailureKind | None = None,
        provider_data: Mapping[str, object] | None = None,
    ) -> None:
        self.provider_text = provider_text
        self.exit_code = exit_code
        self.kind_hint = kind_hint
        self.provider_data = provider_data
        super().__init__(provider_text)


@dataclass(frozen=True)
class QuotaWaitPolicy:
    automatic: bool = True
    safety_margin_seconds: int = 60
    maximum_wait_seconds: int = 604_800
    maximum_auto_resumes: int = 32
    heartbeat_interval_seconds: int = 3_600

    def __post_init__(self) -> None:
        if not isinstance(self.automatic, bool):
            raise ValueError("quota automatic policy must be a boolean")
        for value, label, allow_zero in (
            (self.safety_margin_seconds, "quota safety margin", True),
            (self.maximum_wait_seconds, "quota maximum wait", False),
            (self.maximum_auto_resumes, "quota maximum auto resumes", True),
            (self.heartbeat_interval_seconds, "quota heartbeat interval", False),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < (0 if allow_zero else 1)
            ):
                qualifier = "non-negative" if allow_zero else "positive"
                raise ValueError(f"{label} must be a {qualifier} integer")


@dataclass(frozen=True)
class TransientRetryPolicy:
    automatic: bool = True
    initial_delay_seconds: int = 5
    maximum_delay_seconds: int = 30
    maximum_auto_resumes: int = 2

    def __post_init__(self) -> None:
        if not isinstance(self.automatic, bool):
            raise ValueError("transient retry automatic policy must be a boolean")
        for value, label, allow_zero in (
            (self.initial_delay_seconds, "transient retry initial delay", False),
            (self.maximum_delay_seconds, "transient retry maximum delay", False),
            (self.maximum_auto_resumes, "transient retry maximum auto resumes", True),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < (0 if allow_zero else 1):
                qualifier = "non-negative" if allow_zero else "positive"
                raise ValueError(f"{label} must be a {qualifier} integer")
        if self.maximum_delay_seconds < self.initial_delay_seconds:
            raise ValueError("transient retry maximum delay cannot be below initial delay")


def wait_until_transient_retry(
    *,
    role: str,
    task_label: str,
    work_unit_id: int,
    resume_at_utc: datetime,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    sleep_fn: Callable[[float], None] = time.sleep,
    heartbeat_fn: Callable[[str], None] = logger.info,
) -> None:
    target = resume_at_utc.astimezone(timezone.utc)
    now = now_fn().astimezone(timezone.utc)
    remaining = max(0.0, (target - now).total_seconds())
    if remaining <= 0:
        return
    heartbeat_fn(
        "transient retry wait: "
        f"role={role} task={task_label} work_unit={work_unit_id} "
        f"resume_utc={target.isoformat()} remaining={int(remaining + 0.999)}s"
    )
    sleep_fn(remaining)


def wait_until_quota_resume(
    *,
    role: str,
    task_label: str,
    work_unit_id: int,
    reset_at_utc: datetime,
    resume_at_utc: datetime,
    heartbeat_interval_seconds: int,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    sleep_fn: Callable[[float], None] = time.sleep,
    heartbeat_fn: Callable[[str], None] = logger.info,
) -> None:
    """Wait through reset and safety-margin phases using an injected clock."""
    for value, label in (
        (reset_at_utc, "quota reset timestamp"),
        (resume_at_utc, "quota resume timestamp"),
    ):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{label} must be timezone-aware")
    if heartbeat_interval_seconds < 1:
        raise ValueError("quota heartbeat interval must be positive")
    reset_target = reset_at_utc.astimezone(timezone.utc)
    resume_target = resume_at_utc.astimezone(timezone.utc)
    if resume_target < reset_target:
        raise ValueError("quota resume timestamp cannot precede quota reset timestamp")

    def current_utc() -> datetime:
        now = now_fn()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("quota wait clock must return a timezone-aware datetime")
        return now.astimezone(timezone.utc)

    now_utc = current_utc()
    heartbeat_fn(
        "quota wait entered: "
        f"role={role} task={task_label} work_unit={work_unit_id} "
        f"reset_local={reset_target.astimezone().isoformat()} "
        f"reset_utc={reset_target.isoformat()} "
        f"resume_local={resume_target.astimezone().isoformat()} "
        f"resume_utc={resume_target.isoformat()} "
        f"remaining_to_reset={int(max(0.0, (reset_target - now_utc).total_seconds()) + 0.999)}s"
    )

    while True:
        remaining = max(0.0, (reset_target - now_utc).total_seconds())
        if remaining <= 0:
            break
        sleep_fn(min(float(heartbeat_interval_seconds), remaining))
        now_utc = current_utc()
        remaining = max(0.0, (reset_target - now_utc).total_seconds())
        if remaining <= 0:
            break
        heartbeat_fn(
            "quota wait heartbeat: "
            f"role={role} task={task_label} work_unit={work_unit_id} "
            f"reset_local={reset_target.astimezone().isoformat()} "
            f"reset_utc={reset_target.isoformat()} "
            f"remaining_to_reset={int(remaining + 0.999)}s"
        )

    heartbeat_fn(
        "quota reset reached: "
        f"role={role} task={task_label} work_unit={work_unit_id} "
        f"reset_utc={reset_target.isoformat()} resume_utc={resume_target.isoformat()}"
    )
    remaining_margin = max(0.0, (resume_target - now_utc).total_seconds())
    while remaining_margin > 0:
        sleep_fn(remaining_margin)
        now_utc = current_utc()
        remaining_margin = max(0.0, (resume_target - now_utc).total_seconds())
    heartbeat_fn(
        "quota wait resumed: "
        f"role={role} task={task_label} work_unit={work_unit_id} "
        f"resume_utc={resume_target.isoformat()}"
    )


@dataclass
class OrchestratorConfig:
    dry_run: bool = False
    agent_output_mode: str = "summary"
    agent_output_max_chars: int = 1800
    agent_live_stream: bool = False
    agent_live_stream_mode: str = "compact"
    agent_live_stream_channels: str = "both"
    repo_root: Path = field(default_factory=lambda: Path.cwd().resolve())
    strict_preflight: bool = False
    phase_progress_threshold_seconds: float = 30.0
    provider_input_budget: ProviderInputBudgetPolicy = field(
        default_factory=default_provider_input_budget_policy
    )


@dataclass
class StreamResult:
    """Lightweight CompletedProcess-compatible container for streamed runs."""

    returncode: int
    stdout: str
    stderr: str


@dataclass
class ReviewerWorkspace:
    root: Path
    container: Path

    def cleanup(self) -> None:
        if not self.container.exists():
            return
        for path in sorted(self.container.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if path.is_symlink():
                continue
            try:
                path.chmod(0o700 if path.is_dir() else 0o600)
            except OSError:
                pass
        try:
            self.container.chmod(0o700)
        except OSError:
            pass
        shutil.rmtree(self.container, ignore_errors=True)


def _compact_text(text: str, *, max_chars: int = 900) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 14].rstrip() + " …[gekürzt]"


def _compact_stream_text(
    adapter: AgentAdapter,
    channel: str,
    line: str,
    state: dict[str, str | bool],
) -> str | None:
    """Render useful live progress without leaking provider JSON envelopes."""
    text = line.strip()
    if not text:
        return None

    if channel == "stdout" and adapter.name == "codex" and text.startswith("{"):
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(event, dict):
            return None
        candidate: object = event.get("message")
        item = event.get("item")
        if isinstance(item, dict):
            candidate = item.get("text") or item.get("content") or candidate
        if not isinstance(candidate, str) or not candidate.strip():
            return None
        text = candidate.strip()
    elif channel == "stdout" and adapter.reviewer:
        # Claude emits its complete result and usage metadata as
        # one JSON line. The extracted contract summary is logged after parsing.
        return None
    # Non-JSON diagnostics (normally stderr warnings) remain visible. Compact
    # mode owns its channel-aware deduplication instead of the adapters' legacy
    # cross-channel boolean filter.

    rendered = _compact_text(text)
    dedup_key = f"last_compact_text_{channel}"
    if rendered == state.get(dedup_key):
        return None
    state[dedup_key] = rendered
    return rendered


def _compact_result_lines(output: str) -> tuple[str, ...]:
    """Render a compact human view from one completed native JSON result."""
    try:
        document = json.loads(output)
    except json.JSONDecodeError:
        return ()
    if not isinstance(document, dict):
        return ()
    lines: list[str] = []
    result_type = document.get("result_type")
    if isinstance(result_type, str):
        lines.append(f"result_type={result_type}")
    for field in ("ready", "approved", "decision", "request_id"):
        value = document.get(field)
        if isinstance(value, (str, bool)):
            lines.append(f"{field}={value}")
    for field in (
        "new_findings",
        "status_changes",
        "reclassifications",
        "finding_dispositions",
    ):
        value = document.get(field)
        if isinstance(value, list):
            lines.append(f"{field}={len(value)}")
    return tuple(_compact_text(line, max_chars=480) for line in lines)


def normalize_provider_usage(metadata: Mapping[str, object] | None) -> ProviderUsagePayload | None:
    """Map provider envelopes onto the sole persisted/logged numeric allowlist."""
    if not isinstance(metadata, Mapping):
        return None
    usage = metadata.get("usage")
    usage_map = usage if isinstance(usage, Mapping) else {}

    def integer(*keys: str) -> int | None:
        for source in (usage_map, metadata):
            for key in keys:
                value = source.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    return value
        return None

    def number(*keys: str) -> float | None:
        for source in (usage_map, metadata):
            for key in keys:
                value = source.get(key)
                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                    and value >= 0
                ):
                    return float(value)
        return None

    normalized = ProviderUsagePayload(
        input_tokens=integer("input_tokens", "inputTokens", "promptTokenCount"),
        tool_input_tokens=integer("tool_input_tokens", "toolInputTokens", "toolUseInputTokens"),
        cache_read_input_tokens=integer("cache_read_input_tokens", "cacheReadInputTokens"),
        cache_creation_input_tokens=integer(
            "cache_creation_input_tokens", "cache_write_input_tokens",
            "cacheCreationInputTokens", "cacheWriteInputTokens",
        ),
        thinking_tokens=integer("thinking_tokens", "thinkingTokens", "thoughtsTokenCount"),
        output_tokens=integer("output_tokens", "outputTokens", "candidatesTokenCount"),
        total_tokens=integer("total_tokens", "totalTokens", "totalTokenCount"),
        turns=integer("num_turns", "turns"),
        cost_usd=number("total_cost_usd", "cost_usd"),
    )
    return normalized if any(value is not None for value in asdict(normalized).values()) else None


def _compact_usage_metadata(metadata: Mapping[str, object] | None) -> str:
    """Summarize only normalized provider usage; unknown is never rendered as zero."""
    normalized = normalize_provider_usage(metadata)
    if normalized is None:
        return "unknown"
    parts: list[str] = []
    for name, value in asdict(normalized).items():
        if value is not None:
            rendered = f"{value:.4f}" if name == "cost_usd" else str(value)
            parts.append(f"{name}={rendered}")
    return " ".join(parts)


@dataclass(frozen=True)
class ProviderAttemptLifecycle:
    start: Callable[[ProviderInputMeasurement, object | None], object]
    terminal: Callable[[object, float, str | None, ProviderUsagePayload | None], None]
    durable_response_path: Callable[[object], Path] | None = None
    failure_path: Callable[[Path], Path] | None = None
    monotonic_fn: Callable[[], float] = time.monotonic


@dataclass
class _ProviderAttemptInvocation:
    lifecycle: ProviderAttemptLifecycle
    handle: object | None = None
    monotonic_started: float | None = None
    terminalized: bool = False

    def begin(self, measurement: ProviderInputMeasurement, bootstrap: object | None) -> None:
        self.handle = self.lifecycle.start(measurement, bootstrap)
        self.monotonic_started = self.lifecycle.monotonic_fn()

    def response_path(self, fallback: Path) -> Path:
        if self.handle is None or self.lifecycle.durable_response_path is None:
            return fallback
        return self.lifecycle.durable_response_path(self.handle)

    def failure_path(self, fallback: Path) -> Path:
        response_path = self.response_path(fallback)
        if self.lifecycle.failure_path is not None:
            return self.lifecycle.failure_path(response_path)
        return response_path.with_suffix(response_path.suffix + ".failure.json")

    def finish(self, failure_kind: AgentFailureKind | None, metadata: Mapping[str, object] | None) -> None:
        if self.handle is None or self.monotonic_started is None or self.terminalized:
            return
        self.terminalized = True
        self.lifecycle.terminal(
            self.handle,
            max(0.0, self.lifecycle.monotonic_fn() - self.monotonic_started),
            failure_kind.value if failure_kind is not None else None,
            normalize_provider_usage(metadata),
        )


def _review_snapshot_paths(source: Path) -> tuple[PurePosixPath, ...] | None:
    """Return tracked and non-ignored untracked paths, or None outside Git."""
    try:
        result = subprocess.run(
            [
                "git",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
            ],
            cwd=source,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0 or not isinstance(result.stdout, bytes):
        return None
    paths: list[PurePosixPath] = []
    for field in result.stdout.split(b"\0"):
        if not field:
            continue
        raw = os.fsdecode(field)
        path = PurePosixPath(raw)
        if path.is_absolute() or not path.parts or ".." in path.parts:
            raise RuntimeError(f"git returned unsafe reviewer snapshot path: {raw!r}")
        if path.parts[0] in REVIEW_SNAPSHOT_EXCLUDED_ROOTS:
            continue
        if any(
            pattern.fullmatch(path.as_posix())
            for pattern in REVIEW_SNAPSHOT_AUDIT_PATH_PATTERNS
        ):
            continue
        paths.append(path)
    return tuple(sorted(set(paths), key=lambda item: item.as_posix()))


def _copy_review_snapshot(
    source: Path,
    destination: Path,
    manifest_paths: tuple[str, ...] | None = None,
) -> int:
    if manifest_paths is not None:
        if not manifest_paths or manifest_paths != tuple(sorted(set(manifest_paths))):
            raise RuntimeError("reviewer snapshot manifest must be sorted, unique, and non-empty")
        destination.mkdir()
        copied = 0
        for raw in manifest_paths:
            relative = PurePosixPath(raw)
            if relative.is_absolute() or not relative.parts or ".." in relative.parts:
                raise RuntimeError(f"unsafe reviewer snapshot manifest path: {raw!r}")
            source_path = source.joinpath(*relative.parts)
            cursor = source
            for part in relative.parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    raise RuntimeError(
                        f"reviewer snapshot manifest path traverses a symlink: {raw!r}"
                    )
            if not source_path.exists() or not source_path.is_file():
                raise RuntimeError(
                    f"reviewer snapshot manifest path is missing or not a file: {raw!r}"
                )
            destination_path = destination.joinpath(*relative.parts)
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination_path, follow_symlinks=False)
            copied += 1
        return copied

    paths = _review_snapshot_paths(source)
    if paths is None:
        # Unit tests and explicit diagnostics may use a non-Git fixture. Keep that
        # compatibility path bounded by excluding generated and dependency trees.
        def ignore_generated(_directory: str, names: list[str]) -> set[str]:
            return {name for name in names if name in REVIEW_SNAPSHOT_EXCLUDED_ROOTS}

        shutil.copytree(source, destination, symlinks=True, ignore=ignore_generated)
        return sum(1 for path in destination.rglob("*") if path.is_file())

    destination.mkdir()
    copied = 0
    for relative in paths:
        source_path = source.joinpath(*relative.parts)
        destination_path = destination.joinpath(*relative.parts)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        metadata = source_path.lstat()
        if source_path.is_symlink():
            destination_path.symlink_to(os.readlink(source_path))
        elif source_path.is_file():
            shutil.copy2(source_path, destination_path, follow_symlinks=False)
            copied += 1
        elif source_path.is_dir():
            # Gitlinks/submodules are represented as directories but are not copied
            # recursively; their content is outside the canonical repository evidence.
            destination_path.mkdir(exist_ok=True)
        else:
            raise RuntimeError(
                f"unsupported reviewer snapshot path type: {relative.as_posix()}"
            )
    return copied


def create_read_only_reviewer_workspace(
    repo_root: Path,
    manifest_paths: tuple[str, ...] | None = None,
) -> ReviewerWorkspace:
    """Copy canonical repository files and remove write bits without following links."""
    source = repo_root.resolve()
    container = Path(tempfile.mkdtemp(prefix="dao-review-workspace-"))
    destination = container / "repo"
    started = time.monotonic()
    logger.info("Preparing selective read-only reviewer snapshot.")
    try:
        copied = _copy_review_snapshot(source, destination, manifest_paths)
        paths = sorted(destination.rglob("*"), key=lambda item: len(item.parts), reverse=True)
        for path in paths:
            if path.is_symlink():
                continue
            if path.is_dir():
                path.chmod(0o555)
            elif path.is_file():
                executable = bool(path.stat().st_mode & 0o111)
                path.chmod(0o555 if executable else 0o444)
        destination.chmod(0o555)
        container.chmod(0o555)
        logger.info(
            "Reviewer snapshot ready: files=%s elapsed=%.2fs path=%s",
            copied,
            time.monotonic() - started,
            destination,
        )
        return ReviewerWorkspace(root=destination, container=container)
    except Exception:
        ReviewerWorkspace(root=destination, container=container).cleanup()
        raise


def create_empty_reviewer_workspace() -> ReviewerWorkspace:
    """Create a private read-only cwd for contract-only reviewer repairs."""
    container = Path(tempfile.mkdtemp(prefix="dao-review-contract-"))
    destination = container / "empty"
    destination.mkdir(mode=0o555)
    container.chmod(0o555)
    return ReviewerWorkspace(root=destination, container=container)


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


def _resolve_agent_binary(binary: str) -> str | None:
    candidate = Path(binary).expanduser()
    if candidate.is_absolute() or "/" in binary or "\\" in binary:
        try:
            resolved = candidate.resolve()
        except OSError:
            return None
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return str(resolved)
        return None
    return shutil.which(binary)


def verify_agent_capabilities(adapter: AgentAdapter, *, strict_dns: bool = False) -> None:
    """Verify the configured role once, immediately before its first real invocation."""
    if adapter.capability_verified:
        return
    resolved_binary = _resolve_agent_binary(adapter.cli_binary)
    if resolved_binary is None:
        raise AgentCompatibilityError(
            f"Missing CLI binary for '{adapter.name}': {adapter.cli_binary}"
        )

    version_rc, version_out, version_err = run_local_command(
        [resolved_binary, *adapter.capability.version_args]
    )
    version_text = (version_out or version_err).strip()
    if version_rc != 0 or not version_text:
        raise AgentCompatibilityError(
            f"Cannot determine {adapter.name} version using {resolved_binary}: "
            f"{(version_err or version_out).strip() or 'empty output'}"
        )
    version_matches_static_pattern = any(
        re.fullmatch(pattern, version_text)
        for pattern in adapter.capability.supported_version_patterns
    )
    if not version_matches_static_pattern:
        try:
            assert_provider_capabilities(
                adapter.name,
                (),
                cli_version=version_text,
            )
        except NativeProviderSchemaError as exc:
            raise AgentCompatibilityError(
                f"Unsupported {adapter.name} CLI version {version_text!r}: {exc}"
            ) from exc

    help_rc, help_out, help_err = run_local_command(
        [resolved_binary, *adapter.capability.help_args]
    )
    help_text = "\n".join(part for part in (help_out, help_err) if part)
    if help_rc != 0:
        raise AgentCompatibilityError(
            f"Cannot inspect {adapter.name} capabilities: {help_text.strip() or 'empty output'}"
        )
    adapter.validate_process_output(help_err)
    missing_flags = [
        flag for flag in adapter.capability.required_help_flags if flag not in help_text
    ]
    if missing_flags:
        raise AgentCompatibilityError(
            f"{adapter.name} {version_text!r} is missing required capability flags: "
            f"{', '.join(missing_flags)}"
        )

    if strict_dns:
        missing_hosts = [host for host in adapter.required_hosts if not can_resolve_host(host)]
        if missing_hosts:
            raise AgentCompatibilityError(
                f"DNS resolution failed for {adapter.name}: {', '.join(missing_hosts)}"
            )

    adapter.capability_verified = True
    logger.info(
        "Agent ready: role=%s binary=%s version=%s model=%s effort=%s timeout=%ss profile=%s",
        adapter.name,
        resolved_binary,
        version_text,
        adapter.model,
        adapter.effort,
        adapter.timeout,
        "read-only-reviewer" if adapter.reviewer else "workspace-write-implementer",
    )


def check_git_clean() -> tuple[bool, str]:
    """Validate repo cleanliness with explicit handling for detached/non-git environments."""
    if shutil.which("git") is None:
        return True, "Git not found in PATH; skipping git cleanliness check."

    # Step 1: confirm we are in a git worktree before running stricter checks.
    inside_rc, inside_out, inside_err = run_local_command(
        ["git", "rev-parse", "--is-inside-work-tree"]
    )
    if inside_rc != 0 or inside_out.strip() != "true":
        detail = (inside_err or inside_out).strip() or "not a git worktree"
        return True, f"Git cleanliness check skipped ({detail})."

    # Step 2: gather porcelain status plus HEAD existence for tracked-change checks.
    head_rc, _, _ = run_local_command(["git", "rev-parse", "--verify", "HEAD"])
    status_rc, status_out, status_err = run_local_command(
        ["git", "status", "--porcelain", "--untracked-files=normal"]
    )
    if status_rc != 0:
        detail = status_err.strip() or "git status failed"
        return False, f"Git cleanliness check failed: {detail}"

    def format_status_excerpt(limit: int = 10) -> str:
        lines = [line for line in status_out.splitlines() if line.strip()]
        if not lines:
            return "(empty)"
        excerpt = lines[:limit]
        if len(lines) > limit:
            excerpt.append("...")
        return "; ".join(excerpt)

    # Keep only tracked changes here; untracked files are handled below with full status output.
    tracked_paths: list[str] = []
    for line in status_out.splitlines():
        row = line.rstrip()
        if len(row) < 4:
            continue
        if row.startswith("?? "):
            continue
        tracked_paths.append(row[3:])

    if head_rc == 0:
        # Step 3: refresh index and compare tracked files against HEAD.
        refresh_rc, _, refresh_err = run_local_command(["git", "update-index", "-q", "--refresh"])
        if refresh_rc != 0:
            detail = refresh_err.strip() or "git update-index failed"
            return False, f"Git cleanliness check failed: {detail}"

        diff_rc, _, diff_err = run_local_command(["git", "diff-index", "--quiet", "HEAD", "--"])
        if diff_rc not in (0, 1):
            detail = diff_err.strip() or "git diff-index failed"
            return False, f"Git cleanliness check failed: {detail}"
        if diff_rc == 1:
            # Provide a short actionable summary instead of dumping full status output.
            summary = ""
            if tracked_paths:
                listed = ", ".join(tracked_paths[:5])
                if len(tracked_paths) > 5:
                    listed = f"{listed}, ..."
                summary = f" Changed files: {listed}."
            summary = f"{summary} git status --porcelain: {format_status_excerpt()}."
            return (
                False,
                "Git working tree has tracked changes. Commit/stash/revert before running orchestrator, "
                "or run with --skip-git-check if this is intentional."
                f"{summary}",
            )

    if status_out.strip():
        return (
            False,
            "Git working tree is not clean (includes untracked and/or staged files). "
            "Commit/stash/revert before running orchestrator, or run with --skip-git-check "
            "if this is intentional. "
            f"git status --porcelain: {format_status_excerpt()}.",
        )

    return True, "Git working tree is clean."


def repo_snapshot(changes: RepositoryChanges, max_diff_chars: int) -> str:
    """Render the already-collected canonical branch changes for a review prompt."""
    return changes.render_snapshot(max_diff_chars)


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
        # `shell=True` is intentional because test commands can be user-provided pipelines.
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


def run_validation_matrix(
    *,
    config: OrchestratorConfig,
    request: ValidationRequest,
) -> ValidationAttestation:
    """Execute the selected v3 matrix in the configured repository root."""
    return ValidationMatrixRunner(config.repo_root).run(request)


def build_dry_run_agent_output(agent_key: str, prompt: str) -> str:
    _ = (agent_key, prompt)
    raise AgentProcessError(
        "native dry-run requires an explicit scripted JSON scenario via --dry-run-scenario",
        kind_hint=AgentFailureKind.OUTPUT,
    )


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


def _run_agent_process(
    adapter: AgentAdapter,
    command_parts: list[str],
    stdin_text: str | None,
    *,
    config: OrchestratorConfig,
    env: dict[str, str],
    execution_root: Path,
    timeout_seconds: int,
    agent_key: str,
) -> StreamResult | subprocess.CompletedProcess[str]:
    """Start one provider process and retain ownership through its cleanup boundary."""
    if config.agent_live_stream:
        # Stream mode captures stdout/stderr incrementally while still preserving full output.
        process = subprocess.Popen(
            command_parts,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=execution_root,
            bufsize=1,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None

        if stdin_text is not None:
            process.stdin.write(stdin_text)
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
            # Use sentinel None to signal channel completion to the main loop.
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
        heartbeat_interval_seconds = 30.0
        last_heartbeat = start
        while len(completed_channels) < 2:
            if time.monotonic() - start > timeout_seconds:
                process.kill()
                raise subprocess.TimeoutExpired(command_parts, timeout_seconds)
            now = time.monotonic()
            if now - last_heartbeat >= heartbeat_interval_seconds:
                elapsed = int(now - start)
                logger.info("[AGENT] %s still running (elapsed: %ss)", agent_key, elapsed)
                last_heartbeat = now
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
            if config.agent_live_stream_mode == "full":
                logger.info("[%s:%s] %s", agent_key, channel, line.rstrip())
            else:
                rendered = _compact_stream_text(adapter, channel, line, stream_state)
                if rendered is not None:
                    logger.info("[%s:%s] %s", agent_key, channel, rendered)

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
            input=stdin_text,
            capture_output=True,
            text=True,
            env=env,
            cwd=execution_root,
            timeout=timeout_seconds,
            check=False,
        )
    return result


def run_agent(
    adapter: AgentAdapter,
    prompt: str,
    *,
    config: OrchestratorConfig,
    shorten: Callable[[str | None, int], str],
    reviewer_repository_required: bool = True,
    reviewer_manifest_paths: tuple[str, ...] | None = None,
    operation: str | None = None,
    binding_fingerprint: str = "unbound",
    pre_start_callback: Callable[[ProviderInputMeasurement], object | None] | None = None,
    attempt_invocation: _ProviderAttemptInvocation | None = None,
    prepared_provider_input: PreparedProviderInput | None = None,
    execution_root_override: Path | None = None,
) -> str:
    """Run an adapter command once, with optional live streaming and strict output checks."""
    agent_key = adapter.name
    if config.dry_run:
        return build_dry_run_agent_output(agent_key, prompt)
    if agent_key not in PROVIDER_OPERATIONS:
        raise ProviderInputBudgetError(
            f"adapter {agent_key!r} has no provider input budget registration"
        )

    workspace: ReviewerWorkspace | None = None
    execution_root = (
        execution_root_override.resolve()
        if execution_root_override is not None
        else config.repo_root.resolve()
    )
    if execution_root_override is not None and adapter.reviewer:
        raise ValueError("reviewer execution roots are owned by reviewer workspaces")
    if not execution_root.is_dir():
        raise ValueError("agent execution root must be an existing directory")
    timeout_seconds = adapter.timeout
    extra_files: dict[str, str] = {}

    invocation_started = time.monotonic()
    try:
        if adapter.reviewer:
            if not reviewer_repository_required:
                workspace = create_empty_reviewer_workspace()
            else:
                workspace = create_read_only_reviewer_workspace(
                    execution_root, reviewer_manifest_paths
                )
            source_root = execution_root
            execution_root = workspace.root
            adapter.bind_reviewer_workspace(source_root, execution_root)

        prepare_input = getattr(adapter, "prepare_provider_input", None)
        if prepared_provider_input is not None:
            prepared = prepared_provider_input
        elif callable(prepare_input):
            prepared = prepare_input(prompt)
        else:
            legacy_command, legacy_stdin = adapter.build_command(prompt)
            prepared = PreparedProviderInput(
                tuple(legacy_command),
                prompt if legacy_stdin else None,
                (ProviderInputComponent("stdin_prompt", prompt),),
            )
        effective_operation = operation or {
            "codex": "codex_implementation",
            "claude": "claude_slice_review",
        }.get(agent_key)
        if effective_operation is None:
            raise ValueError(f"provider input operation is required for {agent_key}")
        measurement = measure_provider_input(
            prepared,
            provider=agent_key,
            role=agent_key,
            operation=effective_operation,
            binding_fingerprint=binding_fingerprint,
            policy=config.provider_input_budget,
        )
        bootstrap_context = (
            pre_start_callback(measurement) if pre_start_callback is not None else None
        )
        logger.info(
            "[PROVIDER_INPUT] provider=%s role=%s operation=%s allowed=%s "
            "local_input_chars=%s/%s local_input_bytes=%s/%s "
            "local_input_component_count=%s local_input_digest=%s policy_digest=%s "
            "local_input_largest_component=%s violations=%s",
            measurement.provider,
            measurement.role,
            measurement.operation,
            measurement.allowed,
            measurement.total_chars,
            measurement.effective_limit_chars,
            measurement.total_bytes,
            measurement.effective_limit_bytes,
            len(measurement.components),
            measurement.input_digest,
            measurement.policy_digest,
            measurement.largest_component,
            ",".join(measurement.violated_dimensions) or "none",
        )
        if not measurement.allowed:
            raise ProviderInputBudgetExceeded(measurement)

        verify_agent_capabilities(adapter, strict_dns=config.strict_preflight)
        command_parts = list(prepared.command)
        stdin_text = prepared.stdin_text
        env = os.environ.copy()
        env.update(adapter.env)
        if adapter.reviewer:
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            for variable in (
                "RUN_TASK_REVIEW_TEST_COMMAND",
                "RUN_TASK_REVIEW_PROBE_PATH",
                "RUN_TASK_REVIEW_TIMEOUT",
            ):
                env.pop(variable, None)
        env["PWD"] = str(execution_root)

        if attempt_invocation is not None:
            attempt_invocation.begin(measurement, bootstrap_context)

        result = _run_agent_process(
            adapter,
            command_parts,
            stdin_text,
            config=config,
            env=env,
            execution_root=execution_root,
            timeout_seconds=timeout_seconds,
            agent_key=agent_key,
        )

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        try:
            adapter.validate_process_output(stderr)
            output = adapter.extract_output(stdout, stderr, extra_files)
        except AgentOutputError as exc:
            if exc.exit_code is None:
                exc.exit_code = result.returncode
            raise
        if result.returncode != 0:
            error_text = stderr or output or "Unknown CLI error without output."
            raise AgentProcessError(error_text, exit_code=result.returncode)
        if not output:
            raise AgentProcessError(
                f"{agent_key} returned empty output.",
                exit_code=result.returncode,
                kind_hint=AgentFailureKind.OUTPUT,
            )
        if config.agent_live_stream and config.agent_live_stream_mode == "compact":
            summary_lines = _compact_result_lines(output)
            if summary_lines:
                for summary_line in summary_lines:
                    logger.info("[AGENT_RESULT] role=%s %s", agent_key, summary_line)
            else:
                logger.info("[AGENT_RESULT] role=%s completed", agent_key)
        normalized_usage = normalize_provider_usage(adapter.metadata)
        if normalized_usage is not None:
            if config.agent_live_stream_mode == "full" or config.agent_output_mode == "full":
                logger.info(
                    "[AGENT_USAGE] role=%s operation=%s usage=%s",
                    agent_key,
                    effective_operation,
                    json.dumps(asdict(normalized_usage), ensure_ascii=False, sort_keys=True),
                )
            else:
                logger.info(
                    "[AGENT_USAGE] role=%s operation=%s %s",
                    agent_key,
                    effective_operation,
                    _compact_usage_metadata(adapter.metadata),
                )
        logger.info(
            "[PROVIDER_COMPLETION] role=%s operation=%s success=true elapsed=%.2fs usage=%s",
            agent_key,
            effective_operation,
            time.monotonic() - invocation_started,
            _compact_usage_metadata(adapter.metadata),
        )
        return output
    except subprocess.TimeoutExpired as exc:
        raise AgentProcessError(
            f"{agent_key} timed out after {timeout_seconds}s.",
            kind_hint=AgentFailureKind.TIMEOUT,
        ) from exc
    finally:
        try:
            adapter.cleanup()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Adapter cleanup failed for %s: %s", agent_key, exc)
        if workspace is not None:
            workspace.cleanup()


@dataclass(frozen=True, slots=True)
class NativeAgentReviewOutput:
    """One schema- and request-bound native reviewer result."""

    result: ContractResult
    canonical_json: str
    request_id: str
    context: NativeReviewContext | None = None


def run_native_review_agent(
    adapter: AgentAdapter,
    bundle: NativeReviewRequestBundle,
    *,
    config: OrchestratorConfig,
    shorten: Callable[[str | None, int], str],
    reviewer_manifest_paths: tuple[str, ...] | None = None,
    operation: str,
    binding_fingerprint: str,
    pre_start_callback: Callable[[ProviderInputMeasurement], object | None] | None = None,
    attempt_invocation: _ProviderAttemptInvocation | None = None,
    response_callback: Callable[[str], None] | None = None,
) -> NativeAgentReviewOutput:
    """Run one native Claude review without legacy marker or repair parsing."""
    prepare = getattr(adapter, "prepare_native_provider_input", None)
    if not callable(prepare):
        raise TypeError("native review adapter lacks prepare_native_provider_input")
    prepared = prepare(bundle)
    canonical = run_agent(
        adapter,
        bundle.canonical_json,
        config=config,
        shorten=shorten,
        reviewer_repository_required=True,
        reviewer_manifest_paths=reviewer_manifest_paths,
        operation=operation,
        binding_fingerprint=binding_fingerprint,
        pre_start_callback=pre_start_callback,
        attempt_invocation=attempt_invocation,
        prepared_provider_input=prepared,
    )
    if response_callback is not None:
        response_callback(canonical)
    try:
        document = json.loads(canonical)
        if not isinstance(document, dict):
            raise AgentOutputError("native review result must be a JSON object")
        response_request_id = document.get("request_id")
        if (
            isinstance(response_request_id, str)
            and response_request_id != bundle.bound_context.request_id
        ):
            raise NativeReviewContractError(
                NativeReviewErrorCode.REQUEST_MISMATCH,
                "response request_id does not match bound request",
            )
        response_reviewer = document.get("reviewer")
        if (
            isinstance(response_reviewer, str)
            and response_reviewer != bundle.bound_context.context.reviewer.value
        ):
            raise NativeReviewContractError(
                NativeReviewErrorCode.REVIEWER_MISMATCH,
                "response reviewer does not match bound request",
            )
        validate_native_review_disposition_budget(
            document, bundle.bound_context.context
        )
        validate_native_review_document(document)
        validate_native_review_provider_response(document, bundle)
        result = parse_bound_native_contract_result(document, bundle.bound_context)
    except json.JSONDecodeError as exc:
        raise AgentOutputError(
            "native review result is not valid JSON",
            technical_text=f"native-json-invalid: {exc}",
        ) from exc
    except NativeReviewContractError as exc:
        raise AgentOutputError(
            "native review result violates its bound contract",
            provider_data=document,
            technical_text=f"{exc.code.value}: {exc.detail}",
        ) from exc
    except NativeReviewRequestError as exc:
        if exc.code is NativeReviewRequestErrorCode.SCHEMA_INVALID:
            form_error = NativeReviewContractError(
                NativeReviewErrorCode.SCHEMA_INVALID, exc.detail
            )
            raise AgentOutputError(
                "native review result violates its bound contract",
                provider_data=document,
                technical_text=f"{form_error.code.value}: {form_error.detail}",
            ) from form_error
        raise AgentOutputError(
            "native review result violates its bound contract",
            provider_data=document,
            technical_text=f"{exc.code.value}: {exc.detail}",
        ) from exc
    return NativeAgentReviewOutput(
        result=result,
        canonical_json=canonical,
        request_id=bundle.bound_context.request_id,
        context=bundle.bound_context.context,
    )


def run_native_review_agent_checked(
    *,
    adapter: AgentAdapter,
    bundle: NativeReviewRequestBundle,
    log_prefix: str,
    config: OrchestratorConfig,
    log_dir: Path,
    raw_response_path: Path | None = None,
    write_file: Callable[[Path, str], None],
    shorten: Callable[[str | None, int], str],
    reviewer_manifest_paths: tuple[str, ...] | None,
    operation: str,
    binding_fingerprint: str,
    pre_start_callback: Callable[[ProviderInputMeasurement], object | None] | None,
    provider_attempt_lifecycle: ProviderAttemptLifecycle | None,
    accepted_output_callback: (
        Callable[[NativeAgentReviewOutput], None] | None
    ) = None,
) -> NativeAgentReviewOutput:
    """Run and durably capture one native review attempt before validation."""
    invocation_id = uuid.uuid4().hex
    attempt_invocation = (
        _ProviderAttemptInvocation(provider_attempt_lifecycle)
        if provider_attempt_lifecycle is not None
        else None
    )
    log_path = log_dir / f"{log_prefix}.attempt-1.log"
    response_path = raw_response_path or log_path
    try:
        output = run_native_review_agent(
            adapter,
            bundle,
            config=config,
            shorten=shorten,
            reviewer_manifest_paths=reviewer_manifest_paths,
            operation=operation,
            binding_fingerprint=binding_fingerprint,
            pre_start_callback=pre_start_callback,
            attempt_invocation=attempt_invocation,
            response_callback=lambda canonical: write_file(
                attempt_invocation.response_path(response_path)
                if attempt_invocation is not None
                else response_path,
                canonical,
            ),
        )
        actual_log_path = (
            attempt_invocation.response_path(response_path)
            if attempt_invocation is not None
            else response_path
        )
        print_agent_output(
            adapter.name,
            actual_log_path,
            1,
            output.canonical_json,
            config=config,
            shorten=shorten,
        )
        if accepted_output_callback is not None:
            accepted_output_callback(output)
        if attempt_invocation is not None:
            attempt_invocation.finish(None, adapter.metadata)
        return output
    except AgentInvocationError as failure:
        if attempt_invocation is not None:
            attempt_invocation.finish(failure.kind, adapter.metadata)
        raise
    except ProviderRequestRoundRequired:
        raise
    except ProviderInputBudgetExceeded:
        raise
    except Exception as exc:
        failure = classify_agent_failure(
            adapter.name,
            exc,
            invocation_id=invocation_id,
        )
        if attempt_invocation is not None:
            attempt_invocation.finish(failure.kind, adapter.metadata)
        failure_path = (
            attempt_invocation.failure_path(response_path)
            if attempt_invocation is not None
            else log_dir / f"{log_prefix}.attempt-1.failure.json"
            if raw_response_path is None
            else response_path.with_suffix(response_path.suffix + ".failure.json")
        )
        write_file(
            failure_path,
            json.dumps(
                {
                    "agent": failure.agent_key,
                    "failure_kind": failure.kind.value,
                    "invocation_id": failure.invocation_id,
                    "provider_text": failure.provider_text,
                    "provider_diagnostic": failure.provider_data,
                    "technical_text": failure.technical_text,
                    "received_at": failure.received_at.isoformat(),
                    "process_exit_code": failure.process_exit_code,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        raise failure from exc


@dataclass(frozen=True, slots=True)
class NativeAgentCodexOutput:
    """One schema-, request-, and domain-bound native Codex result."""

    result: CodexContractResult
    canonical_json: str
    request_id: str
    response_sha256: str


def run_native_codex_agent(
    adapter: AgentAdapter,
    bundle: NativeCodexRequestBundle,
    *,
    config: OrchestratorConfig,
    shorten: Callable[[str | None, int], str],
    operation: str,
    binding_fingerprint: str,
    pre_start_callback: Callable[[ProviderInputMeasurement], object | None] | None = None,
    attempt_invocation: _ProviderAttemptInvocation | None = None,
    validated_response_callback: Callable[[str], None] | None = None,
    execution_boundary: NativeCodexExecutionBoundary | None = None,
) -> NativeAgentCodexOutput:
    """Run native Codex without marker parsing, flag extraction, or repair."""
    prepare = getattr(adapter, "prepare_native_provider_input", None)
    if not callable(prepare):
        raise TypeError("native Codex adapter lacks prepare_native_provider_input")
    boundary = execution_boundary or NativeCodexExecutionBoundary.production(
        config.repo_root
    )
    prepared = prepare(bundle, boundary)
    canonical = run_agent(
        adapter,
        bundle.canonical_json,
        config=config,
        shorten=shorten,
        reviewer_repository_required=False,
        operation=operation,
        binding_fingerprint=binding_fingerprint,
        pre_start_callback=pre_start_callback,
        attempt_invocation=attempt_invocation,
        prepared_provider_input=prepared,
        execution_root_override=boundary.execution_root,
    )
    try:
        document = json.loads(canonical)
        if not isinstance(document, dict):
            raise AgentOutputError("native Codex result must be a JSON object")
        validate_native_codex_document(document)
        validate_native_codex_provider_response(document, bundle)
        if document.get("request_id") != bundle.bound_context.request_id:
            raise NativeCodexContractError(
                NativeCodexErrorCode.REQUEST_MISMATCH,
                "response request_id does not match bound request",
            )
        if validated_response_callback is not None:
            validated_response_callback(canonical)
        result = parse_bound_native_codex_contract_result(
            document, bundle.bound_context
        )
    except json.JSONDecodeError as exc:
        raise AgentOutputError(
            "native Codex result is not valid JSON",
            technical_text=f"native-json-invalid: {exc}",
        ) from exc
    except (NativeCodexContractError, NativeCodexRequestError) as exc:
        raise AgentOutputError(
            "native Codex result violates its bound contract",
            provider_data=document,
            technical_text=f"{exc.code.value}: {exc.detail}",
            orchestrator_diagnostic=getattr(exc, "orchestrator_diagnostic", None),
        ) from exc
    return NativeAgentCodexOutput(
        result=result,
        canonical_json=canonical,
        request_id=bundle.bound_context.request_id,
        response_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def run_native_codex_agent_checked(
    *,
    adapter: AgentAdapter,
    bundle: NativeCodexRequestBundle,
    raw_response_path: Path,
    config: OrchestratorConfig,
    write_file: Callable[[Path, str], None],
    shorten: Callable[[str | None, int], str],
    operation: str,
    binding_fingerprint: str,
    pre_start_callback: Callable[[ProviderInputMeasurement], object | None] | None,
    provider_attempt_lifecycle: ProviderAttemptLifecycle | None,
    accepted_output_callback: Callable[[NativeAgentCodexOutput], None] | None = None,
    execution_boundary: NativeCodexExecutionBoundary | None = None,
) -> NativeAgentCodexOutput:
    """Persist canonical response bytes before any accepted-result callback."""
    invocation_id = uuid.uuid4().hex
    attempt_invocation = (
        _ProviderAttemptInvocation(provider_attempt_lifecycle)
        if provider_attempt_lifecycle is not None
        else None
    )
    try:
        output = run_native_codex_agent(
            adapter,
            bundle,
            config=config,
            shorten=shorten,
            operation=operation,
            binding_fingerprint=binding_fingerprint,
            pre_start_callback=pre_start_callback,
            attempt_invocation=attempt_invocation,
            execution_boundary=execution_boundary,
            validated_response_callback=lambda canonical: write_file(
                attempt_invocation.response_path(raw_response_path)
                if attempt_invocation is not None
                else raw_response_path,
                canonical,
            ),
        )
        actual_response_path = (
            attempt_invocation.response_path(raw_response_path)
            if attempt_invocation is not None
            else raw_response_path
        )
        print_agent_output(
            adapter.name,
            actual_response_path,
            1,
            output.canonical_json,
            config=config,
            shorten=shorten,
        )
        if accepted_output_callback is not None:
            accepted_output_callback(output)
        if attempt_invocation is not None:
            attempt_invocation.finish(None, adapter.metadata)
        return output
    except AgentInvocationError as failure:
        if attempt_invocation is not None:
            attempt_invocation.finish(failure.kind, adapter.metadata)
        raise
    except ProviderRequestRoundRequired:
        raise
    except ProviderInputBudgetExceeded:
        raise
    except Exception as exc:
        failure = classify_agent_failure(
            adapter.name,
            exc,
            invocation_id=invocation_id,
        )
        if attempt_invocation is not None:
            attempt_invocation.finish(failure.kind, adapter.metadata)
        failure_path = (
            attempt_invocation.failure_path(raw_response_path)
            if attempt_invocation is not None
            else raw_response_path.with_suffix(
                raw_response_path.suffix + ".failure.json"
            )
        )
        try:
            write_file(
                failure_path,
                json.dumps(
                    {
                        "agent": failure.agent_key,
                        "failure_kind": failure.kind.value,
                        "invocation_id": failure.invocation_id,
                        "provider_text": failure.provider_text,
                        "provider_diagnostic": failure.provider_data,
                        "technical_text": failure.technical_text,
                        "received_at": failure.received_at.isoformat(),
                        "process_exit_code": failure.process_exit_code,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
        except Exception as log_exc:  # pragma: no cover - original failure wins
            logger.warning("Native Codex failure log could not be written: %s", log_exc)
        raise failure from exc


_ISO_TIMESTAMP_PATTERN = re.compile(
    r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\b"
)
_RELATIVE_RESET_PATTERN = re.compile(
    r"(?i)\b(?:try again|retry|resets?|available again)\s+(?:after|in)\s+"
    r"(\d+(?:\.\d+)?)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b"
)
_LOCAL_CLOCK_RESET_PATTERN = re.compile(
    r"(?i)\bresets?(?:\s+at)?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)"
    r"\s*\(\s*([A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)+)\s*\)"
)
_CODEX_DATED_LOCAL_RESET_PATTERN = re.compile(
    r"(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+"
    r"(\d{1,2})(?:st|nd|rd|th)?,\s*(\d{4})\s+"
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b"
)
_CODEX_DATED_LOCAL_RESET_TRIGGER_PATTERN = re.compile(
    r"(?i)\b(?:try again|retry|available again|resets?)(?:\s+at)?\s+"
    r"(?=(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b)"
)
_ENGLISH_MONTHS = {
    name: index
    for index, name in enumerate(
        (
            "jan", "feb", "mar", "apr", "may", "jun",
            "jul", "aug", "sep", "oct", "nov", "dec",
        ),
        start=1,
    )
}
_STRUCTURED_ABSOLUTE_KEYS = frozenset(
    {"reset_at", "resets_at", "reset_time", "resettime", "retry_at"}
)
_STRUCTURED_RELATIVE_KEYS = frozenset(
    {"retry_after", "retry_after_seconds", "retryafter", "retry_delay_seconds"}
)


def parse_quota_reset(
    agent_key: str,
    provider_text: str,
    *,
    received_at: datetime,
    provider_data: Mapping[str, object] | None = None,
    local_timezone: tzinfo | None = None,
) -> QuotaReset | None:
    """Parse only unambiguous provider reset evidence, normalized to UTC."""
    if agent_key not in {"codex", "claude"}:
        raise ValueError("quota parser requires a known agent role")
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise ValueError("quota parser received_at must be timezone-aware")
    received_utc = received_at.astimezone(timezone.utc)

    structured = _structured_reset_candidates(provider_data or {})
    if structured:
        distinct = {(item[0], item[2]) for item in structured}
        if len(distinct) != 1:
            return None
        reset_at, parse_path, source_timezone = structured[0]
        if isinstance(reset_at, timedelta):
            absolute = received_utc + reset_at
            source_timezone = received_at.tzname() or str(received_at.tzinfo)
        else:
            absolute = reset_at
        return QuotaReset(absolute, f"{agent_key}:structured:{parse_path}", source_timezone)

    absolute_values: list[datetime] = []
    for match in _ISO_TIMESTAMP_PATTERN.findall(provider_text or ""):
        try:
            parsed = datetime.fromisoformat(match.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            absolute_values.append(parsed)
    distinct_absolute = {item.astimezone(timezone.utc) for item in absolute_values}
    if len(distinct_absolute) == 1:
        parsed = absolute_values[0]
        return QuotaReset(
            parsed,
            f"{agent_key}:text:absolute",
            parsed.tzname() or str(parsed.tzinfo),
        )
    if len(distinct_absolute) > 1:
        return None

    local_clock_values: list[tuple[datetime, str]] = []
    for hour_text, minute_text, meridiem, timezone_name in (
        _LOCAL_CLOCK_RESET_PATTERN.findall(provider_text or "")
    ):
        hour = int(hour_text)
        minute = int(minute_text or "0")
        if not 1 <= hour <= 12 or not 0 <= minute <= 59:
            continue
        try:
            source_zone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            continue
        hour_24 = hour % 12 + (12 if meridiem.lower() == "pm" else 0)
        received_local = received_utc.astimezone(source_zone)
        local_naive = datetime.combine(
            received_local.date(),
            datetime.min.time().replace(hour=hour_24, minute=minute),
        )
        candidate = _unambiguous_local_datetime(local_naive, source_zone)
        if candidate is None:
            continue
        if candidate.astimezone(timezone.utc) <= received_utc:
            local_naive += timedelta(days=1)
            candidate = _unambiguous_local_datetime(local_naive, source_zone)
            if candidate is None:
                continue
        local_clock_values.append((candidate, timezone_name))
    distinct_local_clocks = {
        (item.astimezone(timezone.utc), timezone_name)
        for item, timezone_name in local_clock_values
    }
    if len(distinct_local_clocks) == 1:
        parsed, timezone_name = local_clock_values[0]
        return QuotaReset(
            parsed,
            f"{agent_key}:text:local-clock",
            timezone_name,
        )
    if len(distinct_local_clocks) > 1:
        return None

    dated_local_values: list[tuple[datetime, str]] = []
    if agent_key == "codex" and _CODEX_DATED_LOCAL_RESET_TRIGGER_PATTERN.search(
        provider_text or ""
    ):
        source_zone = local_timezone or _system_local_timezone()
        timezone_name = _timezone_evidence_name(source_zone, received_at)
        if source_zone is not None and timezone_name is not None:
            for month_text, day_text, year_text, hour_text, minute_text, meridiem in (
                _CODEX_DATED_LOCAL_RESET_PATTERN.findall(provider_text or "")
            ):
                hour = int(hour_text)
                minute = int(minute_text or "0")
                if not 1 <= hour <= 12 or not 0 <= minute <= 59:
                    continue
                hour_24 = hour % 12 + (12 if meridiem.lower() == "pm" else 0)
                try:
                    local_naive = datetime(
                        int(year_text),
                        _ENGLISH_MONTHS[month_text.lower()],
                        int(day_text),
                        hour_24,
                        minute,
                    )
                except (KeyError, ValueError):
                    continue
                candidate = _unambiguous_local_datetime(local_naive, source_zone)
                if (
                    candidate is None
                    or candidate.astimezone(timezone.utc) <= received_utc
                ):
                    continue
                dated_local_values.append((candidate, timezone_name))
    distinct_dated_local = {
        (item.astimezone(timezone.utc), timezone_name)
        for item, timezone_name in dated_local_values
    }
    if len(distinct_dated_local) == 1:
        parsed, timezone_name = dated_local_values[0]
        return QuotaReset(
            parsed,
            f"{agent_key}:text:dated-local",
            timezone_name,
        )
    if len(distinct_dated_local) > 1:
        return None

    relative_values: list[timedelta] = []
    for amount_text, unit in _RELATIVE_RESET_PATTERN.findall(provider_text or ""):
        amount = float(amount_text)
        lowered = unit.lower()
        seconds = amount
        if lowered.startswith(("min", "minute")):
            seconds *= 60
        elif lowered.startswith(("h", "hour")):
            seconds *= 3600
        relative_values.append(timedelta(seconds=seconds))
    distinct_relative = {item.total_seconds() for item in relative_values}
    if len(distinct_relative) != 1:
        return None
    delta = relative_values[0]
    return QuotaReset(
        received_utc + delta,
        f"{agent_key}:text:relative",
        received_at.tzname() or str(received_at.tzinfo),
    )


def _unambiguous_local_datetime(
    local_naive: datetime, source_zone: tzinfo
) -> datetime | None:
    """Attach an IANA zone only when the local wall clock identifies one instant."""
    first = local_naive.replace(tzinfo=source_zone, fold=0)
    second = local_naive.replace(tzinfo=source_zone, fold=1)
    if first.utcoffset() != second.utcoffset():
        return None
    roundtrip = first.astimezone(timezone.utc).astimezone(source_zone)
    if roundtrip.replace(tzinfo=None) != local_naive:
        return None
    return first


def _system_local_timezone() -> ZoneInfo | None:
    """Resolve the host's IANA zone; unknown local zones remain fail-closed."""
    candidates: list[str] = []
    configured = os.environ.get("TZ", "").strip()
    if configured:
        candidates.append(configured)
    localtime = Path("/etc/localtime")
    try:
        resolved = localtime.resolve(strict=True).as_posix()
    except (OSError, RuntimeError):
        resolved = ""
    marker = "/zoneinfo/"
    if marker in resolved:
        candidates.append(resolved.split(marker, 1)[1])
    timezone_file = Path("/etc/timezone")
    try:
        configured_file = timezone_file.read_text(encoding="utf-8").strip()
    except OSError:
        configured_file = ""
    if configured_file:
        candidates.append(configured_file)
    for candidate in candidates:
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return None


def _timezone_evidence_name(
    source_zone: tzinfo | None, reference: datetime
) -> str | None:
    if source_zone is None:
        return None
    key = getattr(source_zone, "key", None)
    if isinstance(key, str) and key.strip():
        return key
    localized = reference.astimezone(source_zone)
    return localized.tzname() or str(source_zone)


def _structured_reset_candidates(
    data: Mapping[str, object],
) -> list[tuple[datetime | timedelta, str, str]]:
    candidates: list[tuple[datetime | timedelta, str, str]] = []

    def visit(value: object, path: str) -> None:
        if isinstance(value, Mapping):
            for raw_key, child in value.items():
                key = str(raw_key).lower()
                child_path = f"{path}.{raw_key}" if path else str(raw_key)
                if key in _STRUCTURED_ABSOLUTE_KEYS:
                    parsed: datetime | None = None
                    if isinstance(child, str):
                        try:
                            parsed = datetime.fromisoformat(child.replace("Z", "+00:00"))
                        except ValueError:
                            pass
                    elif (
                        isinstance(child, (int, float))
                        and not isinstance(child, bool)
                        and math.isfinite(float(child))
                        and float(child) >= 0
                    ):
                        try:
                            parsed = datetime.fromtimestamp(float(child), tz=timezone.utc)
                        except (OSError, OverflowError, ValueError):
                            pass
                    if (
                        parsed is not None
                        and parsed.tzinfo is not None
                        and parsed.utcoffset() is not None
                    ):
                        candidates.append(
                            (parsed, child_path, parsed.tzname() or str(parsed.tzinfo))
                        )
                elif (
                    key in _STRUCTURED_RELATIVE_KEYS
                    and isinstance(child, (int, float))
                    and not isinstance(child, bool)
                    and math.isfinite(float(child))
                    and float(child) >= 0
                ):
                    candidates.append(
                        (timedelta(seconds=float(child)), child_path, "relative")
                    )
                visit(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    visit(data, "")
    return candidates


def is_quota_or_rate_limit_error(text: str) -> bool:
    raw = re.sub(r"[_-]+", " ", (text or "").lower())
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


_CLAUDE_SESSION_LIMIT_PATTERN = re.compile(
    r"(?i)\byou(?:'ve| have) hit your session limit\b"
)

_PROVIDER_DIAGNOSTIC_KEYS = frozenset(
    {
        "status",
        "error",
        "code",
        "type",
        "subtype",
        "message",
        "reset_at",
        "resetAt",
        "retry_after",
        "retryAfter",
        "retry_after_seconds",
    }
)


def _sanitize_provider_diagnostic(value: object) -> dict[str, object] | None:
    """Keep technical envelope facts while excluding model output and prompts."""
    if not isinstance(value, Mapping):
        return None
    sanitized: dict[str, object] = {}
    for key, child in value.items():
        if key not in _PROVIDER_DIAGNOSTIC_KEYS:
            continue
        if isinstance(child, Mapping):
            nested = _sanitize_provider_diagnostic(child)
            if nested:
                sanitized[str(key)] = nested
        elif isinstance(child, (str, int, float, bool)) or child is None:
            sanitized[str(key)] = child
    return sanitized or None


def is_structured_output_retry_exhaustion(
    provider_data: Mapping[str, object] | None,
) -> bool:
    """Recognize a provider-side failure to produce schema-conforming output."""
    return (
        isinstance(provider_data, Mapping)
        and provider_data.get("subtype")
        == STRUCTURED_OUTPUT_RETRY_EXHAUSTED_SUBTYPE
    )


def _exception_chain(error: BaseException) -> tuple[BaseException, ...]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__
    return tuple(chain)


def classify_agent_failure(
    agent_key: str,
    exc: BaseException,
    *,
    invocation_id: str,
    received_at: datetime | None = None,
) -> AgentInvocationError:
    """Classify one failed invocation without retrying or substituting its role."""
    stamp = (received_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    explicit_provider_text = getattr(exc, "provider_text", None)
    raw_exception_text = str(exc) or type(exc).__name__
    explicit_technical_text = getattr(exc, "technical_text", None)
    technical_text = str(
        explicit_technical_text
        if isinstance(explicit_technical_text, str) and explicit_technical_text
        else raw_exception_text
        if isinstance(explicit_provider_text, str) and explicit_provider_text
        else f"{type(exc).__name__}: {raw_exception_text}"
    )
    provider_text = str(
        explicit_provider_text
        if isinstance(explicit_provider_text, str) and explicit_provider_text
        else raw_exception_text
    )
    orchestrator_diagnostic = getattr(exc, "orchestrator_diagnostic", None)
    if not isinstance(orchestrator_diagnostic, OrchestratorDiagnostic):
        orchestrator_diagnostic = None
    raw_provider_data = getattr(exc, "provider_data", None)
    provider_data = _sanitize_provider_diagnostic(raw_provider_data)
    process_exit_code = getattr(exc, "exit_code", None)
    kind_hint = getattr(exc, "kind_hint", None)
    structured_output_retry_exhaustion = (
        isinstance(exc, (AgentProcessError, AgentOutputError))
        and is_structured_output_retry_exhaustion(provider_data)
    )
    if structured_output_retry_exhaustion:
        technical_text = (
            f"{type(exc).__name__}: provider_diagnostic.subtype="
            f"{STRUCTURED_OUTPUT_RETRY_EXHAUSTED_SUBTYPE}"
        )
        if technical_text == provider_text:
            technical_text += "; classified=output"
    lowered = technical_text.lower()
    structured_text = (
        json.dumps(provider_data, ensure_ascii=False, sort_keys=True)
        if isinstance(provider_data, Mapping)
        else ""
    )
    claude_technical_session_limit = (
        agent_key == "claude"
        and (
            isinstance(exc, AgentProcessError)
            or (
                isinstance(exc, AgentOutputError)
                and process_exit_code not in (None, 0)
            )
        )
        and _CLAUDE_SESSION_LIMIT_PATTERN.search(technical_text) is not None
    )
    native_review_form_failure = next(
        (
            candidate
            for candidate in _exception_chain(exc)
            if is_retryable_native_review_form_error(candidate)
        ),
        None,
    )
    if (
        is_quota_or_rate_limit_error(technical_text)
        or is_quota_or_rate_limit_error(structured_text)
        or claude_technical_session_limit
    ):
        reset = parse_quota_reset(
            agent_key,
            technical_text,
            received_at=stamp,
            provider_data=provider_data if isinstance(provider_data, Mapping) else None,
        )
        return QuotaReachedError(
            agent_key,
            provider_text,
            invocation_id=invocation_id,
            received_at=stamp,
            quota_reset=reset,
            exit_code=process_exit_code if isinstance(process_exit_code, int) else None,
            provider_data=provider_data,
            technical_text=technical_text,
            orchestrator_diagnostic=orchestrator_diagnostic,
        )
    if native_review_form_failure is not None:
        # Keep the recorded cause honest. The workflow layer separately grants
        # this exact provider-authored review form/content path a bounded retry.
        kind = AgentFailureKind.OUTPUT
    elif structured_output_retry_exhaustion:
        # The provider completed, but its internal retries did not produce a
        # schema-conforming result. Keep that cause distinct from transport.
        kind = AgentFailureKind.OUTPUT
    elif isinstance(kind_hint, AgentFailureKind):
        kind = kind_hint
    elif isinstance(exc, subprocess.TimeoutExpired) or "timed out" in lowered or "timeout" in lowered:
        kind = AgentFailureKind.TIMEOUT
    elif isinstance(exc, AgentPermissionError) or "permission denied" in lowered or "permission request rejected" in lowered:
        kind = AgentFailureKind.PERMISSION
    elif any(marker in lowered for marker in ("unauthorized", "authentication", "invalid api key", "401", "403")):
        kind = AgentFailureKind.AUTH
    elif any(marker in lowered for marker in ("dns", "name resolution", "connection", "network", "econn", "socket", "loopback", "egress")):
        kind = AgentFailureKind.NETWORK
    elif isinstance(exc, FileNotFoundError) or "no such file" in lowered or "missing cli binary" in lowered:
        kind = AgentFailureKind.BINARY
    elif any(marker in lowered for marker in ("empty output", "invalid json", "no non-empty", "unparsable")):
        kind = AgentFailureKind.OUTPUT
    elif process_exit_code not in (None, 0) or "execution error" in lowered or "failed:" in lowered:
        kind = AgentFailureKind.PROCESS
    elif isinstance(exc, AgentOutputError) and provider_text in {
        "native review result violates its bound contract",
        "native Codex result violates its bound contract",
    }:
        kind = AgentFailureKind.OUTPUT
    else:
        kind = AgentFailureKind.RUNTIME
    return AgentInvocationError(
        agent_key=agent_key,
        kind=kind,
        invocation_id=invocation_id,
        provider_text=provider_text,
        received_at=stamp,
        exit_code=process_exit_code if isinstance(process_exit_code, int) else None,
        provider_data=provider_data,
        technical_text=technical_text,
        orchestrator_diagnostic=orchestrator_diagnostic,
    )


def compute_retry_backoff_seconds(error_text: str, attempt: int) -> int:
    exponential = min(30, 2 * (2 ** max(0, attempt - 1)))
    if is_quota_or_rate_limit_error(error_text):
        # Quota/rate issues usually need more time to recover than transient CLI errors.
        return max(10, exponential)
    return exponential


def preflight(
    required_agents: list[str],
    strict: bool,
    agents: dict[str, AgentAdapter],
    *,
    skip_git_check: bool = False,
) -> bool:
    _ = strict
    _ = agents
    ok = True
    logger.info("Preflight: checking git cleanliness; agent capability checks are lazy.")
    if required_agents:
        logger.info(
            "Deferred agent checks until first role use: %s.",
            ", ".join(required_agents),
        )

    if skip_git_check:
        logger.info("Git cleanliness check skipped via --skip-git-check.")
    else:
        git_ok, git_message = check_git_clean()
        if git_ok:
            logger.info("%s", git_message)
        else:
            logger.error("%s", git_message)
            ok = False

    if ok:
        logger.info("Preflight result: OK")
    else:
        logger.error("Preflight result: FAILED")
    return ok


def collect_file_snapshots(
    changed_files: list[str],
    max_lines: int,
    max_files: int,
    *,
    repository_root: Path,
) -> str:
    """Collect bounded plaintext snapshots for changed files referenced in review prompts."""

    def is_plausible_path(value: str) -> bool:
        # Reject markdown/prose lines so only filename-like entries are considered.
        if not value or value.startswith("#"):
            return False
        if value.startswith("..."):
            return False
        if any(character in value for character in ("\x00", "\n", "\r", "|")):
            return False
        return True

    parts: list[str] = []
    seen: set[str] = set()
    selected = 0
    root = repository_root.resolve()
    for raw in changed_files:
        path_text = raw.strip()
        if not is_plausible_path(path_text):
            continue

        try:
            path = resolve_repository_path(path_text, root)
        except PathPolicyError as exc:
            logger.warning("Rejected file snapshot path %r: %s.", path_text, exc)
            continue

        display_path = path.relative_to(root).as_posix() or "."
        if display_path in seen:
            continue
        seen.add(display_path)
        if selected >= max_files:
            break

        selected += 1
        parts.append(f"### {display_path}")
        if not path.exists():
            parts.append("[missing] File does not exist.")
            parts.append("")
            continue
        if path.is_dir():
            parts.append("[skip] Path is a directory.")
            parts.append("")
            continue
        if not path.is_file():
            parts.append("[skip] Path is not a regular file.")
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
