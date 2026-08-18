# Slice 04: Wurzelgebundene Dateischnappschüsse

**Status:** implementiert, vollständig validiert und durch Claude sowie Antigravity freigegeben
**Feature-Branch:** `feature/orchestrator-modernization`
**Branch-Basis des Slice:** `1122b2574455e43a6b62b88042438d65cae5c503`
**GitHub-Status:** nur lokal; kein Upstream; Push und Merge sind nicht Bestandteil dieses Slice
**Arbeitsplan:** [orchestrator-modernization-work-plan.md](orchestrator-modernization-work-plan.md)

## Ziel

Jeder vom Implementiererbericht oder einem Contract gelieferte Dateipfad wird vor dem Lesen kanonisiert und gegen die explizit übergebene Repositorywurzel geprüft. Traversal, fremde absolute Pfade, Windows-Laufwerks-/UNC-Pfade unter POSIX/WSL und Symlink-Ausbrüche gelangen weder als Dateiinhalt noch als unkontrollierter Lesefehler in den Reviewprompt.

Die Wurzelprüfung wird als gemeinsame Primitive ausgelagert und auch von der bestehenden State-Pfadprüfung verwendet. Die fachliche Klassifikation durch konfigurierbare Produktiv-, Test-, Dokumentations- und Generated-Muster bleibt gemäß Slice 02 den Slices 05 und 12 vorbehalten; in Slice 04 bedeutet „erlaubte Pfadklasse“ die technisch sichere Klasse eines kanonisch innerhalb einer erlaubten Wurzel liegenden Pfads. Unbekannte, aber wurzelinterne Dateien werden daher nicht vorzeitig ausgeschlossen.

## Akzeptanzkriterien

- `collect_file_snapshots` benötigt eine explizite Repositorywurzel und liest nie relativ zu einem zufälligen Prozessarbeitsverzeichnis.
- Relative Traversalvarianten mit `/`, `\\` oder gemischten Separatoren werden vor dem Lesen verworfen, auch wenn eine lexikalische Normalisierung wieder innerhalb der Wurzel enden würde.
- Absolute Pfade werden nur akzeptiert, wenn ihre kanonische Auflösung innerhalb der Repositorywurzel liegt; fremde POSIX-Pfade, Windows-Laufwerkspfade und UNC-Pfade werden unter POSIX/WSL verworfen.
- Ein vorhandener oder gebrochener Symlink, dessen Ziel außerhalb der Repositorywurzel liegt, wird nicht gelesen.
- Gültige relative und wurzelinterne absolute Repositorypfade, fehlende Dateien, Verzeichnisse, Dateilimit und Zeilenlimit behalten ein deterministisches Snapshotformat.
- Jeder verworfene Pfad erzeugt eine Warnung mit dem Pfad und einem knappen Grund; fremder Dateiinhalt erscheint nicht zwischen `<<<FILES_BEGIN>>>` und `<<<FILES_END>>>`.
- State- und Snapshot-Pfadvalidierung rufen dieselbe zentrale kanonische Wurzelprüfung auf; das bestehende sichere State-Fallback bleibt unverändert.
- Die vollständige Suite und der bestehende Dry-Run bleiben grün.

## Scope

### Produktive Programmdateien

- `src/path_policy.py` (neu): zentrale kanonische Wurzelprüfung und agentenspezifische Repositorypfadauflösung.
- `src/agent_runtime.py`: wurzelgebundene Snapshot-Erfassung und fail-closed Protokollierung.
- `src/orchestrator.py`: explizite Übergabe der bereits bekannten Repositorywurzel.
- `src/state_io.py`: Wiederverwendung der zentralen Wurzelprüfung ohne Änderung des State-Schemas.

### Dokumentation

- `docs/internal/orchestrator-modernization-work-plan.md`: Link und tatsächlicher Slice-Status.
- diese Slice-MD.

### Genehmigungspflichtige Testpfade

- `tests/test_agent_runtime.py`
- `tests/test_path_policy.py` (neu)

## Nicht-Scope

- Keine Auswertung der konfigurierten fachlichen Pfadklassen für Diff, Scope oder Produktivdateigrenze; dies erfolgt in Slice 05 beziehungsweise Slice 12.
- Keine neue kanonische Git-Diff-Quelle; Agentenbericht und bestehender Fallback bleiben bis Slice 05 bestehen.
- Keine Änderung am State-Schema, an Contracts, Findings, Gates, Reviewreihenfolge oder Commitlogik.
- Keine Änderung an Reviewer-Sandbox, Harness, Agentenmodellen oder CLI-Rechteprofilen.
- Keine Änderung an Root-Instruktionsdateien, Push oder Merge.

## Diff-Risiko vor dem ersten Code-Edit

**Branch-Check:** `feature/orchestrator-modernization`; entspricht dem Arbeitsplan.

