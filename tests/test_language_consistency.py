from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from audit_trail import strip_managed_audit_sections
from semantic_markdown import MANAGED_SECTION_HEADINGS, MANAGED_SECTION_KEYS
from cli import build_parser, parse_args

ROOT = Path(__file__).resolve().parents[1]
THIS_FILE = Path(__file__).resolve()
SOURCE_DIRS = (ROOT / "src", ROOT / "tests")
ROOT_FILES = (ROOT / "run_task",)
ROLE_FILES = (ROOT / "AGENTS.md", ROOT / "CLAUDE.md", ROOT / "CODEX.md")
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


def test_root_roles_share_the_state_v3_contract_and_retired_roles_are_gone() -> None:
    assert not (ROOT / "GEMINI.md").exists()
    assert not (ROOT / ("ANTI" + "GRAVITY.md")).exists()
    for path in ROLE_FILES:
        assert path.is_file(), f"missing role contract: {path.name}"
        text = path.read_text(encoding="utf-8")
        assert "AGENTS.md" in text or path.name == "AGENTS.md"
        assert "validation" in text.lower()
    shared = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for native_contract_term in (
        "native-agent-codex-request-v2",
        "native-agent-codex-result-v2",
        "native-agent-review-request-v2",
        "native-agent-review-result-v2",
        "request binding",
        "domain validation",
        "Plain-text result markers",
    ):
        assert native_contract_term in shared


def test_root_roles_share_structured_artifact_authority_contract() -> None:
    required = (
        "## Structured artifact authority",
        "Native JSON results are validated",
        ".orchestrator/artifacts/<run-id>/records/",
        "technical source of truth",
        "disposable projection used only to locate its `run_id`",
        "human audit view rather than a repair source",
        "state-projection reducer version",
        "foreign reducer semantics is rejected fail-closed",
        "exact cut of the canonical pre-work baseline append sequence",
        "Any non-prefix fact",
        "UNSUPPORTED-PROTOCOL",
        "never migrated, repaired, or used as a fallback",
    )
    sections = []
    for path in ROLE_FILES:
        text = path.read_text(encoding="utf-8")
        assert all(fragment in text for fragment in required), path.name
        assert "Pre-R1 chains without those records remain readable" not in text
        assert (
            "The initializer may finish only the exact canonical incomplete "
            "baseline prefix described above"
        ) in text
        section = text.split(required[0], 1)[1].split("\n## ", 1)[0].strip()
        sections.append(section)
    assert len(set(sections)) == 1


def test_root_roles_share_plan_only_transport_and_validation_tiers() -> None:
    headings = (
        "## PLAN_ONLY repository artifact",
        "## Validation tiers",
    )
    sections: dict[str, list[str]] = {heading: [] for heading in headings}
    for path in ROLE_FILES:
        text = path.read_text(encoding="utf-8")
        for fragment in (
            "creates or updates the exact repository file at `WORK_PLAN_PATH`",
            "`SLICE_PLAN` record for that path as a receipt",
            "correct” explicitly means create the missing file",
            "configured `--agents-file` (root `AGENTS.md` by default)",
            "does not append `CLAUDE.md` or `CODEX.md`",
            "Codex CLI may also discover `AGENTS.md`",
            "Every review with a `ReviewPacket` uses its manifest-selected",
            "Every review without a packet uses the full read-only Git snapshot",
            "git ls-files --cached --others --exclude-standard",
            "direct `IMPLEMENT` flows without an approved work plan",
            "A root role file is manifest-dependent only when a packet exists",
            "tests marked `crash_harness`",
            "python3 -m pytest tests/test_crash_harness.py -v",
            "before the branch-wide final review",
            "The orchestrator does not select or enforce this standalone command",
            "The operator treats a merge as permitted only when",
            "never sampled or reduced",
        ):
            assert fragment in text, f"{path.name}: {fragment}"
        for heading in headings:
            section = text.split(heading, 1)[1].split("\n## ", 1)[0].strip()
            sections[heading].append(section)
    assert all(len(set(values)) == 1 for values in sections.values())


