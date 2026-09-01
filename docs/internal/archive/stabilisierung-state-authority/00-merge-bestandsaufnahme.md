# Bestandsaufnahme für den Merge nach `master`

Stand: 30. August 2026, nach dem S3-Commit `898f14e`.
Zweck: festhalten, was nach Abschluss des Stabilisierungsplans wohin gehört,
damit der Abschluss keine Rekonstruktionsarbeit wird.

Dieses Dokument ist kein Auftrag. Es wird fortgeschrieben, wenn Slices
hinzukommen.

## 1. Branchlage

| Branch | Stand | Inhalt |
|---|---|---|
| `feature/state-authority-consolidation` | **9 Commits über `master`**, 0 zurück | Der Arbeitsbranch. Enthält alles Aktive. |
| `feature/audit-evidence-self-poisoning-guard` | 6 Commits, **vollständig im Arbeitsbranch enthalten** | Historischer Zeiger auf den abgebrochenen Orchestratorlauf. Kein eigener Merge nötig. |
| `wip/validierungsevidenz-slice-02` | 7 Commits, davon **1 einzigartig** (`dc29d82`) | Der einzige Stand, der beim Merge des Arbeitsbranchs **verlorenginge**. Siehe Abschnitt 3. |
| 26 weitere `feature/*` | 0 Commits voraus, alle in `master` | Erledigt, löschbar. |

`master` ist lokal **17 Commits vor `origin/master`**. Mit den 9 Branchcommits
sind es 26 Commits, die GitHub nicht kennt. Der Push ist eine eigene
Entscheidung, nicht Teil des Merges.

## 2. Was der Merge des Arbeitsbranchs mitbringt

Die 9 Commits in Reihenfolge:

| Commit | Herkunft | Art |
|---|---|---|
| `ebb903f` | Orchestratorlauf | Arbeitsplan „Validierungsevidenz“ (PLAN_ONLY) |
| `700b8c7` | Hotfix | PLAN_ONLY-Planrecord vor Finding-Handoff |
| `924a554` | Hotfix | Importierte Findings nach Slice-Denial |
| `6dbd03c` | Orchestratorlauf | Slice 1: kanonische semantische Markdowngrenze |
| `745a2fa` | Hotfix | Carried Findings nach importiertem Slice |
| `3daab29` | Hotfix | Reviewdokument zum Carried-Finding-Hotfix |
| `d839bfa` | **S1** | Dreiwege-Fehlerklassifikation |
| `1c96382` | **S2** | Übergangs- und Divergenzmatrix |
| `898f14e` | **S3** | Finding-Reducer und Regression-Corpus |

Der abgebrochene Orchestratorlauf hinterlässt also **keinen eigenen Branch und
keine verwaisten Commits** — seine Ergebnisse (`ebb903f`, `6dbd03c`) und die
drei Symptom-Hotfixes liegen im Arbeitsbranch und kommen mit dem Merge mit.

## 3. Der einzige divergente Rest: `dc29d82`

Slice 2 des alten Plans („Digestgebundene Validierungsdiagnostik und sichere
Auditdarstellung“), in S0 als WIP gesichert: 15 Dateien, +1561/−80.

**Er lässt sich nicht einfach mergen.** Fünf seiner Dateien wurden seither
umgebaut:

| Datei | geändert in |
|---|---|
| `src/artifact_projection.py` | `898f14e` |
| `src/audit_trail.py` | `898f14e` |
| `src/contracts.py` | `898f14e` |
| `src/orchestrator.py` | `898f14e`, `d839bfa` |
| `src/validation_matrix.py` | `898f14e` |

Der Stabilisierungsplan sagt bereits: „Nach Abschluss dieses Plans wird der alte
Arbeitsplan neu geschnitten.“ Die naheliegende Konsequenz ist, `dc29d82` als
**fachliche Vorlage** zu behandeln und die Validierungsdiagnostik gegen die
neue Record- und Reducerwelt neu umzusetzen, statt den WIP zu rebasen.

**Das ist eine offene Entscheidung, keine Feststellung.** Sie fällt nach S4b.

## 4. Dokumente auf dem Branch

Acht Dateien unter `docs/internal/` existieren nur im Branch, nicht in `master`:

