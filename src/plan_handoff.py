from __future__ import annotations

import re
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Callable

from contracts import PlannedSlice
from state_io import atomic_write_file


class PlanHandoffError(ValueError):
    """Raised when a reviewed work plan cannot become an implementation handoff."""


_SLICE_HEADING = re.compile(
    r"^###\s+Slice\s+(?P<id>\d+)\s*(?:[–—-])\s*(?P<title>.+?)\s*$",
    re.MULTILINE,
)
_EXACT_PATH_HEADING = re.compile(
    r"^\*\*(?:Exakter Änderungspfad|Exakte Änderungspfade):?\*\*\s*$",
    re.MULTILINE,
)
_BULLET_PATH = re.compile(r"^[ \t]*[-*][ \t]+`([^`]+)`[ \t]*$", re.MULTILINE)
_REQUIREMENT_SECTION = re.compile(
    r"^(?:#{1,6}[ \t]+|\*\*[^*]+\*\*\s*$)",
    re.MULTILINE,
)
_GOAL_HEADINGS = (
    "**Ziel**",  # allowlist:german -- supported plan contract
    "**Zweck:**",  # allowlist:german -- legacy plan compatibility
)
_ACCEPTANCE_HEADINGS = (
    "#### Akzeptanzkriterien",  # allowlist:german -- canonical plan contract
    "**Akzeptanzkriterien**",  # allowlist:german -- compatibility
    "**Akzeptanz und fokussierte Tests:**",  # allowlist:german -- compatibility
    "#### Fokussierte synthetische Akzeptanztests",  # allowlist:german -- generated plan compatibility
)


def extract_implementation_slices(
    markdown: str,
    *,
    plan_stem: str,
) -> tuple[PlannedSlice, ...]:
    """Extract ordered future Slices and add one audit document to each scope."""
    headings = tuple(_SLICE_HEADING.finditer(markdown))
    if not headings:
        raise PlanHandoffError("work plan has no '### Slice N – title' sections")
    slices: list[PlannedSlice] = []
    for index, heading in enumerate(headings):
        slice_id = int(heading.group("id"))
        expected = index + 1
        if slice_id != expected:
            raise PlanHandoffError("future Slice ids must be contiguous and 1-based")
        end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        body = markdown[heading.end() : end]
        path_heading = _EXACT_PATH_HEADING.search(body)
        if path_heading is None:
            raise PlanHandoffError(f"Slice {slice_id} has no exact change-path section")
        path_section = body[path_heading.end() :]
        # The exact-path list ends at the next Markdown section regardless of
        # heading depth.  Plans commonly use level-four headings for work steps
        # and validation; treating their bullet lists as repository paths can
        # turn commands such as `npm test` into bogus productive files.
        next_section = re.search(
            r"^(?:\*\*|#{1,6}[ \t]+)", path_section, re.MULTILINE
        )
        if next_section is not None:
            path_section = path_section[: next_section.start()]
        product_paths = tuple(
            _canonical_exact_path(match.group(1), slice_id)
            for match in _BULLET_PATH.finditer(path_section)
        )
        if not product_paths:
            raise PlanHandoffError(f"Slice {slice_id} has no exact change path")
        # Review packets consume the same goal/acceptance facts later.  Validate
        # them here so an approved handoff cannot fail for the first time after
        # implementation and authoritative validation have already completed.
        extract_slice_requirements(markdown, slice_id)
        title = heading.group("title").strip()
        audit_path = (
            f"docs/internal/slice-{_slug(plan_stem)}-{slice_id:02d}-{_slug(title)}.md"
        )
        slices.append(
            PlannedSlice(
                slice_id=slice_id,
                summary=title,
                scope_paths=tuple(sorted({*product_paths, audit_path})),
            )
        )
    return tuple(slices)


def extract_slice_requirements(
    markdown: str,
    slice_id: int,
) -> tuple[str, tuple[str, ...]]:
    """Extract one Slice's stable goal and acceptance-criterion bullets.

    Older plans used an explicit goal/acceptance heading pair.
    Repository-grounded plans also use the unambiguous Slice title as
    their goal and a focused synthetic acceptance-test section.  Both are one
    semantic handoff contract and must be parsed identically by planning and
    review-packet construction.
    """
    headings = tuple(_SLICE_HEADING.finditer(markdown))
    selected = tuple(item for item in headings if int(item.group("id")) == slice_id)
    if len(selected) != 1:
        raise PlanHandoffError(
            f"approved plan must contain exactly one Slice {slice_id} section"
        )
    heading = selected[0]
    end = next(
        (item.start() for item in headings if item.start() > heading.start()),
        len(markdown),
    )
    section = markdown[heading.end() : end]

    goal = _extract_explicit_goal(section)
    if goal is None:
        goal = " ".join(heading.group("title").split())
    if not goal:
        raise PlanHandoffError("Slice goal must not be empty")

    acceptance_matches = tuple(
        match
        for literal in _ACCEPTANCE_HEADINGS
        for match in re.finditer(
            rf"^{re.escape(literal)}\s*$",
            section,
            re.MULTILINE,
        )
    )
    if len(acceptance_matches) != 1:
        raise PlanHandoffError(
            "Slice section lacks an unambiguous acceptance-criteria section"
        )
    acceptance_match = acceptance_matches[0]
    after = section[acceptance_match.end() :]
    next_section = _REQUIREMENT_SECTION.search(after)
    block = after[: next_section.start() if next_section else len(after)]
    criteria = _extract_bullet_records(block)
    if not criteria:
        raise PlanHandoffError("Slice acceptance criteria must contain bullet records")
    return goal, criteria


