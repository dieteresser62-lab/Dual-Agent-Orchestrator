# Slice 02: Python-Einstiegspunkt und deklarative Konfiguration

**Status:** Implementiert und freigegeben; Claude und Antigravity ohne offene Findings
**Feature-Branch:** `feature/orchestrator-modernization`
**Branch-Basis des Slice:** `90c1a63483e2b1157b007e23295a0b20ffeee79f`
**GitHub-Status:** nur lokal; kein Upstream; Push und Merge sind nicht Bestandteil dieses Slice
**Arbeitsplan:** [orchestrator-modernization-work-plan.md](orchestrator-modernization-work-plan.md)

---

## Ziel

Die im Bash-Wrapper verbliebene Ablauf-, Resume-, Watch-, Testautodetektions- und Umgebungskonfiguration wird in einen portablen Python-Einstiegspunkt verlagert. `run_task` bleibt als minimaler Kompatibilitätsstarter erhalten. Eine optionale, versionierbare `orchestrator.toml` stellt die deklarative Konfigurationsbasis für spätere Pfad-, Stop- und Validierungsslices bereit, ohne Agentenmodelle, Timeouts oder lokale Binarypfade im Repository festzuschreiben.

## Abgedeckte Anforderungen

- R-13 teilweise: Python-Einstiegspunkt, Wrapper und Plattformdokumentation sind umgesetzt. Die konfigurierbaren Agentenbefehle einschließlich `agy`/`agy.exe` folgen in Slice 03; automatisierte Evidenz auf allen zugesicherten Plattformvarianten und der abschließende Dokumentationsabgleich folgen spätestens in Slice 19.
- R-2 vorbereitend durch die allgemeine Präzedenz- und Konfigurationsschnittstelle; Agentenadapter und rollenbezogene Optionen folgen in Slice 03.
- R-15 vorbereitend durch deklarative, validierte Stop-Regeln; deren Gate-Auswertung folgt in Slice 12.
- R-16 vorbereitend durch deklarative Standard- und pfadabhängige Validierungen; deren Diff-Auswertung folgt in Slice 13.

## Akzeptanzkriterien

- Testautodetektion, automatische Resume-Entscheidung, Watch-Defaults und Argumentweitergabe liegen ausschließlich in `src/cli.py`.
- `run_task` löst nur seinen eigenen Speicherort auf und ersetzt sich durch den Python-Einstiegspunkt; es enthält keine Workflow-Fallunterscheidung.
- Der erste positionale Taskpfad bleibt kompatibel; die explizite Option `--task-file` bleibt verfügbar und widersprüchliche Doppelnennung wird verständlich abgelehnt.
- Konfigurierbare Werte folgen nachweislich der Präzedenz CLI → Umgebung → Repo-Konfiguration → Standard beziehungsweise Autodetektion.
- Eine explizit leere Testkommando-Konfiguration überspringt Tests und wird nicht durch Autodetektion überschrieben.
- Die Testautodetektion erkennt pytest-Konfiguration in `pyproject.toml`, ein `test`-Script in `package.json` und ein `test`-Target im `Makefile` ohne Bash- oder GNU-spezifische Auswertung.
- Ein unvollständiger aktiver State wird automatisch resumed; Watch startet weiterhin ohne Auto-Resume; ein abgeschlossener State startet einen neuen Lauf.
- Watch-Defaults und die bestehenden `RUN_TASK_*`-Overrides werden in Python aufgelöst; explizite CLI-Werte bleiben vorrangig.
- Die optionale `orchestrator.toml` akzeptiert ausschließlich dokumentierte Tabellen für Pfadklassen, fachliche Stop-Regeln, Standard-/pfadabhängige Validierung und den optionalen manuellen Slice-Gate-Default.
- Unbekannte Schlüssel, falsche Typen, doppelte Stop-Regel-IDs sowie leere, absolute oder aus der Repositorywurzel ausbrechende Pfadmuster führen vor Workflowstart zu einem verständlichen Konfigurationsfehler mit Exitcode 1.
- Agentenmodelle, Timeouts und lokale Binarypfade sind kein Bestandteil der versionierten Repo-Konfiguration dieses Slice.
- Der direkte Python-Einstiegspunkt und `run_task` liefern denselben CLI-Vertrag.
- README dokumentiert Python-Einstiegspunkt, Konfigurationspräzedenz und den tatsächlich zugesicherten Plattformkreis Linux, macOS und WSL2; native Windows-Unterstützung bleibt unzugesichert.
- Die vollständige Testsuite bleibt grün.

