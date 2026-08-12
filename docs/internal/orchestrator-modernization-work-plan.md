# Arbeitsplan: Modernisierung des Dual-Agent-Orchestrators

**Status:** Revision-6-/Revision-7-Stand freigegeben; Revision-8-Testausnahme, Revision-9-Validierungsattestierung und Revision-10-Claude-Reviewpolitik vom Nutzer verbindlich entschieden
**Anforderungsbasis:** `requirements-orchestrator-modernization.md`, Revision 10
**Feature-Branch:** `feature/orchestrator-modernization`
**Branch-Basis:** `master` bei `0bd3bad`
**GitHub-Status:** nur lokal; kein Upstream; Veröffentlichung ausstehend und nur nach Nutzerfreigabe
**Nummerierung:** Slices und Arbeitseinheiten sind durchgehend 1-basiert

---

## 1. Zielbild in eigenen Worten

Der bisherige Zwei-Phasen-Orchestrator wird zu einer zustandsbehafteten Slice-Pipeline mit drei nicht austauschbaren Rollen. Codex plant und implementiert. Claude prüft jede Korrekturrunde. Antigravity sieht ausschließlich einen von Claude freigegebenen Stand und autorisiert nach seinem Kontrollreview den lokalen Commit. Der Orchestrator erzwingt Rechte, Gates, Diff-Scope, Validierung, Prüfspur und Commit mechanisch. Kein Modell kann seine Rolle wechseln, sich selbst freigeben, einen roten Stand übergehen oder unerwartete Dateien committen.

Die Modernisierung erfolgt inkrementell. Tragfähige State-, Prozess- und Watcher-Infrastruktur bleibt erhalten; der monolithische Phasenablauf wird schrittweise durch explizite Work Units, strukturierte Contracts und eine testbare State-Maschine ersetzt. Nach jedem Slice bleibt der aktuelle Stand startbar und die vollständige Testsuite grün.

---

## 2. Branch-, Status- und Scope-Festlegung

- Der erforderliche Feature-Branch existiert bereits und ist aktiv.
- Der Branch wurde nicht auf GitHub veröffentlicht; Push und Merge sind ausdrücklich nicht Bestandteil dieses Plans.
- Vor dieser Planungsrunde war der Arbeitsbaum sauber.
- Gegenüber `master` enthielt der Branch ausschließlich die sieben Referenz- und Anforderungsdokumente unter `docs/internal/`.
- In der aktuellen Planungsrunde werden nur diese Dokumente geändert oder angelegt:
  - `docs/internal/requirements-orchestrator-modernization.md`
  - `docs/internal/handover-orchestrator-modernization.md`
  - `docs/internal/orchestrator-modernization-work-plan.md`
- Quellcode, Tests, Root-Rollendateien, `.orchestrator/state.json` und Checkpoints bleiben bis zur Freigabe dieses Plans unverändert.

---

## 3. Verbindliche Entscheidungen und Umsetzungskonsequenzen

### 3.1 Einstiegspunkt und Plattform

Python wird alleinige Quelle für CLI-, Resume-, Watch-, Testautodetektions- und Konfigurationslogik. `run_task` bleibt als dünner Kompatibilitätsstarter. Zugesichert werden Linux, macOS und WSL2; native Windows-Unterstützung bleibt bis zu eigener CI-/Integrationsevidenz unzugesichert.

Agentenbefehle werden mit der Priorität CLI-Option → Umgebungsvariable → optionale Repo-Konfiguration → Standard aufgelöst. Lokale absolute Pfade werden nicht in versionierte Konfiguration geschrieben. Die tatsächlich verwendeten Befehle und Versionen erscheinen im Preflight. Lokal sind `codex` 0.147.0, `claude` 2.1.226 sowie das native Linux-`agy` 1.1.12 und ein explizit konfigurierbarer `agy.exe`-Alternativpfad verfügbar.

Repository-Sandbox und CLI-Prozessumgebung werden getrennt modelliert. Für Reviewer bedeutet read-only präzise: versionierte Repositorydateien und die Git-Metadaten sind nicht veränderbar; notwendige temporäre Daten und private CLI-Runtime-/Logpfade werden über dedizierte Verzeichnisse außerhalb des Repositorys (`TMPDIR`, `XDG_CACHE_HOME` und adapterspezifische Variablen) beschreibbar gemacht. Provider-Egress und der von Antigravity benötigte lokale Loopback-Socket werden erlaubt, ohne Schreibrechte auf versionierte Repositoryinhalte zu eröffnen. Im normalen Review führt kein Modell die Vollsuite aus. Die Fähigkeitsevidenz nach Installation oder Versionswechsel beweist separat, dass das Read-only-Profil funktioniert und ein gezielter Schreibversuch auf eine versionierte Datei scheitert.

### 3.2 Repo-Konfiguration

Eine optionale, versionierbare `orchestrator.toml` beschreibt ausschließlich repoübergreifend sinnvolle deklarative Regeln:

- produktive, Test-, Dokumentations- und generierte Pfadmuster,
- fachliche Stop-Regeln mit stabiler ID und Beschreibung,
- Standardvalidierung und pfadabhängige Zusatzvalidierungen,
- optionaler manueller Slice-Gate-Modus.

Agentenmodelle, Timeouts und lokale Binarypfade bleiben per CLI oder Umgebung überschreibbar. Unbekannte Konfigurationsschlüssel und ungültige Pfadmuster führen zu einem lauten Konfigurationsfehler.

### 3.3 Commit-Autorisierung

Antigravity trifft die Freigabeentscheidung. Der Orchestrator führt den Commit danach ohne Modellermessen aus. Er vergleicht einen unmittelbar vor dem Antigravity-Review erfassten Diff-Fingerprint mit dem Stand vor dem Commit, prüft alle Pfade gegen den Slice-Scope, staged nur die exakte Allowlist und verifiziert Commit-Hash sowie Arbeitsbaum. Pauschales Staging ist ausgeschlossen.

### 3.4 Nutzer-Gates

Der Normalpfad benötigt nach Claude- und Antigravity-Freigabe kein drittes manuelles Slice-Verdikt. Zwingende Nutzerentscheidungen bleiben bei Teständerungen, Stop-Regeln, unerwarteten Dateien, vier erfolglosen Korrekturrunden, Ankeränderungen, Push und Merge. Für die Ausführung genau dieses Modernisierungsarbeitsplans sind die erforderlichen Teständerungen ab Slice 06 vorab autorisiert und halten nicht erneut an; das zu implementierende Zielverhalten aus R-10 bleibt davon unberührt. `--manual-slice-gate` fügt optional ein Gate vor jedem Slice-Commit ein.

Eine Quota mit eindeutig erkanntem, innerhalb der konfigurierten Maximalwartezeit liegendem Resetzeitpunkt ist bei aktivierter Wartepolitik kein Nutzergate. Der Orchestrator persistiert die Pause und setzt denselben Schritt nach Ablauf automatisch fort. Ohne verlässlichen Zeitpunkt oder nach ausgeschöpfter Wartepolitik bleibt Exitcode 2 mit manueller Fortsetzung über `--resume`.

### 3.5 Contracts und Findings

State-Version 3 akzeptiert nur die neuen, schrittbezogenen Marker. Jede Agentenantwort durchläuft denselben zentralen Contract-Validator. Findings werden als Records mit Quelle, Klasse, Status, Beschreibung, Akzeptanztest, Work Unit, Runde, Implementiererantwort und Schließbegründung gespeichert. `BLOCKER` blockiert; `OBSERVATION` bleibt sichtbar, blockiert aber nicht. Ein Codex-Widerspruch schließt kein Finding.

### 3.6 Prüfspur

Der Runtime-State ist während eines Laufs maschinenlesbare Wahrheit. Rohe Logs dienen nur der Diagnose. Der Orchestrator schreibt validierte Agentenantworten deterministisch in die vorgesehenen Abschnitte der Plan- und Slice-Dokumente. Nach dem Commit sind Git, Arbeitsplan und Slice-MD die historische Prüfspur; flüchtige Logs werden nicht als paralleles Auditformat behandelt.

Die maßgebliche Validierung ist ein eigener Orchestratorrecord, keine Agentenaussage. Je kanonischem Diff-Fingerprint führt der Orchestrator die erforderliche Matrix einmal aus und bindet Attestierungs-ID, Befehle, Exitcodes, Vollständigkeit, Ergebnisdigest und Fingerprint an Claude- und Antigravity-Review. Beide Reviewer investieren ihr Budget in die Implementierungsanalyse; nach einer Korrektur verfällt die alte Attestierung. Deterministisch projizierte Auditabschnitte sind vom fachlichen Reviewfingerprint getrennt, damit ein Revieweintrag nicht seine eigene Freigabe invalidiert.

### 3.7 Quota-Wartepolitik

Die Runtime-Parameter für diese Funktion werden erst in Slice 14 eingeführt und erweitern den bereits abgeschlossenen Konfigurationsslice 02 nicht. Ihre Präzedenz ist CLI → Umgebung → Standard. Der Normalmodus ist `wait-until-reset`, der Sicherheitszuschlag beträgt standardmäßig 60 Sekunden, die Maximalwartezeit 24 Stunden und die Zahl automatischer Fortsetzungen je blockiertem Schritt eins. Alle Werte einschließlich des nicht spamartigen Statusintervalls sind konfigurierbar; `manual` deaktiviert das automatische Warten vollständig. Ein nicht eindeutig normalisierbarer Providerzeitpunkt aktiviert niemals die Automatik.

### 3.8 Migration

Der Umbau erfolgt im Bestand. Abgeschlossene Version-2-States bleiben erkennbar. Aktive oder eingefrorene Version-2-States werden mit unveränderten Artefakten und einer klaren Handlungsanweisung abgelehnt, weil eine zuverlässige Abbildung des monolithischen Phase-2-Fortschritts auf Slices nicht möglich ist. Unbekannte Schemaversionen führen immer zu einem Fehler.

### 3.9 Ankerwerte

Die Infrastruktur unterstützt strukturierte `ANCHOR`-Records für deterministische Rechenkerne. Für diesen Umbau sind keine konkreten Ankerwerte erforderlich. Änderungen an einem bereits freigegebenen Anker wären ein zwingendes Nutzergate und würden die Planreviewkette zurücksetzen.

### 3.10 Widersprüche zu den historischen Referenzen

| Referenzregel | Entscheidung dieses Plans |
|---|---|
| Antigravity/Gemini ist primärer Reviewer, Claude optional | Claude ist Reviewer jeder Runde; Antigravity prüft nur den Claude-freigegebenen Stand |
| Antigravity editiert Reviewdokumente direkt | Reviewer-Prozesse sind read-only; der Orchestrator schreibt validiertes Feedback in den erlaubten Abschnitt |
| Antigravity führt Git selbst aus | Antigravity autorisiert; der Orchestrator staged und committet mechanisch |
| Nutzerreview vor jedem Slice-Commit | nur risikobedingte Gates verpflichtend; optional `--manual-slice-gate` |
| Plan wird nach Freigabe automatisch gepusht | kein Push ohne ausdrückliche Nutzerfreigabe |
| Rollback kann `git reset --hard HEAD` verwenden | kein Hard Reset; Arbeitsbaum und State bleiben beim Halt erhalten |
| Slice-Dateinamen in Großbuchstaben | neue Dateien verwenden englische, kleingeschriebene Namen gemäß Anforderungsdokument |
| `GEMINI.md` beschreibt Fallback | Root-Datei wird zu `ANTIGRAVITY.md`; historische Referenz bleibt unverändert |
| Anforderungen benennen `main` als Basisbranch | Das Repository verwendet `master`; der Orchestrator ermittelt und persistiert den tatsächlichen Basisbranch beziehungsweise dessen Merge-Base, statt `main` fest zu verdrahten |

---

## 4. Geplanter Modulzuschnitt

Die endgültigen Dateinamen dürfen im Slice-Review angepasst werden, solange die Verantwortungsgrenzen erhalten bleiben.

| Komponente | Verantwortung |
|---|---|
| `src/cli.py` | Argumente, Konfigurationspräzedenz, Python-Einstiegspunkt und Wrapperkompatibilität |
| `src/agent_adapters.py` | CLI-spezifische Befehle, Ausgabeextraktion, Streamfilter und Quota-Resetzeit-Erkennung |
| `src/agent_runtime.py` | Prozessausführung, role-bound read-only/implementer modes, Fehlerklassifikation und unterbrechbares Quota-Warten |
| `src/contracts.py` | Marker, Finding-Records, Anker und zentrale Contract-Validierung |
| `src/repo_changes.py` | Merge-Base, Branch-Diff, unversionierte Dateien, Scope und Committransaktion |
| `src/workflow_state.py` | State-Version 3, Work Units, Schritte, Quota-Wartezustand, Migration und Checkpoints |
| `src/audit_trail.py` | deterministische Plan-/Slice-Dokumentation und Entscheidungstabellen |
| `src/gates.py` | Test-, Stop-, Validierungs-, Iterations- und Nutzergates |
| `src/workflow.py` | asymmetrische Plan-, Slice- und Endreview-State-Maschine |
| `src/inbox_watcher.py` | Queue-Lifecycle und Integration definierter Pausen-/Fehlerzustände |
| `src/orchestrator.py` | schmaler Kompositions- und Kompatibilitätsrand während der Migration |

Große neue Sammelmodule werden vermieden. Wenn ein Slice eine Verantwortungsgrenze überschreitet oder mehr als zehn produktive Dateien benötigt, greift die Stop-Regel vor dem ersten Edit.

---

## 5. Slice-Liste

### 5.1 Geplante Slice-MD-Dateien

Jede Slice-MD wird unmittelbar vor Beginn ihres Slice angelegt und dann aus der jeweiligen Slice-Überschrift dieses Arbeitsplans als relativer Markdown-Link verknüpft. Bis zur Anlage bleibt der geplante Zielpfad hier bewusst als Code statt als toter Link notiert. Die Namen folgen durchgängig `slice-<thema>-<nummer>-<kurztitel>.md`:

| Slice | Geplanter Zielpfad |
|---:|---|
| 1 | [`docs/internal/slice-orchestrator-modernization-01-remove-agent-fallback.md`](slice-orchestrator-modernization-01-remove-agent-fallback.md) |
| 2 | [`docs/internal/slice-orchestrator-modernization-02-python-cli-config.md`](slice-orchestrator-modernization-02-python-cli-config.md) |
| 3 | [`docs/internal/slice-orchestrator-modernization-03-three-agent-adapters.md`](slice-orchestrator-modernization-03-three-agent-adapters.md) |
| 4 | `docs/internal/slice-orchestrator-modernization-04-root-bound-snapshots.md` |
| 5 | `docs/internal/slice-orchestrator-modernization-05-canonical-branch-diff.md` |
| 6 | [`docs/internal/slice-orchestrator-modernization-06-contract-finding-core.md`](slice-orchestrator-modernization-06-contract-finding-core.md) |
| 7 | [`docs/internal/slice-orchestrator-modernization-07-state-v3-work-units.md`](slice-orchestrator-modernization-07-state-v3-work-units.md) |
| 8 | [`docs/internal/slice-orchestrator-modernization-08-audit-trail.md`](slice-orchestrator-modernization-08-audit-trail.md) |
| 9 | `docs/internal/slice-orchestrator-modernization-09-branch-commit-transaction.md` |
| 10 | `docs/internal/slice-orchestrator-modernization-10-asymmetric-review-chain.md` |
| 11 | `docs/internal/slice-orchestrator-modernization-11-test-change-gate.md` |
| 12 | `docs/internal/slice-orchestrator-modernization-12-stop-rules-file-limit.md` |
| 13 | `docs/internal/slice-orchestrator-modernization-13-path-validation-matrix.md` |
| 14 | `docs/internal/slice-orchestrator-modernization-14-agent-failures-resume.md` |
| 15 | `docs/internal/slice-orchestrator-modernization-15-scripted-dry-run.md` |
| 16 | `docs/internal/slice-orchestrator-modernization-16-branch-final-review.md` |
| 17 | `docs/internal/slice-orchestrator-modernization-17-watch-mode-pauses.md` |
| 18 | `docs/internal/slice-orchestrator-modernization-18-default-cutover-contract.md` |
| 19 | `docs/internal/slice-orchestrator-modernization-19-user-docs-consistency.md` |

