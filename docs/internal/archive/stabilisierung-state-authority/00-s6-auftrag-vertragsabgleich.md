# S6 — Implementierungsauftrag: Vertragsabgleich

Auftrag an: Codex
Review durch: Claude (read-only)
Branch: `feature/state-authority-consolidation`, Vorgänger: `ce7a51c` (S5b)
Übergeordneter Plan: `00-stabilisierung-arbeitsplan.md`
Enthält Backlogaufgabe `20`

**Ohne Orchestrator.** Testsuite unter WSL: `python3 -m pytest tests/ -v`.
Baseline nach S5b (`ce7a51c`): **1408 passed in 440 s**.

## Warum es diesen Slice gibt

Der Ausgangsbefund des gesamten Plans war eine Divergenz zwischen dokumentiertem
und gelebtem Vertrag: `CLAUDE.md` nannte die Recordkette autoritativ, der
Modulkopf von `artifact_bridge.py` den State-v3-Mirror. S4b hat den Modulkopf
korrigiert. Jetzt müssen alle übrigen Dokumente denselben Zustand beschreiben.

Die Gefahr dabei ist spiegelbildlich zum Ausgangsbefund: Ein Vertragstext, der
eine **Absicht** statt des Ist-Zustands beschreibt, erzeugt genau dieselbe
Divergenz noch einmal — nur in die andere Richtung.

## Teil 1 — Autorität in allen Verträgen

`AGENTS.md`, `CLAUDE.md`, `CODEX.md` und die Modulköpfe beschreiben dieselbe
Autorität, dieselbe Reducer-Semantik und dieselbe Resumeregel. Der Ist-Zustand
nach S4b bis S5b:

- Die Recordkette ist autoritativ; `state.json` ist eine verwerfbare Projektion
  und dient nur noch als Run-Locator.
- Ein Lauf ist an seine Reducer-Version gebunden; fremde Semantik wird
  abgewiesen.
- Ein unvollständiges kanonisches Basispräfix wird beim Resume vervollständigt;
  jeder Nicht-Präfix-Fakt und jeder vorherige Seiteneffekt bleibt fail-closed.
- Alte Ketten sind irrelevant und werden fail-closed abgewiesen.

## Teil 2 — PLAN_ONLY-Schreibpflicht, aus Backlog `20`

Die Rootverträge beschreiben die PLAN_ONLY-Pflicht schwächer als der
operationale Prompt. `AGENTS.md` sagt `emit exactly one executable Slice`,
`CODEX.md` sagt `return exactly one executable Slice` — beide benennen nur den
Ergebnisdatensatz, nicht die Repositorywirkung.

**Zu tun:** Repositorywirkung und strukturierter Ergebnisdatensatz werden in
allen drei Rootverträgen getrennt benannt. Ein PLAN_ONLY-Lauf erstellt oder
aktualisiert die Plandatei; der `SLICE_PLAN`-Datensatz ist die Quittung und
ersetzt sie nicht. `correct` umfasst bei fehlendem Artefakt ausdrücklich
`create`.

Zusätzlich zu prüfen und zu dokumentieren: welche Rootdateien der Orchestrator
selbst in `assignment` einbettet und welche der Provider eigenständig lädt. Die
tatsächliche Transportgrenze ist zu belegen, nicht anzunehmen.

## Teil 3 — Entscheidung: Gehört der Crash-Harness in jede Validierung?

S5b hat die Suite von 275 s auf 440 s gebracht. Der Zuwachs steckt vollständig
in den drei Harness-Tests, und zwar **weil die Reparatur wirkt**: Die
Basispräfix-Fälle brechen nicht mehr früh ab, sondern laufen den vollständigen
Resume-Pfad.

| | vorher | jetzt |
|---|---:|---:|
| Crash-Harness | 74 s = 27 % | **235 s = 53 %** |
| Rest der Suite | 201 s | 205 s |

Der Orchestrator fährt laut `orchestrator.toml`
`default_command = ["python3", "-m", "pytest", "tests/", "-v"]` als Validierung —
**je Slice und je Korrekturrunde**. Ein Slice mit drei Runden zahlt damit 22
Minuten reine Testzeit.

Das ist eine Fehlanpassung: Crash-Injection ist eine periodische Zusicherung,
keine Prüfung, die jede Korrekturrunde braucht. Eine Runde, die einen
Reviewtext ändert, muss nicht 36 Absturzpunkte neu durchspielen.

**Zu entscheiden und zu begründen:**

- **A — Harness aus der Slice-Validierung nehmen.** Marker setzen,
  `default_command` entsprechend einschränken, den Harness als eigenständigen
  Nachweis vor Finalreview oder Merge fahren. Die Zusicherung bleibt
  vollständig, sie greift seltener. Slice-Validierung fiele auf rund 205 s.
