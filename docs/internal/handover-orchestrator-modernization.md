# Übergabe: Modernisierung des Dual-Agent-Orchestrators

**Stand:** 2026-08-11
**Anforderungsstand:** Revision 9
**Zweck:** Kompakter, lokal verifizierter Wiedereinstieg und Übergabe an den Review des Arbeitsplans.
**Ablage:** `docs/internal/`

---

## 1. Maßgebliche Dokumente

| Datei | Inhalt | Status |
|---|---|---|
| `requirements-orchestrator-modernization.md` | Anforderungen R-1 bis R-18 und verbindliche Architekturentscheidungen | Hauptdokument, Revision 9 |
| `orchestrator-modernization-work-plan.md` | Umsetzungsslices, Abhängigkeiten, Abdeckungsmatrix und Testplan | zur Nutzerprüfung vorgelegt |
| `reference-target-repo-agents.md` | `AGENTS.md` der Ruhestandsuite | historische Verfahrensreferenz |
| `reference-target-repo-slice-execution-rules.md` | manuelle Slice-Regeln der Ruhestandsuite | historische Verfahrensreferenz |
| `reference-target-repo-claude.md` | alte Claude-Rolle des Zielrepos | historische Referenz |
| `reference-target-repo-gemini.md` | alte Gemini-/Antigravity-Rolle des Zielrepos | historische Referenz |
| `reference-target-repo-codex.md` | alte Codex-Rolle des Zielrepos | historische Referenz |

Die Referenzdateien belegen den manuellen Ausgangsprozess. Sie sind nicht die Laufzeitregeln dieses Repos und werden nicht an das neue Rollenmodell angepasst. Maßgeblich bleiben die Root-`AGENTS.md` für den aktuellen Code und die Anforderungsbeschreibung für den geplanten Umbau.

---

## 2. Aktueller, lokal verifizierter Repository-Stand

- Aktiver Branch: `feature/orchestrator-modernization`
- Basis: `master` bei `0bd3bad`
- Freigegebener Implementierungscommit der Revision 9: `76c06c7` (`Bind reviews to orchestrator validation attestations`)
- Die nachgelagerte Auditprojektion wird separat lokal committed. Ein Upstream ist nicht konfiguriert.
- Slices 01 bis 07 sowie die Revision-9-Querschnittskorrektur sind lokal committed. Gemini-Fallback und Agentenersetzung wurden in Slice 01 entfernt; konfigurierbare Drei-Agenten-Adapter, wurzelgebundene Dateischnappschüsse, die kanonische Branch-Diff-Quelle, der typisierte v3-Contract-/Finding-Kern sowie State v3 mit Work Units und Resume-Persistenz sind implementiert.
- Der aktive Defaultpfad verwendet übergangsweise weiterhin die alte Phase-1-/Phase-2-Steuerung mit Codex und Claude. Der Antigravity-Adapter ist verfügbar, wird aber erst mit der neuen asymmetrischen State-Maschine aus Slice 10 als automatischer Abschlussreviewer verdrahtet.
- Revision 9 der Validierungsarchitektur ist als vom Nutzer beauftragte Querschnittskorrektur nach Slice 07 committed: ein Orchestratorlauf je kanonischem Diff-Fingerprint, fingerprintgebundene Attestierung für beide Reviewer und mehr Reviewbudget für Implementierungsanalyse statt wiederholter Vollsuiten. Editor-Lockdateien gehören weiterhin nicht zum Scope.
- `.orchestrator/state.json` liegt in Version 2 vor und steht auf `phase: done`; er stammt vom 2026-02-23.
- Verbindliche Validierung: `python3 -m pytest tests/ -v`
- Ergebnis der lokalen Prüfung für Slice 07 am 2026-08-11: **277 gesammelt, 277 bestanden**; zusätzlich sind 52 fokussierte State-/I/O-Tests, Compile, Diffcheck und der aktive v2-Dry-Run grün. Claude F-001 ist korrigiert, formal geschlossen und mit `PHASE2_APPROVAL: YES` freigegeben. Antigravity prüfte den vollständigen finalen Slice-Diff und erteilte `SLICE_APPROVAL: 07 | YES`; der lokale Commit ist autorisiert.
- Ergebnis der Revision-9-Abnahme am 2026-08-11: fachlicher Diff-Fingerprint `81f7cb377434c32bd7278d56d88b14f365f1ea1732e6534e961dfea5e32a4336`, **283 gesammelt, 283 bestanden**, aktiver v2-Dry-Run grün. Claude schloss F-002 und erteilte `PHASE2_APPROVAL: YES`; Antigravity schloss A-01 bis A-03 auf dem vollständigen finalen Diff und erteilte `FINAL_APPROVAL: YES`. Diese Zeile ist die deterministische Auditprojektion nach dem Commit und gehört nicht zum fachlichen Reviewfingerprint.

