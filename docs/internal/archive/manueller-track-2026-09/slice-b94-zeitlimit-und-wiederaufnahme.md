# B94 — Zeitlimit und Wiederaufnahme

## Exakter Änderungspfad

- `AGENTS.md`
- `CLAUDE.md`
- `CODEX.md`
- `docs/internal/slice-b94-zeitlimit-und-wiederaufnahme.md`
- `schemas/orchestrator-artifact-v2.schema.json`
- `src/artifact_models.py`
- `src/artifact_replay.py`
- `src/orchestrator.py`
- `src/workflow.py`
- `src/workflow_failure_recording.py`
- `src/workflow_persistence.py`
- `src/workflow_state.py`
- `tests/test_artifact_models.py`
- `tests/test_orchestrator_resilience_evidence.py`
- `tests/test_orchestrator_runtime.py`
- `tests/test_payload_dispatch.py`
- `tests/test_workflow.py`

## Schema-Randbedingung

Der bestehende Recordtyp `GateDecision` erhält als einzige Schemaerweiterung das
optionale Feld `invocation_id`. Alte Records ohne dieses Feld bleiben gültig.
Recordtypen, Schema-Version und Reducer-Version bleiben unverändert.

`src/cli.py` wird nicht geändert: Die vorhandene Kombination `--resume
--approve-gate --gate-rationale` ist der explizite Operatorweg. Deshalb bleibt
auch `tests/fixtures/cli-argument-evaluation-corpus-v1.json` unverändert.

## Akzeptanzkriterien

- Zeitlimits werden innerhalb der konfigurierten transienten Retry-Grenze
  automatisch wiederholt.
- Nach Ausschöpfung nennt die Diagnose die Zahl der physischen Versuche und der
  Failure-Record trägt `resumable_halt` statt `transient`.
- `QUOTA-RESUME-DIFF` bleibt ohne Entscheidung geschlossen und nennt Pfade sowie
  den exakten CLI-Weg.
- Eine ausdrückliche Bestätigung erzeugt einen zeitgestempelten, an
  Invocation-ID und Fingerprint gebundenen `GateDecision`-Record.
- `src/artifact_replay.py` bleibt zeilenzahlneutral.