- **B — In der Validierung sampeln.** Schwächt die Garantie; nicht empfohlen.
- **C — Akzeptieren** und 440 s je Validierung fahren.

Empfehlung ist A. Der Harness ist versioniert und liefert ein gebundenes
Ergebnisartefakt, eignet sich also als eigenständiger Nachweis. **Wird A
gewählt, darf die Zusicherung nicht schwächer werden — nur seltener.** Wo genau
sie dann greift, ist verbindlich festzulegen und in den Verträgen zu benennen.

## Teil 4 — Memoisierung in `test_workflow_transition_matrix.py`

Gemessen am 1. September 2026 per `cProfile`:
`test_gate_source_map_rejects_orphans_and_per_emission_prefix_moves` kostet 39 s.
Die Kosten liegen vollständig im Test, keine `src/`-Funktion taucht im Profil
auf. Ursache ist wiederholte AST-Traversierung ohne Cache:
`_final_review_preflight_error_codes` wird 16.071-mal aufgerufen,
`_assigned_rule_ids` 98.436-mal, eine Generator-Expression 257.136-mal — bei
einer Handvoll tatsächlich verschiedener Eingaben.

**Zu tun:** `functools.lru_cache` auf die Hilfsfunktionen oder das Parsen aus
den Schleifen ziehen. Reine Teständerung. **Die Zusicherungen bleiben
unverändert** — sie bekommen ihre Daten nur nicht mehr tausendfach neu
berechnet. Laufzeit vorher und nachher festhalten.

## Teil 5 — Zwei offene Entscheidungen aktenkundig machen

1. **Slice 5 des abgelösten Arbeitsplans** — preflightgebundene, kollisionsfreie
   native Final-Requests. Der Plan sieht die Bewertung hier vor: umsetzen, neu
   schneiden oder verwerfen. Die Entscheidung ist zu treffen und zu begründen,
   nicht offenzulassen.
2. **Die Rolle von Claude im manuellen Track.** `CLAUDE.md` legt read-only fest
   — keine Dateiänderungen, kein Commit. Im manuellen Stabilisierungstrack hat
   Claude auf ausdrückliche Anweisung des Betreibers Review, Suite, Commit und
   Nachfolgeauftrag übernommen. Das ist eine bewusste Ausnahme außerhalb des
   Orchestrators. **Ob sie in `CLAUDE.md` als abgegrenzter Absatz festgehalten
   wird, ist eine Entscheidung des Betreibers** — sie ist vorzulegen, nicht
   eigenmächtig zu treffen. Ohne sie liest eine neue Session den read-only-Text
   und verweigert den Commit.

## Anforderungen

1. Der Synchronitätsguard über die drei Rootverträge ist grün.
2. Ein providerfreier Test prüft den real transportierten `canonical_request`.
3. Keine Vertragsaussage beschreibt einen Zustand, den der Code nicht hat.
4. Die Entscheidungen aus Teil 3 und Teil 5 sind getroffen und begründet.
5. Die Memoisierung ändert keine Zusicherung.
6. Keine Protokoll-, Schema- oder Registerversion angehoben.

## Abnahme

- Alle Verträge und Modulköpfe beschreiben denselben Ist-Zustand.
- Die PLAN_ONLY-Invariante trennt Repositorywirkung und Quittung.
- Die Transportgrenze ist belegt, nicht angenommen.
- Teil 3 ist entschieden; bei A ist festgelegt, wo der Harness stattdessen greift.
- Die Laufzeit ist vorher und nachher festgehalten.
- Suite grün gegen die Baseline nach S5b: 1408 passed.

## Nicht-Ziele

- Keine Verhaltensänderung, um Dokumentation zu bestätigen. Wo Text und Code
  auseinandergehen, gewinnt der Code — oder es ist ein Befund.
- Keine Archivierung und kein Merge — S7.
- Keine Strukturzerlegung — Backlog `12`.

## Stopbedingungen

- Anhalten, wenn ein Vertragstext nur durch eine Verhaltensänderung wahr würde.
- Anhalten, wenn die Transportgrenze nicht belegbar ist.
- Anhalten, wenn Teil 3 nur durch Abschwächen der Crashzusicherung lösbar wäre.

## Reviewfokus (Claude)

- Ob ein Vertragstext eine Absicht statt des Ist-Zustands beschreibt. Das ist
  der Ausgangsbefund des Plans in umgekehrter Richtung.
- Ob die Memoisierung wirklich nur cached und keine Zusicherung berührt.
- Ob bei Auflösung A der Harness tatsächlich noch greift, und wo — oder ob er
  faktisch abgeschaltet wurde.
- Ob die beiden Entscheidungen aus Teil 5 vorgelegt statt eigenmächtig
  getroffen wurden.