Die Audit-Komponente aus Slice 8 prüft bei Start eines Slice, dass genau diese Datei existiert, vom Arbeitsplan verlinkt wird und die Pflichtabschnitte aus §9.2 der Anforderungen enthält.

### [Slice 1 — Ersatzagentenpfad entfernen](slice-orchestrator-modernization-01-remove-agent-fallback.md)

**Zweck:** Den erzwungenen und optionalen Claude→Gemini-Fallback vollständig entfernen, bevor neue Rollen eingeführt werden.
**Anforderungen:** R-1; Grundlage für R-18.
**Tatsächlich betroffene Dateien vor Review:** `.gitattributes`, `run_task`, `src/orchestrator.py`, `src/agent_runtime.py`, `README.md`, `tests/test_agent_runtime.py`, `tests/test_inbox_watcher.py`, `tests/test_orchestrator_quota.py`, `tests/test_orchestrator_watch_cli.py` sowie Plan- und Slice-Prüfspur.
**Umsetzungsstatus:** Implementierung und Reviews abgeschlossen; C-16 bis C-26 geschlossen; Antigravity ohne neue Findings, lokaler Slice-Commit autorisiert.

**Akzeptanzkriterien:**

- `--allow-fallback-to-gemini` existiert weder im Wrapper noch im Python-Parser.
- Kein Laufzeitpfad ersetzt eine ausgefallene Instanz durch eine andere.
- Quota-/Aufruffehler nennen die tatsächlich ausgefallene Rolle.
- Fehlende, für den aktuellen alten Schritt nicht benötigte CLIs blockieren diesen Schritt nicht.
- `./run_task --dry-run` beendet den bestehenden Ablauf erfolgreich.
- README und CLI-Hilfe beschreiben, dass kein Ersatzagent mehr aufgerufen wird.

**Tests:** Entfernen der Fallbacktests; neue Tests für fail-fast ohne Substitution und bedarfsgerechte Agentenprüfung; vollständige Suite.
**Abhängigkeiten:** keine; zwingend erster Implementierungsslice.
**Test-Riegel:** ja. **Red-State:** nein.
**Risiko/Rückfalloption:** Quota-Verhalten kann Watch-Modus beeinflussen; Änderungen bleiben auf die explizit genannten Dateien begrenzt und werden bei Fehlschlag dateiweise zurückgenommen.

### [Slice 2 — Python-Einstiegspunkt und deklarative Konfiguration](slice-orchestrator-modernization-02-python-cli-config.md)

**Zweck:** Bash-Geschäftslogik durch einen portablen Python-Einstiegspunkt mit klarer Konfigurationspräzedenz ersetzen.
**Anforderungen:** R-13; Grundlage für R-2, R-15 und R-16.
**Voraussichtlich betroffene Dateien:** `run_task`, `src/cli.py` (neu), `src/orchestrator.py`, `pyproject.toml`, `orchestrator.toml` (Beispiel/Default, falls erforderlich), `README.md`, `tests/test_cli.py` (neu), `tests/test_orchestrator_watch_cli.py`.
**Tatsächlich betroffene Dateien vor Review:** `run_task`, `src/cli.py`, `src/orchestrator.py`, `pyproject.toml`, `orchestrator.toml`, `README.md`, `tests/test_cli.py`, `tests/test_orchestrator_watch_cli.py` sowie Plan- und Slice-Prüfspur.
**Umsetzungsstatus:** implementiert und doppelt freigegeben; Claude C-27 bis C-37 geschlossen, Antigravity ohne Findings, Vollsuite jeweils 131 Tests.

**Akzeptanzkriterien:**

- Testautodetektion, Resume-Entscheidung, Watch-Defaults und Argumentweitergabe liegen nur noch in Python.
- `run_task` enthält keine Fallunterscheidung des Workflows und startet nur den Python-Einstiegspunkt.
- CLI → Umgebung → Repo-Konfiguration → Standard ist getestet.
- Unbekannte Schlüssel und ungültige Konfiguration scheitern mit verständlicher Meldung.
- Pfade und Befehle werden ohne Bash-4-spezifische Syntax verarbeitet; der Python-Einstiegspunkt setzt Python 3.11 oder neuer voraus.
- README dokumentiert Einstiegspunkt, Konfigurationspräzedenz und den zu diesem Slice tatsächlich unterstützten Plattformstand.

**Tests:** Parser-, Präzedenz-, Auto-Detection-, Wrapper- und Plattformpfadtests; vollständige Suite. macOS-CI wird vorbereitet, WSL2 lokal geprüft.
**Abhängigkeiten:** Slice 1.
**Test-Riegel:** ja. **Red-State:** nein.
**Risiko/Rückfalloption:** Wrapperregression; alter Aufrufvertrag wird durch Kompatibilitätstests geschützt.

### [Slice 3 — Konfigurierbare Drei-Agenten-Adapter und Rollenrechte](slice-orchestrator-modernization-03-three-agent-adapters.md)

**Zweck:** Gemini durch Antigravity ersetzen, alle Adapter aktuell und konfigurierbar machen und Reviewer-Prozesse technisch read-only starten.
**Anforderungen:** R-2, R-3; Teil von R-13.
**Voraussichtlich betroffene Dateien:** `src/agent_adapters.py`, `src/agent_runtime.py`, `src/cli.py`, optional `src/agent_config.py` (neu), `tests/test_agent_runtime.py`, `tests/test_agent_adapters.py` (neu), `tests/test_cli.py`.
**Umsetzungsstatus:** Implementierung und ursprüngliche 154 Tests grün; Claude und Antigravity haben den Slice freigegeben. Die quotaorientierte Nachschärfung nach Slice 05 ist mit 182 Tests und vollständigem Antigravity-Abschlussreview ebenfalls doppelt freigegeben.

**Akzeptanzkriterien:**

- Registry enthält exakt `codex`, `claude` und `antigravity`; kein Gemini-Backend bleibt erreichbar.
- Antigravity unterstützt konfigurierbar `agy`, `agy.exe` oder einen expliziten Pfad sowie den werttragenden `--print <prompt>`-Aufruf und parsbare Ausgabe; Optionen werden vor `--print` angeordnet, damit kein Optionsname versehentlich als Prompt verarbeitet wird.
- Unter WSL2 wird das native Linux-`agy` bevorzugt; `agy.exe` bleibt ein explizit konfigurierbarer Alternativbefehl derselben Rolle.
- Agentenbefehle werden erst vor dem Schritt der jeweiligen Rolle verbindlich geprüft; eine fehlende Antigravity-Binary blockiert Codex- und Claude-Schritte nicht vorzeitig.
- Modell und Timeout sind je Rolle konfigurierbar; kein Modellname ist im Adapter fest verdrahtet.
- Headless-Aufrufe setzen das konfigurierte Modell und eine harte Prozesszeitgrenze explizit, verwenden eine maschinenlesbare Ausgabehülle und protokollieren CLI-Fehler einschließlich Berechtigungsablehnungen statt leeren Text als Agentenantwort zu behandeln.
- CLI-Warnungen über wirkungslose oder inkompatible Optionen gelten als fehlgeschlagener Fähigkeitstest; insbesondere darf Antigravitys Hinweis, dass `--mode plan` zusammen mit `--disable-slash-commands` wirkungslos ist, nicht als wirksames Read-only-Profil verbucht werden.
- Codex erhält Implementiererrechte; Claude und Antigravity erhalten technisch erzwungenen Lesezugriff.
- Reviewer können weder Code, Tests noch Dokumente direkt verändern.
- Verfügbarkeit eines Werkzeugs und dessen Berechtigung werden getrennt konfiguriert. Reviewer erhalten im Normalreview nur die für die Implementierungsanalyse erforderlichen Leserechte; Validierungsbefehle sind ausschließlich über explizite, getrennte Diagnosebefehle der Adapter freigegeben. Ein interaktiver Planmodus wird nicht als Berechtigungskonzept für headless Läufe verwendet.
- CLI-Runtime-, Log- und Tempverzeichnisse sind separat beschreibbar; Antigravity kann seinen lokalen Language-Server-Loopback öffnen, während der Repositoryzugriff read-only bleibt.
- Jede Reviewerrolle besteht nach Installation oder Versionswechsel einen getrennten Fähigkeitssmoke: Die vollständige Suite ist im definierten Read-only-Profil (`PYTHONDONTWRITEBYTECODE=1`, pytest ohne Cacheprovider, externe Temp-/Cachepfade) ausführbar und ein gezielter Schreibversuch auf eine versionierte Repositorydatei scheitert. Dieser Nachweis wird nicht bei jedem fachlichen Review wiederholt.
- Minimalprompts aller drei Adapter liefern den Abschlussmarker; ein eigener Isolationstest erzwingt je Reviewer mindestens einen tatsächlich erlaubten Werkzeugaufruf, damit ein tool-loser Smoke nicht fälschlich nur Authentifizierung und Erreichbarkeit bestätigt. Automatisierte Tests verwenden Fake-CLIs, Live-Smokes werden nach Installation oder Versionsänderung separat protokolliert.
- Befehl, Version und Fähigkeiten werden geprüft. Eine noch nicht freigegebene Version hält mit Nutzergate an, bis die Kompatibilitätsmatrix erfolgreich ist.
- Der vorgesehene Eingabekanal verarbeitet einen realistischen Langprompt, ohne Betriebssystemgrenzen für Kommandozeilenargumente zu überschreiten oder Prompttext in der Prozessliste offenzulegen.
- Claude erhält ein einziges externes, manifestiertes Reviewpaket statt offener Repositoryerkundung. Es wird verlustfrei in begrenzte nummerierte Chunks zerlegt. Im Normalreview ist nur `Read` verfügbar; der Auftrag erlaubt exakt einen Leseaufruf für Manifest und jeden Chunk, begrenzt das kleine JSON-Schema auf 12.000 Zeichen und deaktiviert Plugins, MCPs, Promptvorschläge sowie Sessionpersistenz. Der Adapter akzeptiert dabei neutral die v2-Testsnapshot- oder v3-Attestierungsevidenz des aufrufenden Workflows. Der Harness gehört nur zum expliziten Adapter-/Versionsdiagnosebefehl.

**Tests:** Befehlsaufbau, Ausgabeextraktion, Text-/JSON-/Streamfilter, Binaryauflösung, Version/Fähigkeiten, Timeout/Modell, Auth-/Quota-/Netz-/Berechtigungsfehler, tool-loser Minimalprompt getrennt vom erzwungenen Werkzeug-Isolationstest, verlustfreie Langprompt-Segmentierung mit dynamischem Lesebudget, Prozessende, einmaliger Fähigkeitssmoke mit Vollsuite im Read-only-Profil, negativer Schreibversuch auf eine versionierte Datei je CLI und Live-Smoke-Checkliste; vollständige Suite.
**Abhängigkeiten:** Slice 2.
**Test-Riegel:** ja. **Red-State:** nein.
**Risiko/Rückfalloption:** CLI-Versionen und benötigte Prozessressourcen unterscheiden sich je Plattform; adapterspezifische Abweichungen bleiben hinter dem gemeinsamen Protocol isoliert. Ein unbekannter Versions- oder Fähigkeitsstand führt zum Nutzergate statt zu optimistischer Ausführung.

### [Slice 4 — Wurzelgebundene Dateischnappschüsse](slice-orchestrator-modernization-04-root-bound-file-snapshots.md)

**Zweck:** Jeden vom Agenten oder Contract gemeldeten Pfad vor dem Lesen gegen die Repositorywurzel und erlaubte Pfadklassen validieren.
**Anforderungen:** R-4.
**Voraussichtlich betroffene Dateien:** `src/agent_runtime.py`, optional `src/path_policy.py` (neu), `src/state_io.py` nur zur Wiederverwendung gemeinsamer Logik, `tests/test_agent_runtime.py`, optional `tests/test_path_policy.py` (neu).
**Tatsächlich betroffene Dateien vor Review:** `src/path_policy.py` (neu), `src/agent_runtime.py`, `src/orchestrator.py`, `src/state_io.py`, `tests/test_agent_runtime.py`, `tests/test_path_policy.py` (neu), Slice-MD und dieser Arbeitsplan.
**Umsetzungsstatus:** Claude und Antigravity haben mit `OPEN_FINDINGS: NONE` freigegeben; die einzige umgesetzte Observation (Spezialdatei-Test) ist mit 170 Tests validiert; lokaler Slice-Commit autorisiert.

**Akzeptanzkriterien:**

- `../`, absolute Fremdpfade, Symlink-Ausbrüche und äquivalente Normalisierungsfälle werden nicht gelesen.
- Gültige Repositorypfade funktionieren auf POSIX- und WSL-Pfaden.
- Ablehnungen werden protokolliert, ohne fremden Dateiinhalt in den Prompt zu übernehmen.
- State- und Snapshot-Pfadvalidierung verwenden dieselbe zentrale Primitive.

**Tests:** Traversal, Symlink, absolute Pfade, gemischte Separatoren, fehlende und gültige Dateien; vollständige Suite.
**Abhängigkeiten:** Slice 2.
**Test-Riegel:** ja. **Red-State:** nein.
**Risiko/Rückfalloption:** Zu strenge Normalisierung kann legitime Pfade blockieren; Positivfälle werden explizit abgedeckt.

### [Slice 5 — Kanonische Branch-Diff-Quelle](slice-orchestrator-modernization-05-canonical-branch-diff.md)

**Zweck:** Eine einzige git-basierte Quelle für alle seit Branch-Basis geänderten Dateien und Inhalte bereitstellen.
**Anforderungen:** R-5; Voraussetzung für R-9, R-10 und R-15.
**Voraussichtlich betroffene Dateien:** `src/repo_changes.py` (neu), `src/agent_runtime.py`, `src/orchestrator.py`, `tests/test_repo_changes.py` (neu), `tests/test_agent_runtime.py`.
**Tatsächlich betroffene Dateien vor Review:** `src/repo_changes.py` (neu), `src/agent_runtime.py`, `src/orchestrator.py`, `tests/test_repo_changes.py` (neu), `tests/test_agent_runtime.py`, Slice-MD und dieser Arbeitsplan.
**Umsetzungsstatus:** implementiert, mit 182 Tests sowie Git-/Nicht-Git-Dry-Run validiert und durch Claude sowie Antigravity freigegeben; F-001 geschlossen, F-002 für Slice 10 vorgemerkt, lokaler Slice-Commit autorisiert.

**Akzeptanzkriterien:**

- Die Branch-Basis wird über Merge-Base bestimmt und explizit an die Diff-Funktion übergeben.
- Uncommitted Änderungen, bereits committete Slices und neue unversionierte Dateien erscheinen gemeinsam und ohne Duplikate.
- Löschungen und Umbenennungen bleiben erkennbar.
- Unversionierte, von Git ignorierte Pfade werden aus Diff, Fingerprint, Produktivdateigrenze und dem Gate „unerwartete Datei“ ausgeschlossen; Änderungen an bereits versionierten Dateien zählen unabhängig von Ignore-Regeln weiterhin.
- Agentenberichte sind nur Zusatzsignal und können den Git-Befund nicht einschränken.
- Pfadlisten und Diff-Fingerprint sind stabil und deterministisch.

**Tests:** temporäre Git-Repos für die drei Pflichtzustände aus R-5 plus Löschung, Rename, Divergenz, ignorierte unversionierte Artefakte und versionierte Dateien mit späterer Ignore-Regel; vollständige Suite.
**Abhängigkeiten:** Slice 4.
**Test-Riegel:** ja. **Red-State:** nein.
**Risiko/Rückfalloption:** Git-Eckfälle; Implementierung bleibt in einem eigenständigen Modul und ersetzt alte Aufrufer erst nach bestandenen Vergleichstests.

### [Slice 6 — Zentraler Contract- und Finding-Kern](slice-orchestrator-modernization-06-contract-finding-core.md)