def _extract_explicit_goal(section: str) -> str | None:
    matches = tuple(
        match
        for literal in _GOAL_HEADINGS
        for match in re.finditer(
            rf"^{re.escape(literal)}(?P<inline>.*?)$",
            section,
            re.MULTILINE,
        )
    )
    if not matches:
        return None
    if len(matches) != 1:
        raise PlanHandoffError("Slice section contains ambiguous goal headings")
    match = matches[0]
    inline = " ".join(match.group("inline").split())
    after = section[match.end() :]
    next_section = _REQUIREMENT_SECTION.search(after)
    block = after[: next_section.start() if next_section else len(after)]
    body = " ".join(block.split())
    goal = " ".join(item for item in (inline, body) if item)
    if not goal:
        raise PlanHandoffError("Slice goal must not be empty")
    return goal


def _extract_bullet_records(block: str) -> tuple[str, ...]:
    records: list[str] = []
    current: list[str] = []
    for line in block.splitlines():
        if line.startswith("- "):
            if current:
                records.append(" ".join(current))
            current = [line[2:].strip()]
        elif current and line.strip():
            current.append(line.strip())
    if current:
        records.append(" ".join(current))
    return tuple(records)


def implementation_task_path(plan_task_path: Path) -> Path:
    stem = plan_task_path.stem
    if stem.lower().endswith("-plan"):
        stem = stem[:-5] + "-implement"
    else:
        stem += "-implement"
    return plan_task_path.with_name(stem + plan_task_path.suffix)


def render_implementation_task(
    *,
    work_plan_path: str,
    target_branch: str,
    approved_plan_commit: str,
    slices: tuple[PlannedSlice, ...],
    finding_handoff: tuple[str, str] | None = None,
) -> str:
    if not slices:
        raise PlanHandoffError("implementation handoff requires at least one Slice")
    scope = tuple(sorted({path for item in slices for path in item.scope_paths}))
    records = "\n".join(
        f"SLICE_PLAN: {item.slice_id} | {item.summary} | {', '.join(item.scope_paths)}"
        for item in slices
    )
    handoff_markers = ""
    if finding_handoff is not None:
        source_run_id, export_record_id = finding_handoff
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", source_run_id) is None:
            raise PlanHandoffError("finding handoff source run is invalid")
        if re.fullmatch(r"ar1-[0-9a-f]{64}", export_record_id) is None:
            raise PlanHandoffError("finding handoff export is invalid")
        handoff_markers = (
            f"FINDING_HANDOFF_SOURCE_RUN: {source_run_id}\n"
            f"FINDING_HANDOFF_EXPORT: {export_record_id}\n"
        )
    return (
        "Setze den freigegebenen Arbeitsplan Slice für Slice um.\n\n"
        "ORCHESTRATOR_MODE: IMPLEMENT\n"
        f"WORK_PLAN_PATH: {work_plan_path}\n"
        f"APPROVED_PLAN_COMMIT: {approved_plan_commit}\n"
        f"{handoff_markers}"
        f"TARGET_BRANCH: {target_branch}\n"
        f"TASK_SCOPE: {', '.join(scope)}\n\n"
        "Der Arbeitsplan ist bereits von Claude geprüft und vom "
        "Orchestrator lokal commitgebunden freigegeben. Ein konfiguriertes manuelles "
        "Plangate ist gegebenenfalls bereits abgeschlossen. Plane oder reviewe ihn "  # allowlist:german
        "nicht erneut. Verwende ihn als schreibgeschützte fachliche Quelle.\n\n"
        "Jeder Slice ändert ausschließlich seine persistierten Pfade. Das zugehörige "
        "Slice-MD dokumentiert Umsetzung, Validierung, Findings und Freigabe.\n\n"  # allowlist:german
        f"{records}\n"
    )


def write_implementation_handoff(
    *,
    plan_task_path: Path,
    repository_root: Path,
    work_plan_path: str,
    target_branch: str,
    approved_plan_commit: str,
    finding_handoff: tuple[str, str] | None = None,
    write_content: Callable[[Path, str], None] | None = None,
) -> Path:
    plan = repository_root / PurePosixPath(work_plan_path)
    try:
        markdown = plan.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PlanHandoffError(f"approved work plan could not be read: {exc}") from exc
    slices = extract_implementation_slices(markdown, plan_stem=plan.stem)
    content = render_implementation_task(
        work_plan_path=work_plan_path,
        target_branch=target_branch,
        approved_plan_commit=approved_plan_commit,
        slices=slices,
        finding_handoff=finding_handoff,
    )
    target = implementation_task_path(plan_task_path)
    if target.exists():
        try:
            existing = target.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise PlanHandoffError(f"implementation handoff is unreadable: {exc}") from exc
        if existing != content:
            raise PlanHandoffError(
                f"implementation handoff already exists with different content: {target}"
            )
        if write_content is not None:
            write_content(target, content)
        return target
    if write_content is None:
        atomic_write_file(target, content)
    else:
        write_content(target, content)
    return target


def _canonical_exact_path(raw: str, slice_id: int) -> str:
    if not raw or "\\" in raw:
        raise PlanHandoffError(f"Slice {slice_id} contains an invalid exact path")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or any(char in raw for char in "*?["):
        raise PlanHandoffError(f"Slice {slice_id} contains a non-exact path")
    return path.as_posix()


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    if not slug:
        raise PlanHandoffError("Slice title cannot be converted to a safe filename")
    return slug[:72].rstrip("-")