| Datei | Herkunft | Zielort nach Abschluss |
|---|---|---|
| `01-validierungsevidenz-…-arbeitsplan.md` | abgebrochener Lauf | Archiv — **aber testgebunden**, siehe 5 |
| `01-validierungsevidenz-…-implement-review-e94f81e2.md` | abgebrochener Lauf | Archiv |
| `slice-01-validierungsevidenz-…-markdowngrenze.md` | abgebrochener Lauf | Archiv |
| `p0-finding-import-slice-denial-record-ahead-hotfix-20260829.md` | Hotfix | Archiv |
| `p0-later-work-unit-carried-finding-guard-hotfix-20260829.md` | Hotfix | Archiv |
| `p0-later-work-unit-carried-finding-guard-review-claude-20260829-745a2fa.md` | Hotfix | Archiv |
| `p0-plan-only-finding-handoff-plan-record-hotfix-20260829.md` | Hotfix | Archiv |
| `stabilisierung-s2-uebergangsmatrix.md` | **S2** | **bleibt aktiv** — die eine lebende Inventur, testgebunden |

Vorschlag für die Archivordner, dem Hausmuster folgend:
`archive/validierungsevidenz-selbstvergiftung/` für die ersten drei,
`archive/plan-only-finding-hotfixes-20260829/` für die vier `p0-`Dokumente.

`docs/internal/README.md` führt heute nur die Roadmap als aktiv. Beim Abschluss
sind dort die S2-Matrix als aktives Dokument und die neuen Archivordner mit je
einem Absatz nachzutragen — so wie es für alle bestehenden Archivordner gemacht ist.

## 5. Stolperstein: zwei Tests binden reale Dokumentpfade

Archivieren ist hier **keine reine Dateiverschiebung**. Zwei Tests lesen echte
Bytes aus `docs/internal/`:

| Test | gebundener Pfad |
|---|---|
| `tests/test_semantic_markdown.py:146` | `docs/internal/01-validierungsevidenz-…-arbeitsplan.md` |
| `tests/test_stabilisierung_s2_transition_matrix.py:11` | `docs/internal/stabilisierung-s2-uebergangsmatrix.md` |

Der erste ist die Regression, mit der Slice 1 das Finding `C-01` geschlossen
hat: Er beweist an den realen Bytes des Arbeitsplans, dass ein veralteter
Attestierungs-Fingerprint keine semantische Autorität hat. Wird die Datei
verschoben, ist der Beweis weg oder der Test rot.

Beim Archivieren also im **selben Commit** entweder die Pfadkonstante
mitziehen oder die Datei bewusst am Platz lassen. Ein zweiter, unabhängiger
Grund, die S2-Matrix nicht zu archivieren.

## 6. Unversioniertes Material

Kommt nie in den Merge, ist aber die Evidenz des Vorhabens. `.gitignore`
schließt `inbox/`, `outbox/` und `.orchestrator/` aus.

| Ort | Umfang | Bedeutung |
|---|---|---|
| `inbox/backlog/00-*`, `01-codex-gegenanalyse-*` | 7 Dokumente | Analyse, Masterplan, Aufträge S1–S4a, diese Aufnahme |
| `inbox/backlog/02-*` bis `20-*` | 19 Dokumente | Übriger Produktbacklog, unabhängig |
| `outbox/failed/` | 9 Poison-Reports + Fehlerdateien | Belegmaterial der S1-Fehlerklassifikation |
| `.orchestrator/` | 20 Laufverzeichnisse, 14 MB | Laufhistorie; darin der eingefrorene Lauf `watch-20260829-102810.560490Z-c16fdae858d0` mit 82 Records auf `awaiting_user_decision` |

Der eingefrorene Lauf bleibt laut S0 bewusst unangetastet. Vor einem Aufräumen
von `.orchestrator/` ist zu klären, ob er noch als Evidenz gebraucht wird.

## 7. Checkliste für den Abschluss

1. Letzten Stabilisierungsslice abnehmen und committen.
2. Gesamtreview über `master..feature/state-authority-consolidation` erstellen.
3. Entscheidung zu `dc29d82` treffen und festhalten.
4. Dokumente nach Abschnitt 4 archivieren, Testpfade aus Abschnitt 5 im selben
   Commit mitziehen, `docs/internal/README.md` nachführen.
5. Aufträge und Analysen aus `inbox/backlog/` nach `docs/internal/archive/`
   überführen, sofern sie zur Entwicklung gehören.
6. Vollständige Suite unter WSL grün nachweisen.
7. Nach `master` mergen.
8. 26 erledigte `feature/*`-Branches löschen; über den Push nach `origin`
   getrennt entscheiden.

## 8. Offene Entscheidungen

- `dc29d82`: neu umsetzen oder verwerfen (nach S4b).
- Push nach `origin` — 26 Commits Rückstand.
- Aufbewahrung der 20 Laufverzeichnisse in `.orchestrator/`.