def test_crash_harness_is_complete_but_not_in_default_slice_validation() -> None:
    repository_config = tomllib.loads(
        (ROOT / "orchestrator.toml").read_text(encoding="utf-8")
    )
    default_command = repository_config["validation"]["default_command"]
    assert default_command == [
        "python3",
        "-m",
        "pytest",
        "tests/",
        "-v",
        "-m",
        "not crash_harness",
    ]
    pytest_config = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert any(
        marker.startswith("crash_harness:")
        for marker in pytest_config["tool"]["pytest"]["ini_options"]["markers"]
    )
    harness_source = (ROOT / "tests" / "test_crash_harness.py").read_text(
        encoding="utf-8"
    )
    assert "pytestmark = pytest.mark.crash_harness" in harness_source


def test_record_authority_module_headers_match_the_root_contract() -> None:
    required_by_module = {
        "artifact_bridge.py": ("technical authority", "disposable run locator"),
        "artifact_migration.py": ("only technical authority", "disposable run locator"),
        "artifact_replay.py": ("authoritative structured record chain", "reducer version"),
    }
    for filename, fragments in required_by_module.items():
        source = (ROOT / "src" / filename).read_text(encoding="utf-8")
        header = source.split('"""', 2)[1]
        assert all(fragment in header for fragment in fragments), filename


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


def test_quickstart_run_task_commands_use_only_public_long_cli_options() -> None:
    quickstart = (ROOT / "Quickstart.md").read_text(encoding="utf-8")
    lines = quickstart.splitlines()
    commands: list[str] = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped.startswith(
            ("run_task", "python -m orchestrator", "python3 -m orchestrator")
        ):
            index += 1
            continue
        parts = [stripped]
        while parts[-1].endswith("\\") and index + 1 < len(lines):
            index += 1
            parts.append(lines[index].strip())
        commands.append(" ".join(parts))
        index += 1
    documented = set(
        re.findall(
            r"(?<![A-Za-z0-9_])--[a-z][a-z0-9-]+", "\n".join(commands)
        )
    )
    public = {
        option
        for action in build_parser()._actions
        for option in action.option_strings
        if option.startswith("--")
    }
    assert documented <= public, (
        f"Quickstart-only options: {sorted(documented - public)}"
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
        f"| `--transient-retry-initial-delay <seconds>` | `{args.transient_retry_policy.initial_delay_seconds}` |",
        f"| `--transient-retry-max-delay <seconds>` | `{args.transient_retry_policy.maximum_delay_seconds}` |",
        f"| `--transient-retry-max-auto-resumes <count>` | `{args.transient_retry_policy.maximum_auto_resumes}` |",
        "`claude`, `sonnet`, 1800s, `high`",
        "`codex`, `gpt-5.6-sol`, 1800s, `medium`",
        "`RUN_TASK_QUOTA_AUTO_RESUME`",
        "`RUN_TASK_QUOTA_SAFETY_MARGIN`",
        "`RUN_TASK_QUOTA_MAX_WAIT`",
        "`RUN_TASK_QUOTA_MAX_AUTO_RESUMES`",
        "`RUN_TASK_QUOTA_HEARTBEAT_INTERVAL`",
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
        for role in ("codex", "claude")
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
        "Google " + "Anti" + "gravity 2.0",
        "GitHub Copilot Cloud Agent",
        "Cursor Cloud Agents",
        "OpenHands",
        "aider",
    ):
        assert product in comparison
    assert "ausschließlich offizielle Produktseiten und Dokumentationen" in comparison
    assert "in den geprüften offiziellen quellen nicht nachgewiesen" in comparison.lower()
    assert "kein Bestandteil dieses Orchestrators" in comparison

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