## Scope

Geplante Laufzeit- und Konfigurationsänderungen:

- `run_task`
- `src/cli.py` (neu)
- `src/orchestrator.py`
- `pyproject.toml`
- `orchestrator.toml` (neu; deklaratives Repo-Default/Beispiel)
- `README.md`

Geplante Teständerungen, die dem separaten Test-Riegel unterliegen:

- `tests/test_cli.py` (neu)
- `tests/test_orchestrator_watch_cli.py`

Prüfspur und Rückdokumentation:

- `docs/internal/slice-orchestrator-modernization-02-python-cli-config.md`
- `docs/internal/orchestrator-modernization-work-plan.md`

## Nicht-Scope

- Antigravity-Adapter, Agentenmodell-, Timeout- und Binarykonfiguration; dies erfolgt in Slice 03.
- Technische Reviewer-Sandbox und Rollenrechte; dies erfolgt in Slice 03.
- Auswertung von Pfadklassen für Diff, Scope oder Dateigrenze; dies erfolgt in Slice 05 beziehungsweise Slice 12.
- Ausführung pfadabhängiger Validierungen; dies erfolgt in Slice 13.
- Neues Slice-State-Schema oder neue Resume-Zustände; dies erfolgt in Slice 07 beziehungsweise Slice 14.
- Aktivierung von `--manual-slice-gate`; der deklarative Default wird nur strikt geladen und für den späteren Workflow bereitgestellt.
- Änderungen an Root-Instruktionsdateien oder Markersemantik.
- Push oder Merge.

## Diff-Risiko-Block

**Branch-Check vor dem ersten Edit:** `git branch --show-current` → `feature/orchestrator-modernization`; stimmt mit dem Arbeitsplan überein.
**Statuscheck vor dem ersten Edit:** `git status --short` → leer; Arbeitsbaum war sauber.
**HEAD vor dem ersten Edit:** `90c1a63483e2b1157b007e23295a0b20ffeee79f`.
**Produktive Programm-/Konfigurationsdateien:** fünf (`run_task`, `src/cli.py`, `src/orchestrator.py`, `pyproject.toml`, `orchestrator.toml`); Grenze 10 wird nicht erreicht. README und interne Prüfspur zählen als Dokumentation.
**Contractklarheit:** Der aktive Zwei-Phasen- und Markercontract bleibt unverändert; dieser Slice verschiebt nur Aufruf- und Konfigurationsverantwortung.
**Validierbarkeit:** Der grüne Ausgangszustand wurde mit dem Standardbefehl nachgewiesen.
**Bewertung:** Keine Stop-Regel aus §6.4 der Anforderungen greift. Der gesonderte Teständerungsriegel greift planmäßig vor dem ersten Edit unter `tests/**`.

## Teständerungsfreigabe

Die Freigabe soll exakt für die beiden oben genannten Testpfade gelten. Ausgangspunkt ist HEAD `90c1a63483e2b1157b007e23295a0b20ffeee79f`; der Test-Diff gegen diesen Stand war beim Gate leer und hatte als SHA-256-Fingerprint:

