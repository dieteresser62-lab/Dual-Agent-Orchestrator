from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

from audit_document_contract import (
    OVERALL_SECTIONS, PLAN_APPENDIX_HEADING, PLAN_SECTIONS, SLICE_SECTIONS,
)


MANAGED_SECTION_KEYS = (
    "claude-review",  # allowlist:provider -- canonical managed marker
    "codex-responses",  # allowlist:provider -- canonical managed marker
    "validation-attestation",
    "test-approval-premortem",
    "findings",
    "decision-table",
    "approval-status",
)

MANAGED_SECTION_HEADINGS = {
    "claude-review": "Review-Feedback von Claude",  # allowlist:provider -- canonical heading
    "codex-responses": "Review-Antworten von Codex",  # allowlist:provider -- canonical heading
    "validation-attestation": "Validierungsattestierung",  # allowlist:german
    "test-approval-premortem": "Testfreigabe und Pre-Mortem",  # allowlist:german
    "findings": "Findings-Lebenszyklus",  # allowlist:german
    "decision-table": "Entscheidungstabelle",  # allowlist:german
    "approval-status": "Freigabestatus",  # allowlist:german
}

_LEGACY_WORK_PLAN_HEADINGS = {
    "claude-review": "Review-Feedback von Claude",  # allowlist:provider -- legacy heading
    "codex-responses": "Review-Antworten von Codex",  # allowlist:provider -- legacy heading
    **{
        key: "Planstatus und formale Marker"
        for key in MANAGED_SECTION_KEYS[2:]
    },
}

_MARKER = re.compile(
    r"^<!-- audit:(?P<key>[a-z0-9-]+):(?P<edge>begin|end) -->[ \t]*$"
)


class SemanticMarkdownKind(str, Enum):
    SLICE_AUDIT = "slice-audit"
    AUDIT_APPENDIX = "audit-appendix"
    LEGACY_WORK_PLAN = "legacy-work-plan"
    READABLE_SLICE = "readable-slice"
    READABLE_OVERALL = "readable-overall"
    READABLE_APPENDIX = "readable-appendix"


class SemanticMarkdownError(ValueError):
    """A managed Markdown document cannot be interpreted without hiding text."""

    def __init__(self, path: str, marker_kind: str, cause: str) -> None:
        self.path = path
        self.marker_kind = marker_kind
        self.cause = cause
        super().__init__(f"{path}: {marker_kind}: {cause}")


@dataclass(frozen=True)
class ManagedSection:
    key: str
    start: int
    end: int


@dataclass(frozen=True)
class SemanticMarkdownDocument:
    path: str
    kind: SemanticMarkdownKind
    markdown: str
    sections: tuple[ManagedSection, ...]
    appendix_start: int | None

    def semantic_text(self, *, remove_appendix: bool = False) -> str:
        if remove_appendix and self.kind in {
            SemanticMarkdownKind.AUDIT_APPENDIX,
            SemanticMarkdownKind.READABLE_APPENDIX,
        }:
            assert self.appendix_start is not None
            return self.markdown[: self.appendix_start].rstrip("\r\n") + "\n"
        rendered = self.markdown
        for section in reversed(self.sections):
            replacement = (
                f"<!-- audit:{section.key}:begin -->\n"
                f"<!-- audit:{section.key}:end -->"
            )
            rendered = rendered[: section.start] + replacement + rendered[section.end :]
        return rendered