def test_readme_native_json_contract_matches_the_active_root_contract() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    native_contract_terms = (
        "native-agent-codex-request-v2",
        "native-agent-codex-result-v2",
        "native-agent-review-request-v2",
        "native-agent-review-result-v2",
        "native JSON",
    )
    for term in native_contract_terms:
        assert term in readme
        assert term in agents


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
        "SLICE_PLAN", "PLAN_APPROVAL", "Codex", "Claude",
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
        "structured-v2",
        "UNSUPPORTED-PROTOCOL",
        "state.json",
        "head.json",
        "Auditansicht",
        "fail-closed",
    )
    for text in (readme, quickstart):
        assert all(fragment in text for fragment in common)
    assert "stille Migration" in readme
    assert "weder migriert" in quickstart

    for fragment in (
        "authoritative append-only records",
        "State-v3 operational mirror",
        "UNSUPPORTED-PROTOCOL",
        "Append validated records before any workflow decision",
        "prove semantic equality",
    ):
        assert fragment in diagram

    allowed_merge_claim = (
        "The operator treats a merge as permitted only when that same green "
        "HEAD-bound evidence exists, or runs a fresh complete proof after HEAD changes."
    )
    for path in ROLE_FILES:
        shared_contract_tail = path.read_text(encoding="utf-8").split(
            "## Structured artifact authority", 1
        )[1]
        assert shared_contract_tail.count(allowed_merge_claim) == 1, path.name
        guarded_tail = shared_contract_tail.replace(allowed_merge_claim, "")
        for forbidden_claim in (
            "SQLite",
            "JSONL",
            "push",
            "merge",
            "external publication",
        ):
            assert forbidden_claim not in guarded_tail, (
                f"{path.name}: {forbidden_claim}"
            )


_RETIREMENT_EVIDENCE_PREFIX = "antigravity-endgueltige-entfernung-"
_RETIREMENT_ARCHIVE = Path("docs/internal/archive")
_PROVIDER_NAMES = ("codex", "claude")
_PROVIDER_COUPLING_BASELINE = (
    ROOT / "tests/fixtures/provider-name-coupling-baseline-v1.json"
)


def _matches_config_path(root: Path, path: Path, pattern: str) -> bool:
    candidate = path.relative_to(root).as_posix()
    alternatives = (pattern, pattern.replace("/**/", "/"))
    return any(
        Path(candidate).match(item)
        or re.fullmatch(
            re.escape(item).replace(r"\*\*", ".*").replace(r"\*", "[^/]*"),
            candidate,
        )
        is not None
        for item in alternatives
    )


def _provider_productive_files(root: Path = ROOT) -> tuple[Path, ...]:
    with (root / "orchestrator.toml").open("rb") as handle:
        path_config = tomllib.load(handle)["paths"]
    generated_patterns = tuple(path_config.get("generated", ()))
    files: set[Path] = set()
    resolved_root = root.resolve()
    for pattern in path_config["productive"]:
        glob_pattern = pattern + "/*" if pattern.endswith("/**") else pattern
        for path in root.glob(glob_pattern):
            resolved = path.resolve()
            if not resolved.is_file():
                continue
            resolved.relative_to(resolved_root)
            files.add(resolved)
    return tuple(
        sorted(
            (
                path
                for path in files
                if not any(
                    _matches_config_path(resolved_root, path, pattern)
                    for pattern in generated_patterns
                )
            ),
            key=lambda path: path.relative_to(resolved_root).as_posix(),
        )
    )


def _provider_name_counts(text: str) -> dict[str, int]:
    text = "\n".join(
        line for line in text.splitlines() if "# allowlist:provider" not in line
    )
    return {
        name: sum(1 for _match in re.finditer(re.escape(name), text, re.I))
        for name in _PROVIDER_NAMES
    }


