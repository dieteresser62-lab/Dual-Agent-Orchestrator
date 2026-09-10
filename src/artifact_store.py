"""Root-bound, append-only persistence for structured artifact records."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import shlex
import stat
import tempfile
import threading
from typing import Iterator

from artifact_models import (
    ArtifactRecord,
    ArtifactValidationError,
    BlobReference,
    ProviderContentPayload,
    RecordType,
    ReviewPacketPayload,
    ValidationContentPayload,
    WorkflowCompletionPayload,
    canonical_json,
)
from content_authority import ValidationCapture, validation_output_digest


_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_RECORD_NAME_RE = re.compile(r"^(ar1-[0-9a-f]{64})\.json$")
_BLOB_NAME_RE = re.compile(r"^([0-9a-f]{64})\.blob$")
logger = logging.getLogger(__name__)
_INVALID_CACHE = object()
_EMPTY_CHAIN_SHA256 = hashlib.sha256(b"artifact-chain-v1").hexdigest()
DEFAULT_PHASE_PROGRESS_THRESHOLD_SECONDS = 30.0


class ArtifactStoreError(ValueError):
    """Raised when an artifact store is inconsistent or cannot be trusted."""


class ArtifactConflictError(ArtifactStoreError):
    """Raised when an append would contradict an already persisted record."""


class ArtifactCorruptionError(ArtifactStoreError):
    """Raised when persisted bytes do not form one valid append-only chain."""


@dataclass(frozen=True, slots=True)
class ArtifactAppendContext:
    """Derived lookup facts needed to construct one append candidate."""

    existing: ArtifactRecord | None
    next_revision: int
    predecessor_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _RecordsDirectoryStamp:
    exists: bool
    modified_ns: int | None
    changed_ns: int | None


@dataclass(frozen=True, slots=True)
class _ArtifactFileStamp:
    """Cheap change detector for bytes validated earlier in this process."""

    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(slots=True)
class _AppendIndex:
    """Process-local derivative of one fully validated record prefix."""

    run_id: str
    chain: tuple[ArtifactRecord, ...]
    by_idempotency_key: dict[str, tuple[str, ArtifactRecord]]
    record_ids: set[str]
    revision_keys: set[tuple[RecordType, str, int]]
    max_revisions: dict[tuple[RecordType, str], int]
    head_record_id: str | None
    record_count: int
    chain_sha256: str
    records_dir_stamp: _RecordsDirectoryStamp

    def head_document(self) -> dict[str, object]:
        return {
            "head_record_id": self.head_record_id,
            "record_count": self.record_count,
            "chain_sha256": self.chain_sha256,
        }


class ArtifactStore:
    """Persist one run below ``.orchestrator/artifacts/<run-id>``.

    Record files are authoritative. ``head.json`` is only a reconstructable
    acceleration cache and is replaced whenever a scan finds it missing or
    stale. Dot-prefixed temporary files are deliberately excluded from scans.
    """

    def __init__(
        self,
        repository_root: Path,
        run_id: str,
        *,
        progress_threshold_seconds: float = DEFAULT_PHASE_PROGRESS_THRESHOLD_SECONDS,
    ) -> None:
        if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
            raise ArtifactStoreError("run_id is not a safe artifact path component")
        root = Path(repository_root).resolve()
        if not root.is_dir():
            raise ArtifactStoreError("repository_root must be an existing directory")
        if (
            isinstance(progress_threshold_seconds, bool)
            or not isinstance(progress_threshold_seconds, (int, float))
            or not math.isfinite(float(progress_threshold_seconds))
            or progress_threshold_seconds <= 0
        ):
            raise ArtifactStoreError("progress threshold must be a positive finite number")
        self.repository_root = root
        self.run_id = run_id
        self.progress_threshold_seconds = float(progress_threshold_seconds)
        self.run_dir = self._confined(root / ".orchestrator" / "artifacts" / run_id)
        self.records_dir = self._confined(self.run_dir / "records")
        self.blobs_dir = self._confined(self.run_dir / "blobs")
        self.head_path = self._confined(self.run_dir / "head.json")
        self._append_index: _AppendIndex | None = None

    @contextmanager
    def progress_phase(self, phase: str) -> Iterator[None]:
        """Log once while a configured slow phase is still running."""
        if not isinstance(phase, str) or not phase.strip():
            raise ArtifactStoreError("progress phase must be a non-empty string")
        finished = threading.Event()

        def report() -> None:
            if not finished.is_set():
                logger.info(
                    "Artifact phase still running: run=%s phase=%s threshold=%gs",
                    self.run_id,
                    phase,
                    self.progress_threshold_seconds,
                )

        timer = threading.Timer(self.progress_threshold_seconds, report)
        timer.daemon = True
        timer.start()
        try:
            yield
        finally:
            finished.set()
            timer.cancel()

    def put_blob(self, content: bytes) -> BlobReference:
        """Publish immutable run-bound content and return its recordable binding."""
        if not isinstance(content, bytes):
            raise ArtifactStoreError("artifact blob content must be bytes")
        reference = BlobReference(hashlib.sha256(content).hexdigest(), len(content))
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        target = self._blob_path(reference.sha256)
        if target.exists():
            persisted = self.read_blob(reference)
            if persisted != content:  # pragma: no cover - digest collision guard
                raise ArtifactConflictError("artifact blob digest has conflicting content")
            return reference
        try:
            _atomic_write(target, content)
        except Exception:
            if not target.exists():
                raise
        persisted = self.read_blob(reference)
        if persisted != content:  # pragma: no cover - read_blob verifies this too
            raise ArtifactCorruptionError("published artifact blob differs from input")
        return reference

    def read_blob(self, reference: BlobReference) -> bytes:
        """Read one record-bound blob, rejecting absence, links, size, or digest drift."""
        if not isinstance(reference, BlobReference):
            raise ArtifactStoreError("artifact blob reference is invalid")
        path = self._blob_path(reference.sha256)
        if not path.is_file() or path.is_symlink():
            raise ArtifactCorruptionError(
                f"artifact blob is missing or not a regular file: {path.name!r}"
            )
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise ArtifactCorruptionError(
                f"artifact blob cannot be read: {path.name!r}: {exc}"
            ) from exc
        if len(content) != reference.bytes:
            raise ArtifactCorruptionError(
                f"artifact blob byte length mismatch: {path.name!r}"
            )
        if hashlib.sha256(content).hexdigest() != reference.sha256:
            raise ArtifactCorruptionError(
                f"artifact blob digest mismatch: {path.name!r}"
            )
        return content

    def append_context(
        self,
        *,
        record_type: RecordType,
        logical_id: str,
        idempotency_key: str,
    ) -> ArtifactAppendContext:
        """Return append lookup facts without rescanning an unchanged chain."""
        index = self._ensure_append_index()
        existing = index.by_idempotency_key.get(idempotency_key)
        return ArtifactAppendContext(
            existing=None if existing is None else existing[1],
            next_revision=index.max_revisions.get((record_type, logical_id), 0) + 1,
            predecessor_ids=(
                () if index.head_record_id is None else (index.head_record_id,)
            ),
        )

    def put(self, record: ArtifactRecord) -> ArtifactRecord:
        """Append ``record`` or return the matching idempotent prior write.

        Appends require one external writer per run. The store detects conflicts
        and corruption but does not serialize concurrent callers or processes.

        An exception does not prove that nothing was written.  Once the bytes
        have been atomically published in ``records_dir``, a later verification
        scan or head-cache refresh can still fail.  Callers must therefore treat
        every exception as *possibly already persisted* and may safely retry the
        identical logical call (same idempotency key and semantic content).
        """
        if record.run_id != self.run_id:
            raise ArtifactConflictError(
                f"record run_id {record.run_id!r} does not match store {self.run_id!r}"
            )
        index = self._ensure_append_index()
        semantic = _semantic_digest(record)
        existing = index.by_idempotency_key.get(record.idempotency_key)
        if existing is not None:
            if existing[0] != semantic:
                raise ArtifactConflictError(
                    f"idempotency key {record.idempotency_key!r} has conflicting content"
                )
            return existing[1]
        if record.record_id in index.record_ids:
            raise ArtifactConflictError(
                f"record_id {record.record_id!r} already exists with another idempotency key"
            )
        revision_key = (record.record_type, record.logical_id, record.revision)
        if revision_key in index.revision_keys:
            raise ArtifactConflictError(
                "duplicate revision for record_type/logical_id"
            )

        expected_head = index.head_record_id
        if expected_head is None:
            if record.predecessor_ids:
                raise ArtifactConflictError("the first record cannot have predecessors")
        elif not record.predecessor_ids or record.predecessor_ids[0] != expected_head:
            raise ArtifactConflictError(
                f"record predecessor does not match current head {expected_head!r}"
            )
        missing = [
            item for item in record.predecessor_ids if item not in index.record_ids
        ]
        if missing:
            raise ArtifactConflictError(f"record references missing predecessor {missing[0]!r}")
        _validate_store_invariants(record)

        record_bytes = record.canonical_json()
        envelope = canonical_json(
            {
                "content_sha256": hashlib.sha256(record_bytes).hexdigest(),
                "record": record.to_dict(),
            }
        ) + b"\n"
        self.records_dir.mkdir(parents=True, exist_ok=True)
        target = self._record_path(record.record_id)
        if target.exists():
            # A concurrent/crash-recovery publication is resolved by a fresh scan.
            self._append_index = None
            recovered = self.load_chain()
            for persisted in recovered:
                if persisted.idempotency_key == record.idempotency_key:
                    if _semantic_digest(persisted) == semantic:
                        return persisted
            raise ArtifactConflictError(f"record target already exists: {target.name}")
        try:
            _atomic_write(target, envelope)
            published_before = self._artifact_file_stamp(target)
            published, _ = _read_record(target)
            published_stamp = self._artifact_file_stamp(target)
            if published_before != published_stamp:
                raise ArtifactCorruptionError(
                    "published record changed during verification"
                )
            if published != record:
                raise ArtifactCorruptionError(
                    "published record differs from the append candidate"
                )
            _validate_store_invariants(published, persisted=True)
            self._validate_record_blobs((published,))

            # The prefix was fully validated when the process-local index was
            # built and its directory/head proofs were checked immediately
            # before publication.  Re-statting every prefix record and blob at
            # this point made each append O(chain length), especially on 9p.
            # Verify only the newly published record and its referenced blobs;
            # explicit load/resume paths retain the complete fail-closed scan.
            # A cooperating second writer which completed its cache update
            # after our pre-write guard still invalidates the head proof.
            if not self._head_cache_matches(index):
                self._append_index = None
                recovered = self.load_chain()
                result = next(
                    (item for item in recovered if item.record_id == record.record_id),
                    None,
                )
                if result is None:
                    raise ArtifactCorruptionError(
                        "published record was not recovered by store scan"
                    )
                return result
            prior_document = index.head_document()
            index.by_idempotency_key[record.idempotency_key] = (semantic, published)
            index.record_ids.add(record.record_id)
            index.revision_keys.add(revision_key)
            revision_identity = (record.record_type, record.logical_id)
            index.max_revisions[revision_identity] = max(
                record.revision,
                index.max_revisions.get(revision_identity, 0),
            )
            index.head_record_id = record.record_id
            index.record_count += 1
            index.chain_sha256 = _extend_chain_sha256(
                index.chain_sha256, record.record_id
            )
            index.chain = (*index.chain, published)
            index.records_dir_stamp = self._records_directory_stamp()
            self._refresh_append_head_cache(index, prior_document)
            return published
        except Exception:
            # Once publication may have happened, no in-memory derivative is
            # retained.  The bridge's durable-failure recovery performs a full
            # authoritative scan before deciding whether to propagate.
            self._append_index = None
            raise

    def load_chain(self) -> tuple[ArtifactRecord, ...]:
        """Explicitly reload and fully validate the authoritative chain."""
        with self.progress_phase("full-chain-validation"):
            return self._load_chain()

    def current_chain(self) -> tuple[ArtifactRecord, ...]:
        """Return the process-local validated prefix, rebuilding on any drift."""
        with self.progress_phase("process-local-chain-validation"):
            return self._ensure_append_index().chain

    def _load_chain(
        self,
        *,
        expected_cache_chain: tuple[ArtifactRecord, ...] | None = None,
    ) -> tuple[ArtifactRecord, ...]:
        """Internal scan with optional proof of this store's own append."""
        self._append_index = None
        if not self.records_dir.exists():
            if self.head_path.exists():
                self._refresh_cache_with_context((), expected_cache_chain)
            self._append_index = _build_append_index(
                self.run_id, (), self._records_directory_stamp()
            )
            return ()
        initial_directory_stamp = self._records_directory_stamp()
        records: dict[str, ArtifactRecord] = {}
        record_file_stamps: dict[str, _ArtifactFileStamp] = {}
        semantics_by_key: dict[str, str] = {}
        revisions: set[tuple[RecordType, str, int]] = set()
        for path in sorted(self.records_dir.iterdir(), key=lambda item: item.name):
            if path.name.startswith(".") and path.name.endswith(".tmp"):
                continue
            match = _RECORD_NAME_RE.fullmatch(path.name)
            if match is None:
                raise ArtifactCorruptionError(
                    f"unexpected entry in artifact records directory: {path.name!r}"
                )
            record, opened_stamp = _read_record(path)
            record_file_stamps[path.name] = opened_stamp
            if record.record_id != match.group(1):
                raise ArtifactCorruptionError(
                    f"record filename does not match record_id: {path.name!r}"
                )
            if record.run_id != self.run_id:
                raise ArtifactCorruptionError(
                    f"record {record.record_id!r} belongs to run {record.run_id!r}"
                )
            if record.record_id in records:
                raise ArtifactCorruptionError(f"duplicate record_id {record.record_id!r}")
            revision_key = (record.record_type, record.logical_id, record.revision)
            if revision_key in revisions:
                raise ArtifactCorruptionError(
                    "duplicate revision for record_type/logical_id"
                )
            revisions.add(revision_key)
            semantic = _semantic_digest(record)
            prior_semantic = semantics_by_key.get(record.idempotency_key)
            if prior_semantic is not None and prior_semantic != semantic:
                raise ArtifactCorruptionError(
                    f"idempotency key {record.idempotency_key!r} has conflicting content"
                )
            if prior_semantic is not None:
                raise ArtifactCorruptionError(
                    f"idempotency key {record.idempotency_key!r} is duplicated"
                )
            semantics_by_key[record.idempotency_key] = semantic
            _validate_store_invariants(record, persisted=True)
            records[record.record_id] = record

        ordered = _order_chain(records)
        blob_file_stamps = self._validate_record_blobs(ordered)
        final_directory_stamp = self._records_directory_stamp()
        record_binding_matches = self._file_stamps_match(
            self.records_dir, record_file_stamps
        )
        blob_binding_matches = all(
            self._file_stamp_matches(self.blobs_dir / name, expected)
            for name, expected in blob_file_stamps.items()
        )
        if (
            initial_directory_stamp != final_directory_stamp
            or not record_binding_matches
            or not blob_binding_matches
        ):
            raise ArtifactCorruptionError(
                "artifact chain changed during validation"
            )
        self._refresh_cache_with_context(ordered, expected_cache_chain)
        self._append_index = _build_append_index(
            self.run_id,
            ordered,
            final_directory_stamp,
        )
        return ordered

    def _ensure_append_index(self) -> _AppendIndex:
        index = self._append_index
        if index is not None and not self._derivative_matches(index):
            logger.warning(
                "Discarding stale artifact append index for run %s", self.run_id
            )
            self._append_index = None
            index = None
        if index is None:
            self.load_chain()
            index = self._append_index
        if index is None:  # pragma: no cover - defensive postcondition
            raise ArtifactStoreError("validated append index was not reconstructed")
        return index

    def _derivative_matches(self, index: _AppendIndex) -> bool:
        # Content stamps belong to the explicit full-chain validation above.
        # The hot lookup deliberately uses only constant-cost chain proofs:
        # structural record changes update the directory stamp, while a
        # cooperating append also advances the reconstructable head cache.
        return (
            index.run_id == self.run_id
            and index.records_dir_stamp == self._records_directory_stamp()
            and self._head_cache_matches(index)
        )

    def _file_stamps_match(
        self,
        directory: Path,
        expected: dict[str, _ArtifactFileStamp],
    ) -> bool:
        if not expected and not directory.exists():
            return True
        try:
            actual = {
                path.name: self._artifact_file_stamp(path)
                for path in directory.iterdir()
                if not (path.name.startswith(".") and path.name.endswith(".tmp"))
            }
        except (FileNotFoundError, OSError, ArtifactStoreError):
            return False
        return actual == expected

    def _file_stamp_matches(
        self, path: Path, expected: _ArtifactFileStamp
    ) -> bool:
        try:
            return self._artifact_file_stamp(path) == expected
        except (FileNotFoundError, OSError, ArtifactStoreError):
            return False

    def _artifact_file_stamp(self, path: Path) -> _ArtifactFileStamp:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ArtifactStoreError(
                f"artifact path is not a regular file: {path.name!r}"
            )
        return _artifact_file_stamp_from_stat(metadata)

    def _records_directory_stamp(self) -> _RecordsDirectoryStamp:
        try:
            stat = self.records_dir.stat()
        except FileNotFoundError:
            return _RecordsDirectoryStamp(False, None, None)
        return _RecordsDirectoryStamp(True, stat.st_mtime_ns, stat.st_ctime_ns)

    def _read_head_cache(self) -> object:
        try:
            metadata = self.head_path.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                raise ArtifactStoreError(
                    f"artifact path is not a regular file: {self.head_path.name!r}"
                )
            return json.loads(self.head_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError):
            return _INVALID_CACHE

    def _head_cache_matches(self, index: _AppendIndex) -> bool:
        current = self._read_head_cache()
        if index.record_count == 0 and current is None:
            return True
        return current == index.head_document()

    def _refresh_append_head_cache(
        self,
        index: _AppendIndex,
        prior_document: dict[str, object],
    ) -> None:
        current = self._read_head_cache()
        allowed_prior = (
            (prior_document, None)
            if prior_document["record_count"] == 0
            else (prior_document,)
        )
        if current not in allowed_prior:
            raise ArtifactCorruptionError(
                "artifact head cache changed during append; authoritative scan required"
            )
        expected = index.head_document()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.head_path, canonical_json(expected) + b"\n")

    def select(
        self,
        *,
        record_type: RecordType | str | None = None,
        logical_id: str | None = None,
    ) -> tuple[ArtifactRecord, ...]:
        """Return chain-ordered records matching type and/or logical identity."""
        normalized_type = RecordType(record_type) if record_type is not None else None
        return tuple(
            record
            for record in self.load_chain()
            if (normalized_type is None or record.record_type is normalized_type)
            and (logical_id is None or record.logical_id == logical_id)
        )

    def find(
        self,
        *,
        record_type: RecordType | str | None = None,
        logical_id: str | None = None,
    ) -> tuple[ArtifactRecord, ...]:
        return self.select(record_type=record_type, logical_id=logical_id)

    @property
    def head(self) -> ArtifactRecord | None:
        chain = self.load_chain()
        return chain[-1] if chain else None

    def rebuild_head_cache(self) -> str | None:
        chain = self.load_chain()
        return chain[-1].record_id if chain else None

    def _record_path(self, record_id: str) -> Path:
        if _RECORD_NAME_RE.fullmatch(f"{record_id}.json") is None:
            raise ArtifactStoreError("record_id is not a safe artifact filename")
        return self.records_dir / f"{record_id}.json"

    def _blob_path(self, sha256: str) -> Path:
        if _BLOB_NAME_RE.fullmatch(f"{sha256}.blob") is None:
            raise ArtifactStoreError("blob digest is not a safe artifact filename")
        return self.blobs_dir / f"{sha256}.blob"

    def _validate_record_blobs(
        self, chain: tuple[ArtifactRecord, ...]
    ) -> dict[str, _ArtifactFileStamp]:
        validated: dict[BlobReference, bytes] = {}
        file_stamps: dict[str, _ArtifactFileStamp] = {}

        def content(reference: BlobReference) -> bytes:
            persisted = validated.get(reference)
            if persisted is None:
                path = self._blob_path(reference.sha256)
                try:
                    before = self._artifact_file_stamp(path)
                except (OSError, ArtifactStoreError):
                    # Preserve read_blob()'s stable fail-closed diagnostic for
                    # missing, linked, or otherwise invalid blob targets.
                    self.read_blob(reference)
                    raise  # pragma: no cover - read_blob always rejects here
                persisted = self.read_blob(reference)
                after = self._artifact_file_stamp(path)
                if before != after:
                    raise ArtifactCorruptionError(
                        f"artifact blob changed during validation: {path.name!r}"
                    )
                file_stamps[path.name] = after
                validated[reference] = persisted
            return persisted

        for record in chain:
            for reference in _payload_blob_references(record.payload):
                content(reference)
            payload = record.payload
            if isinstance(payload, ValidationContentPayload):
                try:
                    captures = tuple(
                        ValidationCapture(
                            _command_display(item.command),
                            item.digest_outcome,
                            item.exit_code,
                            content(item.raw_stdout).decode("utf-8"),
                            content(item.raw_stderr).decode("utf-8"),
                            content(item.compact_output).decode("utf-8"),
                        )
                        for item in payload.outputs
                    )
                    actual = validation_output_digest(captures, payload.digest_format)
                except (UnicodeDecodeError, ValueError) as exc:
                    raise ArtifactCorruptionError(
                        "validation content cannot reproduce its digest"
                    ) from exc
                if actual != payload.output_digest:
                    raise ArtifactCorruptionError(
                        "validation content digest differs from its raw streams"
                    )
            elif isinstance(payload, ReviewPacketPayload):
                from review_packets import ReviewPacket, ReviewPacketError

                try:
                    packet = ReviewPacket.restore(
                        content(payload.blob), payload.blob.sha256
                    )
                except ReviewPacketError as exc:
                    raise ArtifactCorruptionError(
                        "review packet blob is not canonical"
                    ) from exc
                if (
                    packet.fingerprint != payload.fingerprint
                    or packet.purpose != payload.purpose
                    or packet.manifest.paths != payload.manifest
                    or packet.manifest.diff_coverage_digest
                    != payload.diff_coverage_sha256
                ):
                    raise ArtifactCorruptionError(
                        "review packet blob differs from its record metadata"
                    )
        return file_stamps

    def _confined(self, path: Path) -> Path:
        try:
            resolved = Path(path).resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise ArtifactStoreError(
                f"artifact path could not be resolved safely: {path}"
            ) from exc
        if not resolved.is_relative_to(self.repository_root):
            raise ArtifactStoreError(f"artifact path escapes repository root: {path}")
        return resolved

    def _refresh_cache_with_context(
        self,
        chain: tuple[ArtifactRecord, ...],
        expected_cache_chain: tuple[ArtifactRecord, ...] | None,
    ) -> None:
        self._refresh_head_cache(
            chain,
            expected_cache_chain=expected_cache_chain,
        )

    def _refresh_head_cache(
        self,
        chain: tuple[ArtifactRecord, ...],
        *,
        expected_cache_chain: tuple[ArtifactRecord, ...] | None = None,
    ) -> None:
        head_path = self.head_path
        expected = _head_cache_document(chain)
        current = self._read_head_cache()
        if not chain and current is None:
            return
        if current != expected:
            expected_prior = (
                _head_cache_document(expected_cache_chain)
                if expected_cache_chain is not None
                else None
            )
            own_expected_progress = current == expected_prior
            if current is not None and not own_expected_progress:
                logger.warning(
                    "Discarding stale artifact head cache for run %s", self.run_id
                )
            self.run_dir.mkdir(parents=True, exist_ok=True)
            _atomic_write(head_path, canonical_json(expected) + b"\n")


