# Übergabe: Modernisierung des Dual-Agent-Orchestrators

**Stand:** 2026-08-10
**Anforderungsstand:** Revision 5
**Zweck:** Kompakter, lokal verifizierter Wiedereinstieg und Übergabe an den Review des Arbeitsplans.
**Ablage:** `docs/internal/`

---

## 1. Maßgebliche Dokumente

| Datei | Inhalt | Status |
|---|---|---|
| `requirements-orchestrator-modernization.md` | Anforderungen R-1 bis R-18 und verbindliche Architekturentscheidungen | Hauptdokument, Revision 5 |
| `orchestrator-modernization-work-plan.md` | Umsetzungsslices, Abhängigkeiten, Abdeckungsmatrix und Testplan | zur Nutzerprüfung vorgelegt |
| `reference-target-repo-agents.md` | `AGENTS.md` der Ruhestandsuite | historische Verfahrensreferenz |
| `reference-target-repo-slice-execution-rules.md` | manuelle Slice-Regeln der Ruhestandsuite | historische Verfahrensreferenz |
| `reference-target-repo-claude.md` | alte Claude-Rolle des Zielrepos | historische Referenz |
| `reference-target-repo-gemini.md` | alte Gemini-/Antigravity-Rolle des Zielrepos | historische Referenz |
| `reference-target-repo-codex.md` | alte Codex-Rolle des Zielrepos | historische Referenz |

Die Referenzdateien belegen den manuellen Ausgangsprozess. Sie sind nicht die Laufzeitregeln dieses Repos und werden nicht an das neue Rollenmodell angepasst. Maßgeblich bleiben die Root-`AGENTS.md` für den aktuellen Code und die Anforderungsbeschreibung für den geplanten Umbau.

---

## 2. Lokal verifizierter Repository-Stand

- Aktiver Branch: `feature/orchestrator-modernization`
- Basis: `master` bei `0bd3bad`
- HEAD vor den aktuellen Planungsänderungen: `75337eb`
- Der Branch liegt vier Dokumentationscommits vor `master` und besitzt keinen konfigurierten Upstream.
- Der Arbeitsbaum war vor den Änderungen dieser Planungsrunde sauber.
- Gegenüber `master` war kein Quell- oder Testcode geändert.
- Die vorhandene Pipeline arbeitet weiterhin mit Phase 1/Phase 2 und Claude→Gemini-Fallback.
- `.orchestrator/state.json` liegt in Version 2 vor und steht auf `phase: done`; er stammt vom 2026-02-23.
- Verbindliche Validierung: `python3 -m pytest tests/ -v`
- Ergebnis der erneuten lokalen Prüfung am 2026-08-10: **92 gesammelt, 92 bestanden**.

Die frühere Angabe „91 bestanden, 1 übersprungen" wurde in Revision 4 korrigiert.

---

## 3. Lokal verifizierte CLIs

| Instanz | Befehl | Version | Verifizierter Non-Interactive-Modus |
|---|---|---:|---|
| Codex | `codex` | 0.147.0 | `codex exec`, read-only, ephemeral, JSONL |
| Claude Code | `claude` | 2.1.226 | Print, JSON, Plan-Modus, keine Tools, keine Sessionpersistenz |
| Antigravity | `agy` und `agy.exe` | 1.1.11 | Print, JSON, Plan-/Sandbox-Modus; natives Linux-`agy` bevorzugt |
| Gemini CLI | `gemini` | 0.39.1 | noch installiert, im Zielsystem nicht mehr verwendet |

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

Es gibt keinen Agentenfallback. Der Ausfall einer Instanz hält den Lauf definiert an. Push und Merge bleiben Nutzerentscheidungen.

---

## 5. Bestätigte Architekturentscheidungen

1. Python übernimmt die vollständige Einstiegspunkt- und Wrapperlogik; `run_task` bleibt höchstens ein dünner Starter.
2. Unterstützt werden Linux, macOS und WSL2; native Windows-Unterstützung wird erst nach automatisierter Verifikation zugesichert.
3. Agentenbefehle sind konfigurierbar; Antigravity darf als natives `agy`, `agy.exe` oder expliziter Pfad aufgelöst werden. Unter WSL2 hat das native `agy` Vorrang.
4. Antigravity autorisiert Commits, der Orchestrator führt sie mechanisch aus.
5. Pro Slice gelten höchstens zehn produktive Dateien; Pfadklassen sind konfigurierbar und unbekannte Dateien zählen produktiv.
6. Nach vier Rückgaben an Codex hält die Arbeitseinheit mit `awaiting_user_decision` an; kein Reset und kein WIP-Commit.
7. Exitcodes unterscheiden Erfolg, technischen Fehler, Quota, Instanzausfall und Nutzergate.
8. Fachliche Rechenkerne verwenden strukturierte, nach Planfreigabe geschützte Ankerwerte.
9. Marker sind schrittbezogen und weisen die Instanz separat aus; Phase- und Legacy-Marker entfallen mit State-Version 3.
10. Der Umbau erfolgt inkrementell im Bestand; aktive Version-2-Läufe werden nicht künstlich in Slices migriert.
11. Ein normaler Slice-Commit benötigt kein zusätzliches Nutzergate; risikobedingte Gates bleiben zwingend, `--manual-slice-gate` ist optional.
12. Runtime-State und Logs bleiben flüchtig; Plan- und Slice-MDs bilden die committete Prüfspur, Git wird nach dem Commit historische Source of Truth.
13. Die Root-Rollendatei heißt künftig `ANTIGRAVITY.md`; die historische Referenz `reference-target-repo-gemini.md` bleibt bestehen.

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

In dieser Planungsrunde werden ausschließlich folgende Dokumente geändert beziehungsweise neu angelegt:

- `requirements-orchestrator-modernization.md`
- `handover-orchestrator-modernization.md`
- `orchestrator-modernization-work-plan.md`

Quellcode, Tests, Root-Rollendateien und Laufzeit-State bleiben unverändert. Es wird kein Commit und kein Push erzeugt. Nächster Schritt ist der Nutzerreview des Arbeitsplans einschließlich Slice-Zerlegung. Erst nach dessen Freigabe beginnt der formale Claude-/Antigravity-Reviewzyklus und anschließend die Slice-Implementierung.
