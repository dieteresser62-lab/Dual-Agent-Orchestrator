# Dual-Agent Ticketing Orchestrator

Universeller Orchestrator fuer einen zweiphasigen Agenten-Workflow (Planung + Implementierung) mit Artefakten unter `.orchestrator/`.

## Schnellstart

```bash
./bearbeite_aufgabe
```

- Standard-Task-Datei: `Aufgabe.md`
- Resume-first Verhalten bei bestehendem `.orchestrator/state.json`
- Live-Stream im kompakten Modus

## Hilfe

```bash
./bearbeite_aufgabe --help
python3 src/orchestrator.py --help
```

## Eigene Task-Datei

```bash
./bearbeite_aufgabe my-task.md
```

## Testkommando konfigurieren

Tests in Phase 2 sind ueber `--test-command` steuerbar.

```bash
python3 src/orchestrator.py --task-file my-task.md --test-command "pytest -x"
python3 src/orchestrator.py --task-file my-task.md --test-command "npm test"
python3 src/orchestrator.py --task-file my-task.md --test-command ""
```

Leerer Wert (`""`) ueberspringt den Testlauf explizit.

## Dry-Run

```bash
python3 src/orchestrator.py --dry-run --auto --task-file example-task.md --test-command ""
```