**Status vor Slice-Beginn:** Drei bereits vorhandene, nicht zu Slice 04 gehörende Revision-6-Änderungen liegen in `docs/internal/handover-orchestrator-modernization.md`, `docs/internal/orchestrator-modernization-work-plan.md` und `docs/internal/requirements-orchestrator-modernization.md`. Zusätzlich existiert die unversionierte Editor-Lockdatei `docs/internal/.~lock.slice-orchestrator-modernization-03-three-agent-adapters.md#`. Sie wird weder gelesen noch verändert, reviewed, gestaged oder committet. Nur klar abgegrenzte Slice-04-Hunks des Arbeitsplans dürfen später aufgenommen werden.

**HEAD:** `1122b2574455e43a6b62b88042438d65cae5c503`.

**Geplante Dateien:** vier produktive Programmdateien, Arbeitsplan, Slice-MD und zwei Testpfade. Die Grenze von zehn produktiven Dateien wird nicht erreicht.

**Voraussichtliche Änderungstiefe:** mittel. Die Änderung liegt an einer Prompt-Datengrenze und muss Plattformnormalisierung, Symlinkauflösung und das bestehende State-Fallback konsistent behandeln.

**Gefährdete bestehende Tests:** Snapshotformat/-limits, State-Pfadmigration, Phase-2-Reviewprompt und Dry-Run.

**Nicht anfassen:** fremde Revision-6-Dokumenthunks, die Editor-Lockdatei, `.orchestrator`-State/Checkpoints, CLI-Konfiguration, Adapter, Harness, Watcher, Contracts/Prompts und Root-Instruktionsdateien.

**Rollback-Strategie:** Änderungen werden dateiweise durch Gegenpatches zurückgeführt; die neue Datei wird nur nach ausdrücklicher Freigabe entfernt. Kein Hard Reset und kein pauschales Checkout.

## Test-Riegel

Der Ausgangsdiff gegen HEAD ist für beide vorgesehenen Testpfade leer; `tests/test_path_policy.py` existiert noch nicht. SHA-256 des binären Testdiffs:

