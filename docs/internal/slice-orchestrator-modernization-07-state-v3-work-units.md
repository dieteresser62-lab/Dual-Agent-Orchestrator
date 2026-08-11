# Slice 07: State-Version 3 und Work Units

**Status:** implementiert, vollständig validiert und durch Claude sowie Antigravity freigegeben
**Feature-Branch:** `feature/orchestrator-modernization`
**Branch-Basis des Slice:** `4364c11c1b02920e903a9b3926deee02772e9548`
**GitHub-Status:** nur lokal; kein Upstream; Push und Merge sind nicht Bestandteil dieses Slice
**Arbeitsplan:** [orchestrator-modernization-work-plan.md](orchestrator-modernization-work-plan.md)

## Ziel

Eine additive, typisierte State-v3-Schicht bildet Slices, Arbeitseinheiten, persistierte Schritte, Korrekturrunden, Gates, Branch-Basis, Commitreferenzen und bereits ausgeführte Seiteneffekte explizit ab. Sie schafft den Resume-Unterbau für die spätere Workflow-State-Maschine, ohne den aktiven v2-Defaultpfad vor dem gemeinsamen Cutover in Slice 18 umzuschalten.

Der v3-Ladepfad arbeitet fail-closed: abgeschlossene v2-Läufe werden als historische Abschlüsse erkannt, aktive oder eingefrorene v2-Läufe und unbekannte Schemaversionen werden mit typisierten Fehlern unverändert abgelehnt. Es findet keine implizite Migration und kein stiller Neustart statt.

## Akzeptanzkriterien

- State v3 verwendet ausschließlich 1-basierte Slice- und Work-Unit-IDs und speichert den aktuellen Schritt, einen auf vier Codex-Rückgaben begrenzten Rundenzähler, Gatezustand, Branch-Basis und Commitreferenzen.
- Plan-, Slice- und Endreview-Korrektureinheiten sind als explizite Work-Unit-Typen darstellbar.
- Die vierte verweigerte Freigabe setzt die Work Unit deterministisch auf `awaiting_user_decision`, bewahrt Reviewer und offene Findings und erzeugt keinen Traceback, Reset oder WIP-Commit.
- Resume liefert exakt den persistierten Schritt. Persistierte Seiteneffekt-Schlüssel sind idempotent und verhindern eine zweite Ausführung nach einem Neustart.
- Checkpointnamen enthalten Work Unit, Slice und Runde in 1-basierter, kollisionsfreier Form.
- v3-State und Checkpoints werden atomar und ausschließlich unter explizit erlaubten Wurzeln geschrieben und geladen.
- Ein abgeschlossener v2-State wird als historischer Abschluss erkannt. Aktive/eingefrorene v2-States sowie unbekannte oder strukturell ungültige States scheitern laut und bleiben unverändert.
- Der aktive v2-Orchestrator bleibt bis Slice 18 funktionsfähig; die neue v3-Schnittstelle ist nur ein additiver Development-Mode-Einstieg.
- Die vollständige Testsuite und der bestehende v2-Dry-Run bleiben grün.

## Scope

### Produktive Programmdateien

- `src/workflow_state.py` (neu): unveränderliche v3-State-, Slice-, Work-Unit-, Gate- und Resume-Modelle samt Validierung und JSON-Abbildung.
- `src/state_io.py`: wurzelgebundener v3-Loader, atomare Speicherung, explizite v2-/Versionsklassifikation und kollisionsfreie v3-Checkpoints; bestehende v2-Helfer bleiben für den Altpfad erhalten.
- `src/orchestrator.py`: schmale Development-Mode-Fassade für v3-Initialisierung, Laden, Speichern und Resume; keine Aktivierung im Defaultablauf.

### Dokumentation

- `docs/internal/orchestrator-modernization-work-plan.md`: Link und tatsächlicher Slice-Status.
- `docs/internal/handover-orchestrator-modernization.md`: aktueller Commit-/Slice-Stand.
- diese Slice-MD.

### Vorab autorisierte Testpfade

- `tests/test_state_io.py`
- `tests/test_workflow_state.py` (neu)

## Nicht-Scope

- Keine Aktivierung der asymmetrischen Claude-/Antigravity-Reviewkette; dies folgt in Slice 10.
- Keine deterministische Projektion in Slice- und Plandokumente; dies folgt in Slice 08.
- Keine Git-Committransaktion; dies folgt in Slice 09.
- Keine produktive CLI-Option für State v3, kein Cutover des Defaultpfads und keine Änderung der Root-Rollenmarker; dies erfolgt in späteren Slices, abschließend Slice 18.
- Keine Quota-Warteautomatik, keine vollständige Nutzer-Gate-Steuerung und kein Watch-Resume; dies folgt in Slice 11, Slice 14 und Slice 17.
- Kein Push oder Merge.

## Diff-Risiko vor dem ersten Code-Edit