```text
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

Reproduktionsbefehl:

```text
git diff HEAD --binary -- tests/test_cli.py tests/test_orchestrator_watch_cli.py | sha256sum
```

Geplant sind ausschließlich:

- Parser- und Einstiegspunkttests für positionale sowie explizite Taskpfade und verständliche Konfliktfehler.
- Präzedenztests für Testkommando und bestehende `RUN_TASK_*`-Overrides über CLI, Umgebung, Repo-Konfiguration und Standard/Autodetektion; explizit leer bleibt ein eigener Wert.
- Autodetektionstests für pytest, npm, Make und den Fall ohne erkennbaren Testtreiber.
- Konfigurationsschematests für gültige Pfadklassen, Stop-Regeln, Validierungsmatrix und manuellen Slice-Gate-Default.
- Negativtests für unbekannte Schlüssel, falsche Typen, doppelte Stop-Regel-IDs und ungültige Pfadmuster.
- Auto-Resume-, abgeschlossener-State- und Watch-ohne-Resume-Tests.
- Wrapperregressionen, die den Bash-Geschäftslogikabbau, LF-Zeilenenden, Executable-Bit, Argumentdurchleitung sowie einen echten Aufruf des Python-Einstiegspunkts absichern.
- Anpassung bestehender Parser-/Main-Tests an die nach `src/cli.py` verschobene Verantwortungsgrenze, ohne den aktiven Workflowcontract zu ändern.

Jeder zusätzliche Testpfad oder eine Änderung der beschriebenen Testintention erfordert eine neue Freigabe. Der resultierende Test-Diff-Fingerprint wird nach der Implementierung dokumentiert.

**Freigabestatus:** am 2026-08-11 durch den Nutzer ausdrücklich erteilt; gebunden an die beiden genannten Testpfade, die dokumentierte Testintention und den Ausgangsstand `90c1a63483e2b1157b007e23295a0b20ffeee79f`.

## Geplante Validierung

1. Gezielte Tests der beiden freigegebenen Testmodule.
2. Statische Prüfung, dass `run_task` keine Workflow-Fallunterscheidung enthält.
3. CLI-Hilfe und Konfigurationsfehler-Smokes über direkten Python-Einstiegspunkt und Wrapper.
4. Isolierte Auto-Resume-, Watch- und Testautodetektions-Smokes in temporären Repositories ohne Eingriff in den Runtime-State dieses Repositorys.
5. Plattformneutrale Syntax-/Importprüfung mit der minimal unterstützten Python-Semantik, soweit lokal verfügbar.
6. Vollsuite: `python3 -m pytest tests/ -v`.
7. `git diff --check` und Scope-/Fingerprintprüfung.

## Durchgeführte Änderungen

- `src/cli.py` als alleinige Quelle für Parser, Konfigurationsauflösung, Testautodetektion, automatische Resume-Entscheidung, Watch-Defaults und Programmstart angelegt.
- Den bisherigen `src/orchestrator.py`-Parser und dessen `main()` durch schmale Kompatibilitätsdelegaten ersetzt; Workflowfunktionen und aktiver Markercontract bleiben unverändert.
- `run_task` auf portable Symlink-/Standortauflösung und genau einen `exec`-Aufruf des Python-Einstiegspunkts reduziert.
- Striktes TOML-Schema für Pfadklassen, Stop-Regeln, Standard-/Zusatzvalidierungen und manuellen Slice-Gate-Default implementiert.
- Unbekannte Schlüssel, falsche Typen, doppelte Stop-Regel-IDs, explizit fehlende Konfigurationsdateien sowie leere, absolute, plattformspezifisch getrennte oder ausbrechende Pfadmuster werden vor Workflowstart abgelehnt.
- Präzedenz CLI → Umgebung → Repo-Konfiguration → Standard/Autodetektion umgesetzt; explizit leere Testkommandos bleiben erhalten.
- Testautodetektion für pytest-TOML, npm-Testscript und Make-Testtarget ohne Shellauswertung implementiert.
- Unfertigen beziehungsweise eingefrorenen State für Auto-Resume erkannt, abgeschlossenen State auf promptfreien Neustart gesetzt und Watch von Auto-Resume getrennt.
- `orchestrator.toml` als versioniertes Repo-Default/Schema-Beispiel ergänzt.
- Die zuvor ungesicherte Python-3.10-/`tomli`-Annahme nach Claude-Finding C-30 entfernt: Mindestversion ist Python 3.11, TOML wird ohne Drittanbieterabhängigkeit aus der Standardbibliothek gelesen.
- README auf Python-Einstiegspunkt, Konfigurationspräzedenz, TOML-Schema sowie Linux/macOS/WSL2-Unterstützung aktualisiert.
- Ausschließlich die freigegebenen Testpfade um Parser-, Präzedenz-, Schema-, Negativ-, Auto-Resume-, Watch-, Wrapper- und Symlinkregressionen ergänzt.

## Ergebnisse

Grüner Ausgangszustand:

```text
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | 0 (95 passed)
```

Validierung des implementierten Stands:

```text
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_cli.py tests/test_orchestrator_watch_cli.py -v -p no:cacheprovider | 0 (46 passed nach C-27 bis C-35 und zusätzlicher leerer-CLI-Konfigurationspräzedenz)
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | 0 (121 passed vor Claude-Runde 1)
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | 0 (130 passed nach C-27 bis C-35)
VALIDATION_RESULT: PASS | RUN_TASK_SKIP_GIT_CHECK=1 RUN_TASK_TEST_CMD=injected RUN_TASK_WATCH_STREAM_CHANNELS=both PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -q -p no:cacheprovider | 0 (130 passed; Umgebungsisolation nach C-31)
VALIDATION_RESULT: PASS | RUN_TASK_CONFIG=/definitely/missing/orchestrator.toml RUN_TASK_SKIP_GIT_CHECK=1 RUN_TASK_TEST_CMD=injected RUN_TASK_WATCH_STREAM_CHANNELS=both PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -q -p no:cacheprovider | 0 (131 passed; vollständige RUN_TASK-Umgebungsisolation)
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_cli.py tests/test_orchestrator_watch_cli.py -v -p no:cacheprovider | 0 (46 passed nach C-36)
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | 0 (131 passed nach C-36)
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile src/cli.py src/orchestrator.py | 0
VALIDATION_RESULT: PASS | ./run_task --help und python3 src/cli.py --help, bytegleicher stdout | 0
VALIDATION_RESULT: PASS | ./run_task task.md --dry-run --skip-git-check --no-agent-live-stream --agent-output none (isoliertes temporäres Zielverzeichnis) | 0
VALIDATION_RESULT: PASS | ungültige orchestrator.toml (isoliertes Zielverzeichnis) | 1; verständliche Meldung; kein .orchestrator-Verzeichnis erzeugt
VALIDATION_RESULT: PASS | git diff --check | 0
```

**Test-Diff-Fingerprint vor dem Claude-Review (SHA-256):**

Der Fingerprint umfasst den Binärdiff der bestehenden Testdatei sowie Pfad und Git-Blob-ID der neuen, noch unversionierten `tests/test_cli.py`:

```text
a536442eb7dc7fa5a57063bbe8915bd8564f37106436acee67758829f6440d69
```

Reproduktionsbefehl:

```text
{ git diff HEAD --binary -- tests/test_orchestrator_watch_cli.py; printf 'UNTRACKED\0tests/test_cli.py\0'; git hash-object tests/test_cli.py; } | sha256sum
```

**Test-Diff-Fingerprint nach C-36 (SHA-256):**

Der nach der C-36-Korrektur erneut mit demselben Reproduktionsbefehl berechnete Fingerprint lautet:

```text
8297aa682af6451cc5de3436d709f4b81b40714c2081c109b8eea2184c3471e4
```

## Abweichungen vom Plan

- Die im Arbeitsplan noch optionale `orchestrator.toml` wurde angelegt, weil nur so die vierstufige Präzedenz und das portable Repo-Schema am realen Einstiegspunkt nachweisbar sind. Sie enthält keine lokalen Pfade, Modelle, Timeouts oder Binarynamen.
- Der Wrapper nutzt eine kleine `readlink`-Schleife statt `realpath`, weil macOS `realpath` nicht standardmäßig bereitstellt und der dokumentierte Symlink-Aufruf sonst nicht portabel wäre. Diese Logik bestimmt ausschließlich den Ort des Python-Einstiegspunkts und enthält keine Workflowentscheidung.
- Lokal ist Python 3.12 verfügbar. Die deklarierte Mindestversion 3.11 kann lokal nicht als eigener Interpreterlauf geprüft werden; der Parser nutzt ausschließlich das seit 3.11 verfügbare Standardbibliotheksmodul `tomllib` und die Metadaten-/README-Regression ist automatisiert.

## Offene Risiken

- Auto-Resume darf den bestehenden Watch-Lifecycle nicht versehentlich an einen alten Einzelrun-State koppeln.
- Ein Zielrepo kann selbst eine `orchestrator.toml` besitzen; Schemafehler müssen vor State- oder Artefaktschreibvorgängen auffallen.
- Der zugesicherte Plattformkreis ist erst mit CI-/Integrationsläufen auf macOS und WSL2 vollständig automatisiert belegt; dieser Slice liefert den plattformneutralen Einstiegspunkt und lokale Linux-/WSL2-Evidenz, nicht die noch unveröffentlichte CI-Evidenz.

## Review-Feedback von Claude

### Runde 1 — Ergebnis vom 2026-08-11

Claude prüfte im vollständigen schreibgeschützten Snapshot Korrektheit, Vollständigkeit, Sicherheit, Regression, Scope/Test-Gate, Plattformportabilität, Konfigurationspräzedenz, Wrapperkompatibilität, Dokumentation und Operabilität. Eigene Vollsuite: **121 passed, Exitcode 0**. Der Reviewer bestätigte insbesondere, dass die Bash-Geschäftslogik vollständig nach Python verschoben ist, Konfigurationsfehler vor State-Schreibvorgängen auftreten, Auto-Resume relativ zum Zielrepo arbeitet und der produktive Scope mit fünf Dateien unter der Grenze bleibt. Die Reviewhülle meldete `is_error:false`; einige nicht allowlist-konforme zusammengesetzte Diagnosebefehle wurden erwartungsgemäß verweigert, der exakte Vollsuite-Befehl lief dagegen erfolgreich.

NEW_FINDING: C-27 | BLOCKER | README führte nach der Defaultvereinheitlichung noch alte Direktaufruf-Defaults und nicht alle Negations-/Positionsformen. | CLI-Referenztabelle nennt die dynamischen Resume-, Test-, Stream- und Watchdefaults sowie Positions- und Negationsformen korrekt; Regression gleicht Kernaussagen mit Parserdefaults ab.
NEW_FINDING: C-28 | BLOCKER | 25 von 32 öffentlichen CLI-Optionen hatten beim Umzug ihren Hilfetext verloren. | Jede öffentliche Parseraktion außer dem versteckten Legacy-`--auto` besitzt nichtleeren Hilfetext; `--dry-run`-Beschreibung wird ausdrücklich getestet.
NEW_FINDING: C-29 | BLOCKER | Leeres `--config` beziehungsweise `RUN_TASK_CONFIG` machte die optionale Defaultdatei fälschlich verpflichtend. | Leere Werte fallen auf die optionale Defaultauflösung zurück und ergeben ohne Datei `config_file is None`.
NEW_FINDING: C-30 | BLOCKER | Python 3.10 konnte beim dokumentierten Quellaufruf ohne installierte `tomli`-Abhängigkeit nicht starten. | Mindestversion konsistent auf Python 3.11 angehoben, Drittanbieterabhängigkeit entfernt und Metadaten/README automatisiert abgesichert.
NEW_FINDING: C-31 | OBSERVATION | Bestehende Parsertests erbten reale `RUN_TASK_*`-Variablen, CWD-Konfiguration und Live-State. | Autouse-Fixture isoliert Umgebung und CWD; zusätzliche Vollsuite mit injizierten Variablen bleibt grün.
NEW_FINDING: C-32 | OBSERVATION | Slice-MD erklärte R-13 trotz ausstehender Agentenbinarykonfiguration und Plattform-CI fälschlich für vollständig. | R-13 als teilweise ausgewiesen; offene Teile Slice 03 und 19 sowie Abdeckungsmatrix zugeordnet.
NEW_FINDING: C-33 | OBSERVATION | CLI- und Runtimekonstanten waren ohne Driftguard dupliziert. | Orchestrator importiert Task-, AGENTS- und Shared-Limit-Defaults aus `cli.py`; eigener Regressionstest schützt die Grenze.
NEW_FINDING: C-34 | OBSERVATION | Watch-Sicherheitsmeldungen fehlten und ein früher Konfigurationswarnpfad umging das Logformat. | Effektive Kanäle und automatisches Skip-Git werden geloggt; Konfigurationswarnungen werden erst nach `basicConfig` ausgegeben und im echten Subprozess geprüft.
NEW_FINDING: C-35 | OBSERVATION | Eine leere produktive Pfadklasse könnte die spätere Dateigrenze trivialisieren. | `paths.productive` darf nicht leer sein; README und späterer Slice-05/12-Vertrag halten unklassifizierte Pfade konservativ produktiv.

REVIEWER: claude
OPEN_FINDINGS: C-27,C-28,C-29,C-30,C-31,C-32,C-33,C-34,C-35
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | 0
PRE_MORTEM: Ein Python-3.10-Nutzer mit leerem RUN_TASK_CONFIG trifft zuerst einen fehlenden tomli-Import und danach eine irreführende Pflichtdatei; der unvollständige Hilfetext erklärt keinen Ausweg.
SLICE_APPROVAL: 02 | NO

### Implementiererantwort auf Runde 1

C-27 bis C-35 wurden innerhalb des freigegebenen Produktiv-, Dokumentations- und Testscopes umgesetzt. Die vollständige und die umgebungsinjizierte Suite bestehen mit 130 Tests. Re-Review ausstehend.

### Technisch fehlgeschlagener Re-Review-Versuch

Der erste Re-Review-Aufruf endete am 2026-08-11 um 11:20 Uhr nicht mit einem Verdikt, sondern mit einer maschinenlesbaren Fehlerhülle: `is_error:true`, HTTP-Status 429, Ergebnis `You've hit your session limit · resets 1:50pm (Europe/Berlin)`. Ein unmittelbar danach ausgeführter tool-loser Minimalaufruf mit festem Modell `sonnet` bestätigte denselben rollenweiten Zustand und Exitcode 1. Der Versuch zählt deshalb weder als Reviewrunde noch als Freigabe; Claude wird nicht durch eine andere Rolle ersetzt. Der Re-Review bleibt bis zum angekündigten Provider-Reset pausiert.