**Zweck:** Marker, Findings, Implementiererantworten, Anker und Verdiktkonsistenz in einem einzigen typisierten Contract-Modell zusammenführen.
**Anforderungen:** R-6; Entscheidung §7.5 und §7.6; Voraussetzung für R-7.
**Voraussichtlich betroffene Dateien:** `src/contracts.py` (neu), `src/prompts.py`, `src/orchestrator.py`, `tests/test_parsing.py`, `tests/test_prompts.py`, optional `tests/test_contracts.py` (neu).
**Tatsächlich betroffene Dateien vor Review:** `src/contracts.py` (neu), `src/prompts.py`, `src/orchestrator.py`, `tests/test_contracts.py` (neu), `tests/test_parsing.py`, `tests/test_prompts.py`, Anforderungs-, Arbeitsplan-, Übergabe- und Slice-MD.
**Umsetzungsstatus:** lokal implementiert und mit 236 Tests, Compile, Diffcheck sowie aktivem v2-Dry-Run validiert. Claude F-001/F-002 sind korrigiert und formal geschlossen; `PHASE2_APPROVAL: YES`. Antigravity prüfte den vollständigen Slice-Diff seit `021a2aa` und erteilte `SLICE_APPROVAL: 06 | YES` bei `OPEN_FINDINGS: NONE`. Lokaler Slice-Commit autorisiert. Die Teständerungen sind durch die arbeitsplanweite Revision-8-Ausnahme autorisiert.

**Akzeptanzkriterien:**

- Jeder Agentenaufruf wird mit einem expliziten Schrittcontract validiert.
- Finding-Records verlieren beim Öffnen, Bestreiten, Herabstufen und Schließen keine Felder.
- `BLOCKER` und `OBSERVATION` wirken entsprechend ihrer Klasse.
- `FINDING_RESPONSE: REJECTED` lässt das Finding offen.
- Jede gültige Reviewantwort enthält mindestens einen Finding-/Prüfrecord. Gibt es keine konkrete Schwäche, ist stattdessen ein nicht blockierender `OBSERVATION`-Record mit geprüften Dimensionen, größtem Restrisiko und realistischer Bruchbedingung Pflicht; reine pauschale Zustimmung ist ungültig.
- Freigaben benötigen ein vollständiges Pre-Mortem, ein parsbares Verdikt, passende Validierung, zulässigen Teststatus und keine offenen Blocker.
- `STOP_REQUESTED` und ein Freigabemarker können nicht gemeinsam gültig sein.
- State-v3-Contracts akzeptieren keine Phase- oder Legacy-Marker.
- Strukturierte Anker werden geparst und Änderungen nach Planfreigabe erkannt.
- Der neue Contract entsteht additiv hinter dem expliziten Entwicklungsmodus. Aktive Prompts, Marker und Parsersemantik des Altpfads bleiben bis zum gemeinsamen Cutover in Slice 18 unverändert.

**Tests:** tabellengetriebene Marker-/Konsistenztests, kompletter Finding-Lebenszyklus, pauschale Zustimmung ohne Prüf-/Findingrecord, vollständiger No-Finding-Observation-Record, fehlendes Pre-Mortem, fehlende/unparsbare Verdikte, Ankeränderung sowie Regressionstests für den unveränderten aktiven Altcontract; vollständige Suite.
**Abhängigkeiten:** Slice 1.
**Test-Riegel:** ja. **Red-State:** nein.
**Risiko/Rückfalloption:** Contract-Schnitt betrifft viele Prompts; zentrale Tests werden vor der Aufrufermigration aufgebaut, der bestehende Ablauf bleibt bis Slice 18 startbar und unverändert aktiv.

### [Slice 7 — State-Version 3 und Work Units](slice-orchestrator-modernization-07-state-v3-work-units.md)

**Zweck:** Einen expliziten State für Plan, Slices, Korrektureinheiten, Reviewer-Schritte, Gates und Resume einführen.
**Anforderungen:** R-8; Voraussetzung für R-18.
**Voraussichtlich betroffene Dateien:** `src/workflow_state.py` (neu), `src/state_io.py`, `src/orchestrator.py`, `tests/test_state_io.py`, `tests/test_workflow_state.py` (neu).
**Tatsächlich betroffene Dateien vor Review:** `src/workflow_state.py` (neu), `src/state_io.py`, `src/orchestrator.py`, `tests/test_state_io.py`, `tests/test_workflow_state.py` (neu), Arbeitsplan-, Übergabe- und Slice-MD.
**Umsetzungsstatus:** lokal additiv implementiert; 52 fokussierte State-/I/O-Tests, 277 Tests der Vollsuite, Compile, Diffcheck und aktiver v2-Dry-Run sind grün. Claude F-001 (fehlender Test des unvollständigen v2-States) ist korrigiert und formal geschlossen; `PHASE2_APPROVAL: YES`. Antigravity prüfte den vollständigen finalen Slice-Diff seit `4364c11` und erteilte `SLICE_APPROVAL: 07 | YES` bei `OPEN_FINDINGS: NONE`; lokaler Slice-Commit autorisiert. Die Teständerungen sind durch die arbeitsplanweite Revision-8-Ausnahme autorisiert.

**Akzeptanzkriterien:**

- State-Version 3 führt 1-basierte Work-Unit- und Slice-IDs, aktuellen Schritt, vier-Runden-Zähler, Gatezustand, Branch-Basis und Commitreferenz.
- Checkpointnamen enthalten Work Unit, Slice und Runde und kollidieren nicht.
- Schreiben und Laden bleiben atomar und wurzelgebunden.
- Ein abgeschlossener Version-2-State bleibt als abgeschlossen erkennbar.
- Aktive/eingefrorene Version-2-States und unbekannte Versionen scheitern laut und ohne Datenverlust.
- Resume setzt am gespeicherten Schritt fort und wiederholt keinen bereits persistierten Seiteneffekt.

**Tests:** Initialisierung, atomare Writes, Checkpoints, abgeschlossener v2-State, Ablehnung aktiver v2-/unbekannter States und Resume an jedem Schritt; vollständige Suite.
**Abhängigkeiten:** Slice 6.
**Test-Riegel:** ja. **Red-State:** nein.
**Risiko/Rückfalloption:** Stateverlust ist kritisch; v2-Dateien werden nie überschrieben, bevor die Version erfolgreich validiert ist.

### [Slice 8 — Deterministische Plan- und Slice-Prüfspur](slice-orchestrator-modernization-08-audit-trail.md)

**Zweck:** Strukturierte Laufereignisse ohne doppelte manuelle Pflege in Plan- und Slice-Dokumente projizieren.
**Anforderungen:** R-17; Teil von R-3 und R-8.
**Voraussichtlich betroffene Dateien:** `src/audit_trail.py` (neu), `src/contracts.py`, `src/workflow_state.py`, `tests/test_audit_trail.py` (neu), `tests/test_language_consistency.py` nur falls Dateierkennung erweitert werden muss.
**Tatsächlich betroffene Dateien vor Review:** `src/audit_trail.py` (neu), `src/repo_changes.py`, `tests/test_audit_trail.py` (neu), `tests/test_repo_changes.py`, Arbeitsplan, Übergabe und Slice-MD. Die bestehenden typisierten Contracts und die idempotenten v3-Seiteneffekt-Schlüssel reichen als Eingabe- und Resume-Grenze aus; `src/contracts.py` und `src/workflow_state.py` bleiben deshalb unverändert.
**Umsetzungsstatus:** additive Auditprojektion implementiert; finale Validierungsattestierung und Reviews werden in der Slice-MD verwaltet.

**Akzeptanzkriterien:**

- Plan- und Slice-Dateien entstehen 1-basiert mit den Pflichtabschnitten aus §9.2 der Anforderungen.
- Vor Start jedes Slice prüft die Audit-Komponente den in §5.1 festgelegten Zielpfad, die aktive relative Verlinkung aus diesem Arbeitsplan und die vollständige Pflichtstruktur der Slice-MD.
- Reviewerfeedback wird nur nach erfolgreicher Contract-Validierung in den exakt vorgesehenen Abschnitt geschrieben.
- Freier Reviewerinhalt kann keine anderen Dateien oder geschützten Dokumentabschnitte überschreiben.
- Entscheidungstabelle, Validierung, Testfreigabe, Pre-Mortem und Findings werden aus strukturierten Records aktualisiert.
- Eine strukturierte `VALIDATION_ATTESTATION` mit Attestierungs-ID, kanonischem Diff-Fingerprint, Matrixbefehlen, Exitcodes, Vollständigkeit, Kurzresultat und Ausgabedigest wird als eigenes Laufereignis projiziert; Reviewtexte können diese Felder nicht überschreiben.
- Deterministisch verwaltete Auditabschnitte sind vom fachlichen Reviewgegenstand getrennt, damit die Projektion einer bereits validierten Reviewerantwort ihre eigene Fingerprintbindung nicht invalidiert.
- Wiederholtes Rendern ist idempotent.
- Rohe Logs sind nicht zweite Audit-Source-of-Truth.

**Tests:** Golden-/Strukturtests, idempotentes Rendern, Abschnittsinjektion, Sonderzeichen, ungültiger Zielpfad und vollständiger Finding-Lebenszyklus; vollständige Suite.
**Abhängigkeiten:** Slice 6 und Slice 7.
**Test-Riegel:** ja. **Red-State:** nein.
**Risiko/Rückfalloption:** Dokumentverlust; vor jeder Aktualisierung erfolgt atomisches Schreiben, bestehende nicht verwaltete Abschnitte bleiben erhalten.

### Slice 9 — Branch- und Committransaktion

**Zweck:** Feature-Branch, Slice-Scope und den von Antigravity autorisierten lokalen Commit sicher und reproduzierbar verwalten.
**Anforderungen:** R-9; Teil von R-17.
**Voraussichtlich betroffene Dateien:** `src/repo_changes.py`, `src/workflow_state.py`, optional `src/git_service.py` (neu, falls Trennung nötig), `tests/test_repo_changes.py`, `tests/test_workflow_state.py`.

**Akzeptanzkriterien:**

- Planungsstart erstellt oder prüft einen zulässigen Feature-Branch und dokumentiert lokalen/Remote-Status.
- Vor dem ersten Slice-Edit werden Branch, Status und Diff-Risiko persistiert.
- Der Preflight unterscheidet neue Läufe von Resume: Ein neuer Lauf verlangt standardmäßig einen sauberen Ausgangsstand; beim Resume werden die aktuellen Änderungen gegen persistierten Slice-Scope, Ausgangsfingerprint und Gatezustand geprüft. Erwartete In-Scope-Änderungen eines pausierten Slice sind zulässig, scope-fremde oder seit dem Halt unerwartet veränderte Pfade blockieren weiterhin.
- Commit erfordert Claude- und Antigravity-Freigabe sowie eine vollständige grüne Orchestrator-Attestierung für denselben kanonischen Diff-Fingerprint.
- Unerwartete oder nach Review veränderte Dateien blockieren.
- Nur erlaubte Pfade werden staged; keine pauschale Add-Operation.
- Commitmessage, Slice-ID und resultierender Hash werden dokumentiert.
- Push und Merge sind nicht implementiert beziehungsweise nur als explizit gesperrte Nutzeraktionen vorhanden.

**Tests:** temporäre Repos für Branchabweichung, unerwartete Datei, geänderten Fingerprint, exaktes Staging, Commitzuordnung, bereits committierten Vorgängerslice sowie Resume mit zulässigem dirty In-Scope-Stand und blockierendem scope-fremdem Dirty-Stand; vollständige Suite.
**Abhängigkeiten:** Slice 5, Slice 7 und Slice 8.
**Test-Riegel:** ja. **Red-State:** nein.
**Risiko/Rückfalloption:** Fremde lokale Änderungen; der Commitpfad arbeitet ausschließlich mit exakten Pfaden und lässt bei Ablehnung den Arbeitsbaum unverändert.

### Slice 10 — Asymmetrische Plan- und Slice-Reviewkette

**Zweck:** Codex→Claude↔Codex→Antigravity als gemeinsame Work-Unit-State-Maschine implementieren.
**Anforderungen:** R-7; zentrale Teile von R-8.
**Voraussichtlich betroffene Dateien:** `src/workflow.py` (neu), `src/orchestrator.py`, `src/prompts.py`, `src/contracts.py`, `src/workflow_state.py`, `src/audit_trail.py`, `tests/test_workflow.py` (neu), `tests/test_prompts.py`.

**Akzeptanzkriterien:**

- Codex erstellt Plan/Slice und implementiert, gibt aber niemals eigene Arbeit frei.
- Claude wird in jeder Korrekturrunde aufgerufen.
- Claude verwendet Sonnet mit Effort `high`. Sein erster Aufruf erhält nur den vollständigen Änderungsdiff des aktuellen Slice, keine unveränderten Repositorydateien; Folgerunden erhalten nur den Korrekturdelta seit Claudes zuletzt geprüftem Fingerprint plus Finding-Records und neue Attestierung.
- Reine Ausgabecontract-/Markerfehler werden aus der abgelehnten Antwort und dem Contract repariert. Sie lösen weder ein neues fachliches Review noch eine erneute Übertragung der Implementierungsevidenz aus.
- Antigravity wird erst nach Claude-Freigabe und je Anlauf genau einmal aufgerufen.
- Antigravity erhält dabei immer den vollständigen Diff des aktuellen Slice seit dem persistierten Slice-Start-Commit. Der letzte Korrekturdelta darf hervorgehoben werden, ersetzt aber weder frühere Slice-Dateien noch den vollständigen Fingerprint.
- Antigravity-Rückgabe führt zu Codex und anschließend wieder zu Claude.
- Zwei Codex-Nachbesserungen ergeben drei Claude- und einen Antigravity-Aufruf.
- Fehlendes oder unparsbares Verdikt ist Ablehnung, kein Überspringen.
- Freigaben beziehen sich nachweislich auf denselben Diff-Fingerprint.
- Vor dem ersten Claude-Aufruf und nach jeder inhaltlichen Codex-Korrektur muss eine passende Orchestrator-Attestierung vorliegen. Claude und Antigravity konsumieren dieselbe Attestierung und starten weder Vollsuite noch Review-Harness erneut.
- State-v3-Agentenantworten mit selbst behauptetem `VALIDATION_RESULT` sind ungültig; Validierung ist Contract-Eingabe, kein Modellverdikt. Fehlende, rote, unvollständige oder fingerprintfremde Attestierung hält vor der Freigabe fail-closed.
- Ein Reviewer fordert zusätzliche gezielte Validierung ausschließlich über ein Finding-Akzeptanzkriterium an; Ausführung und Attestierung erfolgen mechanisch vor der nächsten Reviewrunde.
- Prompts erhalten eine strukturierte, destillierte Sicht auf Planentscheidungen und den aktuellen Slice; die relevante Finding-/Reviewhistorie bleibt als Records erhalten und wird nicht durch blindes Abschneiden des ältesten Textes verfälscht.
- Die neue Engine bleibt bis einschließlich Slice 17 hinter einem expliziten Entwicklungsmodus; der bestehende Defaultpfad bleibt startbar.

**Tests:** deterministische Fake-Agent-Sequenzen für Freigabe, mehrfache Claude-Runden, Antigravity-Rückgabe, fehlendes Verdikt und Resume; ein Mehr-Runden-Fall beweist, dass Antigravity sowohl einen ausschließlich in Runde 1 geänderten Pfad als auch den letzten Korrekturpfad im vollständigen Slice-Paket erhält; vollständige Suite.
**Abhängigkeiten:** Slice 3, Slice 6, Slice 7, Slice 8 und Slice 9.
**Test-Riegel:** ja. **Red-State:** nein.
**Risiko/Rückfalloption:** Aufrufreihenfolge; State-Transitions werden als geschlossene Tabelle getestet und noch nicht als Default aktiviert.

### Slice 11 — Teständerungsriegel und Nutzerfreigabe

**Zweck:** Jede geänderte oder unversionierte Testdatei vor dem Review erkennen und an eine diffgebundene Nutzerfreigabe koppeln.
**Anforderungen:** R-10; Entscheidung §7.8.
**Voraussichtlich betroffene Dateien:** `src/gates.py` (neu), `src/workflow.py`, `src/workflow_state.py`, `src/cli.py`, `src/repo_changes.py`, `tests/test_gates.py` (neu), `tests/test_workflow.py`.

**Akzeptanzkriterien:**

