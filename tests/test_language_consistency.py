from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from cli import build_parser, parse_args

ROOT = Path(__file__).resolve().parents[1]
THIS_FILE = Path(__file__).resolve()
SOURCE_DIRS = (ROOT / "src", ROOT / "tests")
ROOT_FILES = (ROOT / "run_task",)
ROLE_FILES = (
    ROOT / "AGENTS.md", ROOT / "CLAUDE.md", ROOT / "CODEX.md", ROOT / "ANTIGRAVITY.md"
)
REFERENCE_DOC_FILES = (
    ROOT / "docs" / "reference" / "architecture-and-domain-concept.md",
    ROOT / "docs" / "reference" / "market-comparison.md",
)
USER_MARKDOWN_FILES = (
    ROOT / "README.md",
    ROOT / "Quickstart.md",
    ROOT / "example-task.md",
    ROOT / "example-plan-task.md",
    *REFERENCE_DOC_FILES,
)
USER_DOC_FILES = (*USER_MARKDOWN_FILES, ROOT / "workflow.puml")
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


def test_no_german_terms_in_runtime_content() -> None:
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


def test_root_roles_share_structured_artifact_authority_contract() -> None:
    required = (
        "## Structured artifact authority",
        "Agent text markers are ingress-adapter input only",
        ".orchestrator/artifacts/<run-id>/records/",
        "technical source of truth",
        "operational mirrors",
        "human audit view rather than a repair source",
        "legacy-state-v3",
        "not silently migrated",
        "Resume is fail-closed",
    )
    sections = []
    for path in ROLE_FILES:
        text = path.read_text(encoding="utf-8")
        assert all(fragment in text for fragment in required), path.name
        section = text.split(required[0], 1)[1].split("\n## ", 1)[0].strip()
        sections.append(section)
    assert len(set(sections)) == 1


def test_claude_profile_is_persistently_sonnet_high() -> None:
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "Sonnet" in claude and "`high`" in claude
    assert "Sonnet" in agents and "`high`" in agents


def test_active_user_docs_use_only_the_state_v3_role_model() -> None:
    forbidden = (
        "two-phase", "phase 1", "phase 2", "phase1", "phase2",
        "GEMINI.md", "OPEN_FINDINGS", "allow-fallback-to-gemini", "--from-phase",
        "--manual-gate", "--phase1-max-cycles", "--phase2-max-cycles",
        "--max-agent-retries", "--max-shared-chars", "--file-snapshot-max-lines",
        "--file-snapshot-max-files", "--no-recover",
    )
    hits = []
    for path in USER_DOC_FILES:
        text = path.read_text(encoding="utf-8").lower()
        hits.extend(
            f"{path.name}: {term}"
            for term in forbidden
            if term.lower() in text
        )
    assert not hits, "Legacy user-document terms found:\n" + "\n".join(hits)


def test_active_markdown_user_documentation_is_german() -> None:
    expected_german = {
        ROOT / "README.md": ("## Überblick", "## Voraussetzungen und unterstützte Plattformen"),
        ROOT / "Quickstart.md": ("# Schnellstart", "## 1. Voraussetzungen prüfen"),
        ROOT / "example-plan-task.md": (
            "# Arbeitsplan für einen begrenzten Gap erstellen",
            "## Akzeptanzkriterien",
        ),
        ROOT / "example-task.md": ("## Kontext", "## Akzeptanzkriterien"),
        REFERENCE_DOC_FILES[0]: ("# Architektur- und Fachkonzept", "## 2. Fachliches Problem"),
        REFERENCE_DOC_FILES[1]: ("# Marktvergleich", "## 1. Zusammenfassung"),
    }
    forbidden_english_headings = (
        "## Overview",
        "## Requirements and Supported Platforms",
        "## Quick Start",
        "## Context",
        "## Acceptance Criteria",
        "## Purpose",
        "## Domain Problem",
        "## Executive Summary",
    )
    assert set(expected_german) == set(USER_MARKDOWN_FILES)
    for path, required_fragments in expected_german.items():
        text = path.read_text(encoding="utf-8")
        assert all(fragment in text for fragment in required_fragments), path.name
        assert not any(heading in text for heading in forbidden_english_headings), path.name


def test_readme_documents_exactly_the_public_long_cli_options() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"(?<![A-Za-z0-9_])--[a-z][a-z0-9-]+", readme))
    public = {
        option
        for action in build_parser()._actions
        for option in action.option_strings
        if option.startswith("--")
    }
    assert documented == public, (
        f"README-only options: {sorted(documented - public)}; "
        f"undocumented parser options: {sorted(public - documented)}"
    )