Um 13:50 Uhr bestätigte ein neuer tool-loser Minimalaufruf mit `is_error:false`, Ergebnis `CLAUDE_AVAILABLE` und Exitcode 0 die wiederhergestellte Verfügbarkeit. Gleichzeitig lagen neue Revision-6-Änderungen zur erst für Slice 14 vorgesehenen Quota-Wartepolitik in `requirements-orchestrator-modernization.md`, `handover-orchestrator-modernization.md` und Teilen des Arbeitsplans vor. Diese fremden, fachlich ausdrücklich nicht zu Slice 02 gehörenden Änderungen bleiben erhalten, werden aber aus Reviewfingerprint und Slice-02-Commit ausgeschlossen; nur die Slice-02-Hunks des gemeinsam genutzten Arbeitsplans gehören zum Slice-Scope.

### Runde 2 — Re-Review von C-27 bis C-35

Claude prüfte einen aus HEAD und ausschließlich den Slice-02-Dateien beziehungsweise -Hunks aufgebauten schreibgeschützten Snapshot. Damit waren die gleichzeitig im Originalarbeitsbaum vorhandenen Revision-6-Änderungen zur späteren Quota-Wartepolitik nicht Teil des Reviewgegenstands. Eigene Vollsuite: **131 passed, Exitcode 0**; die JSON-Hülle meldete `is_error:false`. Der Reviewer legte offen, dass zusätzliche zusammengesetzte Umgebungs- und Direktaufrufdiagnosen von der engen Bash-Allowlist abgelehnt wurden; die exakte Pflichtsuite lief zweimal erfolgreich und die betroffenen Aussagen wurden zusätzlich über Quellprüfung und In-Suite-Subprozesstests bewertet.

