from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from contracts import PlannedSlice
from state_io import atomic_write_file


class PlanHandoffError(ValueError):
    """Raised when a reviewed work plan cannot become an implementation handoff."""


@dataclass(frozen=True, slots=True)
class PlanArtifactFormatContract:
    """Single source for the parser grammar and its provider-facing description."""

    canonical_slice_heading: str
    minimum_slices: int
    slice_id_start: int
    slice_separators: tuple[str, ...]
    canonical_path_heading: str
    path_headings: tuple[str, ...]
    path_markers: tuple[str, ...]
    minimum_paths_per_slice: int
    invalid_path_characters: tuple[str, ...]
    nonexact_path_characters: tuple[str, ...]
    canonical_acceptance_heading: str
    acceptance_headings: tuple[str, ...]
    required_acceptance_heading_count: int
    acceptance_unordered_markers: tuple[str, ...]
    acceptance_ordered_marker_pattern: str
    minimum_acceptance_records: int
    goal_headings: tuple[str, ...]
    required_explicit_goal_heading_count: int

    @property
    def slice_heading_pattern(self) -> str:
        separators = "|".join(re.escape(item) for item in self.slice_separators)
        return (
            rf"^###\s+Slice\s+(?P<id>\d+)\s*(?:{separators})\s*"
            r"(?P<title>.+?)\s*$"
        )

    def render_provider_instructions(self) -> str:
        """Describe exactly the format accepted by the active parser contract."""
        path_headings = ", ".join(f"`{item}`" for item in self.path_headings)
        acceptance_headings = ", ".join(
            f"`{item}`" for item in self.acceptance_headings
        )
        goal_headings = ", ".join(f"`{item}`" for item in self.goal_headings)
        path_markers = ", ".join(f"`{item}`" for item in self.path_markers)
        unordered_markers = ", ".join(
            f"`{item}`" for item in self.acceptance_unordered_markers
        )
        separators = ", ".join(f"`{item}`" for item in self.slice_separators)
        forbidden_path_characters = ", ".join(
            f"`{item}`"
            for item in (
                *self.invalid_path_characters,
                *self.nonexact_path_characters,
            )
        )
        return (
            "PARSER-DERIVED PLAN ARTIFACT FORMAT CONTRACT\n"
            "The repository work-plan artifact must satisfy every rule below:\n"
            f"- Include at least {self.minimum_slices} future Slice section. Use the canonical heading "
            f"`{self.canonical_slice_heading}`. The parser accepts the title separators "
            f"{separators}; Slice ids start at {self.slice_id_start}, remain contiguous, "
            "and every title must be non-empty and convertible to a non-empty safe "
            "audit filename.\n"
            f"- Every Slice must contain an exact change-path section. Use the canonical "
            f"standalone heading `{self.canonical_path_heading}`. Accepted headings are "
            f"{path_headings}.\n"
            f"- Before the next Markdown or bold section heading, write at least "
            f"{self.minimum_paths_per_slice} exact "
            f"repository-relative POSIX path as a bullet with the path enclosed in "
            f"backticks. Accepted path bullet markers are {path_markers}. Paths must be "
            "non-empty and must not be absolute, contain parent traversal, "
            f"or contain any of {forbidden_path_characters}.\n"
            f"- Every Slice must contain exactly "
            f"{self.required_acceptance_heading_count} acceptance-criteria section. Use the "
            f"canonical standalone line `{self.canonical_acceptance_heading}`. Accepted "
            f"headings are {acceptance_headings}.\n"
            f"- Under that heading, write at least {self.minimum_acceptance_records} "
            f"individually delimited list record. "
            f"Accepted unordered markers are {unordered_markers}; ordered markers must "
            f"match `{self.acceptance_ordered_marker_pattern}` (for example `1.`, `2.`, "
            "or `a)`). List forms may be mixed. Wrapped non-empty continuation lines "
            "belong to the preceding record. A free-text paragraph without a list marker "
            "is invalid.\n"
            f"- An explicit Slice goal is optional because the Slice title is the fallback. "
            f"If an explicit goal is present, use exactly "
            f"{self.required_explicit_goal_heading_count} of {goal_headings} and give "
            "it non-empty text."
        )


