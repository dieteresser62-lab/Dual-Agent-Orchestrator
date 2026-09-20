from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Mapping

from acceptance_criteria import acceptance_criteria_from_documents
from artifact_models import (
    ArtifactValidationError,
    FamilyBindingPayload,
    family_binding_document,
)
from contracts import PlannedSlice
from finding_order import finding_id_sort_key
from finding_responsibility import (
    FindingResponsibility,
    parse_responsibility,
    responsibility_document,
)
import native_finding_decisions
from orchestrator_diagnostics import ORCHESTRATOR_DIAGNOSTIC_TEXTS


STATE_VERSION = 3
DEFAULT_MAX_CODEX_RETURNS = 4
IMPLEMENTER_RETURN_SAFETY_LIMIT = 256
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
TECHNICAL_TEXT_MARKER_PATTERN = re.compile(
    r"^\[technical text redacted; sha256=[0-9a-f]{64}; "
    r"utf8_bytes=[1-9][0-9]*\]$"
)
QUOTA_RESUME_DIFF_PATTERN = re.compile(
    r"^QUOTA-RESUME-DIFF \| .*\bgot ([0-9a-f]{64})$"
)


def quota_resume_diff_acknowledgement(
    invocation_id: str, fingerprint: str
) -> str:
    return f"quota-resume-diff:{invocation_id}:{fingerprint}"


class WorkflowStateValidationError(ValueError):
    """Raised when a version-3 workflow state is structurally inconsistent."""


class WorkUnitKind(str, Enum):
    PLAN = "plan"
    SLICE = "slice"
    BRANCH_DISCOVERY = "branch_discovery"


class WorkflowStep(str, Enum):
    CODEX_PLAN = "codex_plan"
    CLAUDE_PLAN_REVIEW = "claude_plan_review"
    CODEX_PLAN_REVISION = "codex_plan_revision"
    CODEX_IMPLEMENTATION = "codex_implementation"
    CLAUDE_SLICE_REVIEW = "claude_slice_review"
    CODEX_CORRECTION = "codex_correction"
    SLICE_COMMIT = "slice_commit"
    CLAUDE_BRANCH_DISCOVERY = "claude_branch_discovery"
    COMPLETED = "completed"


class WorkUnitStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_USER_DECISION = "awaiting_user_decision"
    WAITING_FOR_QUOTA = "waiting_for_quota"
    WAITING_FOR_RETRY = "waiting_for_retry"
    AWAITING_RESUME = "awaiting_resume"
    COMPLETED = "completed"


class SliceStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_USER_DECISION = "awaiting_user_decision"
    WAITING_FOR_QUOTA = "waiting_for_quota"
    WAITING_FOR_RETRY = "waiting_for_retry"
    AWAITING_RESUME = "awaiting_resume"
    COMPLETED = "completed"


class GateStatus(str, Enum):
    CLEAR = "clear"
    AWAITING_USER_DECISION = "awaiting_user_decision"
    WAITING_FOR_QUOTA = "waiting_for_quota"
    WAITING_FOR_RETRY = "waiting_for_retry"
    AWAITING_RESUME = "awaiting_resume"


class GateReason(str, Enum):
    NONE = "none"
    ITERATION_LIMIT = "iteration_limit"
    TEST_CHANGE = "test_change"
    STOP_REQUEST = "stop_request"
    UNEXPECTED_FILE = "unexpected_file"
    ANCHOR_CHANGE = "anchor_change"
    MANUAL_SLICE = "manual_slice"
    PLAN_APPROVAL = "plan_approval"
    QUOTA = "quota"
    INSTANCE_FAILURE = "instance_failure"
    BOOTSTRAP_CHECK = "bootstrap_check"
    QUOTA_RESUME_DIFF = "quota_resume_diff"


class AgentFailureKind(str, Enum):
    QUOTA = "quota"
    AUTH = "auth"
    NETWORK = "network"
    PERMISSION = "permission"
    TIMEOUT = "timeout"
    BINARY = "binary"
    PROCESS = "process"
    OUTPUT = "output"
    RUNTIME = "runtime"


class Reviewer(str, Enum):
    CLAUDE = "claude"


NATIVE_REVIEW_RESPONSE_REJECTION_CODES = frozenset(
    {
        "schema-invalid",
        "request-mismatch",
        "reviewer-mismatch",
        "finding-id-invalid",
        "finding-reference-unknown",
        "finding-reference-not-open",
        "finding-event-conflict",
        "missing-own-finding-update",
        "finding-content-invalid",
        "finding-signature-duplicate",
        "acceptance-invalid",
        "anchor-invalid",
        "review-content-missing",
        "stop-content-invalid",
        "approval-invalid",
        "dormant-finding-decision-field",
    }
)
NATIVE_IMPLEMENTER_RESPONSE_REJECTION_CODES = frozenset(
    {
        "schema-invalid",
        "request-mismatch",
        "result-kind-mismatch",
        "result-content-invalid",
        "finding-reference-invalid",
        "test-files-invalid",
        "slice-plan-invalid",
        "stop-content-invalid",
        "dormant-finding-decision-field",
    }
)


def is_native_review_output_retry(
    failure_kind: AgentFailureKind, role: str, step: WorkflowStep
) -> bool:
    """Whether state-v3 can represent the typed native-review retry path."""
    return (
        failure_kind is AgentFailureKind.OUTPUT
        and role in {reviewer.value for reviewer in Reviewer}
        and step.value.startswith(f"{role}_")
        and step.value.endswith("_review")
    )


def is_native_implementer_output_retry(
    failure_kind: AgentFailureKind, role: str, step: WorkflowStep
) -> bool:
    """Whether state-v3 can represent the typed native-implementer retry path."""
    return (
        failure_kind is AgentFailureKind.OUTPUT
        and role == "codex"
        and step.value.startswith("codex_")
    )


def is_native_response_output_retry(
    failure_kind: AgentFailureKind, role: str, step: WorkflowStep
) -> bool:
    """Whether state-v3 can represent either native response retry path."""
    return is_native_review_output_retry(
        failure_kind, role, step
    ) or is_native_implementer_output_retry(failure_kind, role, step)


class ProtocolMode(str, Enum):
    LEGACY_STATE_V3 = "legacy-state-v3"
    STRUCTURED_V1 = "structured-v1"
    STRUCTURED_V2 = "structured-v2"


NATIVE_CLAUDE_REVIEW_TRANSPORT = "native-claude-review-v2"
NATIVE_CODEX_RESULT_TRANSPORT = "native-codex-v2"


@dataclass(frozen=True)
class AgentProfileBinding:
    """Immutable model and reasoning selection for one workflow role."""

    model: str
    effort: str

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model.strip():
            raise WorkflowStateValidationError("agent profile model must be non-empty")
        if not isinstance(self.effort, str) or not self.effort.strip():
            raise WorkflowStateValidationError("agent profile effort must be non-empty")
        if self.model != self.model.strip():
            raise WorkflowStateValidationError("agent profile model must be canonical")
        if self.effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise WorkflowStateValidationError("agent profile effort is unsupported")

    def to_dict(self) -> dict[str, str]:
        return {"model": self.model, "effort": self.effort}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], label: str) -> AgentProfileBinding:
        if set(raw) != {"model", "effort"}:
            raise WorkflowStateValidationError(f"{label} has unknown or missing fields")
        return cls(
            model=_string(raw["model"], f"{label} model"),
            effort=_string(raw["effort"], f"{label} effort"),
        )


@dataclass(frozen=True)
class ProtocolBinding:
    """Immutable selection of the persistence protocol for one workflow."""

    mode: ProtocolMode
    schema_version: str
    claude_review_transport: str | None = NATIVE_CLAUDE_REVIEW_TRANSPORT
    codex_result_transport: str | None = NATIVE_CODEX_RESULT_TRANSPORT
    codex_profile: AgentProfileBinding = AgentProfileBinding("gpt-5.6-sol", "medium")
    claude_profile: AgentProfileBinding = AgentProfileBinding("sonnet", "high")

    def __post_init__(self) -> None:
        if not isinstance(self.mode, ProtocolMode):
            raise WorkflowStateValidationError("protocol mode is invalid")
        _require_non_empty(self.schema_version, "protocol schema_version")
        expected = {
            ProtocolMode.LEGACY_STATE_V3: "3",
            ProtocolMode.STRUCTURED_V1: "1",
            ProtocolMode.STRUCTURED_V2: "2",
        }[self.mode]
        if self.schema_version != expected:
            raise WorkflowStateValidationError(
                f"protocol mode {self.mode.value} requires schema_version {expected}"
            )
        if self.claude_review_transport is not None:
            if self.mode is not ProtocolMode.STRUCTURED_V2:
                raise WorkflowStateValidationError(
                    "native Claude review transport requires structured-v2"
                )
            if self.claude_review_transport != NATIVE_CLAUDE_REVIEW_TRANSPORT:
                raise WorkflowStateValidationError(
                    "claude_review_transport is unsupported"
                )
        if self.codex_result_transport is not None:
            if self.mode is not ProtocolMode.STRUCTURED_V2:
                raise WorkflowStateValidationError(
                    "native Codex result transport requires structured-v2"
                )
            if self.codex_result_transport != NATIVE_CODEX_RESULT_TRANSPORT:
                raise WorkflowStateValidationError(
                    "codex_result_transport is unsupported"
                )
        if self.mode is ProtocolMode.STRUCTURED_V2 and (
            self.claude_review_transport != NATIVE_CLAUDE_REVIEW_TRANSPORT
            or self.codex_result_transport != NATIVE_CODEX_RESULT_TRANSPORT
        ):
            raise WorkflowStateValidationError(
                "structured-v2 requires the complete native Codex-Claude transport binding"
            )

    def to_dict(self) -> dict[str, object]:
        result = {"mode": self.mode.value, "schema_version": self.schema_version}
        if self.claude_review_transport is not None:
            result["claude_review_transport"] = self.claude_review_transport
        if self.codex_result_transport is not None:
            result["codex_result_transport"] = self.codex_result_transport
        result["codex_profile"] = self.codex_profile.to_dict()
        result["claude_profile"] = self.claude_profile.to_dict()
        return result

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ProtocolBinding:
        keys = set(raw)
        if not {"mode", "schema_version"}.issubset(keys) or not keys.issubset(
            {
                "mode",
                "schema_version",
                "claude_review_transport",
                "codex_result_transport",
                "codex_profile",
                "claude_profile",
            }
        ):
            raise WorkflowStateValidationError(
                "protocol binding has unknown or missing fields"
            )
        return cls(
            mode=_enum_value(ProtocolMode, raw["mode"], "protocol mode"),
            schema_version=_string(raw["schema_version"], "protocol schema_version"),
            claude_review_transport=(
                _string(
                    raw["claude_review_transport"],
                    "protocol claude_review_transport",
                )
                if "claude_review_transport" in raw
                else None
            ),
            codex_result_transport=(
                _string(
                    raw["codex_result_transport"],
                    "protocol codex_result_transport",
                )
                if "codex_result_transport" in raw
                else None
            ),
            codex_profile=AgentProfileBinding.from_dict(
                _mapping(raw.get("codex_profile"), "protocol codex_profile"),
                "protocol codex_profile",
            ),
            claude_profile=AgentProfileBinding.from_dict(
                _mapping(raw.get("claude_profile"), "protocol claude_profile"),
                "protocol claude_profile",
            ),
        )


