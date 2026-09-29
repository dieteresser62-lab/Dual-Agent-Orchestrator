"""Materialize a native reviewer request as ordered, bounded Read inputs."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from native_review_request import NativeReviewRequestBundle, PROVIDER_INPUT_BOUNDARY_EVIDENCE_KIND
from provider_input_budget import ProviderInputComponent

REVIEW_PACKET_CHUNK_CHARS = 24_000


class ReviewerInputError(RuntimeError):
    """A reviewer input cannot be materialized with its declared bindings."""


def _split_text_at_lines(text: str, max_chars: int) -> tuple[str, ...]:
    """Split a review packet into bounded, lossless chunks readable in one call each."""
    if max_chars < 1:
        raise ValueError("review packet chunk size must be positive")
    if not text:
        return ("",)
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break
        boundary = remaining.rfind("\n", 0, max_chars)
        if boundary < 1:
            boundary = max_chars
        else:
            boundary += 1
        chunks.append(remaining[:boundary])
        remaining = remaining[boundary:]
    return tuple(chunks)


def _write_review_manifest(
    runtime_dir: Path, entries: list[tuple[str, Path, str, int, str | None, int | None]],
    *, limit: int = REVIEW_PACKET_CHUNK_CHARS,
) -> tuple[Path, tuple[Path, ...]]:
    """Write a bounded manifest, paging its entries when one Read is insufficient."""
    instruction = (
        "Read every listed file exactly once in order. Concatenate request chunks "
        "without separators before interpreting the JSON request. For each evidence "
        "content_ref, concatenate its numbered parts without separators before "
        "evaluating the full-content sha256 and byte_count in the request."
    )
    lines = ["# Native review request manifest", "", instruction, ""]
    for name, path, digest, byte_count, content_ref, part_number in entries:
        relation = (
            f" | content_ref={content_ref} | part={part_number}"
            if content_ref is not None else ""
        )
        lines.append(
            f"- `{path}` | component={name}{relation} | bytes={byte_count} | sha256={digest}"
        )
    manifest = runtime_dir / "native-review-manifest.md"
    full_text = "\n".join(lines) + "\n"
    if len(full_text) <= limit:
        manifest.write_text(full_text, encoding="utf-8")
        return manifest, ()

    page_header = "# Native review manifest entries\n"
    pages: list[Path] = []
    page_lines: list[str] = []
    for line in lines[4:]:
        if len(page_header + line + "\n") > limit:
            raise ReviewerInputError("review manifest entry exceeds one Read")
        candidate = page_header + "\n".join((*page_lines, line)) + "\n"
        if len(candidate) > limit:
            if not page_lines:
                raise ReviewerInputError("review manifest entry exceeds one Read")
            page = runtime_dir / f"native-review-manifest-{len(pages) + 1:03d}.md"
            page.write_text(page_header + "\n".join(page_lines) + "\n", encoding="utf-8")
            pages.append(page)
            page_lines = [line]
        else:
            page_lines.append(line)
    if page_lines:
        page = runtime_dir / f"native-review-manifest-{len(pages) + 1:03d}.md"
        page.write_text(page_header + "\n".join(page_lines) + "\n", encoding="utf-8")
        pages.append(page)
    index_lines = [
        "# Native review request manifest",
        "",
        "Read each manifest page once in order, then every file listed in those pages "
        "once in order. " + instruction,
        "",
    ]
    for page in pages:
        data = page.read_bytes()
        index_lines.append(
            f"- `{page}` | bytes={len(data)} | sha256={hashlib.sha256(data).hexdigest()}"
        )
    index_text = "\n".join(index_lines) + "\n"
    if len(index_text) > limit:
        raise ReviewerInputError("review manifest index exceeds one Read")
    manifest.write_text(index_text, encoding="utf-8")
    return manifest, tuple(pages)


@dataclass(frozen=True, slots=True)
class ReviewerInputBundle:
    """One invocation's files, order, directive and measured input components."""

    manifest_file: Path
    manifest_pages: tuple[Path, ...]
    request_files: tuple[Path, ...]
    evidence_files: tuple[Path, ...]
    directive: str
    components: tuple[ProviderInputComponent, ...]


REVIEW_INPUT_PATH_PLACEHOLDER = "<review-input>"


def measured_reviewer_path_text(content: str, input_dir: Path) -> str:
    """Replace only the random review input path in measured text."""
    return content.replace(str(input_dir), REVIEW_INPUT_PATH_PLACEHOLDER)


