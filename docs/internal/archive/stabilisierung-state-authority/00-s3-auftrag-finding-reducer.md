# S3 — Implementierungsauftrag: Finding-Reducer und Regression-Corpus

Auftrag an: Codex
Review durch: Claude (read-only)
Branch: `feature/state-authority-consolidation`, Vorgänger: der S2-Commit
Übergeordneter Plan: `inbox/backlog/00-stabilisierung-arbeitsplan.md`
Grundlage: `docs/internal/stabilisierung-s2-uebergangsmatrix.md`

**Ohne Orchestrator.** Testsuite unter WSL: `python3 -m pytest tests/ -v`.
Baseline nach S2: **1195 passed**.

## Ziel

Genau eine Stelle beantwortet für jeden Übergang, welche Findings offen,
geschlossen, importiert oder einer Correction zugeordnet sind.

Der Reducer ist rein und deterministisch: **Recordpräfix hinein — kanonisches
Finding-Ledger plus benannte Projektionen hinaus.** Kein Zugriff auf State,
Checkpoint, Dateisystem, Uhr oder Zufall.

## Warum jetzt möglich

Die S2-Matrix stuft die Finding-Fakten als **aus dem Recordpräfix ableitbar**
ein: `runtime_history.findings` sowie
`runtime_history.reviews[*].reviewer/approval/stopped/findings`. S3 hängt
deshalb nicht an den offenen Recordlücken.

**Abgrenzung:** Die als STOP markierten `ContractResult`-Felder
(`red_state_followup_slice`, `test_files`, `pre_mortem`, `anchors`,
`stop_request`, `validation`, `evidence.*`) sind **nicht** Gegenstand dieses
Slices. Sie gehören zu S4a. Wo der Reducer sie berührt, wird die Lücke benannt,
nicht überbrückt.

## Benannte Projektionen

Diese sechs Sichten bleiben ausdrücklich benannt und werden nirgends sonst
hergeleitet:

1. **Ledger** — die vollständige laufübergreifende Finding-Historie
2. **Open-Satz** — die aktuell offenen Findings
3. **Correction-Attribution** — die einer Correction-Work-Unit unveränderlich
   zugeordneten Findings
4. **Import-Snapshot** — der aus `PLAN_ONLY` übernommene Stand der ersten
   Implementierungs-Work-Unit
5. **Request-Teilmenge** — die einem Agentenrequest angebotene Auswahl
6. **Statusübergänge** — die vom jeweiligen Reviewer beherrschten Wechsel

Nach diesem Slice leitet **kein** Modul außerhalb des Reducers Findingzustand
selbst her. `work_unit.open_findings` ist entweder eine Projektion des Reducers
oder entfällt; der heutige Kommentar in `_finding_statuses()`, der erklärt, dass
`open_findings` gerade nicht der lebende Open-Mirror ist, muss gegenstandslos
werden.

## Regression-Corpus

Die zwölf historischen Finding-Fixes werden als versionierter Corpus erfasst.
Je Eintrag: Commit, Auslöser, betroffener Übergang, erwartete Projektion.

| Commit | Datum | Betreff | berührte Module |
|---|---|---|---|
| `745a2fa` | 29.08. | preserve carried findings after imported slice | `artifact_migration` |
| `924a554` | 29.08. | resume imported findings after slice denial | `artifact_migration`, `artifact_replay` |
| `700b8c7` | 29.08. | persist PLAN_ONLY plan record before finding handoff | `orchestrator`, `workflow`, `workflow_state` |
| `f09ed8b` | 27.08. | compare correction finding subset | `orchestrator` |
| `47b4ddc` | 26.08. | reject lossy finding carry-forward | `orchestrator` |
| `24abbfd` | 26.08. | carry findings across correction rounds | `orchestrator` |
| `047934f` | 26.08. | converge correction finding history | `dry_run_scenarios`, `orchestrator`, `workflow` |
| `5a654d3` | 26.08. | carry correction finding lineages | `artifact_replay`, `orchestrator` |
| `ade231f` | 26.08. | scope finding authority to work unit | `orchestrator` |
| `286f5b2` | 24.08. | close finding-state contract gaps | `native_review_contract` |
| `17c39c2` | 23.08. | recover pending slice review findings | `artifact_migration` |
| `d24511e` | 23.08. | preserve omitted findings on denied native reviews | `native_review_contract` |

Acht der zwölf berühren `orchestrator.py` — dort sitzt die verstreute
Herleitung, die dieser Slice ablöst.

**Der Corpus wird nicht als zwölf Sonderfälle nachgebaut.** Die neue Definition
muss alle zwölf abdecken, ohne für einen einzelnen eine eigene Bedingung zu
führen. Ein Corpus-Eintrag, der nur durch eine gezielte Ausnahme grün wird, ist
ein Stopgrund.

## Sequenztests

Einzelfälle decken die Kombination nicht ab. Zusätzlich sind Sequenzen über
mindestens folgende Verkettung zu prüfen:

mehrere Slices → verneintes Review → carried Observation → Correction →
erneutes Review → Resume an beliebiger Stelle der Kette.

Property- beziehungsweise generierte Sequenztests sind erwünscht, sofern sie
deterministisch und providerfrei bleiben.

## Abnahme

- Alle zwölf historischen Fälle sind abgedeckt, keiner durch eine Sonderregel.
- Kein Modul außerhalb des Reducers leitet Findingzustand her; ein Test sichert
  das gegen den Quelltext ab (wie der S2-Vollständigkeitstest per `ast`).
- Der Reducer ist rein: gleiche Records → gleiches Ergebnis, kein Zugriff auf
  State, Uhr, Dateisystem oder Zufall.
- Die sechs Projektionen sind einzeln benannt und einzeln getestet.
- Sequenztests decken die genannte Verkettung ab.
- Identische Szenarien erzeugen dieselben kanonischen Zustände wie vor dem
  Umbau — Records und Auditprojektion bleiben für gleiche Eingaben gleich.
- Suite grün gegen Baseline 1195.

## Nicht-Ziele

- Keine Recordlücken schließen — das ist S4a.
- Kein Cutover, kein Ableiten des Mirrors — das ist S4b.
- Keine neuen `_recoverable_*`-Sonderfälle.
- Keine Änderung an Reviewhoheit, Findingnamensraum oder Freigabelogik.
- Keine Protokoll- oder Schemaversion anheben.

## Stopbedingungen

- Anhalten, wenn eine Projektion nur mit einem Fakt bestimmbar wäre, der laut
  S2-Matrix keinen Record besitzt. Dann gehört der Fall nach S4a — mit
  Benennung, welcher Fakt fehlt.
- Anhalten, wenn zwei der zwölf Fälle einander widersprechen: Dann war einer
  der historischen Fixes fachlich falsch, und das ist eine Entscheidung, keine
  Implementierung.
- Anhalten, wenn der Reducer nur durch Zugriff auf den Mirror rein zu halten
  wäre.

## Reviewfokus (Claude)

- Ob wirklich **alle** Herleitungen abgelöst sind oder nur die sichtbaren —
  besonders in `orchestrator.py`, das acht der zwölf Fixes trägt.
- Sonderbedingungen, die einen einzelnen Corpus-Fall grün machen.
- Ob die Sequenztests echte Kombinationen erzeugen oder nur längere
  Einzelfälle aneinanderreihen.
- Ob `open_findings` tatsächlich Projektion geworden ist oder weiterhin
  parallel fortgeschrieben wird.
- Verdeckte Unreinheit: Uhr, Pfade, Sortierung nach Dateisystemreihenfolge.