- Grobe, konfigurierbare Testpfade erkennen bestehende, neue und umbenannte Testdateien.
- Ohne Freigabe hält dieselbe Run-ID in `awaiting_test_change_approval` mit Exitcode 4.
- Interaktive Freigabe oder `--resume` mit expliziter Zustimmung setzt denselben Lauf fort.
- Zustimmung gilt nur für den geprüften Test-Diff-Fingerprint und verfällt bei weiterer Teständerung.
- Freigebender Nutzer, Zeitpunkt, Pfade und Fingerprint erscheinen in der Prüfspur.
- `--manual-slice-gate` verwendet denselben resumefähigen Gate-Mechanismus.
- Eine Änderung eines nach Planfreigabe persistierten Ankers hält mit Exitcode 4 in `awaiting_user_decision`, invalidiert bisherige Planfreigaben und setzt die Planreviewkette auf `codex_plan_revision` mit anschließendem Claude-Review zurück. Fortsetzung erfordert eine explizite Nutzerentscheidung; der neue Ankerfingerprint wird erst danach zur Reviewbasis.

**Tests:** unversionierte Testdatei, Änderung, Rename, geänderter Fingerprint, Ablehnung, Zustimmung, geänderter freigegebener Anker mit Exitcode/State/Kettenreset und Resume ohne Wiederholung abgeschlossener Schritte; vollständige Suite.
**Abhängigkeiten:** Slice 5, Slice 7 und Slice 10.
**Test-Riegel:** ja — der Slice testet seinen eigenen Freigabepfad. **Red-State:** nein.
**Risiko/Rückfalloption:** Selbstreferenzielles Gate; die Testfreigabe für diesen Slice wird vor der Implementierung ausdrücklich dokumentiert.

### Slice 12 — Maschinelle Stop-Regeln und Dateigrenze

**Zweck:** Generische und zielrepospezifische Stop-Regeln als echte Zustandsübergänge statt Prompt-Prosa implementieren.
**Anforderungen:** R-15; Entscheidung §7.3.
**Voraussichtlich betroffene Dateien:** `src/gates.py`, `src/repo_changes.py`, `src/workflow.py`, `src/cli.py`, optional `orchestrator.toml`, `tests/test_gates.py`, `tests/test_workflow.py`, `tests/test_cli.py`.

**Akzeptanzkriterien:**

- Pfadklassen sind konfigurierbar; unbekannte Dateien zählen produktiv.
- Mehr als zehn produktive Dateien halten vor Agentenmeldung mit Exitcode 4 an.
- Zielrepos können fachliche Stop-Regeln mit stabiler ID und Beschreibung deklarieren.
- Regeln werden ungekürzt und referenzierbar in relevante Prompts übernommen.
- `STOP_REQUESTED` beendet die Work Unit als Nutzer-Gate und wird nicht als Retry behandelt.
- Branchabweichung und nicht ausführbare Validierung verwenden denselben Haltmechanismus.

**Tests:** Dateigrenze 10/11, unbekannter Dateityp, ausgeschlossene Tests/Doku, deklarierte Fachregel, STOP-Marker und Konflikt mit Approval; vollständige Suite.
**Abhängigkeiten:** Slice 5, Slice 10 und Slice 11.
**Test-Riegel:** ja. **Red-State:** nein.
**Risiko/Rückfalloption:** Fehlklassifikation; fail-closed-Verhalten und protokollierte Pfadklassen machen die Entscheidung prüfbar.

### Slice 13 — Pfadabhängige Validierungsmatrix und Attestierung

**Zweck:** Standard- und Zusatzvalidierungen diffabhängig je kanonischem Fingerprint einmal durch den Orchestrator ausführen, attestieren und beweissicher an alle Reviewer binden.
**Anforderungen:** R-16; Gate-Grundlage aus §2.5.
**Voraussichtlich betroffene Dateien:** `src/gates.py`, `src/agent_runtime.py`, `src/workflow.py`, `src/contracts.py`, `src/cli.py`, optional `orchestrator.toml`, `tests/test_gates.py`, `tests/test_agent_runtime.py`, `tests/test_workflow.py`.

**Akzeptanzkriterien:**

- Ein Standardbefehl und beliebig viele pathgebundene Zusatzbefehle sind deklarierbar.
- Beispiel „`npm test` immer, `npm run build:engine` bei `engine/**`" funktioniert exakt.
- Der Orchestrator führt die ermittelte Matrix je kanonischem Diff-Fingerprint genau einmal aus und erfasst Befehl, Exitcode, Vollständigkeit, kompakte Ausgabe und Ausgabedigest in einer unveränderlichen Attestierung.
- Claude und Antigravity erhalten dieselbe Attestierung als Contract-Input und führen die Vollsuite nicht erneut aus. Ein zweiter Reviewer desselben Fingerprints erhöht den Ausführungszähler nicht.
- Der Attestierungsfingerprint muss mit Reviewpaket und Freigaben übereinstimmen; selbst gemeldete `VALIDATION_RESULT`-Marker von Agenten werden abgelehnt.
- Eine inhaltliche Korrektur invalidiert die alte Attestierung und erzeugt genau einen neuen Matrixlauf. Reviewer-verlangte gezielte Akzeptanztests werden in diese nächste Matrix aufgenommen.
- Read-only-Profil und negativer Schreibversuch bleiben Adapter-/Versions-Smokes und sind nicht Bestandteil jedes Slice-Reviews.
- Eine vollständige rote Matrix blockiert, außer einem ausdrücklich benannten Red-State/Folge-Slice-Paar.
- Nicht ausgeführte Pflichtbefehle werden als `INCOMPLETE` dokumentiert und blockieren ausnahmslos; die Red-State-Regel gilt dafür nicht.

**Tests:** Matching mehrerer Pfade, kein Match, fehlender Befehl, Timeout, roter Exit, Fingerprintabweichung, Wiederverwendung derselben Attestierung für Claude/Antigravity ohne zweiten Lauf, Invalidierung nach Korrektur, gezielter Finding-Akzeptanztest, Red-State-Ausnahme sowie separate Adapter-Smokes; vollständige Suite.
**Abhängigkeiten:** Slice 10, Slice 11 und Slice 12.
**Test-Riegel:** ja. **Red-State:** nein.
**Risiko/Rückfalloption:** Befehlsausführung und Plattformquoting; Befehle werden als strukturierte Argumentlisten behandelt, Shellstrings nur als ausdrücklich deklarierte Kompatibilitätsoption.

### Slice 14 — Quota-Wartezustand, definierte Instanzausfälle, Rundenlimit und Resume

**Zweck:** Quota mit Resetzeitpunkt als automatisch fortsetzbare Pause und fehlende Binary, Timeout, Prozessfehler sowie Iterationsgrenze als unterscheidbare, manuell fortsetzbare Haltzustände modellieren.
**Anforderungen:** R-18; Abschluss von R-8 und Entscheidung §7.4.
**Voraussichtlich betroffene Dateien:** `src/agent_runtime.py`, `src/workflow.py`, `src/workflow_state.py`, `src/cli.py`, `src/inbox_watcher.py` nur für Zustandsklassifikation, `tests/test_agent_runtime.py`, `tests/test_workflow.py`, `tests/test_orchestrator_quota.py`.

**Akzeptanzkriterien:**

- Der Adapter trennt Quota von Auth-, Netzwerk-, Berechtigungs-, Timeout-, Binary- und generischen Prozessfehlern. Adapter-spezifische Parser akzeptieren absolute UTC-/Offset-Zeitstempel, gegen den Empfangszeitpunkt eindeutig auflösbare relative Angaben, strukturierte Providerfelder und bekannte Provider-Fließtextmuster; unbekannter oder mehrdeutiger Fließtext fällt fail-safe auf Exitcode 2 zurück.
- Ein verlässlicher Resetzeitpunkt innerhalb der konfigurierten Grenze erzeugt `waiting_for_quota`; State und Meldung nennen Rolle, Slice/Work Unit, Schritt, unveränderten Providertext, Parseweg, Empfangszeitpunkt, Zeitzone, UTC-Zeitpunkt, Sicherheitszuschlag, Automatikstatus und Fortsetzungszähler.
- Der Vordergrundprozess wartet unterbrechbar und ohne Busy Loop bis Resetzeitpunkt plus Sicherheitszuschlag. CLI und Watch-Modus zeigen im konfigurierten Intervall einen knappen Heartbeat mit Rolle, Task/Work Unit, lokalem und UTC-Fortsetzungszeitpunkt sowie verbleibender Wartezeit. Danach prüft der Orchestrator Slice-Scope, Diff-Fingerprint und Gatezustand erneut und ruft exakt dieselbe Rolle im selben Schritt auf.
- Teilweise, leere oder unparsbare Ausgabe des quota-beendeten Aufrufs wird nicht als erfolgreich persistiert. Nachfolgende Rollen werden nicht vorgezogen.
- Ohne verlässlichen Resetzeitpunkt, bei deaktivierter oder ausgeschöpfter Automatik oder bei Überschreitung der Maximalwartezeit endet Quota mit 2; sonstiger Instanzausfall/Timeout endet mit 3, Policy-/Iterationshalt mit 4.
- Automatische Fortsetzungen sind begrenzt; Standard ist höchstens eine je blockiertem Schritt. Bleibt die Quota danach bestehen, hält der Lauf mit Exitcode 2 manuell resumefähig an.
- Andere klassifizierte Instanzausfälle werden intern nicht automatisch wiederholt. Es gibt niemals einen Ersatzagenten.
- Leere Ausgabe, unparsbare Ausgabehülle, `is_error`, Berechtigungsablehnung und generischer `Execution error` werden mit Exitcode und Invocation-ID als gescheiterter Instanzaufruf persistiert. Sie lösen weder ein stilles Wiederholen noch eine erfundene Prozesszustandsmeldung aus.
- Fehlende Schreibbarkeit privater CLI-Runtimepfade, gesperrter erforderlicher Loopback oder fehlender Provider-Egress werden im Preflight beziehungsweise Aufruffehler eindeutig von einer Modellquota unterschieden.
- Nach vier Rückgaben an Codex bleibt der Arbeitsbaum unverändert und State ist `awaiting_user_decision`.
- Resume setzt am betroffenen Schritt fort; bereits committete Slices und persistierte Dokumentereignisse werden nicht wiederholt.
- Resume aus jedem persistierbaren Haltezustand besteht den Preflight bei unverändertem, persistiertem In-Scope-Diff; scope-fremde oder nach dem Halt unerwartet veränderte Pfade halten weiterhin vor dem Agentenaufruf an.
- Ein kontrollierter Abbruch während `waiting_for_quota` bewahrt den Zustand. Endet der WSL-/Terminalprozess, erfolgt kein unsichtbarer Neustart; `--resume` stellt den Wiedereinstieg her.
- Codex-, Claude- und Antigravity-Ausfälle sind separat getestet.

**Tests:** simulierte Quota und Ausfälle je Rolle; Parserfälle für absolute UTC-/Offset-Zeitstempel, relative Sekunden-/Minutenangaben mit festem Empfangszeitpunkt, strukturierte Providerfelder, bekannte Provider-Fließtextmuster sowie unbekannten und mehrdeutigen Fließtext; Fake Clock für künftigen, bereits erreichten und fehlenden Resetzeitpunkt, Sicherheitszuschlag, Maximalwartezeit, Warte-Heartbeat und dessen Intervall, Abbruch während des Wartens und fortbestehende Quota nach der erlaubten automatischen Fortsetzung; Timeout, leere/unparsbare JSON-Hülle, Berechtigungsablehnung und `Execution error`; vierte Rückgabe; Resume bei Codex/Claude/Antigravity; Resume aus jedem Gatezustand mit zulässigem In-Scope-Diff, Ablehnung scope-fremder Änderungen und unveränderter Commitverlauf; vollständige Suite.
**Abhängigkeiten:** Slice 7, Slice 10, Slice 11, Slice 12 und Slice 13.
**Test-Riegel:** ja. **Red-State:** nein.
**Risiko/Rückfalloption:** Doppelte Seiteneffekte beim Resume oder ein beendeter WSL-/Terminalprozess während der Wartezeit; jeder Seiteneffekt erhält einen persistierten Idempotenzschlüssel, der Wartezustand bleibt auf Platte und ist manuell resumefähig.

### Slice 15 — Skriptbarer Dry-Run für alle Gates

**Zweck:** Den vollständigen neuen Ablauf einschließlich negativer Pfade ohne echte Agenten oder API-Kosten deterministisch testbar machen.
**Anforderungen:** R-12.
**Voraussichtlich betroffene Dateien:** `src/agent_runtime.py`, `src/workflow.py`, `src/cli.py`, `src/contracts.py`, `src/gates.py`, `tests/test_workflow.py`, `tests/test_cli.py`, optional `tests/fixtures/dry_run_scenarios/`.

**Akzeptanzkriterien:**

- Ein Szenario kann Antworten je Rolle, Work Unit, Runde und Schritt vorgeben.
- Für jedes harte Gate aus §2.5 existieren je ein positives und negatives Szenario: grüne/rote Validierung einschließlich expliziter Red-State-Ausnahme, unveränderte/nicht freigegebene beziehungsweise nach Freigabe erneut geänderte Tests, ausbleibende/ausgelöste Stop-Regel, vorhandenes/fehlendes oder unparsbares Verdikt, vollständiger/fehlender Finding-/Prüfrecord, vorhandenes/fehlendes Pre-Mortem, erwartete/unerwartete Datei, unveränderter/geänderter freigegebener Anker sowie zulässige/überschrittene Rundenzahl.
- Quota und sonstige Instanzausfälle sind zusätzlich je Rolle simulierbar. Quota-Szenarien steuern eine Fake Clock und decken die vorgesehenen Zeitformate, verlässlichen, bereits erreichten, fehlenden und mehrdeutigen Resetzeitpunkt, Sicherheitszuschlag, Maximalwartezeit, Warte-Heartbeat, Unterbrechung, geänderten Fingerprint und weiterhin bestehende Quota nach der zulässigen automatischen Fortsetzung ab, ohne real zu schlafen.
- Szenarien prüfen Aufrufreihenfolge, State, Exitcode, Auditdokument und Commitentscheidung.
- Szenarien zählen Validierungsausführungen: ein unveränderter Fingerprint mit Claude und Antigravity führt die Matrix genau einmal aus; Korrektur und neuer Fingerprint führen genau zu einer neuen Attestierung. Reviewerantworten mit `VALIDATION_RESULT` sowie fehlende/fingerprintfremde Attestierungen werden negativ getestet.
- Der positive Dry-Run durchläuft Plan, mehrere Slices und Endreview vollständig.
- Dry-Run genehmigt nichts mehr bedingungslos.

**Tests:** die aufgezählte Positiv-/Negativmatrix wird als parametrisierte Szenariotabelle vollständig ausgeführt; der Anker-Negativfall prüft Exitcode 4, `awaiting_user_decision` und Rücksetzung der Planreviewkette; vollständige Suite.
**Abhängigkeiten:** Slice 10 bis Slice 14.
**Test-Riegel:** ja. **Red-State:** nein.
**Risiko/Rückfalloption:** Test-Doppelimplementierung; Dry-Run speist dieselbe State-Maschine und ersetzt nur die Agenten-/Command-Backends.

### Slice 16 — Branchweites Endreview

**Zweck:** Das branchweite Endreview und den Rückweg über eine reguläre Korrektur-Work-Unit vollständig in den neuen Entwicklungsworkflow integrieren.
**Anforderungen:** R-11; Integrationsnachweis für R-7, R-8, R-9, R-10, R-15 und R-16.
**Voraussichtlich betroffene Dateien:** `src/workflow.py`, `src/prompts.py`, `src/contracts.py`, `src/workflow_state.py`, `src/repo_changes.py`, `tests/test_workflow.py`, `tests/test_prompts.py`, `tests/test_parsing.py`.

**Akzeptanzkriterien:**

- Codex erstellt im Endreview einen Vollständigkeits-/Selbstprüfbericht ohne Freigaberecht.
- Claude und Antigravity prüfen den gesamten Branch-Diff gegen die gespeicherte Basis.
- Der Orchestrator validiert den Branch-Fingerprint genau einmal; Codex-Bericht, Claude und Antigravity verwenden dieselbe branchweite Attestierung ohne drei weitere Vollsuiten.
- Prüfgegenstände sind Architekturdrift, Schnittstellen, tote Übergangszustände, Dokumentations-Sync und R-1 bis R-18.
- Ein Endreview-Blocker erzeugt eine reguläre Korrektur-Work-Unit mit normaler Review- und Commitkette; anschließend beginnt das Endreview neu.
- Der neue Workflow durchläuft Plan, mehrere Slices, Commits, Korrektur und erneutes Endreview vollständig im expliziten Entwicklungsmodus.
- Der produktive Default bleibt bis zur Watch-Integration und zum gemeinsamen Contract-/Instruktions-Cutover in Slice 18 unverändert.

