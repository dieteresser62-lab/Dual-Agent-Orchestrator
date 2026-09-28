# README-Prüfinventar: Rollenneutralität v3

Jede README-Überschrift ist an einen ausführbaren Anker und einen automatischen Nachweis gebunden. Die Beispiele in README, Quickstart und Einrichtung werden zusätzlich durch `tests/test_language_consistency.py` geprüft. Der echte Abschlusslauf in einem Zielrepository ist ein nachgelagerter Operatornachweis.

| README-Abschnitt | Maßgebliches Symbol | Nachweis |
|---|---|---|
| `# Dual-Agent Task Orchestrator` | `src/workflow_run_setup.py::_context` | `tests/test_workflow_run_setup.py::test_context_matches_every_workflow_context_field` |
| `## Überblick` | `src/workflow_run_setup.py::_context` | `tests/test_workflow_run_setup.py::test_context_matches_every_workflow_context_field` |
| `### Warum?` | `src/workflow_run_setup.py::_context` | `tests/test_workflow_run_setup.py::test_context_matches_every_workflow_context_field` |
| `### So läuft eine Aufgabe ab` | `src/workflow_run_setup.py::_context` | `tests/test_workflow_run_setup.py::test_context_matches_every_workflow_context_field` |
| `### Was ihn von anderen Ansätzen unterscheidet` | `src/workflow_run_setup.py::_context` | `tests/test_workflow_run_setup.py::test_context_matches_every_workflow_context_field` |
| `### Hintergrund` | `src/cli.py::parse_args` | `tests/test_language_consistency.py::test_readme_local_links_exist_and_help_examples_start` |
| `### Stand und Grenzen` | `src/workflow_run_setup.py::_context` | `tests/test_workflow_run_setup.py::test_context_matches_every_workflow_context_field` |
| `### Ausprobieren` | `src/cli.py::parse_args` | `tests/test_language_consistency.py::test_readme_local_links_exist_and_help_examples_start` |
| `## Ablauf im Detail` | `src/workflow_run_setup.py::_context` | `tests/test_workflow_run_setup.py::test_context_matches_every_workflow_context_field` |
| `## Referenzdokumentation` | `src/cli.py::parse_args` | `tests/test_language_consistency.py::test_readme_local_links_exist_and_help_examples_start` |
| `## Voraussetzungen und unterstützte Plattformen` | `src/agent_runtime.py::create_read_only_reviewer_workspace` | `tests/test_agent_runtime.py::test_read_only_reviewer_workspace_blocks_writes_and_preserves_source` |
| `## Schnellstart` | `src/cli.py::parse_args` | `tests/test_language_consistency.py::test_readme_local_links_exist_and_help_examples_start` |
| `## Informeller Inbox- und formaler Aufgabenbetrieb` | `src/workflow_run_setup.py::_context` | `tests/test_workflow_run_setup.py::test_context_matches_every_workflow_context_field` |
| `## Zustand, Checkpoints, Logs und Auditdokumente` | `src/artifact_replay.py::replay_artifacts` | `tests/test_artifact_replay.py::test_replay_projects_run_identity_and_profiles_without_external_state` |
| `## Validierung und Reviewisolation` | `src/agent_runtime.py::create_read_only_reviewer_workspace` | `tests/test_agent_runtime.py::test_read_only_reviewer_workspace_blocks_writes_and_preserves_source` |
| `## Gates, Findings und Fortsetzung` | `src/artifact_replay.py::replay_artifacts` | `tests/test_artifact_replay.py::test_replay_projects_run_identity_and_profiles_without_external_state` |
| `## Lokale Commits und externe Git-Aktionen` | `src/workflow_completion.py::complete_chain` | `tests/test_workflow_completion.py::test_clean_completion_archives_branch_additions_and_merges` |
| `## Watch-Modus` | `src/cli.py::run_cli` | `tests/test_cli.py::test_quota_wait_policy_defaults_and_explicit_disable` |
| `## Probeläufe` | `src/cli.py::parse_args` | `tests/test_cli.py::test_readme_cli_defaults_match_resolved_parser_contract` |
| `## CLI-Referenz` | `src/cli.py::parse_args` | `tests/test_cli.py::test_readme_cli_defaults_match_resolved_parser_contract` |
| `### Kern- und Zustandsoptionen` | `src/artifact_replay.py::replay_artifacts` | `tests/test_artifact_replay.py::test_replay_projects_run_identity_and_profiles_without_external_state` |
| `### Validierung, Probelauf und Quota` | `src/cli.py::parse_args` | `tests/test_cli.py::test_readme_cli_defaults_match_resolved_parser_contract` |
| `### Agentenausgabe und Rollenkonfiguration` | `src/cli.py::parse_args` | `tests/test_cli.py::test_readme_cli_defaults_match_resolved_parser_contract` |
| `### Watch- und Loggingoptionen` | `src/cli.py::run_cli` | `tests/test_cli.py::test_quota_wait_policy_defaults_and_explicit_disable` |
| `## Konfiguration` | `src/cli.py::parse_args` | `tests/test_cli.py::test_readme_cli_defaults_match_resolved_parser_contract` |
| `## Agentenanweisungen und nativer Ausgabevertrag` | `src/workflow_requests.py::native_implementer_request` | `tests/test_workflow_requests.py::test_implementer_request_delivers_the_language_rule_as_bound_policy` |
| `## Exitcodes` | `src/cli.py::parse_args` | `tests/test_cli.py::test_readme_cli_defaults_match_resolved_parser_contract` |
| `## Optionaler globaler Befehl` | `src/cli.py::parse_args` | `tests/test_cli.py::test_readme_cli_defaults_match_resolved_parser_contract` |
| `## Verifikation` | `src/workflow_run_setup.py::_context` | `tests/test_workflow_run_setup.py::test_context_matches_every_workflow_context_field` |
| `### Diagnosewerkzeuge` | `src/workflow_run_setup.py::_context` | `tests/test_workflow_run_setup.py::test_context_matches_every_workflow_context_field` |

## Ergänzende Querverweise

- Intake und Handoff: `src/cli.py::run_cli`, `src/workflow_run_setup.py::_context`, `Quickstart.md`.
- Records, Gates, Resume und Fehler: `src/artifact_replay.py`, `docs/reference/ablauf-des-orchestrators.md`.
- Completion und Hook: `src/workflow_completion.py::complete_chain`.
- Watch und Quota: `src/cli.py::run_cli`, `Quickstart.md`.
- CLI, TOML, Isolation und Versionen: `src/cli.py::parse_args`, `src/agent_runtime.py::run_agent`, `docs/reference/einrichtung.md`.
- Fachmodell und Ablauf: `docs/reference/architecture-and-domain-concept.md`, `workflow.puml`.

Neue Rollenbesetzungen oder Transportprofile gehören in Task C; eine ausgebaute Reviewisolation gehört in Task B.