PLAN_ARTIFACT_FORMAT_CONTRACT = PlanArtifactFormatContract(
    canonical_slice_heading="### Slice N - title",
    minimum_slices=1,
    slice_id_start=1,
    slice_separators=("-", "–", "—"),
    canonical_path_heading="**Exakter Änderungspfad**",  # allowlist:german -- plan contract
    path_headings=(
        "**Exakter Änderungspfad**",  # allowlist:german -- plan contract
        "**Exakter Änderungspfad:**",  # allowlist:german -- plan compatibility
        "**Exakte Änderungspfade**",  # allowlist:german -- plan compatibility
        "**Exakte Änderungspfade:**",  # allowlist:german -- plan compatibility
    ),
    path_markers=("-", "*"),
    minimum_paths_per_slice=1,
    invalid_path_characters=("\\",),
    nonexact_path_characters=("*", "?", "["),
    canonical_acceptance_heading="**Akzeptanzkriterien**",  # allowlist:german -- plan contract
    acceptance_headings=(
        "#### Akzeptanzkriterien",  # allowlist:german -- plan compatibility
        "**Akzeptanzkriterien**",  # allowlist:german -- canonical plan contract
        "**Akzeptanz und fokussierte Tests:**",  # allowlist:german -- compatibility
        "#### Fokussierte synthetische Akzeptanztests",  # allowlist:german -- generated plan compatibility
    ),
    required_acceptance_heading_count=1,
    acceptance_unordered_markers=("-",),
    acceptance_ordered_marker_pattern=r"(?:\d+|[A-Za-z])[.)]",
    minimum_acceptance_records=1,
    goal_headings=(
        "**Ziel**",  # allowlist:german -- supported plan contract
        "**Zweck:**",  # allowlist:german -- legacy plan compatibility
    ),
    required_explicit_goal_heading_count=1,
)


def render_plan_artifact_format_contract() -> str:
    """Return the provider instructions generated by the active parser contract."""
    return PLAN_ARTIFACT_FORMAT_CONTRACT.render_provider_instructions()


_SLICE_HEADING = re.compile(
    PLAN_ARTIFACT_FORMAT_CONTRACT.slice_heading_pattern,
    re.MULTILINE,
)
_EXACT_PATH_HEADING = re.compile(
    "^(?:"
    + "|".join(
        re.escape(item) for item in PLAN_ARTIFACT_FORMAT_CONTRACT.path_headings
    )
    + r")\s*$",
    re.MULTILINE,
)
_BULLET_PATH = re.compile(
    r"^[ \t]*(?:"
    + "|".join(
        re.escape(item) for item in PLAN_ARTIFACT_FORMAT_CONTRACT.path_markers
    )
    + r")[ \t]+`([^`]+)`[ \t]*$",
    re.MULTILINE,
)
_REQUIREMENT_SECTION = re.compile(
    r"^(?:#{1,6}[ \t]+|\*\*[^*]+\*\*\s*$)",
    re.MULTILINE,
)
_ACCEPTANCE_LIST_ITEM = re.compile(
    r"^(?:"
    + "|".join(
        re.escape(item)
        for item in PLAN_ARTIFACT_FORMAT_CONTRACT.acceptance_unordered_markers
    )
    + "|"
    + PLAN_ARTIFACT_FORMAT_CONTRACT.acceptance_ordered_marker_pattern
    + r")[ \t]+(?P<text>.*)$"
)
_GOAL_HEADINGS = PLAN_ARTIFACT_FORMAT_CONTRACT.goal_headings
_ACCEPTANCE_HEADINGS = PLAN_ARTIFACT_FORMAT_CONTRACT.acceptance_headings