def test_readme_defaults_and_environment_names_match_runtime(tmp_path: Path) -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    args = parse_args([], cwd=tmp_path, environ={})
    expected_fragments = (
        f"| `--poll-interval <seconds>` | `{args.poll_interval}` |",
        f"| `--watch-max-retries <count>` | `{args.watch_max_retries}` |",
        f"| `--agent-output-max-chars <count>` | `{args.agent_output_max_chars}` |",
        f"| `--quota-safety-margin <seconds>` | `{args.quota_wait_policy.safety_margin_seconds}` |",
        f"| `--quota-max-wait <seconds>` | `{args.quota_wait_policy.maximum_wait_seconds}` |",
        f"| `--quota-max-auto-resumes <count>` | `{args.quota_wait_policy.maximum_auto_resumes}` |",
        f"| `--quota-heartbeat-interval <seconds>` | `{args.quota_wait_policy.heartbeat_interval_seconds}` |",
        "`claude`, `sonnet`, 1800s, `high`",
        "`codex`, `gpt-5.6-sol`, 1800s, `medium`",
        "erkanntes `agy`, `gemini-3.7-flash-high`, 1800s, `high`",
    )
    for fragment in expected_fragments:
        assert fragment in readme

    documented_names = set(re.findall(r"\bRUN_TASK_[A-Z][A-Z0-9_]*\b", readme))
    runtime_sources = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in ("src/cli.py", "src/agent_config.py")
    )
    dynamic_role_names = {
        f"RUN_TASK_{role.upper()}_{field.upper()}"
        for role in ("codex", "claude", "antigravity")
        for field in ("binary", "model", "timeout", "effort")
    }
    consumed_names = dynamic_role_names | {
        name for name in documented_names if name in runtime_sources
    }
    assert documented_names <= consumed_names, sorted(documented_names - consumed_names)


def test_readme_local_links_exist_and_help_examples_start() -> None:
    readme_path = ROOT / "README.md"
    readme = readme_path.read_text(encoding="utf-8")
    local_targets = []
    for target in re.findall(r"!?\[[^]]*\]\(([^)]+)\)", readme):
        if target.startswith(("http://", "https://", "#")):
            continue
        local_targets.append((readme_path.parent / target).resolve())
    assert local_targets
    assert all(path.is_file() for path in local_targets), local_targets

    run_task_cmd: list[str] | None = [str(ROOT / "run_task"), "--help"]
    if sys.platform == "win32":
        bash_bin = shutil.which("bash") or shutil.which("sh")
        run_task_cmd = [bash_bin, "./run_task", "--help"] if bash_bin else None

    commands = [
        cmd
        for cmd in (
            run_task_cmd,
            [sys.executable, str(ROOT / "src" / "cli.py"), "--help"],
        )
        if cmd is not None
    ]

    for command in commands:
        result = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stderr
        assert "state-v3 slice workflow" in result.stdout


def test_quickstart_is_linked_and_declares_the_safe_first_run() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    quickstart_path = ROOT / "Quickstart.md"
    quickstart = quickstart_path.read_text(encoding="utf-8")

    assert "[Quickstart.md](Quickstart.md)" in readme
    for heading in (
        "Voraussetzungen prüfen",
        "Zielrepository prüfen",
        "Eine Idee in die Inbox legen",
        "Optionalen Probelauf ausführen",
        "Automatischen Ablauf starten",
        "Optionale manuelle und formale Betriebsarten",
        "Angehaltenen Lauf fortsetzen",
        "Abschluss prüfen",
    ):
        assert re.search(rf"^## \d+\. {re.escape(heading)}$", quickstart, re.MULTILINE)

    for required in (
        "--dry-run",
        "--task-file task.md",
        "--resume",
        "--approve-gate",
        "ORCHESTRATOR_MODE: PLAN_ONLY",
        "ORCHESTRATOR_MODE: IMPLEMENT",
        "TARGET_BRANCH:",
        "TASK_SCOPE",
        "Claude läuft standardmäßig mit Sonnet und Effort `high`",
        "nano inbox/meine-idee.md",
        "run_task --watch",
        "pusht, mergt oder force-pusht niemals und schreibt die Historie nicht um",
        "`.orchestrator/state.json` und Checkpoints führen denselben Lauf",
    ):
        assert required in quickstart

    assert "Lege den Zielbranch vor dem Lauf selbst an" not in quickstart
    assert "--task-file Inbox/" not in quickstart

    local_targets = []
    for target in re.findall(r"!?\[[^]]*\]\(([^)]+)\)", quickstart):
        if target.startswith(("http://", "https://", "#")):
            continue
        local_targets.append((quickstart_path.parent / target).resolve())
    assert local_targets
    assert all(path.is_file() for path in local_targets), local_targets


