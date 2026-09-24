"""Headings and fail-closed marker layouts of human audit documents."""

from __future__ import annotations

SLICE_SECTIONS = (
    ("status", None),
    ("goal", "Ziel"),
    ("acceptance", "Akzeptanzkriterien"),
    ("scope", "Umfang"),
    ("history", "Verlauf"),
    ("findings", "Befunde"),
    ("validation", "Validierung"),
    ("approval", "Abschlussprüfung"),
    ("reference", "Abschlussprüfung"),
)
OVERALL_SECTIONS = (
    ("meta", None),
    ("overview", "Übersicht"),
    ("findings", "Befunde"),
    ("holds", "Halte und Entscheidungen"),
    ("acceptance-review", "Abnahmereview"),
)
PLAN_SECTIONS = (
    ("history", "Verlauf"),
    ("findings", "Befunde"),
    ("approval", "Abschlussprüfung"),
)
PLAN_APPENDIX_HEADING = "Orchestrator-Prüfprotokoll"


def marker(key: str, edge: str) -> str:
    return f"<!-- audit:{key}:{edge} -->"


def managed_section(key: str, heading: str | None, *, level: int = 2, body: str = "") -> str:
    if heading is None:
        return "\n".join((marker(key, "begin"), body.rstrip("\n"), marker(key, "end"), ""))
    return "\n".join((
        f"{'#' * level} {heading}", "", marker(key, "begin"),
        body.rstrip("\n"), marker(key, "end"), "",
    ))
