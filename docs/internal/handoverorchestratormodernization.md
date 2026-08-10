# Übergabe: Modernisierung des Dual-Agent-Orchestrators

**Stand:** 2026-08-10
**Zweck:** Übergabe aus einer Cloud-Session (Container-Klon von GitHub) in eine neue Session auf dem lokalen Arbeitsverzeichnis.
**Ablage:** `docs/internal/`

---

## 1. Was vorliegt

Vier Dokumente unter `docs/internal/`:

| Datei | Inhalt | Status |
|---|---|---|
| `requirements-orchestrator-modernization.md` | Anforderungsbeschreibung für Codex, Revision 2 | **Hauptdokument** |
| `reference-target-repo-agents.md` | `AGENTS.md` der Ruhestandsuite, wortgleich | Referenz, unverändert |
| `reference-target-repo-slice-execution-rules.md` | `SLICE_EXECUTION_RULES.md` der Ruhestandsuite, wortgleich | Referenz, unverändert |
| `handover-orchestrator-modernization.md` | dieses Dokument | Übergabe |

Kein Code wurde geändert. Der Beitrag besteht ausschließlich aus diesen neuen Dateien.

---

## 2. Vorbehalt zur Analysebasis — vor Weiterarbeit prüfen

Die Analyse entstand in einem Container, der `origin/master` bei Commit **`0bd3bad`** („Docs: Add PlantUML workflow diagram") geklont hatte. **Das lokale Arbeitsverzeichnis hat einen anderen Stand.** Wie stark er abweicht, ist unbekannt.

### Betroffen — muss nachverifiziert werden

**§3 der Anforderungsbeschreibung** („Verifizierter Ist-Zustand", Befunde B-1 bis B-17). Jeder Befund nennt Datei und Zeilenbereich, geprüft gegen `0bd3bad`. Bei abweichendem lokalem Stand können Zeilennummern verschoben, Befunde bereits behoben oder neue hinzugekommen sein.

**Erste Aufgabe in der neuen Session:** §3 gegen den lokalen Code neu verifizieren und die Tabelle korrigieren. Bis dahin ist §3 als „unverifiziert gegenüber lokalem Stand" zu behandeln.

Ebenfalls zu prüfen: die Testanzahl. Im Container waren es 92 grüne Tests (`python3 -m pytest tests/ -v`). Diese Zahl steht an mehreren Stellen im Anforderungsdokument und ist auf den lokalen Stand anzupassen.

### Nicht betroffen — unabhängig vom Code-Stand

- **§2 Verfahren** — stammt aus der Nutzervorgabe und den beiden Referenzdokumenten.
- **§5 Slice-Regeln** — aus den Stop-Regeln des Zielrepos abgeleitet.
- **§6 Randbedingungen** — Sprachregel, Validierung, Vorrangregel, Stop-Regeln.
- **§7 Entscheidungen** — Sachfragen, nicht Code-Befunde.
- **§9/§10 Liefergegenstände und Abnahmekriterien** — Formvorgaben.

Die 17 Anforderungen in §4 sind überwiegend verhaltensbeschrieben und damit robust; ihre Begründungen verweisen aber auf §3 und sind nach dessen Korrektur gegenzulesen.

---

## 3. Inhaltliche Zusammenfassung

### Ausgangspunkt

Das Repo entstand Ende 2025 unter der Annahme, ein Coding-Agent könne eine Aufgabe kaum ohne Abdriften beenden. Daraus folgten monolithische Phase 2, harte Zyklendeckel, aggressive Kontextbeschneidung. Die Frage ist heute eine andere: nicht ob ein Agent die Aufgabe schafft, sondern ob er rechtzeitig aufhört. Die Struktur muss von einer Hilfestellung für schwache Modelle zu einer Leine für starke werden.

### Verfahren (§2 der Anforderungsbeschreibung)

Drei Instanzen mit getrennten Rechten:

- **Codex** schreibt als einziger Code, ist Autor von Arbeitsplan und Slice-Dokumenten, gibt nie frei, committet nie.
- **Claude Code** reviewt als erste Instanz, schreibt nur Dokumentation.
- **Antigravity** reviewt abschließend, schreibt nur Dokumentation, hält das Commit-Recht.

Ablauf: Planung mit Reviewkette → je Slice Implementierung mit Reviewkette und lokalem Commit → Endreview durch alle drei über den gesamten Branch-Diff. Jede Rückgabe an Codex setzt die Kette wieder auf Claude zurück. Push und Merge bleiben Nutzerentscheidungen.

Harte Gates: rote Validierung blockiert (Ausnahme nur für Red-State-Slices mit benannter Folge-Slice), Teständerungen halten an, Stop-Regeln halten an, kein Verdikt gilt als Ablehnung, keine Freigabe ohne Findings, Pre-Mortem-Pflicht, unerwartete Dateien blockieren den Commit, Iterationszähler je Slice.

### Anforderungen (§4)

17 Stück, priorisiert, jede mit Abnahmekriterium. Die tragenden:

- **R-1 (Blocker):** Der Normalpfad ist heute blockiert — `run_task` erzwingt den Gemini-Fallback, der Preflight scheitert ohne die Binary. Reparatur vor Modernisierung.
- **R-5 (Blocker):** Eine einzige git-basierte Diff-Quelle, die unversionierte Dateien sieht und nach Slice-Commits noch funktioniert. Voraussetzung für R-9, R-10, R-15.
- **R-6 (Blocker):** Einheitlicher Contract-Validator für jeden Review-Aufruf, Finding-Klassen `BLOCKER`/`OBSERVATION`, Antwortkanal des Implementierers, Finding-Record statt Statusstring. Voraussetzung für R-7.
- **R-7 bis R-11:** dritter Reviewer und Rollentausch, Slice-Modell mit State-Migration, Git-Integration, Test-Riegel, Gesamtabnahme.
- **R-15 bis R-17:** Stop-Regeln als echtes Gate statt Prompt-Prosa, pfadabhängige Validierungsmatrix, committete Prüfspur in Plan- und Slice-Dokumenten.

### Zwei Erweiterungen gegenüber dem manuellen Verfahren

**Finding-Klassen.** „Keine Freigabe ohne Findings" und „Freigabe nur ohne offene Findings" schließen einander aus, solange es nur eine Finding-Sorte gibt. Mit `BLOCKER` und `OBSERVATION` passt beides zusammen.

**Widerspruchsrecht des Implementierers.** Die Entscheidungstabelle des manuellen Verfahrens kennt „abgelehnt", der Marker-Kontrakt des Orchestrators nicht. Ergänzt als `FINDING_RESPONSE: ACCEPTED|REJECTED` — ein bestrittenes Finding bleibt offen, bis der meldende Reviewer zustimmt. Widerspruchsrecht, kein Vetorecht.

---

## 4. Entscheidungsstand (§7)

| Punkt | Stand |
|---|---|
| 7.2 Wer committet | **entschieden:** Antigravity, mit Sicherheitsprüfung gegen den Slice-Scope. Offen bleibt nur, ob technisch durch Antigravity selbst oder mechanisch durch den Orchestrator auf dessen Freigabe hin. |
| 7.3 Slice-Maß | **entschieden:** höchstens 10 produktive Programmdateien; Dokumentation und Tests zählen nicht mit. Offen bleibt die Endungsliste für dieses Repo. |
| 7.1 Plattform | offen — Empfehlung: Wrapper nach Python, löst macOS und Windows zugleich. |
| 7.4 Ping-Pong-Bremse | offen — N, Abbruchzustand, Exitcode. |
| 7.5 Ankerwerte bei Rechenkernen | offen — Empfehlung: eigener `ANCHOR`-Kontraktblock. Betrifft die Zielarchitektur, nicht dieses Repo. |
| 7.6 Marker-Namensraum | offen — Empfehlung: generisch mit Instanz-ID, Finding-IDs mit Quellenpräfix, Legacy-Marker entfernen. |
| 7.7 Migrationsweg | offen — Empfehlung: Umbau im Bestand; die Kopplung sitzt allein in `orchestrator.py`. |
| 7.8 Mensch als Gate | **offen, Annahme gesetzt:** Nutzer ist Gate bei Push und Merge, nicht vor jedem Slice-Commit. Die Referenzregeln sehen ihn zusätzlich vor jedem Slice-Commit vor. Zu bestätigen oder zu verwerfen. |
| 7.9 Slice-Dokumente vs. Laufartefakte | offen — zwei parallele Aufzeichnungen desselben Vorgangs vermeiden. |

---

## 5. Altlasten auf GitHub

Auf `origin` existiert der Branch `claude/orchestrator-modernisierung-i2cm99` mit zwei Commits (`25e1faf`, `72958ae`). Sie enthalten eine **ältere Fassung** der Anforderungsbeschreibung (Revision 1, ohne die Einarbeitung des manuellen Verfahrens) und die beiden Referenzdokumente nicht.

**Maßgeblich ist ausschließlich die lokale Fassung** (Revision 2). Der Remote-Branch ist nicht weiterzuverwenden. Er kann gelöscht werden; das ist eine Nutzerentscheidung.

---

## 6. Erste Schritte in der neuen Session

1. Lokalen Stand feststellen: `git log --oneline -5`, `git status --short`, Testlauf.
2. §3 der Anforderungsbeschreibung gegen den lokalen Code neu verifizieren, Befunde und Zeilenangaben korrigieren, Testanzahl anpassen.
3. Entscheidung zu §7.8 treffen (Mensch als Gate vor jedem Slice-Commit?).
4. Erst danach: Feature-Branch anlegen und Codex mit der Erstellung des Arbeitsplans beauftragen.

Schritt 2 vor Schritt 4 — ein Arbeitsplan auf Basis unverifizierter Befunde erzeugt Slices für Probleme, die es möglicherweise nicht mehr gibt.