**Tests:** kompletter Mehr-Slice-End-to-End-Dry-Run im Entwicklungsmodus, Endreview-Finding/Korrektur/Neustart und Branch-Diff statt letzter Slice; vollständige Suite.
**Abhängigkeiten:** Slice 9 bis Slice 15.
**Test-Riegel:** ja. **Red-State:** nein.
**Risiko/Rückfalloption:** Endreview-Schleifen können Seiteneffekte wiederholen; Korrektur-Work-Units und Endreview-Anläufe besitzen getrennte Idempotenzschlüssel, die Defaultpipeline bleibt noch unberührt.

### Slice 17 — Watch-Modus und pausierte Läufe

**Zweck:** Inbox-/Outbox-Verarbeitung mit Slice-Commits, Resume und definierten Nutzer-/Instanzhalts integrieren.
**Anforderungen:** R-8, R-9, R-10 und R-18 im Watch-Kontext.
**Voraussichtlich betroffene Dateien:** `src/inbox_watcher.py`, `src/cli.py`, `src/workflow.py`, `src/workflow_state.py`, `tests/test_inbox_watcher.py`, `tests/test_orchestrator_watch_cli.py`, `tests/test_workflow.py`.

**Akzeptanzkriterien:**

- Jeder Inbox-Task erhält isolierten Run-State und eine stabile Run-ID.
- Ein Nutzer-/Policy-Gate wird nicht als Fehlerretry oder Poison-Pill gezählt; der Watcher hält kontrolliert und lässt den Task resumefähig.
- `waiting_for_quota` wird nicht als Fehlerretry oder Poison-Pill gezählt. Der Watcher hält den aktuellen Task und die Queue kontrolliert, zeigt denselben knappen Warte-Heartbeat wie die Einzel-CLI, wartet bei aktiver Automatik bis zum Resetzeitpunkt und setzt danach denselben Task und Schritt fort.
- Quota ohne terminierbare automatische Fortsetzung und sonstige Instanzausfälle behalten ihre Exitcode-/State-Klasse. Nach einem Prozessneustart bleibt der Task über `--resume` fortsetzbar; ein externer Scheduler wird nicht vorausgesetzt.
- Erfolgreiche Tasks werden erst nach vollständig committierten Slices und Endreview in die Outbox verschoben.
- Commitbedingte Änderungen lösen keinen falschen Dirty-Tree-Preflight aus.
- Locking, Success-Marker, FIFO und Retry bestehender technischer Fehler bleiben erhalten.

**Tests:** bestehende Watcher-Suite plus Gatepause, terminierte Quota-Wartepause mit Fake Clock, Quota ohne Resetzeitpunkt, Prozessunterbrechung und Resume, Instanzausfall, mehrere Slice-Commits und erfolgreicher Outbox-Move; vollständige Suite.
**Abhängigkeiten:** Slice 16.
**Test-Riegel:** ja. **Red-State:** nein.
**Risiko/Rückfalloption:** Queue-Stau durch Gate; der Halt nennt Task und Run-ID eindeutig und verarbeitet keine Folgetasks still weiter.

### Slice 18 — Defaultaktivierung, Altpfadentfernung und Instruktionsvertrag

**Zweck:** Den vollständig integrierten Slice-Workflow zusammen mit seinem neuen Marker- und Rollenvertrag als einzigen Default aktivieren und den alten Zwei-Phasen-Pfad entfernen.
**Anforderungen:** Abschluss von R-1, R-7 bis R-11 sowie R-14 bis R-16; gemeinsamer Cutover gemäß §6.6 der Anforderungen.
**Voraussichtlich betroffene Dateien:** `src/orchestrator.py`, `src/cli.py`, `src/prompts.py`, `AGENTS.md`, `CLAUDE.md`, `CODEX.md`, `ANTIGRAVITY.md` (neu), `GEMINI.md` (entfällt), `tests/test_language_consistency.py`, `tests/test_prompts.py`, `tests/test_parsing.py`, `tests/test_cli.py`, `tests/test_workflow.py`.

**Akzeptanzkriterien:**

- Der Slice-Workflow ist der einzige Default; Entwicklungsflag, `--from-phase`, `run_phase1`, `run_phase2`, Phase-Marker und Legacy-Parserpfade sind entfernt und werden von der CLI ausdrücklich abgelehnt.
- Positiver Dry-Run, Resume, Watch-Modus und Commitfolge funktionieren ohne Entwicklungsflag.
- `AGENTS.md`, Parser, Prompts und alle Root-Rollendateien enthalten im selben Commit denselben neuen Marker- und Findings-Contract; es entsteht kein Zwischenstand mit gemischter Semantik.
- Der gemeinsame Instruktionsvertrag weist deterministische Validierung ausschließlich dem Orchestrator zu, verbietet `VALIDATION_RESULT` in Agentenantworten und beschreibt die fingerprintgebundene Attestierung als Freigabevoraussetzung.
- `GEMINI.md` wird durch `ANTIGRAVITY.md` ersetzt; Rollendateien sind schlank, widerspruchsfrei und technisch passend zu Codex, Claude und Antigravity.
- Adversariales Review, fünf Prüfdimensionen, Finding-/Prüfrecord-Pflicht, Pre-Mortem, Verdiktkonsistenz und Rollenrechte stehen in den gemeinsamen Regeln.
- Kein aktiver Ausführungspfad akzeptiert Legacy-Marker oder phasenbezogene Optionen.
- Vollsuite und bestehende Wrapperaufrufe bleiben grün; der Repositoryzustand ist nach dem Cutover startbar und ohne Dokumentationsslice 19 bereits vollständig bedienbar.

**Tests:** vollständiger Default-End-to-End-Dry-Run, Ablehnung von Entwicklungsflag, `--from-phase` und weiteren Legacy-Optionen, Root-Instruktions-/Contract-/Promptkonsistenz, CLI-Hilfe-Snapshots und vollständige Suite.
**Abhängigkeiten:** Slice 17.
**Test-Riegel:** ja. **Red-State:** nein.
**Risiko/Rückfalloption:** atomarer Verhaltens- und Contract-Cutover; Aktivierung erfolgt erst nach allen Komponenten-, Gate-, Endreview- und Watch-Szenarien. Der letzte Commit aus Slice 17 bleibt der klare Rückfallpunkt.

### Slice 19 — Nutzerdokumentation, Diagramm und Konsistenzabschluss

**Zweck:** Die bereits aktive, getestete Pipeline für Nutzer vollständig dokumentieren und Diagramm sowie Beispieltask an den tatsächlichen Defaultstand angleichen.
**Anforderungen:** R-13, R-14 und R-17; Dokumentationsabschluss ohne weitere Workflowänderung.
**Voraussichtlich betroffene Dateien:** `README.md`, `workflow.puml`, `example-task.md`, optional `pyproject.toml` nur für eine vorhandene Dokument-/PlantUML-Prüfung, `tests/test_language_consistency.py` und optional ein fokussierter Dokumentkonsistenztest.

**Akzeptanzkriterien:**

- README dokumentiert Plattformen, Adapterkonfiguration, Read-only-Reviewerprofil, Exitcodes, Gates, Resume, Watch, Slice-MD und Auditpfad entsprechend dem in Slice 18 aktivierten Verhalten.
- Diagramm und Beispieltask zeigen Plan, Slices, Korrekturrunden, lokale Commits und Endreview ohne Zwei-Phasen- oder Gemini-Begriffe.
- Dokumentation erfindet keine noch nicht implementierten Optionen und verweist auf die tatsächlich erzeugten Plan-/Slice-Artefakte.
- Alle aktiven Nutzerdokumente und Beispiele bestehen Sprach-, Link- und Konsistenzprüfungen.
- Es wird kein produktiver Workflowcode geändert; der Stand bleibt deshalb unabhängig vom Dokumentationscommit startbar und grün.

**Tests:** Dokument-/Sprach-/Linkkonsistenz, PlantUML-Syntaxprüfung soweit lokal verfügbar, Beispiel-CLI-Aufrufe gegen `--help` sowie vollständige Suite.
**Abhängigkeiten:** Slice 18.
**Test-Riegel:** ja. **Red-State:** nein.
**Risiko/Rückfalloption:** reine Dokumentationsdrift; ein Rückfall dieses Slice berührt die in Slice 18 aktivierte Pipeline nicht.

---

## 6. Reihenfolge und Abhängigkeitsgraph

```text
1 Fallback entfernen
├─ 2 Python-CLI/Konfiguration
│  ├─ 3 Adapter und Rollenrechte ───────────────────────┐
│  └─ 4 Pfadsicherheit                                  │
│     └─ 5 kanonischer Branch-Diff ───────┐             │
└─ 6 Contract/Finding-Kern                │             │
   └─ 7 State v3/Work Units               │             │
      └─ 8 Prüfspur                       │             │
         └─ 9 Branch/Commit ──────────────┴─────────────┤
                                                       └─ 10 Reviewkette
                                                          ├─ 11 Test-Riegel
                                                          │  └─ 12 Stop-Regeln
                                                          │     └─ 13 Validierung
                                                          │        └─ 14 Ausfälle/Resume
                                                          │           └─ 15 Dry-Run
                                                          └───────────────┘
                                                                      │
                                                                      16 Endreview
                                                                      └─ 17 Watch-Modus
                                                                         └─ 18 Aktivierung/Contract-Cutover
                                                                            └─ 19 Nutzerdokumentation
```

Die vorgeschriebenen Abhängigkeiten sind erfüllt:

- R-1 ist Slice 1.
- R-5 liegt in Slice 5 vor R-9 (Slice 9), R-10 (Slice 11) und R-15 (Slice 12).
- R-6 liegt in Slice 6 vor R-7 (Slice 10).
- R-8 beginnt in Slice 7 und liegt vor dem primären R-18-Slice 14; Slice 1 ist dafür nur vorbereitend.

Jeder Slice liefert einen grünen, startbaren Stand. Neue Komponenten werden bis einschließlich Slice 17 hinter einem expliziten Entwicklungsmodus verdrahtet; es gibt keinen bewusst roten Contract-Slice. Slice 18 entfernt Entwicklungs- und Altpfad gemeinsam mit dem neuen Instruktionsvertrag, sodass kein dauerhafter Parallelworkflow verbleibt. Slice 19 ändert ausschließlich Nutzerdokumentation und deren Konsistenzprüfungen.

### 6.1 Nachweis des lauffähigen Stands je Slice

| Slice | Warum der Stand danach startbar und grün bleibt |
|---:|---|
| 1 | Nur der Ersatzagentenpfad entfällt; der bestehende Zwei-Phasen-Normalpfad und sein Dry-Run bleiben vollständig erhalten. |
| 2 | Der Python-Einstieg übernimmt den durch Wrappertests geschützten bestehenden Aufrufvertrag; Workflowsemantik ändert sich noch nicht. |
| 3 | Die neuen Adapter und Rechteprofile sind isoliert beziehungsweise nur im Entwicklungsmodus aktiv; der bestehende Defaultpfad bleibt verfügbar. |
| 4 | Die zentrale Pfadprimitive ersetzt bestehende Reads erst nach Positiv- und Regressionstests; der Workflowvertrag bleibt unverändert. |
| 5 | Die neue Diff-Quelle wird gegen den bisherigen Befund verglichen und noch nicht als Default-Commitmechanik aktiviert. |
| 6 | State-v3-Contract und Findings entstehen additiv hinter dem Entwicklungsmodus; aktive Altmarker und Alt-Prompts bleiben unverändert. |
| 7 | State v3 wird nur vom Entwicklungsmodus verwendet; abgeschlossene v2-States bleiben lesbar und der v2-Default wird nicht migriert. |
| 8 | Die Auditprojektion verarbeitet nur strukturierte v3-Ereignisse; bestehende Artefakte und der Defaultpfad bleiben unangetastet. |
| 9 | Branch-/Committransaktionen werden im Entwicklungsmodus integriert; der Default erzeugt weiterhin keine neuen Slice-Commits. |
| 10 | Die Review-State-Maschine ist end-to-end mit Fake-Agenten startbar, bleibt aber hinter dem Entwicklungsmodus. |
| 11 | Nutzer- und Testgates halten resumefähig; positive und negative Pfade werden getestet, ohne den alten Default umzuschalten. |
| 12 | Stop-Regeln ergänzen denselben getesteten Gate-Mechanismus; ohne ausgelöste Regel bleibt der Entwicklungsworkflow durchlaufbar. |
| 13 | Die Validierungsmatrix wird einmal je Diff-Fingerprint ausgeführt und ihre Attestierung von allen Reviewern wiederverwendet; rote Fälle halten kontrolliert. |
| 14 | Quota-Wartezustand, automatische Fortsetzung, sonstige Ausfälle und manuelle Resumewege sind mit Fake Clock getestet; der positive Entwicklungsworkflow bleibt grün. |
| 15 | Der skriptbare Dry-Run nutzt dieselbe State-Maschine und ersetzt nur Backends; alle Gate- und Quota-Zeitszenarien laufen ohne API-Abhängigkeit oder reales Warten. |
| 16 | Endreview und Korrektur-Work-Unit werden im Entwicklungsmodus vollständig durchlaufen; der Default bleibt unverändert. |
| 17 | Watch-Modus integriert Quota-Warten und die übrigen pausierten Zustände; bestehende FIFO-, Lock- und Erfolgspfade bleiben regressionsgeschützt. |
| 18 | Default, neuer Contract und Root-Instruktionen wechseln atomar; Altoptionen sind entfernt und der vollständige Default-E2E-Test ist grün. |
| 19 | Nur Nutzertexte, Diagramm, Beispiel und Konsistenztests ändern sich; die in Slice 18 aktivierte Runtime bleibt unverändert. |

---

## 7. Abdeckungsmatrix R-1 bis R-18

| Anforderung | Primäre Slices | Abschlussnachweis |
|---|---|---|
| R-1 Normalpfad/Fallback | 1, 3, 14, 18 | kein Substitutionspfad; bedarfsgerechte Rollenprüfung; definierter Halt und Altpfadentfernung |
| R-2 Adapter | 3 | drei aktuelle, konfigurierbare Adapter inklusive getesteter Versionen, Fähigkeiten und `agy`/`agy.exe` |
| R-3 Rollenrechte | 3, 8 | Reviewer-Repository read-only; private CLI-Runtime gezielt beschreibbar; dokumentierte Ausgabe nur durch Orchestrator |
| R-4 Pfadprüfung | 4 | Repositorygrenze und Symlink-/Traversaltests |
| R-5 Diff-Quelle | 5 | Merge-Base plus committed, uncommitted und nicht ignoriert untracked |
| R-6 Contract | 6 | zentraler Validator und verlustfreier Finding-Record |
| R-7 Rollentausch/Kette | 10, 18 | asymmetrische Aufrufreihenfolge und Defaultaktivierung |
| R-8 Slice-Modell | 7, 10, 14, 17, 18 | State v3, Work Units, Resume und Watch-Integration |
| R-9 Git-Integration | 5, 9, 17, 18 | Branchprüfung, exaktes Staging, Commit je Slice |
| R-10 Test-Riegel | 5, 11, 17, 18 | unversionierte Tests, diffgebundene Zustimmung und Resume |
| R-11 Gesamtabnahme | 16, 18 | kompletter Branch-Diff, Korrektur-Work-Unit und Defaultaktivierung |
| R-12 Dry-Run | 15 | skriptbare positive und negative Gates |
| R-13 Plattform | 2, 3, 19 | Slice 2: Python-Einstieg und Plattformdokumentation; Slice 3: Binarykonfiguration einschließlich `agy`/`agy.exe`; Slice 19: abschließende plattformübergreifende Evidenz und Nutzerdokumentation |
| R-14 Dokumentation | 18, 19 | Root-Instruktionen/Marker atomar im Cutover; README, Diagramm und Beispiel anschließend synchron |
| R-15 Stop-Regeln | 5, 12, 18 | Dateigrenze und deklarierte fachliche Regeln |
| R-16 Validierung | 13, 18 | Matrix einmal je kanonischem Diff-Fingerprint, vollständige Attestierung und Wiederverwendung durch beide Reviewer |
| R-17 Prüfspur | 8, 9, 19 | deterministische Slice-MD, Git-Historie und Nutzerdokumentation |
| R-18 Quota/Instanzausfall | 1 (Vorbereitung), 7, 14 (primär), 15, 17 | terminierter Quota-Wartezustand mit automatischer Fortsetzung, Fake-Clock-Nachweis, differenzierte Exitcodes sowie Runtime-/Loopback-/Egress-Diagnose und Resume je Rolle |