Der frühere Planungsbaseline-Stand `75337eb` mit 92 Tests und noch vorhandenem Claude→Gemini-Fallback ist nur historische Analysebasis der Revisionen 1 bis 5 und beschreibt nicht mehr den aktuellen Arbeitsbaum.

---

## 3. Lokal verifizierte CLIs

| Instanz | Befehl | Version | Verifizierter Non-Interactive-Modus |
|---|---|---:|---|
| Codex | `codex` | 0.147.0 | `codex exec`, `workspace-write`, ephemeral, JSONL und finale Nachrichtendatei |
| Claude Code | `claude` | 2.1.227 | Print, Einzel-JSON, Safe Mode, externes Reviewpaket, im Normalreview nur `Read`, keine Sessionpersistenz |
| Antigravity | `agy` | 1.1.12 | Print, JSON, Sandbox; natives Linux-`agy` bevorzugt, `agy.exe` bleibt explizit konfigurierbar |
| Gemini CLI | `gemini` | 0.39.1 | noch installiert, im Zielsystem nicht mehr verwendet |

Claude 2.1.227 und Antigravity 1.1.12 führten am 2026-08-11 die vollständigen Reviewer-Harnesses in schreibgeschützten Snapshots erfolgreich aus. Diese Fähigkeitsevidenz bleibt gültig, wird nach Revision 9 aber nicht pro Review wiederholt. Die folgenden Abschnitte 3.1 und 3.2 dokumentieren zusätzlich die ursprünglichen Positiv- und Negativ-Smokes vom 2026-08-10.

### 3.1 Positiver Live-Smoke

Am 2026-08-10 wurden alle drei benötigten CLIs außerhalb der einschränkenden Test-Sandbox mit einem werkzeuglosen Minimalprompt gestartet. Codex, Claude und Antigravity antworteten jeweils exakt `EXTERNAL_CONTROL_OK` und endeten mit Exitcode 0. Codex und Claude meldeten einen gültigen Login; der erfolgreiche Antigravity-Aufruf bestätigte dessen operative Authentifizierung.

Die offizielle OpenAI-Dokumentation führt `codex exec` als stabilen Non-Interactive-/CI-Modus und dokumentiert JSONL, read-only Sandbox sowie separate Finalausgabe: <https://learn.chatgpt.com/docs/developer-commands?surface=cli>.

### 3.2 Erkenntnis aus den negativen Sandbox-Versuchen

Die ersten Aufrufe innerhalb einer äußeren Umgebung mit schreibgeschütztem Benutzer-/Runtimebereich und eingeschränktem Netzwerk beziehungsweise Loopback scheiterten vor dem eigentlichen Modellaufruf:

- Codex konnte seinen internen App-Server nicht initialisieren.
- Claude erreichte den Provider nicht und lief in den äußeren Timeout.
- Antigravity konnte Log-/Crashdateien sowie den lokalen Language-Server-Socket nicht anlegen.

Die Sicherheit muss deshalb zwei Grenzen getrennt behandeln:

1. Reviewer sehen das Zielrepository technisch read-only.
2. CLI-private Runtime-, Log- und Tempverzeichnisse bleiben beschreibbar; Provider-Egress und der Antigravity-Loopback werden gezielt erlaubt.

Diese Runtime-Freigaben geben keinem Reviewer Schreibrechte im Repository.

### 3.3 Noch ausstehende Adapterevidenz

