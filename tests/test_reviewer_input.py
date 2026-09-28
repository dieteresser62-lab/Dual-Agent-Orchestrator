from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from content_authority_support import prior_role_wire_document
from agent_adapters import NativeClaudeReviewAdapter, AgentOutputError
from agent_config import AgentSettings
from provider_input_budget import default_provider_input_budget_policy, measure_provider_input
from native_review_request import (
    NativeReviewEvidenceInput, NativeReviewKind, NativeReviewRequestSpec,
    build_native_review_request,
)
from reviewer_input import ReviewerInputError, _split_text_at_lines, build_reviewer_input
from test_agent_adapters import _review_bundle


def _adapter() -> NativeClaudeReviewAdapter:
    return NativeClaudeReviewAdapter(AgentSettings("claude", "claude", "opus", 1800, "high"))


def test_reviewer_components_match_start_head_bytes() -> None:
    # Slice 8b wire cut; the historical component digests are checked below.
    expected = {
        "request_chunk_001": ("5dbbf48dd842d6c9e207b382a53ebd14c1b3d884d966227481d45f6c22515d8b", 1813),
        "packet_manifest": ("583b08d2958e03a257cbc301a2dac7493cb08198858b62462bd508bb6308e999", 502),
        "system_policy": ("a1f002614be324619583ed6058d1f902c203a1b1779f2ae6967df33de0551a6d", 2440),
        "response_schema": ("8ecef1f1d81582ef94d64f85f046495a247b9de37d019eafcedd72d37f4e09b2", 25916),
        "start_directive": ("344363a35fdc403d7803b5c1edfaf731becbade36c37b941d7b8e204c65db6a0", 280),
    }
    adapter = _adapter()
    try:
        prepared = adapter.prepare_native_provider_input(_review_bundle())
        runtime_parent = str(adapter.invocation.runtime_dir.parent)

        def historical_content(content: str) -> str:
            return content.replace(runtime_parent, "/tmp")

        assert list(expected) == [item.name for item in prepared.components]
        # Slice 8b wire cut: inverse request/schema values recreate the old
        # request-chunk pin and its digest-bound manifest line exactly.
        bundle = _review_bundle()
        prior_schema = (bundle.provider_response_schema_json
            .replace('"const":"reviewer"', '"const":"claude"')
            .replace('"enum":["reviewer"]', '"enum":["claude"]'))
        prior_schema_digest = hashlib.sha256(prior_schema.encode()).hexdigest()
        assert prior_schema_digest == "8892fb0113d102afa886f2be6421c393440a683fd6065e31198823205c2c9746"
        prior_request = prior_role_wire_document(
            bundle.document, prior_schema_sha256=prior_schema_digest
        )
        prior_chunk = json.dumps(prior_request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        prior_chunk_digest = hashlib.sha256(prior_chunk.encode()).hexdigest()
        assert (prior_chunk_digest, len(prior_chunk.encode())) == (
            "09f1fbb1a0e29d88d73e9c1373e0a8ee0ae2d1c3f6a49325f5427529b131f85b", 1809
        )
        current_chunk = next(item for item in prepared.components if item.name == "request_chunk_001")
        current_manifest = next(item for item in prepared.components if item.name == "packet_manifest")
        prior_manifest = (historical_content(current_manifest.content)
            .replace(str(len(current_chunk.content.encode())), str(len(prior_chunk.encode())))
            .replace(hashlib.sha256(current_chunk.content.encode()).hexdigest(), prior_chunk_digest))
        assert hashlib.sha256(prior_manifest.encode()).hexdigest() == (
            "26ee597cd0f1ad6d8e566286d5f6bc88fad04ebceaa0b273081a499b93548c1e"
        )
        assert {
            item.name: (hashlib.sha256(historical_content(item.content).encode()).hexdigest(),
                        len(historical_content(item.content).encode()))
            for item in prepared.components
        } == expected
        inputs = adapter.invocation.reviewer_input
        assert inputs is not None
        assert b"".join(path.read_bytes() for path in inputs.request_files) == _review_bundle().canonical_json.encode()
        assert inputs.manifest_file.read_text().startswith("# Native review request manifest\n")
        measurement = measure_provider_input(
            prepared, provider="claude", role="reviewer", operation="reviewer_slice_review",
            binding_fingerprint="a" * 64, policy=default_provider_input_budget_policy(),
        )
        assert measurement.total_bytes == sum(len(item.content.encode()) for item in prepared.components)
        actual_bytes = sum(path.stat().st_size for path in (
            *inputs.request_files, *inputs.evidence_files,
            *inputs.manifest_pages, inputs.manifest_file,
        )) + sum(len(prepared.command[prepared.command.index(flag) + 1].encode()) for flag in (
            "--system-prompt", "--json-schema",
        )) + len(prepared.command[-1].encode())
        assert measurement.total_bytes == actual_bytes
    finally:
        adapter.cleanup()


def test_chunking_preserves_unicode_and_line_order() -> None:
    content = "one\n" + "ä" * 13 + "\nthree"
    chunks = _split_text_at_lines(content, 12)
    assert "".join(chunks) == content
    assert chunks[0] == "one\n"


def test_paged_manifest_and_chunk_bytes_match_start_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent_adapters

    base = _review_bundle()
    bundle = build_native_review_request(NativeReviewRequestSpec(
        context=base.bound_context.context,
        review_kind=NativeReviewKind.SLICE,
        target_branch="feature/native",
        base_commit="b" * 40,
        authorized_paths=("src/workflow.py",),
        acceptance_criteria=("Inspect all evidence.",),
        evidence=(NativeReviewEvidenceInput("e" * 180, "diff", "z" * 240_001),),
    ))
    monkeypatch.setattr(agent_adapters, "REVIEW_PACKET_CHUNK_CHARS", 5_000)
    adapter = _adapter()
    try:
        prepared = adapter.prepare_native_provider_input(bundle)
        inputs = adapter.invocation.reviewer_input
        assert inputs is not None and len(inputs.manifest_pages) > 1
        assert all(len(page.read_text(encoding="utf-8")) <= 5_000 for page in inputs.manifest_pages)
        assert len(inputs.manifest_file.read_text(encoding="utf-8")) <= 5_000
        assert len(prepared.components) == (
            len(inputs.request_files) + len(inputs.evidence_files) + len(inputs.manifest_pages) + 4
        )
        assert b"".join(path.read_bytes() for path in inputs.request_files) == bundle.canonical_json.encode()
        assert b"".join(path.read_bytes() for path in inputs.evidence_files) == b"z" * 240_001
        assert [item.name for item in prepared.components if item.name.startswith("packet_chunk_")] == [
            f"packet_chunk_{index:03d}" for index in range(1, len(inputs.manifest_pages) + 1)
        ]
    finally:
        adapter.cleanup()


def test_materialization_failure_and_abort_remove_temporary_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent_adapters

    for failure in (ReviewerInputError("review manifest entry exceeds one Read"), KeyboardInterrupt()):
        adapter = _adapter()
        paths: list[Path] = []

        def fail(_bundle: object, runtime_dir: Path, *, chunk_chars: int) -> object:
            paths.append(runtime_dir)
            (runtime_dir / "partial").write_text("partial")
            raise failure

        with monkeypatch.context() as patch:
            patch.setattr(agent_adapters, "build_reviewer_input", fail)
            expected = AgentOutputError if isinstance(failure, ReviewerInputError) else type(failure)
            with pytest.raises(expected):
                adapter.prepare_native_provider_input(_review_bundle())
        assert paths and not paths[0].exists()
        assert adapter.invocation.runtime_dir is None


def test_manifest_error_after_files_exist_is_cleaned_up(monkeypatch: pytest.MonkeyPatch) -> None:
    import reviewer_input

    adapter = _adapter()
    original = reviewer_input._write_review_manifest
    paths: list[Path] = []

    def fail(runtime_dir: Path, entries: list[object], *, limit: int) -> object:
        paths.append(runtime_dir)
        original(runtime_dir, entries, limit=limit)
        raise ReviewerInputError("review manifest index exceeds one Read")

    with monkeypatch.context() as patch:
        patch.setattr(reviewer_input, "_write_review_manifest", fail)
        with pytest.raises(AgentOutputError, match="review manifest index exceeds one Read"):
            adapter.prepare_native_provider_input(_review_bundle())
    assert paths and not paths[0].exists()


def test_runtime_cache_creation_failure_removes_temporary_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import agent_adapters

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    original_mkdir = Path.mkdir

    def fail_cache(path: Path, *args: object, **kwargs: object) -> None:
        if path == runtime / "cache":
            raise OSError("cache creation failed")
        original_mkdir(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(agent_adapters.tempfile, "mkdtemp", lambda **_kwargs: str(runtime))
        patch.setattr(Path, "mkdir", fail_cache)
        with pytest.raises(OSError, match="cache creation failed"):
            _adapter().prepare_native_provider_input(_review_bundle())
    assert not runtime.exists()
