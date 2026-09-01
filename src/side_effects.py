"""Crash-safe execution and provider-free reconciliation for external effects."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import os
from pathlib import Path
import stat
from typing import Callable, TypeVar

from artifact_bridge import ArtifactBridge
from artifact_models import FingerprintKind, stable_side_effect_key


class SideEffectReconciliationError(RuntimeError):
    """Raised when an intent cannot be reconciled without repeating an effect."""


class ReconciliationOutcome(StrEnum):
    OCCURRED = "occurred"
    NOT_OCCURRED = "not_occurred"
    UNKNOWN = "unknown"


class SideEffectBoundaryPhase(StrEnum):
    """Stable interruption points around one ledger-bound physical effect."""

    BEFORE_INTENT = "before_intent"
    AFTER_INTENT = "after_intent"
    BEFORE_EFFECT = "before_effect"
    AFTER_EFFECT = "after_effect"
    BEFORE_RESULT = "before_result"
    AFTER_RESULT = "after_result"


@dataclass(frozen=True, slots=True)
class SideEffectBoundary:
    """One exact production boundary exposed to provider-free crash harnesses."""

    effect_class: str
    effect_key: str
    phase: SideEffectBoundaryPhase


SideEffectBoundaryObserver = Callable[[SideEffectBoundary], None]


@dataclass(frozen=True, slots=True)
class Reconciliation:
    outcome: ReconciliationOutcome
    result: str | None = None

    def __post_init__(self) -> None:
        if (self.outcome is ReconciliationOutcome.OCCURRED) != (self.result is not None):
            raise ValueError("only an occurred reconciliation carries a result")


@dataclass(frozen=True, slots=True)
class SideEffectSpec:
    effect_class: str
    work_unit_id: str
    operation: tuple[str, ...]
    fingerprint_sha256: str
    fingerprint_kind: FingerprintKind = FingerprintKind.IMPLEMENTATION

    @property
    def effect_key(self) -> str:
        return stable_side_effect_key(
            self.effect_class, self.work_unit_id, self.operation
        )


T = TypeVar("T")


@dataclass(slots=True)
class SideEffectExecutor:
    bridge: ArtifactBridge
    boundary_observer: SideEffectBoundaryObserver | None = None

    def _observe(self, spec: SideEffectSpec, phase: SideEffectBoundaryPhase) -> None:
        if self.boundary_observer is not None:
            self.boundary_observer(
                SideEffectBoundary(spec.effect_class, spec.effect_key, phase)
            )

    def _record_intent(self, spec: SideEffectSpec) -> bool:
        self._observe(spec, SideEffectBoundaryPhase.BEFORE_INTENT)
        _, created = self.bridge.record_side_effect_intent(
            effect_class=spec.effect_class,
            work_unit_id=spec.work_unit_id,
            operation=spec.operation,
            fingerprint_sha256=spec.fingerprint_sha256,
            fingerprint_kind=spec.fingerprint_kind,
        )
        self._observe(spec, SideEffectBoundaryPhase.AFTER_INTENT)
        return created

    def execute(
        self,
        spec: SideEffectSpec,
        *,
        reconcile: Callable[[], Reconciliation],
        perform: Callable[[], tuple[T, str]],
    ) -> T | str:
        """Execute once, or reconcile the crash window before any repetition."""
        created = self._record_intent(spec)
        result = self.bridge.side_effect_result(
            effect_class=spec.effect_class,
            work_unit_id=spec.work_unit_id,
            operation=spec.operation,
        )
        if result is not None:
            return result
        if not created:
            recovered = reconcile()
            if recovered.outcome is ReconciliationOutcome.UNKNOWN:
                raise SideEffectReconciliationError(
                    f"side effect {spec.effect_key!r} has an unknown physical outcome"
                )
            if recovered.outcome is ReconciliationOutcome.OCCURRED:
                assert recovered.result is not None
                self._observe(spec, SideEffectBoundaryPhase.AFTER_EFFECT)
                self._complete(spec, recovered.result)
                return recovered.result
        self._observe(spec, SideEffectBoundaryPhase.BEFORE_EFFECT)
        value, result = perform()
        self._observe(spec, SideEffectBoundaryPhase.AFTER_EFFECT)
        self._complete(spec, result)
        return value

    def begin(
        self,
        spec: SideEffectSpec,
        *,
        reconcile: Callable[[], Reconciliation],
    ) -> bool:
        """Open a split operation such as a provider process invocation."""
        created = self._record_intent(spec)
        result = self.bridge.side_effect_result(
            effect_class=spec.effect_class,
            work_unit_id=spec.work_unit_id,
            operation=spec.operation,
        )
        if result is not None:
            return False
        if created:
            self._observe(spec, SideEffectBoundaryPhase.BEFORE_EFFECT)
            return True
        recovered = reconcile()
        if recovered.outcome is ReconciliationOutcome.OCCURRED:
            assert recovered.result is not None
            self._observe(spec, SideEffectBoundaryPhase.AFTER_EFFECT)
            self._complete(spec, recovered.result)
            return False
        if recovered.outcome is ReconciliationOutcome.NOT_OCCURRED:
            self._observe(spec, SideEffectBoundaryPhase.BEFORE_EFFECT)
            return True
        raise SideEffectReconciliationError(
            f"side effect {spec.effect_key!r} has an unknown physical outcome"
        )

    def complete(self, spec: SideEffectSpec, result: str) -> None:
        # Split operations call ``complete`` immediately after the physical
        # provider/process edge, so expose the same post-effect boundary as
        # the single-call ``execute`` path.
        self._observe(spec, SideEffectBoundaryPhase.AFTER_EFFECT)
        self._complete(spec, result)

    def _complete(self, spec: SideEffectSpec, result: str) -> None:
        self._observe(spec, SideEffectBoundaryPhase.BEFORE_RESULT)
        self.bridge.record_side_effect_result(
            effect_class=spec.effect_class,
            work_unit_id=spec.work_unit_id,
            operation=spec.operation,
            result=result,
            fingerprint_sha256=spec.fingerprint_sha256,
            fingerprint_kind=spec.fingerprint_kind,
        )
        self._observe(spec, SideEffectBoundaryPhase.AFTER_RESULT)


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _regular_file_digest(path: Path) -> tuple[bool, str | None]:
    """Return presence and digest without accepting or following a symlink."""
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return False, None
    if not stat.S_ISREG(path_stat.st_mode):
        return True, None
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return True, None
    try:
        descriptor_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(descriptor_stat.st_mode)
            or (path_stat.st_dev, path_stat.st_ino)
            != (descriptor_stat.st_dev, descriptor_stat.st_ino)
        ):
            return True, None
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        return True, digest.hexdigest()
    finally:
        os.close(descriptor)


def file_state_digest(path: Path) -> str:
    """Capture the exact regular-file state used by an overwriting intent."""
    exists, digest = _regular_file_digest(path)
    if not exists:
        return "absent"
    if digest is None:
        raise SideEffectReconciliationError(
            f"file side-effect target {path} is not a stable regular file"
        )
    return digest


def encode_file_write_content(content: bytes) -> str:
    return base64.b64encode(content).decode("ascii")


def decode_file_write_content(encoded: str, expected_sha256: str) -> bytes:
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise SideEffectReconciliationError(
            "file side-effect intent contains invalid durable content"
        ) from exc
    if sha256_bytes(content) != expected_sha256:
        raise SideEffectReconciliationError(
            "file side-effect intent content differs from its digest"
        )
    return content


def reconcile_file_write(
    path: Path,
    expected_sha256: str,
    prior_sha256: str | None = None,
) -> Reconciliation:
    exists, actual = _regular_file_digest(path)
    if not exists:
        if prior_sha256 is None or prior_sha256 == "absent":
            return Reconciliation(ReconciliationOutcome.NOT_OCCURRED)
        return Reconciliation(ReconciliationOutcome.UNKNOWN)
    if actual is None:
        return Reconciliation(ReconciliationOutcome.UNKNOWN)
    if actual == expected_sha256:
        return Reconciliation(ReconciliationOutcome.OCCURRED, actual)
    if prior_sha256 is not None and actual == prior_sha256:
        return Reconciliation(ReconciliationOutcome.NOT_OCCURRED)
    return Reconciliation(ReconciliationOutcome.UNKNOWN)


def reconcile_queue_move(
    source: Path,
    destination: Path,
    expected_sha256: str,
) -> Reconciliation:
    source_exists, source_digest = _regular_file_digest(source)
    destination_exists, destination_digest = _regular_file_digest(destination)
    if source_exists and not destination_exists:
        if source_digest == expected_sha256:
            return Reconciliation(ReconciliationOutcome.NOT_OCCURRED)
        return Reconciliation(ReconciliationOutcome.UNKNOWN)
    if not source_exists and destination_exists:
        if destination_digest == expected_sha256:
            return Reconciliation(ReconciliationOutcome.OCCURRED, destination_digest)
    return Reconciliation(ReconciliationOutcome.UNKNOWN)


def reconcile_provider_start(
    durable_response: Path,
    expected_sha256: str | None = None,
) -> Reconciliation:
    if not durable_response.exists():
        # An absent answer cannot prove that a remote request was not accepted.
        return Reconciliation(ReconciliationOutcome.UNKNOWN)
    if not durable_response.is_file():
        return Reconciliation(ReconciliationOutcome.UNKNOWN)
    actual = sha256_bytes(durable_response.read_bytes())
    if expected_sha256 is not None and actual != expected_sha256:
        return Reconciliation(ReconciliationOutcome.UNKNOWN)
    return Reconciliation(ReconciliationOutcome.OCCURRED, actual)


def reconcile_git_commit(
    *,
    prior_head: str,
    current_head: str,
    current_parent: str | None,
    expected_tree: str,
    current_tree: str | None,
) -> Reconciliation:
    if current_head == prior_head:
        return Reconciliation(ReconciliationOutcome.NOT_OCCURRED)
    if current_parent == prior_head and current_tree == expected_tree:
        return Reconciliation(ReconciliationOutcome.OCCURRED, current_head)
    return Reconciliation(ReconciliationOutcome.UNKNOWN)