def test_reference_documents_are_linked_current_and_locally_resolvable() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    architecture = REFERENCE_DOC_FILES[0].read_text(encoding="utf-8")
    comparison = REFERENCE_DOC_FILES[1].read_text(encoding="utf-8")

    for path in REFERENCE_DOC_FILES:
        relative = path.relative_to(ROOT).as_posix()
        assert f"]({relative})" in readme

    assert "**Zuletzt verifiziert:** 2026-08-16" in architecture
    assert "**Recherchestand:** 2026-08-13" in comparison
    assert "**Orchestrator-Funktionsstand:** 2026-08-16" in comparison

    for heading in (
        "Zweck",
        "Fachliches Problem",
        "Akteure und Verantwortlichkeiten",
        "Architekturprinzipien und Invarianten",
        "Logische Architektur",
        "Vollständiger Workflow",
        "Sicherheits- und Vertrauensgrenzen",
        "Persistierung, Fortsetzung und Idempotenz",
        "Bekannte Grenzen",
    ):
        assert re.search(rf"^## \d+\. {re.escape(heading)}$", architecture, re.MULTILINE)

    for product in (
        "OpenAI Codex",
        "Claude Code Agent Teams",
        "Google Antigravity 2.0",
        "GitHub Copilot Cloud Agent",
        "Cursor Cloud Agents",
        "OpenHands",
        "aider",
    ):
        assert product in comparison
    assert "ausschließlich offizielle Produktseiten und Dokumentationen" in comparison
    assert "in den geprüften offiziellen quellen nicht nachgewiesen" in comparison.lower()

    unresolved = []
    for document in REFERENCE_DOC_FILES:
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"!?\[[^]]*\]\(([^)]+)\)", text):
            if target.startswith(("http://", "https://", "#")):
                continue
            resolved = (document.parent / target).resolve()
            if not resolved.is_file():
                unresolved.append(f"{document.name}: {target}")
    assert not unresolved, "Broken local reference links:\n" + "\n".join(unresolved)


def test_readme_markers_match_the_active_root_contract() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    active_markers = (
        "SLICE_PLAN", "PLAN_READY", "IMPLEMENTATION_READY", "FINAL_REPORT_READY",
        "REVIEWER", "PLAN_APPROVAL", "SLICE_APPROVAL", "FINAL_APPROVAL",
        "NEW_FINDING", "FINDING_STATUS", "FINDING_RECLASSIFIED",
        "FINDING_RESPONSE", "REVIEW_EVIDENCE", "PRE_MORTEM",
        "STOP_REQUESTED", "STATUS: DONE",
    )
    for marker in active_markers:
        assert marker in readme
        assert marker in agents


def test_workflow_diagram_has_balanced_state_v3_topology() -> None:
    diagram = (ROOT / "workflow.puml").read_text(encoding="utf-8")
    assert diagram.count("@startuml") == diagram.count("@enduml") == 1
    assert diagram.count("partition ") == diagram.count("}") - 1
    assert len(re.findall(r"^\s*while\s*\(", diagram, re.MULTILINE)) == len(
        re.findall(r"^\s*endwhile\b", diagram, re.MULTILINE)
    )
    assert len(re.findall(r"^\s*if\s*\(", diagram, re.MULTILINE)) == len(
        re.findall(r"^\s*endif\b", diagram, re.MULTILINE)
    )
    for term in (
        "SLICE_PLAN", "PLAN_APPROVAL", "Codex", "Claude", "Antigravity",
        "canonical diff", "validation", "local Slice NN commit",
        "Branch-wide final review", "correction work unit", "STATUS: DONE",
    ):
        assert term in diagram


def test_user_docs_and_diagram_explain_structured_artifact_operations() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    quickstart = (ROOT / "Quickstart.md").read_text(encoding="utf-8")
    diagram = (ROOT / "workflow.puml").read_text(encoding="utf-8")

    common = (
        ".orchestrator/artifacts/<run-id>/records/",
        "structured-v1",
        "legacy-state-v3",
        "state.json",
        "head.json",
        "Auditansicht",
        "fail-closed",
    )
    for text in (readme, quickstart):
        assert all(fragment in text for fragment in common)
    assert "stille Migration" in readme
    assert "still migriert" in quickstart

    for fragment in (
        "authoritative append-only records",
        "State-v3 operational mirror",
        "Markdown fallback without migration",
        "Append validated records before any workflow decision",
        "prove semantic equality",
    ):
        assert fragment in diagram

    authority_sections = "\n".join(
        path.read_text(encoding="utf-8").split("## Structured artifact authority", 1)[1]
        for path in ROLE_FILES
    )
    for forbidden_claim in ("SQLite", "JSONL", "push", "merge", "external publication"):
        assert forbidden_claim not in authority_sections


def test_example_task_declares_every_required_boundary() -> None:
    example = (ROOT / "example-task.md").read_text(encoding="utf-8")
    for heading in (
        "Kontext", "Ziel", "Erlaubter Scope", "Anforderungen", "Akzeptanzkriterien",
        "Validierung", "Nicht-Scope", "Stopbedingungen",
    ):
        assert f"## {heading}" in example
    assert "Dateien außerhalb dieser Liste dürfen nicht bearbeitet werden" in example
    assert "Kein Push, Merge, Release oder Deployment" in example