FINDING_STATUS: C-27 | CLOSED | README nennt die vereinheitlichten dynamischen Defaults, Positions-/Negationsformen und wird gegen Parserdefaults regressionsgeschützt.
FINDING_STATUS: C-28 | CLOSED | Alle öffentlichen Parseraktionen besitzen Hilfetexte; Altoptionsumfang und gerenderte Dry-Run-Hilfe sind abgesichert.
FINDING_STATUS: C-29 | CLOSED | Leere CLI-/Umgebungskonfiguration fällt korrekt auf die nächste Präzedenzstufe beziehungsweise optionale Defaultdatei zurück.
FINDING_STATUS: C-30 | CLOSED | Mindestversion, Code und README sind ohne Drittanbieterabhängigkeit konsistent auf Python 3.11 ausgerichtet.
FINDING_STATUS: C-31 | CLOSED | Beide CLI-Testmodule isolieren die vier produktiv gelesenen `RUN_TASK_*`-Variablen; bestehende Parsertests isolieren zusätzlich CWD und Live-State.
FINDING_STATUS: C-32 | CLOSED | R-13 ist wahrheitsgemäß als teilweise abgedeckt und den Slices 02, 03 und 19 zugeordnet.
FINDING_STATUS: C-33 | CLOSED | Gemeinsame Defaults stammen aus `cli.py` und werden über die Orchestrator-Kompatibilitätsgrenze getestet.
FINDING_STATUS: C-34 | CLOSED | Watch-Sicherheitsmeldungen und formatierte Konfigurationswarnungen sind wiederhergestellt und getestet.
FINDING_STATUS: C-35 | CLOSED | Leere produktive Pfadmuster werden abgelehnt; konservative Behandlung unklassifizierter Pfade bleibt für die vorgesehenen Konsumentenslices verbindlich dokumentiert.