Keine Anforderung ist zurückgestellt.

---

## 8. Migration und Rollback

### State-Migration

1. State wird vollständig gelesen und strukturell validiert, bevor irgendein Schreibvorgang erfolgt.
2. Version 3 wird normal geladen.
3. Ein abgeschlossener Version-2-State wird als historisch abgeschlossen erkannt; ein neuer Auftrag beginnt mit einem neuen Version-3-State.
4. Ein aktiver oder eingefrorener Version-2-State hält mit klarer Meldung an. Artefakte und State bleiben unverändert; der Nutzer entscheidet über einen neuen Lauf.
5. Jede unbekannte Version ist ein Konfigurations-/Statefehler mit Exitcode 1.

### Code- und Branch-Rollback

- Jeder freigegebene Slice endet in einem lokalen Commit und bildet den Rollback-Punkt für den folgenden Slice.
- Innerhalb eines noch nicht committeten Slice werden ausschließlich die in der Slice-MD genannten Pfade dateiweise zurückgenommen.
- Neue Dateien werden nur nach expliziter Prüfung und Freigabe entfernt.
- Es gibt keinen automatischen Hard Reset, keinen History-Rewrite und keinen Force Push.
- Ein abgebrochener oder pausierter Slice bleibt unverändert im Arbeitsbaum und State; kein WIP-Commit kaschiert einen ungeprüften Zustand.
- Beim Resume ist dieser dirty In-Scope-Stand erwarteter Laufzustand: Der Preflight vergleicht ihn mit persistiertem Slice-Scope, Fingerprint und Gatezustand. Nur scope-fremde oder nach dem Halt unerwartet hinzugekommene Änderungen blockieren.

---

## 9. Test- und Validierungsplan

### Verbindlicher Standard

Nach jedem Slice läuft vollständig:

```text
python3 -m pytest tests/ -v
```

Gezielte Tests dürfen vorher zur Fehlersuche laufen, ersetzen aber nie die Vollsuite. Der Orchestrator führt die konfigurierte Matrix für den kanonischen Diff-Fingerprint genau einmal aus und bindet Befehle, Exitcodes, Vollständigkeit sowie Ergebnisdigest in eine Attestierung. Reviewer prüfen Implementierung und Testabdeckung gegen dieses Ergebnis, ohne dieselbe Matrix erneut auszuführen.

Der Orchestrator führt die Validierungsmatrix je kanonischem Diff-Fingerprint genau einmal aus und erzeugt die für alle Reviewer maßgebliche Attestierung. Claude und Antigravity starten keine eigene Vollsuite. Das schreibgeschützte Reviewerprofil, externe private Cache-/Runtimepfade und der negative Schreibtest bleiben als getrennte Adapter-/Versionsdiagnose erhalten und laufen nach Installation, Versionswechsel oder ausdrücklicher Fehlersuche.

### Test-Riegel während dieses Umbaus

Jeder Implementierungsslice plant Regressionstests und berührt deshalb voraussichtlich `tests/**`. Für Slice 01 bis 05 wurden die gesonderten Teständerungsfreigaben historisch je Slice eingeholt und an Pfade sowie Diff-Fingerprint gebunden. Auf verbindliche Nutzerentscheidung sind ab Slice 06 alle zur dokumentierten Slice-Intention gehörenden Teständerungen dieses Modernisierungsarbeitsplans vorab autorisiert. Sie werden weiterhin als `TEST_FILES_TOUCHED` erfasst, scope- und fingerprintgenau reviewed und mit der Vollsuite validiert, halten die Implementierung aber nicht erneut für ein separates `TEST_CHANGE_APPROVAL` an. R-10 bleibt als Zielanforderung bestehen und wird einschließlich seines regulären Nutzer-Gates implementiert und getestet.

### Gate-Abdeckung

| Gate | Pflichtnachweis |
|---|---|
| rote Validierung | roter Exit blockiert; benannter Red-State/Folgeslice als einziger Sonderfall |
| Teständerung | bestehende, neue, umbenannte und nach Freigabe erneut geänderte Testdatei |
| Stop-Regel | automatische 10/11-Dateigrenze und gemeldete Fachregel |
| fehlendes Verdikt | Ablehnung ohne stilles Überspringen |
| Findings/Prüfrecord | Blocker/Observation, Widerspruch, Schließung; pauschale Zustimmung ohne Record wird abgelehnt |
| Pre-Mortem | vollständiges Pre-Mortem akzeptiert, fehlendes blockiert |
| unerwartete Datei | Commit blockiert, Arbeitsbaum unverändert |
| Ankeränderung | unverändert akzeptiert; Änderung hält mit Exitcode 4 und setzt Planreview zurück |
| Iterationslimit | vier Rückgaben, State `awaiting_user_decision`, Exitcode 4 |
| Quota/Instanzausfall | Codex, Claude und Antigravity: Quota mit künftigem, erreichtem, fehlendem und mehrdeutigem Resetzeitpunkt; Wartegrenze, Sicherheitszuschlag, Automatiklimit, Unterbrechung, Timeout und fehlende Binary |
| CLI-Kompatibilität | bekannte Version, ungetestete Version, fehlende Fähigkeit, negativer Schreibtest und Langprompt |
| Resume | jeder persistierbare Schritt ohne doppelten Commit oder doppelte Doku; dirty In-Scope erlaubt, scope-fremd blockiert |
| Endreview | Branch-Diff, Korrektur-Work-Unit und erneuter vollständiger Review |

### Plattformnachweis

- Unit- und Integrationstests vermeiden Bash-spezifische Annahmen.
- Linux läuft lokal und in CI.
- macOS läuft in einer CI-Matrix nach nutzerautorisiertem Push.
- WSL2 wird lokal mit `codex`, `claude`, nativem `agy` und dem konfigurierbaren `agy.exe`-Alternativpfad geprüft.
- Native Windows wird nicht als unterstützt dokumentiert, solange kein vollständiger eigener Lauf vorliegt.

### Live-Agent-Smokes

Die grundsätzliche externe Steuerbarkeit wurde am 2026-08-10 bereits außerhalb der einschränkenden Test-Sandbox verifiziert:

| CLI | Version und Modus | Ergebnis |
|---|---|---|
| Codex | 0.147.0, `codex exec`, read-only, ephemeral, JSONL | Exitcode 0, Antwort `EXTERNAL_CONTROL_OK` |
| Claude Code | 2.1.226, Print, JSON, Plan-Modus, keine Tools, keine Sessionpersistenz | Exitcode 0, Antwort `EXTERNAL_CONTROL_OK` |
| Antigravity | 1.1.12, natives `agy`, werttragendes Print, JSON, Sandbox | Exitcode 0; erzwungener `git status`-Werkzeugaufruf extern steuerbar |

Die offizielle OpenAI-Dokumentation bezeichnet `codex exec` als stabilen nicht-interaktiven Modus und dokumentiert JSONL, read-only Sandbox und separate Finalausgabe: <https://learn.chatgpt.com/docs/developer-commands?surface=cli>.

Negative Vorversuche in einer zu engen äußeren Sandbox belegten die Runtime-Anforderungen: Codex benötigt initialisierbare interne App-Server-/Runtimepfade, Antigravity beschreibbare Log-/Crashpfade und einen lokalen Language-Server-Loopback, Claude Provider-Egress. Diese Ressourcen werden gezielt erlaubt; sie ändern nichts am read-only Repositoryzugriff der Reviewer.

Ein erneuter Antigravity-Isolationstest am 2026-08-11 präzisierte außerdem den CLI-Vertrag: `--print` nimmt den Prompt als Wert und muss nach den übrigen Optionen stehen. Bei korrekter Reihenfolge führte 1.1.12 einen erzwungenen `git status`-Aufruf mit JSON-Hülle und Exitcode 0 aus. Gleichzeitig meldete die CLI, dass `--mode plan` bei gesetztem `--disable-slash-commands` keine Wirkung hat. Slice 3 muss diese Kombination deshalb ablehnen oder durch ein nachweislich wirksames Rechteprofil ersetzen; ein erfolgreicher Text-Smoke allein genügt weiterhin nicht.

Die automatisierte Suite verwendet Fake-CLIs und verursacht keine API-Kosten. Slice 3 wiederholt nach Adapterimplementierung je einen bewusst ausgelösten Minimalprompt über exakt den Adapterbefehl und ergänzt negative Schreib-, Langprompt-, Stream-, Timeout-, Auth-, Quota- und Netzwerkfälle. Live-Smokes laufen nur nach Installation oder Versionsänderung, weil bereits Minimalaufrufe erhebliche Systemkontexte laden und Tokens beziehungsweise Quota verbrauchen.

---

## 10. Risiken und Gegenmaßnahmen

| Risiko | Gegenmaßnahme |
|---|---|
| großer Cutover in `orchestrator.py` | Komponenten vor Aktivierung isoliert testen; Altpfad erst in Slice 18 entfernen |
| Statekorruption | atomare Writes, laute Versionsprüfung, keine In-place-Migration aktiver v2-Läufe |
| versehentlicher Commit fremder Dateien | Branch-Diff, Fingerprint, Allowlist-Staging, erneute Pre-Commit-Prüfung |
| CLI-Drift | Adaptergrenzen, konfigurierbare Befehle/Modelle/Timeouts, Fake- und Live-Smokes |
| äußere Sandbox blockiert CLI-internen Betrieb | Repositoryrechte von privaten Runtimepfaden, Provider-Egress und Antigravity-Loopback trennen; negativer und positiver Starttest |
| Reviewer verändert Arbeitsbaum | technisch read-only; Dokuaktualisierung nur durch Audit-Komponente |
| Test-Riegel blockiert Selbstumbau | Freigabe je Slice als Kernpfad planen und diffgebunden dokumentieren |
| Marker-/Instruktionsdrift | zentrale Contractdefinition; Root-Instruktionen und aktive Marker atomar in Slice 18 umschalten, Nutzerdokumentation separat in Slice 19 synchronisieren |
| Watcher behandelt Pause als Fehler | eigene pausierte Zustandsklasse, kein Retry-/Poison-Zähler |
| WSL-/Terminalprozess endet während einer Quota-Wartezeit | `waiting_for_quota` vor dem Warten atomar persistieren; manueller Wiedereinstieg über `--resume`, kein vorausgesetzter externer Scheduler |
| zu große Slices | Scope- und 10-Dateien-Prüfung vor Edit; Stop statt stiller Erweiterung |

---

## 11. Offene Fragen

Keine offenen Architekturfragen. Die operative Verfügbarkeit der aktuell installierten CLIs und Authentifizierungen ist durch die dokumentierten Minimalprompts bestätigt. Slice 3 muss diese Evidenz über die dann implementierten Adapterbefehle wiederholen und die noch offenen Negativ-, Langprompt-, Stream- und Fehlerpfadtests schließen. Ein später negativer Befund ändert nicht das Rollendesign, sondern hält den betroffenen Slice mit dem definierten Instanzzustand an.

---

## 12. Review-Feedback von Claude

REVIEWER: claude

**Runde:** 1 (Planreview) — **Datum:** 2026-08-10
**Prüfbasis:** dieser Arbeitsplan (unversioniert), `requirements-orchestrator-modernization.md` Revision 5, `handover-orchestrator-modernization.md`, Repo-Stand `75337eb` auf `feature/orchestrator-modernization`.
**Selbst ausgeführte Validierung:** `python3 -m pytest tests/ -v` → 92 passed, Exitcode 0. Merge-Base gegen `master` = `0bd3bad` und die sieben Referenzdokumente als einziger Branch-Diff wurden mit `git merge-base` beziehungsweise `git diff --name-only master...HEAD` gegengeprüft; beide Angaben im Plan stimmen.

### Geprüfte Dimensionen

1. Abdeckung R-1 bis R-18 und der verbindlichen Entscheidungen aus §7 der Anforderungen.
2. Vertragstreue gegenüber den harten Gates (§2.5) und dem Markerkontrakt (§2.6).
3. Technische Durchführbarkeit gegen den verifizierten Ist-Code (`src/orchestrator.py`, `src/agent_runtime.py`, `src/state_io.py`, `.gitignore`).
4. Slice-Schnitt, Reihenfolge, Abhängigkeiten, Dateigrenze und Rückfalloption (§5, §10.3 bis §10.7).
5. Prüfspur, Migration und Rechtemodell (R-3, R-8, R-17, §7.7 bis §7.9).

### Nicht beanstandet

Abdeckungsmatrix, Abhängigkeitsreihenfolge (R-1 zuerst, R-5 vor R-9/R-10/R-15, R-6 vor R-7, R-8 vor R-18), Dateigrenze je Slice (kein Slice überschreitet zehn produktive Dateien), Sprachregel, Trennung Repository-Sandbox gegen CLI-Runtime, Ablehnung aktiver v2-States und der Verzicht auf Hard Reset sind korrekt und prüfbar formuliert. Der lokal vorhandene v2-State (`phase: done`) fällt nachweislich in den zulässigen Migrationsfall.

### Blocker

**C-01 — Read-only-Reviewer und selbst ausgeführte Validierung widersprechen sich.**
§3.1 und Slice 3 fordern „Reviewer können weder Code, Tests noch Dokumente direkt verändern" und beschränken die Schreibfreigabe ausdrücklich auf *CLI-eigene* Runtime-, Log- und Tempverzeichnisse. Slice 13 und §6.2 der Anforderungen verlangen zugleich, dass jede validierungspflichtige Rolle `python3 -m pytest tests/ -v` **selbst im Repository** ausführt. Ein Testlauf schreibt hier nachweislich in das Repository: `src/__pycache__/`, `tests/__pycache__/` und `.pytest_cache/` existieren im Arbeitsbaum. Ein technisch read-only gemountetes Repository lässt die Reviewer-Validierung also scheitern — und eine fehlende oder rote Validierung blockiert nach §2.5 jede Freigabe, das heißt der Normalpfad wäre dauerhaft blockiert. Der Plan löst diesen Konflikt an keiner Stelle auf.

**C-02 — Der Clean-Tree-Preflight blockiert den gesamten Resume-Pfad.**
`src/orchestrator.py:1119-1124` ruft den Preflight vor jedem Lauf auf — auch bei `--resume` (`src/orchestrator.py:1073`) — und `src/agent_runtime.py:613-621` lässt ihn bei unsauberem Arbeitsbaum mit `return 1` fehlschlagen. Der Plan verlangt gleichzeitig, dass jeder Halt (Test-Riegel, Stop-Regel, Iterationslimit, Instanzausfall; §3.4, §8, Slices 11/12/14) den Arbeitsbaum **unverändert** stehen lässt und derselbe Lauf mit `--resume` fortsetzt. Ein solcher Halt hinterlässt per Definition einen dirty tree, der Resume scheitert dann im Preflight mit Exitcode 1 statt am gespeicherten Schritt fortzusetzen. R-9 verlangt ausdrücklich, die Wechselwirkung mit dem Preflight im Plan zu beschreiben; der Plan erwähnt sie nur beiläufig in Slice 17 für den Watch-Modus und weist sie keinem Slice als Änderung zu.