Die grundsätzliche externe Steuerbarkeit ist bewiesen. Slice 3 muss über die tatsächlichen neuen Adapterbefehle zusätzlich verifizieren:

- negativen Schreibversuch je CLI in einem Wegwerf-Repository,
- Text-/JSON-/Stream-Auswertung,
- realistischen Langprompt über einen sicheren Eingabekanal,
- Timeout und kontrolliertes Prozessende,
- Auth-, Quota- und Netzwerkfehler,
- Versions-/Fähigkeitsprüfung und Nutzergate für ungetestete Versionen.

Live-Smokes werden nach Installation oder Versionsänderung ausgeführt, nicht bei jedem Start, weil sie Tokens beziehungsweise Quota verbrauchen.

---

## 4. Verbindliches Zielverfahren

### Rollen

- **Codex:** alleiniger Implementierer; Autor von Plan und Slice-Dokumenten; keine eigene Freigabe; kein Commit.
- **Claude Code:** adversarialer Reviewer jeder Korrekturrunde; schreibt nur Reviewdokumentation.
- **Antigravity:** prüft nur einen von Claude freigegebenen Stand, einmal je Anlauf; autorisiert den Commit.
- **Orchestrator:** führt den von Antigravity autorisierten Commit mechanisch mit Scope-Prüfung und exakt begrenztem Staging aus.

### Ablauf

1. Codex erstellt oder überarbeitet Plan beziehungsweise Slice.
2. Claude prüft; bei Blockern korrigiert Codex und Claude prüft erneut.
3. Nach Claude-Freigabe prüft Antigravity genau einmal.
4. Eine Antigravity-Rückgabe beginnt wieder bei Codex und anschließend Claude.
5. Nach beiden Freigaben erzeugt der Orchestrator den lokalen Commit.
6. Nach dem letzten Slice folgt ein Endreview über den gesamten Branch-Diff.

Es gibt keinen Agentenfallback. Eine Quota mit eindeutigem Resetzeitpunkt versetzt den Python-Orchestrator in einen persistierten Wartezustand; nach dem Zeitpunkt plus Sicherheitszuschlag setzt er exakt denselben Rollenschritt automatisch fort. Ohne verlässlichen Zeitpunkt oder nach ausgeschöpfter Wartepolitik hält Quota mit Exitcode 2 manuell resumefähig an. Andere Instanzausfälle halten weiterhin definiert an. Push und Merge bleiben Nutzerentscheidungen.

---

## 5. Bestätigte Architekturentscheidungen