def extract_implementation_slices(
    markdown: str,
    *,
    plan_stem: str,
) -> tuple[PlannedSlice, ...]:
    """Extract ordered future Slices and add one audit document to each scope."""
    headings = tuple(_SLICE_HEADING.finditer(markdown))
    if len(headings) < PLAN_ARTIFACT_FORMAT_CONTRACT.minimum_slices:
        raise PlanHandoffError("work plan has no '### Slice N – title' sections")
    slices: list[PlannedSlice] = []
    for index, heading in enumerate(headings):
        slice_id = int(heading.group("id"))
        expected = index + PLAN_ARTIFACT_FORMAT_CONTRACT.slice_id_start
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
        if len(product_paths) < PLAN_ARTIFACT_FORMAT_CONTRACT.minimum_paths_per_slice:
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
    if (
        len(acceptance_matches)
        != PLAN_ARTIFACT_FORMAT_CONTRACT.required_acceptance_heading_count
    ):
        raise PlanHandoffError(
            "Slice section lacks an unambiguous acceptance-criteria section"
        )
    acceptance_match = acceptance_matches[0]
    after = section[acceptance_match.end() :]
    next_section = _REQUIREMENT_SECTION.search(after)
    block = after[: next_section.start() if next_section else len(after)]
    criteria = _extract_bullet_records(block)
    if len(criteria) < PLAN_ARTIFACT_FORMAT_CONTRACT.minimum_acceptance_records:
        raise PlanHandoffError("Slice acceptance criteria must contain list records")
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
    if (
        len(matches)
        != PLAN_ARTIFACT_FORMAT_CONTRACT.required_explicit_goal_heading_count
    ):
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
        item = _ACCEPTANCE_LIST_ITEM.fullmatch(line)
        if item is not None:
            if current:
                records.append(" ".join(current))
            current = [item.group("text").strip()]
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


def branch_discovery_task_path(source_task_path: Path) -> Path:
    """Return the one deterministic queue path for a linked discovery run."""

    stem = source_task_path.stem
    if stem.lower().endswith("-implement"):
        stem = stem[:-10] + "-branch-discovery"
    else:
        stem += "-branch-discovery"
    return source_task_path.with_name(stem + source_task_path.suffix)


def render_branch_discovery_task(
    *,
    target_branch: str,
    scope_paths: tuple[str, ...],
    finding_handoff: tuple[str, str],
) -> str:
    """Render the slice-free child task consumed by BRANCH_DISCOVERY."""

    if not scope_paths:
        raise PlanHandoffError("branch discovery handoff requires a non-empty scope")
    source_run_id, export_record_id = finding_handoff
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", source_run_id) is None:
        raise PlanHandoffError("finding handoff source run is invalid")
    if re.fullmatch(r"ar1-[0-9a-f]{64}", export_record_id) is None:
        raise PlanHandoffError("finding handoff export is invalid")
    return (
        "Pruefe den vollstaendigen Branchstand im verknuepften Entdeckungslauf.\n\n"
        "ORCHESTRATOR_MODE: BRANCH_DISCOVERY\n"
        f"FINDING_HANDOFF_SOURCE_RUN: {source_run_id}\n"
        f"FINDING_HANDOFF_EXPORT: {export_record_id}\n"
        f"TARGET_BRANCH: {target_branch}\n"
        f"TASK_SCOPE: {', '.join(scope_paths)}\n"
    )


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
    if not raw or any(
        character in raw
        for character in PLAN_ARTIFACT_FORMAT_CONTRACT.invalid_path_characters
    ):
        raise PlanHandoffError(f"Slice {slice_id} contains an invalid exact path")
    path = PurePosixPath(raw)
    if (
        path.is_absolute()
        or ".." in path.parts
        or any(
            character in raw
            for character in PLAN_ARTIFACT_FORMAT_CONTRACT.nonexact_path_characters
        )
    ):
        raise PlanHandoffError(f"Slice {slice_id} contains a non-exact path")
    return path.as_posix()


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    if not slug:
        raise PlanHandoffError("Slice title cannot be converted to a safe filename")
    return slug[:72].rstrip("-")
