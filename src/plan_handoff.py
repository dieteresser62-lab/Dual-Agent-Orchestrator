from __future__ import annotations

import re
import unicodedata
from pathlib import Path, PurePosixPath

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
) -> str:
    if not slices:
        raise PlanHandoffError("implementation handoff requires at least one Slice")
    scope = tuple(sorted({path for item in slices for path in item.scope_paths}))
    records = "\n".join(
        f"SLICE_PLAN: {item.slice_id} | {item.summary} | {', '.join(item.scope_paths)}"
        for item in slices
    )
    return (
        "Setze den freigegebenen Arbeitsplan Slice für Slice um.\n\n"
        "ORCHESTRATOR_MODE: IMPLEMENT\n"
        f"WORK_PLAN_PATH: {work_plan_path}\n"
        f"APPROVED_PLAN_COMMIT: {approved_plan_commit}\n"
        f"TARGET_BRANCH: {target_branch}\n"
        f"TASK_SCOPE: {', '.join(scope)}\n\n"
        "Der Arbeitsplan ist bereits von Claude und Antigravity geprüft und vom "
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
        return target
    atomic_write_file(target, content)
    return target


def _canonical_exact_path(raw: str, slice_id: int) -> str:
    if not raw or "\\" in raw:
        raise PlanHandoffError(f"Slice {slice_id} contains an invalid exact path")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or any(char in raw for char in "*?["):
        raise PlanHandoffError(f"Slice {slice_id} contains a non-exact path: {raw}")
    return path.as_posix()


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    if not slug:
        raise PlanHandoffError("Slice title cannot be converted to a safe filename")
    return slug[:72].rstrip("-")