NEW_FINDING: C-36 | OBSERVATION | Die alte operative Meldung beim automatischen Neustart nach einem abgeschlossenen `phase=done`-State ging beim Wrapperumbau verloren. | Parser kennzeichnet die automatisch aktivierte State-Ersetzung; der Einstiegspunkt meldet vor Workflowstart den abgeschlossenen State und den Start eines neuen Laufs; Test prüft Flags und Meldung.

REVIEWER: claude
OPEN_FINDINGS: C-36
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | 0
PRE_MORTEM: Ein Betreiber hält alte Artefakte für gültig, weil der automatische Neustart nach phase=done ohne Hinweis erfolgt.
SLICE_APPROVAL: 02 | NO

### Implementiererantwort auf C-36

`parse_args()` setzt bei automatisch ersetztem abgeschlossenem State nun `completed_state_replaced`; `run_cli()` protokolliert vor dem Workflowstart `Existing state is completed (phase=done); starting a new run.`. Der bestehende Completed-State-Test prüft neben `resume=False` und `force_overwrite_state=True` jetzt auch Marker und Meldung. Gezielte Suite: 46 passed; Vollsuite: 131 passed; `git diff --check`: grün.

### Claude-Review, Runde 3

Claude hat C-36 anhand des isolierten, schreibgeschützten Slice-Snapshots geschlossen. Als neuen reinen Dokumentationsbefund C-37 beanstandete der Review, dass der Auditnachweis den aktuellen Test-Diff-Fingerprint und die Validierung nach C-36 nicht ausdrücklich so bezeichnete.