**C-03 — Zwei harte Gates aus §2.5 bleiben Prosa statt Contract-Regel.**
„Keine Freigabe ohne Findings" (Review ohne dokumentierte Findings ist unzulässig; ersatzweise geprüfte Dimensionen, größtes Restrisiko und Bruchbedingung) taucht nur als Dokumentationsziel in Slice 18 auf („Findings-Pflicht … stehen in den gemeinsamen Regeln"), aber in keinem Akzeptanzkriterium des Contract-Kerns (Slice 6) und in keinem Dry-Run-Szenario (Slice 15). Damit wiederholt der Plan genau den Befund B-15: eine Regel, die im Prompt steht und nichts anhält. Zusätzlich fehlen in der Szenarienliste von Slice 15 die Gates „kein Verdikt = Ablehnung", „Pre-Mortem fehlt" und „unerwartete Datei"; R-12 verlangt aber ausdrücklich *jedes* Gate aus §2.5 verdrahtungstestbar.

**C-04 — Das zwingende Nutzergate für Ankeränderungen hat keinen implementierenden Slice.**
§3.4 und §3.8 des Plans erklären eine Änderung an einem freigegebenen Anker zum zwingenden Nutzergate mit Rücksetzung der Planreviewkette (entsprechend §7.5/§7.8 der Anforderungen). Slice 6 leistet nur Parsing und Erkennung („Strukturierte Anker werden geparst und Änderungen nach Planfreigabe erkannt"); weder Slice 11 noch Slice 12 noch Slice 15 führen den Halt, den Exitcode, den State oder die Kettenrücksetzung als Akzeptanzkriterium. Ein erkanntes, aber folgenloses Gate ist kein Gate.

**C-05 — Slice 18 bündelt mehrere kohärente Verhaltensänderungen.**
Slice 18 enthält Defaultaktivierung des neuen Workflows, Entfernung von Entwicklungsflag und komplettem Altpfad (`run_phase1`, `run_phase2`, Phase-Marker, Legacy-Parser), Rollendateischnitt `GEMINI.md` → `ANTIGRAVITY.md` samt Neufassung aller Root-Rollendateien sowie README-, Diagramm- und Beispieltasksynchronisierung — der Plan selbst nennt ihn „größter Umschaltpunkt". Das verstößt gegen §5 und Abnahmekriterium §10.5 („höchstens eine kohärente Verhaltensänderung je Slice") und macht ausgerechnet den riskantesten Schritt unteilbar rollbackbar. Die Aufteilung ist durch §6.6 eingeschränkt (Markersemantik nur gemeinsam mit `prompts.py`, `orchestrator.py` und den Instruktionsdateien), aber genau entlang dieser Grenze möglich.

### Observations

**C-06 — Kontraktstabilität in Slice 6 nicht ausdrücklich gesichert.**
Slice 6 fasst `src/prompts.py` und `src/orchestrator.py` an, während §6.6 der Anforderungen die bestehende Markersemantik bis zur gemeinsamen Umstellung mit den Instruktionsdateien konserviert. Der Plan sollte ausdrücklich festhalten, dass Slice 6 den neuen Contract additiv hinter dem Entwicklungsmodus einführt und die aktiven Prompts/Marker des Altpfads unverändert lässt.

**C-07 — Widersprüchliche R-18-Zuordnung.**
Die Abdeckungsmatrix (§7) nennt Slice 1 als primären Slice für R-18, §6 behauptet dagegen „R-8 beginnt in Slice 7 und liegt vor R-18 (Slice 14)". Slice 1 sollte in der Matrix als Vorbereitung gekennzeichnet werden, damit die vorgeschriebene Abhängigkeit R-8 vor R-18 auch formal eindeutig bleibt.

**C-08 — Verhältnis von Diff-Quelle und `.gitignore` ist ungeregelt.**
R-5 verlangt, unversionierte Dateien in die Diff-Quelle aufzunehmen; der Plan sagt nirgends, dass ignorierte Pfade ausgenommen sind. Lokal sind `.orchestrator/`, `__pycache__/`, `.pytest_cache/`, `Inbox/`, `outbox/`, `task.md` und `.venv*/` ignoriert. Ohne explizite Regel geraten Testartefakte und Watcher-Bewegungen in Diff, Fingerprint, Dateigrenze und in das Gate „unerwartete Datei" — Slice 17 („Commitbedingte Änderungen lösen keinen falschen Dirty-Tree-Preflight aus") behandelt nur einen Teilfall davon.

**C-09 — Der Nachweis „lauffähiger Stand nach jedem Slice" ist eine Behauptung.**
§9.1 Punkt 5 der Anforderungen verlangt eine Begründung; §6 des Plans liefert einen Satz für alle achtzehn Slices. Für die Slices, die neue Module vor ihrer Verdrahtung einführen (6 bis 9), ist der Nachweis trivial, für 10 bis 17 nicht.

**C-10 — Slice-MD-Namen und Verlinkung fehlen.**
§9.2 verlangt je Slice eine vor Arbeitsbeginn angelegte, aus der Arbeitsplan-MD **verlinkte** Datei nach dem Muster `slice-<thema>-<nummer>-<kurztitel>.md`. Der Plan nennt weder die konkreten Dateinamen noch die Verlinkung; §3.9 verweist nur allgemein auf kleingeschriebene englische Namen.

**C-11 — `--from-phase` fehlt in der Altpfadliste.**
Slice 18 nennt Entwicklungsflag, `run_phase1`, `run_phase2`, Phase-Marker und Legacy-Parserpfade, aber nicht die phasengebundene CLI-Option `--from-phase` (`src/orchestrator.py:1110`), die im Slice-Modell keine Bedeutung mehr hat.

**C-12 — `main` gegen `master` nicht als Abweichung ausgewiesen.**
§2.4 und R-11 der Anforderungen sprechen vom Branch-Diff „gegen `main`"; der Default-Branch dieses Repos ist `master`, und der Plan verwendet stillschweigend `master` beziehungsweise die Merge-Base. Sachlich richtig, aber §6.5 verlangt, solche Abweichungen zu benennen — die Tabelle in §3.9 ist der vorgesehene Ort.

### Formale Marker

NEW_FINDING: C-01 | BLOCKER | Read-only-Repository für Reviewer widerspricht der von Reviewern selbst auszuführenden Validierung; pytest schreibt `__pycache__/` und `.pytest_cache/` in das Repository | Plan definiert die Read-only-Grenze präzise (z. B. verfolgte Dateien unveränderbar, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, schreibbares Tempverzeichnis) und Slice 3 sowie Slice 13 tragen je ein Akzeptanzkriterium „Reviewerrolle führt die Vollsuite im Read-only-Profil erfolgreich aus, ein Schreibversuch auf verfolgte Dateien scheitert weiterhin"
NEW_FINDING: C-02 | BLOCKER | Der Clean-Tree-Preflight (`orchestrator.py:1119-1124`, `agent_runtime.py:613-621`) läuft auch bei `--resume` und lässt jeden definierten Halt mit unverändertem Arbeitsbaum unfortsetzbar werden | Ein benannter Slice (9 oder 14) enthält das Kriterium „Resume aus jedem persistierbaren Haltezustand besteht den Preflight, solange die Änderungen im gespeicherten Slice-Scope liegen; scope-fremde Änderungen blockieren weiterhin" plus Test für beide Fälle
NEW_FINDING: C-03 | BLOCKER | Die §2.5-Gates „keine Freigabe ohne Findings", „fehlendes Pre-Mortem" und „kein Verdikt" sind nicht als Contract-Regel und nicht als Dry-Run-Szenario abgebildet | Slice 6 lehnt eine Freigabe ohne Finding-Record beziehungsweise ohne dokumentierte Dimensionen, Restrisiko und Bruchbedingung ab, und Slice 15 führt je ein positives und negatives Szenario für alle Gates aus §2.5 auf
NEW_FINDING: C-04 | BLOCKER | Das zwingende Nutzergate bei Änderung eines freigegebenen Ankerwerts wird erkannt, aber von keinem Slice als Halt umgesetzt | Slice 11 oder 12 enthält das Kriterium „geänderter Anker nach Planfreigabe hält mit Exitcode 4 in `awaiting_user_decision` und setzt die Planreviewkette zurück" mit Test und Dry-Run-Szenario
NEW_FINDING: C-05 | BLOCKER | Slice 18 bündelt Defaultaktivierung, Altpfadentfernung, Rollendateischnitt und Dokumentationssynchronisierung in einer Einheit und verletzt damit §5 und §10.5 | Aufteilung in mindestens zwei Slices, z. B. 18 = Aktivierung, Altpfadentfernung und Instruktionsdateien mit Markerkontrakt (§6.6-konform), 19 = README, `workflow.puml`, `example-task.md` und Konsistenztests; jeder Teil einzeln grün, startbar und rückrollbar
NEW_FINDING: C-06 | OBSERVATION | Slice 6 ändert `prompts.py`/`orchestrator.py`, ohne die Kontraktstabilität nach §6.6 ausdrücklich zu sichern | Slice 6 erhält den Zusatz, dass der neue Contract additiv hinter dem Entwicklungsmodus entsteht und die aktiven Marker des Altpfads unverändert bleiben
NEW_FINDING: C-07 | OBSERVATION | Abdeckungsmatrix nennt Slice 1 als primären R-18-Slice, §6 nennt Slice 14 | Matrixzeile R-18 weist Slice 1 als Vorbereitung aus
NEW_FINDING: C-08 | OBSERVATION | Der Umgang der Diff-Quelle mit ignorierten Pfaden ist ungeregelt | Slice 5 erhält Kriterium und Test „ignorierte Pfade zählen nicht in Diff, Fingerprint, Dateigrenze und Gate ‚unerwartete Datei'"
NEW_FINDING: C-09 | OBSERVATION | Der nach §9.1 Punkt 5 geforderte Nachweis des lauffähigen Stands je Slice fehlt | §6 erhält je Slice eine Zeile oder eine kompakte Tabelle mit dem konkreten Grund
NEW_FINDING: C-10 | OBSERVATION | Slice-MD-Dateinamen nach §9.2 und ihre Verlinkung aus dem Arbeitsplan sind nicht festgelegt | §5 oder Slice 8 listet je Slice den geplanten Dateinamen nach Muster `slice-<thema>-<nummer>-<kurztitel>.md` und die Verlinkungspflicht
NEW_FINDING: C-11 | OBSERVATION | `--from-phase` fehlt in der Liste der in Slice 18 zu entfernenden Altpfade | Slice 18 nennt `--from-phase` ausdrücklich, Legacy-Flag-Ablehnungstest deckt es ab
NEW_FINDING: C-12 | OBSERVATION | Abweichung `main` (Anforderungen) gegen `master` (Repo, Plan) ist nicht als Widerspruch ausgewiesen | Tabelle §3.9 enthält eine Zeile zur Basisbranch-Benennung

FINDING_STATUS: C-01 | OPEN | Konflikt zwischen R-3 und §6.2 im Plan ungelöst
FINDING_STATUS: C-02 | OPEN | Preflight-Wechselwirkung nach R-9 nicht beschrieben und keinem Slice zugewiesen
FINDING_STATUS: C-03 | OPEN | Gate existiert nur als Dokumentationsziel
FINDING_STATUS: C-04 | OPEN | Gate ohne implementierenden Slice
FINDING_STATUS: C-05 | OPEN | Slice-Schnitt verletzt §5 und §10.5
FINDING_STATUS: C-06 | OPEN | Klarstellung ausstehend
FINDING_STATUS: C-07 | OPEN | Klarstellung ausstehend
FINDING_STATUS: C-08 | OPEN | Regel fehlt
FINDING_STATUS: C-09 | OPEN | Begründung fehlt
FINDING_STATUS: C-10 | OPEN | Festlegung fehlt
FINDING_STATUS: C-11 | OPEN | Ergänzung fehlt
FINDING_STATUS: C-12 | OPEN | Ausweisung fehlt

TEST_FILES_TOUCHED: NONE

VALIDATION_RESULT: PASS | python3 -m pytest tests/ -v | 0

PRE_MORTEM: In drei Monaten scheitert der Umbau am wahrscheinlichsten daran, dass die Rechte- und Preflight-Annahmen (C-01, C-02) erst in Slice 13 beziehungsweise beim ersten echten Resume auffallen — dann sind Contract-, State- und Commitmechanik bereits darauf gebaut, dass Reviewer im Repository validieren und dass ein Lauf nur mit sauberem Arbeitsbaum startet, und die Korrektur trifft gleichzeitig `agent_runtime.py`, `gates.py` und `workflow.py`.

PLAN_APPROVAL: NO

---

### Runde 2 — Re-Review der Überarbeitung

REVIEWER: claude

**Runde:** 2 (Re-Review) — **Datum:** 2026-08-10
**Prüfumfang:** alle zwölf Findings aus Runde 1 gegen den überarbeiteten Plan, zusätzlich Regressionsblick auf neu entstandene Widersprüche, Slice-Schnitt, Dateigrenzen und Abdeckungsmatrix.
**Selbst ausgeführte Validierung:** `python3 -m pytest tests/ -v` → 92 passed, Exitcode 0.

#### Empirischer Nachweis für C-01

Das in §3.1 definierte Read-only-Profil wurde nicht nur gelesen, sondern ausprobiert. Nach Löschen von `src/__pycache__`, `tests/__pycache__` und `.pytest_cache` lief `PYTHONDONTWRITEBYTECODE=1 TMPDIR=<extern> python3 -m pytest tests/ -q -p no:cacheprovider` mit **92 passed, Exitcode 0**, und ein Vergleich von `git status --short --ignored` vor und nach dem Lauf zeigt **keine einzige neue Datei im Arbeitsbaum** — weder Bytecode noch pytest-Cache noch sonstige Testartefakte. Die vorgeschlagene Profildefinition ist damit für dieses Repo nachweislich tragfähig; die bestehende Suite schreibt außerhalb temporärer Verzeichnisse nichts ins Repository. Slice 3 und Slice 13 sollten genau diesen Vergleich (Arbeitsbaum vor/nach Reviewer-Validierung ist identisch) als Testform übernehmen, weil er die Grenze schärfer prüft als ein reiner Schreibversuch auf eine einzelne Datei.

#### Ergebnis je Finding aus Runde 1