@dataclass(frozen=True)
class InvocationFailureRecord:
    invocation_id: str
    idempotency_key: str
    role: str
    failure_kind: AgentFailureKind
    provider_text: str
    received_at: str
    step: WorkflowStep
    slice_id: int
    work_unit_id: int
    diagnostic_exit_code: int
    process_exit_code: int | None
    technical_text: str
    parse_path: str | None = None
    source_timezone: str | None = None
    reset_at_utc: str | None = None
    resume_at_utc: str | None = None
    safety_margin_seconds: int = 0
    auto_resume_count: int = 0
    automatic_resume: bool = False
    diff_fingerprint: str | None = None
    orchestrator_diagnostic: str | None = None
    native_review_rejection: str | None = None
    native_review_retry_round: int | None = None
    native_implementer_rejection: str | None = None
    native_implementer_retry_round: int | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.invocation_id, "invocation failure invocation_id"),
            (self.idempotency_key, "invocation failure idempotency_key"),
            (self.provider_text, "invocation failure provider_text"),
            (self.technical_text, "invocation failure technical_text"),
        ):
            _require_non_empty(value, label)
        if self.role not in {"codex", "claude"}:
            raise WorkflowStateValidationError(
                "invocation failure role must be codex or claude"
            )
        _require_timestamp(self.received_at, "invocation failure received_at")
        _require_positive_int(self.slice_id, "invocation failure slice_id")
        _require_positive_int(self.work_unit_id, "invocation failure work_unit_id")
        if self.failure_kind is AgentFailureKind.QUOTA:
            if self.diagnostic_exit_code != 2:
                raise WorkflowStateValidationError("quota failure requires exit code 2")
        elif self.diagnostic_exit_code != 3:
            raise WorkflowStateValidationError("instance failure requires exit code 3")
        if self.process_exit_code is not None and (
            isinstance(self.process_exit_code, bool)
            or not isinstance(self.process_exit_code, int)
        ):
            raise WorkflowStateValidationError(
                "invocation failure process_exit_code must be an integer or null"
            )
        if not TECHNICAL_TEXT_MARKER_PATTERN.fullmatch(self.technical_text):
            raise WorkflowStateValidationError(
                "invocation failure technical_text must be redacted evidence"
            )
        for value, label in (
            (self.safety_margin_seconds, "invocation failure safety margin"),
            (self.auto_resume_count, "invocation failure auto-resume count"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise WorkflowStateValidationError(f"{label} must be a non-negative integer")
        if not isinstance(self.automatic_resume, bool):
            raise WorkflowStateValidationError(
                "invocation failure automatic_resume must be a boolean"
            )
        timestamp_fields = (
            (self.reset_at_utc, "invocation failure reset_at_utc"),
            (self.resume_at_utc, "invocation failure resume_at_utc"),
        )
        for value, label in timestamp_fields:
            if value is not None:
                _require_timestamp(value, label)
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
                    raise WorkflowStateValidationError(f"{label} must be normalized to UTC")
        has_reset = self.reset_at_utc is not None
        has_resume = self.resume_at_utc is not None
        if has_reset and not has_resume:
            raise WorkflowStateValidationError(
                "invocation failure reset timestamp requires a resume timestamp"
            )
        if self.automatic_resume and not has_resume:
            raise WorkflowStateValidationError(
                "automatic resume requires a resume timestamp"
            )
        automatic_native_output = is_native_response_output_retry(
            self.failure_kind, self.role, self.step
        )
        if (
            self.automatic_resume
            and self.failure_kind not in {
                AgentFailureKind.QUOTA,
                AgentFailureKind.NETWORK,
                AgentFailureKind.TIMEOUT,
            }
            and not automatic_native_output
        ):
            raise WorkflowStateValidationError(
                "automatic resume is limited to quota, network, and native response form failures"
            )
        if self.failure_kind is AgentFailureKind.QUOTA and self.automatic_resume and not has_reset:
            raise WorkflowStateValidationError(
                "automatic quota resume requires a reset timestamp"
            )
        if self.failure_kind is not AgentFailureKind.QUOTA and any(
            value is not None
            for value in (self.parse_path, self.source_timezone, self.reset_at_utc)
        ):
            raise WorkflowStateValidationError(
                "non-quota failure cannot carry quota reset evidence"
            )
        if self.diff_fingerprint is not None and not SHA256_PATTERN.fullmatch(
            self.diff_fingerprint
        ):
            raise WorkflowStateValidationError(
                "invocation failure diff fingerprint must be a lowercase SHA-256 digest"
            )
        if (
            self.orchestrator_diagnostic is not None
            and self.orchestrator_diagnostic not in ORCHESTRATOR_DIAGNOSTIC_TEXTS
        ):
            raise WorkflowStateValidationError(
                "invocation failure orchestrator diagnostic is not allowlisted"
            )
        if self.native_review_rejection is not None:
            if self.native_review_rejection not in NATIVE_REVIEW_RESPONSE_REJECTION_CODES:
                raise WorkflowStateValidationError(
                    "invocation failure native review rejection is invalid"
                )
            if not is_native_review_output_retry(
                self.failure_kind, self.role, self.step
            ):
                raise WorkflowStateValidationError(
                    "native review rejection requires a reviewer output failure"
                )
        if self.native_implementer_rejection is not None:
            if (
                self.native_implementer_rejection
                not in NATIVE_IMPLEMENTER_RESPONSE_REJECTION_CODES
            ):
                raise WorkflowStateValidationError(
                    "invocation failure native implementer rejection is invalid"
                )
            if not is_native_implementer_output_retry(
                self.failure_kind, self.role, self.step
            ):
                raise WorkflowStateValidationError(
                    "native implementer rejection requires an output failure"
                )
            if self.orchestrator_diagnostic is None or not (
                self.orchestrator_diagnostic.startswith(
                    f"{self.native_implementer_rejection}: "
                )
            ):
                raise WorkflowStateValidationError(
                    "native implementer rejection requires matching closed guidance"
                )
        if (
            self.native_review_rejection is not None
            and self.native_implementer_rejection is not None
        ):
            raise WorkflowStateValidationError(
                "invocation failure cannot contain two native rejection roles"
            )
        if (self.native_review_rejection is None) != (
            self.native_review_retry_round is None
        ):
            raise WorkflowStateValidationError(
                "native review rejection and retry round must be bound together"
            )
        if self.native_review_retry_round is not None:
            _require_positive_int(
                self.native_review_retry_round,
                "invocation failure native review retry round",
            )
        if (self.native_implementer_rejection is None) != (
            self.native_implementer_retry_round is None
        ):
            raise WorkflowStateValidationError(
                "native implementer rejection and retry round must be bound together"
            )
        if self.native_implementer_retry_round is not None:
            _require_positive_int(
                self.native_implementer_retry_round,
                "invocation failure native implementer retry round",
            )

    def to_dict(self) -> dict[str, object]:
        document: dict[str, object] = {
            "invocation_id": self.invocation_id,
            "idempotency_key": self.idempotency_key,
            "role": self.role,
            "failure_kind": self.failure_kind.value,
            "provider_text": self.provider_text,
            "received_at": self.received_at,
            "step": self.step.value,
            "slice_id": self.slice_id,
            "work_unit_id": self.work_unit_id,
            "diagnostic_exit_code": self.diagnostic_exit_code,
            "process_exit_code": self.process_exit_code,
            "technical_text": self.technical_text,
            "parse_path": self.parse_path,
            "source_timezone": self.source_timezone,
            "reset_at_utc": self.reset_at_utc,
            "resume_at_utc": self.resume_at_utc,
            "safety_margin_seconds": self.safety_margin_seconds,
            "auto_resume_count": self.auto_resume_count,
            "automatic_resume": self.automatic_resume,
            "diff_fingerprint": self.diff_fingerprint,
        }
        if self.orchestrator_diagnostic is not None:
            document["orchestrator_diagnostic"] = self.orchestrator_diagnostic
        if self.native_review_rejection is not None:
            document["native_review_rejection"] = self.native_review_rejection
            document["native_review_retry_round"] = self.native_review_retry_round
        if self.native_implementer_rejection is not None:
            document["native_implementer_rejection"] = (
                self.native_implementer_rejection
            )
            document["native_implementer_retry_round"] = (
                self.native_implementer_retry_round
            )
        return document

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> InvocationFailureRecord:
        required_keys = {
                "invocation_id", "idempotency_key", "role", "failure_kind",
                "provider_text", "received_at", "step", "slice_id",
                "work_unit_id", "diagnostic_exit_code", "process_exit_code",
                "technical_text", "parse_path",
                "source_timezone", "reset_at_utc", "resume_at_utc",
                "safety_margin_seconds", "auto_resume_count", "automatic_resume",
                "diff_fingerprint",
        }
        optional_keys = {
            "orchestrator_diagnostic",
            "native_review_rejection",
            "native_review_retry_round",
            "native_implementer_rejection",
            "native_implementer_retry_round",
        }
        if not required_keys.issubset(raw) or not set(raw).issubset(
            required_keys | optional_keys
        ):
            _require_exact_keys(raw, required_keys, "invocation failure")
        if not isinstance(raw["automatic_resume"], bool):
            raise WorkflowStateValidationError(
                "invocation failure automatic_resume must be a boolean"
            )
        return cls(
            invocation_id=_string(raw["invocation_id"], "invocation failure invocation_id"),
            idempotency_key=_string(raw["idempotency_key"], "invocation failure idempotency_key"),
            role=_string(raw["role"], "invocation failure role"),
            failure_kind=_enum_value(AgentFailureKind, raw["failure_kind"], "invocation failure kind"),
            provider_text=_string(raw["provider_text"], "invocation failure provider_text"),
            received_at=_string(raw["received_at"], "invocation failure received_at"),
            step=_enum_value(WorkflowStep, raw["step"], "invocation failure step"),
            slice_id=_positive_int(raw["slice_id"], "invocation failure slice_id"),
            work_unit_id=_positive_int(raw["work_unit_id"], "invocation failure work_unit_id"),
            diagnostic_exit_code=_positive_int(raw["diagnostic_exit_code"], "invocation failure diagnostic_exit_code"),
            process_exit_code=_optional_int(raw["process_exit_code"], "invocation failure process_exit_code"),
            technical_text=_string(raw["technical_text"], "invocation failure technical_text"),
            parse_path=_optional_string(raw["parse_path"], "invocation failure parse_path"),
            source_timezone=_optional_string(raw["source_timezone"], "invocation failure source_timezone"),
            reset_at_utc=_optional_string(raw["reset_at_utc"], "invocation failure reset_at_utc"),
            resume_at_utc=_optional_string(raw["resume_at_utc"], "invocation failure resume_at_utc"),
            safety_margin_seconds=_non_negative_int(raw["safety_margin_seconds"], "invocation failure safety margin"),
            auto_resume_count=_non_negative_int(raw["auto_resume_count"], "invocation failure auto-resume count"),
            automatic_resume=raw["automatic_resume"],
            diff_fingerprint=_optional_string(raw["diff_fingerprint"], "invocation failure diff fingerprint"),
            orchestrator_diagnostic=_optional_string(
                raw.get("orchestrator_diagnostic"),
                "invocation failure orchestrator diagnostic",
            ),
            native_review_rejection=_optional_string(
                raw.get("native_review_rejection"),
                "invocation failure native review rejection",
            ),
            native_review_retry_round=(
                None
                if raw.get("native_review_retry_round") is None
                else _positive_int(
                    raw["native_review_retry_round"],
                    "invocation failure native review retry round",
                )
            ),
            native_implementer_rejection=_optional_string(
                raw.get("native_implementer_rejection"),
                "invocation failure native implementer rejection",
            ),
            native_implementer_retry_round=(
                None
                if raw.get("native_implementer_retry_round") is None
                else _positive_int(
                    raw["native_implementer_retry_round"],
                    "invocation failure native implementer retry round",
                )
            ),
        )


@dataclass(frozen=True)
class GateRecord:
    status: GateStatus = GateStatus.CLEAR
    reason: GateReason = GateReason.NONE
    detail: str | None = None
    fingerprint: str | None = None
    paths: tuple[str, ...] = ()
    resume_step: WorkflowStep | None = None

    def __post_init__(self) -> None:
        if self.status is GateStatus.CLEAR:
            if (
                self.reason is not GateReason.NONE
                or self.detail is not None
                or self.fingerprint is not None
                or self.paths
                or self.resume_step is not None
            ):
                raise WorkflowStateValidationError("a clear gate cannot carry gate evidence")
        elif self.reason is GateReason.NONE:
            raise WorkflowStateValidationError("a non-clear gate requires a reason")
        if self.detail is not None and not self.detail.strip():
            raise WorkflowStateValidationError("gate detail must be non-empty when present")
        if any(not isinstance(path, str) or not path.strip() for path in self.paths):
            raise WorkflowStateValidationError(
                "gate.paths entries must be non-empty strings"
            )
        if len(set(self.paths)) != len(self.paths):
            raise WorkflowStateValidationError("gate.paths entries must be unique")
        if self.paths != tuple(sorted(self.paths)):
            raise WorkflowStateValidationError("gate.paths must be sorted")
        if self.fingerprint is not None and not SHA256_PATTERN.fullmatch(self.fingerprint):
            raise WorkflowStateValidationError(
                "gate fingerprint must be a lowercase SHA-256 digest"
            )
        if (
            self.fingerprint is not None
            and self.reason is GateReason.TEST_CHANGE
            and not self.paths
        ):
            raise WorkflowStateValidationError("test-change gate requires changed test paths")
        if (
            self.fingerprint is not None
            and self.reason in {GateReason.MANUAL_SLICE, GateReason.PLAN_APPROVAL}
            and not self.paths
        ):
            raise WorkflowStateValidationError(
                "manual-slice and plan-approval gates require bound paths"
            )
        if self.reason in {
            GateReason.TEST_CHANGE,
            GateReason.MANUAL_SLICE,
            GateReason.PLAN_APPROVAL,
            GateReason.UNEXPECTED_FILE,
            GateReason.STOP_REQUEST,
            GateReason.QUOTA_RESUME_DIFF,
        }:
            for raw_path in self.paths:
                path = PurePosixPath(raw_path)
                if (
                    path.is_absolute()
                    or "\\" in raw_path
                    or not path.parts
                    or ".." in path.parts
                ):
                    raise WorkflowStateValidationError(
                        "file-bound gate paths must be repository-relative POSIX paths"
                    )
        if (
            self.fingerprint is not None
            and self.reason is GateReason.ANCHOR_CHANGE
            and self.resume_step is None
        ):
            raise WorkflowStateValidationError("anchor-change gate requires a resume step")
        if self.reason is GateReason.QUOTA_RESUME_DIFF:
            if self.fingerprint is None or self.resume_step is None:
                raise WorkflowStateValidationError(
                    "quota-resume-diff gate requires fingerprint and resume step"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "reason": self.reason.value,
            "detail": self.detail,
            "fingerprint": self.fingerprint,
            "paths": list(self.paths),
            "resume_step": self.resume_step.value if self.resume_step is not None else None,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> GateRecord:
        legacy_keys = {"status", "reason", "detail"}
        current_keys = {*legacy_keys, "fingerprint", "paths", "resume_step"}
        if set(raw) == legacy_keys:
            fingerprint = None
            paths: tuple[str, ...] = ()
            resume_step = None
        else:
            _require_exact_keys(raw, current_keys, "gate")
            fingerprint = _optional_string(raw["fingerprint"], "gate.fingerprint")
            paths = _string_tuple(raw["paths"], "gate.paths")
            resume_raw = raw["resume_step"]
            resume_step = (
                None
                if resume_raw is None
                else _enum_value(WorkflowStep, resume_raw, "gate.resume_step")
            )
        return cls(
            status=_enum_value(GateStatus, raw["status"], "gate.status"),
            reason=_enum_value(GateReason, raw["reason"], "gate.reason"),
            detail=_optional_string(raw["detail"], "gate.detail"),
            fingerprint=fingerprint,
            paths=paths,
            resume_step=resume_step,
        )


@dataclass(frozen=True)
class GateDecisionRecord:
    approved: bool
    reason: GateReason
    fingerprint: str
    paths: tuple[str, ...]
    rationale: str
    resume_step: WorkflowStep | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.approved, bool):
            raise WorkflowStateValidationError("gate decision approved must be a boolean")
        if self.reason not in {
            GateReason.TEST_CHANGE,
            GateReason.ANCHOR_CHANGE,
            GateReason.MANUAL_SLICE,
            GateReason.PLAN_APPROVAL,
            GateReason.UNEXPECTED_FILE,
            GateReason.QUOTA_RESUME_DIFF,
        }:
            raise WorkflowStateValidationError(
                "a fingerprint-bound decision requires a user-gate reason"
            )
        if not SHA256_PATTERN.fullmatch(self.fingerprint):
            raise WorkflowStateValidationError(
                "gate decision fingerprint must be a lowercase SHA-256 digest"
            )
        if any(not isinstance(path, str) or not path.strip() for path in self.paths):
            raise WorkflowStateValidationError(
                "gate decision paths entries must be non-empty strings"
            )
        if len(set(self.paths)) != len(self.paths):
            raise WorkflowStateValidationError(
                "gate decision paths entries must be unique"
            )
        if self.paths != tuple(sorted(self.paths)):
            raise WorkflowStateValidationError("gate decision paths must be sorted")
        _require_non_empty(self.rationale, "gate decision rationale")
        if self.reason is GateReason.TEST_CHANGE and not self.paths:
            raise WorkflowStateValidationError(
                "test-change decision requires changed test paths"
            )
        if self.reason in {
            GateReason.MANUAL_SLICE,
            GateReason.PLAN_APPROVAL,
            GateReason.UNEXPECTED_FILE,
        } and not self.paths:
            raise WorkflowStateValidationError(
                "manual-slice, plan-approval, and unexpected-file decisions require "
                "bound paths"
            )
        if self.reason in {
            GateReason.TEST_CHANGE,
            GateReason.MANUAL_SLICE,
            GateReason.PLAN_APPROVAL,
            GateReason.UNEXPECTED_FILE,
            GateReason.STOP_REQUEST,
            GateReason.QUOTA_RESUME_DIFF,
        }:
            for raw_path in self.paths:
                path = PurePosixPath(raw_path)
                if (
                    path.is_absolute()
                    or "\\" in raw_path
                    or not path.parts
                    or ".." in path.parts
                ):
                    raise WorkflowStateValidationError(
                        "file-bound decision paths must be repository-relative POSIX paths"
                    )
        if self.reason is GateReason.ANCHOR_CHANGE and self.resume_step is None:
            raise WorkflowStateValidationError(
                "anchor-change decision requires a resume step"
            )
        if (
            self.reason is GateReason.QUOTA_RESUME_DIFF
            and self.resume_step is None
        ):
            raise WorkflowStateValidationError(
                "quota-resume-diff decision requires a resume step"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "approved": self.approved,
            "reason": self.reason.value,
            "fingerprint": self.fingerprint,
            "paths": list(self.paths),
            "rationale": self.rationale,
            "resume_step": self.resume_step.value if self.resume_step is not None else None,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> GateDecisionRecord:
        _require_exact_keys(
            raw,
            {
                "approved",
                "reason",
                "fingerprint",
                "paths",
                "rationale",
                "resume_step",
            },
            "gate decision",
        )
        if not isinstance(raw["approved"], bool):
            raise WorkflowStateValidationError("gate decision approved must be a boolean")
        resume_raw = raw["resume_step"]
        return cls(
            approved=raw["approved"],
            reason=_enum_value(GateReason, raw["reason"], "gate decision reason"),
            fingerprint=_string(raw["fingerprint"], "gate decision fingerprint"),
            paths=_string_tuple(raw["paths"], "gate decision paths"),
            rationale=_string(raw["rationale"], "gate decision rationale"),
            resume_step=(
                None
                if resume_raw is None
                else _enum_value(WorkflowStep, resume_raw, "gate decision resume_step")
            ),
        )


@dataclass(frozen=True)
class SliceRecord:
    slice_id: int
    status: SliceStatus
    start_commit: str | None = None
    scope_paths: tuple[str, ...] = ()
    scope_change_groups: tuple[tuple[str, ...], ...] = ()
    start_fingerprint: str | None = None
    commit_ref: str | None = None

    def __post_init__(self) -> None:
        _require_positive_int(self.slice_id, "slice_id")
        if self.start_commit is not None:
            _require_non_empty(self.start_commit, "slice start_commit")
        if self.status is not SliceStatus.PENDING and self.start_commit is None:
            raise WorkflowStateValidationError("a started slice requires start_commit")
        _require_canonical_scope(self.scope_paths)
        if self.scope_paths:
            _require_scope_change_groups(
                self.scope_change_groups, self.scope_paths
            )
        elif self.scope_change_groups:
            raise WorkflowStateValidationError(
                "slice scope change groups require scope paths"
            )
        if (self.start_fingerprint is None) != (not self.scope_paths):
            raise WorkflowStateValidationError(
                "slice scope_paths and start_fingerprint must be persisted together"
            )
        if self.start_fingerprint is not None and not SHA256_PATTERN.fullmatch(
            self.start_fingerprint
        ):
            raise WorkflowStateValidationError(
                "slice start_fingerprint must be a lowercase SHA-256 digest"
            )
        if self.commit_ref is not None:
            _require_non_empty(self.commit_ref, "slice commit_ref")
        if self.status is SliceStatus.COMPLETED and self.commit_ref is None:
            raise WorkflowStateValidationError("a completed slice requires commit_ref")

    def to_dict(self) -> dict[str, object]:
        return {
            "slice_id": self.slice_id,
            "status": self.status.value,
            "start_commit": self.start_commit,
            "scope_paths": list(self.scope_paths),
            "scope_change_groups": [list(group) for group in self.scope_change_groups],
            "start_fingerprint": self.start_fingerprint,
            "commit_ref": self.commit_ref,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> SliceRecord:
        legacy_keys = {"slice_id", "status", "start_commit", "commit_ref"}
        boundary_keys = {*legacy_keys, "scope_paths", "start_fingerprint"}
        current_keys = {*boundary_keys, "scope_change_groups"}
        if set(raw) == legacy_keys:
            scope_paths: tuple[str, ...] = ()
            scope_change_groups: tuple[tuple[str, ...], ...] = ()
            start_fingerprint = None
        elif set(raw) == boundary_keys:
            scope_paths = _string_tuple(raw["scope_paths"], "slice.scope_paths")
            scope_change_groups = tuple((path,) for path in scope_paths)
            start_fingerprint = _optional_string(
                raw["start_fingerprint"], "slice.start_fingerprint"
            )
        else:
            _require_exact_keys(raw, current_keys, "slice")
            scope_paths = _string_tuple(raw["scope_paths"], "slice.scope_paths")
            raw_groups = _list(
                raw["scope_change_groups"], "slice.scope_change_groups"
            )
            scope_change_groups = tuple(
                _string_tuple(group, f"slice.scope_change_groups[{index}]")
                for index, group in enumerate(raw_groups)
            )
            start_fingerprint = _optional_string(
                raw["start_fingerprint"], "slice.start_fingerprint"
            )
        return cls(
            slice_id=_positive_int(raw["slice_id"], "slice.slice_id"),
            status=_enum_value(SliceStatus, raw["status"], "slice.status"),
            start_commit=_optional_string(raw["start_commit"], "slice.start_commit"),
            scope_paths=scope_paths,
            scope_change_groups=scope_change_groups,
            start_fingerprint=start_fingerprint,
            commit_ref=_optional_string(raw["commit_ref"], "slice.commit_ref"),
        )


@dataclass(frozen=True)
class WorkUnitRecord:
    work_unit_id: int
    slice_id: int
    kind: WorkUnitKind
    status: WorkUnitStatus
    current_step: WorkflowStep
    round_number: int = 1
    request_sequence: int | None = None
    codex_return_count: int = 0
    max_codex_returns: int = DEFAULT_MAX_CODEX_RETURNS
    gate: GateRecord = GateRecord()
    reviewer: Reviewer | None = None
    open_findings: tuple[str, ...] = ()
    completed_side_effects: tuple[str, ...] = ()
    gate_decisions: tuple[GateDecisionRecord, ...] = ()
    active_test_fingerprint: str | None = None
    active_test_paths: tuple[str, ...] = ()
    invocation_failures: tuple[InvocationFailureRecord, ...] = ()

    def __post_init__(self) -> None:
        _require_positive_int(self.work_unit_id, "work_unit_id")
        _require_positive_int(self.slice_id, "work unit slice_id")
        _require_positive_int(self.round_number, "round_number")
        if self.request_sequence is None:
            object.__setattr__(self, "request_sequence", self.round_number)
        assert self.request_sequence is not None
        _require_positive_int(self.request_sequence, "request_sequence")
        _require_positive_int(self.max_codex_returns, "max_codex_returns")
        if isinstance(self.codex_return_count, bool) or not isinstance(self.codex_return_count, int):
            raise WorkflowStateValidationError("codex_return_count must be an integer")
        if not 0 <= self.codex_return_count <= self.max_codex_returns:
            raise WorkflowStateValidationError("codex_return_count is outside its configured limit")
        # A workflow round is the accepted domain-review round, not a physical
        # provider attempt or a recomposed-request identity. Only
        # ``codex_return_count`` is bounded by ``max_codex_returns``.
        _require_unique_non_empty(self.open_findings, "open_findings")
        _require_unique_non_empty(self.completed_side_effects, "completed_side_effects")
        expected_gate_status = {
            WorkUnitStatus.IN_PROGRESS: GateStatus.CLEAR,
            WorkUnitStatus.PENDING: GateStatus.CLEAR,
            WorkUnitStatus.COMPLETED: GateStatus.CLEAR,
            WorkUnitStatus.AWAITING_USER_DECISION: GateStatus.AWAITING_USER_DECISION,
            WorkUnitStatus.WAITING_FOR_QUOTA: GateStatus.WAITING_FOR_QUOTA,
            WorkUnitStatus.WAITING_FOR_RETRY: GateStatus.WAITING_FOR_RETRY,
            WorkUnitStatus.AWAITING_RESUME: GateStatus.AWAITING_RESUME,
        }[self.status]
        if self.gate.status is not expected_gate_status:
            raise WorkflowStateValidationError(
                "work-unit status and gate status must change together"
            )
        invocation_ids = tuple(item.invocation_id for item in self.invocation_failures)
        if len(set(invocation_ids)) != len(invocation_ids):
            raise WorkflowStateValidationError(
                "invocation failure ids must be unique within a work unit"
            )
        if any(
            item.work_unit_id != self.work_unit_id or item.slice_id != self.slice_id
            for item in self.invocation_failures
        ):
            raise WorkflowStateValidationError(
                "invocation failures must belong to their containing work unit and slice"
            )
        if self.status in {
            WorkUnitStatus.WAITING_FOR_QUOTA,
            WorkUnitStatus.WAITING_FOR_RETRY,
            WorkUnitStatus.AWAITING_RESUME,
        }:
            if not self.invocation_failures and self.gate.reason is not GateReason.BOOTSTRAP_CHECK:
                raise WorkflowStateValidationError(
                    "an invocation halt requires persisted failure evidence"
                )
            latest = self.invocation_failures[-1] if self.invocation_failures else None
            if latest is not None and latest.step is not self.current_step:
                raise WorkflowStateValidationError(
                    "invocation halt must preserve the failed workflow step"
                )
            if self.gate.reason is GateReason.QUOTA:
                if latest.failure_kind is not AgentFailureKind.QUOTA:
                    raise WorkflowStateValidationError(
                        "quota gate requires a quota invocation failure"
                    )
            elif self.gate.reason is GateReason.INSTANCE_FAILURE:
                if latest.failure_kind is AgentFailureKind.QUOTA:
                    raise WorkflowStateValidationError(
                        "instance-failure gate cannot carry a quota failure"
                    )
            elif self.gate.reason is not GateReason.BOOTSTRAP_CHECK:
                raise WorkflowStateValidationError(
                    "invocation halt requires quota or instance_failure reason"
                )
        if self.gate.reason is GateReason.ITERATION_LIMIT:
            if self.codex_return_count != self.max_codex_returns:
                raise WorkflowStateValidationError(
                    "iteration-limit gate requires the configured Codex return limit"
                )
            if self.reviewer is None:
                raise WorkflowStateValidationError("iteration-limit gate requires a reviewer")
        if (self.active_test_fingerprint is None) != (not self.active_test_paths):
            raise WorkflowStateValidationError(
                "active test fingerprint and paths must be persisted together"
            )
        if self.active_test_fingerprint is not None:
            if not SHA256_PATTERN.fullmatch(self.active_test_fingerprint):
                raise WorkflowStateValidationError(
                    "active test fingerprint must be a lowercase SHA-256 digest"
                )
            if self.active_test_paths != tuple(sorted(set(self.active_test_paths))):
                raise WorkflowStateValidationError(
                    "active test paths must be sorted and unique"
                )
            if not self.has_gate_approval(
                GateReason.TEST_CHANGE,
                self.active_test_fingerprint,
                self.active_test_paths,
            ):
                raise WorkflowStateValidationError(
                    "active test evidence requires an exact approved test gate decision"
                )

    def has_completed_side_effect(self, key: str) -> bool:
        _require_non_empty(key, "side-effect key")
        return key in self.completed_side_effects

    def has_gate_approval(
        self,
        reason: GateReason,
        fingerprint: str,
        paths: tuple[str, ...],
    ) -> bool:
        expected_paths = tuple(sorted(set(paths)))
        return any(
            decision.approved
            and decision.reason is reason
            and decision.fingerprint == fingerprint
            and decision.paths == expected_paths
            for decision in self.gate_decisions
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "work_unit_id": self.work_unit_id,
            "slice_id": self.slice_id,
            "kind": self.kind.value,
            "status": self.status.value,
            "current_step": self.current_step.value,
            "round_number": self.round_number,
            "request_sequence": self.request_sequence,
            "codex_return_count": self.codex_return_count,
            "max_codex_returns": self.max_codex_returns,
            "gate": self.gate.to_dict(),
            "reviewer": self.reviewer.value if self.reviewer is not None else None,
            "open_findings": list(self.open_findings),
            "completed_side_effects": list(self.completed_side_effects),
            "gate_decisions": [item.to_dict() for item in self.gate_decisions],
            "active_test_fingerprint": self.active_test_fingerprint,
            "active_test_paths": list(self.active_test_paths),
            "invocation_failures": [item.to_dict() for item in self.invocation_failures],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> WorkUnitRecord:
        legacy_keys = {
                "work_unit_id",
                "slice_id",
                "kind",
                "status",
                "current_step",
                "round_number",
                "codex_return_count",
                "max_codex_returns",
                "gate",
                "reviewer",
                "open_findings",
                "completed_side_effects",
            }
        decision_keys = {*legacy_keys, "gate_decisions"}
        current_keys = {
            *decision_keys,
            "active_test_fingerprint",
            "active_test_paths",
        }
        quota_keys = {*current_keys, "invocation_failures"}
        request_sequence_keys = {*quota_keys, "request_sequence"}
        invocation_failures: tuple[InvocationFailureRecord, ...] = ()
        shape_keys = set(raw) - {"invocation_failures", "request_sequence"}
        if shape_keys == legacy_keys:
            gate_decisions: tuple[GateDecisionRecord, ...] = ()
            active_test_fingerprint = None
            active_test_paths: tuple[str, ...] = ()
        elif shape_keys == decision_keys:
            raw_decisions = raw["gate_decisions"]
            if not isinstance(raw_decisions, list):
                raise WorkflowStateValidationError("work_unit.gate_decisions must be a list")
            gate_decisions = tuple(
                GateDecisionRecord.from_dict(
                    _mapping(item, f"work_unit.gate_decisions[{index}]")
                )
                for index, item in enumerate(raw_decisions)
            )
            active_test_fingerprint = None
            active_test_paths = ()
        elif shape_keys == current_keys:
            raw_decisions = raw["gate_decisions"]
            if not isinstance(raw_decisions, list):
                raise WorkflowStateValidationError("work_unit.gate_decisions must be a list")
            gate_decisions = tuple(
                GateDecisionRecord.from_dict(
                    _mapping(item, f"work_unit.gate_decisions[{index}]")
                )
                for index, item in enumerate(raw_decisions)
            )
            active_test_fingerprint = _optional_string(
                raw["active_test_fingerprint"],
                "work_unit.active_test_fingerprint",
            )
            active_test_paths = _string_tuple(
                raw["active_test_paths"], "work_unit.active_test_paths"
            )
        else:
            _require_exact_keys(
                raw,
                request_sequence_keys if "request_sequence" in raw else quota_keys,
                "work unit",
            )
        if "invocation_failures" in raw:
            raw_failures = _list(
                raw["invocation_failures"], "work_unit.invocation_failures"
            )
            invocation_failures = tuple(
                InvocationFailureRecord.from_dict(
                    _mapping(item, f"work_unit.invocation_failures[{index}]")
                )
                for index, item in enumerate(raw_failures)
            )
        reviewer_raw = raw["reviewer"]
        return cls(
            work_unit_id=_positive_int(raw["work_unit_id"], "work_unit.work_unit_id"),
            slice_id=_positive_int(raw["slice_id"], "work_unit.slice_id"),
            kind=_enum_value(WorkUnitKind, raw["kind"], "work_unit.kind"),
            status=_enum_value(WorkUnitStatus, raw["status"], "work_unit.status"),
            current_step=_enum_value(WorkflowStep, raw["current_step"], "work_unit.current_step"),
            round_number=_positive_int(raw["round_number"], "work_unit.round_number"),
            request_sequence=_positive_int(
                raw.get("request_sequence", raw["round_number"]),
                "work_unit.request_sequence",
            ),
            codex_return_count=_non_negative_int(
                raw["codex_return_count"], "work_unit.codex_return_count"
            ),
            max_codex_returns=_positive_int(
                raw["max_codex_returns"], "work_unit.max_codex_returns"
            ),
            gate=GateRecord.from_dict(_mapping(raw["gate"], "work_unit.gate")),
            reviewer=(
                None
                if reviewer_raw is None
                else _enum_value(Reviewer, reviewer_raw, "work_unit.reviewer")
            ),
            open_findings=_string_tuple(raw["open_findings"], "work_unit.open_findings"),
            completed_side_effects=_string_tuple(
                raw["completed_side_effects"], "work_unit.completed_side_effects"
            ),
            gate_decisions=gate_decisions,
            active_test_fingerprint=active_test_fingerprint,
            active_test_paths=active_test_paths,
            invocation_failures=invocation_failures,
        )


def project_implementer_return_policy(unit: WorkUnitRecord) -> tuple[int, int]:
    """Map the provider-named state-v3 mirror fields to stable workflow roles."""
    return unit.codex_return_count, unit.max_codex_returns  # allowlist:provider -- named R2 mirror projection


@dataclass(frozen=True)
class ResumeCursor:
    work_unit_id: int
    slice_id: int
    step: WorkflowStep
    round_number: int
    request_sequence: int
    completed_side_effects: tuple[str, ...]

    def should_execute(self, side_effect_key: str) -> bool:
        _require_non_empty(side_effect_key, "side-effect key")
        return side_effect_key not in self.completed_side_effects


@dataclass(frozen=True)
class BootstrapCheckFact:
    check_kind: str
    transition_fingerprint: str
    provider: str
    role: str
    operation: str
    work_unit_id: int
    semantic_digest: str
    decision: str
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.check_kind not in {"provider_input_measurement", "final_review_preflight"}:
            raise WorkflowStateValidationError("bootstrap check kind is invalid")
        for value, label in ((self.transition_fingerprint, "bootstrap transition fingerprint"), (self.semantic_digest, "bootstrap semantic digest")):
            if not SHA256_PATTERN.fullmatch(value):
                raise WorkflowStateValidationError(f"{label} must be a lowercase SHA-256 digest")
        if self.provider not in {"codex", "claude"} or self.role != self.provider:
            raise WorkflowStateValidationError("bootstrap provider and role are invalid")
        _require_non_empty(self.operation, "bootstrap operation")
        _require_positive_int(self.work_unit_id, "bootstrap work_unit_id")
        if self.decision not in {"allowed", "denied", "passed"}:
            raise WorkflowStateValidationError("bootstrap decision is invalid")
        if self.decision == "denied" and self.error_code is None:
            raise WorkflowStateValidationError("denied bootstrap fact requires error_code")
        if self.error_code is not None:
            _require_non_empty(self.error_code, "bootstrap error_code")

    def to_dict(self) -> dict[str, object]:
        return {
            "check_kind": self.check_kind, "transition_fingerprint": self.transition_fingerprint,
            "provider": self.provider, "role": self.role, "operation": self.operation,
            "work_unit_id": self.work_unit_id, "semantic_digest": self.semantic_digest,
            "decision": self.decision, "error_code": self.error_code,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "BootstrapCheckFact":
        _require_exact_keys(raw, {"check_kind", "transition_fingerprint", "provider", "role", "operation", "work_unit_id", "semantic_digest", "decision", "error_code"}, "bootstrap check")
        return cls(
            _string(raw["check_kind"], "bootstrap check_kind"),
            _string(raw["transition_fingerprint"], "bootstrap transition_fingerprint"),
            _string(raw["provider"], "bootstrap provider"), _string(raw["role"], "bootstrap role"),
            _string(raw["operation"], "bootstrap operation"), _positive_int(raw["work_unit_id"], "bootstrap work_unit_id"),
            _string(raw["semantic_digest"], "bootstrap semantic_digest"), _string(raw["decision"], "bootstrap decision"),
            _optional_string(raw["error_code"], "bootstrap error_code"),
        )


def _parse_finding_responsibilities(
    raw: Mapping[str, Any],
) -> tuple[tuple[str, FindingResponsibility], ...]:
    responsibility_raw = _mapping(
        raw.get("finding_responsibilities", {}),
        "finding_responsibilities",
    )
    try:
        return tuple(
            (
                finding_id,
                parse_responsibility(
                    _mapping(
                        responsibility_raw[finding_id],
                        f"finding_responsibilities.{finding_id}",
                    )
                ),
            )
            for finding_id in sorted(
                responsibility_raw, key=finding_id_sort_key
            )
        )
    except ValueError as exc:
        raise WorkflowStateValidationError(
            f"finding_responsibilities is invalid: {exc}"
        ) from exc


def _parse_bootstrap_checks(raw: Mapping[str, Any]) -> tuple[BootstrapCheckFact, ...]:
    return tuple(
        BootstrapCheckFact.from_dict(_mapping(item, f"bootstrap_checks[{index}]"))
        for index, item in enumerate(_list(raw.get("bootstrap_checks", []), "bootstrap_checks"))
    )


def _parse_family_binding(
    raw: Mapping[str, Any],
) -> FamilyBindingPayload | None:
    value = raw.get("family_binding")
    if value is None:
        return None
    document = _mapping(value, "family_binding")
    required = {
        "family_id",
        "family_base_commit",
        "family_authorized_change_set",
        "predecessor_run_id",
        "predecessor_head_record_id",
        "cycle_number",
        "current_plan_commit",
        "current_implementation_commit",
    }
    if set(document) != required:
        missing = sorted(required.difference(document))
        foreign = sorted(set(document).difference(required))
        detail = (
            f"missing required field {missing[0]}"
            if missing
            else f"contains foreign field {foreign[0]}"
        )
        raise WorkflowStateValidationError(f"family_binding {detail}")
    try:
        return FamilyBindingPayload(
            family_id=_string(document["family_id"], "family_binding.family_id"),
            family_base_commit=_string(
                document["family_base_commit"],
                "family_binding.family_base_commit",
            ),
            family_authorized_change_set=_string_tuple(
                document["family_authorized_change_set"],
                "family_binding.family_authorized_change_set",
            ),
            predecessor_run_id=_optional_string(
                document["predecessor_run_id"],
                "family_binding.predecessor_run_id",
            ),
            predecessor_head_record_id=_optional_string(
                document["predecessor_head_record_id"],
                "family_binding.predecessor_head_record_id",
            ),
            cycle_number=_positive_int(
                document["cycle_number"], "family_binding.cycle_number"
            ),
            current_plan_commit=_optional_string(
                document["current_plan_commit"],
                "family_binding.current_plan_commit",
            ),
            current_implementation_commit=_optional_string(
                document["current_implementation_commit"],
                "family_binding.current_implementation_commit",
            ),
        )
    except ArtifactValidationError as exc:
        raise WorkflowStateValidationError(str(exc)) from exc


def _validate_family_binding(
    *,
    family_binding: FamilyBindingPayload | None,
    branch_base: str,
    slices: tuple[SliceRecord, ...],
    planned_slices: tuple[PlannedSlice, ...],
    work_plan_path: str | None,
) -> None:
    if family_binding is None:
        return
    if not isinstance(family_binding, FamilyBindingPayload):
        raise WorkflowStateValidationError("family_binding is invalid")
    if branch_base != family_binding.family_base_commit:
        raise WorkflowStateValidationError(
            "branch_base differs from family_base_commit"
        )
    required_family_paths = {
        *(path for item in slices for path in item.scope_paths),
        *(path for item in planned_slices for path in item.scope_paths),
    }
    if work_plan_path is not None:
        required_family_paths.add(work_plan_path)
    missing_family_paths = tuple(
        sorted(
            required_family_paths.difference(
                family_binding.family_authorized_change_set
            )
        )
    )
    if missing_family_paths:
        raise WorkflowStateValidationError(
            "family_authorized_change_set is missing bound path "
            f"{missing_family_paths[0]}"
        )


@dataclass(frozen=True)
class WorkflowState:
    version: int
    run_id: str
    task_file: str
    branch: str
    branch_base: str
    created_at: str
    updated_at: str
    current_slice_id: int
    current_work_unit_id: int
    current_step: WorkflowStep
    slices: tuple[SliceRecord, ...]
    work_units: tuple[WorkUnitRecord, ...]
    planned_slices: tuple[PlannedSlice, ...] = ()
    runtime_history: Mapping[str, Any] | None = None
    task_digest: str | None = None
    execution_mode: str = "IMPLEMENT"
    task_scope_patterns: tuple[str, ...] = ()
    work_plan_path: str | None = None
    approved_plan_commit: str | None = None
    finding_handoff_source_run_id: str | None = None
    finding_handoff_export_record_id: str | None = None
    audit_report_path: str | None = None
    target_branch: str | None = None
    protocol_binding: ProtocolBinding | None = None
    bootstrap_checks: tuple[BootstrapCheckFact, ...] = ()
    finding_responsibilities: tuple[tuple[str, FindingResponsibility], ...] = ()
    family_binding: FamilyBindingPayload | None = None

    def __post_init__(self) -> None:
        if self.version != STATE_VERSION:
            raise WorkflowStateValidationError(
                f"WorkflowState requires version {STATE_VERSION}, got {self.version!r}"
            )
        for value, label in (
            (self.run_id, "run_id"),
            (self.task_file, "task_file"),
            (self.branch, "branch"),
            (self.branch_base, "branch_base"),
            (self.created_at, "created_at"),
            (self.updated_at, "updated_at"),
        ):
            _require_non_empty(value, label)
        _require_positive_int(self.current_slice_id, "current_slice_id")
        _require_positive_int(self.current_work_unit_id, "current_work_unit_id")
        if not self.slices or not self.work_units:
            raise WorkflowStateValidationError("workflow state requires slices and work units")
        slice_ids = tuple(item.slice_id for item in self.slices)
        work_unit_ids = tuple(item.work_unit_id for item in self.work_units)
        _require_contiguous_ids(slice_ids, "slice")
        _require_contiguous_ids(work_unit_ids, "work-unit")
        if self.current_slice_id not in slice_ids:
            raise WorkflowStateValidationError("current_slice_id does not reference a slice")
        if self.current_work_unit_id not in work_unit_ids:
            raise WorkflowStateValidationError(
                "current_work_unit_id does not reference a work unit"
            )
        for unit in self.work_units:
            if unit.slice_id not in slice_ids:
                raise WorkflowStateValidationError(
                    f"work unit {unit.work_unit_id} references unknown slice {unit.slice_id}"
                )
        current = self.current_work_unit
        if current.slice_id != self.current_slice_id:
            raise WorkflowStateValidationError(
                "current work unit does not belong to current slice"
            )
        if current.current_step is not self.current_step:
            raise WorkflowStateValidationError(
                "top-level current_step must match the current work unit"
            )
        if self.planned_slices:
            plan_ids = tuple(item.slice_id for item in self.planned_slices)
            if plan_ids != tuple(range(1, len(plan_ids) + 1)):
                raise WorkflowStateValidationError(
                    "planned slice ids must be contiguous and 1-based"
                )
            if len(self.planned_slices) != len(self.slices):
                raise WorkflowStateValidationError(
                    "planned slice count must equal persisted slices"
                )
        if self.runtime_history is not None and not isinstance(
            self.runtime_history, Mapping
        ):
            raise WorkflowStateValidationError("runtime_history must be an object")
        if isinstance(self.runtime_history, Mapping):
            candidates: list[object] = []
            if set(self.runtime_history) == {"current", "archive"}:
                archive = self.runtime_history.get("archive")
                if isinstance(archive, list):
                    candidates.extend(archive)
                candidates.append(self.runtime_history.get("current"))
            else:
                candidates.append(self.runtime_history)
            if any(
                isinstance(candidate, Mapping) and "events" in candidate
                for candidate in candidates
            ):
                raise WorkflowStateValidationError(
                    "runtime_history.events is retired; project workflow events from records"
                )
        if self.execution_mode not in {"IMPLEMENT", "PLAN_ONLY", "BRANCH_DISCOVERY"}:
            raise WorkflowStateValidationError(
                "execution_mode must be IMPLEMENT, PLAN_ONLY, or BRANCH_DISCOVERY"
            )
        if self.task_digest is not None:
            if not SHA256_PATTERN.fullmatch(self.task_digest):
                raise WorkflowStateValidationError(
                    "task_digest must be a lowercase SHA-256 digest"
                )
            if not self.task_scope_patterns:
                raise WorkflowStateValidationError(
                    "a bound task contract requires task_scope_patterns"
                )
            if self.target_branch != self.branch:
                raise WorkflowStateValidationError(
                    "target_branch must match the persisted workflow branch"
                )
            if self.execution_mode == "PLAN_ONLY" and self.work_plan_path is None:
                raise WorkflowStateValidationError(
                    "PLAN_ONLY state requires work_plan_path"
                )
        if self.approved_plan_commit is not None:
            if not re.fullmatch(r"[0-9a-f]{40}", self.approved_plan_commit):
                raise WorkflowStateValidationError(
                    "approved_plan_commit must be a lowercase 40-character Git SHA"
                )
            if self.work_plan_path is None:
                raise WorkflowStateValidationError(
                    "approved_plan_commit requires work_plan_path"
                )
        _validate_family_binding(
            family_binding=self.family_binding,
            branch_base=self.branch_base,
            slices=self.slices,
            planned_slices=self.planned_slices,
            work_plan_path=self.work_plan_path,
        )
        handoff_values = (
            self.finding_handoff_source_run_id,
            self.finding_handoff_export_record_id,
        )
        if any(value is None for value in handoff_values) != all(
            value is None for value in handoff_values
        ):
            raise WorkflowStateValidationError(
                "finding handoff requires source run and export record"
            )
        if self.finding_handoff_source_run_id is not None:
            if (
                self.approved_plan_commit is None
                and self.execution_mode not in {"PLAN_ONLY", "BRANCH_DISCOVERY"}
            ):
                raise WorkflowStateValidationError(
                    "finding handoff requires PLAN_ONLY, BRANCH_DISCOVERY, or approved_plan_commit"
                )
            if re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}",
                self.finding_handoff_source_run_id,
            ) is None:
                raise WorkflowStateValidationError("finding handoff source run is invalid")
            if re.fullmatch(
                r"ar1-[0-9a-f]{64}", self.finding_handoff_export_record_id or ""
            ) is None:
                raise WorkflowStateValidationError("finding handoff export record is invalid")
        if self.audit_report_path is not None:
            path = PurePosixPath(self.audit_report_path)
            if (
                path.is_absolute()
                or ".." in path.parts
                or path.parts[:2] != ("docs", "internal")
                or len(path.parts) != 3
                or path.suffix.lower() != ".md"
                or path.as_posix() != self.audit_report_path
            ):
                raise WorkflowStateValidationError(
                    "audit_report_path must be a canonical Markdown path directly below docs/internal"
                )
        keys = tuple((item.check_kind, item.transition_fingerprint) for item in self.bootstrap_checks)
        if len(keys) != len(set(keys)):
            raise WorkflowStateValidationError("bootstrap checks must be idempotently unique")
        responsibility_ids = tuple(
            finding_id for finding_id, _responsibility in self.finding_responsibilities
        )
        if any(
            re.fullmatch(r"C-(0[1-9]|[1-9][0-9]*)", finding_id) is None
            for finding_id in responsibility_ids
        ):
            raise WorkflowStateValidationError(
                "finding_responsibilities contains an invalid finding ID"
            )
        if responsibility_ids != tuple(
            sorted(set(responsibility_ids), key=finding_id_sort_key)
        ):
            raise WorkflowStateValidationError(
                "finding_responsibilities must be sorted and unique by finding ID"
            )
        try:
            for _finding_id, responsibility in self.finding_responsibilities:
                responsibility_document(responsibility)
        except ValueError as exc:
            raise WorkflowStateValidationError(
                f"finding_responsibilities is invalid: {exc}"
            ) from exc

    @property
    def current_work_unit(self) -> WorkUnitRecord:
        return next(
            unit for unit in self.work_units if unit.work_unit_id == self.current_work_unit_id
        )

    @property
    def current_slice(self) -> SliceRecord:
        return next(item for item in self.slices if item.slice_id == self.current_slice_id)

    @property
    def effective_protocol_mode(self) -> ProtocolMode:
        """Return the resume mode; missing bindings identify historical v3 state."""
        if self.protocol_binding is None:
            return ProtocolMode.LEGACY_STATE_V3
        return self.protocol_binding.mode

    @property
    def protocol_mode(self) -> ProtocolMode:
        """Compatibility-friendly name for the effective persisted mode."""
        return self.effective_protocol_mode

    def resume_cursor(self) -> ResumeCursor:
        current = self.current_work_unit
        return ResumeCursor(
            work_unit_id=current.work_unit_id,
            slice_id=current.slice_id,
            step=current.current_step,
            round_number=current.round_number,
            request_sequence=current.request_sequence,
            completed_side_effects=current.completed_side_effects,
        )

    def with_current_step(
        self,
        step: WorkflowStep,
        *,
        updated_at: str | None = None,
    ) -> WorkflowState:
        current = self.current_work_unit
        if current.status is not WorkUnitStatus.IN_PROGRESS:
            raise WorkflowStateValidationError("only an in-progress work unit can advance")
        return self._replace_current_unit(
            replace(current, current_step=step),
            updated_at=updated_at,
        )

    def bind_slice_plan(
        self,
        planned_slices: tuple[PlannedSlice, ...],
        *,
        first_start_commit: str,
        updated_at: str | None = None,
    ) -> WorkflowState:
        """Persist Codex's ordered allowlists before any implementation step."""
        if self.current_work_unit.kind is not WorkUnitKind.PLAN:
            raise WorkflowStateValidationError("slice plan can only be bound during planning")
        if not planned_slices:
            raise WorkflowStateValidationError("slice plan must not be empty")
        _require_non_empty(first_start_commit, "first_start_commit")
        expected_ids = tuple(range(1, len(planned_slices) + 1))
        if tuple(item.slice_id for item in planned_slices) != expected_ids:
            raise WorkflowStateValidationError(
                "planned slice ids must be contiguous and 1-based"
            )
        if self.planned_slices and self.planned_slices != planned_slices:
            if any(item.status is SliceStatus.COMPLETED for item in self.slices):
                raise WorkflowStateValidationError(
                    "cannot revise the slice plan after implementation commits"
                )
        slices = tuple(
            SliceRecord(
                slice_id=item.slice_id,
                status=(SliceStatus.IN_PROGRESS if item.slice_id == 1 else SliceStatus.PENDING),
                start_commit=(first_start_commit if item.slice_id == 1 else None),
            )
            for item in planned_slices
        )
        return replace(
            self,
            slices=slices,
            planned_slices=planned_slices,
            updated_at=updated_at or _now_iso(),
        )

    def complete_current_work_unit(self, *, updated_at: str | None = None) -> WorkflowState:
        current = self.current_work_unit
        if current.status is not WorkUnitStatus.IN_PROGRESS:
            raise WorkflowStateValidationError("only an in-progress work unit can complete")
        return self._replace_current_unit(
            replace(
                current,
                status=WorkUnitStatus.COMPLETED,
                current_step=WorkflowStep.COMPLETED,
            ),
            updated_at=updated_at,
        )

    def complete_provider_input_boundary_verdict(
        self, *, updated_at: str | None = None
    ) -> WorkflowState:
        """Finish with a visible denial when a provider request cannot fit safely."""

        current = self.current_work_unit
        if current.status is not WorkUnitStatus.IN_PROGRESS:
            raise WorkflowStateValidationError(
                "only an in-progress work unit can record a provider-input verdict"
            )
        matching_fact = next(
            (
                fact
                for fact in reversed(self.bootstrap_checks)
                if fact.check_kind == "provider_input_measurement"
                and fact.work_unit_id == current.work_unit_id
                and fact.operation == current.current_step.value
                and fact.decision == "denied"
                and fact.error_code == "PROVIDER-INPUT-BUDGET"
            ),
            None,
        )
        if matching_fact is None:
            raise WorkflowStateValidationError(
                "provider-input verdict requires its denied measurement fact"
            )
        return self._replace_current_unit(
            replace(current, status=WorkUnitStatus.COMPLETED),
            slices=self._slices_with_current_status(SliceStatus.IN_PROGRESS),
            updated_at=updated_at,
        )

    def start_work_unit(
        self,
        *,
        slice_id: int,
        kind: WorkUnitKind,
        step: WorkflowStep,
        slice_start_commit: str | None = None,
        updated_at: str | None = None,
    ) -> WorkflowState:
        _require_positive_int(slice_id, "slice_id")
        if self.current_work_unit.status is not WorkUnitStatus.COMPLETED:
            raise WorkflowStateValidationError(
                "the current work unit must be completed before starting another"
            )
        try:
            target_slice = next(item for item in self.slices if item.slice_id == slice_id)
        except StopIteration as exc:
            raise WorkflowStateValidationError(f"unknown slice_id {slice_id}") from exc
        if target_slice.status is SliceStatus.COMPLETED:
            raise WorkflowStateValidationError("cannot start a work unit for a completed slice")
        if target_slice.status is SliceStatus.PENDING:
            if slice_start_commit is None:
                raise WorkflowStateValidationError("a new slice requires slice_start_commit")
            _require_non_empty(slice_start_commit, "slice_start_commit")
            target_slice = replace(
                target_slice,
                status=SliceStatus.IN_PROGRESS,
                start_commit=slice_start_commit,
            )
        elif slice_start_commit is not None and slice_start_commit != target_slice.start_commit:
            raise WorkflowStateValidationError("cannot change a persisted slice start commit")
        new_unit = WorkUnitRecord(
            work_unit_id=len(self.work_units) + 1,
            slice_id=slice_id,
            kind=kind,
            status=WorkUnitStatus.IN_PROGRESS,
            current_step=step,
        )
        slices = tuple(
            target_slice if item.slice_id == slice_id else item for item in self.slices
        )
        return replace(
            self,
            current_slice_id=slice_id,
            current_work_unit_id=new_unit.work_unit_id,
            current_step=step,
            slices=slices,
            work_units=(*self.work_units, new_unit),
            updated_at=updated_at or _now_iso(),
        )

    def complete_current_slice(
        self,
        *,
        commit_ref: str,
        updated_at: str | None = None,
    ) -> WorkflowState:
        _require_non_empty(commit_ref, "commit_ref")
        current = self.current_work_unit
        if current.status is not WorkUnitStatus.IN_PROGRESS:
            raise WorkflowStateValidationError("only an in-progress work unit can complete a slice")
        if not self.current_slice.scope_paths:
            raise WorkflowStateValidationError(
                "cannot complete a slice without a persisted Git boundary"
            )
        completed_unit = replace(
            current,
            status=WorkUnitStatus.COMPLETED,
            current_step=WorkflowStep.COMPLETED,
        )
        slices = tuple(
            replace(item, status=SliceStatus.COMPLETED, commit_ref=commit_ref)
            if item.slice_id == self.current_slice_id
            else item
            for item in self.slices
        )
        return self._replace_current_unit(
            completed_unit,
            slices=slices,
            updated_at=updated_at,
        )

    def bind_completed_plan_commit(
        self,
        *,
        commit_ref: str,
        updated_at: str | None = None,
    ) -> WorkflowState:
        """Bind the reviewed PLAN_ONLY commit for export and exact resume."""
        _require_non_empty(commit_ref, "commit_ref")
        if self.execution_mode != "PLAN_ONLY":
            raise WorkflowStateValidationError(
                "only a PLAN_ONLY workflow can bind its completed plan commit"
            )
        if self.current_work_unit.kind is not WorkUnitKind.PLAN:
            raise WorkflowStateValidationError(
                "completed plan commit binding requires the plan work unit"
            )
        if self.current_work_unit.status is not WorkUnitStatus.COMPLETED:
            raise WorkflowStateValidationError(
                "completed plan commit binding requires a completed plan work unit"
            )
        if self.current_slice.commit_ref != commit_ref:
            raise WorkflowStateValidationError(
                "completed plan commit binding must match the persisted Slice commit"
            )
        if self.work_plan_path is None or not self.planned_slices:
            raise WorkflowStateValidationError(
                "completed plan commit binding requires the persisted plan contract"
            )
        if self.approved_plan_commit is not None:
            if self.approved_plan_commit != commit_ref:
                raise WorkflowStateValidationError(
                    "cannot replace the approved plan commit binding"
                )
            return self
        return replace(
            self,
            approved_plan_commit=commit_ref,
            updated_at=updated_at or _now_iso(),
        )

    def bind_current_slice_git_boundary(
        self,
        *,
        start_commit: str,
        scope_paths: tuple[str, ...],
        scope_change_groups: tuple[tuple[str, ...], ...] | None = None,
        start_fingerprint: str,
        updated_at: str | None = None,
    ) -> WorkflowState:
        """Persist the immutable Git boundary before the first slice edit."""
        _require_non_empty(start_commit, "start_commit")
        normalized_scope = _normalize_scope_paths(scope_paths)
        normalized_groups = (
            tuple((path,) for path in normalized_scope)
            if scope_change_groups is None
            else _normalize_scope_change_groups(scope_change_groups, normalized_scope)
        )
        if not SHA256_PATTERN.fullmatch(start_fingerprint):
            raise WorkflowStateValidationError(
                "start_fingerprint must be a lowercase SHA-256 digest"
            )
        current_slice = self.current_slice
        if current_slice.status is SliceStatus.COMPLETED:
            raise WorkflowStateValidationError("cannot bind a completed slice")
        if current_slice.start_commit != start_commit:
            raise WorkflowStateValidationError(
                "Git boundary start_commit must match the persisted slice start commit"
            )
        if current_slice.scope_paths:
            if (
                current_slice.scope_paths == normalized_scope
                and current_slice.scope_change_groups == normalized_groups
                and current_slice.start_fingerprint == start_fingerprint
            ):
                return self
            raise WorkflowStateValidationError("cannot change a persisted slice Git boundary")
        bound = replace(
            current_slice,
            scope_paths=normalized_scope,
            scope_change_groups=normalized_groups,
            start_fingerprint=start_fingerprint,
        )
        slices = tuple(
            bound if item.slice_id == current_slice.slice_id else item for item in self.slices
        )
        return replace(
            self,
            slices=slices,
            updated_at=updated_at or _now_iso(),
        )

    def extend_current_slice_scope(
        self,
        additions: tuple[str, ...],
        *,
        updated_at: str | None = None,
    ) -> WorkflowState:
        """Extend an in-progress Slice with a minimal approved remediation allowlist."""
        current = self.current_work_unit
        current_slice = self.current_slice
        if (
            current.kind is not WorkUnitKind.SLICE
            or current.status is not WorkUnitStatus.IN_PROGRESS
            or current_slice.status is not SliceStatus.IN_PROGRESS
        ):
            raise WorkflowStateValidationError(
                "only an in-progress regular Slice can extend its remediation scope"
            )
        if not current_slice.scope_paths or current_slice.start_fingerprint is None:
            raise WorkflowStateValidationError(
                "remediation scope extension requires a persisted Git boundary"
            )
        normalized_additions = _normalize_scope_paths(additions)
        if set(normalized_additions).intersection(current_slice.scope_paths):
            raise WorkflowStateValidationError(
                "remediation additions must not repeat current Slice paths"
            )
        expanded_scope = tuple(
            sorted({*current_slice.scope_paths, *normalized_additions})
        )
        expanded_groups = tuple(
            sorted(
                {
                    *current_slice.scope_change_groups,
                    *((path,) for path in normalized_additions),
                }
            )
        )
        expanded_slice = replace(
            current_slice,
            scope_paths=expanded_scope,
            scope_change_groups=expanded_groups,
        )
        slices = tuple(
            expanded_slice if item.slice_id == current_slice.slice_id else item
            for item in self.slices
        )
        return replace(
            self,
            slices=slices,
            updated_at=updated_at or _now_iso(),
        )

    def mark_side_effect_completed(self, key: str, *, updated_at: str | None = None) -> WorkflowState:
        _require_non_empty(key, "side-effect key")
        current = self.current_work_unit
        if current.has_completed_side_effect(key):
            return self
        updated_unit = replace(
            current,
            completed_side_effects=(*current.completed_side_effects, key),
        )
        return self._replace_current_unit(updated_unit, updated_at=updated_at)

    def record_active_test_approval(
        self,
        fingerprint: str | None,
        paths: tuple[str, ...] = (),
        *,
        updated_at: str | None = None,
    ) -> WorkflowState:
        """Bind audit projection to the exact test approval used by the active review."""
        normalized_paths = tuple(sorted(set(paths)))
        if fingerprint is None:
            normalized_paths = ()
        current = self.current_work_unit
        if (
            current.active_test_fingerprint == fingerprint
            and current.active_test_paths == normalized_paths
        ):
            return self
        updated_unit = replace(
            current,
            active_test_fingerprint=fingerprint,
            active_test_paths=normalized_paths,
        )
        return self._replace_current_unit(updated_unit, updated_at=updated_at)

    def inherit_prior_test_approval(
        self,
        fingerprint: str,
        paths: tuple[str, ...],
        *,
        updated_at: str | None = None,
    ) -> WorkflowState:
        """Reuse an exact earlier test decision without weakening changed-test gates."""
        normalized_paths = tuple(sorted(set(paths)))
        prior_decision = next(
            (
                decision
                for unit in reversed(self.work_units)
                if unit.work_unit_id != self.current_work_unit_id
                for decision in reversed(unit.gate_decisions)
                if decision.approved
                and decision.reason is GateReason.TEST_CHANGE
                and decision.fingerprint == fingerprint
                and decision.paths == normalized_paths
            ),
            None,
        )
        if prior_decision is None:
            return self
        current = self.current_work_unit
        if current.status is WorkUnitStatus.AWAITING_USER_DECISION:
            gate = current.gate
            if (
                gate.reason is not GateReason.TEST_CHANGE
                or gate.fingerprint != fingerprint
                or gate.paths != normalized_paths
            ):
                return self
        elif current.status is not WorkUnitStatus.IN_PROGRESS:
            return self
        decisions = current.gate_decisions
        if prior_decision not in decisions:
            decisions = (*decisions, prior_decision)
        updated_unit = replace(
            current,
            status=WorkUnitStatus.IN_PROGRESS,
            gate=GateRecord(),
            gate_decisions=decisions,
            active_test_fingerprint=fingerprint,
            active_test_paths=normalized_paths,
        )
        slices = self._slices_with_current_status(SliceStatus.IN_PROGRESS)
        return self._replace_current_unit(
            updated_unit,
            slices=slices,
            updated_at=updated_at,
        )

    def await_user_gate(
        self,
        *,
        reason: GateReason,
        detail: str,
        fingerprint: str,
        paths: tuple[str, ...] = (),
        gate_step: WorkflowStep | None = None,
        resume_step: WorkflowStep | None = None,
        updated_at: str | None = None,
    ) -> WorkflowState:
        current = self.current_work_unit
        normalized_paths = tuple(sorted(set(paths)))
        gate = GateRecord(
            status=GateStatus.AWAITING_USER_DECISION,
            reason=reason,
            detail=detail,
            fingerprint=fingerprint,
            paths=normalized_paths,
            resume_step=resume_step,
        )
        next_step = gate_step or current.current_step
        if current.status is WorkUnitStatus.AWAITING_USER_DECISION:
            if current.gate == gate and current.current_step is next_step:
                return self
            raise WorkflowStateValidationError(
                "cannot replace an unresolved user gate with different evidence"
            )
        if current.status is not WorkUnitStatus.IN_PROGRESS:
            raise WorkflowStateValidationError(
                "only an in-progress work unit can enter a user gate"
            )
        updated_unit = replace(
            current,
            status=WorkUnitStatus.AWAITING_USER_DECISION,
            current_step=next_step,
            gate=gate,
        )
        slices = self._slices_with_current_status(SliceStatus.AWAITING_USER_DECISION)
        return self._replace_current_unit(
            updated_unit, slices=slices, updated_at=updated_at
        )

    def await_policy_gate(
        self,
        *,
        reason: GateReason,
        detail: str,
        paths: tuple[str, ...] = (),
        updated_at: str | None = None,
    ) -> WorkflowState:
        """Persist a machine- or agent-triggered halt that must be resolved, not approved."""
        if reason not in {GateReason.STOP_REQUEST, GateReason.UNEXPECTED_FILE}:
            raise WorkflowStateValidationError(
                "policy gate reason must be stop_request or unexpected_file"
            )
        current = self.current_work_unit
        normalized_paths = tuple(sorted(set(paths)))
        gate = GateRecord(
            status=GateStatus.AWAITING_USER_DECISION,
            reason=reason,
            detail=detail,
            paths=normalized_paths,
        )
        if current.status is WorkUnitStatus.AWAITING_USER_DECISION:
            if current.gate == gate:
                return self
            raise WorkflowStateValidationError(
                "cannot replace an unresolved policy gate with different evidence"
            )
        if current.status is not WorkUnitStatus.IN_PROGRESS:
            raise WorkflowStateValidationError(
                "only an in-progress work unit can enter a policy gate"
            )
        updated_unit = replace(
            current,
            status=WorkUnitStatus.AWAITING_USER_DECISION,
            gate=gate,
        )
        slices = self._slices_with_current_status(SliceStatus.AWAITING_USER_DECISION)
        return self._replace_current_unit(
            updated_unit, slices=slices, updated_at=updated_at
        )

    def record_user_gate_decision(
        self,
        *,
        approved: bool,
        fingerprint: str,
        paths: tuple[str, ...],
        rationale: str,
        updated_at: str | None = None,
    ) -> WorkflowState:
        current = self.current_work_unit
        if current.status is not WorkUnitStatus.AWAITING_USER_DECISION:
            raise WorkflowStateValidationError("workflow is not awaiting a user decision")
        gate = current.gate
        if gate.fingerprint is None:
            raise WorkflowStateValidationError(
                "this gate is not fingerprint-bound; use the legacy resume transition"
            )
        normalized_paths = tuple(sorted(set(paths)))
        if fingerprint != gate.fingerprint or normalized_paths != gate.paths:
            raise WorkflowStateValidationError(
                "user decision does not match the persisted gate fingerprint and paths"
            )
        existing_approval = next(
            (
                decision
                for decision in reversed(current.gate_decisions)
                if approved
                and decision.approved
                and decision.reason is gate.reason
                and decision.fingerprint == fingerprint
                and decision.paths == normalized_paths
                and decision.resume_step is gate.resume_step
            ),
            None,
        )
        decision = existing_approval or GateDecisionRecord(
            approved=approved,
            reason=gate.reason,
            fingerprint=fingerprint,
            paths=normalized_paths,
            rationale=rationale,
            resume_step=gate.resume_step,
        )
        completed_side_effects = current.completed_side_effects
        if approved and gate.reason is GateReason.QUOTA_RESUME_DIFF:
            if not current.invocation_failures:
                raise WorkflowStateValidationError(
                    "quota-resume-diff approval requires invocation evidence"
                )
            acknowledgement = quota_resume_diff_acknowledgement(
                current.invocation_failures[-1].invocation_id,
                fingerprint,
            )
            if acknowledgement not in completed_side_effects:
                completed_side_effects = (*completed_side_effects, acknowledgement)
        updated_unit = replace(
            current,
            gate_decisions=(
                current.gate_decisions
                if existing_approval is not None
                else (*current.gate_decisions, decision)
            ),
            status=(
                WorkUnitStatus.IN_PROGRESS
                if approved
                else WorkUnitStatus.AWAITING_USER_DECISION
            ),
            gate=GateRecord() if approved else gate,
            completed_side_effects=completed_side_effects,
        )
        slices = self.slices
        if approved:
            slices = self._slices_with_current_status(SliceStatus.IN_PROGRESS)
        return self._replace_current_unit(
            updated_unit,
            slices=slices,
            updated_at=updated_at,
        )

    def reopen_legacy_quota_resume_diff_gate(
        self, *, updated_at: str | None = None
    ) -> WorkflowState:
        """Return a pre-fingerprint QUOTA-RESUME-DIFF gate to safe revalidation."""
        current = self.current_work_unit
        gate = current.gate
        if (
            current.status is not WorkUnitStatus.AWAITING_USER_DECISION
            or gate.reason is not GateReason.STOP_REQUEST
            or gate.fingerprint is not None
            or gate.detail is None
        ):
            return self
        match = QUOTA_RESUME_DIFF_PATTERN.fullmatch(gate.detail)
        if match is None or not current.invocation_failures:
            return self
        failure = current.invocation_failures[-1]
        acknowledgement_prefix = f"quota-resume-diff:{failure.invocation_id}:"
        completed_side_effects = tuple(
            item
            for item in current.completed_side_effects
            if not item.startswith(acknowledgement_prefix)
        )
        reason = (
            GateReason.QUOTA
            if failure.failure_kind is AgentFailureKind.QUOTA
            else GateReason.INSTANCE_FAILURE
        )
        updated_unit = replace(
            current,
            status=WorkUnitStatus.AWAITING_RESUME,
            gate=GateRecord(
                status=GateStatus.AWAITING_RESUME,
                reason=reason,
                detail=(
                    "legacy QUOTA-RESUME-DIFF requires fingerprint-bound "
                    "repository revalidation"
                ),
                fingerprint=failure.diff_fingerprint,
                resume_step=failure.step,
            ),
            completed_side_effects=completed_side_effects,
        )
        return self._replace_current_unit(
            updated_unit,
            slices=self._slices_with_current_status(SliceStatus.AWAITING_RESUME),
            updated_at=updated_at,
        )

    def record_review_denial(
        self,
        *,
        reviewer: Reviewer,
        open_findings: tuple[str, ...],
        return_step: WorkflowStep,
        progress_made: bool,
        updated_at: str | None = None,
    ) -> WorkflowState:
        _require_unique_non_empty(open_findings, "open_findings")
        if not open_findings:
            raise WorkflowStateValidationError("a review denial requires open findings")
        if not isinstance(progress_made, bool):
            raise WorkflowStateValidationError("review progress must be boolean")
        current = self.current_work_unit
        next_count = current.codex_return_count + 1  # allowlist:provider -- persisted counter
        if next_count > IMPLEMENTER_RETURN_SAFETY_LIMIT:
            raise WorkflowStateValidationError(
                "review denial exceeds the implementer return safety limit"
            )
        safety_limit_reached = next_count == IMPLEMENTER_RETURN_SAFETY_LIMIT
        continue_rounds = progress_made and not safety_limit_reached
        next_max = current.max_codex_returns
        if continue_rounds and next_count >= next_max:
            next_max = min(
                IMPLEMENTER_RETURN_SAFETY_LIMIT,
                max(next_count + 1, next_max + DEFAULT_MAX_CODEX_RETURNS),
            )
        elif next_count > next_max:
            # A legacy iteration gate can be cleared automatically before this
            # method observes its next denial. Preserve the exact return count by
            # extending the old policy block rather than saturating the counter.
            next_max = min(
                IMPLEMENTER_RETURN_SAFETY_LIMIT,
                max(next_count, next_max + DEFAULT_MAX_CODEX_RETURNS),
            )
        updated_unit = replace(
            current,
            status=(WorkUnitStatus.IN_PROGRESS if continue_rounds else WorkUnitStatus.COMPLETED),
            current_step=(return_step if continue_rounds else WorkflowStep.COMPLETED),
            round_number=(current.round_number + 1 if continue_rounds else current.round_number),
            request_sequence=(
                current.request_sequence + 1
                if continue_rounds
                else current.request_sequence
            ),
            codex_return_count=next_count,
            max_codex_returns=next_max,
            gate=GateRecord(),
            reviewer=reviewer,
            open_findings=open_findings,
        )
        return self._replace_current_unit(
            updated_unit,
            slices=self._slices_with_current_status(SliceStatus.IN_PROGRESS),
            updated_at=updated_at,
        )

    def continue_retired_iteration_limit(
        self, *, progress_made: bool, updated_at: str | None = None
    ) -> WorkflowState:
        """Resolve a persisted pre-B112 counter gate without a user decision."""

        current = self.current_work_unit
        if (
            current.status is not WorkUnitStatus.AWAITING_USER_DECISION
            or current.gate.reason is not GateReason.ITERATION_LIMIT
        ):
            return self
        if not isinstance(progress_made, bool):
            raise WorkflowStateValidationError("review progress must be boolean")
        if (
            not progress_made
            or current.max_codex_returns >= IMPLEMENTER_RETURN_SAFETY_LIMIT
        ):
            return self._replace_current_unit(
                replace(
                    current,
                    status=WorkUnitStatus.COMPLETED,
                    current_step=WorkflowStep.COMPLETED,
                    gate=GateRecord(),
                ),
                slices=self._slices_with_current_status(SliceStatus.IN_PROGRESS),
                updated_at=updated_at,
            )
        return self._replace_current_unit(
            replace(
                current,
                status=WorkUnitStatus.IN_PROGRESS,
                max_codex_returns=min(
                    IMPLEMENTER_RETURN_SAFETY_LIMIT,
                    current.max_codex_returns + DEFAULT_MAX_CODEX_RETURNS,
                ),
                gate=GateRecord(),
            ),
            slices=self._slices_with_current_status(SliceStatus.IN_PROGRESS),
            updated_at=updated_at,
        )

    def record_invocation_failure(
        self,
        failure: InvocationFailureRecord,
        *,
        wait_automatically: bool,
        updated_at: str | None = None,
    ) -> WorkflowState:
        """Persist one failed role invocation without advancing its logical step."""
        current = self.current_work_unit
        if current.status is not WorkUnitStatus.IN_PROGRESS:
            raise WorkflowStateValidationError(
                "only an in-progress work unit can record an invocation failure"
            )
        if (
            failure.work_unit_id != current.work_unit_id
            or failure.slice_id != current.slice_id
            or failure.step is not current.current_step
        ):
            raise WorkflowStateValidationError(
                "invocation failure does not match the current work-unit identity"
            )
        if any(
            item.invocation_id == failure.invocation_id
            for item in current.invocation_failures
        ):
            raise WorkflowStateValidationError(
                "invocation failure id was already persisted"
            )
        if wait_automatically != failure.automatic_resume:
            raise WorkflowStateValidationError(
                "invocation wait mode differs from persisted failure evidence"
            )
        automatic_native_output = is_native_response_output_retry(
            failure.failure_kind, failure.role, failure.step
        )
        if (
            wait_automatically
            and failure.failure_kind not in {
                AgentFailureKind.QUOTA,
                AgentFailureKind.NETWORK,
                AgentFailureKind.TIMEOUT,
            }
            and not automatic_native_output
        ):
            raise WorkflowStateValidationError(
                "only quota, network, timeout, and native response form failures may wait automatically"
            )
        automatic_quota = wait_automatically and failure.failure_kind is AgentFailureKind.QUOTA
        automatic_transient = wait_automatically and failure.failure_kind in {
            AgentFailureKind.NETWORK,
            AgentFailureKind.TIMEOUT,
            AgentFailureKind.OUTPUT,
        }
        status = (
            WorkUnitStatus.WAITING_FOR_QUOTA if automatic_quota else
            WorkUnitStatus.WAITING_FOR_RETRY if automatic_transient else
            WorkUnitStatus.AWAITING_RESUME
        )

        slice_status = (
            SliceStatus.WAITING_FOR_QUOTA if automatic_quota else
            SliceStatus.WAITING_FOR_RETRY if automatic_transient else
            SliceStatus.AWAITING_RESUME
        )
        gate_status = (
            GateStatus.WAITING_FOR_QUOTA if automatic_quota else
            GateStatus.WAITING_FOR_RETRY if automatic_transient else
            GateStatus.AWAITING_RESUME
        )
        reason = (
            GateReason.QUOTA
            if failure.failure_kind is AgentFailureKind.QUOTA
            else GateReason.INSTANCE_FAILURE
        )
        reset = failure.resume_at_utc or "manual resume required"
        detail = (
            f"role={failure.role} step={failure.step.value} "
            f"invocation={failure.invocation_id} kind={failure.failure_kind.value} "
            f"resume={reset} auto={str(failure.automatic_resume).lower()} "
            f"continuations={failure.auto_resume_count} provider={failure.provider_text}"
        )
        updated_unit = replace(
            current,
            status=status,
            gate=GateRecord(
                status=gate_status,
                reason=reason,
                detail=detail,
                fingerprint=failure.diff_fingerprint,
                resume_step=failure.step,
            ),
            invocation_failures=(*current.invocation_failures, failure),
        )
        slices = self._slices_with_current_status(slice_status)
        return self._replace_current_unit(
            updated_unit, slices=slices, updated_at=updated_at
        )

    def with_bootstrap_check(self, fact: BootstrapCheckFact, *, updated_at: str | None = None) -> "WorkflowState":
        existing = next((item for item in self.bootstrap_checks if (item.check_kind, item.transition_fingerprint) == (fact.check_kind, fact.transition_fingerprint)), None)
        if existing is not None:
            if existing != fact:
                raise WorkflowStateValidationError("bootstrap idempotency conflict")
            return self
        return replace(self, bootstrap_checks=(*self.bootstrap_checks, fact), updated_at=updated_at or self.updated_at)

    def await_bootstrap_resume(
        self,
        *,
        detail: str,
        fingerprint: str,
        paths: tuple[str, ...] = (),
        updated_at: str | None = None,
    ) -> "WorkflowState":
        current = self.current_work_unit
        normalized_paths = tuple(sorted(set(paths)))
        if current.status is WorkUnitStatus.AWAITING_RESUME:
            if (
                current.gate.reason is GateReason.BOOTSTRAP_CHECK
                and current.gate.fingerprint == fingerprint
                and current.gate.paths == normalized_paths
            ):
                return self
            raise WorkflowStateValidationError("cannot replace an unresolved bootstrap gate")
        if current.status is not WorkUnitStatus.IN_PROGRESS:
            raise WorkflowStateValidationError("only an in-progress work unit can enter bootstrap resume")
        updated = replace(
            current,
            status=WorkUnitStatus.AWAITING_RESUME,
            gate=GateRecord(
                status=GateStatus.AWAITING_RESUME,
                reason=GateReason.BOOTSTRAP_CHECK,
                detail=detail,
                fingerprint=fingerprint,
                paths=normalized_paths,
                resume_step=current.current_step,
            ),
        )
        return self._replace_current_unit(updated, slices=self._slices_with_current_status(SliceStatus.AWAITING_RESUME), updated_at=updated_at)

    def complete_quota_automation_verdict(
        self, *, updated_at: str | None = None
    ) -> WorkflowState:
        """Turn a safely classified quota stop into a terminal operator-visible result."""

        current = self.current_work_unit
        if (
            current.status is not WorkUnitStatus.AWAITING_RESUME
            or current.gate.reason is not GateReason.QUOTA
            or not current.invocation_failures
        ):
            raise WorkflowStateValidationError(
                "quota verdict requires a non-automatic quota failure"
            )
        failure = current.invocation_failures[-1]
        if (
            failure.failure_kind is not AgentFailureKind.QUOTA
            or failure.automatic_resume
            or failure.step is not current.current_step
            or (
                current.kind is not WorkUnitKind.PLAN
                and failure.diff_fingerprint is None
            )
        ):
            raise WorkflowStateValidationError(
                "quota verdict requires safely bound terminal failure evidence"
            )
        return self._replace_current_unit(
            replace(
                current,
                status=WorkUnitStatus.COMPLETED,
                gate=GateRecord(),
            ),
            slices=self._slices_with_current_status(SliceStatus.IN_PROGRESS),
            updated_at=updated_at,
        )

    def resume_after_invocation_halt(
        self, *, updated_at: str | None = None
    ) -> WorkflowState:
        """Resume exactly the failed role step after an automatic or manual halt."""
        current = self.current_work_unit
        if current.status not in {
            WorkUnitStatus.WAITING_FOR_QUOTA,
            WorkUnitStatus.WAITING_FOR_RETRY,
            WorkUnitStatus.AWAITING_RESUME,
        }:
            raise WorkflowStateValidationError(
                "workflow is not halted on an agent invocation"
            )
        if current.gate.resume_step is not current.current_step:
            raise WorkflowStateValidationError(
                "invocation halt does not preserve the current resume step"
            )
        updated_unit = replace(
            current,
            status=WorkUnitStatus.IN_PROGRESS,
            gate=GateRecord(),
        )
        slices = self._slices_with_current_status(SliceStatus.IN_PROGRESS)
        return self._replace_current_unit(
            updated_unit, slices=slices, updated_at=updated_at
        )

    def start_recomposed_request(
        self, *, updated_at: str | None = None
    ) -> "WorkflowState":
        """Advance only request identity after local request recomposition."""

        current = self.current_work_unit
        if current.status is not WorkUnitStatus.IN_PROGRESS:
            raise WorkflowStateValidationError(
                "only an in-progress work unit can recompose its provider request"
            )
        updated_unit = replace(
            current, request_sequence=current.request_sequence + 1
        )
        return self._replace_current_unit(
            updated_unit,
            slices=self._slices_with_current_status(SliceStatus.IN_PROGRESS),
            updated_at=updated_at,
        )

    def resume_after_user_decision(self, *, updated_at: str | None = None) -> WorkflowState:
        current = self.current_work_unit
        if current.status is not WorkUnitStatus.AWAITING_USER_DECISION:
            raise WorkflowStateValidationError("workflow is not awaiting a user decision")
        if current.gate.fingerprint is not None:
            raise WorkflowStateValidationError(
                "fingerprint-bound gate requires an explicit recorded user decision"
            )
        if current.gate.reason is GateReason.ITERATION_LIMIT:
            raise WorkflowStateValidationError(
                "retired iteration-limit gates require record-derived automatic resolution"
            )
        continuing_stop_request = current.gate.reason is GateReason.STOP_REQUEST
        completed_side_effects = current.completed_side_effects
        if (
            current.gate.reason is GateReason.STOP_REQUEST
            and current.gate.detail is not None
            and current.invocation_failures
        ):
            match = QUOTA_RESUME_DIFF_PATTERN.fullmatch(current.gate.detail)
            if match is not None:
                acknowledgement = quota_resume_diff_acknowledgement(
                    current.invocation_failures[-1].invocation_id,
                    match.group(1),
                )
                if acknowledgement not in completed_side_effects:
                    completed_side_effects = (
                        *completed_side_effects,
                        acknowledgement,
                    )
        updated_unit = replace(
            current,
            status=WorkUnitStatus.IN_PROGRESS,
            round_number=(
                current.round_number + 1
                if continuing_stop_request
                else current.round_number
            ),
            request_sequence=(
                current.request_sequence + 1
                if continuing_stop_request
                else current.request_sequence
            ),
            completed_side_effects=completed_side_effects,
            gate=GateRecord(),
        )
        slices = self._slices_with_current_status(SliceStatus.IN_PROGRESS)
        return self._replace_current_unit(updated_unit, slices=slices, updated_at=updated_at)

    def _slices_with_current_status(
        self, status: SliceStatus
    ) -> tuple[SliceRecord, ...]:
        if self.current_work_unit.kind is WorkUnitKind.BRANCH_DISCOVERY:
            return self.slices
        return tuple(
            replace(item, status=status)
            if item.slice_id == self.current_slice_id
            else item
            for item in self.slices
        )

    def _replace_current_unit(
        self,
        updated_unit: WorkUnitRecord,
        *,
        slices: tuple[SliceRecord, ...] | None = None,
        updated_at: str | None = None,
    ) -> WorkflowState:
        units = tuple(
            updated_unit if item.work_unit_id == self.current_work_unit_id else item
            for item in self.work_units
        )
        return replace(
            self,
            current_step=updated_unit.current_step,
            work_units=units,
            slices=self.slices if slices is None else slices,
            updated_at=updated_at or _now_iso(),
        )

    def to_dict(self) -> dict[str, object]:
        document: dict[str, object] = {
            "version": self.version,
            "run_id": self.run_id,
            "task_file": self.task_file,
            "branch": self.branch,
            "branch_base": self.branch_base,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "current_slice_id": self.current_slice_id,
            "current_work_unit_id": self.current_work_unit_id,
            "current_step": self.current_step.value,
            "slices": [item.to_dict() for item in self.slices],
            "work_units": [item.to_dict() for item in self.work_units],
            "planned_slices": [
                {
                    "slice_id": item.slice_id,
                    "summary": item.summary,
                    "scope_paths": list(item.scope_paths),
                    **(
                        {
                            "acceptance_criteria": [
                                {
                                    "criterion_id": criterion.criterion_id,
                                    "text": criterion.text,
                                    "measured_against": (
                                        criterion.measured_against.value
                                    ),
                                }
                                for criterion in item.acceptance_criteria
                            ]
                        }
                        if item.acceptance_criteria
                        else {}
                    ),
                }
                for item in self.planned_slices
            ],
            "runtime_history": self.runtime_history,
            "task_digest": self.task_digest,
            "execution_mode": self.execution_mode,
            "task_scope_patterns": list(self.task_scope_patterns),
            "work_plan_path": self.work_plan_path,
            "approved_plan_commit": self.approved_plan_commit,
            "finding_handoff_source_run_id": self.finding_handoff_source_run_id,
            "finding_handoff_export_record_id": self.finding_handoff_export_record_id,
            "audit_report_path": self.audit_report_path,
            "target_branch": self.target_branch,
            "protocol_binding": (
                None if self.protocol_binding is None else self.protocol_binding.to_dict()
            ),
            "bootstrap_checks": [item.to_dict() for item in self.bootstrap_checks],
        }
        if self.finding_responsibilities:
            document["finding_responsibilities"] = {
                finding_id: responsibility_document(responsibility)
                for finding_id, responsibility in self.finding_responsibilities
            }
        if self.family_binding is not None:
            document["family_binding"] = family_binding_document(
                self.family_binding
            )
        return document

    @property
    def active_family_binding(self) -> FamilyBindingPayload | None:
        """Expose the installed shared 67/68 family semantics."""
        return self.family_binding

    @property
    def branch_review_base_commit(self) -> str:
        binding = self.active_family_binding
        return self.branch_base if binding is None else binding.family_base_commit

    @property
    def branch_review_authorized_change_set(self) -> tuple[str, ...]:
        binding = self.active_family_binding
        if binding is not None:
            return binding.family_authorized_change_set
        return tuple(
            sorted(
                {
                    path
                    for item in self.slices
                    if item.status is SliceStatus.COMPLETED
                    for path in item.scope_paths
                }
            )
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> WorkflowState:
        legacy_keys = {
                "version",
                "run_id",
                "task_file",
                "branch",
                "branch_base",
                "created_at",
                "updated_at",
                "current_slice_id",
                "current_work_unit_id",
                "current_step",
                "slices",
                "work_units",
            }
        previous_keys = {*legacy_keys, "planned_slices", "runtime_history"}
        current_keys = {
            *previous_keys,
            "task_digest",
            "execution_mode",
            "task_scope_patterns",
            "work_plan_path",
            "target_branch",
        }
        audit_keys = {*current_keys, "audit_report_path"}
        protocol_keys = {*audit_keys, "protocol_binding"}
        plan_commit_keys = {*audit_keys, "approved_plan_commit"}
        plan_binding_keys = {*plan_commit_keys, "protocol_binding"}
        bootstrap_keys = {*plan_binding_keys, "bootstrap_checks"}
        handoff_shape_keys = {
            *plan_binding_keys,
            "finding_handoff_source_run_id",
            "finding_handoff_export_record_id",
        }
        handoff_plan_commit_shape_keys = {
            *plan_commit_keys,
            "finding_handoff_source_run_id",
            "finding_handoff_export_record_id",
        }
        handoff_protocol_shape_keys = {
            *protocol_keys,
            "finding_handoff_source_run_id",
            "finding_handoff_export_record_id",
        }
        handoff_keys = {*handoff_shape_keys, "bootstrap_checks"}
        # The dormant responsibility field is optional for every historical shape.
        finding_responsibilities = _parse_finding_responsibilities(raw)
        if set(raw) == legacy_keys:
            planned_slices: tuple[PlannedSlice, ...] = ()
            runtime_history = None
            task_digest = None
            execution_mode = "IMPLEMENT"
            task_scope_patterns: tuple[str, ...] = ()
            work_plan_path = None
            approved_plan_commit = None
            finding_handoff_source_run_id = None
            finding_handoff_export_record_id = None
            audit_report_path = None
            target_branch = None
            protocol_binding = None
        else:
            # ``bootstrap_checks`` is an additive optional mirror field.  Remove it
            # for historical shape selection while validating its contents below.
            raw_keys = frozenset(raw) - {
                "bootstrap_checks",
                "finding_responsibilities",
                "family_binding",
            }
            if raw_keys not in {
                frozenset(previous_keys),
                frozenset(current_keys),
                frozenset(audit_keys),
            }:
                if raw_keys not in {
                    frozenset(protocol_keys),
                    frozenset(plan_commit_keys),
                    frozenset(plan_binding_keys),
                    frozenset(bootstrap_keys),
                    frozenset(handoff_shape_keys),
                    frozenset(handoff_plan_commit_shape_keys),
                    frozenset(handoff_protocol_shape_keys),
                }:
                    _require_exact_keys(raw, handoff_keys, "workflow state")
            planned_slices = _parse_planned_slices(raw["planned_slices"])
            history_raw = raw["runtime_history"]
            runtime_history = (
                None if history_raw is None else _mapping(history_raw, "runtime_history")
            )
            if raw_keys == frozenset(previous_keys):
                task_digest = None
                execution_mode = "IMPLEMENT"
                task_scope_patterns = ()
                work_plan_path = None
                approved_plan_commit = None
                finding_handoff_source_run_id = None
                finding_handoff_export_record_id = None
                audit_report_path = None
                target_branch = None
            else:
                task_digest = _optional_string(raw["task_digest"], "task_digest")
                execution_mode = _string(raw["execution_mode"], "execution_mode")
                task_scope_patterns = _string_tuple(
                    raw["task_scope_patterns"], "task_scope_patterns"
                )
                work_plan_path = _optional_string(
                    raw["work_plan_path"], "work_plan_path"
                )
                audit_report_path = (
                    _optional_string(raw["audit_report_path"], "audit_report_path")
                    if raw_keys in {
                        frozenset(audit_keys),
                        frozenset(protocol_keys),
                        frozenset(plan_commit_keys),
                        frozenset(plan_binding_keys),
                        frozenset(bootstrap_keys),
                        frozenset(handoff_shape_keys),
                        frozenset(handoff_plan_commit_shape_keys),
                        frozenset(handoff_protocol_shape_keys),
                    }
                    else None
                )
                target_branch = _optional_string(raw["target_branch"], "target_branch")
                approved_plan_commit = _optional_string(
                    raw.get("approved_plan_commit"), "approved_plan_commit"
                )
                finding_handoff_source_run_id = _optional_string(
                    raw.get("finding_handoff_source_run_id"),
                    "finding_handoff_source_run_id",
                )
                finding_handoff_export_record_id = _optional_string(
                    raw.get("finding_handoff_export_record_id"),
                    "finding_handoff_export_record_id",
                )
            binding_raw = raw.get("protocol_binding")
            protocol_binding = (
                None
                if binding_raw is None
                else ProtocolBinding.from_dict(
                    _mapping(binding_raw, "protocol_binding")
                )
            )
            bootstrap_checks = _parse_bootstrap_checks(raw)
        family_binding = _parse_family_binding(raw)
        if set(raw) == legacy_keys:
            bootstrap_checks = ()
        slices_raw = _list(raw["slices"], "slices")
        units_raw = _list(raw["work_units"], "work_units")
        return cls(
            version=_positive_int(raw["version"], "version"),
            run_id=_string(raw["run_id"], "run_id"),
            task_file=_string(raw["task_file"], "task_file"),
            branch=_string(raw["branch"], "branch"),
            branch_base=_string(raw["branch_base"], "branch_base"),
            created_at=_string(raw["created_at"], "created_at"),
            updated_at=_string(raw["updated_at"], "updated_at"),
            current_slice_id=_positive_int(raw["current_slice_id"], "current_slice_id"),
            current_work_unit_id=_positive_int(
                raw["current_work_unit_id"], "current_work_unit_id"
            ),
            current_step=_enum_value(WorkflowStep, raw["current_step"], "current_step"),
            slices=tuple(SliceRecord.from_dict(_mapping(item, "slice")) for item in slices_raw),
            work_units=tuple(
                WorkUnitRecord.from_dict(_mapping(item, "work unit")) for item in units_raw
            ),
            planned_slices=planned_slices,
            runtime_history=runtime_history,
            task_digest=task_digest,
            execution_mode=execution_mode,
            task_scope_patterns=task_scope_patterns,
            work_plan_path=work_plan_path,
            approved_plan_commit=approved_plan_commit,
            finding_handoff_source_run_id=finding_handoff_source_run_id,
            finding_handoff_export_record_id=finding_handoff_export_record_id,
            audit_report_path=audit_report_path,
            target_branch=target_branch,
            protocol_binding=protocol_binding,
            bootstrap_checks=bootstrap_checks,
            finding_responsibilities=finding_responsibilities,
            family_binding=family_binding,
        )


def _parse_planned_slices(raw: object) -> tuple[PlannedSlice, ...]:
    planned: list[PlannedSlice] = []
    for index, item in enumerate(_list(raw, "planned_slices")):
        label = f"planned_slices[{index}]"
        plan_item = _mapping(item, label)
        base_keys = {"slice_id", "summary", "scope_paths"}
        if set(plan_item) not in {
            frozenset(base_keys),
            frozenset((*base_keys, "acceptance_criteria")),
        }:
            raise WorkflowStateValidationError(
                f"{label} has unknown or missing fields"
            )
        try:
            slice_id = _positive_int(plan_item["slice_id"], f"{label}.slice_id")
            planned.append(
                PlannedSlice(
                    slice_id=slice_id,
                    summary=_string(plan_item["summary"], f"{label}.summary"),
                    scope_paths=_string_tuple(
                        plan_item["scope_paths"], f"{label}.scope_paths"
                    ),
                    acceptance_criteria=acceptance_criteria_from_documents(
                        slice_id,
                        _list(
                            plan_item.get("acceptance_criteria", []),
                            f"{label}.acceptance_criteria",
                        ),
                    ),
                )
            )
        except ValueError as exc:
            raise WorkflowStateValidationError(str(exc)) from exc
    return tuple(planned)


def init_workflow_state(
    *,
    run_id: str,
    task_file: str,
    branch: str,
    branch_base: str,
    slice_count: int,
    first_slice_start_commit: str,
    task_digest: str | None = None,
    execution_mode: str = "IMPLEMENT",
    task_scope_patterns: tuple[str, ...] = (),
    work_plan_path: str | None = None,
    approved_plan_commit: str | None = None,
    finding_handoff_source_run_id: str | None = None,
    finding_handoff_export_record_id: str | None = None,
    audit_report_path: str | None = None,
    target_branch: str | None = None,
    protocol_binding: ProtocolBinding | None = None,
    family_binding: FamilyBindingPayload | None = None,
    timestamp: str | None = None,
) -> WorkflowState:
    _require_positive_int(slice_count, "slice_count")
    _require_non_empty(first_slice_start_commit, "first_slice_start_commit")
    stamp = timestamp or _now_iso()
    branch_discovery = execution_mode == "BRANCH_DISCOVERY"
    slices = tuple(
        SliceRecord(
            slice_id=slice_id,
            status=(
                SliceStatus.COMPLETED
                if branch_discovery
                else SliceStatus.IN_PROGRESS
                if slice_id == 1
                else SliceStatus.PENDING
            ),
            start_commit=(
                first_slice_start_commit
                if slice_id == 1 or branch_discovery
                else None
            ),
            commit_ref=(first_slice_start_commit if branch_discovery else None),
        )
        for slice_id in range(1, slice_count + 1)
    )
    work_unit = WorkUnitRecord(
        work_unit_id=1,
        slice_id=1,
        kind=(
            WorkUnitKind.BRANCH_DISCOVERY if branch_discovery else WorkUnitKind.PLAN
        ),
        status=WorkUnitStatus.IN_PROGRESS,
        current_step=(
            WorkflowStep.CLAUDE_BRANCH_DISCOVERY  # allowlist:provider -- canonical state-v3 step
            if branch_discovery
            else WorkflowStep.CODEX_PLAN
        ),
    )
    return WorkflowState(
        version=STATE_VERSION,
        run_id=run_id,
        task_file=task_file,
        branch=branch,
        branch_base=branch_base,
        created_at=stamp,
        updated_at=stamp,
        current_slice_id=1,
        current_work_unit_id=1,
        current_step=work_unit.current_step,
        slices=slices,
        work_units=(work_unit,),
        task_digest=task_digest,
        execution_mode=execution_mode,
        task_scope_patterns=task_scope_patterns,
        work_plan_path=work_plan_path,
        approved_plan_commit=approved_plan_commit,
        finding_handoff_source_run_id=finding_handoff_source_run_id,
        finding_handoff_export_record_id=finding_handoff_export_record_id,
        audit_report_path=audit_report_path,
        target_branch=target_branch,
        protocol_binding=protocol_binding,
        family_binding=family_binding,
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_non_empty(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowStateValidationError(f"{label} must be a non-empty string")


def _require_timestamp(value: str, label: str) -> None:
    _require_non_empty(value, label)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WorkflowStateValidationError(
            f"{label} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WorkflowStateValidationError(
            f"{label} must include an explicit timezone offset"
        )


def _require_positive_int(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise WorkflowStateValidationError(f"{label} must be a 1-based integer")


def _require_unique_non_empty(values: tuple[str, ...], label: str) -> None:
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise WorkflowStateValidationError(f"{label} entries must be non-empty strings")
    if len(set(values)) != len(values):
        raise WorkflowStateValidationError(f"{label} entries must be unique")


def _normalize_scope_paths(values: tuple[str, ...]) -> tuple[str, ...]:
    if not values:
        raise WorkflowStateValidationError("slice scope_paths must not be empty")
    normalized = tuple(sorted(set(values)))
    _require_canonical_scope(normalized)
    return normalized


def _normalize_scope_change_groups(
    groups: tuple[tuple[str, ...], ...],
    scope_paths: tuple[str, ...],
) -> tuple[tuple[str, ...], ...]:
    normalized = tuple(tuple(sorted(set(group))) for group in groups)
    _require_scope_change_groups(normalized, scope_paths)
    return normalized


def _require_scope_change_groups(
    groups: tuple[tuple[str, ...], ...],
    scope_paths: tuple[str, ...],
) -> None:
    if not groups or any(not group for group in groups):
        raise WorkflowStateValidationError(
            "slice scope change groups must be non-empty"
        )
    if groups != tuple(sorted(groups)) or len(set(groups)) != len(groups):
        raise WorkflowStateValidationError(
            "slice scope change groups must be sorted and unique"
        )
    flattened = tuple(path for group in groups for path in group)
    if len(set(flattened)) != len(flattened) or tuple(sorted(flattened)) != scope_paths:
        raise WorkflowStateValidationError(
            "slice scope change groups must partition the exact scope paths"
        )


def _require_canonical_scope(values: tuple[str, ...]) -> None:
    if values != tuple(sorted(set(values))):
        raise WorkflowStateValidationError(
            "slice scope_paths must be sorted, unique, relative POSIX paths"
        )
    for value in values:
        if not isinstance(value, str) or not value or "\\" in value:
            raise WorkflowStateValidationError(
                "slice scope_paths must be sorted, unique, relative POSIX paths"
            )
        path = PurePosixPath(value)
        if path.is_absolute() or not path.parts or any(
            part in ("", ".", "..") for part in path.parts
        ) or path.parts[0] == ".orchestrator":
            raise WorkflowStateValidationError(
                "slice scope_paths must be sorted, unique, relative POSIX paths outside .orchestrator"
            )


def _require_contiguous_ids(values: tuple[int, ...], label: str) -> None:
    expected = tuple(range(1, len(values) + 1))
    if values != expected:
        raise WorkflowStateValidationError(
            f"{label} ids must be ordered, unique, contiguous, and 1-based"
        )


def _require_exact_keys(raw: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(raw)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise WorkflowStateValidationError(
            f"invalid {label} fields: missing={missing}, unexpected={extra}"
        )


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkflowStateValidationError(f"{label} must be an object")
    return value


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise WorkflowStateValidationError(f"{label} must be a list")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise WorkflowStateValidationError(f"{label} must be a string")
    return value


def _optional_string(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _string(value, label)


def _optional_int(value: object, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkflowStateValidationError(f"{label} must be an integer or null")
    return value


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise WorkflowStateValidationError(f"{label} must be a 1-based integer")
    return value


def _non_negative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WorkflowStateValidationError(f"{label} must be a non-negative integer")
    return value


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    return tuple(_string(item, label) for item in _list(value, label))


def _enum_value(enum_type: type[Enum], value: object, label: str):
    if not isinstance(value, str):
        raise WorkflowStateValidationError(f"{label} must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise WorkflowStateValidationError(f"unknown {label}: {value!r}") from exc
