# R1 — Implementierungsauftrag: Runidentität und frühe Protokoll-/Profilbindung

Auftrag an: Codex
Review durch: Claude (read-only)
Branch: `feature/state-authority-consolidation`, Vorgänger: der S4a-Commit
Übergeordneter Plan: `00-stabilisierung-arbeitsplan.md`
Grundlage: `docs/internal/stabilisierung-s2-uebergangsmatrix.md`, Abschnitte
„S4a-Sortierung der STOP-Einträge“ und „S4a-Schnittvorschlag“, Bündel 1

**Ohne Orchestrator.** Testsuite unter WSL: `python3 -m pytest tests/ -v`.
Baseline nach S4a (`ac2f3d6`): **1227 passed**.

## Warum dieses Bündel zuerst

Der Schnittvorschlag ordnet Bündel 1 vor alle anderen: „Muss vor allen weiteren
Bündeln landen, weil bereits der erste Dispatch diese Fakten liest.“ Ohne
Runidentität im Recordpräfix kann kein späteres Bündel seine eigenen Records
an einen Lauf binden, und S4b könnte keinen leeren State projizieren.

## Umfang — sechs Fakten der Gruppe A

| Statefeld | Zielrecord und Feld |
|---|---|
| `task_file` | `RunIdentityPayload.task_file` |
| `branch` | `RunIdentityPayload.branch` |
| `branch_base` | `RunIdentityPayload.branch_base` |
| `execution_mode` | `RunIdentityPayload.execution_mode` |
| `audit_report_path` | `RunIdentityPayload.audit_report_path` |
| `protocol_binding.codex_profile`, `claude_profile` | `RunProfilePayload.codex_model/codex_effort/claude_model/claude_effort` |

Schreiber laut Sortierung: Repository- beziehungsweise Taskinitialisierung in
`ProductionWorkflowDriver` vor dem ersten Resume-/Watch-Handoff; die
Profilbindung aus CLI-/Taskdefault **vor dem ersten Providerstart**.

`ProviderAttemptPayload` bestätigt die Profilbindung je Aufruf, kommt für die
Erstentscheidung aber zu spät. Der neue Record muss früher liegen.

## Ausdrücklich nicht recorden

`protocol_binding.mode/schema/transports` ist in der Sortierung als **Gruppe C**
eingestuft und aus dem Präfix ableitbar: Der erste akzeptierte
`ArtifactRecord.schema_version == "2"` legt `mode` und `schema_version` fest,
und das geschlossene Schema 2 erzwingt beide Transportwerte. Diese drei Felder
bekommen keinen eigenen Record. Wird die Ableitung im Code benötigt, ist sie
als benannte Projektion zu implementieren, nicht als Record.

## Anforderungen

1. Beide Payloads additiv in Schema 2 ergänzen. `schema_version` bleibt `2`;
   beide nativen Transportversionen bleiben unverändert. Alte Ketten ohne die
   neuen Records bleiben lesbar.
2. Jeder Fakt wird geschrieben, **bevor** sein erster Leser ihn braucht. Der
   Nachweis ist Teil der Abnahme und nicht die Behauptung, der Record entstehe
   „irgendwann im Lauf“.
3. Der Mirror wird unverändert weiter geschrieben. **Kein Cutover.**
4. Ein Replay des akzeptierten Präfixes rekonstruiert alle sechs Fakten ohne
   Zugriff auf `state.json`, Checkpoint oder Dateisystem.
5. Resume bleibt fail-closed. Weicht ein Record vom Mirror ab, wird das
   typisiert gemeldet.
6. Entsteht dabei ein neuer Record-/Mirror-Vergleich, wird er wie in S4a in der
   Matrix inventarisiert und im Vollständigkeitstest gezählt. Die Matrix bleibt
   die eine Inventur.
7. Die sechs STOP-Einträge werden in der Matrix von offen auf gedeckt
   fortgeschrieben, mit Verweis auf den tatsächlichen Recordtyp.

## Providerfreie Akzeptanzfälle

- Ein Lauf schreibt beide Records vor dem ersten Dispatch beziehungsweise vor
  dem ersten Providerstart; ein Test beweist die Reihenfolge, nicht nur die
  Existenz.
- Ein Replay des Präfixes liefert alle sechs Fakten byteidentisch zum Mirror.
- Ein Lauf mit abweichendem explizit angefordertem Profil wird beim Resume
  fail-closed abgewiesen, wie heute.
- Eine Kette ohne die neuen Records — also jede vor R1 geschriebene — bleibt
  lesbar und wird nicht als korrupt abgewiesen.
- Der Vollständigkeitstest der Matrix bleibt grün.

## Abnahme

- Alle sechs Fakten sind aus dem Recordpräfix allein belegbar.
- Kein Fakt wird doppelt normativ geführt: Der Mirror bleibt Mirror, der Record
  wird Quelle für den nächsten Slice.
- `protocol_binding.mode/schema/transports` hat keinen Record bekommen.
- Keine Protokoll-, Schema- oder Registerversion angehoben.
- Keine neuen `_recoverable_*`-Sonderfälle.
- Suite grün gegen die Baseline nach S4a: 1227 passed.

## Nicht-Ziele

- Kein Cutover und kein Ableiten des Mirrors — das bleibt S4b.
- Keine weiteren Bündel aus dem Schnittvorschlag.
- Keine Änderung an Reviewhoheit, Findingnamensraum oder Freigabelogik.
- Keine Arbeit an der Treiberoberfläche — das ist S4c.

## Stopbedingungen

- Anhalten, wenn ein Fakt nicht vor seinem ersten Leser geschrieben werden
  kann, ohne die Startreihenfolge zu ändern. Das ist eine Entscheidung, keine
  Implementierung.
- Anhalten, wenn die Profilbindung nur durch Aufweichen der unveränderlichen
  Laufbindung oder der Resumeprüfung recordfähig wäre.
- Anhalten, wenn ein neuer Record die Bedeutung bestehender Records ändern
  müsste.

## Reviewfokus (Claude)

- Ob der Record wirklich vor seinem ersten Leser liegt oder nur irgendwo im
  Lauf. Der Ordnungsnachweis ist der Kern dieses Slice.
- Ob `protocol_binding.mode/schema/transports` versehentlich doch einen Record
  bekommen hat, statt abgeleitet zu werden.
- Ob neue Record-/Mirror-Vergleiche in der Matrix gelandet sind oder still
  hinzugekommen sind.
- Ob die Abwärtslesbarkeit alter Ketten wirklich getestet ist und nicht nur
  behauptet.
- Verdeckte Cutover-Anteile: eine Stelle, die den State bereits aus dem Record
  liest statt aus dem Mirror.
