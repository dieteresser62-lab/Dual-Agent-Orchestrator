from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

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
    # Measured on HEAD 7064367 from the same _review_bundle() input.
    expected = {
        "request_chunk_001": ("4fb5228491380660239a220d115f2246418d769e339a213aec1a5cadae53523c", 1809),
        "packet_manifest": ("686b23c62d2817f79043478ce9d5392d38a05a729e1046f7cb87c63f835b31ca", 502),
        "system_policy": ("3418dced3a3674f0c7fa5e8569b2fdc88a01a1a3be51fb14056c9d1d352578f6", 2440),
        "response_schema": ("e25e390e6c895a7549e0645b66987419c373e0c25358e8a8ead20c5f8045c107", 24277),
        "start_directive": ("344363a35fdc403d7803b5c1edfaf731becbade36c37b941d7b8e204c65db6a0", 280),
    }
    adapter = _adapter()
    try:
        prepared = adapter.prepare_native_provider_input(_review_bundle())
        assert list(expected) == [item.name for item in prepared.components]
        assert {
            item.name: (hashlib.sha256(item.content.encode()).hexdigest(), len(item.content.encode()))
            for item in prepared.components
        } == expected
        inputs = adapter.invocation.reviewer_input
        assert inputs is not None
        assert b"".join(path.read_bytes() for path in inputs.request_files) == _review_bundle().canonical_json.encode()
        assert inputs.manifest_file.read_text().startswith("# Native review request manifest\n")
        measurement = measure_provider_input(
            prepared, provider="claude", role="claude", operation="claude_slice_review",
            binding_fingerprint="a" * 64, policy=default_provider_input_budget_policy(),
        )
        assert measurement.total_bytes == sum(size for _, size in expected.values())
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
        assert inputs is not None and len(inputs.manifest_pages) == 7
        assert len(prepared.components) == 61
        encoded = json.dumps(
            [(item.name, item.content) for item in prepared.components],
            sort_keys=True, separators=(",", ":"),
        ).encode()
        assert hashlib.sha256(encoded).hexdigest() == (
            "ef664c4cc85dc5f22f5866b6e96a724cf30e13781ab923ec3c96234336306891"
        )
        assert sum(len(item.content.encode()) for item in prepared.components) == 300_975
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
