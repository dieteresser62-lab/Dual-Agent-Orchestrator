from __future__ import annotations

import argparse
import copy
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, TextIO

from error_classification import (
    ClassifiedFailure,
    ERROR_CLASSIFICATIONS,
    FailureClass,
    classify_exception,
    enforce_record_start_boundary,
)
from artifact_bridge import ArtifactBridge, ArtifactBridgeError
from artifact_models import RecordType, provider_text_evidence
from artifact_store import ArtifactStore
from artifact_replay import replay_artifacts
from orchestrator_diagnostics import OrchestratorDiagnostic
from side_effects import (
    ReconciliationOutcome,
    SideEffectExecutor,
    SideEffectReconciliationError,
    SideEffectSpec,
    reconcile_queue_move,
)
from state_io import atomic_write_file
from workflow import WorkflowRunResult
from workflow_state import GateReason, WorkUnitStatus

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix fallback
    fcntl = None

logger = logging.getLogger(__name__)
STUCK_RETRY_MULTIPLIER = 3
WATCH_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


class WatchTaskDisposition(str, Enum):
    COMPLETED = "completed"
    RESUMABLE_HALT = "resumable_halt"
    REJECTED = "rejected"
    TECHNICAL_FAILURE = "technical_failure"


class QueueFinalizationDisposition(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class QueueFinalizationResult:
    disposition: QueueFinalizationDisposition
    destination: Path | None = None
    detail: str | None = None


@dataclass(frozen=True)
class QueueSuccessEvidence:
    run_id: str
    task_digest: str
    protocol_mode: str
    source: str
    destination: str
    evidence_digest: str
    version: int = 1

    def __post_init__(self) -> None:
        if self.version != 1:
            raise ValueError("queue success evidence version is invalid")
        if not self.run_id.strip():
            raise ValueError("queue success evidence requires a run id")
        if len(self.task_digest) != 64 or any(
            character not in "0123456789abcdef" for character in self.task_digest
        ):
            raise ValueError("queue success evidence requires a SHA-256 task digest")
        if self.protocol_mode != "structured-v2":
            raise ValueError("queue success evidence protocol mode is invalid")
        if not self.source or not self.destination:
            raise ValueError("queue success evidence requires source and destination")
        if self.evidence_digest != self.calculate_digest(
            run_id=self.run_id,
            task_digest=self.task_digest,
            protocol_mode=self.protocol_mode,
            source=self.source,
            destination=self.destination,
        ):
            raise ValueError("queue success evidence binding digest differs")

    @staticmethod
    def calculate_digest(
        *, run_id: str, task_digest: str, protocol_mode: str, source: str, destination: str
    ) -> str:
        payload = json.dumps(
            {
                "destination": destination,
                "protocol_mode": protocol_mode,
                "run_id": run_id,
                "source": source,
                "task_digest": task_digest,
                "version": 1,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "run_id": self.run_id,
            "task_digest": self.task_digest,
            "protocol_mode": self.protocol_mode,
            "source": self.source,
            "destination": self.destination,
            "evidence_digest": self.evidence_digest,
        }

    @classmethod
    def from_dict(cls, raw: object) -> QueueSuccessEvidence:
        expected = {
            "version", "run_id", "task_digest", "protocol_mode", "source", "destination",
            "evidence_digest",
        }
        if not isinstance(raw, dict) or set(raw) != expected:
            raise ValueError("queue success evidence has an invalid schema")
        if raw.get("version") != 1 or any(
            not isinstance(raw.get(field), str) for field in expected - {"version"}
        ):
            raise ValueError("queue success evidence has invalid field types")
        return cls(
            run_id=raw["run_id"],
            task_digest=raw["task_digest"],
            protocol_mode=raw["protocol_mode"],
            source=raw["source"],
            destination=raw["destination"],
            evidence_digest=raw["evidence_digest"],
        )


@dataclass(frozen=True)
class WatchTaskResult:
    exit_code: int
    run_id: str
    disposition: WatchTaskDisposition
    status: str
    step: str
    work_unit_id: int
    gate_reason: str
    failure_detail: str | None = None
    resume_available: bool = True
    protocol_mode: str | None = None
    classified_failure: ClassifiedFailure | None = None

    def __post_init__(self) -> None:
        if self.exit_code < 0:
            raise ValueError("watch task exit code must be non-negative")
        if WATCH_RUN_ID_PATTERN.fullmatch(self.run_id) is None:
            raise ValueError("watch task result requires a safe run id")
        if self.work_unit_id < 1:
            raise ValueError("watch task result requires a 1-based work unit id")
        if not isinstance(self.resume_available, bool):
            raise ValueError("watch task resume availability must be boolean")
        if self.protocol_mode not in {None, "structured-v2"}:
            raise ValueError("watch task protocol mode is invalid")
        if self.disposition is WatchTaskDisposition.COMPLETED and self.exit_code != 0:
            raise ValueError("completed watch task result requires exit code zero")
        if (
            self.disposition is WatchTaskDisposition.COMPLETED
            and self.classified_failure is not None
        ):
            raise ValueError("completed watch task result cannot carry a failure")
        if (
            self.disposition is WatchTaskDisposition.RESUMABLE_HALT
            and self.exit_code not in {2, 3, 4}
        ):
            raise ValueError("resumable watch halt requires exit code 2, 3, or 4")
        if (
            self.disposition is WatchTaskDisposition.REJECTED
            and (
                self.exit_code != 5
                or self.resume_available
                or self.classified_failure is None
                or self.classified_failure.failure_class
                is not FailureClass.TERMINAL_REJECTION
            )
        ):
            raise ValueError(
                "rejected watch task requires an exit-5 terminal classification"
            )
        if (
            self.disposition is WatchTaskDisposition.TECHNICAL_FAILURE
            and (
                self.classified_failure is None
                or self.classified_failure.failure_class
                is not FailureClass.TRANSIENT
            )
        ):
            raise ValueError(
                "technical watch failure requires a typed transient classification"
            )
        if (
            self.disposition is WatchTaskDisposition.RESUMABLE_HALT
            and self.classified_failure is not None
            and self.classified_failure.failure_class
            is not FailureClass.RESUMABLE_HALT
        ):
            raise ValueError("resumable watch halt carries a conflicting classification")
        if self.gate_reason == GateReason.BOOTSTRAP_CHECK.value:
            if (
                self.disposition is not WatchTaskDisposition.RESUMABLE_HALT
                or self.status != WorkUnitStatus.AWAITING_RESUME.value
                or self.exit_code != 4
                or not self.resume_available
            ):
                raise ValueError(
                    "bootstrap check must remain an exit-4 resumable watch halt"
                )

    @classmethod
    def from_workflow(cls, result: WorkflowRunResult) -> WatchTaskResult:
        state = result.state
        status = state.current_work_unit.status
        resumable = status in {
            WorkUnitStatus.AWAITING_USER_DECISION,
            WorkUnitStatus.WAITING_FOR_QUOTA,
            WorkUnitStatus.WAITING_FOR_RETRY,
            WorkUnitStatus.AWAITING_RESUME,
        }
        bootstrap_halt = (
            status is WorkUnitStatus.AWAITING_RESUME
            and state.current_work_unit.gate.reason is GateReason.BOOTSTRAP_CHECK
        )
        terminal_rejection = result.workflow_rejected
        classified_failure: ClassifiedFailure | None = None
        if result.workflow_completed:
            disposition = WatchTaskDisposition.COMPLETED
            exit_code = 0
            result_status = status.value
        elif terminal_rejection:
            detail = result.rejection_detail
            diagnostic_code = result.rejection_code
            exception_type = result.rejection_exception_type
            assert detail is not None
            assert diagnostic_code is not None
            assert exception_type is not None
            disposition = WatchTaskDisposition.REJECTED
            exit_code = 5
            result_status = "rejected"
            classified_failure = ClassifiedFailure(
                failure_class=FailureClass.TERMINAL_REJECTION,
                diagnostic_code=diagnostic_code,
                exception_type=exception_type,
                detail=detail,
                cause_depth=0,
                explicitly_mapped=True,
            )
        elif bootstrap_halt:
            # This local denial happens before a provider process.  Exit 4 keeps it
            # distinct from quota/agent failures while preserving the watch identity
            # for an operator to repair the cause and resume the exact same step.
            disposition = WatchTaskDisposition.RESUMABLE_HALT
            exit_code = 4
            result_status = status.value
        elif resumable and result.exit_code in {2, 3, 4}:
            disposition = WatchTaskDisposition.RESUMABLE_HALT
            exit_code = result.exit_code
            result_status = status.value
        else:
            # A non-terminal workflow result without an acknowledged halt has no
            # retry authorization.  Treat the missing classification as a
            # fail-closed operator halt instead of manufacturing a transient.
            disposition = WatchTaskDisposition.RESUMABLE_HALT
            exit_code = 4
            result_status = status.value
            classified_failure = classify_exception(
                RuntimeError("workflow returned a non-resumable, non-terminal result")
            )
        return cls(
            exit_code=exit_code,
            run_id=state.run_id,
            disposition=disposition,
            status=result_status,
            step=state.current_step.value,
            work_unit_id=state.current_work_unit_id,
            gate_reason=(
                classified_failure.diagnostic_code
                if classified_failure is not None
                else state.current_work_unit.gate.reason.value
            ),
            failure_detail=(
                classified_failure.detail
                if classified_failure is not None
                else state.current_work_unit.gate.detail
                if disposition is WatchTaskDisposition.RESUMABLE_HALT
                else None
            ),
            protocol_mode=state.effective_protocol_mode.value,
            classified_failure=classified_failure,
            resume_available=(
                disposition is WatchTaskDisposition.RESUMABLE_HALT
            ),
        )

    @classmethod
    def from_failure(
        cls,
        failure: ClassifiedFailure,
        *,
        run_id: str,
        records_written: bool,
        protocol_mode: str | None = None,
    ) -> WatchTaskResult:
        """Translate a classified exception into one typed Watch outcome."""

        failure = enforce_record_start_boundary(
            failure, records_written=records_written
        )
        if failure.failure_class is FailureClass.TRANSIENT:
            disposition = WatchTaskDisposition.TECHNICAL_FAILURE
            exit_code = 1
            status = "technical_failure"
            resume_available = records_written
        elif failure.failure_class is FailureClass.TERMINAL_REJECTION:
            disposition = WatchTaskDisposition.REJECTED
            exit_code = 5
            status = "rejected"
            resume_available = False
        else:
            disposition = WatchTaskDisposition.RESUMABLE_HALT
            exit_code = 4
            status = "awaiting_resume" if records_written else "awaiting_user_decision"
            resume_available = records_written
        return cls(
            exit_code=exit_code,
            run_id=run_id,
            disposition=disposition,
            status=status,
            step="pipeline",
            work_unit_id=1,
            gate_reason=failure.diagnostic_code,
            failure_detail=failure.detail,
            resume_available=resume_available,
            protocol_mode=protocol_mode,
            classified_failure=failure,
        )


@dataclass(frozen=True)
class WatchTaskIdentity:
    run_id: str
    task_digest: str
    started: bool = False
    protocol_mode: str | None = None
    sidecar_version: int = 2

    def __post_init__(self) -> None:
        if WATCH_RUN_ID_PATTERN.fullmatch(self.run_id) is None:
            raise ValueError("watch task identity requires a safe run id")
        if len(self.task_digest) != 64 or any(
            character not in "0123456789abcdef" for character in self.task_digest
        ):
            raise ValueError("watch task identity requires a SHA-256 task digest")
        if self.protocol_mode not in {None, "structured-v2"}:
            raise ValueError("watch task identity protocol mode is invalid")
        if self.sidecar_version not in {1, 2}:
            raise ValueError("watch task identity sidecar version is invalid")
        if self.sidecar_version == 1 and self.protocol_mode is not None:
            raise ValueError("legacy watch identity cannot carry a protocol mode")

    def to_dict(self) -> dict[str, object]:
        document: dict[str, object] = {
            "version": self.sidecar_version,
            "run_id": self.run_id,
            "task_digest": self.task_digest,
            "started": self.started,
        }
        if self.sidecar_version == 2:
            document["protocol_mode"] = self.protocol_mode
        return document

    @classmethod
    def from_dict(cls, raw: object) -> WatchTaskIdentity:
        if not isinstance(raw, dict):
            raise ValueError("watch task identity has an invalid schema")
        version = raw.get("version")
        expected = {
            "version", "run_id", "task_digest", "started",
            *({"protocol_mode"} if version == 2 else set()),
        }
        if set(raw) != expected or version not in {1, 2} or not isinstance(raw["started"], bool):
            raise ValueError("watch task identity has an unsupported version or status")
        if not isinstance(raw["run_id"], str) or not isinstance(raw["task_digest"], str):
            raise ValueError("watch task identity fields have invalid types")
        protocol_mode = raw.get("protocol_mode")
        if protocol_mode is not None and not isinstance(protocol_mode, str):
            raise ValueError("watch task identity protocol mode has an invalid type")
        return cls(
            raw["run_id"], raw["task_digest"], raw["started"], protocol_mode, version
        )


def watch_identity_path(task_file: Path) -> Path:
    return task_file.with_name(f".{task_file.name}.watch.json")


def rejection_marker_path(task_file: Path) -> Path:
    return task_file.with_name(f".{task_file.name}.rejection.json")


def _task_digest(task_file: Path) -> str:
    path_stat = task_file.lstat()
    if not stat.S_ISREG(path_stat.st_mode):
        raise ValueError("queue task source must be a regular non-symlink file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(task_file, flags)
    try:
        descriptor_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(descriptor_stat.st_mode)
            or (path_stat.st_dev, path_stat.st_ino)
            != (descriptor_stat.st_dev, descriptor_stat.st_ino)
        ):
            raise ValueError("queue task source must be a regular non-symlink file")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _new_watch_run_id(task_file: Path) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S.%fZ")
    entropy = hashlib.sha256(
        f"{task_file.resolve()}:{stamp}".encode("utf-8")
    ).hexdigest()[:12]
    return f"watch-{stamp}-{entropy}"


def load_or_create_watch_identity(
    task_file: Path,
    *,
    run_id_fn: Callable[[Path], str] = _new_watch_run_id,
) -> WatchTaskIdentity:
    path = watch_identity_path(task_file)
    digest = _task_digest(task_file)
    if path.exists():
        try:
            identity = WatchTaskIdentity.from_dict(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"cannot resume invalid watch identity {path.name}: {exc}") from exc
        if identity.task_digest != digest:
            raise ValueError(
                f"watch task {task_file.name} changed after run {identity.run_id} started"
            )
        return identity
    identity = WatchTaskIdentity(run_id_fn(task_file), digest, sidecar_version=2)
    atomic_write_file(path, json.dumps(identity.to_dict(), sort_keys=True) + "\n")
    return identity


def save_watch_identity(task_file: Path, identity: WatchTaskIdentity) -> None:
    atomic_write_file(
        watch_identity_path(task_file),
        json.dumps(identity.to_dict(), sort_keys=True) + "\n",
    )


def delete_watch_identity(task_file: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        watch_identity_path(task_file).unlink()


def _rejection_evidence_digest(document: dict[str, object]) -> str:
    payload = {key: value for key, value in document.items() if key != "evidence_digest"}
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def save_rejection_marker(
    task_file: Path, task_result: WatchTaskResult
) -> None:
    """Durably bind a terminal result before its queue move is attempted."""

    failure = task_result.classified_failure
    if (
        task_result.disposition is not WatchTaskDisposition.REJECTED
        or failure is None
        or failure.failure_class is not FailureClass.TERMINAL_REJECTION
    ):
        raise ValueError("rejection marker requires a terminally rejected task")
    document: dict[str, object] = {
        "version": 1,
        "run_id": task_result.run_id,
        "task_digest": _task_digest(task_file),
        "protocol_mode": task_result.protocol_mode,
        "failure_class": failure.failure_class.value,
        "diagnostic_code": failure.diagnostic_code,
        "exception_type": failure.exception_type,
        "detail": failure.detail,
        "cause_depth": failure.cause_depth,
        "explicitly_mapped": failure.explicitly_mapped,
        "promoted_after_record_start": failure.promoted_after_record_start,
    }
    document["evidence_digest"] = _rejection_evidence_digest(document)
    atomic_write_file(
        rejection_marker_path(task_file),
        json.dumps(document, indent=2, sort_keys=True) + "\n",
    )


def load_rejection_marker(task_file: Path) -> WatchTaskResult:
    """Recover a prior terminal result without executing the task again."""

    path = rejection_marker_path(task_file)
    expected = {
        "version",
        "run_id",
        "task_digest",
        "protocol_mode",
        "failure_class",
        "diagnostic_code",
        "exception_type",
        "detail",
        "cause_depth",
        "explicitly_mapped",
        "promoted_after_record_start",
        "evidence_digest",
    }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load rejection marker {path.name}: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) != expected or raw.get("version") != 1:
        raise ValueError("rejection marker has an invalid schema")
    string_fields = {
        "run_id", "task_digest", "failure_class", "diagnostic_code",
        "exception_type", "detail", "evidence_digest",
    }
    if any(not isinstance(raw.get(field), str) for field in string_fields):
        raise ValueError("rejection marker has invalid string fields")
    if raw.get("protocol_mode") not in {None, "structured-v2"}:
        raise ValueError("rejection marker has an invalid protocol mode")
    if (
        not isinstance(raw.get("cause_depth"), int)
        or isinstance(raw.get("cause_depth"), bool)
        or raw["cause_depth"] < 0
        or raw.get("explicitly_mapped") is not True
        or raw.get("promoted_after_record_start") is not False
    ):
        raise ValueError("rejection marker has invalid classification fields")
    if raw["task_digest"] != _task_digest(task_file):
        raise ValueError("rejection marker task digest differs")
    if raw["evidence_digest"] != _rejection_evidence_digest(raw):
        raise ValueError("rejection marker evidence digest differs")
    try:
        failure_class = FailureClass(raw["failure_class"])
    except ValueError as exc:
        raise ValueError("rejection marker failure class is invalid") from exc
    if failure_class is not FailureClass.TERMINAL_REJECTION:
        raise ValueError("rejection marker is not terminal")
    failure = ClassifiedFailure(
        failure_class=failure_class,
        diagnostic_code=raw["diagnostic_code"],
        exception_type=raw["exception_type"],
        detail=raw["detail"],
        cause_depth=raw["cause_depth"],
        explicitly_mapped=True,
    )
    records_written = watch_run_has_records(Path.cwd(), raw["run_id"])
    return WatchTaskResult.from_failure(
        failure,
        run_id=raw["run_id"],
        records_written=records_written,
        protocol_mode=raw["protocol_mode"],
    )


def delete_rejection_marker(task_file: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        rejection_marker_path(task_file).unlink()


def list_inbox_tasks(inbox_dir: Path) -> list[Path]:
    tasks = [path for path in inbox_dir.glob("*.md") if path.is_file()]
    # Oldest-first ordering keeps processing deterministic across watcher restarts.
    return sorted(tasks, key=lambda path: (path.stat().st_mtime, path.name))


def is_file_stable(path: Path, now_epoch: float, min_age_seconds: float) -> bool:
    try:
        age_seconds = now_epoch - path.stat().st_mtime
    except FileNotFoundError:
        return False
    return age_seconds >= max(0.0, float(min_age_seconds))


def build_outbox_destination(outbox_subdir: Path, source_name: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%f")[:-3] + "Z"
    base_name = f"{stamp}_{source_name}"
    candidate = outbox_subdir / base_name
    if not candidate.exists():
        return candidate

    stem = Path(source_name).stem
    suffix = Path(source_name).suffix
    counter = 1
    while True:
        # Add numeric suffix when multiple tasks share the same millisecond timestamp.
        candidate = outbox_subdir / f"{stamp}_{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def move_to_outbox(task_file: Path, outbox_subdir: Path, *, source_name: str | None = None) -> Path:
    destination = build_outbox_destination(outbox_subdir, source_name or task_file.name)
    return move_to_reserved_outbox(task_file, destination)


def move_to_outbox_with_ledger(
    task_file: Path,
    outbox_subdir: Path,
    *,
    repository_root: Path,
    run_id: str,
    task_digest: str,
    source_name: str | None = None,
) -> Path:
    """Move one structured task through the same crash-safe queue ledger."""
    bridge = ArtifactBridge(ArtifactStore(repository_root.resolve(), run_id))
    source = _canonical(task_file)
    chain = bridge.store.load_chain()
    replay = replay_artifacts(chain, run_id)
    initializers = tuple(
        item
        for item in replay.side_effects
        if item.effect_class == "ledger"
        and item.operation == ("structured-v2-side-effect-ledger",)
        and item.result == "initialized"
    )
    if len(initializers) != 1:
        raise ValueError("queue move requires one initialized side-effect ledger")
    existing = tuple(
        item
        for item in replay.side_effects
        if item.effect_class == "queue_move"
        and item.work_unit_id == "queue"
        and item.operation[0] == str(source)
        and item.operation[2] == task_digest
    )
    if len(existing) > 1:
        raise ValueError("queue source has multiple move ledger entries")
    destination = (
        Path(existing[0].operation[1])
        if existing
        else _canonical(
            build_outbox_destination(
                outbox_subdir, source_name or task_file.name
            )
        )
    )
    spec = SideEffectSpec(
        "queue_move",
        "queue",
        (str(source), str(destination), task_digest),
        task_digest,
    )

    def perform_queue_move() -> tuple[Path, str]:
        moved = (
            move_to_reserved_outbox(task_file, destination)
            if task_file.exists()
            else destination
        )
        if (
            not destination.is_file()
            or destination.is_symlink()
            or _task_digest(destination) != task_digest
        ):
            raise ValueError(
                "ledgered queue destination differs before result completion"
            )
        return moved, task_digest

    result = SideEffectExecutor(bridge).execute(
        spec,
        reconcile=lambda: reconcile_queue_move(
            task_file, destination, task_digest
        ),
        perform=perform_queue_move,
    )
    if not destination.is_file() or _task_digest(destination) != task_digest:
        raise ValueError("ledgered queue destination differs from the task binding")
    return destination


def move_poison_to_outbox_recoverably(
    task_file: Path,
    outbox_subdir: Path,
    *,
    repository_root: Path,
    run_id: str,
    task_digest: str,
    source_name: str,
    quarantine_diagnostic: Callable[[str], None] | None = None,
) -> Path:
    """Quarantine even a pre-baseline or corrupt run without a retry loop."""
    try:
        return move_to_outbox_with_ledger(
            task_file,
            outbox_subdir,
            repository_root=repository_root,
            run_id=run_id,
            task_digest=task_digest,
            source_name=source_name,
        )
    except (ValueError, ArtifactBridgeError, SideEffectReconciliationError) as exc:
        detail = f"{type(exc).__name__}: {exc}"
        if quarantine_diagnostic is not None:
            quarantine_diagnostic(detail)
        logger.error(
            "Artifact chain is unavailable; using deterministic poison quarantine: %s",
            exc,
        )
        destination = _canonical(
            outbox_subdir
            / f"quarantine-{run_id}-{task_digest[:16]}-{source_name}"
        )
        outbox_subdir.mkdir(parents=True, exist_ok=True)
        outcome = reconcile_queue_move(task_file, destination, task_digest)
        if outcome.outcome is ReconciliationOutcome.OCCURRED:
            return destination
        if outcome.outcome is not ReconciliationOutcome.NOT_OCCURRED:
            raise ValueError("poison quarantine has an ambiguous physical state")
        return move_to_reserved_outbox(task_file, destination)


def move_to_reserved_outbox(task_file: Path, destination: Path) -> Path:
    # Re-check at the filesystem mutation boundary. Digest reads use O_NOFOLLOW,
    # while this guard prevents a path swapped afterward from being moved.
    try:
        source_mode = task_file.lstat().st_mode
    except FileNotFoundError as exc:
        raise ValueError("queue task source must be a regular non-symlink file") from exc
    if not stat.S_ISREG(source_mode):
        raise ValueError("queue task source must be a regular non-symlink file")
    shutil.move(str(task_file), str(destination))
    return destination


def attempt_sidecar_path(task_file: Path) -> Path:
    return task_file.with_name(f"{task_file.name}.attempts")


def success_marker_path(task_file: Path) -> Path:
    return task_file.with_name(f"{task_file.name}.success")


def has_success_marker(task_file: Path) -> bool:
    return success_marker_path(task_file).exists()


def has_bound_queue_success_marker(task_file: Path) -> bool:
    try:
        return isinstance(
            json.loads(success_marker_path(task_file).read_text(encoding="utf-8")), dict
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False


def write_success_marker(task_file: Path) -> None:
    marker = success_marker_path(task_file)
    marker.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")


def _canonical(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _validate_queue_paths(task_file: Path, inbox_dir: Path, outbox_dir: Path) -> tuple[Path, Path]:
    source = _canonical(task_file)
    inbox = _canonical(inbox_dir)
    done = _canonical(outbox_dir) / "done"
    if source.parent != inbox or source.suffix.casefold() != ".md":
        raise ValueError("queue task source is not a direct Markdown child of the configured inbox")
    if task_file.is_symlink():
        raise ValueError("queue task source must not be a symlink")
    return source, done


def load_watch_identity(
    task_file: Path, *, expected_digest: str | None = None
) -> WatchTaskIdentity:
    path = watch_identity_path(task_file)
    try:
        identity = WatchTaskIdentity.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"cannot load watch identity {path.name}: {exc}") from exc
    digest = expected_digest
    if task_file.exists():
        if task_file.is_symlink() or not task_file.is_file():
            raise ValueError("watch task source must be a regular non-symlink file")
        digest = _task_digest(task_file)
    if digest is None or identity.task_digest != digest:
        raise ValueError("watch identity task digest differs from queue task")
    if not identity.started or identity.sidecar_version != 2:
        raise ValueError("watch identity is not a started version-2 identity")
    if identity.protocol_mode != "structured-v2":
        raise ValueError("watch identity is not bound to structured-v2")
    return identity


def load_queue_success_evidence(
    task_file: Path,
    *,
    inbox_dir: Path,
    outbox_dir: Path,
    expected_run_id: str | None = None,
    expected_task_digest: str | None = None,
    expected_protocol_mode: str = "structured-v2",
) -> QueueSuccessEvidence:
    source, done = _validate_queue_paths(task_file, inbox_dir, outbox_dir)
    marker = success_marker_path(task_file)
    try:
        evidence = QueueSuccessEvidence.from_dict(
            json.loads(marker.read_text(encoding="utf-8"))
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"cannot load bound queue success evidence: {exc}") from exc
    destination = _canonical(Path(evidence.destination))
    if Path(evidence.source) != source or _canonical(Path(evidence.source)) != source:
        raise ValueError("queue success evidence source binding differs")
    if destination.parent != done or Path(evidence.destination) != destination:
        raise ValueError("queue success evidence destination escapes configured done outbox")
    if expected_run_id is not None and evidence.run_id != expected_run_id:
        raise ValueError("queue success evidence run id differs")
    if expected_task_digest is not None and evidence.task_digest != expected_task_digest:
        raise ValueError("queue success evidence task digest differs")
    if evidence.protocol_mode != expected_protocol_mode:
        raise ValueError("queue success evidence protocol mode differs")
    identity_path = watch_identity_path(task_file)
    if identity_path.exists():
        identity = load_watch_identity(task_file, expected_digest=evidence.task_digest)
        if identity.run_id != evidence.run_id or identity.protocol_mode != evidence.protocol_mode:
            raise ValueError("queue success evidence differs from watch identity")
    return evidence


def finalize_queue_success(
    task_file: Path,
    *,
    inbox_dir: Path,
    outbox_dir: Path,
    run_id: str | None = None,
    task_digest: str | None = None,
    protocol_mode: str = "structured-v2",
    publish: bool = False,
    repository_root: Path | None = None,
) -> QueueFinalizationResult:
    """Publish or recover a bound, idempotent successful queue finalization."""
    try:
        source, done = _validate_queue_paths(task_file, inbox_dir, outbox_dir)
        marker = success_marker_path(task_file)
        if marker.exists():
            evidence = load_queue_success_evidence(
                task_file,
                inbox_dir=inbox_dir,
                outbox_dir=outbox_dir,
                expected_run_id=run_id,
                expected_task_digest=task_digest,
                expected_protocol_mode=protocol_mode,
            )
        else:
            if not publish:
                return QueueFinalizationResult(QueueFinalizationDisposition.NOT_APPLICABLE)
            if run_id is None or task_digest is None:
                raise ValueError("publishing queue success requires run and task bindings")
            identity = load_watch_identity(task_file, expected_digest=task_digest)
            if identity.run_id != run_id or identity.protocol_mode != protocol_mode:
                raise ValueError("terminal workflow result differs from watch identity")
            if not task_file.is_file():
                raise ValueError("queue source is absent before success evidence publication")
            done.mkdir(parents=True, exist_ok=True)
            destination = _canonical(build_outbox_destination(done, task_file.name))
            evidence = QueueSuccessEvidence(
                run_id=run_id,
                task_digest=task_digest,
                protocol_mode=protocol_mode,
                source=str(source),
                destination=str(destination),
                evidence_digest=QueueSuccessEvidence.calculate_digest(
                    run_id=run_id,
                    task_digest=task_digest,
                    protocol_mode=protocol_mode,
                    source=str(source),
                    destination=str(destination),
                ),
            )
            atomic_write_file(marker, json.dumps(evidence.to_dict(), sort_keys=True) + "\n")

        destination = Path(evidence.destination)
        source_exists = task_file.exists()
        destination_exists = destination.exists()
        if source_exists and destination_exists:
            raise ValueError("queue source and bound destination both exist")
        if not source_exists and not destination_exists:
            raise ValueError("queue source and bound destination are both absent")
        if source_exists and _task_digest(task_file) != evidence.task_digest:
            raise ValueError("queue source digest differs from success evidence")
        if repository_root is not None:
            bridge = ArtifactBridge(
                ArtifactStore(repository_root.resolve(), evidence.run_id)
            )
            spec = SideEffectSpec(
                "queue_move",
                "queue",
                (str(source), str(destination), evidence.task_digest),
                evidence.task_digest,
            )

            def perform_queue_move() -> tuple[Path, str]:
                moved = (
                    move_to_reserved_outbox(task_file, destination)
                    if task_file.exists()
                    else destination
                )
                if (
                    not destination.is_file()
                    or destination.is_symlink()
                    or _task_digest(destination) != evidence.task_digest
                ):
                    raise ValueError(
                        "bound queue destination differs before ledger completion"
                    )
                return moved, evidence.task_digest

            SideEffectExecutor(bridge).execute(
                spec,
                reconcile=lambda: reconcile_queue_move(
                    task_file, destination, evidence.task_digest
                ),
                perform=perform_queue_move,
            )
        elif source_exists:
            move_to_reserved_outbox(task_file, destination)
        elif not destination.is_file() or destination.is_symlink():
            raise ValueError("bound queue destination is not a regular file")
        if _task_digest(destination) != evidence.task_digest:
            raise ValueError("bound queue destination digest differs from success evidence")

        # The marker remains until last, so every partial cleanup is safely resumable.
        delete_attempt_sidecar(task_file)
        delete_watch_identity(task_file)
        delete_rejection_marker(task_file)
        delete_success_marker(task_file)
        return QueueFinalizationResult(
            QueueFinalizationDisposition.COMPLETED, destination=destination
        )
    except Exception as exc:
        return QueueFinalizationResult(
            QueueFinalizationDisposition.FAILED,
            detail=f"{type(exc).__name__}: {exc}",
        )


def delete_success_marker(task_file: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        success_marker_path(task_file).unlink()


def read_attempt_count(task_file: Path) -> int:
    sidecar = attempt_sidecar_path(task_file)
    if not sidecar.exists():
        return 0
    try:
        raw = sidecar.read_text(encoding="utf-8").strip()
    except OSError:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def write_attempt_count(task_file: Path, count: int) -> None:
    sidecar = attempt_sidecar_path(task_file)
    sidecar.write_text(str(max(0, int(count))), encoding="utf-8")


def delete_attempt_sidecar(task_file: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        attempt_sidecar_path(task_file).unlink()


def write_poison_failure_report(
    destination: Path,
    *,
    attempts: int,
    task_result: WatchTaskResult | None,
    exception_detail: str | None,
) -> Path:
    """Persist the final technical cause next to a poison task."""
    report_path = destination.with_name(destination.name + ".error.json")
    payload = {
        "version": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "poison_task": destination.name,
        "attempts": attempts,
        "exit_code": None if task_result is None else task_result.exit_code,
        "run_id": None if task_result is None else task_result.run_id,
        "status": None if task_result is None else task_result.status,
        "step": None if task_result is None else task_result.step,
        "work_unit_id": None if task_result is None else task_result.work_unit_id,
        "gate_reason": None if task_result is None else task_result.gate_reason,
        "protocol_mode": None if task_result is None else task_result.protocol_mode,
        "failure_detail": (
            exception_detail
            if exception_detail is not None
            else None
            if task_result is None
            else task_result.failure_detail
        ),
        "failure_class": (
            None
            if task_result is None or task_result.classified_failure is None
            else task_result.classified_failure.failure_class.value
        ),
        "diagnostic_code": (
            None
            if task_result is None or task_result.classified_failure is None
            else task_result.classified_failure.diagnostic_code
        ),
    }
    atomic_write_file(report_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return report_path


def write_rejected_failure_report(
    destination: Path, *, task_result: WatchTaskResult
) -> Path:
    """Persist one typed terminal rejection next to its rejected task."""

    failure = task_result.classified_failure
    if (
        task_result.disposition is not WatchTaskDisposition.REJECTED
        or failure is None
        or failure.failure_class is not FailureClass.TERMINAL_REJECTION
    ):
        raise ValueError("rejection report requires a terminally rejected task")
    report_path = destination.with_name(destination.name + ".error.json")
    payload = {
        "version": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "rejected_task": destination.name,
        "attempts": 0,
        "exit_code": task_result.exit_code,
        "run_id": task_result.run_id,
        "status": task_result.status,
        "step": task_result.step,
        "work_unit_id": task_result.work_unit_id,
        "gate_reason": task_result.gate_reason,
        "protocol_mode": task_result.protocol_mode,
        "failure_class": failure.failure_class.value,
        "diagnostic_code": failure.diagnostic_code,
        "failure_detail": task_result.failure_detail,
    }
    atomic_write_file(report_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return report_path


def watch_run_has_baseline(repository_root: Path, run_id: str) -> bool:
    """Report whether the durable chain contains its identity/profile baseline.

    An unreadable chain is treated conservatively as already baseline-bound so
    this diagnostic path can never compete with an existing authoritative
    failure location merely because inspection itself failed.
    """

    if WATCH_RUN_ID_PATTERN.fullmatch(run_id) is None:
        return True
    try:
        chain = ArtifactStore(Path(repository_root).resolve(), run_id).load_chain()
    except (OSError, ValueError):
        return True
    record_types = tuple(record.record_type for record in chain)
    return (
        record_types.count(RecordType.RUN_IDENTITY) == 1
        and record_types.count(RecordType.RUN_PROFILE) == 1
    )


def pre_baseline_halt_diagnostic_path(
    repository_root: Path, run_id: str
) -> Path:
    """Return the single discoverable non-authoritative halt report path."""

    if WATCH_RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ValueError("pre-baseline diagnostic requires a safe run id")
    return (
        Path(repository_root).resolve()
        / ".orchestrator"
        / "logs"
        / f"{run_id}.pipeline.pre-baseline.failure.json"
    )


def write_pre_baseline_halt_diagnostic(
    repository_root: Path,
    task_result: WatchTaskResult,
    *,
    error: BaseException,
) -> Path | None:
    """Persist source-separated evidence for a halt without a baseline."""

    failure = task_result.classified_failure
    if (
        task_result.disposition is not WatchTaskDisposition.RESUMABLE_HALT
        or failure is None
        or failure.failure_class is not FailureClass.RESUMABLE_HALT
    ):
        return None
    if watch_run_has_baseline(repository_root, task_result.run_id):
        return None

    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__

    provider_parts: list[str] = []
    for candidate in chain:
        provider_text = getattr(candidate, "provider_text", None)
        if (
            isinstance(provider_text, str)
            and provider_text.strip()
            and provider_text not in provider_parts
        ):
            provider_parts.append(provider_text)

    source_is_known = all(
        type(candidate) in ERROR_CLASSIFICATIONS for candidate in chain
    )
    if provider_parts:
        readable_diagnostic = next(
            (
                diagnostic.text
                for candidate in chain
                if isinstance(
                    diagnostic := getattr(candidate, "orchestrator_diagnostic", None),
                    OrchestratorDiagnostic,
                )
            ),
            None,
        )
        last_cause = readable_diagnostic or (
            f"{failure.exception_type}: provider-origin detail redacted "
            f"({failure.diagnostic_code})"
        )
        provider_source = "\n".join(provider_parts)
    elif source_is_known:
        last_cause = failure.detail
        provider_source = None
    else:
        # Unknown exception text has no trustworthy source boundary. Treat it
        # as provider-origin evidence instead of risking disclosure.
        last_cause = (
            f"{failure.exception_type}: unclassified detail redacted "
            f"({failure.diagnostic_code})"
        )
        provider_source = failure.detail

    if provider_source is None:
        provider_marker = None
        provider_sha256 = None
        provider_bytes = 0
    else:
        provider_marker, provider_sha256, provider_bytes = provider_text_evidence(
            provider_source
        )
    report_path = pre_baseline_halt_diagnostic_path(
        repository_root, task_result.run_id
    )
    payload = {
        "version": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "failure_class": failure.failure_class.value,
        "diagnostic_code": failure.diagnostic_code,
        "run_id": task_result.run_id,
        "step": task_result.step,
        "last_cause": last_cause,
        "provider_text": provider_marker,
        "provider_text_sha256": provider_sha256,
        "provider_text_bytes": provider_bytes,
    }
    atomic_write_file(report_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return report_path


def watch_run_has_records(repository_root: Path, run_id: str) -> bool:
    """Conservatively report whether a Watch run has started its record chain."""

    if WATCH_RUN_ID_PATTERN.fullmatch(run_id) is None:
        return True
    records_dir = (
        Path(repository_root).resolve()
        / ".orchestrator"
        / "artifacts"
        / run_id
        / "records"
    )
    try:
        if not records_dir.is_dir():
            return False
        return any(records_dir.iterdir())
    except OSError:
        # An unreadable record directory cannot prove that the run is pristine.
        return True


def acquire_inbox_lock(inbox_dir: Path) -> TextIO | None:
    lock_path = inbox_dir / ".lock"
    # Open in a+ so the lock file is created if missing without truncating existing content.
    handle = lock_path.open("a+", encoding="utf-8")
    if fcntl is None:
        # Non-Unix fallback keeps functionality but cannot enforce single-process safety.
        logger.warning("fcntl not available; running without single-instance inbox lock.")
        return handle

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def release_inbox_lock(lock_handle: TextIO | None) -> None:
    if lock_handle is None:
        return
    try:
        if fcntl is not None:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    finally:
        lock_handle.close()


@dataclass(frozen=True)
class _WatchProcessingResult:
    task_result: WatchTaskResult
    run_id_failed: bool = False
    protocol_mode_failed: bool = False
    bind_protocol_mode: bool = False
    reset_started: bool = False


def _rename_stuck_task(task_file: Path, stuck_destination: Path) -> None:
    task_file.rename(stuck_destination)
    delete_attempt_sidecar(task_file)
    delete_success_marker(task_file)
    delete_watch_identity(task_file)
    delete_rejection_marker(task_file)


def _prepare_watch_invocation(
    task_file: Path,
    identity: WatchTaskIdentity,
    args: argparse.Namespace,
) -> tuple[WatchTaskIdentity, argparse.Namespace, bool]:
    task_args = copy.copy(args)
    task_args.watch_run_id = identity.run_id
    task_args.resume = identity.started
    task_args.force_overwrite_state = not identity.started
    force_new = not identity.started
    if not identity.started:
        identity = replace(identity, started=True)
        save_watch_identity(task_file, identity)
    return identity, task_args, force_new


def _process_watch_task(
    task_file: Path,
    task_args: argparse.Namespace,
    force_new: bool,
    identity: WatchTaskIdentity,
    process_task: Callable[
        [Path, argparse.Namespace, bool], int | WatchTaskResult
    ],
) -> _WatchProcessingResult:
    raw_result = process_task(task_file, task_args, force_new)
    if isinstance(raw_result, WatchTaskResult):
        task_result = raw_result
        if task_result.run_id != identity.run_id:
            logger.error(
                "Workflow result run id %s differs from persisted watch "
                "identity %s for %s.",
                task_result.run_id,
                identity.run_id,
                task_file.name,
            )
            return _WatchProcessingResult(task_result, run_id_failed=True)
        if (
            identity.protocol_mode is not None
            and task_result.protocol_mode is not None
            and task_result.protocol_mode != identity.protocol_mode
        ):
            logger.error(
                "Workflow protocol mode %s differs from persisted watch "
                "identity %s for %s.",
                task_result.protocol_mode,
                identity.protocol_mode,
                task_file.name,
            )
            return _WatchProcessingResult(
                task_result, protocol_mode_failed=True
            )
        bind_protocol_mode = (
            identity.sidecar_version == 2
            and identity.protocol_mode is None
            and task_result.protocol_mode is not None
        )
        reset_started = (
            task_result.disposition is WatchTaskDisposition.TECHNICAL_FAILURE
            and not task_result.resume_available
        )
        return _WatchProcessingResult(
            task_result,
            bind_protocol_mode=bind_protocol_mode,
            reset_started=reset_started,
        )

    exit_code = int(raw_result)
    if exit_code == 0 or exit_code in {2, 3, 4}:
        task_result = WatchTaskResult(
            exit_code=exit_code,
            run_id=identity.run_id,
            disposition=(
                WatchTaskDisposition.COMPLETED
                if exit_code == 0
                else WatchTaskDisposition.RESUMABLE_HALT
            ),
            status="legacy",
            step="legacy",
            work_unit_id=1,
            gate_reason="legacy",
            failure_detail=(None if exit_code == 0 else f"legacy exit code {exit_code}"),
        )
    else:
        task_result = WatchTaskResult.from_failure(
            classify_exception(RuntimeError(f"legacy exit code {exit_code}")),
            run_id=identity.run_id,
            records_written=watch_run_has_records(Path.cwd(), identity.run_id),
        )
    return _WatchProcessingResult(task_result)


def _strengthen_rejected_result(task_result: WatchTaskResult) -> WatchTaskResult:
    # Re-check input-rejection lifecycle authority immediately before publishing.
    # A reviewer verdict, in contrast, is terminal because its records exist.
    assert task_result.classified_failure is not None
    failure = task_result.classified_failure
    reviewer_verdict_keys = {
        ("FINAL-REVIEW-DENIED", "FinalReviewVerdict", True),
        ("CORRECTION-REVIEW-DENIED", "CorrectionReviewVerdict", True),
    }
    resolver = {
        # This verdict exists only after its review and failed-completion records.
        key: lambda: task_result for key in reviewer_verdict_keys
    }.get(
        (failure.diagnostic_code, failure.exception_type, failure.explicitly_mapped),
        lambda: WatchTaskResult.from_failure(
            failure,
            run_id=task_result.run_id,
            records_written=watch_run_has_records(Path.cwd(), task_result.run_id),
            protocol_mode=task_result.protocol_mode,
        ),
    )
    return resolver()


def _begin_rejected_archive(
    task_file: Path,
    outbox_failed_dir: Path,
    task_result: WatchTaskResult,
    rejected_name: str,
    *,
    task_rejected_already: bool,
) -> Path:
    if not task_rejected_already:
        save_rejection_marker(task_file, task_result)
    return move_to_outbox(
        task_file,
        outbox_failed_dir,
        source_name=rejected_name,
    )


def _finish_rejected_archive(
    task_file: Path,
    destination: Path,
    task_result: WatchTaskResult,
    report: Path | None,
) -> None:
    delete_attempt_sidecar(task_file)
    delete_success_marker(task_file)
    delete_watch_identity(task_file)
    delete_rejection_marker(task_file)
    logger.warning(
        "Task rejected without retry and moved to failed outbox: %s diagnostic=%s",
        destination,
        task_result.gate_reason,
    )
    if report is not None:
        logger.warning("Rejection report written: %s", report)


def _move_poison_task(
    task_file: Path,
    outbox_failed_dir: Path,
    task_result: WatchTaskResult,
    identity: WatchTaskIdentity | None,
    poison_name: str,
    quarantine_diagnostics: list[str],
) -> Path:
    return (
        move_poison_to_outbox_recoverably(
            task_file,
            outbox_failed_dir,
            repository_root=Path.cwd(),
            run_id=task_result.run_id,
            task_digest=identity.task_digest,
            source_name=poison_name,
            quarantine_diagnostic=quarantine_diagnostics.append,
        )
        if identity is not None
        else move_to_outbox(
            task_file,
            outbox_failed_dir,
            source_name=poison_name,
        )
    )


def _finish_poison_archive(
    task_file: Path,
    destination: Path,
    report: Path | None,
    *,
    attempts: int,
    max_retries: int,
) -> None:
    delete_attempt_sidecar(task_file)
    delete_success_marker(task_file)
    delete_watch_identity(task_file)
    delete_rejection_marker(task_file)
    logger.warning(
        "Task marked poison after %s/%s failures and moved to failed outbox: %s",
        attempts,
        max_retries,
        destination,
    )
    if report is not None:
        logger.warning("Poison failure report written: %s", report)


def _finalize_new_bound_success(
    task_file: Path,
    inbox_dir: Path,
    outbox_dir: Path,
    identity: WatchTaskIdentity,
) -> QueueFinalizationResult:
    return finalize_queue_success(
        task_file,
        inbox_dir=inbox_dir,
        outbox_dir=outbox_dir,
        run_id=identity.run_id,
        task_digest=identity.task_digest,
        protocol_mode=identity.protocol_mode or "structured-v2",
        publish=True,
        repository_root=Path.cwd(),
    )


def _recover_bound_success(
    task_file: Path,
    inbox_dir: Path,
    outbox_dir: Path,
) -> QueueFinalizationResult:
    return finalize_queue_success(
        task_file,
        inbox_dir=inbox_dir,
        outbox_dir=outbox_dir,
        repository_root=Path.cwd(),
    )


def _archive_completed_task(
    task_file: Path,
    outbox_done_dir: Path,
    bound_failure: str | None,
) -> None:
    # Compatibility for timestamp-only success markers from older watchers.
    if bound_failure is not None:
        raise RuntimeError(bound_failure)
    destination = move_to_outbox(task_file, outbox_done_dir)
    delete_attempt_sidecar(task_file)
    delete_success_marker(task_file)
    delete_watch_identity(task_file)
    delete_rejection_marker(task_file)
    logger.info("Moved task to done outbox: %s", destination)


def _processing_crash_result(
    task_file: Path,
    identity: WatchTaskIdentity,
    exc: Exception,
) -> WatchTaskResult:
    classified = enforce_record_start_boundary(
        classify_exception(exc),
        records_written=watch_run_has_records(Path.cwd(), identity.run_id),
    )
    task_result = WatchTaskResult.from_failure(
        classified,
        run_id=identity.run_id,
        records_written=watch_run_has_records(Path.cwd(), identity.run_id),
    )
    logger.exception("Task processing crashed for %s.", task_file)
    return task_result


def _log_resumable_pause(
    task_file: Path,
    task_result: WatchTaskResult,
) -> None:
    logger.warning(
        "Pausing watch queue for resumable task %s: run=%s work-unit=%s "
        "step=%s status=%s gate=%s detail=%s exit=%s",
        task_file.name,
        task_result.run_id,
        task_result.work_unit_id,
        task_result.step,
        task_result.status,
        task_result.gate_reason,
        task_result.failure_detail or "(none)",
        task_result.exit_code,
    )


def _log_technical_failure_result(
    task_file: Path, task_result: WatchTaskResult
) -> None:
    logger.info(
        "Task finished with exit code %s: %s",
        task_result.exit_code,
        task_file.name,
    )


def _log_bound_completion(
    task_file: Path,
    task_result: WatchTaskResult,
    queue_result: QueueFinalizationResult,
) -> None:
    logger.info("Moved task to done outbox: %s", queue_result.destination)
    logger.info(
        "Task finished with exit code %s: %s",
        task_result.exit_code,
        task_file.name,
    )


def _bound_finalization_failure(
    task_file: Path, queue_result: QueueFinalizationResult
) -> str:
    logger.error(
        "Bound queue finalization failed for %s: %s",
        task_file.name,
        queue_result.detail,
    )
    return queue_result.detail or "bound queue finalization failed"


def _log_bound_recovery(task_file: Path) -> None:
    logger.info(
        "Task bookkeeping completed for previously succeeded task: %s",
        task_file.name,
    )


def _bound_recovery_failure(
    task_file: Path, queue_result: QueueFinalizationResult
) -> str:
    logger.error(
        "Bound queue recovery failed for %s: %s",
        task_file.name,
        queue_result.detail,
    )
    return queue_result.detail or "bound queue recovery failed"


def _log_bound_success_pause(task_file: Path, bound_failure: str) -> None:
    # Bound evidence must never be redirected to failed/stuck: it names the
    # sole safe destination and is the recovery authority for a direct resume.
    logger.error(
        "Pausing watch queue with recoverable bound success evidence for %s: %s",
        task_file.name,
        bound_failure,
    )


def _archive_failed_task(
    task_file: Path,
    outbox_failed_dir: Path,
    failed_name: str,
    attempts: int,
    max_retries: int,
    marker_exists: bool,
) -> None:
    destination = move_to_outbox(
        task_file, outbox_failed_dir, source_name=failed_name
    )
    delete_attempt_sidecar(task_file)
    delete_success_marker(task_file)
    delete_watch_identity(task_file)
    delete_rejection_marker(task_file)
    if marker_exists:
        logger.warning(
            "Task SUCCEEDED (exit 0) but move to done/ failed (%s/%s). "
            "Marking as move_error despite successful execution: %s",
            attempts,
            max_retries,
            destination,
        )
    else:
        logger.warning(
            "Task marked poison after %s/%s failures and moved to failed outbox: %s",
            attempts,
            max_retries,
            destination,
        )


def _log_failed_archive_error(task_file: Path, marker_exists: bool) -> None:
    if marker_exists:
        logger.exception(
            "Failed to move succeeded-but-unmoved task %s to failed outbox.",
            task_file,
        )
    else:
        logger.exception("Failed to move poison task %s to outbox.", task_file)


def _log_archive_retry(
    task_file: Path,
    attempts: int,
    max_retries: int,
    marker_exists: bool,
) -> None:
    if marker_exists:
        logger.warning(
            "Task SUCCEEDED (exit 0) but move to done/ failed (%s/%s). "
            "Leaving in inbox to retry move only: %s",
            attempts,
            max_retries,
            task_file.name,
        )
    else:
        logger.warning(
            "Task move to done failed (%s/%s). Leaving in inbox for retry: %s",
            attempts,
            max_retries,
            task_file.name,
        )


def _log_watch_task_completion(
    task_file: Path,
    task_succeeded_already: bool,
    task_result: WatchTaskResult | None,
) -> None:
    if task_succeeded_already:
        logger.info(
            "Task bookkeeping completed for previously succeeded task: %s",
            task_file.name,
        )
    else:
        assert task_result is not None
        logger.info(
            "Task finished with exit code %s: %s",
            task_result.exit_code,
            task_file.name,
        )


def _handle_stuck_task(
    task_file: Path,
    current_attempts: int,
    stuck_limit: int,
) -> None:
    stuck_destination = task_file.with_suffix(".md.stuck")
    logger.critical(
        "Task %s stuck after %s attempts (limit %s). Renaming to %s for manual intervention.",
        task_file.name,
        current_attempts,
        stuck_limit,
        stuck_destination.name,
    )
    try:
        _rename_stuck_task(task_file, stuck_destination)
    except Exception:
        logger.exception("Failed to rename stuck task %s.", task_file)


def _archive_rejected_watch_task(
    task_file: Path,
    outbox_failed_dir: Path,
    task_result: WatchTaskResult,
    rejected_name: str,
    task_rejected_already: bool,
) -> None:
    destination = _begin_rejected_archive(
        task_file,
        outbox_failed_dir,
        task_result,
        rejected_name,
        task_rejected_already=task_rejected_already,
    )
    try:
        report = write_rejected_failure_report(
            destination, task_result=task_result
        )
    except Exception:
        report = None
        logger.exception(
            "Failed to write rejection report for %s.", destination
        )
    _finish_rejected_archive(
        task_file,
        destination,
        task_result,
        report,
    )


def _archive_poisoned_watch_task(
    task_file: Path,
    outbox_failed_dir: Path,
    task_result: WatchTaskResult,
    structured_identity: WatchTaskIdentity | None,
    poison_name: str,
    quarantine_diagnostics: list[str],
    attempts: int,
    max_retries: int,
) -> None:
    destination = _move_poison_task(
        task_file,
        outbox_failed_dir,
        task_result,
        structured_identity,
        poison_name,
        quarantine_diagnostics,
    )
    report: Path | None = None
    try:
        report = write_poison_failure_report(
            destination,
            attempts=attempts,
            task_result=task_result,
            exception_detail=(
                quarantine_diagnostics[-1]
                if quarantine_diagnostics
                else None
            ),
        )
    except Exception:
        logger.exception(
            "Failed to write poison failure report for %s.",
            destination,
        )
    _finish_poison_archive(
        task_file,
        destination,
        report,
        attempts=attempts,
        max_retries=max_retries,
    )


def _handle_completed_archive_failure(
    task_file: Path,
    outbox_failed_dir: Path,
    max_retries: int,
) -> None:
    # Keep retry accounting symmetrical with processing failures.
    attempts = read_attempt_count(task_file) + 1
    write_attempt_count(task_file, attempts)
    marker_exists = has_success_marker(task_file)
    if attempts >= max_retries:
        failed_name = (
            f"{task_file.name}.move_error"
            if marker_exists
            else f"{task_file.name}.poison"
        )
        try:
            _archive_failed_task(
                task_file,
                outbox_failed_dir,
                failed_name,
                attempts,
                max_retries,
                marker_exists,
            )
        except Exception:
            _log_failed_archive_error(task_file, marker_exists)
    else:
        _log_archive_retry(
            task_file, attempts, max_retries, marker_exists
        )


def watch_inbox(
    *,
    inbox_dir: Path,
    outbox_dir: Path,
    poll_interval: float,
    args: argparse.Namespace,
    process_task: Callable[
        [Path, argparse.Namespace, bool], int | WatchTaskResult
    ],
    min_file_age_seconds: float = 1.0,
    max_retries: int = 3,
    sleep_fn: Callable[[float], None] = time.sleep,
    time_fn: Callable[[], float] = time.time,
) -> int:
    """Continuously process stable inbox tasks and move them to done/failed outboxes."""

    inbox_dir.mkdir(parents=True, exist_ok=True)
    outbox_done_dir = outbox_dir / "done"
    outbox_failed_dir = outbox_dir / "failed"
    outbox_done_dir.mkdir(parents=True, exist_ok=True)
    outbox_failed_dir.mkdir(parents=True, exist_ok=True)

    lock_handle = acquire_inbox_lock(inbox_dir)
    if lock_handle is None:
        logger.error("Another watcher is already running on inbox: %s", inbox_dir)
        return 1

    logger.info(
        "Watching %s for .md files (outbox: %s, poll: %.2fs, min age: %.2fs, max retries: %s).",
        inbox_dir,
        outbox_dir,
        poll_interval,
        min_file_age_seconds,
        max_retries,
    )

    try:
        while True:
            now_epoch = time_fn()
            pending = list_inbox_tasks(inbox_dir)
            ready = [path for path in pending if is_file_stable(path, now_epoch, min_file_age_seconds)]

            if not ready:
                sleep_fn(poll_interval)
                continue

            task_file = ready[0]
            logger.info("Processing inbox task: %s", task_file)
            stuck_limit = max_retries * STUCK_RETRY_MULTIPLIER
            if stuck_limit > 0:
                current_attempts = read_attempt_count(task_file)
                if current_attempts >= stuck_limit:
                    _handle_stuck_task(task_file, current_attempts, stuck_limit)
                    continue

            task_result: WatchTaskResult | None = None
            task_succeeded_already = has_success_marker(task_file)
            task_rejected_already = rejection_marker_path(task_file).exists()

            if task_succeeded_already:
                logger.info(
                    "Skipping re-execution for already-succeeded task; retrying move only: %s",
                    task_file.name,
                )
            elif task_rejected_already:
                logger.info(
                    "Skipping re-execution for already-rejected task; retrying move only: %s",
                    task_file.name,
                )
                try:
                    task_result = load_rejection_marker(task_file)
                except ValueError as exc:
                    logger.error(
                        "Cannot safely finalize rejected watch task %s: %s",
                        task_file.name,
                        exc,
                    )
                    return 4
            else:
                try:
                    identity = load_or_create_watch_identity(task_file)
                except ValueError as exc:
                    logger.error(
                        "Cannot safely start or resume watch task %s: %s",
                        task_file.name,
                        exc,
                    )
                    return 4
                identity, task_args, force_new = _prepare_watch_invocation(
                    task_file, identity, args
                )
                try:
                    processing = _process_watch_task(
                        task_file,
                        task_args,
                        force_new,
                        identity,
                        process_task,
                    )
                    task_result = processing.task_result
                    if processing.run_id_failed:
                        return 4
                    if processing.protocol_mode_failed:
                        return 4
                    if processing.bind_protocol_mode:
                        identity = replace(
                            identity, protocol_mode=task_result.protocol_mode
                        )
                        save_watch_identity(task_file, identity)
                    if processing.reset_started:
                        identity = replace(identity, started=False)
                        save_watch_identity(task_file, identity)
                except Exception as exc:
                    task_result = _processing_crash_result(
                        task_file, identity, exc
                    )

            if (
                task_result is not None
                and task_result.disposition is WatchTaskDisposition.REJECTED
            ):
                task_result = _strengthen_rejected_result(task_result)

            if (
                task_result is not None
                and task_result.disposition is WatchTaskDisposition.REJECTED
            ):
                rejected_name = f"{task_file.name}.rejected"
                try:
                    _archive_rejected_watch_task(
                        task_file,
                        outbox_failed_dir,
                        task_result,
                        rejected_name,
                        task_rejected_already,
                    )
                except Exception:
                    logger.exception(
                        "Failed to move rejected task %s to outbox.", task_file
                    )
                    return 1
                continue

            if (
                task_result is not None
                and task_result.disposition is WatchTaskDisposition.RESUMABLE_HALT
            ):
                if not task_result.resume_available:
                    identity = replace(identity, started=False)
                    save_watch_identity(task_file, identity)
                _log_resumable_pause(task_file, task_result)
                return task_result.exit_code

            failed = (
                task_result is not None
                and task_result.disposition is WatchTaskDisposition.TECHNICAL_FAILURE
            )
            if failed:
                attempts = read_attempt_count(task_file) + 1
                write_attempt_count(task_file, attempts)
                if attempts >= max_retries:
                    # Poison-pill naming makes permanently failing tasks visible to operators.
                    poison_name = f"{task_file.name}.poison"
                    quarantine_diagnostics: list[str] = []
                    try:
                        _archive_poisoned_watch_task(
                            task_file,
                            outbox_failed_dir,
                            task_result,
                            (
                                identity
                                if task_result.protocol_mode == "structured-v2"
                                else None
                            ),
                            poison_name,
                            quarantine_diagnostics,
                            attempts,
                            max_retries,
                        )
                    except Exception:
                        logger.exception(
                            "Failed to move poison task %s to outbox.", task_file
                        )
                else:
                    logger.warning(
                        "Task failed (%s/%s). Leaving in inbox for retry: %s",
                        attempts,
                        max_retries,
                        task_file.name,
                    )
                assert task_result is not None
                _log_technical_failure_result(task_file, task_result)
                continue

            if not task_succeeded_already and (
                task_result is None
                or task_result.disposition is not WatchTaskDisposition.COMPLETED
            ):
                logger.error("Task %s returned no terminal watch result.", task_file.name)
                return 1

            bound_failure: str | None = None
            bound_completion = bool(
                not task_succeeded_already
                and task_result is not None
                and task_result.protocol_mode == "structured-v2"
            )
            if bound_completion:
                assert task_result is not None
                assert identity is not None
                queue_result = _finalize_new_bound_success(
                    task_file,
                    inbox_dir,
                    outbox_dir,
                    identity,
                )
                if queue_result.disposition is QueueFinalizationDisposition.COMPLETED:
                    _log_bound_completion(task_file, task_result, queue_result)
                    continue
                bound_failure = _bound_finalization_failure(
                    task_file, queue_result
                )
            elif not task_succeeded_already:
                # Legacy integer callbacks retain the historical timestamp marker and
                # move primitive; production structured results use bound evidence.
                try:
                    write_success_marker(task_file)
                except Exception as exc:
                    bound_failure = f"{type(exc).__name__}: {exc}"
            elif has_bound_queue_success_marker(task_file):
                queue_result = _recover_bound_success(
                    task_file, inbox_dir, outbox_dir
                )
                if queue_result.disposition is QueueFinalizationDisposition.COMPLETED:
                    _log_bound_recovery(task_file)
                    continue
                if queue_result.disposition is QueueFinalizationDisposition.FAILED:
                    bound_failure = _bound_recovery_failure(
                        task_file, queue_result
                    )

            if bound_failure is not None and has_bound_queue_success_marker(task_file):
                _log_bound_success_pause(task_file, bound_failure)
                return 1

            try:
                _archive_completed_task(task_file, outbox_done_dir, bound_failure)
            except Exception:
                _handle_completed_archive_failure(
                    task_file, outbox_failed_dir, max_retries
                )
                continue

            _log_watch_task_completion(
                task_file, task_succeeded_already, task_result
            )
    except KeyboardInterrupt:
        logger.info("Watch mode stopped.")
        return 0
    finally:
        release_inbox_lock(lock_handle)