def _head_cache_document(chain: tuple[ArtifactRecord, ...]) -> dict[str, object]:
    chain_sha256 = _EMPTY_CHAIN_SHA256
    for record in chain:
        chain_sha256 = _extend_chain_sha256(chain_sha256, record.record_id)
    return {
        "head_record_id": chain[-1].record_id if chain else None,
        "record_count": len(chain),
        "chain_sha256": chain_sha256,
    }


def _extend_chain_sha256(prior_sha256: str, record_id: str) -> str:
    return hashlib.sha256(
        canonical_json(
            {
                "prior_chain_sha256": prior_sha256,
                "record_id": record_id,
            }
        )
    ).hexdigest()


def _build_append_index(
    run_id: str,
    chain: tuple[ArtifactRecord, ...],
    records_dir_stamp: _RecordsDirectoryStamp,
) -> _AppendIndex:
    by_idempotency_key: dict[str, tuple[str, ArtifactRecord]] = {}
    record_ids: set[str] = set()
    revision_keys: set[tuple[RecordType, str, int]] = set()
    max_revisions: dict[tuple[RecordType, str], int] = {}
    chain_sha256 = _EMPTY_CHAIN_SHA256
    for record in chain:
        by_idempotency_key[record.idempotency_key] = (
            _semantic_digest(record),
            record,
        )
        record_ids.add(record.record_id)
        revision_keys.add((record.record_type, record.logical_id, record.revision))
        identity = (record.record_type, record.logical_id)
        max_revisions[identity] = max(
            record.revision,
            max_revisions.get(identity, 0),
        )
        chain_sha256 = _extend_chain_sha256(chain_sha256, record.record_id)
    return _AppendIndex(
        run_id=run_id,
        chain=chain,
        by_idempotency_key=by_idempotency_key,
        record_ids=record_ids,
        revision_keys=revision_keys,
        max_revisions=max_revisions,
        head_record_id=chain[-1].record_id if chain else None,
        record_count=len(chain),
        chain_sha256=chain_sha256,
        records_dir_stamp=records_dir_stamp,
    )