```text
FINDING_STATUS: C-36 | CLOSED | Marker und Meldung sind auf den Completed-State-Neustart begrenzt und werden vor dem Workflowstart ausgegeben.
NEW_FINDING: C-37 | OBSERVATION | Der Auditnachweis ordnet Fingerprint und Validierung nicht ausdrücklich dem Teststand nach C-36 zu. | Test-Diff-Fingerprint nach C-36 und zugehörige VALIDATION_RESULT-Zeilen dokumentieren.
OPEN_FINDINGS: C-37
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | 0 (131 passed)
SLICE_APPROVAL: 02 | NO
STATUS: DONE
```

### Implementiererantwort auf C-37

Der unverändert reproduzierbare aktuelle Test-Diff-Fingerprint ist nun ausdrücklich als Stand nach C-36 bezeichnet; die gezielte Suite mit 46 Tests und die Vollsuite mit 131 Tests sind ebenfalls als Validierung nach C-36 dokumentiert. C-37 erfordert und bewirkt keine Code- oder Teständerung.

### Claude-Review, Runde 4

Claude hat C-36 erneut bestätigt, C-37 geschlossen und den Slice auf dem isolierten, schreibgeschützten Stand freigegeben. Die Vollsuite lief im Review mit 131 bestandenen Tests. Die zusammengesetzte Fingerprint-Berechnung war durch die enge Bash-Allowlist des Reviewers nicht ausführbar; der Implementierer hat denselben dokumentierten Befehl unmittelbar vor dem Review erfolgreich mit dem unveränderten Ergebnis `8297aa682af6451cc5de3436d709f4b81b40714c2081c109b8eea2184c3471e4` ausgeführt.

