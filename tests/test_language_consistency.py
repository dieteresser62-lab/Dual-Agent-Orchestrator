from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THIS_FILE = Path(__file__).resolve()
SOURCE_DIRS = (ROOT / "src", ROOT / "tests")
ROOT_FILES = (ROOT / "run_task", ROOT / "README.md", ROOT / "example-task.md")
ROLE_FILES = (
    ROOT / "AGENTS.md", ROOT / "CLAUDE.md", ROOT / "CODEX.md", ROOT / "ANTIGRAVITY.md"
)
ALLOWLIST_FILENAME_PATTERNS: tuple[str, ...] = ()
GERMAN_TOKENS = [  # allowlist:german
    "Aufgabe",
    "Zyklus",
    "Planstand",
    "Arbeitspakete",
    "Akzeptanzkriterien",
    "Risiken",
    "Teststrategie",
    "Offene Fragen",
    "Erforderliche",
    "Konsolidierter",
    "Entscheidung",
    "Begruendung",
    "Naechste",
    "Pflichtanpassungen",
    "Geaenderte",
    "Umgesetzte",
    "Restpunkte",
    "Pflicht-Fixes",
    "Freigabe",
    "Fortfahren",
    "abgeschlossen",
    "erkannt",
    "Versuche",
    "Korrigiere",
    "Maengel",
    "Fehlerkontext",
    "simuliert",
    "leer",
    "Zeit",
    "Ergebnis",
    "Zusammenfassung",
    "Bericht",
    "Pruefung",
    "Prüfung",
]
PATTERNS = [re.compile(re.escape(token), re.IGNORECASE) for token in GERMAN_TOKENS]


def _iter_content_files() -> list[Path]:
    files: list[Path] = []
    for source_dir in SOURCE_DIRS:
        files.extend(sorted(source_dir.rglob("*.py")))
    files.extend(path for path in ROOT_FILES if path.exists())
    return files


def _is_allowlisted_line(path: Path, line: str) -> bool:
    if "# allowlist:german" in line:
        return True
    if path == THIS_FILE:
        return True
    return False


def _collect_content_hits() -> list[str]:
    hits: list[str] = []
    for path in _iter_content_files():
        rel_path = path.relative_to(ROOT)
        resolved_path = path.resolve()
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _is_allowlisted_line(resolved_path, line):
                continue
            for pattern in PATTERNS:
                match = pattern.search(line)
                if match:
                    hits.append(f"{rel_path}:{line_no} -> {match.group(0)}")
    return hits


def _iter_filename_targets() -> list[Path]:
    targets: list[Path] = []
    for source_dir in SOURCE_DIRS:
        targets.extend(sorted(source_dir.rglob("*")))

    for path in sorted(ROOT.iterdir()):
        name = path.name
        if name.startswith("."):
            continue
        if name in {"src", "tests"}:
            continue
        targets.append(path)
    return targets


def _is_excluded_path(path: Path) -> bool:
    excluded_names = {".git", ".orchestrator", "__pycache__"}
    for part in path.parts:
        if part in excluded_names:
            return True
    return path.name.startswith(".")


def _is_allowlisted_filename(path: Path) -> bool:
    rel = str(path.relative_to(ROOT))
    return any(path.match(pattern) or rel == pattern for pattern in ALLOWLIST_FILENAME_PATTERNS)


def _collect_filename_hits() -> list[str]:
    hits: list[str] = []
    for path in _iter_filename_targets():
        if _is_excluded_path(path):
            continue
        if _is_allowlisted_filename(path):
            continue
        rel_path = path.relative_to(ROOT)
        path_text = str(rel_path)
        for pattern in PATTERNS:
            match = pattern.search(path_text)
            if match:
                hits.append(f"{rel_path} -> {match.group(0)}")
    return hits


def test_no_german_terms_in_content() -> None:
    hits = _collect_content_hits()
    assert not hits, "German tokens found in content:\n" + "\n".join(hits)


def test_no_german_terms_in_filenames() -> None:
    hits = _collect_filename_hits()
    assert not hits, "German tokens found in filenames:\n" + "\n".join(hits)


def test_root_roles_share_the_state_v3_contract_and_gemini_role_is_gone() -> None:
    assert not (ROOT / "GEMINI.md").exists()
    for path in ROLE_FILES:
        assert path.is_file(), f"missing role contract: {path.name}"
        text = path.read_text(encoding="utf-8")
        assert "AGENTS.md" in text or path.name == "AGENTS.md"
        assert "VALIDATION_RESULT" in text
    shared = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for marker in (
        "PLAN_READY", "SLICE_PLAN", "IMPLEMENTATION_READY", "FINAL_REPORT_READY",
        "PLAN_APPROVAL", "SLICE_APPROVAL", "FINAL_APPROVAL", "NEW_FINDING",
        "FINDING_STATUS", "FINDING_RESPONSE", "REVIEW_EVIDENCE", "PRE_MORTEM",
        "STOP_REQUESTED", "STATUS: DONE",
    ):
        assert marker in shared


def test_claude_profile_is_persistently_sonnet_high() -> None:
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "Sonnet" in claude and "`high`" in claude
    assert "Sonnet" in agents and "`high`" in agents