def _artifact_file_stamp_from_stat(metadata: os.stat_result) -> _ArtifactFileStamp:
    return _ArtifactFileStamp(
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_regular_file_without_following_symlinks(
    path: Path,
) -> tuple[bytes, _ArtifactFileStamp]:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ArtifactStoreError(
                f"artifact path is not a regular file: {path.name!r}"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read()
        return content, _artifact_file_stamp_from_stat(before)
    finally:
        os.close(descriptor)


def _read_record(path: Path) -> tuple[ArtifactRecord, _ArtifactFileStamp]:
    try:
        content, opened_stamp = _read_regular_file_without_following_symlinks(path)
        envelope = json.loads(content.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ArtifactStoreError) as exc:
        raise ArtifactCorruptionError(f"could not parse record {path.name!r}: {exc}") from exc
    if not isinstance(envelope, dict) or set(envelope) != {"content_sha256", "record"}:
        raise ArtifactCorruptionError(f"record envelope is invalid: {path.name!r}")
    digest = envelope["content_sha256"]
    document = envelope["record"]
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ArtifactCorruptionError(f"record digest is invalid: {path.name!r}")
    try:
        actual = hashlib.sha256(canonical_json(document)).hexdigest()
    except (TypeError, ValueError) as exc:
        raise ArtifactCorruptionError(f"record JSON is not canonicalizable: {path.name!r}") from exc
    if actual != digest:
        raise ArtifactCorruptionError(f"record digest mismatch: {path.name!r}")
    if not isinstance(document, dict):
        raise ArtifactCorruptionError(f"record payload is not an object: {path.name!r}")
    try:
        record = ArtifactRecord.from_dict(document)
    except (ArtifactValidationError, KeyError, TypeError, ValueError) as exc:
        raise ArtifactCorruptionError(f"record validation failed for {path.name!r}: {exc}") from exc
    return record, opened_stamp


def _order_chain(records: dict[str, ArtifactRecord]) -> tuple[ArtifactRecord, ...]:
    if not records:
        return ()
    for record in records.values():
        for predecessor in record.predecessor_ids:
            if predecessor not in records:
                raise ArtifactCorruptionError(
                    f"record {record.record_id!r} has missing predecessor {predecessor!r}"
                )
    genesis = [record for record in records.values() if not record.predecessor_ids]
    if len(genesis) != 1:
        if not genesis:
            for start in records:
                cursor = start
                walked: set[str] = set()
                while cursor not in walked:
                    walked.add(cursor)
                    predecessors = records[cursor].predecessor_ids
                    if not predecessors or predecessors[0] not in records:
                        break
                    cursor = predecessors[0]
                if cursor in walked:
                    raise ArtifactCorruptionError(
                        f"artifact predecessor cycle at {cursor!r}"
                    )
        raise ArtifactCorruptionError(
            f"artifact chain requires exactly one genesis record, found {len(genesis)}"
        )
    successor: dict[str, ArtifactRecord] = {}
    for record in records.values():
        if record.predecessor_ids:
            primary = record.predecessor_ids[0]
            if primary in successor:
                raise ArtifactCorruptionError(
                    f"artifact chain forks after predecessor {primary!r}"
                )
            successor[primary] = record

    ordered: list[ArtifactRecord] = []
    seen: set[str] = set()
    current: ArtifactRecord | None = genesis[0]
    while current is not None:
        if current.record_id in seen:
            raise ArtifactCorruptionError(
                f"artifact predecessor cycle at {current.record_id!r}"
            )
        seen.add(current.record_id)
        ordered.append(current)
        current = successor.get(current.record_id)
    if len(seen) != len(records):
        remaining = next(record_id for record_id in records if record_id not in seen)
        cursor = remaining
        walked: set[str] = set()
        while cursor not in walked:
            walked.add(cursor)
            predecessors = records[cursor].predecessor_ids
            if not predecessors:
                break
            cursor = predecessors[0]
        if cursor in walked:
            raise ArtifactCorruptionError(f"artifact predecessor cycle at {cursor!r}")
        raise ArtifactCorruptionError(
            f"artifact chain is disconnected at record {remaining!r}"
        )
    prior: set[str] = set()
    for record in ordered:
        if any(reference not in prior for reference in record.predecessor_ids):
            raise ArtifactCorruptionError(
                f"record {record.record_id!r} has a non-prior predecessor"
            )
        prior.add(record.record_id)
    return tuple(ordered)


def _semantic_digest(record: ArtifactRecord) -> str:
    statement = record.to_dict()
    # created_at is deliberately volatile across retries. The deterministic
    # record_id already binds run/type/logical identity/revision.
    statement.pop("created_at", None)
    return hashlib.sha256(canonical_json(statement)).hexdigest()


def _validate_store_invariants(
    record: ArtifactRecord, *, persisted: bool = False
) -> None:
    payload = record.payload
    if (
        isinstance(payload, WorkflowCompletionPayload)
        and payload.outcome in {"failed", "stopped"}
        and payload.final_binding_id is not None
    ):
        error_type = ArtifactCorruptionError if persisted else ArtifactConflictError
        raise error_type(
            f"{payload.outcome} workflow completion cannot carry final_binding_id"
        )


def _payload_blob_references(payload: object) -> tuple[BlobReference, ...]:
    if isinstance(payload, ValidationContentPayload):
        return tuple(
            reference
            for output in payload.outputs
            for reference in (
                output.raw_stdout,
                output.raw_stderr,
                output.compact_output,
            )
        )
    if isinstance(payload, (ProviderContentPayload, ReviewPacketPayload)):
        return (payload.blob,)
    return ()


def _command_display(command: object) -> str:
    if getattr(command, "mode", None) == "legacy_shell":
        return command.argv[0]  # type: ignore[attr-defined]
    return shlex.join(command.argv)  # type: ignore[attr-defined]


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            temporary = Path(stream.name)
        os.replace(temporary, path)
        temporary = None
        try:
            descriptor = os.open(path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