1. Python übernimmt die vollständige Einstiegspunkt- und Wrapperlogik; `run_task` bleibt höchstens ein dünner Starter.
2. Unterstützt werden Linux, macOS und WSL2; native Windows-Unterstützung wird erst nach automatisierter Verifikation zugesichert.
3. Agentenbefehle sind konfigurierbar; Antigravity darf als natives `agy`, `agy.exe` oder expliziter Pfad aufgelöst werden. Unter WSL2 hat das native `agy` Vorrang.
4. Antigravity autorisiert Commits, der Orchestrator führt sie mechanisch aus.
5. Pro Slice gelten höchstens zehn produktive Dateien; Pfadklassen sind konfigurierbar und unbekannte Dateien zählen produktiv.
6. Nach vier Rückgaben an Codex hält die Arbeitseinheit mit `awaiting_user_decision` an; kein Reset und kein WIP-Commit.
7. Exitcodes unterscheiden Erfolg, technischen Fehler, nicht automatisch fortsetzbare Quota, Instanzausfall und Nutzergate.
8. Terminierbare Quota wird als `waiting_for_quota` mit Rolle, Schritt, UTC-Resetzeitpunkt, Sicherheitszuschlag und begrenztem Automatikzähler persistiert. Der laufende Vordergrundprozess wartet ressourcenschonend und unterbrechbar, prüft Scope/Fingerprint erneut und ruft dieselbe Rolle auf; ohne laufenden Prozess bleibt `--resume` der Wiedereinstieg.
9. Fachliche Rechenkerne verwenden strukturierte, nach Planfreigabe geschützte Ankerwerte.
10. Marker sind schrittbezogen und weisen die Instanz separat aus; Phase- und Legacy-Marker entfallen mit State-Version 3.
11. Der Umbau erfolgt inkrementell im Bestand; aktive Version-2-Läufe werden nicht künstlich in Slices migriert.
12. Ein normaler Slice-Commit benötigt kein zusätzliches Nutzergate; risikobedingte Gates bleiben zwingend, `--manual-slice-gate` ist optional.
13. Runtime-State und Logs bleiben flüchtig; Plan- und Slice-MDs bilden die committete Prüfspur, Git wird nach dem Commit historische Source of Truth.
14. Die Root-Rollendatei heißt künftig `ANTIGRAVITY.md`; die historische Referenz `reference-target-repo-gemini.md` bleibt bestehen.
15. Für die Durchführung dieses Modernisierungsarbeitsplans sind ab Slice 06 alle zur jeweiligen Slice-Intention gehörenden Teständerungen vorab autorisiert. Pfadnachweis, Diff-Fingerprint, Review und Vollsuite bleiben Pflicht; nur die wiederholte separate Nutzerfreigabe entfällt. R-10 im Zielsystem bleibt unverändert.
16. Die maßgebliche Validierung führt der Python-Orchestrator je kanonischem Diff-Fingerprint genau einmal aus. Claude und Antigravity prüfen dieselbe strukturierte Attestierung statt die Vollsuite erneut zu starten; Korrekturen invalidieren sie, Adapter-/Schreib-Smokes laufen nur über explizite Diagnosebefehle bei Installation, Versionswechsel oder Fehlersuche. Der aktive v2-Pfad liefert bis zum Cutover weiterhin seinen Testsnapshot, ohne dass der Adapter fälschlich eine v3-Attestierung voraussetzt. Claudes Paket wird manifestiert und verlustfrei segmentiert; sein Lesebudget wächst exakt mit der Chunkzahl, damit das frei werdende Modellbudget tatsächlich der tieferen Implementierungsprüfung dient.

---

## 6. Tragfähige Teile und bekannte Defizite

### Beibehalten

- atomare State-Schreibvorgänge und Pfadvalidierung in `src/state_io.py`,
- Adaptergrundmuster in `src/agent_adapters.py`,
- Prozess-, Retry- und Streaminggrundlagen in `src/agent_runtime.py`,
- Inbox-Watcher mit Locking, Retry und Poison-Pill in `src/inbox_watcher.py`,
- bestehende Findings- und Markertests als Migrationsbasis.

### Ersetzen oder erweitern

- Zwei-Phasen-State und monolithische Ablaufsteuerung,
- Gemini-Fallback und Gemini-Adapter,
- asymmetrische und teilweise fehlende Contract-Validierung,
- arbeitsbaumbezogene statt branchbezogene Diff-Ermittlung,
- unvollständige Pfadprüfung für Dateischnappschüsse,
- unstrukturierte Findings-Historie,
- einzelner Validierungsbefehl,
- rein promptbasierte Stop-Regeln,
- nicht skriptbarer Dry-Run,
- flüchtige statt committierte Reviewspur.

Die vollständige Zuordnung steht in den Befunden B-1 bis B-18 und Anforderungen R-1 bis R-18 des Hauptdokuments.

---

## 7. Aktueller Übergabepunkt

Slice 07 ist lokal implementiert, validiert und von beiden Reviewern freigegeben. State v3 bleibt additiv hinter einer Development-Mode-Fassade; der aktive v2-Defaultpfad ist weiterhin startbar und im Dry-Run grün. Unbekannte Zustände werden auch am v2-Resume-Rand nicht mehr still neu initialisiert, sondern mit Exitcode 1 und klarer Meldung unverändert abgelehnt. 52 fokussierte Tests und die Vollsuite mit 277 Tests sind grün. Claude Sonnet 5/High fand nur F-001 (fehlender direkter Test für einen unvollständigen v2-State); der Test ist ergänzt, fokussiert bestätigt und F-001 formal geschlossen. Antigravity prüfte anschließend das vollständige finale Slice-Paket seit `4364c11`, meldete keine Findings und autorisierte den lokalen Commit. Ein Push erfolgt nicht automatisch.