def _provider_coupling_hits(
    root: Path = ROOT, baseline_path: Path = _PROVIDER_COUPLING_BASELINE
) -> list[str]:
    baseline_document = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert baseline_document["schema_version"] == "provider-name-coupling-baseline-v1"
    baseline = baseline_document["counts"]
    assert list(baseline) == sorted(baseline)
    assert all(
        set(counts) <= set(_PROVIDER_NAMES)
        and counts
        and all(isinstance(value, int) and value > 0 for value in counts.values())
        for counts in baseline.values()
    )
    resolved_root = root.resolve()
    files = _provider_productive_files(root)
    current_paths = {
        path.relative_to(resolved_root).as_posix() for path in files
    }
    hits = [
        f"stale provider-name baseline path: {path}"
        for path in baseline
        if path not in current_paths
    ]
    for path in files:
        relative = path.relative_to(resolved_root).as_posix()
        actual = _provider_name_counts(path.read_text(encoding="utf-8"))
        expected = baseline.get(relative, {})
        for name in _PROVIDER_NAMES:
            limit = expected.get(name, 0)
            if actual[name] > limit:
                hits.append(
                    f"{relative}: {name} baseline={limit} actual={actual[name]}"
                )
    return hits


def _retirement_active_files(root: Path = ROOT) -> tuple[Path, ...]:
    """Inventory every active path class declared by orchestrator.toml."""
    with (root / "orchestrator.toml").open("rb") as handle:
        path_config = tomllib.load(handle)["paths"]
    active_patterns = tuple(
        pattern
        for category in ("productive", "tests", "documentation")
        for pattern in path_config[category]
    )
    generated_patterns = tuple(path_config.get("generated", ()))
    files: set[Path] = set()
    for pattern in active_patterns:
        # pathlib's terminal ** yields directories; append * to include all
        # declared descendants while keeping the repository pattern canonical.
        glob_pattern = pattern + "/*" if pattern.endswith("/**") else pattern
        files.update(path for path in root.glob(glob_pattern) if path.is_file())

    def relative(path: Path) -> Path:
        return path.relative_to(root)

    def matches(path: Path, pattern: str) -> bool:
        candidate = relative(path).as_posix()
        alternatives = (pattern, pattern.replace("/**/", "/"))
        return any(
            Path(candidate).match(item)
            or re.fullmatch(
                re.escape(item)
                .replace(r"\*\*", ".*")
                .replace(r"\*", "[^/]*"),
                candidate,
            )
            is not None
            for item in alternatives
        )

    return tuple(
        sorted(
            (
                path
                for path in files
                if path != THIS_FILE
                and not any(matches(path, pattern) for pattern in generated_patterns)
                and _RETIREMENT_ARCHIVE not in relative(path).parents
                and not (
                    relative(path).parent == Path("docs/internal")
                    and relative(path).name.startswith(_RETIREMENT_EVIDENCE_PREFIX)
                )
            ),
            key=lambda path: relative(path).as_posix(),
        )
    )