def build_reviewer_input(
    bundle: NativeReviewRequestBundle, runtime_dir: Path, *,
    chunk_chars: int = REVIEW_PACKET_CHUNK_CHARS,
    normalize_runtime_path: bool = True,
    measured_path_placeholder: str | None = None,
) -> ReviewerInputBundle:
    """Write input files in manifest order and bind measured components."""
    if not isinstance(bundle, NativeReviewRequestBundle):
        raise TypeError("native Claude adapter requires NativeReviewRequestBundle")
    request_chunks = _split_text_at_lines(bundle.canonical_json, chunk_chars)
    request_files = tuple(
        runtime_dir / f"native-request-{index:03d}.json.part"
        for index in range(1, len(request_chunks) + 1)
    )
    manifest_entries: list[tuple[str, Path, str, int, str | None, int | None]] = []
    for index, (packet_file, chunk) in enumerate(
        zip(request_files, request_chunks, strict=True), start=1
    ):
        packet_file.write_text(chunk, encoding="utf-8")
        encoded = packet_file.read_bytes()
        manifest_entries.append((
            f"request_chunk_{index:03d}", packet_file,
            hashlib.sha256(encoded).hexdigest(), len(encoded), None, None,
        ))

    evidence_files: list[Path] = []
    for asset in bundle.evidence_assets:
        chunks = _split_text_at_lines(asset.content, chunk_chars)
        for index, chunk in enumerate(chunks, start=1):
            target = runtime_dir.joinpath(*Path(asset.path).parts)
            target = target.with_name(f"{target.name}.part-{index:03d}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(chunk.encode("utf-8"))
            encoded = target.read_bytes()
            evidence_files.append(target)
            manifest_entries.append((
                f"evidence_asset_{len(evidence_files):03d}", target,
                hashlib.sha256(encoded).hexdigest(), len(encoded), asset.path, index,
            ))
    manifest_file, manifest_pages = _write_review_manifest(
        runtime_dir, manifest_entries, limit=chunk_chars
    )
    read_call_budget = 1 + len(manifest_pages) + len(manifest_entries)
    boundary_evidence_withheld = any(
        item.get("kind") == PROVIDER_INPUT_BOUNDARY_EVIDENCE_KIND
        for item in bundle.document["evidence_manifest"]
    )
    if boundary_evidence_withheld:
        directive = (
            f"Read {manifest_file} exactly once, then its manifest "
            "pages if any and every listed request or evidence file exactly once "
            "in order before using additional Read calls for repository "
            "paths required by the provider-input boundary notice. Review the "
            "reconstructed native request and current read-only repository snapshot; "
            "return only the schema-bound JSON result."
        )
    else:
        directive = (
            f"Read {manifest_file} exactly once, then its manifest "
            "pages if any and every listed request or evidence file "
            f"exactly once in order ({read_call_budget} Read calls total). Review the "
            "reconstructed native request and return only the schema-bound JSON result."
        )

    runtime_path = str(runtime_dir)
    # Use the actual directory prefix, including any non-default adapter name.
    if measured_path_placeholder is not None:
        stable_runtime_path = measured_path_placeholder
    elif normalize_runtime_path:
        runtime_prefix = runtime_dir.name.rsplit("-runtime-", 1)[0] + "-runtime-"
        random_suffix = runtime_dir.name.removeprefix(runtime_prefix)
        stable_runtime_path = str(
            runtime_dir.with_name(runtime_prefix + "_" * len(random_suffix))
        )
    else:
        # Container inputs retain their actual paths in measured components.
        stable_runtime_path = runtime_path

    def stable_paths(content: str) -> str:
        return content.replace(runtime_path, stable_runtime_path)

    components = [
        ProviderInputComponent(name, path.read_text(encoding="utf-8"))
        for name, path, _digest, _byte_count, _content_ref, _part in manifest_entries
    ]
    measured_manifest = stable_paths(manifest_file.read_text(encoding="utf-8"))
    for index, path in enumerate(manifest_pages, start=1):
        page_bytes = path.read_bytes()
        measured_page = stable_paths(page_bytes.decode("utf-8"))
        page_row = f"`{stable_paths(str(path))}` | bytes={len(page_bytes)} | sha256="
        actual_row = page_row + hashlib.sha256(page_bytes).hexdigest()
        if measured_manifest.count(actual_row) != 1:
            raise ReviewerInputError("review manifest page binding is missing or repeated")
        measured_manifest = measured_manifest.replace(
            actual_row,
            page_row + hashlib.sha256(measured_page.encode("utf-8")).hexdigest(),
        )
        components.append(ProviderInputComponent(f"packet_chunk_{index:03d}", measured_page))
    components.append(ProviderInputComponent("packet_manifest", measured_manifest))
    return ReviewerInputBundle(
        manifest_file, manifest_pages, request_files, tuple(evidence_files),
        directive, tuple(components),
    )