**Branch-Check:** `feature/orchestrator-modernization`; entspricht dem Arbeitsplan.

**Status vor Slice-Beginn:** Der Arbeitsbaum enthielt ausschließlich die unversionierte Editor-Lockdatei `docs/internal/.~lock.slice-orchestrator-modernization-03-three-agent-adapters.md#`. Sie gehört nicht zu Slice 07 und wird weder gelesen noch verändert, reviewed, gestaged oder committet.

**HEAD:** `4364c11c1b02920e903a9b3926deee02772e9548`.

**Merge-Base mit `master`:** `0bd3baddbac307b90e85db4c9bdcb5a787558652`.

**Geplante Dateien:** drei produktive Programmdateien, Arbeitsplan, Übergabe, Slice-MD und zwei Testpfade. Die Grenze von zehn produktiven Dateien wird nicht erreicht.

**Voraussichtliche Änderungstiefe:** hoch im neuen State-Modell, mittel an der State-I/O-Grenze und niedrig im aktiven Orchestrator. Das Hauptrisiko ist ein stiller Datenverlust durch Versionsfehlklassifikation oder ein Resume, das einen schon ausgeführten Seiteneffekt wiederholt.

**Gefährdete bestehende Tests:** v2-State-Normalisierung und -Checkpoint-Recovery, Pfadvalidierung, atomare Schreibvorgänge sowie der aktive v2-Dry-Run.

**Nicht anfassen:** die Editor-Lockdatei, vorhandene `.orchestrator/state.json` und Checkpoints, Agentenadapter, Prompts/Contracts, Repository-Diff, CLI-Konfiguration, Watcher und Root-Instruktionsdateien.

**Rollback-Strategie:** Änderungen werden dateiweise durch Gegenpatches zurückgeführt; die neue Datei wird nur nach ausdrücklicher Freigabe entfernt. Kein Hard Reset und kein pauschales Checkout.

## Teständerungs-Autorisierung

Die Änderungen in `tests/test_state_io.py` und `tests/test_workflow_state.py` sind durch die arbeitsplanweite Revision-8-Ausnahme vorab autorisiert. Sie bleiben scope- und fingerprintgebunden, werden vollständig reviewed und mit der Vollsuite validiert. Das Zielverhalten von R-10 wird dadurch nicht geändert.

## Validierung

```text
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_workflow_state.py tests/test_state_io.py -q -p no:cacheprovider | 0 (52 passed nach F-001)
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -q -p no:cacheprovider | 0 (277 passed nach F-001)
VALIDATION_RESULT: PASS | python3 -m py_compile src/workflow_state.py src/state_io.py src/orchestrator.py | 0
VALIDATION_RESULT: PASS | ./run_task --dry-run --skip-git-check --test-command '' | 0; aktiver v2-Altpfad vollständig durchlaufen
VALIDATION_RESULT: PASS | git diff --check | 0
```

Testbindung vor dem Claude-Review:

```text
4eea5038a6ccc80bd68581b5c259ad926445679f3637f240238b184c3290abe6  tests/test_state_io.py
9198bfc57dc0259a23b8ff8e13e8025df3388a766244922c8ff01a76d2970769  tests/test_workflow_state.py
```

## Abweichungen vom Plan

- Der bestehende v2-Normalisierer initialisiert unbekannte Versionen nicht mehr still neu. Das ist keine Aktivierung von State v3, sondern die bereits in R-8 geforderte fail-closed Korrektur am Resume-Rand; gültige v2-States und neue v2-Läufe bleiben unverändert aktiv.
- Neben der reinen Datenrepräsentation enthält das Modell kleine unveränderliche Übergangsoperationen für Schrittwechsel, Work-Unit-/Slice-Wechsel, vier Reviewverweigerungen und idempotente Seiteneffekte. Dadurch ist Resume-Verhalten ohne Vorgriff auf die Agenten-State-Maschine aus Slice 10 direkt testbar.

## Offene Risiken

- Ein formal gültiger, aber semantisch inkonsistenter State könnte einen falschen Resume-Schritt oder eine falsche Slice-/Work-Unit-Zuordnung erzeugen; deshalb validiert das Modell Querverweise und Statuskonsistenz beim Laden.
- Atomare Pfadersetzung verhindert Teildateien, schützt aber nicht gegen zwei gleichzeitig schreibende Orchestratorprozesse. Prozessübergreifendes Locking gehört nicht zum Scope dieses Slice.
- Die v3-Schnittstelle wird erst in Slice 10/18 zur aktiven State-Maschine. Dieser Slice beweist Persistenz und Resume-Semantik, nicht bereits den vollständigen Mehr-Slice-Lauf.

## Review-Feedback von Claude

Claude Sonnet 5 mit Effort `high` prüfte zunächst das vollständige Slice-Paket seit `4364c11` und führte den gebundenen Read-only-Harness genau einmal aus. 276 Tests bestanden, Diffcheck und Statusabfrage waren sauber ausführbar, die Schreibprobe wurde korrekt blockiert. Der Lauf benötigte fünf Turns und kostete laut JSON-Hülle 0,508 USD.

