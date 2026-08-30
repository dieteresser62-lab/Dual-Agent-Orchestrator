"""Root-bound, append-only persistence for structured artifact records."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import tempfile

from artifact_models import (
    ArtifactRecord,
    ArtifactValidationError,
    RecordType,
    WorkflowCompletionPayload,
    canonical_json,
)
from path_policy import PathPolicyError, resolve_path_within_roots


_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_RECORD_NAME_RE = re.compile(r"^(ar1-[0-9a-f]{64})\.json$")
logger = logging.getLogger(__name__)
_INVALID_CACHE = object()
_EMPTY_CHAIN_SHA256 = hashlib.sha256(b"artifact-chain-v1").hexdigest()


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


@dataclass(slots=True)
class _AppendIndex:
    """Process-local derivative of one fully validated record prefix."""

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

    def __init__(self, repository_root: Path, run_id: str) -> None:
        if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
            raise ArtifactStoreError("run_id is not a safe artifact path component")
        root = Path(repository_root).resolve()
        if not root.is_dir():
            raise ArtifactStoreError("repository_root must be an existing directory")
        self.repository_root = root
        self.run_id = run_id
        self.run_dir = self._confined(root / ".orchestrator" / "artifacts" / run_id)
        self.records_dir = self._confined(self.run_dir / "records")
        self.head_path = self._confined(self.run_dir / "head.json")
        self._append_index: _AppendIndex | None = None

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
            published = _read_record(target)
            if published != record:
                raise ArtifactCorruptionError(
                    "published record differs from the append candidate"
                )
            _validate_store_invariants(published, persisted=True)

            # A second writer which completed its own cache update after our
            # pre-write guard makes the prior proof stale.  Re-scan instead of
            # letting either cache choose the winner.
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
        """Load and fully validate the authoritative chain in append order."""
        return self._load_chain()

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
                (), self._records_directory_stamp()
            )
            return ()
        self._confined(self.records_dir)
        records: dict[str, ArtifactRecord] = {}
        semantics_by_key: dict[str, str] = {}
        revisions: set[tuple[RecordType, str, int]] = set()
        for path in sorted(self.records_dir.iterdir(), key=lambda item: item.name):
            if path.name.startswith(".") and path.name.endswith(".tmp"):
                continue
            match = _RECORD_NAME_RE.fullmatch(path.name)
            if match is None or not path.is_file() or path.is_symlink():
                raise ArtifactCorruptionError(
                    f"unexpected entry in artifact records directory: {path.name!r}"
                )
            confined = self._confined(path)
            record = _read_record(confined)
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
        self._refresh_cache_with_context(ordered, expected_cache_chain)
        self._append_index = _build_append_index(
            ordered, self._records_directory_stamp()
        )
        return ordered

    def _ensure_append_index(self) -> _AppendIndex:
        index = self._append_index
        if index is not None and (
            index.records_dir_stamp != self._records_directory_stamp()
            or not self._head_cache_matches(index)
        ):
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

    def _records_directory_stamp(self) -> _RecordsDirectoryStamp:
        try:
            stat = self._confined(self.records_dir).stat()
        except FileNotFoundError:
            return _RecordsDirectoryStamp(False, None, None)
        return _RecordsDirectoryStamp(True, stat.st_mtime_ns, stat.st_ctime_ns)

    def _read_head_cache(self) -> object:
        try:
            return json.loads(self._confined(self.head_path).read_text(encoding="utf-8"))
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
        return self._confined(self.records_dir / f"{record_id}.json")

    def _confined(self, path: Path) -> Path:
        try:
            return resolve_path_within_roots(path, (self.repository_root,))
        except PathPolicyError as exc:
            raise ArtifactStoreError(f"artifact path escapes repository root: {path}") from exc

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
        head_path = self._confined(self.head_path)
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
        by_idempotency_key=by_idempotency_key,
        record_ids=record_ids,
        revision_keys=revision_keys,
        max_revisions=max_revisions,
        head_record_id=chain[-1].record_id if chain else None,
        record_count=len(chain),
        chain_sha256=chain_sha256,
        records_dir_stamp=records_dir_stamp,
    )


def _read_record(path: Path) -> ArtifactRecord:
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
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
        return ArtifactRecord.from_dict(document)
    except (ArtifactValidationError, KeyError, TypeError, ValueError) as exc:
        raise ArtifactCorruptionError(f"record validation failed for {path.name!r}: {exc}") from exc


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
