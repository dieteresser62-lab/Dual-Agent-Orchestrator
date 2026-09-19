"""Read-only verifier for ``structured-v2-schema-2-state-v3-v1`` chains.

This module is deliberately outside every workflow path.  It has one
capability: validate an already persisted legacy chain and return its canonical
state projection.  The reducer sources are read from an immutable Git object,
hash-checked against ``legacy_verifier_v1_manifest.json``, and executed in an
isolated in-memory module namespace.  No cache, checkpoint, projection, or
other file is ever written.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import importlib
import importlib.abc
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import re
import stat
import sys
import tarfile
from types import ModuleType
from typing import Any, Mapping, Sequence


# Importing the verifier must not create a bytecode cache either.
sys.dont_write_bytecode = True

LEGACY_REDUCER_VERSION = "structured-v2-schema-2-state-v3-v1"
LEGACY_SOURCE_COMMIT = "4ca7482bf1d60726e1750c1d6d7d62960403054b"
LEGACY_ARTIFACT_SHA256 = (
    "cfc087e23b3c5ce17171c72f51b254baa21e366c510e9e648a5f5633132d593e"
)
_MANIFEST_PATH = Path(__file__).with_name("legacy_verifier_v1_manifest.json")
_BUNDLE_PATH = Path(__file__).with_name("legacy-verifier-v1.bundle.json")
_SNAPSHOT_PACKAGE = "_dual_agent_legacy_reducer_v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RECORD_FILE_RE = re.compile(r"^(ar1-[0-9a-f]{64})\.json$")
_BATCH_FILE_RE = re.compile(r"^(arb1-[0-9a-f]{64})\.json$")
_WRITE_FLAGS = (
    os.O_WRONLY
    | os.O_RDWR
    | os.O_APPEND
    | os.O_CREAT
    | os.O_TRUNC
    | getattr(os, "O_TMPFILE", 0)
)


# This non-production reader must not extend the Watch boundary's closed error
# inventory.  Stable message prefixes distinguish its failures; these aliases
# deliberately remain the standard-library ValueError type.
LegacyVerifierError = ValueError
LegacyProtocolError = ValueError
LegacyCorruptionError = ValueError
LegacyCapabilityError = ValueError


@dataclass(frozen=True, slots=True)
class LegacyVerificationResult:
    run_id: str
    reducer_version: str
    record_count: int
    head_record_id: str
    semantic_sha256: str
    projection_sha256: str
    canonical_projection: bytes

    def summary_document(self) -> dict[str, object]:
        return {
            "head_record_id": self.head_record_id,
            "projection_sha256": self.projection_sha256,
            "record_count": self.record_count,
            "reducer_version": self.reducer_version,
            "run_id": self.run_id,
            "semantic_sha256": self.semantic_sha256,
        }


@dataclass(frozen=True, slots=True)
class LegacyFixtureDigests:
    projection_sha256: str
    finding_transitions_sha256: str


@dataclass(frozen=True, slots=True)
class _FileStamp:
    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True, slots=True)
class _RawRecord:
    document: dict[str, Any]
    location: str


@dataclass(frozen=True, slots=True)
class _LegacyRuntime:
    models: ModuleType
    replay: ModuleType
    workflow_state: ModuleType


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _stamp(metadata: os.stat_result) -> _FileStamp:
    return _FileStamp(
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _require_directory(path: Path, label: str) -> _FileStamp:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise LegacyCorruptionError(f"{label} is unavailable at {path}: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise LegacyCorruptionError(f"{label} is not a real directory: {path}")
    return _stamp(metadata)


def _read_regular_file(path: Path, label: str) -> bytes:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    if flags & _WRITE_FLAGS:  # pragma: no cover - platform constant guard
        raise LegacyCapabilityError("legacy verifier attempted a write-capable open")
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise LegacyCorruptionError(f"could not open {label} {path}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise LegacyCorruptionError(f"{label} is not a regular file: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if _stamp(before) != _stamp(after):
            raise LegacyCorruptionError(f"{label} changed while it was read: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _parse_json(content: bytes, location: str) -> object:
    try:
        return json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise LegacyCorruptionError(f"invalid JSON at {location}: {exc}") from exc


def _check_envelope_digest(
    digest: object,
    value: object,
    location: str,
) -> None:
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        raise LegacyCorruptionError(f"invalid content digest at {location}")
    try:
        actual = hashlib.sha256(_canonical_json(value)).hexdigest()
    except (TypeError, ValueError) as exc:
        raise LegacyCorruptionError(
            f"non-canonical JSON value at {location}: {exc}"
        ) from exc
    if actual != digest:
        raise LegacyCorruptionError(f"content digest mismatch at {location}")


def _record_documents(run_directory: Path) -> tuple[_RawRecord, ...]:
    run_directory = Path(os.path.abspath(os.fspath(run_directory)))
    _require_directory(run_directory, "legacy run directory")
    records_directory = run_directory / "records"
    initial_stamp = _require_directory(records_directory, "legacy records directory")
    raw_records: list[_RawRecord] = []
    try:
        entries = sorted(os.scandir(records_directory), key=lambda item: item.name)
    except OSError as exc:
        raise LegacyCorruptionError(
            f"could not list legacy records directory {records_directory}: {exc}"
        ) from exc
    for entry in entries:
        if entry.name.startswith(".") and entry.name.endswith(".tmp"):
            continue
        record_match = _RECORD_FILE_RE.fullmatch(entry.name)
        batch_match = _BATCH_FILE_RE.fullmatch(entry.name)
        if record_match is None and batch_match is None:
            raise LegacyCorruptionError(
                f"unexpected records entry at {records_directory / entry.name}"
            )
        path = records_directory / entry.name
        envelope = _parse_json(_read_regular_file(path, "record file"), str(path))
        if not isinstance(envelope, dict):
            raise LegacyCorruptionError(f"record envelope is not an object at {path}")
        if record_match is not None:
            if set(envelope) != {"content_sha256", "record"}:
                raise LegacyCorruptionError(f"record envelope is invalid at {path}")
            document = envelope["record"]
            _check_envelope_digest(envelope["content_sha256"], document, str(path))
            if not isinstance(document, dict):
                raise LegacyCorruptionError(f"record is not an object at {path}")
            if document.get("record_id") != record_match.group(1):
                raise LegacyCorruptionError(f"record filename mismatch at {path}")
            raw_records.append(_RawRecord(document, str(path)))
            continue

        if set(envelope) != {"content_sha256", "records"}:
            raise LegacyCorruptionError(f"record batch envelope is invalid at {path}")
        documents = envelope["records"]
        _check_envelope_digest(envelope["content_sha256"], documents, str(path))
        if not isinstance(documents, list) or len(documents) < 2:
            raise LegacyCorruptionError(f"record batch is invalid at {path}")
        record_ids = []
        for index, document in enumerate(documents):
            location = f"{path}:records[{index}]"
            if not isinstance(document, dict):
                raise LegacyCorruptionError(f"record is not an object at {location}")
            record_ids.append(document.get("record_id"))
            raw_records.append(_RawRecord(document, location))
        batch_id = "arb1-" + hashlib.sha256(_canonical_json(record_ids)).hexdigest()
        if batch_id != batch_match.group(1):
            raise LegacyCorruptionError(f"record batch filename mismatch at {path}")

    if _stamp(records_directory.lstat()) != initial_stamp:
        raise LegacyCorruptionError(
            f"legacy records directory changed during verification: {records_directory}"
        )
    if not raw_records:
        raise LegacyCorruptionError(
            f"legacy record chain is empty at {records_directory}"
        )
    return tuple(raw_records)


def _require_legacy_profile(raw_records: Sequence[_RawRecord]) -> str:
    profiles = tuple(
        item
        for item in raw_records
        if item.document.get("record_type") == "run_profile"
    )
    if len(profiles) != 1:
        raise LegacyProtocolError(
            "UNSUPPORTED-PROTOCOL: legacy chain requires exactly one raw run_profile; "
            f"found {len(profiles)}"
        )
    profile = profiles[0]
    payload = profile.document.get("payload")
    reducer_version = payload.get("reducer_version") if isinstance(payload, dict) else None
    if reducer_version != LEGACY_REDUCER_VERSION:
        record_id = profile.document.get("record_id", "unknown record")
        raise LegacyProtocolError(
            "UNSUPPORTED-PROTOCOL at "
            f"{profile.location} ({record_id}): expected reducer "
            f"{LEGACY_REDUCER_VERSION!r}, found {reducer_version!r}; chain was not interpreted"
        )
    run_id = profile.document.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise LegacyCorruptionError(f"run_profile has no run_id at {profile.location}")
    return run_id


def _load_manifest() -> dict[str, Any]:
    document = _parse_json(
        _read_regular_file(_MANIFEST_PATH, "legacy reducer manifest"),
        str(_MANIFEST_PATH),
    )
    if not isinstance(document, dict):
        raise LegacyVerifierError("legacy reducer manifest is not an object")
    if document.get("schema_version") != "legacy-verifier-artifact-v1":
        raise LegacyVerifierError("legacy reducer manifest version is unsupported")
    if document.get("source_commit") != LEGACY_SOURCE_COMMIT:
        raise LegacyVerifierError("legacy reducer manifest source commit changed")
    if document.get("reducer_version") != LEGACY_REDUCER_VERSION:
        raise LegacyVerifierError("legacy reducer manifest reducer changed")
    runtime = document.get("runtime")
    if not isinstance(runtime, dict) or runtime != {
        "implementation": "CPython",
        "maximum_exclusive": "3.15",
        "minimum": "3.11",
        "stdlib_only": True,
    }:
        raise LegacyVerifierError("legacy reducer runtime contract changed")
    if platform.python_implementation() != "CPython" or not (
        (3, 11) <= sys.version_info[:2] < (3, 15)
    ):
        raise LegacyVerifierError(
            "legacy reducer requires CPython >=3.11 and <3.15"
        )
    sources = document.get("sources")
    if not isinstance(sources, list) or not sources:
        raise LegacyVerifierError("legacy reducer manifest has no sources")
    if any(
        not isinstance(entry, dict)
        or set(entry) != {"path", "sha256"}
        or not isinstance(entry.get("path"), str)
        or not isinstance(entry.get("sha256"), str)
        or _SHA256_RE.fullmatch(entry["sha256"]) is None
        for entry in sources
    ):
        raise LegacyVerifierError("legacy reducer source entry is invalid")
    if len({entry["path"] for entry in sources}) != len(sources):
        raise LegacyVerifierError("legacy reducer source path is duplicated")
    bundle_digest = document.get("bundle_sha256")
    if not isinstance(bundle_digest, str) or _SHA256_RE.fullmatch(bundle_digest) is None:
        raise LegacyVerifierError("legacy reducer bundle binding is invalid")
    expected_artifact = hashlib.sha256(_canonical_json(sources)).hexdigest()
    if (
        expected_artifact != LEGACY_ARTIFACT_SHA256
        or document.get("artifact_sha256") != LEGACY_ARTIFACT_SHA256
    ):
        raise LegacyVerifierError("legacy reducer artifact digest is invalid")
    return document


def _read_archived_sources(manifest: Mapping[str, Any]) -> dict[str, bytes]:
    bundle_envelope = _parse_json(
        _read_regular_file(_BUNDLE_PATH, "legacy reducer bundle"),
        str(_BUNDLE_PATH),
    )
    if not isinstance(bundle_envelope, dict) or set(bundle_envelope) != {
        "encoding",
        "payload",
        "schema_version",
    }:
        raise LegacyVerifierError("legacy reducer bundle envelope is invalid")
    if (
        bundle_envelope["schema_version"] != "binary-fixture-envelope-v1"
        or bundle_envelope["encoding"] != "gzip+hex"
        or not isinstance(bundle_envelope["payload"], str)
    ):
        raise LegacyVerifierError("legacy reducer bundle encoding is invalid")
    try:
        bundle = bytes.fromhex(bundle_envelope["payload"])
    except ValueError as exc:
        raise LegacyVerifierError("legacy reducer bundle payload is invalid") from exc
    if hashlib.sha256(bundle).hexdigest() != manifest.get("bundle_sha256"):
        raise LegacyVerifierError("legacy reducer bundle digest is invalid")
    expected_entries = {
        entry["path"]: entry["sha256"]
        for entry in manifest["sources"]
        if isinstance(entry, dict)
    }
    sources: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=BytesIO(bundle), mode="r:gz") as archive:
            members = archive.getmembers()
            if {member.name for member in members} != set(expected_entries):
                raise LegacyVerifierError("legacy reducer bundle membership is invalid")
            for member in members:
                if not member.isfile() or member.issym() or member.islnk():
                    raise LegacyVerifierError(
                        f"legacy reducer bundle member is not a regular file: {member.name}"
                    )
                stream = archive.extractfile(member)
                if stream is None:  # pragma: no cover - guarded by isfile
                    raise LegacyVerifierError(
                        f"legacy reducer bundle member is unreadable: {member.name}"
                    )
                content = stream.read()
                if hashlib.sha256(content).hexdigest() != expected_entries[member.name]:
                    raise LegacyVerifierError(
                        f"archived reducer source digest mismatch for {member.name}"
                    )
                sources[member.name] = content
    except (tarfile.TarError, OSError) as exc:
        raise LegacyVerifierError(f"legacy reducer bundle is invalid: {exc}") from exc
    return sources


class _ArchivedImportRewriter(ast.NodeTransformer):
    def __init__(self, local_modules: frozenset[str]) -> None:
        self._local_modules = local_modules

    def visit_Import(self, node: ast.Import) -> ast.AST:
        aliases = []
        for alias in node.names:
            if alias.name in self._local_modules:
                aliases.append(
                    ast.alias(
                        name=f"{_SNAPSHOT_PACKAGE}.{alias.name}",
                        asname=alias.asname or alias.name,
                    )
                )
            else:
                aliases.append(alias)
        return ast.copy_location(ast.Import(names=aliases), node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.AST:
        if node.level == 0 and node.module in self._local_modules:
            return ast.copy_location(
                ast.ImportFrom(
                    module=f"{_SNAPSHOT_PACKAGE}.{node.module}",
                    names=node.names,
                    level=0,
                ),
                node,
            )
        return node


class _ArchivedSourceFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, module_sources: Mapping[str, bytes]) -> None:
        self._module_sources = dict(module_sources)
        self._local_modules = frozenset(module_sources)

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        prefix = _SNAPSHOT_PACKAGE + "."
        if fullname.startswith(prefix) and fullname[len(prefix):] in self._module_sources:
            return importlib.util.spec_from_loader(fullname, self)
        return None

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        name = module.__name__.rsplit(".", 1)[-1]
        source = self._module_sources[name]
        filename = f"git:{LEGACY_SOURCE_COMMIT}:src/{name}.py"
        try:
            tree = ast.parse(source, filename=filename)
        except (SyntaxError, ValueError) as exc:  # pragma: no cover - hash-bound source
            raise LegacyVerifierError(f"archived reducer source is invalid: {filename}") from exc
        tree = _ArchivedImportRewriter(self._local_modules).visit(tree)
        ast.fix_missing_locations(tree)
        module.__file__ = filename
        exec(compile(tree, filename, "exec"), module.__dict__)


@lru_cache(maxsize=1)
def _load_runtime() -> _LegacyRuntime:
    manifest = _load_manifest()
    sources = _read_archived_sources(manifest)
    schema_path = "schemas/orchestrator-artifact-v2.schema.json"
    try:
        schema = json.loads(sources.pop(schema_path).decode("utf-8"))
    except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise LegacyVerifierError("archived reducer schema is invalid") from exc
    module_sources = {
        path.removeprefix("src/").removesuffix(".py"): content
        for path, content in sources.items()
    }
    package = ModuleType(_SNAPSHOT_PACKAGE)
    package.__path__ = []  # type: ignore[attr-defined]
    package.__package__ = _SNAPSHOT_PACKAGE
    sys.modules[_SNAPSHOT_PACKAGE] = package
    finder = _ArchivedSourceFinder(module_sources)
    sys.meta_path.insert(0, finder)
    try:
        models = importlib.import_module(f"{_SNAPSHOT_PACKAGE}.artifact_models")
        schema_validation = importlib.import_module(
            f"{_SNAPSHOT_PACKAGE}.schema_validation"
        )
        schema_validation.check_schema(schema)
        models._validated_schema = lru_cache(maxsize=1)(lambda: schema)
        replay = importlib.import_module(f"{_SNAPSHOT_PACKAGE}.artifact_replay")
        workflow_state = importlib.import_module(
            f"{_SNAPSHOT_PACKAGE}.workflow_state"
        )
    except Exception:
        sys.meta_path.remove(finder)
        raise
    # Keep the narrowly prefixed in-memory finder installed: archived replay
    # deliberately imports finding_reducer lazily at several validation and
    # projection boundaries.  The finder cannot resolve any production name.
    return _LegacyRuntime(models, replay, workflow_state)


def _typed_records(
    raw_records: Sequence[_RawRecord],
    runtime: _LegacyRuntime,
) -> tuple[tuple[object, ...], dict[str, str]]:
    records: list[object] = []
    locations: dict[str, str] = {}
    for raw in raw_records:
        try:
            record = runtime.models.ArtifactRecord.from_dict(raw.document)
        except Exception as exc:
            raise LegacyCorruptionError(
                f"record validation failed at {raw.location}: {exc}"
            ) from exc
        record_id = record.record_id
        if record_id in locations:
            raise LegacyCorruptionError(
                f"duplicate record {record_id} at {raw.location}; first at {locations[record_id]}"
            )
        locations[record_id] = raw.location
        records.append(record)
    return tuple(records), locations


def _order_chain(
    records: Sequence[object],
    locations: Mapping[str, str],
) -> tuple[object, ...]:
    by_id = {record.record_id: record for record in records}
    for record in records:
        for predecessor in record.predecessor_ids:
            if predecessor not in by_id:
                raise LegacyCorruptionError(
                    f"missing predecessor {predecessor} at {locations[record.record_id]}"
                )
    genesis = tuple(record for record in records if not record.predecessor_ids)
    if len(genesis) != 1:
        raise LegacyCorruptionError(
            f"legacy chain requires exactly one genesis record; found {len(genesis)}"
        )
    successor: dict[str, object] = {}
    for record in records:
        if not record.predecessor_ids:
            continue
        primary = record.predecessor_ids[0]
        if primary in successor:
            raise LegacyCorruptionError(
                f"legacy chain forks after {primary} at {locations[record.record_id]}"
            )
        successor[primary] = record
    ordered: list[object] = []
    seen: set[str] = set()
    current: object | None = genesis[0]
    while current is not None:
        if current.record_id in seen:
            raise LegacyCorruptionError(
                f"legacy chain cycle at {locations[current.record_id]}"
            )
        seen.add(current.record_id)
        ordered.append(current)
        current = successor.get(current.record_id)
    if len(seen) != len(records):
        remaining = next(record_id for record_id in by_id if record_id not in seen)
        raise LegacyCorruptionError(
            f"legacy chain is disconnected at {locations[remaining]} ({remaining})"
        )
    prior: set[str] = set()
    for record in ordered:
        if any(item not in prior for item in record.predecessor_ids):
            raise LegacyCorruptionError(
                f"non-prior predecessor at {locations[record.record_id]}"
            )
        prior.add(record.record_id)
    return tuple(ordered)


def _blob_references(record: object, runtime: _LegacyRuntime) -> tuple[object, ...]:
    payload = record.payload
    models = runtime.models
    if isinstance(payload, models.ValidationContentPayload):
        return tuple(
            reference
            for output in payload.outputs
            for reference in (
                output.raw_stdout,
                output.raw_stderr,
                output.compact_output,
            )
        )
    if isinstance(payload, (models.ProviderContentPayload, models.ReviewPacketPayload)):
        return (payload.blob,)
    return ()


def _validate_blobs(
    run_directory: Path,
    records: Sequence[object],
    runtime: _LegacyRuntime,
) -> None:
    references: dict[str, object] = {}
    for record in records:
        for reference in _blob_references(record, runtime):
            prior = references.get(reference.sha256)
            if prior is not None and prior != reference:
                raise LegacyCorruptionError(
                    f"conflicting blob lengths for {reference.sha256}"
                )
            references[reference.sha256] = reference
    if not references:
        return
    blobs_directory = run_directory / "blobs"
    initial_stamp = _require_directory(blobs_directory, "legacy blobs directory")
    for digest, reference in sorted(references.items()):
        path = blobs_directory / f"{digest}.blob"
        content = _read_regular_file(path, "artifact blob")
        if len(content) != reference.bytes:
            raise LegacyCorruptionError(f"blob length mismatch at {path}")
        if hashlib.sha256(content).hexdigest() != digest:
            raise LegacyCorruptionError(f"blob digest mismatch at {path}")
    if _stamp(blobs_directory.lstat()) != initial_stamp:
        raise LegacyCorruptionError(
            f"legacy blobs directory changed during verification: {blobs_directory}"
        )


def verify_legacy_chain(run_directory: Path | str) -> LegacyVerificationResult:
    """Validate and project one legacy run without mutating any filesystem."""
    run_directory = Path(os.path.abspath(os.fspath(run_directory)))
    raw_records = _record_documents(run_directory)
    raw_run_id = _require_legacy_profile(raw_records)
    runtime = _load_runtime()
    typed_records, locations = _typed_records(raw_records, runtime)
    ordered = _order_chain(typed_records, locations)
    if any(record.run_id != raw_run_id for record in ordered):
        offender = next(record for record in ordered if record.run_id != raw_run_id)
        raise LegacyCorruptionError(
            f"record run_id mismatch at {locations[offender.record_id]}"
        )
    _validate_blobs(run_directory, ordered, runtime)
    try:
        replay = runtime.replay.replay_artifacts(ordered, raw_run_id)
        projection = runtime.replay.project_workflow_state(replay).canonical_document
    except Exception as exc:
        record_id = getattr(exc, "record_id", None)
        location = locations.get(record_id, "semantic chain")
        raise LegacyCorruptionError(
            f"legacy replay failed at {location}: {exc}"
        ) from exc
    profile = replay.run_profile
    if profile is None or profile.reducer_version != LEGACY_REDUCER_VERSION:
        raise LegacyProtocolError(
            "UNSUPPORTED-PROTOCOL: typed replay did not preserve the legacy reducer binding"
        )
    return LegacyVerificationResult(
        run_id=raw_run_id,
        reducer_version=profile.reducer_version,
        record_count=len(ordered),
        head_record_id=ordered[-1].record_id,
        semantic_sha256=replay.semantic_digest,
        projection_sha256=hashlib.sha256(projection).hexdigest(),
        canonical_projection=projection,
    )


def run_legacy_operation(
    operation: str,
    run_directory: Path | str,
) -> LegacyVerificationResult:
    """Expose the capability boundary explicitly; only verification exists."""
    if operation != "verify":
        raise LegacyCapabilityError(
            f"legacy operation {operation!r} is forbidden; resume, dispatch, and providers do not exist"
        )
    return verify_legacy_chain(run_directory)


def verify_run_8_fixtures(
    handoff_fixture: Path | str,
    projection_fixture: Path | str,
) -> LegacyFixtureDigests:
    """Recompute both published Run-8 digests from repository fixtures."""
    runtime = _load_runtime()
    fixture_path = Path(os.path.abspath(os.fspath(handoff_fixture)))
    fixture = _parse_json(
        _read_regular_file(fixture_path, "Run-8 handoff fixture"),
        str(fixture_path),
    )
    if not isinstance(fixture, dict) or not isinstance(fixture.get("transitions"), list):
        raise LegacyCorruptionError("Run-8 handoff fixture is invalid")
    try:
        transitions = tuple(
            runtime.models.ImportedFindingTransition(
                item["record_id"],
                runtime.models._payload_from_dict(
                    runtime.models.RecordType.FINDING_TRANSITION,
                    item["payload"],
                ),
            )
            for item in fixture["transitions"]
        )
        handoff_digest = runtime.models.finding_transition_sequence_sha256(transitions)
    except Exception as exc:
        raise LegacyCorruptionError(
            f"Run-8 handoff fixture validation failed at {fixture_path}: {exc}"
        ) from exc
    if handoff_digest != fixture.get("finding_transitions_sha256"):
        raise LegacyCorruptionError("Run-8 handoff fixture digest mismatch")

    projection_path = Path(os.path.abspath(os.fspath(projection_fixture)))
    projection_envelope = _parse_json(
        _read_regular_file(projection_path, "Run-8 projection fixture"),
        str(projection_path),
    )
    if not isinstance(projection_envelope, dict) or set(projection_envelope) != {
        "encoding",
        "payload",
        "schema_version",
    }:
        raise LegacyCorruptionError("Run-8 projection fixture envelope is invalid")
    if (
        projection_envelope["schema_version"] != "binary-fixture-envelope-v1"
        or projection_envelope["encoding"] != "gzip+hex"
        or not isinstance(projection_envelope["payload"], str)
    ):
        raise LegacyCorruptionError("Run-8 projection fixture encoding is invalid")
    try:
        import gzip

        compressed = bytes.fromhex(projection_envelope["payload"])
        projection = gzip.decompress(compressed)
        document = json.loads(projection)
        state = runtime.workflow_state.WorkflowState.from_dict(document)
        canonical_projection = runtime.models.canonical_json(state.to_dict())
    except Exception as exc:
        raise LegacyCorruptionError(
            f"Run-8 projection fixture validation failed at {projection_path}: {exc}"
        ) from exc
    if canonical_projection != projection:
        raise LegacyCorruptionError("Run-8 projection fixture is not canonical")
    projection_digest = hashlib.sha256(projection).hexdigest()
    if projection_digest != fixture.get("state_projection_sha256"):
        raise LegacyCorruptionError("Run-8 projection fixture digest mismatch")
    return LegacyFixtureDigests(projection_digest, handoff_digest)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and project a structured-v2-schema-2-state-v3-v1 artifact "
            "chain without writing or dispatching anything."
        )
    )
    parser.add_argument("run_directory", type=Path)
    parser.add_argument(
        "--format",
        choices=("summary", "projection"),
        default="summary",
        dest="output_format",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = verify_legacy_chain(arguments.run_directory)
    except LegacyVerifierError as exc:
        print(f"legacy verifier rejected chain: {exc}", file=sys.stderr)
        return 2
    if arguments.output_format == "projection":
        sys.stdout.buffer.write(result.canonical_projection)
        sys.stdout.buffer.write(b"\n")
    else:
        sys.stdout.buffer.write(_canonical_json(result.summary_document()))
        sys.stdout.buffer.write(b"\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess tests
    raise SystemExit(main())


__all__ = [
    "LEGACY_ARTIFACT_SHA256",
    "LEGACY_REDUCER_VERSION",
    "LEGACY_SOURCE_COMMIT",
    "LegacyCapabilityError",
    "LegacyCorruptionError",
    "LegacyFixtureDigests",
    "LegacyProtocolError",
    "LegacyVerificationResult",
    "LegacyVerifierError",
    "run_legacy_operation",
    "verify_legacy_chain",
    "verify_run_8_fixtures",
]