Claude bestätigte das typisierte 1-basierte Modell, die gemeinsame Vier-Rückgaben-Grenze, Gate-/Statuskonsistenz, persistierte Resume-Schritte und Seiteneffekte, atomare/wurzelgebundene Speicherung, Checkpointidentität, strikte Versionsklassifikation sowie die additive v2-Grenze. Als akzeptiertes Restrisiko blieb das bereits dokumentierte fehlende prozessübergreifende Locking. Einziger konkreter Befund war eine Testlücke:

- F-001: Der neue `StateSchemaError`-Zweig für einen Version-2-State, dem `phase1` oder `phase2` fehlt, war nicht direkt auf Fehlertext und unveränderte Eingabedaten getestet.

Codex ergänzte den direkten Negativtest. Die fokussierte Korrekturprüfung erhielt nur Finding, relevanten Diff und Akzeptanztest, führte den Harness genau einmal aus und kostete 0,118 USD. Sie bestätigte 277 grüne Tests und schloss F-001 formal:

```text
FINDING_STATUS: F-001 | CLOSED | incomplete v2 raises StateSchemaError with the missing-phase message and leaves the input unchanged
VALIDATION_RESULT: PASS | python3 -m pytest tests/ -v | 0 (277 passed; write probe blocked)
OPEN_FINDINGS: NONE
PHASE2_APPROVAL: YES
STATUS: DONE
```

## Review-Feedback von Antigravity

Antigravity erhielt das vollständige finale Slice-Paket seit dem persistierten Startcommit `4364c11`: alle drei Produktivdateien, beide Testpfade, Arbeitsplan, Übergabe, Slice-MD sowie die Claude-Korrektur F-001. Das Paket war ausdrücklich kein Deltareview. Der unmittelbar vor dem Aufruf gebildete SHA-256-Fingerprint dieses Prüfstands lautet:

```text
ec1eb5d7743a77b48deb69b17e89734cdacabc9a1dfdb4542dc2ac96625bfa32
```

Der Read-only-Harness lief genau einmal mit 277 grünen Tests; Diffcheck und Statusabfrage waren erfolgreich, die Schreibprobe wurde korrekt blockiert. Antigravity bestätigte 1-basierte IDs, Work-Unit-Typen, Vier-Rückgaben-Grenze, Branch-/Commitreferenzen, idempotente Seiteneffekte, wurzelgebundene atomare I/O, v2-Migrationsgrenze, additive Architektur und die vollständige Korrektur von F-001. Es meldete keine neuen Findings.

Als größtes Restrisiko bestätigte Antigravity das bereits dokumentierte fehlende prozessübergreifende Locking: Zwei gleichzeitige Orchestratorprozesse könnten nach getrenntem Laden in umgekehrter Reihenfolge schreiben und dadurch den neueren Fortschritt überschreiben. Dieses Risiko bleibt für den lokalen Einzelprozessbetrieb akzeptiert und außerhalb von Slice 07.

```text
REVIEWER: antigravity
VALIDATION_RESULT: PASS | python3 -m pytest tests/ -v | 0 (277 passed; write probe blocked)
TEST_FILES_TOUCHED: tests/test_state_io.py,tests/test_workflow_state.py
OPEN_FINDINGS: NONE
SLICE_APPROVAL: 07 | YES
STATUS: DONE
```

## Review-Antworten von Codex

- F-001 wurde akzeptiert. `test_ensure_state_shape_rejects_incomplete_v2_state_without_mutation` deckt nun den exakten neuen Zweig, den erwarteten Fehlertext und die unveränderte Eingabe ab. Fokussierte Suite und Vollsuite sind grün.

## Entscheidungstabelle

| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| F-001 | Claude | Unvollständiger v2-State-Zweig nicht direkt getestet | BLOCKER | CLOSED | direkter Fehlertext-/No-Mutation-Test ergänzt; Claude fokussiert bestätigt |

## Rückdokumentation in den Arbeitsplan

Der Arbeitsplan verlinkt diese Slice-MD. Implementierungs-, Validierungs- und Reviewstatus werden nach Abschluss ergänzt.

## Freigabestatus

- Teständerungen: durch Revision 8 vorab autorisiert.
- Lokale Implementierung und Validierung: abgeschlossen; 52 fokussierte und 277 vollständige Tests grün.
- Claude-Review: `PHASE2_APPROVAL: YES`; F-001 geschlossen, keine offenen Findings.
- Antigravity-Review: vollständiger finaler Slice-Diff geprüft; `SLICE_APPROVAL: 07 | YES`, `OPEN_FINDINGS: NONE`.
- Lokaler Commit: durch Antigravity autorisiert; scopegenaue Abschlussprüfung ausstehend.
