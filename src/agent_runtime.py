from __future__ import annotations

import json
from _thread import LockType
from contextlib import nullcontext
import errno
import hashlib
import logging
import math
import os
import queue
import re
import signal
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
    AgentAdapter,
    NativeImplementerAdapter,
    NativeReviewAdapter,
    AgentBudgetError,
    AgentPermissionError,
    AgentOutputError,
    PROVIDER_FAILURE_METRIC_KEYS,
    NativeCodexExecutionBoundary,
)
from agent_roles import AgentRoleName, AgentSlot, role_for_slot
from path_policy import PathPolicyError, resolve_repository_path
from repo_changes import RepositoryChanges
from contracts import (
    ImplementerContractResult,
    ContractResult,
    FindingRecord,
    ValidationAttestation,
)
from native_implementer_contract import (
    NativeImplementerContext,
    NativeImplementerContractError,
    NativeImplementerErrorCode,
    find_native_implementer_contract_error,
    is_retryable_native_implementer_response_error,
    parse_bound_native_implementer_contract_result,
    validate_native_implementer_document,
)
from native_implementer_request import (
    NativeImplementerRequestBundle,
    NativeImplementerRequestError,
    NativeImplementerRequestErrorCode,
    validate_native_implementer_provider_response,
)
from native_review_contract import (
    NativeReviewContext,
    NativeReviewContractError,
    NativeReviewErrorCode,
    find_native_review_contract_error,
    is_retryable_native_review_response_error,
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
from provider_process import (
    ProcessIdentity, ProcessStatus, capture_process_identity,
    count_process_group_members, observe_identity, signal_process_group,
)
from provider_identity import (
    ProviderIdentity, capture_provider_identity, check_provider_candidate,
    executable_candidates, inspect_provider_installations,
)
from role_occupancy import role_for_provider
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
from rejected_response_shape import (
    RejectedNativeResponseShape,
    extract_rejected_native_response_shape,
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
        native_review_rejection: NativeReviewErrorCode | None = None,
        native_review_rejection_detail: str | None = None,
        native_review_response_retryable: bool = False,
        native_implementer_rejection: NativeImplementerErrorCode | None = None,
        native_implementer_rejection_detail: str | None = None,
        native_implementer_response_retryable: bool = False,
        rejected_response_shape: RejectedNativeResponseShape | None = None,
    ) -> None:
        if orchestrator_diagnostic is not None and not isinstance(
            orchestrator_diagnostic, OrchestratorDiagnostic
        ):
            raise TypeError("orchestrator diagnostic must be a closed enum member")
        if native_review_rejection is not None and not isinstance(
            native_review_rejection, NativeReviewErrorCode
        ):
            raise TypeError("native review rejection must be a closed enum member")
        if native_review_rejection_detail is not None and (
            native_review_rejection is None
            or not native_review_rejection_detail.strip()
            or len(native_review_rejection_detail) > 1200
            or any(
                character in native_review_rejection_detail
                for character in ("\x00", "\r", "\n")
            )
        ):
            raise TypeError(
                "native review rejection detail must be a bounded typed diagnostic"
            )
        if not isinstance(native_review_response_retryable, bool) or (
            native_review_response_retryable and native_review_rejection is None
        ):
            raise TypeError(
                "native review response retryability requires a typed rejection"
            )
        if native_implementer_rejection is not None and not isinstance(
            native_implementer_rejection, NativeImplementerErrorCode
        ):
            raise TypeError("native implementer rejection must be a closed enum member")
        if native_implementer_rejection_detail is not None and (
            native_implementer_rejection is None
            or not native_implementer_rejection_detail.strip()
            or len(native_implementer_rejection_detail) > 1200
            or any(
                character in native_implementer_rejection_detail
                for character in ("\x00", "\r", "\n")
            )
        ):
            raise TypeError(
                "native implementer rejection detail must be a bounded typed diagnostic"
            )
        if not isinstance(native_implementer_response_retryable, bool) or (
            native_implementer_response_retryable
            and native_implementer_rejection is None
        ):
            raise TypeError(
                "native implementer response retryability requires a typed rejection"
            )
        if (
            native_review_rejection is not None
            and native_implementer_rejection is not None
        ):
            raise TypeError("one invocation cannot carry two native rejection roles")
        if rejected_response_shape is not None and (
            not isinstance(rejected_response_shape, RejectedNativeResponseShape)
            or (
                native_review_rejection is None
                and native_implementer_rejection is None
            )
        ):
            raise TypeError(
                "rejected response shape requires one typed native rejection"
            )
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
        self.native_review_rejection = native_review_rejection
        self.native_review_rejection_detail = native_review_rejection_detail
        self.native_review_response_retryable = native_review_response_retryable
        self.native_implementer_rejection = native_implementer_rejection
        self.native_implementer_rejection_detail = native_implementer_rejection_detail
        self.native_implementer_response_retryable = (
            native_implementer_response_retryable
        )
        self.rejected_response_shape = rejected_response_shape
        label = "quota/rate limit reached" if kind is AgentFailureKind.QUOTA else f"{kind.value} failure"
        super().__init__(
            f"{agent_key} {label} [invocation {invocation_id}]: {provider_text}"
        )

    @property
    def readable_orchestrator_diagnostic(self) -> str | None:
        diagnostic = self.orchestrator_diagnostic
        return diagnostic.text if isinstance(diagnostic, OrchestratorDiagnostic) else None

    @property
    def readable_native_review_rejection(self) -> str | None:
        rejection = self.native_review_rejection
        if not isinstance(rejection, NativeReviewErrorCode):
            return None
        detail = self.native_review_rejection_detail
        return (
            f"{rejection.value}: {detail}"
            if isinstance(detail, str)
            else rejection.value
        )

    @property
    def readable_native_implementer_rejection(self) -> str | None:
        rejection = self.native_implementer_rejection
        if not isinstance(rejection, NativeImplementerErrorCode):
            return None
        # Implementer guidance is repository-owned.  The validator detail can
        # contain provider-authored values and must never become feedback or a
        # readable log field.
        return self.readable_orchestrator_diagnostic or rejection.value


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
        orchestrator_diagnostic: OrchestratorDiagnostic | None = None,
        provider_data: Mapping[str, object] | None = None,
    ) -> None:
        self.provider_text = provider_text
        self.exit_code = exit_code
        self.kind_hint = kind_hint
        if orchestrator_diagnostic is not None and not isinstance(orchestrator_diagnostic, OrchestratorDiagnostic):
            raise TypeError("orchestrator diagnostic must be a closed enum member")
        self.orchestrator_diagnostic = orchestrator_diagnostic
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
    merge_completed_branch: bool = True
    archive_run_directory: str = "{run_id}"
    base_branch: str | None = None
    dry_run: bool = False
    agent_output_mode: str = "summary"
    agent_output_max_chars: int = 1800
    agent_live_stream: bool = False
    agent_live_stream_mode: str = "compact"
    agent_live_stream_channels: str = "both"
    repo_root: Path = field(default_factory=lambda: Path.cwd().resolve())
    inbox_dir: Path | None = None
    outbox_dir: Path | None = None
    strict_preflight: bool = False
    phase_progress_threshold_seconds: float = 30.0
    max_acceptance_reviews: int = 6
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
    state: dict[str, object],
) -> str | None:
    """Render useful live progress without leaking provider JSON envelopes."""
    text = line.strip()
    if not text:
        return None

    if channel == "stdout" and getattr(adapter, "live_stream_profile", "plain") == "claude-stream-json":  # allowlist:provider -- transport: implementer stream profile
        from provider_metrics import compact_stream_event
        try:
            event = json.loads(text)
        except ValueError:
            return None
        return compact_stream_event(event, state, adapter.name, getattr(adapter, "live_stream_version_field", "version")) if isinstance(event, dict) else None

    if channel == "stdout" and getattr(adapter, "live_stream_profile", "plain") == "json-events" and text.startswith("{"):
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
        cache_read_input_tokens=integer("cache_read_input_tokens", "cached_input_tokens", "cacheReadInputTokens", "cache_read_tokens"),
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
    terminal: Callable[..., None]
    durable_response_path: Callable[[object], Path] | None = None
    failure_path: Callable[[Path], Path] | None = None
    monotonic_fn: Callable[[], float] = time.monotonic
    process_started: Callable[[object, int], None] | None = None


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

    def process_started(self, pid: int) -> None:
        if self.handle is not None and self.lifecycle.process_started is not None:
            self.lifecycle.process_started(self.handle, pid)

    def failure_path(self, fallback: Path) -> Path:
        response_path = self.response_path(fallback)
        if self.lifecycle.failure_path is not None:
            return self.lifecycle.failure_path(response_path)
        return response_path.with_suffix(response_path.suffix + ".failure.json")

    def finish(self, failure_kind: AgentFailureKind | None, metadata: Mapping[str, object] | None) -> None:
        if self.handle is None or self.monotonic_started is None or self.terminalized:
            return
        self.terminalized = True
        from artifact_models import AttemptPermissionDenial
        denials = tuple(AttemptPermissionDenial(**item) for item in (metadata or {}).get("permission_denials", [])
                       if isinstance(item, dict) and "disposition" in item)
        self.lifecycle.terminal(
            self.handle,
            max(0.0, self.lifecycle.monotonic_fn() - self.monotonic_started),
            failure_kind.value if failure_kind is not None else None,
            normalize_provider_usage(metadata),
            **({"permission_denials": denials} if denials else {}),
            **({"actual_models": tuple(metadata["actual_models"])} if metadata and metadata.get("actual_models") else {}),
            **({"init_model": metadata["init_model"]} if metadata and metadata.get("init_model") else {}),
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
        if manifest_paths != tuple(sorted(set(manifest_paths))):
            raise RuntimeError("reviewer snapshot manifest must be sorted and unique")
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
    *,
    base_dir: Path | None = None,
) -> ReviewerWorkspace:
    """Copy canonical repository files and remove write bits without following links."""
    source = repo_root.resolve()
    container = Path(tempfile.mkdtemp(
        prefix="dao-review-workspace-", dir=str(base_dir) if base_dir is not None else None,
    ))
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
            env={key: value for key, value in os.environ.items()
                 if key in {"HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "TERM", "SYSTEMROOT"}},
        )
        return result.returncode, (result.stdout or ""), (result.stderr or "")
    except Exception as exc:
        return 1, "", str(exc)


def _resolve_agent_binary(binary: str, *, path: str | None = None) -> str | None:
    candidates = executable_candidates(binary, os.environ.get("PATH", os.defpath) if path is None else path)
    return candidates[0] if candidates else None


def _binary_remedy(adapter: AgentAdapter) -> str:
    slot = getattr(adapter, "bound_slot", None)
    if slot is None:
        role = role_for_provider(adapter.name)
        slot = None if role is None else role.value
    if slot is None:
        return (
            f"Remedy: no role is assigned to provider {adapter.name!r}; "
            "configure a supported provider binary or adjust PATH"
        )
    return (
        f"Remedy: set --{slot.replace('_', '-')}-binary or RUN_TASK_{slot.upper()}_BINARY "
        "to an absolute Linux path, or adjust PATH"
    )


def _capability_runner(adapter: AgentAdapter) -> Callable[[list[str]], tuple[int, str, str]]:
    isolated = getattr(adapter, "run_capability_command", None)
    return isolated if callable(isolated) else run_local_command


def _check_bound_provider_identity(adapter: AgentAdapter, *, path: str | None = None) -> ProviderIdentity:
    bound = getattr(adapter, "provider_identity", None)
    slot = getattr(adapter, "bound_slot", adapter.name)
    if bound is None:
        raise AgentCompatibilityError(f"slot={slot} has no verified binary identity")
    if bound.kind != "verified":
        raise AgentCompatibilityError(f"slot={slot}: dry-run binary identity cannot authorize a provider start")
    search_path = os.environ.get("PATH", os.defpath) if path is None else path
    binary = adapter.cli_binary
    if not Path(binary).is_absolute() and Path(bound.entry_path).is_absolute():
        # A provider environment may use a minimal PATH (an isolated reviewer starts
        # from env -i); re-verify the exact entry bound at run start, not a name lookup.
        binary = bound.entry_path
    entry = _resolve_agent_binary(binary, path=search_path)
    if entry is None:
        raise AgentCompatibilityError(f"Missing CLI binary for '{adapter.name}': {adapter.cli_binary}")
    try:
        current = capture_provider_identity(
            entry, adapter.capability.version_args, _capability_runner(adapter),
            path=search_path, expected=bound,
        )
    except ValueError as exc:
        raise AgentCompatibilityError(
            f"slot={slot} binary identity drift at {entry}: {exc}; {_binary_remedy(adapter)}"
        ) from exc
    if current != bound:
        raise AgentCompatibilityError(
            f"slot={slot} binary identity drift at {entry}: expected {bound!r}; "
            f"observed {current!r}; {_binary_remedy(adapter)}"
        )
    return bound


def _bound_launch_command(adapter: AgentAdapter, command: tuple[str, ...]) -> list[str]:
    if not command or command[0] != adapter.cli_binary:
        raise AgentCompatibilityError(
            f"{adapter.name} prepared command does not use its configured binary"
        )
    identity = getattr(adapter, "provider_identity", None)
    if identity is None:
        raise AgentCompatibilityError(f"{adapter.name} has no verified binary identity")
    return [*identity.launch_prefix, *command[1:]]


def verify_agent_capabilities(
    adapter: AgentAdapter, *, strict_dns: bool = False, path: str | None = None,
) -> None:
    """Verify the configured role once, immediately before its first real invocation."""
    if adapter.capability_verified:
        _check_bound_provider_identity(adapter, path=path)
        return
    search_path = os.environ.get("PATH", os.defpath) if path is None else path
    resolved_binary = _resolve_agent_binary(adapter.cli_binary, path=search_path)
    if resolved_binary is None:
        raise AgentCompatibilityError(
            f"Missing CLI binary for '{adapter.name}': {adapter.cli_binary}"
        )
    try:
        check_provider_candidate(resolved_binary, path=search_path)
    except ValueError as exc:
        raise AgentCompatibilityError(
            f"{adapter.name} CLI {resolved_binary}: {exc}; {_binary_remedy(adapter)}"
        ) from exc

    identities, failures = inspect_provider_installations(
        adapter.cli_binary, adapter.capability.version_args, _capability_runner(adapter),
        path=search_path,
    )
    if len(identities) + len(failures) > 1:
        logger.warning(
            "Multiple %s CLI installations: %s",
            adapter.name,
            "; ".join(
                [f"{item.entry_path} -> {item.real_path} ({item.version})" for item in identities]
                + list(failures)
            ),
        )
    identity = next((item for item in identities if item.entry_path == resolved_binary), None)
    if identity is None:
        raise AgentCompatibilityError(
            f"Cannot determine {adapter.name} version using {resolved_binary}: "
            + (next((item for item in failures if item.startswith(resolved_binary + ":")), "unknown error"))
        )
    version_text = identity.version
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

    help_rc, help_out, help_err = _capability_runner(adapter)(
        [*identity.launch_prefix, *adapter.capability.help_args]
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
    adapter.provider_identity = identity
    slot = getattr(adapter, "bound_slot", "reviewer" if adapter.reviewer else "implementer")
    role = "reviewer" if adapter.reviewer else "implementer"
    logger.info(
        "Agent ready: slot=%s role=%s provider=%s binary=%s version=%s model=%s effort=%s timeout=%s profile=%s",
        slot, role, adapter.name,
        identity.real_path,
        version_text,
        adapter.model,
        adapter.effort,
        f"{adapter.timeout}s" if adapter.timeout else "unlimited",
        getattr(adapter, "log_profile", "read-only-reviewer" if adapter.reviewer else "workspace-write-implementer"),
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


def _detach_provider_pipe(stream: TextIO, flags: int) -> None:
    """Unblock eventual pipe users without freeing a descriptor they still own."""
    try:
        target = stream.fileno()
        replacement = os.open(os.devnull, flags)
        if replacement != target:
            try:
                os.dup2(replacement, target)
            finally:
                os.close(replacement)
    except (OSError, ValueError):
        pass


def _signal_unidentified_child(
    process: subprocess.Popen[str], sig: signal.Signals,
) -> None:
    """Use the owned, live Popen handle when /proc identity is unavailable."""
    if process.poll() is not None:
        return
    try:
        if os.getsid(process.pid) == process.pid and os.getpgid(process.pid) == process.pid:
            os.killpg(process.pid, sig)
            return
    except OSError:
        pass
    try:
        if sig is signal.SIGKILL:
            process.kill()
        else:
            process.terminate()
    except ProcessLookupError:
        pass


def _write_provider_input(
    stream: TextIO, prompt: str, errors: queue.Queue[BaseException],
) -> None:
    try:
        stream.write(prompt)
        stream.close()
    except BaseException as exc:
        errors.put(exc)


def _raise_writer_error(errors: queue.Queue[BaseException], process: subprocess.Popen[str]) -> None:
    if errors.empty():
        return
    error = errors.get_nowait()
    if isinstance(error, OSError) and error.errno == errno.EPIPE:
        if process.poll() is None:
            errors.put(error)
        return
    raise error


def _stop_provider_group(
    process: subprocess.Popen[str], identity: ProcessIdentity | None,
    *, drain_seconds: float = 2.0, drained: bool = False,
) -> None:
    """Bound TERM, KILL, pipe drain and leader reap after every exit path."""
    if identity is not None:
        signal_process_group(identity, signal.SIGTERM)
    else:
        _signal_unidentified_child(process, signal.SIGTERM)
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if (identity is None and process.poll() is not None) or (
            identity is not None and observe_identity(identity).status is not ProcessStatus.RUNNING
        ):
            break
        time.sleep(0.05)
    if identity is not None:
        signal_process_group(identity, signal.SIGKILL)
    else:
        _signal_unidentified_child(process, signal.SIGKILL)
    if drained:
        return
    if process.stdin is not None:
        # Bypass TextIOWrapper's lock: a daemon writer can be blocked in write().
        _detach_provider_pipe(process.stdin, os.O_WRONLY)
        process.stdin = None
    try:
        process.communicate(timeout=drain_seconds)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        try:
            process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            pass
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                _detach_provider_pipe(stream, os.O_RDONLY)
        process.stdout = None
        process.stderr = None


def _finish_exited_leader_group(
    process: subprocess.Popen[str], identity: ProcessIdentity | None, agent_key: str,
) -> None:
    """Release inherited pipes while preserving the leader's completed result."""
    remaining = count_process_group_members(identity) if identity is not None else None
    _stop_provider_group(process, identity, drained=True)
    logger.warning(
        "[AGENT] %s terminated %s remaining provider group process(es) after leader exit",
        agent_key, remaining if remaining is not None else "an unknown number of",
    )


@dataclass
class _ModelSilence:
    last_activity: float
    active_tools: dict[str, float] = field(default_factory=dict)
    _observer_warned: bool = field(default=False, repr=False)
    _lock: LockType = field(default_factory=threading.Lock, repr=False)

    def observe(self, adapter: AgentAdapter, line: str, now: float) -> None:
        # Observer failures are neutral stdout activity; never discard a line.
        try:
            started, completed = adapter.tool_activity(line)
            started, completed = tuple(started), tuple(completed)
            if any(not isinstance(identity, str) for identity in (*started, *completed)):
                raise ValueError("tool identities must be strings")
        except Exception:
            if not self._observer_warned:
                logger.warning("[AGENT] tool activity observer failed; treating stdout as neutral activity")
                self._observer_warned = True
            started, completed = (), ()
        with self._lock:
            for identity in started:
                self.active_tools.setdefault(identity, now)
            for identity in completed:
                self.active_tools.pop(identity, None)
            self.last_activity = now

    def duration(self, now: float) -> float:
        with self._lock:
            return 0.0 if self.active_tools else max(0.0, now - self.last_activity)

    def tool_duration(self, now: float) -> float:
        with self._lock:
            return max((max(0.0, now - start) for start in self.active_tools.values()), default=0.0)


def _safe_stdout_observer(callback: Callable[[str], None]) -> Callable[[str], None]:
    """Keep optional observation failures outside the reader's pipe lifecycle."""
    warned = False
    def observe(line: str) -> None:
        nonlocal warned
        try:
            callback(line)
        except Exception:
            if not warned:
                logger.warning("[AGENT] stdout observer failed; retaining neutral output line")
                warned = True
    return observe


def _start_provider_stream_readers(
    process: subprocess.Popen[str], stdin_text: str | None,
    stdout_observer: Callable[[str], None] | None = None,
) -> tuple[queue.Queue, list[threading.Thread], threading.Thread | None, queue.Queue]:
    assert process.stdout is not None and process.stderr is not None
    if stdin_text is None and process.stdin is not None:
        process.stdin.close()
        process.stdin = None
    stream_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
    writer_errors: queue.Queue[BaseException] = queue.Queue()

    observe_stdout = _safe_stdout_observer(stdout_observer) if stdout_observer is not None else None

    def read_stream(stream: TextIO, channel: str) -> None:
        try:
            while line := stream.readline():
                if channel == "stdout" and observe_stdout is not None:
                    observe_stdout(line)
                stream_queue.put((channel, line))
        except (OSError, ValueError):
            # Forced bounded cleanup may replace an outstanding pipe read.
            pass
        finally:
            stream_queue.put((channel, None))
            try:
                stream.close()
            except Exception:
                pass

    threads = [threading.Thread(target=read_stream, args=(process.stdout, "stdout"), daemon=True),
               threading.Thread(target=read_stream, args=(process.stderr, "stderr"), daemon=True)]
    for thread in threads:
        thread.start()
    writer = None
    if stdin_text is not None:
        assert process.stdin is not None
        writer = threading.Thread(target=_write_provider_input,
                                  args=(process.stdin, stdin_text, writer_errors), daemon=True)
        writer.start()
    return stream_queue, threads, writer, writer_errors


def _run_agent_process(
    adapter: AgentAdapter,
    command_parts: list[str],
    stdin_text: str | None,
    *,
    config: OrchestratorConfig,
    env: dict[str, str],
    execution_root: Path,
    timeout_seconds: int | None,
    agent_key: str,
    process_started: Callable[[int], None] | None = None,
    operation: str | None = None,
) -> StreamResult | subprocess.CompletedProcess[str]:
    """Own a provider session until its pipes and process group are settled."""
    process = None
    identity: ProcessIdentity | None = None
    start = time.monotonic()
    stall_limit = (getattr(adapter, "stall_timeout_seconds", 900)
                   if getattr(adapter, "supports_stall_detection", False) else 0)
    tool_limit = (getattr(adapter, "tool_timeout_seconds", 3600)
                  if getattr(adapter, "supports_stall_detection", False) else 0)
    read_lines = config.agent_live_stream or bool(stall_limit or tool_limit)
    silence = _ModelSilence(start)
    finished = False
    group_cleanup_done = False
    try:
        from shutdown_control import defer_shutdown
        with defer_shutdown():
            process = subprocess.Popen(
                command_parts,
                stdin=(subprocess.DEVNULL if getattr(adapter, "stdin_closed_when_unused", False) and stdin_text is None
                       else subprocess.PIPE if read_lines or stdin_text is not None else None),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env=env, cwd=execution_root, bufsize=1, start_new_session=True,
            )
            try:
                identity = capture_process_identity(process.pid)
            except (OSError, ValueError, IndexError, UnicodeError):
                pass
            getattr(adapter, "bind_sandbox_process", lambda pid, identity: None)(process.pid, identity)
            if process_started is not None:
                process_started(process.pid)
        if read_lines:
            stream_queue, threads, writer, writer_errors = _start_provider_stream_readers(
                process, stdin_text,
                (lambda line: silence.observe(adapter, line, time.monotonic())) if stall_limit or tool_limit else None,
            )
            stdout_chunks: list[str] = []
            stderr_chunks: list[str] = []
            stream_state: dict[str, object] = {
                "skip_prompt_echo": False, "last_emitted_line": "",
            }
            completed_channels: set[str] = set()
            last_heartbeat = start
            leader_exit_at: float | None = None
            drain_deadline: float | None = None
            while len(completed_channels) < 2:
                now = time.monotonic()
                if timeout_seconds is not None and now - start > timeout_seconds:
                    raise subprocess.TimeoutExpired(command_parts, timeout_seconds)
                _raise_writer_error(writer_errors, process)
                if process.poll() is not None:
                    leader_exit_at = leader_exit_at or now
                    if (now - leader_exit_at > 1.0 and not group_cleanup_done
                            and any(thread.is_alive() for thread in threads)):
                        _finish_exited_leader_group(process, identity, agent_key)
                        group_cleanup_done = True
                        drain_deadline = time.monotonic() + 2.0
                if (drain_deadline is not None and now > drain_deadline
                        and any(thread.is_alive() for thread in threads)):
                    raise AgentProcessError(
                        f"{agent_key} output pipes remained open after group cleanup.",
                        kind_hint=AgentFailureKind.PROCESS,
                    )
                duration = silence.duration(now)
                tool_duration = silence.tool_duration(now)
                tool_stalled = bool(tool_limit and tool_duration >= tool_limit)
                if (tool_stalled or (stall_limit and duration >= stall_limit)) and process.poll() is None:
                    role = getattr(adapter, "bound_slot", "reviewer" if getattr(adapter, "reviewer", False) else "implementer")
                    detail = (f"provider stalled: {duration / 60:.2f} minutes of model silence; "
                              f"last activity at elapsed {silence.last_activity - start:.1f}s")
                    if tool_stalled:
                        detail = f"provider stalled: tool open {tool_duration:.1f} s (limit {tool_limit} s)"
                    logger.warning("[AGENT] provider=%s role=%s operation=%s model silence=%.1fs; %s",
                                   agent_key, role, operation or "unspecified", duration, detail)
                    raise AgentProcessError(detail, kind_hint=AgentFailureKind.NETWORK,
                                            orchestrator_diagnostic=OrchestratorDiagnostic.PROVIDER_STALLED)
                if now - last_heartbeat >= 30.0:
                    silence_text = f"{int(silence.duration(now))}s" if stall_limit else "disabled"
                    logger.info("[AGENT] %s still running (elapsed: %ss, model silence: %s)", agent_key, int(now - start), silence_text)
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
                if not config.agent_live_stream:
                    continue
                if config.agent_live_stream_channels not in {"both", channel}:
                    continue
                if getattr(adapter, "suppress_live_stream", False):
                    continue
                if config.agent_live_stream_mode == "full" and getattr(adapter, "live_stream_profile", "plain") != "claude-stream-json":  # allowlist:provider -- transport: keep implementer stream redacted
                    logger.info("[%s:%s] %s", agent_key, channel, line.rstrip())
                else:
                    rendered = _compact_stream_text(adapter, channel, line, stream_state)
                    if rendered is not None:
                        emit = logger.warning if rendered.startswith("[PERMISSION_DENIAL]") else logger.info
                        emit("[%s:%s] %s", agent_key, channel, rendered)
            for thread in threads:
                thread.join(timeout=0.2)
            if writer is not None:
                writer.join(timeout=1)
                if writer.is_alive():
                    raise RuntimeError("provider stdin writer did not finish")
                _raise_writer_error(writer_errors, process)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired as exc:
                if timeout_seconds is not None and time.monotonic() - start >= timeout_seconds:
                    raise
                raise AgentProcessError(
                    f"{agent_key} closed output pipes without exiting.",
                    kind_hint=AgentFailureKind.PROCESS,
                ) from exc
            result: StreamResult | subprocess.CompletedProcess[str] = StreamResult(
                process.returncode if process.returncode is not None else 1,
                "".join(stdout_chunks), "".join(stderr_chunks),
            )
        else:
            writer_errors: queue.Queue[BaseException] = queue.Queue()
            writer: threading.Thread | None = None
            if stdin_text is not None:
                assert process.stdin is not None
                input_stream = process.stdin
                process.stdin = None
                writer = threading.Thread(
                    target=_write_provider_input,
                    args=(input_stream, stdin_text, writer_errors), daemon=True,
                )
                writer.start()
            leader_exit_at = None
            drain_deadline = None
            while True:
                now = time.monotonic()
                remaining = None if timeout_seconds is None else timeout_seconds - (now - start)
                if remaining is not None and remaining <= 0:
                    raise subprocess.TimeoutExpired(command_parts, timeout_seconds)
                if drain_deadline is not None and now > drain_deadline:
                    raise AgentProcessError(
                        f"{agent_key} output pipes remained open after group cleanup.",
                        kind_hint=AgentFailureKind.PROCESS,
                    )
                _raise_writer_error(writer_errors, process)
                try:
                    stdout, stderr = process.communicate(timeout=min(0.2, remaining) if remaining is not None else 0.2)
                    break
                except subprocess.TimeoutExpired:
                    if process.poll() is not None:
                        leader_exit_at = leader_exit_at or time.monotonic()
                        if time.monotonic() - leader_exit_at > 1.0 and not group_cleanup_done:
                            _finish_exited_leader_group(process, identity, agent_key)
                            group_cleanup_done = True
                            drain_deadline = time.monotonic() + 2.0
            if writer is not None:
                writer.join(timeout=1)
                if writer.is_alive():
                    raise AgentProcessError(f"{agent_key} stdin writer did not finish.", kind_hint=AgentFailureKind.PROCESS)
                _raise_writer_error(writer_errors, process)
            result = subprocess.CompletedProcess(command_parts, process.returncode, stdout, stderr)
        finished = True
        return result
    except BaseException:
        if identity is None and process is not None:
            try:
                identity = capture_process_identity(process.pid)
            except (OSError, ValueError, IndexError, UnicodeError):
                pass
        if process is not None:
            _stop_provider_group(process, identity, drain_seconds=0.2 if group_cleanup_done else 2.0)
        raise
    finally:
        if finished:
            # The leader can exit before children. Reap any remaining group members.
            _stop_provider_group(process, identity, drained=True)


def _provider_environment(adapter: AgentAdapter) -> dict[str, str]:
    if getattr(adapter, "inherit_process_environment", True):
        return {**os.environ, **adapter.env}
    env = dict(adapter.env)
    for variable in getattr(adapter, "environment_passthrough", ()):
        if variable in os.environ:
            env[variable] = os.environ[variable]
    return env


def _adapter_role(adapter: AgentAdapter) -> str:
    binding = getattr(adapter, "role_binding", None)
    if binding is not None:
        role = getattr(binding, "role", None)
        if not isinstance(role, AgentRoleName):
            raise ValueError("adapter has an invalid role binding")
        return role.value
    slot = getattr(adapter, "bound_slot", None)
    if slot is not None:
        return role_for_slot(AgentSlot(slot)).value
    return AgentRoleName.REVIEWER.value if adapter.reviewer else AgentRoleName.IMPLEMENTER.value


def _require_input_budget_registration(
    agent_key: str, operation: str | None, policy: ProviderInputBudgetPolicy,
) -> None:
    # Preserve the missing-operation diagnosis for legacy scripted adapters.
    # Real starts require a rule in the run's slot-bound policy.
    if not policy.registered_operations(agent_key) and (
        operation or agent_key not in PROVIDER_OPERATIONS
    ):
        raise ProviderInputBudgetError(
            f"adapter {agent_key!r} has no provider input budget registration"
        )



def _bind_process_evidence(adapter, attempt):
    bind = getattr(adapter, "bind_process_evidence", None)
    if bind is not None and attempt.handle is not None and attempt.lifecycle.durable_response_path is not None:
        from provider_process import process_evidence_path
        bind(process_evidence_path(attempt.lifecycle.durable_response_path(attempt.handle)))

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
    """Run once and own both protected-tree hooks, including the sole postcheck."""
    agent_key = adapter.name
    if config.dry_run:
        return build_dry_run_agent_output(agent_key, prompt)
    if getattr(adapter, "requires_attempt_ledger", False) and attempt_invocation is None:
        raise ValueError("adapter start requires a provider attempt ledger")
    _require_input_budget_registration(agent_key, operation, config.provider_input_budget)
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
        if not operation:
            raise ValueError(f"provider input operation is required for {agent_key}")
        root_hook = getattr(adapter, "prepared_execution_root", None)
        prepared_root = root_hook() if callable(root_hook) else None
        if prepared_root is not None:
            execution_root = Path(prepared_root).resolve()
            if not execution_root.is_dir():
                raise ValueError("adapter prepared execution root must be a directory")
        elif adapter.reviewer:
            if not reviewer_repository_required:
                workspace = create_empty_reviewer_workspace()
            else:
                workspace = create_read_only_reviewer_workspace(
                    execution_root, reviewer_manifest_paths
                )
            source_root = execution_root
            execution_root = workspace.root
            adapter.bind_reviewer_workspace(source_root, execution_root)

        if prepared_provider_input is not None:
            prepared = prepared_provider_input
        else:
            prepared = adapter.prepare_provider_input(prompt)
        effective_operation = operation
        measurement = measure_provider_input(
            prepared,
            provider=agent_key,
            role=_adapter_role(adapter),
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

        env = _provider_environment(adapter)
        verify_agent_capabilities(
            adapter, strict_dns=config.strict_preflight,
            path=env.get("PATH", os.defpath),
        )
        command_parts = _bound_launch_command(adapter, prepared.command)
        stdin_text = prepared.stdin_text
        if adapter.reviewer and getattr(adapter, "sanitize_reviewer_environment", True):
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            for variable in (
                "RUN_TASK_REVIEW_TEST_COMMAND",
                "RUN_TASK_REVIEW_PROBE_PATH",
                "RUN_TASK_REVIEW_TIMEOUT",
            ):
                env.pop(variable, None)
        if getattr(adapter, "set_pwd", True):
            env["PWD"] = str(execution_root)

        if attempt_invocation is not None:
            attempt_invocation.begin(measurement, bootstrap_context)
            _bind_process_evidence(adapter, attempt_invocation)

        getattr(adapter, "before_provider_process", lambda: None)()
        try:
            result = _run_agent_process(
                adapter,
                command_parts,
                stdin_text,
                config=config,
                env=env,
                execution_root=execution_root,
                timeout_seconds=timeout_seconds,
                agent_key=agent_key,
                operation=operation,
                process_started=(
                    attempt_invocation.process_started
                    if attempt_invocation is not None else None
                ),
            )
        finally:
            getattr(adapter, "after_provider_process", lambda: None)()

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        try:
            adapter.validate_process_output(stderr)
            extra_files["exit_code"] = str(result.returncode)
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
        slot = getattr(adapter, "bound_slot", "reviewer" if adapter.reviewer else "implementer")
        role = "reviewer" if adapter.reviewer else "implementer"
        if config.agent_live_stream and config.agent_live_stream_mode == "compact":
            summary_lines = _compact_result_lines(output)
            if summary_lines:
                for summary_line in summary_lines:
                    logger.info("[AGENT_RESULT] slot=%s role=%s provider=%s %s", slot, role, agent_key, summary_line)
            else:
                logger.info("[AGENT_RESULT] slot=%s role=%s provider=%s completed", slot, role, agent_key)
        normalized_usage = normalize_provider_usage(adapter.metadata)
        if normalized_usage is not None:
            if config.agent_live_stream_mode == "full" or config.agent_output_mode == "full":
                logger.info(
                    "[AGENT_USAGE] slot=%s role=%s provider=%s operation=%s usage=%s",
                    slot, role, agent_key,
                    effective_operation,
                    json.dumps(asdict(normalized_usage), ensure_ascii=False, sort_keys=True),
                )
            else:
                logger.info(
                    "[AGENT_USAGE] slot=%s role=%s provider=%s operation=%s %s",
                    slot, role, agent_key,
                    effective_operation,
                    _compact_usage_metadata(adapter.metadata),
                )
        logger.info(
            "[PROVIDER_COMPLETION] slot=%s role=%s provider=%s operation=%s success=true elapsed=%.2fs usage=%s",
            slot, role, agent_key,
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
class RecoveredFindingComparison:
    """Request-time authority needed after record-ahead result persistence."""

    request_findings: tuple[FindingRecord, ...]
    offered_findings: tuple[FindingRecord, ...]
    request_position: str
    recovery_position: str


@dataclass(frozen=True, slots=True)
class NativeAgentReviewOutput:
    """One schema- and request-bound native reviewer result."""

    result: ContractResult
    canonical_json: str
    request_id: str
    context: NativeReviewContext | None = None
    recovered_finding_comparison: RecoveredFindingComparison | None = field(
        default=None,
        compare=False,
    )


def run_native_review_agent(
    adapter: NativeReviewAdapter,
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
    boundary = getattr(adapter, "review_execution_boundary", None)
    scope = boundary(config.repo_root, reviewer_manifest_paths) if callable(boundary) else nullcontext()
    with scope:
        prepared = adapter.prepare_native_provider_input(bundle)
        seal = getattr(adapter, "seal_provider_input", None)
        if callable(seal):
            seal()
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
            orchestrator_diagnostic=exc.orchestrator_diagnostic,
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
                orchestrator_diagnostic=form_error.orchestrator_diagnostic,
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
    adapter: NativeReviewAdapter,
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
    """Capture and publish one native review attempt in order."""
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
            quota_reset_profile=getattr(adapter, "quota_reset_profile", "standard"),
            session_limit_profile=getattr(adapter, "session_limit_profile", "standard"),
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
                    "orchestrator_diagnostic": (
                        failure.readable_orchestrator_diagnostic
                    ),
                    "received_at": failure.received_at.isoformat(),
                    "process_exit_code": failure.process_exit_code,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        raise failure from exc


@dataclass(frozen=True, slots=True)
class NativeAgentImplementerOutput:
    """One schema-, request-, and domain-bound native implementer result."""

    result: ImplementerContractResult
    canonical_json: str
    request_id: str
    response_sha256: str
    context: NativeImplementerContext | None = field(default=None, compare=False)
    recovered_finding_comparison: RecoveredFindingComparison | None = field(
        default=None,
        compare=False,
    )


def run_native_implementer_agent(
    adapter: NativeImplementerAdapter,
    bundle: NativeImplementerRequestBundle,
    *,
    config: OrchestratorConfig,
    shorten: Callable[[str | None, int], str],
    operation: str,
    binding_fingerprint: str,
    pre_start_callback: Callable[[ProviderInputMeasurement], object | None] | None = None,
    attempt_invocation: _ProviderAttemptInvocation | None = None,
    validated_response_callback: Callable[[str], None] | None = None,
    execution_boundary: NativeCodexExecutionBoundary | None = None,
) -> NativeAgentImplementerOutput:
    """Run a registered native implementer without marker parsing or repair."""
    boundary_profile = getattr(adapter, "execution_boundary_profile", None)
    if boundary_profile == "typed-sandbox":
        boundary = execution_boundary or NativeCodexExecutionBoundary.production(
            config.repo_root
        )
        if boundary.mode.value == "production":
            adapter.bind_implementer_boundary(config.repo_root, config.inbox_dir, config.outbox_dir,
                                             bundle.bound_context.context.run_id)
        prepared = adapter.prepare_native_provider_input(bundle, boundary)
        execution_root = boundary.execution_root
    elif boundary_profile == "claude-write-boundary":  # allowlist:provider -- transport: implementer boundary
        if execution_boundary is not None:
            raise ValueError("execution boundary is not supported by this transport")
        adapter.bind_implementer_boundary(
            config.repo_root, config.inbox_dir, config.outbox_dir,
            bundle.bound_context.context.run_id,
        )
        prepared = adapter.prepare_native_provider_input(bundle)
        execution_root = config.repo_root.resolve()
    else:
        raise ValueError(f"unknown implementer execution boundary profile: {boundary_profile!r}")
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
        execution_root_override=execution_root,
    )
    try:
        document = json.loads(canonical)
        if not isinstance(document, dict):
            raise AgentOutputError("native Codex result must be a JSON object")
        validate_native_implementer_document(document)
        validate_native_implementer_provider_response(document, bundle)
        if document.get("request_id") != bundle.bound_context.request_id:
            raise NativeImplementerContractError(
                NativeImplementerErrorCode.REQUEST_MISMATCH,
                "response request_id does not match bound request",
            )
        if validated_response_callback is not None:
            validated_response_callback(canonical)
        result = parse_bound_native_implementer_contract_result(
            document, bundle.bound_context
        )
    except json.JSONDecodeError as exc:
        raise AgentOutputError(
            "native Codex result is not valid JSON",
            technical_text=f"native-json-invalid: {exc}",
        ) from exc
    except NativeImplementerContractError as exc:
        raise AgentOutputError(
            "native implementer result violates its bound contract",
            provider_data=document,
            technical_text=f"{exc.code.value}: {exc.detail}",
            orchestrator_diagnostic=exc.orchestrator_diagnostic,
        ) from exc
    except NativeImplementerRequestError as exc:
        if exc.code is NativeImplementerRequestErrorCode.SCHEMA_INVALID:
            form_error = NativeImplementerContractError(
                NativeImplementerErrorCode.SCHEMA_INVALID, exc.detail
            )
            raise AgentOutputError(
                "native implementer result violates its bound contract",
                provider_data=document,
                technical_text=f"{form_error.code.value}: {form_error.detail}",
                orchestrator_diagnostic=form_error.orchestrator_diagnostic,
            ) from form_error
        raise AgentOutputError(
            "native Codex result violates its bound contract",
            provider_data=document,
            technical_text=f"{exc.code.value}: {exc.detail}",
        ) from exc
    return NativeAgentImplementerOutput(
        result=result,
        canonical_json=canonical,
        request_id=bundle.bound_context.request_id,
        response_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        context=bundle.bound_context.context,
    )


def run_native_implementer_agent_checked(
    *,
    adapter: NativeImplementerAdapter,
    bundle: NativeImplementerRequestBundle,
    raw_response_path: Path,
    config: OrchestratorConfig,
    write_file: Callable[[Path, str], None],
    shorten: Callable[[str | None, int], str],
    operation: str,
    binding_fingerprint: str,
    pre_start_callback: Callable[[ProviderInputMeasurement], object | None] | None,
    provider_attempt_lifecycle: ProviderAttemptLifecycle | None,
    accepted_output_callback: Callable[[NativeAgentImplementerOutput], None] | None = None,
    execution_boundary: NativeCodexExecutionBoundary | None = None,
) -> NativeAgentImplementerOutput:
    """Persist canonical response bytes before any accepted-result callback."""
    invocation_id = uuid.uuid4().hex
    attempt_invocation = (
        _ProviderAttemptInvocation(provider_attempt_lifecycle)
        if provider_attempt_lifecycle is not None
        else None
    )
    try:
        output = run_native_implementer_agent(
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
            quota_reset_profile=getattr(adapter, "quota_reset_profile", "standard"),
            session_limit_profile=getattr(adapter, "session_limit_profile", "standard"),
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
                        "orchestrator_diagnostic": (
                            failure.readable_orchestrator_diagnostic
                        ),
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
    reset_profile: str | None = None,
) -> QuotaReset | None:
    """Parse only unambiguous provider reset evidence, normalized to UTC."""
    if reset_profile is None:
        from agent_adapters import _implementer_transports, _review_transports
        transport = _implementer_transports().get(agent_key) or _review_transports().get(agent_key)
        if transport is None:
            raise ValueError("quota parser requires a registered transport")
        reset_profile = transport.quota_reset_profile
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
    if reset_profile == "dated-local" and _CODEX_DATED_LOCAL_RESET_TRIGGER_PATTERN.search(
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
    numeric_field = re.compile(
        r'(?P<key>"[^"\n]+"|[A-Za-z_][\w-]*)\s*[:=]\s*'
        r'"?-?\d+(?:[.,]\d+)*(?:[eE][+-]?\d+)?"?'
    )

    def hide_numeric_value(match: re.Match[str]) -> str:
        key = match.group("key").strip('"').lower()
        return match.group() if key in {"status", "code", "status_code"} else " "

    raw = re.sub(r"[_-]+", " ", numeric_field.sub(hide_numeric_value, text or "").lower())
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
    return any(
        re.search(
            r"(?<![\w.])(?<!\d,)429(?!\w|[.,]\d)"
            if marker == "429"
            else rf"(?<!\w){re.escape(marker)}(?!\w)",
            raw,
        ) is not None
        for marker in markers
    )


_CLAUDE_SESSION_LIMIT_PATTERN = re.compile(
    r"(?i)\byou(?:'ve| have) hit your session limit\b"
)


def is_provider_overload_error(text: str) -> bool:
    """Recognize capacity errors, never incidental numbers in model prose."""
    raw = text or ""
    words = re.sub(r"[_-]+", " ", raw.lower())
    if re.search(r"\b(?:at capacity|overloaded(?: error)?|server is busy|service unavailable|temporarily unavailable)\b", words):
        return True
    return re.search(
        r'(?im)(?:\bHTTP(?:/\d(?:\.\d)?)?\s+(?:status(?:\s+code)?\s*[:=]?\s*)?'
        r'|["\x27]?\b(?:status|code|status_code|http_status|http_status_code)["\x27]?\s*[:=]\s*["\x27]?'
        r'|\bstatus\s+code\s*[:=]?\s*)(?:503|529)(?![\w.]|,\d)'
        r'|^\s*(?:503|529)\s*$', raw,
    ) is not None

def _is_provider_overload_failure(exc, kind_hint, process_exit_code, technical_text, structured_text, provider_data) -> bool:
    lowered = technical_text.lower()
    return (
        kind_hint in (None, AgentFailureKind.PROCESS, AgentFailureKind.NETWORK)
        and not isinstance(exc, AgentPermissionError)
        and not (isinstance(exc, AgentOutputError) and any(marker in lowered
                 for marker in ("empty output", "invalid json", "no non-empty", "unparsable")))
        and not any(marker in lowered or marker in structured_text.lower()
                    for marker in ("unauthorized", "authentication", "invalid api key", "401", "403"))
        and (not isinstance(exc, AgentOutputError) or process_exit_code not in (None, 0)
             or isinstance(provider_data, Mapping) and ("error" in provider_data or str(provider_data.get("subtype", "")).startswith("error")))
        and (is_provider_overload_error(technical_text) or is_provider_overload_error(structured_text))
    )


_PROVIDER_DIAGNOSTIC_KEYS = frozenset(
    {
        "status",
        "status_code",
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
        if key in PROVIDER_FAILURE_METRIC_KEYS:
            if key == "permission_denials":
                from provider_metrics import permission_denial_summaries
                child = permission_denial_summaries(child)
            sanitized[str(key)] = child
            continue
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


def _registered_session_limit_profile(agent_key: str) -> str:
    from agent_adapters import _implementer_transports, _review_transports

    transport = _implementer_transports().get(agent_key) or _review_transports().get(agent_key)
    return getattr(transport, "session_limit_profile", "standard")


def _diagnostic_texts(value: object) -> list[str]:
    if not isinstance(value, Mapping):
        return []
    found: list[str] = []
    for key, child in value.items():
        if isinstance(child, Mapping):
            found.extend(_diagnostic_texts(child))
        elif isinstance(child, str) and key in _PROVIDER_DIAGNOSTIC_KEYS:
            found.append(f"{key}={child}" if key in {"status", "status_code", "code"} else child)
        elif key in {"status", "status_code", "code"} and isinstance(child, int):
            found.append(f"{key}={child}")
    return found


def classify_agent_failure(
    agent_key: str,
    exc: BaseException,
    *,
    invocation_id: str,
    received_at: datetime | None = None,
    quota_reset_profile: str | None = None,
    session_limit_profile: str | None = None,
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
        # Imported lazily because error_classification owns the complete
        # project-exception inventory and imports this runtime module.
        from error_classification import orchestrator_diagnostic_for_exception

        orchestrator_diagnostic = orchestrator_diagnostic_for_exception(exc)
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
    structured_text = " ".join(_diagnostic_texts(provider_data))
    if session_limit_profile is None:
        session_limit_profile = _registered_session_limit_profile(agent_key)
    technical_session_limit = (
        session_limit_profile == "technical-session-limit"
        and (
            isinstance(exc, AgentProcessError)
            or (
                isinstance(exc, AgentOutputError)
                and process_exit_code not in (None, 0)
            )
        )
        and _CLAUDE_SESSION_LIMIT_PATTERN.search(technical_text) is not None
    )
    native_review_form_failure = find_native_review_contract_error(exc)
    native_implementer_form_failure = find_native_implementer_contract_error(exc)
    if (
        native_review_form_failure is not None
        and native_implementer_form_failure is not None
    ):
        raise TypeError("one invocation cannot contain two native rejection roles")
    if (
        native_review_form_failure is not None
        or native_implementer_form_failure is not None
    ):
        # A validated provider response was rejected by deterministic local
        # semantics.  Provider-like words inside its prose cannot turn that
        # response-content fact into a transient quota or transport failure.
        kind = AgentFailureKind.OUTPUT
    elif structured_output_retry_exhaustion:
        # A completed provider result with this subtype identifies output exhaustion.
        kind = AgentFailureKind.OUTPUT
    elif isinstance(exc, AgentOutputError) and isinstance(kind_hint, AgentFailureKind):
        # An adapter's typed output diagnosis takes precedence over textual
        # provider heuristics, including quota reset parsing.
        if kind_hint is AgentFailureKind.QUOTA:
            return QuotaReachedError(
                agent_key, provider_text, invocation_id=invocation_id,
                received_at=stamp, exit_code=process_exit_code,
                provider_data=provider_data, technical_text=technical_text,
                orchestrator_diagnostic=orchestrator_diagnostic,
            )
        kind = kind_hint
    elif (
        is_quota_or_rate_limit_error(technical_text)
        or is_quota_or_rate_limit_error(structured_text)
        or technical_session_limit
    ):
        reset = parse_quota_reset(
            agent_key,
            technical_text,
            received_at=stamp,
            provider_data=provider_data if isinstance(provider_data, Mapping) else None,
            reset_profile=quota_reset_profile,
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
    elif _is_provider_overload_failure(
        exc, kind_hint, process_exit_code, technical_text, structured_text, provider_data
    ):
        kind = AgentFailureKind.NETWORK
        orchestrator_diagnostic = OrchestratorDiagnostic.PROVIDER_OVERLOADED
        technical_text = "provider overloaded: " + technical_text
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
        native_review_rejection=(
            native_review_form_failure.code
            if native_review_form_failure is not None
            else None
        ),
        native_review_rejection_detail=(
            _bounded_native_response_rejection_detail(native_review_form_failure)
            if native_review_form_failure is not None
            else None
        ),
        native_review_response_retryable=(
            native_review_form_failure is not None
            and is_retryable_native_review_response_error(
                native_review_form_failure
            )
        ),
        native_implementer_rejection=(
            native_implementer_form_failure.code
            if native_implementer_form_failure is not None
            else None
        ),
        native_implementer_rejection_detail=(
            _bounded_native_response_rejection_detail(
                native_implementer_form_failure
            )
            if native_implementer_form_failure is not None
            else None
        ),
        native_implementer_response_retryable=(
            native_implementer_form_failure is not None
            and is_retryable_native_implementer_response_error(
                native_implementer_form_failure
            )
        ),
        rejected_response_shape=(
            extract_rejected_native_response_shape(raw_provider_data)
            if (
                native_review_form_failure is not None
                or native_implementer_form_failure is not None
            )
            else None
        ),
    )


def _bounded_native_response_rejection_detail(
    error: NativeReviewContractError | NativeImplementerContractError,
) -> str:
    """Keep local validator feedback single-line and safe for logs/requests."""

    detail = " ".join(error.detail.replace("\x00", " ").split())
    if not detail:
        detail = getattr(error, "operator_detail", None) or error.code.value
    return detail[:1200]


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
    ok = True
    logger.info("Preflight: checking git cleanliness; agent capability checks are lazy.")
    for slot in required_agents:
        adapter = agents.get(slot)
        if adapter is not None and getattr(adapter, "certification_status", None) == "experimental":
            logger.warning("Preflight: slot=%s provider=%s status=experimental; check the provider egress disclosure before use.", slot, adapter.name)
        disclosure = getattr(adapter, "egress_disclosure", None)
        if disclosure is not None:
            destination, components = disclosure
            logger.info(
                "Preflight provider egress: slot=%s provider=%s destination=%s data=%s",
                slot, adapter.name, destination, ", ".join(components),
            )
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