```text
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

Die erbetene Freigabe gilt ausschließlich für die beiden genannten Pfade und folgende Testintention:

- gültige relative und wurzelinterne absolute Repositorypfade auf POSIX/WSL,
- POSIX-Traversal sowie Windows- und gemischte Separatorvarianten,
- fremde absolute POSIX-, Windows-Laufwerks- und UNC-Pfade,
- vorhandene und gebrochene Symlink-Ausbrüche,
- fehlende Dateien, Verzeichnisse, Duplikate, Datei-/Zeilenlimits und unveränderte Snapshotmarker,
- Warnungsprotokoll ohne Übernahme fremden Dateiinhalts,
- gemeinsame zentrale Primitive für State- und Snapshotpfade sowie bestehendes State-Fallback.

```text
TEST_FILES_TOUCHED: tests/test_agent_runtime.py,tests/test_path_policy.py
TEST_CHANGE_APPROVAL: YES | Nutzerfreigabe vom 2026-08-11 für die zwei dokumentierten Pfade und die beschriebene Pfadpolicy-/Snapshot-Testintention
```

Vor dem Claude-Review war der binäre Diff von `tests/test_agent_runtime.py` an folgenden SHA-256 gebunden:

```text
11593cb931efd41d2125a7489d7baacd32e97482818096a85e55a66bf0bb280a
```

Da Git unversionierte Dateien nicht in `git diff HEAD` aufnimmt, wird die neue Datei separat über ihren vollständigen Inhalt gebunden:

```text
e72192cf744c4a1df02797262d7e3ca077851d34b8632cb344b46b852252686e  tests/test_path_policy.py
```

Nach Umsetzung von Claudes F-003 ist der finale binäre Diff der bestehenden Testdatei an folgenden SHA-256 gebunden; die neue Testdatei blieb unverändert:

```text
3f222a4a0531126398306827710bee64db114df669dfd5364a25c555d251da9a  tests/test_agent_runtime.py (binärer Diff gegen HEAD)
e72192cf744c4a1df02797262d7e3ca077851d34b8632cb344b46b852252686e  tests/test_path_policy.py (vollständiger Dateiinhalt)
```

## Geplante Validierung

1. Gezielte Tests der beiden genehmigten Testpfade.
2. `python3 -m pytest tests/ -v` gemäß Repositoryregel.
3. `python3 -m py_compile` für alle geänderten/neuen Pythonmodule.
4. `./run_task --dry-run --skip-git-check --test-command ''`.
5. `git diff --check`, Scopeprüfung und erneute Test-Diff-Fingerprintprüfung.
6. Danach Stopp vor dem externen Claude-Aufruf; Bereitstellung einer isolierten Read-only-Kopie, eines Review-Requests und des exakten manuellen Sonnet-5-High-Aufrufs.
7. Der Claude-Request enthält zusätzlich den vereinbarten Carry-forward-Audit der kritischen Slice-03-Bereiche: Snapshot-/Harnessbindung, Cleanup, `PWD`-Bindung, kein Retry bei Budget/Permission, kontrollierte `run_pipeline`-Behandlung und einheitlicher JSON-/JSONL-Vertrag.

Ausgangsvalidierung unmittelbar vor dem ersten Produktivcode-Edit:

```text
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_agent_runtime.py tests/test_state_io.py -q -p no:cacheprovider | 0 (34 passed)
```

## Durchgeführte Änderungen

- `src/path_policy.py` stellt `resolve_path_within_roots` als zentrale kanonische Wurzelprüfung bereit. `resolve_repository_path` ergänzt die Regeln für nicht vertrauenswürdige Agentenpfade: Parentsegmente werden vor Normalisierung abgelehnt, relative Backslashes plattformneutral normalisiert und Windows-Laufwerks-/UNC-Pfade unter POSIX/WSL fail-closed behandelt.
- `src/agent_runtime.py` verlangt für `collect_file_snapshots` nun eine explizite `repository_root`. Es dedupliziert über kanonische Repositorypfade, lässt abgelehnte Angaben nicht gegen das Dateilimit zählen, protokolliert den Ablehnungsgrund und liest nur reguläre Dateien.
- `src/orchestrator.py` übergibt die in `OrchestratorConfig` gebundene Repositorywurzel an die Snapshot-Erfassung.
- `src/state_io.py` verwendet dieselbe zentrale Primitive und übersetzt deren Policyfehler in den bestehenden `ValueError`-Fallbackvertrag.
- `tests/test_agent_runtime.py` prüft explizite Wurzelbindung, Limits, fehlende Dateien, kanonische Duplikate, Verzeichnisse, fremde Inhalte, Traversal und Symlink-Ausbruch auf Snapshot-Ebene.
- `tests/test_path_policy.py` prüft relative, gemischte und wurzelinterne absolute Positivpfade sowie POSIX-/Windows-/UNC-Fremdpfade, Parentsegmente, bestehende und gebrochene Symlinks und mehrere erlaubte State-Wurzeln.

## Ausgeführte Validierung

```text
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_agent_runtime.py tests/test_path_policy.py tests/test_state_io.py -q -p no:cacheprovider | 0 (49 passed)
VALIDATION_RESULT: PASS | python3 -m pytest tests/ -v | 0 (169 passed in 66.17s)
VALIDATION_RESULT: PASS | python3 -m py_compile src/path_policy.py src/agent_runtime.py src/orchestrator.py src/state_io.py | 0
VALIDATION_RESULT: PASS | ./run_task --dry-run --skip-git-check --test-command '' | 0
VALIDATION_RESULT: PASS | git diff --check | 0
VALIDATION_RESULT: PASS | review_harness.py in isolated read-only Git snapshot | 0 (169 passed; diff check 0; README write-open denied)
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_agent_runtime.py tests/test_path_policy.py tests/test_state_io.py -q -p no:cacheprovider | 0 (50 passed after F-003)
VALIDATION_RESULT: PASS | python3 -m pytest tests/ -v | 0 (170 passed in 66.89s after F-003)
```

## Abweichungen vom Plan

- `src/orchestrator.py` ist als notwendiger Aufrufer ergänzt, weil nur dort die bereits aufgelöste Repositorywurzel zuverlässig an `collect_file_snapshots` übergeben werden kann.
- Die Formulierung „erlaubte Pfadklassen“ wird entsprechend dem expliziten Slice-02-Nicht-Scope nicht als vorgezogene Patternklassifikation interpretiert. Slice 04 erzwingt die kanonische Wurzelzugehörigkeit; Slice 05/12 werten die konfigurierten fachlichen Klassen aus.

## Offene Risiken

- Symlinkprüfung und späteres Lesen sind zwei getrennte Dateisystemoperationen; ein böswilliger paralleler Symlinktausch wäre damit grundsätzlich ein TOCTOU-Risiko. Für den lokalen, nicht gegnerisch parallel mutierten Arbeitsbaum wird zunächst die kanonische Prüfung direkt vor dem Lesen verwendet; ein offener Reviewerbefund kann eine descriptorgebundene Härtung verlangen.
- Zu strenge Behandlung von Backslashes könnte legitime Windows-Berichte blockieren; relative Backslashes werden deshalb als Separatoren normalisiert, während Laufwerks- und UNC-Pfade unter POSIX/WSL fail-closed bleiben.
- Ein Ein-Zeichen-plus-Doppelpunkt-Präfix wie `a:b` wird von `PureWindowsPath` als Drive behandelt und fail-closed abgelehnt. Das ist eine bewusste Portabilitätseinschränkung: Solche Namen sind unter POSIX möglich, aber keine plattformneutralen Windows-Dateinamen.

## Review-Feedback von Claude

Claude führte das Review interaktiv durch den Nutzer in der isolierten Read-only-Kopie mit Sonnet/High aus. Der geforderte Harness lief genau einmal: 169 Tests, sauberer Diffcheck und blockierter README-Schreibversuch. Alle acht Slice-04-Prüffragen wurden positiv beantwortet; der gezielte Carry-forward-Audit fand keine Regression in den sechs kritischen Slice-03-Bereichen.

Claude benannte drei nicht blockierende Observations: das bereits dokumentierte TOCTOU-Restrisiko (F-001), die fail-closed Behandlung von Ein-Zeichen-plus-Doppelpunkt-Präfixen als Windows-Drive (F-002) und die fehlende Abdeckung des Spezialdatei-Zweigs (F-003). Der folgende Block gibt die tatsächlich ausgegebenen Abschlussmarker wieder; die nachgelagerten Entscheidungen stehen getrennt unter „Review-Antworten von Codex“ und in der Entscheidungstabelle.

```text
PHASE2_APPROVAL: YES
OPEN_FINDINGS: NONE
STATUS: DONE
```

## Review-Feedback von Antigravity

Antigravity prüfte den finalen Observation-Delta in einer frischen scopegenauen Read-only-Kopie. Der Harness lief genau einmal mit 170 bestandenen Tests. F-001 und F-002 wurden als angemessene nicht blockierende Restrisiken bestätigt; der neue FIFO-Test wurde als deterministischer Abschluss von F-003 bestätigt. Es entstanden keine neuen Findings.

```text
REVIEWER: antigravity
VALIDATION_RESULT: PASS | review_harness.py | 170 passed; git_diff_check clean; README write_probe blocked
SLICE_APPROVAL: 04 | YES
OPEN_FINDINGS: NONE
STATUS: DONE
```

Die strukturierte Aufrufhülle meldete `status=SUCCESS`, eine Runde und 118,6 Sekunden Laufzeit.

## Review-Antworten von Codex

- F-001 angenommen und ohne Codeänderung geschlossen: Das TOCTOU-Risiko war bereits vor dem Review ausdrücklich dokumentiert. Descriptorgebundenes Traversieren wäre eine unverhältnismäßige Härtung für den lokalen Vertrauensvertrag dieses Slice.
- F-002 als Tatsachenbeobachtung bestätigt und als bewusste fail-closed Portabilitätseinschränkung geschlossen. `PureWindowsPath` behandelt auch nicht alphabetische Ein-Zeichen-Präfixe als Drive; eine Lockerung würde den plattformneutralen Vertrag zugunsten nur unter POSIX gültiger Namen aufweichen.
- F-003 angenommen und umgesetzt: Ein POSIX-FIFO-Test beweist den `[skip] Path is not a regular file.`-Zweig und instrumentiert `Path.read_text`, damit ein künftiger Fehlversuch deterministisch scheitert statt am FIFO zu blockieren. Gezielte Suite 50/50, Vollsuite 170/170.

## Entscheidungstabelle

| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| F-001 | Claude | TOCTOU zwischen kanonischer Auflösung und späterem Lesen | OBSERVATION | angenommen | als bekanntes lokales Restrisiko dokumentiert; keine Codeänderung |
| F-002 | Claude | Ein-Zeichen-plus-Doppelpunkt wird als Windows-Drive klassifiziert | OBSERVATION | angenommen | bewusste fail-closed Portabilitätseinschränkung dokumentiert |
| F-003 | Claude | Spezialdatei-Zweig war nicht getestet | OBSERVATION | angenommen | FIFO-Regressionstest ergänzt; 50 gezielte und 170 vollständige Tests grün |

## Rückdokumentation in den Arbeitsplan

Der Arbeitsplan verlinkt diese Slice-MD, nennt die tatsächlich betroffenen Dateien und weist die doppelte Freigabe, das grüne Observation-Delta und die Autorisierung des lokalen Slice-Commits aus.

## Freigabestatus

- Teständerungen: am 2026-08-11 für die zwei dokumentierten Testpfade und die gebundene Testintention freigegeben.
- Lokale Implementierung und Validierung: abgeschlossen.
- Claude-Review: freigegeben mit `PHASE2_APPROVAL: YES` und `OPEN_FINDINGS: NONE`.
- Antigravity-Review: freigegeben mit `SLICE_APPROVAL: 04 | YES` und `OPEN_FINDINGS: NONE`.
- Lokaler Commit: durch das positive Antigravity-Verdikt autorisiert; scopegenaue Commitprüfung ausstehend.