_ALLOWED_MARKET_ANTIGRAVITY_LINES = frozenset(
    {
        "Der Dual-Agent Task Orchestrator besetzt eine engere Kategorie als die meisten Produkte in diesem Vergleich. Codex, Claude Code, Google Antigravity, GitHub Copilot, Cursor, OpenHands und aider stellen primär einen Agenten, einen Agenten-Workspace, eine Entwicklungsoberfläche oder eine Agentenplattform bereit. Dieses Projekt ist eine lokale Workflow-Steuerungsebene, die Codex und Claude in festen Rollen aufruft und deterministische Evidenz, unabhängige Reviews, fortsetzbare Gates und eine exakte lokale Commit-Autorisierung ergänzt.",
        "| Google Antigravity 2.0 | Eigenständige Agenten-Kommandozentrale und CLI-/IDE-Ökosystem | Externes Vergleichsprodukt; unterstützt Projekte, Worktrees und Subagenten, ist aber kein Bestandteil dieses Orchestrators. |",
        "| Google Antigravity 2.0 | Eigenständige App, CLI und IDE-Ökosystem | Projektbezogene lokale/Worktree-Ausführung plus Managed-Agent-Optionen | Mehrere Unterhaltungen, dynamische Subagenten, Custom Agents, Skills und MCP | Projekt- und subagentenübergreifend integriert | Projekt-/Worktree-Änderungen; externe SCM-Aktionen abhängig von der Oberfläche |",
        "| Fähigkeit | Dual-Agent Orchestrator | Codex | Claude Teams | Antigravity | GitHub Copilot | Cursor Cloud | OpenHands | aider |",
        "### 6.3 Google Antigravity 2.0",
        "Google positioniert Antigravity 2.0 als eigenständige Kommandozentrale für synchrone und asynchrone Agenten. Projekte können mehrere Ordner umfassen, Git-Worktrees verwenden, begrenzte Einstellungen und Berechtigungen anwenden und dynamische Subagenten ausführen. Das breitere Ökosystem enthält CLI- und IDE-Oberflächen, Browserinteraktion, Artefakte, geplante Aufgaben, Skills, Hooks und MCP-Integration.",
        "Antigravity bietet damit eine reichhaltige Betreiberoberfläche, Parallelität und interaktive Artefakte. Es ist in dieser Tabelle ausschließlich ein externes Vergleichsprodukt und gehört weder zur Laufzeit noch zur Review- oder Freigabetopologie des Dual-Agent Orchestrators.",
        "Offizielle Quellen: [Antigravity-2.0-Überblick](https://antigravity.google/docs/overview), [Antigravity-2.0-Funktionen](https://antigravity.google/docs/features?app=antigravity), [Antigravity-CLI-Agenten](https://antigravity.google/docs/cli/commands/agents?hl=en), [Google-Entwicklerankündigung](https://developers.googleblog.com/build-with-google-antigravity-our-new-agentic-development-platform/).",
        "| Ausgereifte Multi-Agenten-Desktop-Kommandozentrale und parallele Worktrees | OpenAI Codex App oder Google Antigravity 2.0 |",
        "| Paralleler Implementierungsdurchsatz | Codex, Claude Teams, Antigravity, Cursor oder ein OpenHands-basierter Entwurf |",
    }
)
_ALLOWED_INTERNAL_RETIREMENT_LINK_LINES = frozenset(
    {
        "- [Arbeitsplan zur endgültigen Providerbereinigung](antigravity-endgueltige-entfernung-arbeitsplan.md)",
        "[`archive/antigravity-retirement/`](archive/antigravity-retirement/) enthält",
    }
)


def _allowed_retirement_reference_line(path: Path, line: str) -> bool:
    stripped = line.strip()
    if path == ROOT / "docs/reference/market-comparison.md":
        return stripped in _ALLOWED_MARKET_ANTIGRAVITY_LINES
    if path == ROOT / "docs/internal/README.md":
        return stripped in _ALLOWED_INTERNAL_RETIREMENT_LINK_LINES
    return False


def _retirement_hits(path: Path, text: str) -> list[str]:
    if path.suffix.casefold() == ".md":
        text = strip_managed_audit_sections(text)
    text = "\n".join(
        line
        for line in text.splitlines()
        if "# retirement-negative-control" not in line
        and not _allowed_retirement_reference_line(path, line)
    )
    retired = (
        "anti" + "gravity",
        "agy" + ".exe",
        "ANTI" + "GRAVITY_",
        "orchestrator-artifact-" + "v1",
        "native-agent-codex-request-" + "v1",
        "native-agent-codex-result-" + "v1",
        "native-agent-review-request-" + "v1",
        "native-agent-review-result-" + "v1",
        "native-codex-" + "v1",
        "native-claude-review-" + "v1",
    )
    lowered = text.casefold()
    hits = [token for token in retired if token.casefold() in lowered]
    if re.search(r"(?<![A-Za-z0-9_])" + "agy" + r"(?![A-Za-z0-9_])", text, re.I):
        hits.append("standalone retired binary")
    if re.search(
        r"(?:"
        r"(?<![A-Za-z0-9_])A-(?:[0-9]+|\*)(?![A-Za-z0-9_])"
        r"|[\"']A-[\"']"
        r"|\^\[CA\]-"
        r"|\[CA\].{0,80}(?:finding|-[([])"
        r")",
        text,
        re.I | re.S,
    ):
        hits.append("retired finding namespace")
    if re.search(r"\belse\s+[\"']A[\"']", text):
        hits.append("retired finding prefix fallback")
    if re.search(
        r"\bfor\s+[A-Za-z_][A-Za-z0-9_]*\s+in\s+\(\s*[\"']C[\"']\s*,\s*[\"']A[\"']\s*\)",
        text,
    ):
        hits.append("retired finding prefix inventory")
    label = (
        path.relative_to(ROOT).as_posix()
        if path.is_relative_to(ROOT)
        else path.as_posix()
    )
    return [f"{label}: {hit}" for hit in hits]