def parse_semantic_markdown(
    markdown: str,
    *,
    path: str = "<markdown>",
    kind: SemanticMarkdownKind | None = None,
    require_managed: bool = False,
) -> SemanticMarkdownDocument:
    """Parse the one canonical, fail-closed managed-Markdown grammar.

    Marker-looking text in fences, block quotes, and indented examples remains
    ordinary visible content.  At top level it is control syntax and therefore
    must match the complete canonical structure.
    """
    records: list[
        tuple[str, str, int, int, str | None, str | None]
    ] = []
    fence: tuple[str, int] | None = None
    headings: dict[int, str] = {}
    offset = 0
    for line in markdown.splitlines(keepends=True):
        logical = line.rstrip("\r\n")
        fence, boundary = _advance_fence(logical, fence)
        if boundary:
            offset += len(line)
            continue
        if fence is None and not logical.startswith((">", " ", "\t")):
            heading_match = re.fullmatch(r"(#{1,6})[ \t]+(.+?)[ \t]*", logical)
            if heading_match:
                headings[len(heading_match.group(1))] = re.sub(
                    r"^\d+\.\s+", "", heading_match.group(2)
                )
            if logical.startswith("<!-- audit:"):
                marker = _MARKER.fullmatch(logical)
                if marker is None:
                    raise SemanticMarkdownError(
                        path, "marker-syntax", "non-canonical top-level audit marker"
                    )
                records.append(
                    (
                        marker.group("key"), marker.group("edge"), offset,
                        offset + len(logical), headings.get(2), headings.get(3),
                    )
                )
        offset += len(line)
    if fence is not None:
        raise SemanticMarkdownError(path, "code-fence", "unterminated code fence")

    if not records:
        if require_managed:
            raise SemanticMarkdownError(path, "marker-set", "managed sections are missing")
        inferred = kind or SemanticMarkdownKind.SLICE_AUDIT
        return SemanticMarkdownDocument(path, inferred, markdown, (), None)

    keys = [record[0] for record in records]
    layouts = {
        SemanticMarkdownKind.READABLE_SLICE: SLICE_SECTIONS,
        SemanticMarkdownKind.READABLE_OVERALL: OVERALL_SECTIONS,
        SemanticMarkdownKind.READABLE_APPENDIX: PLAN_SECTIONS,
    }
    inferred = kind
    if inferred is None:
        if markdown.startswith("# Gesamtaudit –") and tuple(keys[::2]) == tuple(k for k, _ in OVERALL_SECTIONS):
            inferred = SemanticMarkdownKind.READABLE_OVERALL
        elif markdown.startswith("# Slice ") and tuple(keys[::2]) == tuple(k for k, _ in SLICE_SECTIONS):
            inferred = SemanticMarkdownKind.READABLE_SLICE
        elif f"## {PLAN_APPENDIX_HEADING}\n" in markdown and tuple(keys[::2]) == tuple(k for k, _ in PLAN_SECTIONS):
            inferred = SemanticMarkdownKind.READABLE_APPENDIX
    layout = layouts.get(inferred)
    expected_keys = tuple(key for key, _ in layout) if layout is not None else MANAGED_SECTION_KEYS
    unknown = sorted(set(keys) - set(expected_keys))
    if unknown:
        raise SemanticMarkdownError(
            path, "unknown managed audit section", ", ".join(unknown)
        )
    if len(records) != 2 * len(expected_keys):
        raise SemanticMarkdownError(
            path, "marker-set", "every managed section requires exactly one marker pair"
        )

    appendix_matches = list(
        re.finditer(r"(?m)^## Orchestrator-Prüfprotokoll[ \t]*\r?$", markdown)
    )
    if inferred is None:
        inferred = (
            SemanticMarkdownKind.AUDIT_APPENDIX
            if appendix_matches
            else SemanticMarkdownKind.LEGACY_WORK_PLAN
            if markdown.startswith("# Work plan")
            else SemanticMarkdownKind.SLICE_AUDIT
        )
    appendix_start: int | None = None
    expected_level = 2
    if inferred in {SemanticMarkdownKind.AUDIT_APPENDIX, SemanticMarkdownKind.READABLE_APPENDIX}:
        if len(appendix_matches) != 1:
            raise SemanticMarkdownError(
                path, "appendix-heading", "requires exactly one level-two appendix heading"
            )
        appendix_start = appendix_matches[0].start()
        expected_level = 3

    sections: list[ManagedSection] = []
    stack: tuple[str, int] | None = None
    completed: list[str] = []
    for key, edge, start, end, heading_two, heading_three in records:
        owner_heading = heading_two if expected_level == 2 else heading_three
        expected_headings = (
            dict(layout) if layout is not None else
            _LEGACY_WORK_PLAN_HEADINGS
            if inferred is SemanticMarkdownKind.LEGACY_WORK_PLAN
            else MANAGED_SECTION_HEADINGS
        )
        if edge == "begin" and owner_heading != expected_headings[key]:
            raise SemanticMarkdownError(
                path,
                "marker-heading",
                f"{key} belongs below level-{expected_level} "
                f"'{expected_headings[key]}'",
            )
        if appendix_start is not None and start < appendix_start:
            raise SemanticMarkdownError(path, "marker-scope", "marker precedes audit appendix")
        if edge == "begin":
            if stack is not None:
                raise SemanticMarkdownError(path, "marker-nesting", f"{key} is nested")
            stack = (key, start)
            continue
        if stack is None or stack[0] != key:
            raise SemanticMarkdownError(path, "marker-order", f"unexpected end marker for {key}")
        sections.append(ManagedSection(key, stack[1], end))
        completed.append(key)
        stack = None
    if stack is not None:
        raise SemanticMarkdownError(path, "marker-set", f"missing end marker for {stack[0]}")
    if tuple(completed) != expected_keys:
        raise SemanticMarkdownError(path, "marker-order", "managed sections are out of order")
    return SemanticMarkdownDocument(path, inferred, markdown, tuple(sections), appendix_start)


def canonical_semantic_markdown(
    markdown: str,
    *,
    path: str = "<markdown>",
    kind: SemanticMarkdownKind | None = None,
    remove_appendix: bool = False,
    require_managed: bool = False,
) -> str:
    return parse_semantic_markdown(
        markdown, path=path, kind=kind, require_managed=require_managed
    ).semantic_text(remove_appendix=remove_appendix)


def _advance_fence(
    line: str, fence: tuple[str, int] | None
) -> tuple[tuple[str, int] | None, bool]:
    match = re.match(r"^ {0,3}(?P<run>`{3,}|~{3,})(?P<tail>.*)$", line)
    if match is None:
        return fence, False
    run = match.group("run")
    if fence is None:
        return (run[0], len(run)), True
    if run[0] == fence[0] and len(run) >= fence[1] and not match.group("tail").strip():
        return None, True
    return fence, False


__all__ = [
    "MANAGED_SECTION_HEADINGS",
    "MANAGED_SECTION_KEYS",
    "ManagedSection",
    "SemanticMarkdownDocument",
    "SemanticMarkdownError",
    "SemanticMarkdownKind",
    "canonical_semantic_markdown",
    "parse_semantic_markdown",
]
