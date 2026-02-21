from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = (ROOT / "src", ROOT / "tests")
ROOT_FILES = (ROOT / "run_task", ROOT / "README.md", ROOT / "example-task.md")
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
    if path == Path(__file__).resolve():
        return True
    return False


def _collect_content_hits() -> list[str]:
    hits: list[str] = []
    for path in _iter_content_files():
        rel_path = path.relative_to(ROOT)
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _is_allowlisted_line(path.resolve(), line):
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