def test_provider_name_coupling_stays_at_or_below_fixed_baseline() -> None:
    hits = _provider_coupling_hits()
    assert not hits, "Provider-name coupling increased:\n" + "\n".join(hits)


def test_provider_name_counting_is_literal_embedded_case_insensitive_and_nonoverlapping() -> None:
    assert _provider_name_counts("xCoDeXcodex CLAUDEclaude") == {
        "codex": 2,
        "claude": 2,
    }
    assert _provider_name_counts("codexcodex") == {"codex": 2, "claude": 0}


def test_provider_name_counting_ignores_only_explicitly_allowlisted_lines() -> None:
    assert _provider_name_counts(
        '"claude-review"  # allowlist:provider -- canonical marker\n'
        '"codex-responses"\n'
    ) == {"codex": 1, "claude": 0}


def test_provider_name_ratchet_rejects_only_a_temporary_copy_increase(
    tmp_path: Path,
) -> None:
    real_config = ROOT / "orchestrator.toml"
    real_target = ROOT / "src/cli.py"
    real_paths = (real_config, _PROVIDER_COUPLING_BASELINE, real_target)
    real_bytes = {path: path.read_bytes() for path in real_paths}

    copied_config = tmp_path / "orchestrator.toml"
    copied_baseline = tmp_path / "provider-name-coupling-baseline-v1.json"
    copied_target = tmp_path / "src/cli.py"
    copied_target.parent.mkdir(parents=True)
    shutil.copyfile(real_config, copied_config)
    shutil.copyfile(_PROVIDER_COUPLING_BASELINE, copied_baseline)
    shutil.copyfile(real_target, copied_target)

    copied_config.write_text(
        '[paths]\nproductive = ["src/cli.py"]\ngenerated = []\n',
        encoding="utf-8",
    )
    original_counts = _provider_name_counts(
        copied_target.read_text(encoding="utf-8")
    )
    original_count = original_counts["codex"]
    copied_baseline.write_text(
        json.dumps(
            {
                "schema_version": "provider-name-coupling-baseline-v1",
                "counts": {
                    "src/cli.py": {
                        name: count
                        for name, count in original_counts.items()
                        if count > 0
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with copied_target.open("a", encoding="utf-8") as handle:
        handle.write("\n# CoDeX temporary ratchet control\n")

    hits = _provider_coupling_hits(tmp_path, copied_baseline)
    assert hits == [
        f"src/cli.py: codex baseline={original_count} actual={original_count + 1}"
    ]
    assert all(path.read_bytes() == content for path, content in real_bytes.items())


def test_provider_name_ratchet_accepts_decrease_and_rejects_a_new_file_first_hit(
    tmp_path: Path,
) -> None:
    (tmp_path / "orchestrator.toml").write_text(
        '[paths]\nproductive = ["src/**/*.py"]\ngenerated = []\n',
        encoding="utf-8",
    )
    source = tmp_path / "src/existing.py"
    source.parent.mkdir(parents=True)
    source.write_text("codex\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "schema_version": "provider-name-coupling-baseline-v1",
                "counts": {"src/existing.py": {"codex": 2}},
            }
        ),
        encoding="utf-8",
    )
    assert _provider_coupling_hits(tmp_path, baseline) == []

    new_file = tmp_path / "src/new.py"
    new_file.write_text("embedded-CLAUDE-name\n", encoding="utf-8")
    assert _provider_coupling_hits(tmp_path, baseline) == [
        "src/new.py: claude baseline=0 actual=1"
    ]


def test_provider_name_inventory_excludes_generated_productive_matches(
    tmp_path: Path,
) -> None:
    (tmp_path / "orchestrator.toml").write_text(
        '[paths]\nproductive = ["src/**/*.py"]\n'
        'generated = ["src/generated/**"]\n',
        encoding="utf-8",
    )
    generated = tmp_path / "src/generated/cache.py"
    generated.parent.mkdir(parents=True)
    generated.write_text("codex claude\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "schema_version": "provider-name-coupling-baseline-v1",
                "counts": {},
            }
        ),
        encoding="utf-8",
    )
    assert _provider_productive_files(tmp_path) == ()
    assert _provider_coupling_hits(tmp_path, baseline) == []


def test_retirement_guard_rejects_every_active_retired_reference() -> None:
    hits = [
        hit
        for path in _retirement_active_files()
        for hit in _retirement_hits(path, path.read_text(encoding="utf-8"))
    ]
    assert not hits, "Retired active references found:\n" + "\n".join(hits)


def test_retirement_guard_ignores_only_managed_audit_projection() -> None:
    retired_name = "anti" + "gravity"
    internal_document = ROOT / "docs" / "internal" / "synthetic-audit.md"
    projected_lines = ["# Slice 01 – Active evidence", ""]
    for key in MANAGED_SECTION_KEYS:
        projected_lines.extend(
            (
                f"## {MANAGED_SECTION_HEADINGS[key]}",
                "",
                f"<!-- audit:{key}:begin -->",
                f"pytest negative control: {retired_name}"
                if key == "validation-attestation"
                else "green projection",
                f"<!-- audit:{key}:end -->",
                "",
            )
        )
    projected = "\n".join(projected_lines)

    assert _retirement_hits(internal_document, projected) == []
    assert _retirement_hits(
        internal_document,
        f"# Active declaration\n\n{retired_name} is an active reviewer.\n",
    ) == ["docs/internal/synthetic-audit.md: antigravity"]


@pytest.mark.parametrize(
    ("relative_path", "synthetic"),
    (
        (
            "schemas/native-agent-codex-result-v2.schema.json",
            '{"pattern": "^[CA]-(0[1-9]|[1-9][0-9]*)$"}',
        ),
        ("src/workflow.py", 'LEGACY_FINDING = "A-02"'),
        ("src/workflow.py", "# reviewer may raise A-* findings"),
        (
            "src/prompts.py",
            'prefix = "C" if reviewer == "claude" else "A"',
        ),
        (
            "src/orchestrator.py",
            'for prefix in ("C", "A")',
        ),
        ("run_task", "RUN_TASK_ANTIGRAVITY_BINARY=agy.exe"),
        (
            "docs/reference/architecture-and-domain-concept.md",
            "Antigravity ist die dritte Prozessrolle und finaler Reviewer.",
        ),
        ("tests/fixtures/legacy.txt", "reviewer finding A-02"),
        ("OPERATIONS.md", "Antigravity ist ein produktiver Reviewer."),
    ),
)
def test_retirement_guard_inventory_negative_controls(
    tmp_path: Path, relative_path: str, synthetic: str
) -> None:
    (tmp_path / "orchestrator.toml").write_text(
        (ROOT / "orchestrator.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(synthetic, encoding="utf-8")

    active_files = _retirement_active_files(tmp_path)

    assert target in active_files
    assert _retirement_hits(target, synthetic)


def test_market_comparison_allows_only_exact_external_product_lines() -> None:
    path = ROOT / "docs/reference/market-comparison.md"
    current = path.read_text(encoding="utf-8")
    assert not _retirement_hits(path, current)
    actual = {
        line.strip()
        for line in current.splitlines()
        if "antigravity" in line.casefold()
    }
    assert actual == _ALLOWED_MARKET_ANTIGRAVITY_LINES


@pytest.mark.parametrize(
    ("current_line", "retired_line"),
    (
        (
            "| Google Antigravity 2.0 | Eigenständige Agenten-Kommandozentrale und CLI-/IDE-Ökosystem | Externes Vergleichsprodukt; unterstützt Projekte, Worktrees und Subagenten, ist aber kein Bestandteil dieses Orchestrators. |",
            "| Google Antigravity 2.0 | Eigenständige Agenten-Kommandozentrale und CLI-/IDE-Ökosystem | Stellt den hier verwendeten unabhängigen Reviewer bereit und unterstützt Projekte, Worktrees und Subagenten. |",
        ),
        (
            "| Dual-Agent Orchestrator | Python-CLI und FIFO-Watcher | Ziel-Worktree plus temporäre lokale Reviewerkopie | Feste Rollen Codex → Claude; Rollen-CLIs unabhängig konfiguriert | Slices sequenziell; providerinterne Parallelität außerhalb seiner Kontrolle | Ausschließlich verifizierte lokale Slice-Commits |",
            "| Dual-Agent Orchestrator | Python-CLI und FIFO-Watcher | Ziel-Worktree plus temporäre lokale Reviewerkopie | Feste Rollen Codex → Claude → Antigravity; Rollen-CLIs unabhängig konfiguriert | Slices sequenziell; providerinterne Parallelität außerhalb seiner Kontrolle | Ausschließlich verifizierte lokale Slice-Commits |",
        ),
        (
            "Antigravity bietet damit eine reichhaltige Betreiberoberfläche, Parallelität und interaktive Artefakte. Es ist in dieser Tabelle ausschließlich ein externes Vergleichsprodukt und gehört weder zur Laufzeit noch zur Review- oder Freigabetopologie des Dual-Agent Orchestrators.",
            "Antigravity bietet damit eine reichhaltige Betreiberoberfläche, Parallelität und interaktive Artefakte. In diesem Projekt wird es bewusst auf einen unabhängigen, schreibgeschützten Abschlussreviewer nach Claude begrenzt.",
        ),
        (
            "| Fähigkeit | Dual-Agent Orchestrator | Codex | Claude Teams | Antigravity | GitHub Copilot | Cursor Cloud | OpenHands | aider |",
            "| Dual-Agent Orchestrator | Rollen | Codex, Claude und Antigravity sind die drei aktiven Prozessrollen dieses Orchestrators |",
        ),
    ),
)
def test_market_comparison_rejects_retired_role_lines(
    current_line: str, retired_line: str
) -> None:
    path = ROOT / "docs/reference/market-comparison.md"
    current = path.read_text(encoding="utf-8")
    assert current_line in current

    mutated = current.replace(current_line, retired_line, 1)

    assert _retirement_hits(path, mutated)


def test_internal_archive_link_cannot_hide_a_process_role() -> None:
    path = ROOT / "docs/internal/README.md"
    current = path.read_text(encoding="utf-8")
    link = "[`archive/antigravity-retirement/`](archive/antigravity-retirement/) enthält"
    assert link in current

    mutated = current.replace(
        link,
        link + " Antigravity ist die dritte aktive Prozessrolle.",
        1,
    )

    assert _retirement_hits(path, mutated)


def test_runtime_and_tests_do_not_read_non_authoritative_archives() -> None:
    archive_token = "docs/internal/" + "archive/"
    hits = []
    for root in (ROOT / "src", ROOT / "tests"):
        for path in root.rglob("*.py"):
            if path == THIS_FILE:
                continue
            if archive_token in path.read_text(encoding="utf-8"):
                hits.append(str(path.relative_to(ROOT)))
    assert not hits


def test_example_task_declares_every_required_boundary() -> None:
    example = (ROOT / "example-task.md").read_text(encoding="utf-8")
    for heading in (
        "Kontext", "Ziel", "Erlaubter Scope", "Anforderungen", "Akzeptanzkriterien",
        "Validierung", "Nicht-Scope", "Stopbedingungen",
    ):
        assert f"## {heading}" in example
    assert "Dateien außerhalb dieser Liste dürfen nicht bearbeitet werden" in example
    assert "Kein Push, Merge, Release oder Deployment" in example