```text
FINDING_STATUS: C-36 | CLOSED | Completed-State-Marker und Meldung bleiben auf den phase=done-Ersetzungszweig begrenzt und werden vor Workflowstart ausgegeben.
FINDING_STATUS: C-37 | CLOSED | Fingerprint und beide VALIDATION_RESULT-Einträge sind dem Stand nach C-36 zugeordnet; C-37 bewirkte keine Code- oder Teständerung.
REVIEWER: claude
OPEN_FINDINGS: NONE
TEST_FILES_TOUCHED: tests/test_cli.py,tests/test_orchestrator_watch_cli.py
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | 0 (131 passed)
SLICE_APPROVAL: 02 | YES
STATUS: DONE
```

## Finaler Kontrollreview durch Antigravity

Antigravity CLI 1.1.12 hat den Claude-freigegebenen Slice in einem separaten, schreibgeschützten Snapshot mit dem expliziten Modell `gemini-3.1-pro-high` und hoher Denktiefe geprüft. Der Reviewer führte die vollständige Suite selbst aus und reproduzierte den dokumentierten Test-Diff-Fingerprint exakt.

```text
REVIEWER: antigravity
OPEN_FINDINGS: NONE
TEST_FILES_TOUCHED: tests/test_cli.py,tests/test_orchestrator_watch_cli.py
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | 0 (131 passed)
PRE_MORTEM: Eine fremde, bereits vorhandene orchestrator.toml kann wegen des strikten Schemas den Start blockieren; RUN_TASK_CONFIG beziehungsweise --config ermöglicht die explizite Auswahl einer anderen Datei.
SLICE_APPROVAL: 02 | YES
STATUS: DONE
```

Antigravity meldete keine neuen Findings. Als Restrisiken bleiben die absichtlich strikte Behandlung einer namensgleichen Fremdkonfiguration und das standardmäßige Auto-Resume eines vorhandenen unvollständigen States; beide Verhaltensweisen sind dokumentiert, konfigurierbar und getestet.

## Entscheidungstabelle

| Entscheidung | Status | Evidenz |
|---|---|---|
| Slice-Scope unter Dateigrenze | bestätigt | fünf produktive Programm-/Konfigurationsdateien geplant |
| Teständerungen freigegeben | bestätigt | Nutzerfreigabe vom 2026-08-11; an zwei Pfade, Intention und leeren Ausgangs-Fingerprint gebunden |
| Claude-Freigabe | bestätigt | Runde 4: C-36 und C-37 geschlossen, 131 Tests bestanden, `SLICE_APPROVAL: 02 | YES` |
| Antigravity-Freigabe | bestätigt | finaler Kontrollreview: keine Findings, 131 Tests, Fingerprint reproduziert, `SLICE_APPROVAL: 02 | YES` |
| Lokaler Slice-Commit | Bestandteil des Abschlusses | dieser unveränderte, doppelt freigegebene Stand wird gemeinsam mit der Prüfspur committed |