| ID | Klasse | Ergebnis | Beleg im überarbeiteten Plan |
|---|---|---|---|
| C-01 | BLOCKER | geschlossen | §3.1 (präzise Read-only-Definition), Slice 3 Akzeptanzkriterium und Tests, Slice 13, §9 „Verbindlicher Standard"; zusätzlich empirisch verifiziert |
| C-02 | BLOCKER | geschlossen | Slice 9 (resume-bewusster Preflight, In-Scope zulässig, scope-fremd blockiert), Slice 14, §8 Rollback, §9 Gate-Tabelle Zeile „Resume" |
| C-03 | BLOCKER | geschlossen | Slice 6 (Finding-/Prüfrecord-Pflicht, pauschale Zustimmung ungültig, vollständiges Pre-Mortem, parsbares Verdikt), Slice 15 Positiv-/Negativmatrix über alle §2.5-Gates, §9 Zeilen „Findings/Prüfrecord" und „Pre-Mortem" |
| C-04 | BLOCKER | geschlossen | Slice 11 (Exitcode 4, `awaiting_user_decision`, Freigabeinvalidierung, Rücksetzung auf `codex_plan_revision`), Slice 15 Ankerszenario, §9 Zeile „Ankeränderung" |
| C-05 | BLOCKER | geschlossen | Slice 18 = §6.6-konformer atomarer Runtime-/Marker-/Instruktions-Cutover, Slice 19 = reine Nutzerdokumentation; beide je unter der Zehn-Dateien-Grenze und einzeln rückrollbar |
| C-06 | OBSERVATION | geschlossen | Slice 6 letztes Akzeptanzkriterium und Regressionstests für den unveränderten Altcontract, §6.1 Zeile 6 |
| C-07 | OBSERVATION | geschlossen | §6 Abhängigkeitsnachweis und Matrixzeile R-18 („1 (Vorbereitung), 7, 14 (primär), 17") |
| C-08 | OBSERVATION | geschlossen | Slice 5 Akzeptanzkriterium und Tests; korrekt abgegrenzt, dass versionierte Dateien unabhängig von späteren Ignore-Regeln zählen |
| C-09 | OBSERVATION | geschlossen | §6.1 mit einer belastbaren Begründung je Slice |
| C-10 | OBSERVATION | geschlossen | §5.1 mit allen 19 Zielpfaden und Verlinkungspflicht, Slice 8 prüft Existenz, Link und Pflichtstruktur beim Slice-Start |
| C-11 | OBSERVATION | geschlossen | Slice 18 Akzeptanzkriterium und Ablehnungstest für `--from-phase` |
| C-12 | OBSERVATION | geschlossen | §3.9 letzte Tabellenzeile, inklusive dynamischer Ermittlung statt fest verdrahtetem Branchnamen |

Keine der Überarbeitungen hat eine bestehende Zusage abgeschwächt: Abhängigkeitsreihenfolge, Dateigrenzen, Sprachregel und Migrationspfad bleiben unverändert korrekt, und die Abdeckungsmatrix wurde für R-13, R-14 und R-17 konsistent auf die neue Slice-Nummerierung nachgezogen.

#### Neue, nicht blockierende Findings

**C-13 — Das Endreview dieses Umbaus selbst ist nicht terminiert.**
Slice 16 implementiert die Endreview-Fähigkeit; §2.4 der Anforderungen verlangt aber zusätzlich, dass nach dem letzten Slice alle drei Instanzen den gesamten Branch-Diff prüfen. Im Plan endet die Kette mit Slice 19, und keine Stelle terminiert diesen Meta-Abschluss oder legt fest, ob er von Hand oder bereits durch die in Slice 18 aktivierte Pipeline gefahren wird. Letzteres wäre der wertvollere Abschlusstest.

**C-14 — Slice 9 nennt den resume-bewussten Preflight, aber nicht die Datei, in der er lebt.**
Das Akzeptanzkriterium betrifft `preflight()` (`src/agent_runtime.py:583`, aufgerufen aus `src/orchestrator.py:1119` beziehungsweise künftig `src/cli.py`); die voraussichtlich betroffenen Dateien von Slice 9 listen nur `src/repo_changes.py`, `src/workflow_state.py` und optional `src/git_service.py`. Da die Zehn-Dateien-Prüfung und die Stop-Regel vor dem ersten Edit an dieser Liste hängen, sollte sie den tatsächlichen Änderungsort enthalten.

**C-15 — Titel und Zieldateiname von Slice 11 decken das Anker-Gate nicht ab.**
Slice 11 heißt „Teständerungsriegel und Nutzerfreigabe" und liegt laut §5.1 unter `slice-orchestrator-modernization-11-test-change-gate.md`, enthält jetzt aber zusätzlich das Anker-Gate mit Rücksetzung der Planreviewkette. Da Slice 8 Dateiname und Verlinkung maschinell prüft, ist die Umbenennung in einen Sammeltitel für resumefähige Nutzer-Gates jetzt billig und später teuer.

#### Hinweis ohne Findingcharakter

§15 führt weiterhin alle zwölf Findings unter `OPEN_FINDINGS`. Mit den Schließungen dieser Runde sind C-01 bis C-12 geschlossen; offen bleiben C-13 bis C-15 als Observations. Die Statuszeile in §15 ist entsprechend nachzuziehen — im Zielentwurf übernimmt das die Audit-Komponente aus Slice 8.

#### Formale Marker

FINDING_STATUS: C-01 | CLOSED | Read-only-Profil präzise definiert, in Slice 3, 13 und §9 verankert und lokal empirisch als grün und schreibfrei nachgewiesen
FINDING_STATUS: C-02 | CLOSED | Resume-bewusster Preflight mit persistiertem Slice-Scope und Fingerprint in Slice 9, Slice 14, §8 und §9 verankert
FINDING_STATUS: C-03 | CLOSED | Finding-/Prüfrecord-Pflicht und Pre-Mortem sind Contract-Regel in Slice 6 und vollständige Positiv-/Negativmatrix in Slice 15
FINDING_STATUS: C-04 | CLOSED | Anker-Gate mit Exitcode 4, State und Kettenreset in Slice 11 implementiert und in Slice 15 getestet
FINDING_STATUS: C-05 | CLOSED | Cutover in Slice 18 und Dokumentation in Slice 19 getrennt, jeweils einzeln grün und rückrollbar
FINDING_STATUS: C-06 | CLOSED | Additive Einführung hinter dem Entwicklungsmodus samt Altcontract-Regressionstests zugesichert
FINDING_STATUS: C-07 | CLOSED | Slice 1 als Vorbereitung, Slice 14 als primärer R-18-Slice ausgewiesen
FINDING_STATUS: C-08 | CLOSED | Ignore-Regel für unversionierte Pfade mit korrekter Abgrenzung versionierter Dateien in Slice 5 verankert
FINDING_STATUS: C-09 | CLOSED | §6.1 begründet den lauffähigen Stand je Slice einzeln
FINDING_STATUS: C-10 | CLOSED | §5.1 legt Zielpfade und Verlinkung fest, Slice 8 prüft sie maschinell
FINDING_STATUS: C-11 | CLOSED | `--from-phase` ist in Slice 18 als zu entfernende und abzulehnende Altoption benannt
FINDING_STATUS: C-12 | CLOSED | §3.9 weist die `main`/`master`-Abweichung aus und verlangt dynamische Ermittlung

NEW_FINDING: C-13 | OBSERVATION | Das nach §2.4 verpflichtende Endreview über den gesamten Branch-Diff nach dem letzten Slice ist im Plan nicht terminiert; Slice 16 liefert nur die Fähigkeit | Der Plan benennt nach Slice 19 einen ausdrücklichen Meta-Endreview-Schritt mit den drei Instanzen und legt fest, ob er manuell oder über die aktivierte Pipeline gefahren wird
NEW_FINDING: C-14 | OBSERVATION | Slice 9 fordert den resume-bewussten Preflight, listet aber `src/agent_runtime.py` beziehungsweise `src/cli.py` nicht unter den betroffenen Dateien | Die Dateiliste von Slice 9 enthält den tatsächlichen Änderungsort des Preflights, damit Dateigrenze und Stop-Regel vor dem ersten Edit greifen können
NEW_FINDING: C-15 | OBSERVATION | Titel und §5.1-Zieldateiname von Slice 11 decken das dort ergänzte Anker-Gate nicht ab, obwohl Slice 8 den Dateinamen maschinell prüft | Slice 11 erhält einen Titel für resumefähige Nutzer-Gates und einen dazu passenden Zielpfad in §5.1

FINDING_STATUS: C-13 | OPEN | Terminierung des Meta-Endreviews fehlt
FINDING_STATUS: C-14 | OPEN | Dateiliste unvollständig
FINDING_STATUS: C-15 | OPEN | Benennung nachzuziehen

TEST_FILES_TOUCHED: NONE

VALIDATION_RESULT: PASS | python3 -m pytest tests/ -v | 0

PRE_MORTEM: In drei Monaten scheitert der Umbau am wahrscheinlichsten nicht mehr an den Rechten oder am Preflight, sondern an Slice 18: Er schaltet Runtime, Markerkontrakt und alle Root-Instruktionen in einem einzigen Commit um, und sein einziger belastbarer Vorabnachweis ist der Dry-Run mit Fake-Agenten. Zeigt sich erst dort, dass echte CLIs den neuen Contract anders beantworten als die Fakes, steht der Rückfallpunkt eine ganze Slice-Grenze zurück. Gegenmittel wäre ein Live-Smoke mit den drei echten CLIs gegen den neuen Contract bereits am Ende von Slice 17.

PLAN_APPROVAL: YES

---

## 13. Review-Feedback von Antigravity

Das Abschlussreview durch Antigravity am 2026-08-10 bestätigt den von Codex erarbeiteten und von Claude freigegebenen Arbeitsplan zur Modernisierung des Dual-Agent-Orchestrators.

### Prüfergebnisse

1. **Abdeckung der Anforderungen (R-1 bis R-18):**
   - Die Abdeckungsmatrix in §7 verankert alle 18 Kernanforderungen sowie die Architekturvorgaben (§7.1 bis §7.10) lückenlos in den 19 geplanten Slices.
   - Der Entwurf ist konsistent, behält funktionierende Bestandteile (`state_io.py`, `agent_adapters.py`, `agent_runtime.py`, `inbox_watcher.py`) bei und refaktoriert den monolithischen `orchestrator.py`.

2. **Validierung der Testergebnisse:**
   - Die vollständige Testsuite (`python3 -m pytest tests/ -v`) wurde lokal ausgeführt und liefert **92 von 92 Tests PASSED**.
   - Keinerlei Quell- oder Testcode wurde in diesem Planungs-Commit verändert (`TEST_FILES_TOUCHED: NONE`).

3. **Prüfung der Findings (C-01 bis C-15):**
   - Claude hat den Plan in §12 geprüft und mit `PLAN_APPROVAL: YES` freigegeben.
   - Die Findings C-01 bis C-12 wurden von Codex akzeptiert und in den Slices umgesetzt.
   - Die Observations C-13 bis C-15 sind konstruktive Nachbereitungshinweise für die spätere Slice-Ausführung und stellen keine Blocker für den Gesamtplan dar.

4. **Sicherheit & Scoping:**
   - Die Dateigrenzen sind streng definiert (maximal 10 produktive Programmdateien je Slice).
   - Die Rollentrennung (Codex = Implementierer, Claude = Reviewer, Antigravity = Commit-Autorisierer, Orchestrator = Mechanische Commit-Ausführung) ist klar verankert.

### Formale Marker

TEST_FILES_TOUCHED: NONE

VALIDATION_RESULT: PASS | python3 -m pytest tests/ -v | 0

PRE_MORTEM: Risiko in Slice 18 (Runtime- & Marker-Cutover) ist durch die Trennung von Slice 18 (Cutover) und Slice 19 (Dokumentation & Cleanup) gut abgefedert. Ein Live-Smoke-Test am Ende von Slice 17 gegen alle drei realen CLIs minimiert das verbleibende Risiko weiter.

PLAN_APPROVAL: YES

---

## 14. Review-Antworten von Codex

Die ursprünglichen Claude-Marker in §12 bleiben als unveränderte Reviewhistorie offen, bis Claude die Überarbeitung erneut geprüft und die Findings selbst geschlossen hat.

FINDING_RESPONSE: C-01 | ACCEPTED | §3.1 definiert das Read-only-Profil präzise; Slice 3 und 13 verlangen Vollsuite ohne Repositorycache sowie einen weiterhin scheiternden Schreibversuch auf versionierte Dateien.

FINDING_RESPONSE: C-02 | ACCEPTED | Slice 9 spezifiziert den resume-bewussten Preflight; Slice 14 und §8 decken zulässige dirty In-Scope-Stände und blockierende scope-fremde Änderungen ab.

FINDING_RESPONSE: C-03 | ACCEPTED | Slice 6 macht Finding-/Prüfrecord, Pre-Mortem und parsbares Verdikt zu Contract-Regeln; Slice 15 und §9 führen die vollständige Positiv-/Negativmatrix aller harten Gates auf.

FINDING_RESPONSE: C-04 | ACCEPTED | Slice 11 implementiert bei geändertem freigegebenem Anker Exitcode 4, `awaiting_user_decision`, Freigabeinvalidierung und Rücksetzung auf `codex_plan_revision` mit anschließendem Claude-Review; Slice 15 testet den Ablauf.

FINDING_RESPONSE: C-05 | ACCEPTED | Der alte Slice 18 ist geteilt: Slice 18 enthält den §6.6-konformen atomaren Runtime-/Marker-/Instruktions-Cutover, Slice 19 ausschließlich Nutzerdokumentation, Diagramm, Beispiel und Konsistenzabschluss.

FINDING_RESPONSE: C-06 | ACCEPTED | Slice 6 führt State-v3-Contract und Tests additiv hinter dem Entwicklungsmodus ein und schützt aktive Alt-Prompts, Marker und Parser durch Regressionstests bis Slice 18.

FINDING_RESPONSE: C-07 | ACCEPTED | §6 und die R-18-Matrix weisen Slice 1 ausdrücklich nur als Vorbereitung und Slice 14 als primären R-18-Slice aus.

FINDING_RESPONSE: C-08 | ACCEPTED | Slice 5 schließt unversionierte ignorierte Pfade aus Diff, Fingerprint, Dateigrenze und Unerwartet-Datei-Gate aus; bereits versionierte Dateien bleiben unabhängig von Ignore-Regeln relevant.

FINDING_RESPONSE: C-09 | ACCEPTED | §6.1 begründet für jeden der nun 19 Slices konkret, warum der resultierende Stand startbar und grün bleibt.

FINDING_RESPONSE: C-10 | ACCEPTED | §5.1 legt alle 19 Slice-MD-Zielpfade und die verpflichtende Verlinkung bei Anlage fest; Slice 8 validiert Existenz, Link und Pflichtstruktur beim Slice-Start.

FINDING_RESPONSE: C-11 | ACCEPTED | Slice 18 entfernt und testet `--from-phase` ausdrücklich zusammen mit den übrigen Altoptionen.

FINDING_RESPONSE: C-12 | ACCEPTED | §3.9 weist `main` aus den Anforderungen gegen den tatsächlichen Repository-Basisbranch `master` aus und verlangt dynamische Ermittlung/Persistierung.

---

## 15. Planstatus und formale Marker

ADDRESSED_FINDINGS: C-01,C-02,C-03,C-04,C-05,C-06,C-07,C-08,C-09,C-10,C-11,C-12

OPEN_FINDINGS: C-13,C-14,C-15

TEST_FILES_TOUCHED: NONE

VALIDATION_RESULT: PASS | python3 -m pytest tests/ -v | 0

PLAN_READY: YES

STATUS: DONE

---

## 16. Gemeinsamer Revision-6-/Revision-7-Review

Claude prüfte zunächst den vollständigen Drei-Dokument-Diff und anschließend den vollständigen aktuellen Inhalt von Anforderungen, Arbeitsplan und Übergabe. Der Harness lief je Reviewrunde genau einmal mit 182 bestandenen Tests, sauberem Diffcheck und blockierter Schreibprobe.

- F-001: Übergabeheader und Dateitabelle nannten noch Revision 6. Beide Stellen wurden auf Revision 7 korrigiert und von Claude geschlossen.
- D-REV-02: Die Übergabe stellte den historischen Planungsbaseline-Stand mit Gemini-Fallback und 92 Tests als aktuell dar. §2 nennt nun HEAD `00017c99f3a1`, elf lokale Commits vor `master`, die committed Slices 01–05, den entfernten Fallback, den Übergangspfad bis Slice 10 und 182 Tests. Claude schloss das Finding.
- Observation: Der Lieferumfang in den Anforderungen nannte noch Revision 6. Die Zeile wurde vor Antigravity auf Revision 7 nachgezogen.
- Die reviewlokale ID D-REV-02 ist ausdrücklich nicht das bereits in Slice 05 für einen späteren Slice-10-Orchestrator-Test verwendete F-002.

```text
REVIEWER: claude
FINDING_STATUS: F-001 | CLOSED | Revision labels are consistent at Revision 7.
FINDING_STATUS: D-REV-02 | CLOSED | Handover now reports the verified post-Slice-05 repository state.
PLAN_APPROVAL: YES
PHASE1_APPROVAL: YES
OPEN_FINDINGS: NONE
STATUS: DONE
```

Antigravity prüfte anschließend den vollständigen finalen Inhalt aller drei Dokumente und den gesamten Diff gegen `HEAD`, nicht nur Claudes letzte Korrektur. Der Harness lief genau einmal mit 182 Tests, sauberem Diffcheck und blockierter Schreibprobe. A-01 weist nicht blockierend auf die unversionierte Editor-Lockdatei hin; sie bleibt vom Commit ausgeschlossen und muss spätestens vor dem strikten Unerwartet-Datei-Gate aus Slice 9 geschlossen oder ignoriert werden.

Die erste Antigravity-Antwort verwendete trotz fachlicher Freigabe `FINAL_APPROVAL` statt des verlangten `PLAN_APPROVAL` und ließ `OPEN_FINDINGS` aus. Sie galt deshalb fail-closed nicht als Freigabe. Dieselbe Konversation gab ohne erneute Validierung und ohne neuen Reviewgegenstand anschließend die bereits getroffene Entscheidung mit dem korrekten Contract aus:

```text
REVIEWER: antigravity
VALIDATION_RESULT: PASS | review_harness.py | 182 passed; diff check clean; write probe blocked
PLAN_APPROVAL: YES
OPEN_FINDINGS: NONE
STATUS: DONE
```
